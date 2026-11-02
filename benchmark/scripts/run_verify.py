#!/usr/bin/env python3
"""Standalone: re-run Stage 6 (labels) + Stage 7 (verification) on existing clusters."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Re-run label generation + coherence verification")
    parser.add_argument("--clusters", required=True, help="Path to clusters_raw.json or clusters_labeled.json")
    parser.add_argument("--input", required=True, help="Path to papers JSONL (for paper metadata)")
    parser.add_argument("--fields", default=None, help="Path to fields_extracted.jsonl")
    parser.add_argument("--benchmarks", default=None, help="Path to benchmarks_extracted.jsonl")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--min-leaf", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=30)
    parser.add_argument("--skip-labels", action="store_true", help="Skip Stage 6 if clusters already have research_question")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    from ideaeval_benchmark.taxoadapt.llm import LLMClient, RateLimiter
    from ideaeval_benchmark.taxoadapt.models import PaperRecord, load_papers, load_benchmarks, load_fields
    from ideaeval_benchmark.taxoadapt.pipeline import (
        _generate_final_labels, _verify_coherence, _paper_to_prompt_dict, _write_json,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load clusters
    with open(args.clusters, encoding="utf-8") as f:
        clusters = json.load(f)
    log.info("Loaded %d clusters from %s", len(clusters), args.clusters)

    # Load papers
    papers = load_papers(Path(args.input))
    if args.fields:
        fields_map = load_fields(Path(args.fields))
        for p in papers:
            if p.paper_id in fields_map:
                p.research_problem = fields_map[p.paper_id].get("research_problem", "")
                p.task_formulation = fields_map[p.paper_id].get("task_formulation", "")
    if args.benchmarks:
        bm_map = load_benchmarks(Path(args.benchmarks))
        for p in papers:
            if p.paper_id in bm_map:
                p.benchmarks = bm_map[p.paper_id]
    paper_map = {p.paper_id: p for p in papers}
    log.info("Loaded %d papers", len(papers))

    rate_limiter = RateLimiter(rpm=18_000)
    llm = LLMClient(rate_limiter=rate_limiter)

    # Stage 6: Labels
    if not args.skip_labels:
        log.info("=== Stage 6: Generating research questions for %d clusters ===", len(clusters))
        clusters = _generate_final_labels(clusters, paper_map, llm, max_workers=args.max_workers)
        _write_json(output_dir / "clusters_labeled.json", clusters)
    else:
        log.info("Skipping Stage 6 (--skip-labels)")

    # Stage 7: Verification
    log.info("=== Stage 7: Coherence verification (batch_size=15) ===")
    clusters, n_kicked = _verify_coherence(
        clusters, paper_map, llm,
        min_leaf=args.min_leaf, max_workers=args.max_workers,
        batch_size=15, output_dir=output_dir,
    )
    log.info("Result: %d clusters, %d papers kicked", len(clusters), n_kicked)
    _write_json(output_dir / "clusters_verified.json", clusters)

    # Stats
    sizes = [c["n_papers"] for c in clusters]
    total = sum(sizes)
    log.info("Final: %d clusters, %d papers, sizes: [%d, %d] avg=%.1f",
             len(clusters), total,
             min(sizes) if sizes else 0, max(sizes) if sizes else 0,
             total / len(sizes) if sizes else 0)

    # Summary
    summary = {
        "n_clusters_input": len(json.load(open(args.clusters))),
        "n_clusters_after_verification": len(clusters),
        "n_papers_in_clusters": total,
        "n_papers_kicked": n_kicked,
        "size_min": min(sizes) if sizes else 0,
        "size_max": max(sizes) if sizes else 0,
        "size_avg": round(total / len(sizes), 1) if sizes else 0,
    }
    _write_json(output_dir / "verification_summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
