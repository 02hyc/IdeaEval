#!/usr/bin/env python3
"""Rebalance tree's leaf nodes into target size range.

Usage:
    uv run python scripts/run_rebalance.py taxonomy_tree.json output_dir/
    uv run python scripts/run_rebalance.py taxonomy_tree.json output_dir/ --min-size 30 --max-size 50
"""

import argparse
import json
import logging
from pathlib import Path

from ideaeval_benchmark.taxoadapt.rebalance import rebalance_tree


def main():
    parser = argparse.ArgumentParser(description="Rebalance tree leaf nodes into target size range")
    parser.add_argument("tree", type=Path, help="Input taxonomy_tree.json")
    parser.add_argument("output_dir", type=Path, help="Output directory")
    parser.add_argument("--min-size", type=int, default=30, help="Minimum cluster size (default: 30)")
    parser.add_argument("--max-size", type=int, default=50, help="Maximum cluster size (default: 50)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    stats = rebalance_tree(args.tree, args.output_dir, min_size=args.min_size, max_size=args.max_size)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
