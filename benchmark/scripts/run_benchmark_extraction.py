"""Phase 0: Batch benchmark/dataset extraction for all filtered arxiv papers.

Reads markdowns from the Idea-Bench markdown cache, extracts benchmarks via
the existing benchmarks.py pipeline, and writes results to a JSONL file with
checkpoint/resume support.

Usage:
    python run_benchmark_extraction.py [--workers 256] [--output benchmarks_extracted.jsonl]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from ideaeval_benchmark.benchmarks import extract_benchmarks, extract_table_excerpts
from ideaeval_benchmark.config import LLMSettings
from ideaeval_benchmark.llm_client import LLMClient
from ideaeval_benchmark.models import PaperInput

log = logging.getLogger(__name__)

_HTML_TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
_MD_TABLE_RE = re.compile(r"\|.+\|(?:\n\|[-:| ]+\|)(?:\n\|.+\|)+", re.MULTILINE)

FILTERED_PAPERS = PROJECT_ROOT / "data" / "arxiv" / "arxiv_2025sep_2026jan_metadata_filtered.jsonl"
MARKDOWN_DIR = Path(
    os.environ.get(
        "MARKDOWN_DIR",
        str(PROJECT_ROOT / "data" / "markdown"),
    )
)
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "benchmark_extraction" / "benchmarks_extracted.jsonl"


def load_papers(path: Path) -> list[dict]:
    papers = []
    with open(path) as f:
        for line in f:
            papers.append(json.loads(line))
    return papers


def load_done_ids(output_path: Path) -> set[str]:
    done = set()
    if output_path.exists():
        with open(output_path) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["paper_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return done


def process_one(
    paper: dict, llm: LLMClient, llm_semaphore: threading.Semaphore
) -> dict:
    """Extract benchmarks for a single paper. Returns a result dict."""
    pid = paper["paper_id"]
    title = paper.get("title", "")
    md_path = MARKDOWN_DIR / f"{pid}.md"

    result = {
        "paper_id": pid,
        "title": title,
        "benchmarks_or_datasets": [],
        "table_summary": "",
        "tables_found": 0,
        "tables_used": 0,
        "status": "ok",
    }

    if not md_path.exists():
        result["status"] = "no_markdown"
        return result

    try:
        markdown = md_path.read_text(encoding="utf-8")
    except Exception as e:
        result["status"] = f"read_error: {e}"
        return result

    # Step 1: extract tables / eval sections (no LLM needed, runs freely)
    try:
        table_excerpt, tables_used = extract_table_excerpts(markdown)
        result["tables_used"] = tables_used
    except Exception as e:
        result["status"] = f"table_extract_error: {e}"
        return result

    # Count total tables in markdown
    html_tables = len(_HTML_TABLE_RE.findall(markdown))
    md_tables = len(_MD_TABLE_RE.findall(markdown))
    result["tables_found"] = html_tables + md_tables

    # Step 2: if we have any content (tables or eval section text), call LLM
    if not table_excerpt.strip():
        result["status"] = "no_eval_content"
        return result

    # Rate-limit LLM calls via semaphore + jitter to avoid thundering herd
    import random

    time.sleep(random.uniform(0, 2))  # stagger initial requests
    with llm_semaphore:
        try:
            paper_input = PaperInput(paper_id=pid, title=title, arxiv_id=pid)
            bench_result = extract_benchmarks(
                paper_input, table_excerpt, markdown, llm
            )
            result["benchmarks_or_datasets"] = bench_result.benchmarks_or_datasets
            result["table_summary"] = bench_result.table_summary
        except Exception as e:
            result["status"] = f"llm_error: {e}"

    return result


def main():
    parser = argparse.ArgumentParser(description="Batch benchmark extraction")
    parser.add_argument("--workers", type=int, default=256, help="Parallel workers")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--limit", type=int, default=0, help="Limit papers (0=all)")
    parser.add_argument("--model", type=str, default=os.environ.get("LLM_MODEL", "deepseek-v4-pro"), help="LLM model name (OpenAI-compatible endpoint)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Reduce noise from httpx / openai
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("ideaeval_benchmark.llm_client").setLevel(logging.WARNING)

    output_path = Path(args.output)

    # Load papers
    papers = load_papers(FILTERED_PAPERS)
    log.info("Loaded %d papers", len(papers))

    # Resume: skip already-processed papers
    done_ids = load_done_ids(output_path)
    pending = [p for p in papers if p["paper_id"] not in done_ids]
    if done_ids:
        log.info("Resuming: %d already done, %d pending", len(done_ids), len(pending))

    if args.limit > 0:
        pending = pending[: args.limit]
        log.info("Limited to %d papers", len(pending))

    if not pending:
        log.info("All papers already processed")
        return

    # Init LLM client with high connection limits for max throughput
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    # Set high connection pool limits before creating client
    os.environ.setdefault("OPENAI_MAX_CONNECTIONS", str(args.workers + 50))
    os.environ.setdefault("OPENAI_MAX_KEEPALIVE_CONNECTIONS", str(args.workers + 50))
    os.environ.setdefault("OPENAI_TIMEOUT_SECONDS", "120")

    llm_settings = LLMSettings(
        provider="openai",
        model=args.model,
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", ""),
        max_retries=5,
        max_tokens=2048,
        temperature=0.1,
    )
    llm = LLMClient(llm_settings)

    # Thread-safe file writer
    write_lock = threading.Lock()

    def write_result(result: dict):
        line = json.dumps(result, ensure_ascii=False) + "\n"
        with write_lock:
            with open(output_path, "a") as f:
                f.write(line)

    # Semaphore to limit concurrent LLM calls
    # RPM 18000 = 300 req/s, TPM 3.6M = 60K tokens/s
    # avg ~6.5K tokens/call → TPM allows ~9 calls/s
    # Sonnet latency ~4s/call → 100 concurrent to saturate throughput
    # Retry backoff handles occasional bursts over limit
    llm_concurrency = min(args.workers, 50)
    llm_semaphore = threading.Semaphore(llm_concurrency)

    # Process
    log.info(
        "Starting with %d workers, %d max concurrent LLM calls",
        args.workers, llm_concurrency,
    )
    t0 = time.time()
    completed = 0
    has_benchmarks = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, p, llm, llm_semaphore): p for p in pending
        }
        for fut in as_completed(futures):
            paper = futures[fut]
            try:
                result = fut.result()
                write_result(result)

                completed += 1
                if result["benchmarks_or_datasets"]:
                    has_benchmarks += 1
                if "error" in result.get("status", ""):
                    errors += 1

                if completed % 500 == 0:
                    elapsed = time.time() - t0
                    rate = completed / elapsed
                    eta = (len(pending) - completed) / rate if rate > 0 else 0
                    log.info(
                        "[%d/%d] %.1f papers/s, %d with benchmarks, %d errors, ETA %.0fm",
                        completed,
                        len(pending),
                        rate,
                        has_benchmarks,
                        errors,
                        eta / 60,
                    )
            except Exception as e:
                log.error("Fatal error for %s: %s", paper.get("paper_id", "?"), e)

    elapsed = time.time() - t0
    log.info(
        "Done: %d papers in %.0fs (%.1f/s), %d with benchmarks, %d errors",
        completed,
        elapsed,
        completed / max(elapsed, 1),
        has_benchmarks,
        errors,
    )


if __name__ == "__main__":
    main()
