#!/usr/bin/env python3
"""Full-corpus date leak check on all ~35k papers.

Reads the arxiv metadata JSONL, runs S2 + CrossRef leak detection on each paper,
and outputs a corrected metadata file with leak_date filled in where applicable.

Usage:
    uv run python scripts/corpus/check_date_leaks.py \
        --metadata /path/to/arxiv_metadata.jsonl \
        --papers-dir /path/to/markdowns \
        --output-dir experiments/clustering_v3 \
        --workers 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from ideaeval_benchmark.date_leaks import parse_date, run_checks

log = logging.getLogger(__name__)


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Full-corpus date leak check")
    parser.add_argument("--metadata", type=Path, required=True,
                        help="Input metadata JSONL (paper_id, title, publication_date, s2_citation_count)")
    parser.add_argument("--papers-dir", type=Path, default=None,
                        help="Directory with paper markdown files for header inspection")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--force-recheck", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    s2_keys = [k.strip() for k in os.environ.get("S2_API_KEYS", "").split(",") if k.strip()]
    log.info("S2 API keys: %d", len(s2_keys))

    # Load all papers from metadata
    papers = []
    with open(args.metadata) as f:
        for line in f:
            r = json.loads(line)
            papers.append({
                "paper_id": r.get("paper_id") or r.get("arxiv_id", ""),
                "title": r.get("title", ""),
                "date": r.get("publication_date", ""),
                "citations": r.get("s2_citation_count"),
            })

    log.info("Total papers to check: %d", len(papers))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.output_dir / "date_leak_results_full.jsonl"

    results = asyncio.run(run_checks(
        papers, s2_keys, args.workers, cache_path,
        force_recheck=args.force_recheck,
        papers_dir=args.papers_dir))

    # Stats
    leaks = {pid for pid, r in results.items() if r.get("status") == "leak"}
    suspects = {pid for pid, r in results.items() if r.get("status") == "suspect"}
    clean = {pid for pid, r in results.items() if r.get("status") == "clean"}

    log.info("=== Full Corpus Date Leak Check Complete ===")
    log.info("Total: %d | Clean: %d | Leaks: %d | Suspects: %d",
             len(results), len(clean), len(leaks), len(suspects))

    # Build date corrections
    date_corrections = {}
    for pid in leaks:
        r = results[pid]
        leak_date = r.get("leak_date")
        if leak_date:
            date_corrections[pid] = leak_date

    log.info("Papers with corrected dates: %d", len(date_corrections))

    # Write corrected metadata JSONL
    corrected_path = args.output_dir / "arxiv_metadata_date_corrected.jsonl"
    n_corrected = 0
    with open(args.metadata) as fin, open(corrected_path, "w") as fout:
        for line in fin:
            r = json.loads(line)
            pid = r.get("paper_id") or r.get("arxiv_id", "")
            if pid in date_corrections:
                r["publication_date_original"] = r.get("publication_date", "")
                r["publication_date"] = date_corrections[pid]
                r["date_leak"] = True
                r["date_leak_detection"] = results[pid].get("detection", "")
                r["date_leak_venue"] = results[pid].get("leak_venue", "")
                n_corrected += 1
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info("Corrected metadata: %s (%d papers updated)", corrected_path, n_corrected)

    # Write summary
    summary = {
        "total_papers": len(results),
        "clean": len(clean),
        "leaks": len(leaks),
        "suspects": len(suspects),
        "corrected": n_corrected,
        "leak_papers": sorted(date_corrections.keys()),
    }
    summary_path = args.output_dir / "date_leak_full_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info("Summary: %s", summary_path)


if __name__ == "__main__":
    main()
