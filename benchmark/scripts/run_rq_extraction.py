#!/usr/bin/env python3
"""Run Stage 6 only: extract broad research questions for each cluster."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Extract research questions for clusters (Stage 6 only)")
    parser.add_argument("--clusters", required=True, help="Path to clusters.json")
    parser.add_argument("--input", required=True, help="Path to papers JSONL (with title + abstract)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--max-workers", type=int, default=30)
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    from ideaeval_benchmark.taxoadapt.llm import LLMClient, RateLimiter, MODEL
    from ideaeval_benchmark.taxoadapt.models import load_papers
    from ideaeval_benchmark.taxoadapt.pipeline import (
        _generate_final_labels, _write_json,
    )

    log.info("Using model: %s", MODEL)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(args.clusters, encoding="utf-8") as f:
        clusters = json.load(f)
    log.info("Loaded %d clusters from %s", len(clusters), args.clusters)

    papers = load_papers(Path(args.input))
    paper_map = {p.paper_id: p for p in papers}
    log.info("Loaded %d papers", len(papers))

    rate_limiter = RateLimiter(rpm=500)
    llm = LLMClient(rate_limiter=rate_limiter)

    log.info("=== Stage 6: Generating research questions for %d clusters ===", len(clusters))
    clusters = _generate_final_labels(clusters, paper_map, llm, max_workers=args.max_workers)
    _write_json(output_dir / "clusters_labeled.json", clusters)

    n_with_rq = sum(1 for c in clusters if c.get("research_question"))
    log.info("Done: %d/%d clusters got research_question", n_with_rq, len(clusters))


if __name__ == "__main__":
    main()
