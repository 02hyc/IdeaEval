#!/usr/bin/env python3
"""Check calibration pairs for date leaks (papers public before their arXiv date).

Writes per-paper results as JSONL and a filtered copy of the pairs JSON.
Detection logic lives in ideaeval_benchmark.date_leaks.

Usage:
    uv run python scripts/calibration/check_pair_date_leaks.py \
        --pairs experiments/clustering_v3/ranker_pairs.json \
        --metadata data/arxiv/arxiv_metadata.jsonl \
        --output-dir experiments/clustering_v3 \
        --workers 6
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

    parser = argparse.ArgumentParser(description="Check date leaks in ranker pairs")
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, help="Optional metadata JSONL (fallback for title/date)")
    parser.add_argument("--papers-dir", type=Path, help="Directory containing paper markdown files for header inspection")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--force-recheck", action="store_true",
                        help="Ignore cache and recheck all papers")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    s2_keys = [k.strip() for k in os.environ.get("S2_API_KEYS", "").split(",") if k.strip()]
    log.info("S2 API keys: %d", len(s2_keys))

    # Load pairs
    with open(args.pairs) as f:
        pairs_data = json.load(f)

    # Collect unique papers directly from pairs (title, date, citations are inline)
    paper_meta = {}
    if args.metadata and args.metadata.exists():
        with open(args.metadata) as f:
            for line in f:
                r = json.loads(line)
                paper_meta[r["paper_id"]] = r

    pids_info: dict[str, dict] = {}
    for p in pairs_data["pairs"]:
        for side in ("paper_a", "paper_b"):
            info = p[side]
            pid = info["paper_id"]
            if pid not in pids_info:
                pids_info[pid] = {
                    "paper_id": pid,
                    "title": info.get("title", ""),
                    "date": info.get("date", ""),
                    "citations": info.get("citations"),
                }
                # Fallback to metadata if inline info is sparse
                if (not pids_info[pid]["title"] or not pids_info[pid]["date"]) and pid in paper_meta:
                    m = paper_meta[pid]
                    pids_info[pid]["title"] = pids_info[pid]["title"] or m.get("title", "")
                    pids_info[pid]["date"] = pids_info[pid]["date"] or m.get("publication_date", "")
                    if pids_info[pid]["citations"] is None:
                        pids_info[pid]["citations"] = m.get("s2_citation_count")

    papers_to_check = sorted(pids_info.values(), key=lambda x: x["paper_id"])
    log.info("Papers to check: %d", len(papers_to_check))

    # Run checks
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.output_dir / "date_leak_results_v2.jsonl"
    results = asyncio.run(run_checks(
        papers_to_check, s2_keys, args.workers, cache_path,
        force_recheck=args.force_recheck,
        papers_dir=args.papers_dir))

    # Stats
    leaks = {pid for pid, r in results.items() if r.get("status") == "leak"}
    suspects = {pid for pid, r in results.items() if r.get("status") == "suspect"}
    errors = {pid for pid, r in results.items() if r.get("status") == "error"}
    clean = {pid for pid, r in results.items() if r.get("status") == "clean"}

    log.info("Results: %d clean, %d leaks, %d suspects, %d errors, %d skipped",
             len(clean), len(leaks), len(suspects), len(errors),
             len(results) - len(clean) - len(leaks) - len(suspects) - len(errors))

    # Show leak details
    if leaks:
        log.info("=== Confirmed date leaks ===")
        for pid in sorted(leaks):
            r = results[pid]
            log.info("  [%s] %s  arxiv=%s leak=%s venue=%s detection=%s",
                     pid, (r.get("leak_title") or r.get("title", ""))[:60],
                     r.get("arxiv_date", ""), r.get("leak_date", ""),
                     r.get("leak_venue", ""), r.get("detection", ""))

    if suspects:
        log.info("=== Suspects (citation anomaly, no venue match found) ===")
        for pid in sorted(suspects):
            r = results[pid]
            log.info("  [%s] %s  %s", pid, r.get("title", "")[:60], r.get("detail", ""))

    # Build date correction map: leaked paper_id -> corrected date (leak_date)
    date_corrections: dict[str, str] = {}
    for pid in leaks:
        r = results[pid]
        leak_date = r.get("leak_date")
        if leak_date:
            date_corrections[pid] = leak_date

    # Also remove technical reports / manually flagged papers
    force_remove = set()
    # Dream-Coder 7B is a technical report, not a research paper
    if "2509.01142" in pids_info:
        force_remove.add("2509.01142")

    # Filter pairs:
    # - For leaked papers, correct date to leak_date
    # - Recalculate date_diff with corrected dates
    # - Remove only if new date_diff > 31 days OR paper is force-removed
    kept = []
    removed = 0
    removed_pairs_info = []
    corrected_pairs_info = []

    for p in pairs_data["pairs"]:
        pid_a = p["paper_a"]["paper_id"]
        pid_b = p["paper_b"]["paper_id"]

        # Force-remove (technical reports, etc.)
        if pid_a in force_remove or pid_b in force_remove:
            removed += 1
            removed_pairs_info.append({
                "pair_id": p["pair_id"],
                "reason": "force_remove",
                "paper": pid_a if pid_a in force_remove else pid_b,
            })
            continue

        # Get effective dates (corrected if leaked)
        date_a_str = date_corrections.get(pid_a, p["paper_a"]["date"])
        date_b_str = date_corrections.get(pid_b, p["paper_b"]["date"])
        date_a = parse_date(date_a_str)
        date_b = parse_date(date_b_str)

        if not date_a or not date_b:
            kept.append(p)
            continue

        new_diff = abs((date_a - date_b).days)

        # If either paper was leaked and new date diff > 31 days, remove
        has_leak = pid_a in date_corrections or pid_b in date_corrections
        if has_leak and new_diff > 31:
            removed += 1
            leaked_pid = pid_a if pid_a in date_corrections else pid_b
            removed_pairs_info.append({
                "pair_id": p["pair_id"],
                "reason": "date_diff_exceeds_31d",
                "paper": leaked_pid,
                "original_date": p["paper_a" if leaked_pid == pid_a else "paper_b"]["date"],
                "corrected_date": date_corrections[leaked_pid],
                "new_date_diff": new_diff,
            })
            continue

        # Keep pair — apply date corrections if any
        if pid_a in date_corrections:
            p["paper_a"]["date"] = date_corrections[pid_a]
            p["paper_a"]["date_corrected"] = True
            p["paper_a"]["original_arxiv_date"] = pids_info[pid_a]["date"]
        if pid_b in date_corrections:
            p["paper_b"]["date"] = date_corrections[pid_b]
            p["paper_b"]["date_corrected"] = True
            p["paper_b"]["original_arxiv_date"] = pids_info[pid_b]["date"]

        if has_leak:
            p["date_diff_days"] = new_diff
            corrected_pairs_info.append({
                "pair_id": p["pair_id"],
                "leaked_paper": pid_a if pid_a in date_corrections else pid_b,
                "corrected_date": date_corrections.get(pid_a) or date_corrections.get(pid_b),
                "new_date_diff": new_diff,
            })

        kept.append(p)

    n_nontie = sum(1 for p in kept if p["label"] == "a_better")
    n_tie = sum(1 for p in kept if p["label"] == "tie")

    pairs_data["metadata"]["total_pairs"] = len(kept)
    pairs_data["metadata"]["non_tie_pairs"] = n_nontie
    pairs_data["metadata"]["tie_pairs"] = n_tie
    pairs_data["metadata"]["tie_ratio"] = round(n_tie / max(len(kept), 1), 3)
    pairs_data["metadata"]["date_leak_papers"] = len(leaks)
    pairs_data["metadata"]["date_leak_suspects"] = len(suspects)
    pairs_data["metadata"]["date_leak_pairs_removed"] = removed
    pairs_data["metadata"]["date_leak_pairs_corrected"] = len(corrected_pairs_info)
    pairs_data["pairs"] = kept

    out_path = args.output_dir / "ranker_pairs_clean.json"
    with open(out_path, "w") as f:
        json.dump(pairs_data, f, ensure_ascii=False, indent=2)

    print(f"\n=== Date Leak Check Complete ===")
    print(f"Papers checked: {len(papers_to_check)}")
    print(f"Confirmed leaks: {len(leaks)}")
    print(f"Suspects (citation anomaly): {len(suspects)}")
    print(f"Pairs removed: {removed}")
    print(f"Pairs corrected (kept with new date): {len(corrected_pairs_info)}")
    print(f"Pairs remaining: {len(kept)} (non-tie={n_nontie}, tie={n_tie})")
    if removed_pairs_info:
        print(f"\nRemoved pairs:")
        for info in removed_pairs_info:
            reason = info["reason"]
            if reason == "date_diff_exceeds_31d":
                print(f"  {info['pair_id']} — {info['paper']} corrected {info['original_date']}→{info['corrected_date']}, new diff={info['new_date_diff']}d > 31d")
            else:
                print(f"  {info['pair_id']} — {info['paper']} ({reason})")
    if corrected_pairs_info:
        print(f"\nCorrected pairs (kept):")
        for info in corrected_pairs_info:
            print(f"  {info['pair_id']} — {info['leaked_paper']} date→{info['corrected_date']}, diff={info['new_date_diff']}d")
    if suspects:
        print(f"\nSuspect papers (manual review recommended):")
        for pid in sorted(suspects):
            r = results[pid]
            print(f"  {pid}: {r.get('detail', '')}")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
