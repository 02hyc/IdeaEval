"""CLI entry point for the paper processing pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_settings
from .models import PaperInput
from .pipeline import process_batch, process_paper
from .util import read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ideaeval-benchmark",
        description="Paper processing pipeline: S2 metadata, PDF download, markdown conversion, benchmark extraction",
    )
    # Global options
    parser.add_argument("--config", type=str, default=None, help="Config YAML path")
    parser.add_argument("--env", type=str, default=None, help=".env file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Process papers")
    run_parser.add_argument("--arxiv-id", type=str, help="Single paper: arxiv ID")
    run_parser.add_argument("--title", type=str, default="", help="Single paper: title")
    run_parser.add_argument("--paper-id", type=str, default="", help="Single paper: paper ID")
    run_parser.add_argument("--input", type=str, help="Batch: input JSONL file")
    run_parser.add_argument("--output", type=str, help="Batch: output JSONL file")
    run_parser.add_argument("--workers", type=int, default=None, help="Number of parallel workers")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = load_settings(
        config_path=Path(args.config) if args.config else None,
        env_path=Path(args.env) if args.env else None,
    )

    if getattr(args, "workers", None) is not None:
        from dataclasses import replace
        settings = replace(settings, pipeline=replace(settings.pipeline, workers=args.workers))

    if args.command == "run":
        if args.arxiv_id:
            paper = PaperInput(
                paper_id=args.paper_id or args.arxiv_id,
                title=args.title,
                arxiv_id=args.arxiv_id,
            )
            result = process_paper(paper, settings)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

        elif args.input:
            records = read_jsonl(Path(args.input))
            papers = [
                PaperInput(
                    paper_id=r.get("paper_id", r.get("arxiv_id", "")),
                    title=r.get("title", ""),
                    arxiv_id=r.get("arxiv_id", r.get("arxiv_id_hint", "")),
                )
                for r in records
            ]
            output_path = Path(args.output) if args.output else Path("results.jsonl")
            results = process_batch(papers, settings, output_path)
            logging.getLogger(__name__).info("Results written to %s", output_path)

        else:
            run_parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
