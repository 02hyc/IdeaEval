"""Verify judge position bias by re-running each completed pair with A/B swapped.

For each pair_*/full_ranking_result.json in an experiment dir, run the judge
again with proposal_a/proposal_b and report_a/report_b swapped. Save the swap
result to pair_*/ranking_result_swap.json (does NOT overwrite the originals)
and write swap-judge traces to pair_*/agents/judge_swap/.

After all pairs are judged, compute the consistency rate. If the judge is
unbiased, the swap prediction should be the mirror of the original ("A > B" in
original ↔ "A < B" in swap). If the judge has position bias, both runs will
tend to pick the same position label regardless of content (e.g. always "A > B").

Usage:
    python scripts/verify_position_bias.py <exp_dir> [--concurrency N] [--force]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.secret_bootstrap import bootstrap_secrets
from runtime.rank_runner import RankRunConfig, _parse_ranking_json, _run_judge
from utils.logger import Logger


def find_completed_pairs(exp_dir: Path) -> list[Path]:
    out = []
    for d in sorted(exp_dir.glob("pair_*")):
        if not d.is_dir():
            continue
        if not (d / "full_ranking_result.json").is_file():
            continue
        # Need original proposals on disk (research_*/workspace/proposal.md)
        wa = d / "agents" / "research_a" / "workspace" / "proposal.md"
        wb = d / "agents" / "research_b" / "workspace" / "proposal.md"
        if not wa.is_file() or not wb.is_file():
            continue
        out.append(d)
    return out


async def run_swap(pair_dir: Path) -> dict:
    full = json.loads((pair_dir / "full_ranking_result.json").read_text(encoding="utf-8"))
    meta = full.get("meta", {})
    cutoff_date = meta.get("cutoff_date") or full.get("config", {}).get("cutoff_date")
    if not cutoff_date:
        raise ValueError(f"{pair_dir.name}: cutoff_date missing from full_ranking_result.json")

    proposal_a_text = (pair_dir / "agents" / "research_a" / "workspace" / "proposal.md").read_text(encoding="utf-8")
    proposal_b_text = (pair_dir / "agents" / "research_b" / "workspace" / "proposal.md").read_text(encoding="utf-8")
    report_a = full.get("report_a", "")
    report_b = full.get("report_b", "")
    lit_path = pair_dir / "agents" / "literature_review" / "workspace" / "literature_review.md"
    literature_review = lit_path.read_text(encoding="utf-8") if lit_path.is_file() else None

    swap_dir = pair_dir / "agents" / "judge_swap"
    (swap_dir / "workspace").mkdir(parents=True, exist_ok=True)

    config = RankRunConfig(
        proposal_a=proposal_b_text,
        proposal_b=proposal_a_text,
        cutoff_date=cutoff_date,
        task_root=str(pair_dir),
        meta=meta,
    )

    pair_id = meta.get("pair_id") or meta.get("cluster_id") or pair_dir.name
    logger = Logger()
    with logger.pair_scope(f"swap_{pair_id}"):
        logger.info(f"[swap] Running A/B-swapped judge for {pair_dir.name}")
        output, trace = await _run_judge(
            proposal_a=proposal_b_text,   # SWAPPED
            proposal_b=proposal_a_text,
            report_a=report_b,             # SWAPPED
            report_b=report_a,
            cutoff_date=cutoff_date,
            config=config,
            agent_dir=swap_dir,
            literature_review=literature_review,
        )

    ranking = _parse_ranking_json(output) or {"raw_output": output}
    (pair_dir / "ranking_result_swap.json").write_text(
        json.dumps(ranking, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return ranking


def mirror(result: str) -> str:
    """Mirror an "A > B" / "A < B" result by flipping A↔B."""
    if result == "A > B":
        return "A < B"
    if result == "A < B":
        return "A > B"
    return result  # "A ≈ B" / "A = B" / other stays the same


def classify(orig_res: str, swap_res: str) -> str:
    if not orig_res or not swap_res:
        return "missing"
    if mirror(orig_res) == swap_res:
        return "consistent"          # judge picks the same winner regardless of position
    if orig_res == swap_res:
        return "flipped"             # judge picks position-A both times (or position-B both times)
    return "other"                   # one ties, etc.


def summarize(exp_dir: Path) -> dict:
    rows = []
    for d in sorted(exp_dir.glob("pair_*")):
        orig_path = d / "full_ranking_result.json"
        swap_path = d / "ranking_result_swap.json"
        if not orig_path.is_file() or not swap_path.is_file():
            continue
        orig = json.loads(orig_path.read_text(encoding="utf-8"))
        swap = json.loads(swap_path.read_text(encoding="utf-8"))
        orig_rank = orig.get("ranking", {})
        meta = orig.get("meta", {})
        gt = meta.get("ground_truth", "")
        orig_res = orig_rank.get("result", "")
        swap_res = swap.get("result", "")
        verdict = classify(orig_res, swap_res)
        orig_correct = orig_res == gt
        swap_correct = mirror(swap_res) == gt
        rows.append({
            "pair": d.name,
            "pair_id": meta.get("pair_id") or meta.get("cluster_id") or d.name,
            "ground_truth": gt,
            "display_order": meta.get("display_order", ""),
            "citation_gap": meta.get("citation_gap", 0),
            "original": orig_res,
            "swap": swap_res,
            "verdict": verdict,
            "orig_correct": orig_correct,
            # In swap, A and B are flipped, so swap is correct iff mirror(swap) == gt
            "swap_correct": swap_correct,
            "both_correct": orig_correct and swap_correct,
        })

    consistent = sum(1 for r in rows if r["verdict"] == "consistent")
    flipped = sum(1 for r in rows if r["verdict"] == "flipped")
    other = sum(1 for r in rows if r["verdict"] == "other")
    pos_a_orig = sum(1 for r in rows if r["original"] == "A > B")
    pos_a_swap = sum(1 for r in rows if r["swap"] == "A > B")
    both_correct = sum(1 for r in rows if r["both_correct"])

    return {
        "total": len(rows),
        "consistent": consistent,
        "flipped": flipped,
        "other": other,
        "consistency_rate": consistent / len(rows) if rows else 0.0,
        "position_A_rate_original": pos_a_orig / len(rows) if rows else 0.0,
        "position_A_rate_swap": pos_a_swap / len(rows) if rows else 0.0,
        "orig_accuracy": sum(1 for r in rows if r["orig_correct"]) / len(rows) if rows else 0.0,
        "swap_accuracy": sum(1 for r in rows if r["swap_correct"]) / len(rows) if rows else 0.0,
        "both_orders_correct": both_correct,
        "both_orders_accuracy": both_correct / len(rows) if rows else 0.0,
        "rows": rows,
    }


async def main():
    parser = argparse.ArgumentParser(description="Verify judge position bias via A/B swap.")
    parser.add_argument("exp_dir")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--force", action="store_true",
                        help="Re-run swap judges even if ranking_result_swap.json already exists")
    parser.add_argument("--summarize-only", action="store_true",
                        help="Skip judge runs; just aggregate existing swap results")
    args = parser.parse_args()

    bootstrap_secrets()
    exp_dir = Path(args.exp_dir).resolve()
    if not exp_dir.is_dir():
        print(f"ERROR: experiment dir not found: {exp_dir}", file=sys.stderr)
        sys.exit(2)

    Logger.setup(log_dir=str(exp_dir / "_swap_logs"), level="DEBUG")
    logger = Logger()
    logger.set_task_id("verify_swap")

    if not args.summarize_only:
        pairs = find_completed_pairs(exp_dir)
        to_run = [p for p in pairs if args.force or not (p / "ranking_result_swap.json").is_file()]
        print(f"Found {len(pairs)} completed pair(s); running swap for {len(to_run)}.")

        sem = asyncio.Semaphore(args.concurrency)

        async def go(d):
            async with sem:
                try:
                    r = await run_swap(d)
                    print(f"  [done] {d.name}: swap pred = {r.get('result', r.get('raw_output',''))[:40]!r}")
                except Exception as e:
                    logger.error(f"[swap] {d.name} failed: {e!r}")
                    print(f"  [fail] {d.name}: {e!r}")

        await asyncio.gather(*(go(d) for d in to_run))

    summary = summarize(exp_dir)
    out_path = exp_dir / "position_bias_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 72)
    print(f"Position-bias summary (N={summary['total']})")
    print("-" * 72)
    print(f"  consistent (same winner across A/B swap): {summary['consistent']}")
    print(f"  flipped    (judge picked same position regardless of content): {summary['flipped']}")
    print(f"  other      (e.g. one side tied): {summary['other']}")
    print(f"  consistency rate: {summary['consistency_rate']:.1%}")
    print(f"  P(judge says 'A > B') in original run: {summary['position_A_rate_original']:.1%}")
    print(f"  P(judge says 'A > B') in swap run:     {summary['position_A_rate_swap']:.1%}")
    print(f"  original accuracy: {summary['orig_accuracy']:.1%}")
    print(f"  swap     accuracy: {summary['swap_accuracy']:.1%}")
    print(f"  both-orders robust accuracy: {summary['both_orders_accuracy']:.1%} "
          f"({summary['both_orders_correct']}/{summary['total']})")
    print()
    print(f"{'pair':<32} {'gap':<5} {'order':<8} {'truth':<8} {'orig':<8} {'swap':<8} {'verdict':<12}")
    print("-" * 90)
    for r in summary["rows"]:
        print(f"{r['pair'][:32]:<32} {r['citation_gap']:<5} {r['display_order']:<8} {r['ground_truth']:<8} "
              f"{r['original']:<8} {r['swap']:<8} {r['verdict']:<12}")
    print(f"\nDetailed summary written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
