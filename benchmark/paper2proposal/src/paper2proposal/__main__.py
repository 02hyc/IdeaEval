"""CLI for paper-to-proposal conversion.

Usage:
    # Single paper
    uv run paper2proposal \
        --paper datasets/arxiv_papers/papers/2509.20102.md \
        --output-dir datasets/arxiv_papers/arxiv_papers_proposal

    # Batch (all .md files in a directory)
    uv run paper2proposal \
        --papers-dir datasets/arxiv_papers/papers \
        --output-dir datasets/arxiv_papers/arxiv_papers_proposal \
        --workers 4

    # Batch with limit
    uv run paper2proposal \
        --papers-dir datasets/arxiv_papers/papers \
        --output-dir datasets/arxiv_papers/arxiv_papers_proposal \
        --limit 10 --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .converter import run_conversions


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="paper2proposal",
        description="Convert academic papers to structured research proposals",
    )
    parser.add_argument(
        "--paper", type=str,
        help="Single paper markdown path",
    )
    parser.add_argument(
        "--papers-dir", type=str,
        help="Directory containing paper .md files (batch mode)",
    )
    parser.add_argument(
        "--output-dir", type=str, required=True,
        help="Output directory for proposals",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Custom config.yaml path (default: bundled config)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only process first N papers (batch mode)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Max concurrent conversions (default: 4)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.paper:
        paper_path = Path(args.paper).resolve()
        if not paper_path.exists():
            logging.getLogger(__name__).error("Paper not found: %s", paper_path)
            sys.exit(1)
        paper_ids = [paper_path.stem]
        papers_dir = paper_path.parent
    elif args.papers_dir:
        papers_dir = Path(args.papers_dir).resolve()
        if not papers_dir.is_dir():
            logging.getLogger(__name__).error("Not a directory: %s", papers_dir)
            sys.exit(1)
        paper_ids = sorted(p.stem for p in papers_dir.glob("*.md"))
        if args.limit:
            paper_ids = paper_ids[:args.limit]
            logging.getLogger(__name__).info("Limited to first %d papers", args.limit)
    else:
        parser.error("Must specify --paper or --papers-dir")

    if not paper_ids:
        logging.getLogger(__name__).error("No papers found")
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve()

    asyncio.run(run_conversions(
        paper_ids=paper_ids,
        papers_dir=papers_dir,
        output_dir=output_dir,
        workers=args.workers,
        config_path=args.config,
    ))


if __name__ == "__main__":
    main()
