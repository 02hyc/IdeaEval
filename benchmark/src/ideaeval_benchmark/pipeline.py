"""Pipeline orchestrator: single-paper and batch processing."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .arxiv import download_pdf
from .benchmarks import extract_benchmarks, extract_table_excerpts
from .config import Settings
from .llm_client import LLMClient
from .models import BenchmarkResult, PaperInput, PaperResult
from .s2_client import S2Client
from .somark import convert_pdf_to_markdown
from .util import append_jsonl, read_jsonl

log = logging.getLogger(__name__)


def process_paper(
    paper: PaperInput,
    settings: Settings,
    *,
    s2_client: S2Client | None = None,
    llm_client: LLMClient | None = None,
) -> PaperResult:
    """Run all pipeline stages for a single paper."""
    result = PaperResult(
        paper_id=paper.paper_id,
        title=paper.title,
        arxiv_id=paper.arxiv_id,
    )
    cache_dir = Path(settings.pipeline.cache_dir)

    # Stage 1: Semantic Scholar metadata
    try:
        if s2_client is None:
            s2_client = S2Client(settings.s2)
        s2 = s2_client.resolve_s2_id(paper.title, paper.arxiv_id)
        result.s2_paper_id = s2.s2_paper_id
        result.s2_title = s2.s2_title
        result.citation_count = s2.citation_count
        result.year = s2.year
        result.publication_date = s2.publication_date
        result.s2_match_method = s2.match_method
        result.stages_completed.append("s2")
    except Exception as e:
        log.warning("S2 failed for %s: %s", paper.paper_id, e)
        result.errors["s2"] = str(e)

    # Stage 2: Download PDF
    pdf_path = None
    if paper.arxiv_id:
        try:
            pdf_dir = cache_dir / "pdf"
            pdf_path = download_pdf(paper.arxiv_id, pdf_dir, settings.arxiv)
            result.stages_completed.append("download")
        except Exception as e:
            log.warning("PDF download failed for %s: %s", paper.arxiv_id, e)
            result.errors["download"] = str(e)

    # Stage 3: Convert to markdown
    markdown = ""
    if pdf_path:
        try:
            md_cache = cache_dir / "markdown"
            markdown = convert_pdf_to_markdown(pdf_path, settings.somark, md_cache)
            result.markdown_source = "somark"
            result.markdown_chars = len(markdown)
            result.stages_completed.append("convert")
        except Exception as e:
            log.warning("Conversion failed for %s: %s", paper.arxiv_id, e)
            result.errors["convert"] = str(e)

    # Stage 4: Extract benchmarks
    if markdown:
        try:
            if llm_client is None:
                llm_client = LLMClient(settings.llm)
            table_excerpt, tables_used = extract_table_excerpts(
                markdown, max_chars=settings.pipeline.max_table_chars
            )
            import re
            _HTML_RE = re.compile(r"(<table[\s\S]*?</table>)", re.IGNORECASE)
            _PIPE_RE = re.compile(r"(\|.+\|(?:\n\|[-:| ]+\|)(?:\n\|.+\|)+)", re.MULTILINE)
            result.tables_found = len(_HTML_RE.findall(markdown)) + len(_PIPE_RE.findall(markdown))
            result.tables_used = tables_used

            if table_excerpt.strip():
                bench = extract_benchmarks(paper, table_excerpt, markdown, llm_client)
                result.benchmarks_or_datasets = bench.benchmarks_or_datasets
                result.table_summary = bench.table_summary
            result.stages_completed.append("benchmarks")
        except Exception as e:
            log.warning("Benchmark extraction failed for %s: %s", paper.paper_id, e)
            result.errors["benchmarks"] = str(e)

    return result


def process_batch(
    papers: list[PaperInput],
    settings: Settings,
    output_path: Path | None = None,
) -> list[PaperResult]:
    """Process multiple papers in parallel with checkpoint/resume."""
    # Resume: skip already-processed papers
    done_ids: set[str] = set()
    if output_path and output_path.exists():
        for rec in read_jsonl(output_path):
            done_ids.add(rec.get("paper_id", ""))
        log.info("Resuming: %d papers already done", len(done_ids))

    pending = [p for p in papers if p.paper_id not in done_ids]
    if not pending:
        log.info("All papers already processed")
        return []

    log.info("Processing %d papers (workers=%d)", len(pending), settings.pipeline.workers)

    # Shared clients
    s2_client = S2Client(settings.s2)
    llm_client = LLMClient(settings.llm)

    results: list[PaperResult] = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=settings.pipeline.workers) as pool:
        futures = {
            pool.submit(
                process_paper, p, settings,
                s2_client=s2_client, llm_client=llm_client,
            ): p
            for p in pending
        }
        for fut in as_completed(futures):
            paper = futures[fut]
            try:
                result = fut.result()
                results.append(result)
                if output_path:
                    append_jsonl(output_path, result.to_dict())
                log.info(
                    "[%d/%d] %s: %d benchmarks, stages=%s",
                    len(results), len(pending),
                    paper.arxiv_id or paper.paper_id,
                    len(result.benchmarks_or_datasets),
                    result.stages_completed,
                )
            except Exception as e:
                log.error("Fatal error for %s: %s", paper.paper_id, e)

    elapsed = time.time() - t0
    log.info("Batch complete: %d papers in %.1fs (%.2fs/paper)",
             len(results), elapsed, elapsed / max(len(results), 1))
    return results
