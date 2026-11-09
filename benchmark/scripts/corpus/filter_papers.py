"""Filter papers: remove 2026-02/03, then LLM-filter benchmark/dataset/technical reports.

Usage:
    uv run python scripts/corpus/filter_papers.py --workers 64
"""

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ideaeval_benchmark.config import load_settings, PROJECT_ROOT, resolve_path
from ideaeval_benchmark.llm_client import LLMClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are classifying ML/AI papers. Given a paper's title and abstract, determine whether this paper should be FILTERED OUT.

FILTER OUT (filter_out=true) ONLY if the paper's contribution is EXCLUSIVELY one of:
- A pure benchmark paper: ONLY proposes an evaluation suite, with no new method/model/algorithm
- A pure dataset paper: ONLY releases/curates a dataset, with no new method/pipeline/framework
- A pure technical report: model cards, system descriptions, competition summaries with no novel methodology
- A pure survey/review: only summarizes existing work with no new method or analysis framework
- A pure empirical study: only applies existing tools/models to a new domain with no methodological novelty

DO NOT FILTER (filter_out=false) if the paper does ANY of the following:
- Proposes a new method, algorithm, model, framework, or pipeline — even if it ALSO releases a benchmark/dataset
- Introduces a novel annotation scheme, evaluation methodology, or processing pipeline as a core contribution
- Presents a competition solution that introduces genuinely novel techniques
- Proposes new training strategies, inference-time methods, or architectural innovations alongside a dataset
- Develops a new theoretical framework or analysis approach

When in doubt, keep the paper (filter_out=false). Err on the side of inclusion.

Respond with ONLY valid JSON:
{"filter_out": true/false, "reason": "one sentence explanation"}
"""

USER_TEMPLATE = "Title: {title}\n\nAbstract: {abstract}"


def load_abstracts(submissions: list[Path]) -> dict[str, str]:
    """Load paper_id -> abstract from submissions.jsonl files."""
    abstracts = {}
    for path in submissions:
        if not path.exists():
            log.warning("Submissions file not found: %s", path)
            continue
        with open(path) as f:
            for line in f:
                r = json.loads(line)
                abstracts[r["paper_id"]] = r.get("abstract", "")
    log.info("Loaded %d abstracts from %d files", len(abstracts), len(submissions))
    return abstracts


def classify_paper(paper: dict, llm: LLMClient) -> dict:
    """Use LLM to classify whether paper should be filtered out."""
    user_prompt = USER_TEMPLATE.format(
        title=paper["title"],
        abstract=paper.get("abstract", "")[:2000],
    )
    raw = llm.generate(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        max_tokens=256,
        temperature=0.0,
    )
    # Parse JSON response
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in response
        for i, ch in enumerate(text):
            if ch == "{":
                try:
                    result = json.loads(text[i:])
                    break
                except json.JSONDecodeError:
                    continue
        else:
            result = {"filter_out": False, "reason": "LLM parse failed"}
    return result


def main():
    parser = argparse.ArgumentParser(description="Filter papers: date-filter + LLM type classification")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--metadata", type=Path, default=None,
                        help="Input metadata JSONL (default: data/arxiv/arxiv_2025sep_2026mar_metadata.jsonl)")
    parser.add_argument("--submissions", type=Path, nargs="+", default=None,
                        help="Submissions JSONL files for abstracts")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output filtered metadata (default: data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl)")
    parser.add_argument("--filtered-out", type=Path, default=None,
                        help="Output filtered-out papers (default: data/arxiv/arxiv_2025sep_2026jan_filtered_out.jsonl)")
    parser.add_argument("--llm-cache", type=Path, default=None,
                        help="LLM result cache (default: cache/arxiv_filter_llm_cache.jsonl)")
    args = parser.parse_args()

    settings = load_settings(
        config_path=Path("config.yaml"),
        env_path=Path(".env"),
    )
    llm = LLMClient(settings.llm)

    data_dir = resolve_path(settings.paths.data_dir)
    metadata_path = args.metadata or data_dir / "arxiv" / "arxiv_2025sep_2026mar_metadata.jsonl"
    submissions = args.submissions or [
        data_dir / "arxiv" / "submissions_2025_sep.jsonl",
        data_dir / "arxiv" / "submissions_2025q4_2026q1.jsonl",
    ]
    output_path = args.output or data_dir / "arxiv" / "arxiv_2025sep_2026jan_metadata_filtered.jsonl"
    filtered_out_path = args.filtered_out or data_dir / "arxiv" / "arxiv_2025sep_2026jan_filtered_out.jsonl"
    llm_cache_path = args.llm_cache or resolve_path(settings.paths.cache_dir) / "arxiv_filter_llm_cache.jsonl"

    # Step 1: Load abstracts
    abstracts = load_abstracts(submissions)

    # Step 2: Load metadata, filter by date, enrich with abstracts
    papers = []
    removed_by_date = 0
    with open(metadata_path) as f:
        for line in f:
            r = json.loads(line)
            pub_date = r.get("publication_date", "") or ""
            month = pub_date[:7]
            if month in ("2026-02", "2026-03"):
                removed_by_date += 1
                continue
            r["abstract"] = abstracts.get(r["paper_id"], "")
            papers.append(r)

    log.info("After date filter: %d papers (removed %d from 2026-02/03)", len(papers), removed_by_date)

    # Step 3: Load LLM cache (resume support)
    llm_results: dict[str, dict] = {}
    llm_cache_path.parent.mkdir(parents=True, exist_ok=True)
    if llm_cache_path.exists():
        with open(llm_cache_path) as f:
            for line in f:
                r = json.loads(line)
                llm_results[r["paper_id"]] = r
        log.info("Loaded %d cached LLM results", len(llm_results))

    pending = [p for p in papers if p["paper_id"] not in llm_results]
    log.info("Pending LLM classification: %d papers (workers=%d)", len(pending), args.workers)

    # Step 4: LLM classification
    if pending:
        done = 0
        failed = 0
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(classify_paper, p, llm): p
                for p in pending
            }
            for fut in as_completed(futures):
                paper = futures[fut]
                pid = paper["paper_id"]
                try:
                    result = fut.result()
                    cache_entry = {
                        "paper_id": pid,
                        "filter_out": result.get("filter_out", False),
                        "reason": result.get("reason", ""),
                    }
                    llm_results[pid] = cache_entry
                    with open(llm_cache_path, "a") as cf:
                        cf.write(json.dumps(cache_entry, ensure_ascii=False) + "\n")
                    done += 1
                except Exception as e:
                    failed += 1
                    log.warning("LLM failed for %s: %s", pid, e)

                total_done = done + failed
                if total_done % 500 == 0 or total_done == len(pending):
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    remaining = len(pending) - total_done
                    eta_s = remaining / rate if rate > 0 else 0
                    filtered = sum(1 for r in llm_results.values() if r.get("filter_out"))
                    log.info(
                        "[%d/%d] done=%d failed=%d filtered=%d | %.1f/s | ETA: %.1fmin",
                        total_done, len(pending), done, failed, filtered, rate, eta_s / 60,
                    )

    # Step 5: Write outputs
    kept = []
    filtered_out = []
    for p in papers:
        pid = p["paper_id"]
        llm_result = llm_results.get(pid, {})
        if llm_result.get("filter_out", False):
            p["filter_reason"] = llm_result.get("reason", "")
            filtered_out.append(p)
        else:
            kept.append(p)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for p in kept:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    with open(filtered_out_path, "w") as f:
        for p in filtered_out:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    log.info("DONE: kept=%d, filtered_out=%d (date=%d, llm=%d)",
             len(kept), removed_by_date + len(filtered_out),
             removed_by_date, len(filtered_out))
    log.info("Output: %s", output_path)
    log.info("Filtered: %s", filtered_out_path)


if __name__ == "__main__":
    main()
