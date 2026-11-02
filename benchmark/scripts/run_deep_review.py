#!/usr/bin/env python3
"""Run Phase 7 deep review: full markdown + LLM per-paper judgment."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Phase 7: Deep review with full markdown")
    parser.add_argument("--clusters", required=True, help="Path to clusters JSON (after Phase 6)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--markdown-dir", type=str, default=None,
                        help="Paper markdown directory (default: from MARKDOWN_DIR env or data/markdown)")
    parser.add_argument("--max-workers", type=int, default=30, help="Max concurrent LLM calls")
    parser.add_argument("--min-leaf", type=int, default=20, help="Min cluster size after review")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with open(args.clusters, encoding="utf-8") as f:
        clusters = json.load(f)
    logging.getLogger(__name__).info("Loaded %d clusters", len(clusters))

    from ideaeval_benchmark.taxoadapt.review import run_deep_review

    md_dir = Path(args.markdown_dir) if args.markdown_dir else None

    verified, total_kicked = run_deep_review(
        clusters=clusters,
        output_dir=Path(args.output_dir),
        max_workers=args.max_workers,
        min_leaf=args.min_leaf,
        md_dir=md_dir,
    )

    total_papers = sum(c["n_papers"] for c in verified)
    print(f"\nDeep Review Complete")
    print(f"  Clusters: {len(verified)}")
    print(f"  Papers: {total_papers}")
    print(f"  Kicked: {total_kicked}")
    print(f"  Output: {args.output_dir}")


if __name__ == "__main__":
    main()
