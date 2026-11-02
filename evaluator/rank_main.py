"""CLI entry point for the Ranker Agent.

Compares two research proposals by:
1. Spawning parallel research agents to investigate prior art
2. Having a judge agent compare the research reports and produce a ranking

Usage:
    python rank_main.py \
        --proposal-a path/to/proposal_a.txt \
        --proposal-b path/to/proposal_b.txt \
        --cutoff-date 2025-01-01 \
        --task-dir ./workspace
"""
import argparse
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runtime.secret_bootstrap import bootstrap_secrets
from runtime.rank_runner import RankRunConfig, run_ranker_task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ranker Agent: compare two research proposals via parallel research + judge"
    )
    parser.add_argument(
        "--proposal-a",
        required=True,
        help="First proposal: file path or inline text",
    )
    parser.add_argument(
        "--proposal-b",
        required=True,
        help="Second proposal: file path or inline text",
    )
    parser.add_argument(
        "--cutoff-date",
        required=True,
        help="Date cutoff for searches (YYYY-MM-DD). Papers on or after this date are excluded.",
    )
    parser.add_argument(
        "--max-tool-calls-per-turn",
        type=int,
        default=8,
        help="Max tool calls per LLM response (default: 8)",
    )
    parser.add_argument(
        "--task-dir",
        default="./workspace",
        help="Working directory for outputs (default: ./workspace)",
    )
    return parser.parse_args()


def main() -> int:
    # Load environment
    bootstrap_secrets()

    args = _parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Process-level loguru setup: ONCE here. run_ranker_task no longer calls
    # Logger.setup() — it would race-condition with concurrent tasks.
    from utils.logger import Logger
    os.makedirs(args.task_dir, exist_ok=True)
    Logger.setup(log_dir=args.task_dir, level="DEBUG")

    # Build config
    config = RankRunConfig(
        proposal_a=args.proposal_a,
        proposal_b=args.proposal_b,
        cutoff_date=args.cutoff_date,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        task_root=args.task_dir,
    )

    # Run
    result = asyncio.run(run_ranker_task(config))

    if result.exit_code == 0 and result.ranking:
        print("\n" + "=" * 60)
        print("RANKING RESULT:")
        print(f"  Winner: {result.ranking.get('result', 'N/A')}")
        print(f"  Confidence: {result.ranking.get('confidence', 'N/A')}")
        dims = result.ranking.get("dimensions", {})
        for dim_name, dim_data in dims.items():
            if isinstance(dim_data, dict):
                print(f"  {dim_name}: {dim_data.get('result', 'N/A')} (conf: {dim_data.get('confidence', 'N/A')})")
        print("=" * 60)

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
