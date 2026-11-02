#!/usr/bin/env python3
"""Entry point for TaxoAdapt clustering."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="TaxoAdapt: density-based research question clustering")
    parser.add_argument("--input", required=True, help="Path to papers JSONL")
    parser.add_argument("--output-dir", default="clustering_results_taxoadapt", help="Output directory")
    parser.add_argument("--benchmarks", default=None, help="Path to benchmarks_extracted.jsonl")
    parser.add_argument("--fields", default=None, help="Path to fields_extracted.jsonl")
    parser.add_argument("--min-leaf", type=int, default=10, help="Minimum cluster size")
    parser.add_argument("--max-leaf", type=int, default=50, help="Maximum cluster size (density threshold)")
    parser.add_argument("--max-depth", type=int, default=6, help="Maximum taxonomy tree depth")
    parser.add_argument("--initial-topics", type=int, default=20, help="Number of L1 topics")
    parser.add_argument("--max-workers", type=int, default=100,
                        help="Inner batch concurrency for each LLM stage")
    parser.add_argument("--max-parallel-leaves", type=int, default=50,
                        help="Outer concurrency for dense-leaf expansion. "
                             "Peak LLM concurrency ≈ max_parallel_leaves × max_workers, "
                             "bounded above by OPENAI_MAX_CONNECTIONS.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from ideaeval_benchmark.taxoadapt.models import Config
    from ideaeval_benchmark.taxoadapt.pipeline import HumanAuditRequired, run_pipeline

    config = Config(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        benchmarks_path=Path(args.benchmarks) if args.benchmarks else None,
        fields_path=Path(args.fields) if args.fields else None,
        min_leaf=args.min_leaf,
        max_leaf=args.max_leaf,
        max_depth=args.max_depth,
        initial_topics=args.initial_topics,
        seed=args.seed,
        max_workers=args.max_workers,
        max_parallel_leaves=args.max_parallel_leaves,
    )

    try:
        result = run_pipeline(config)
    except HumanAuditRequired as e:
        print(f"\n{'='*60}")
        print(f"HUMAN AUDIT REQUIRED — {e.stage}")
        print(f"Review and edit: {e.audit_path}")
        print(f"")
        print(f"  - Set 'action' to 'skip' for groups you do NOT want to merge")
        print(f"  - Edit 'suggested_label' / 'suggested_description' as needed")
        print(f"  - The first node_id in each group is the primary (kept) node")
        print(f"")
        print(f"Then re-run the same command to continue.")
        print(f"{'='*60}")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"Clusters: {result['n_clusters']}")
    print(f"Coverage: {result['n_papers_covered']}/{result['n_papers_total']} ({result['coverage']*100:.1f}%)")
    print(f"In [{config.min_leaf},{config.max_leaf}] range: {result['clusters_in_range']}/{result['clusters_total']}")
    print(f"Size: min={result['size_min']} max={result['size_max']} avg={result['size_avg']:.1f}")
    print(f"Tree depth: {result['tree_depth']}")
    print(f"Output: {config.output_dir}")


if __name__ == "__main__":
    main()
