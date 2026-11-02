#!/usr/bin/env python3
"""Build cluster windows: slide 30-day windows over each cluster's papers.

Joins rebalanced clusters + paper metadata + citation timelines to produce
cluster_windows_all.jsonl — the key input for downstream benchmark pool
construction and paper2proposal scheduling.

Usage:
    uv run python scripts/build_cluster_windows.py \
        --clusters experiments/clustering_results_taxoadapt_rebalanced/clusters.json \
        --metadata data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
        --citations datasets/citation_timelines.jsonl \
        --fields data/field_extraction/fields_extracted.jsonl \
        --output datasets/cluster_windows_all.jsonl \
        --window-days 30 --step-days 15 --min-papers 8
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_json(path: Path) -> list[dict] | dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compute_rungs(citations: list[int], gap: int) -> tuple[int, list[int]]:
    """Compute citation ladder rungs with given gap.

    A ladder is a sorted descending sequence of citation counts where each
    consecutive pair differs by at least `gap`. Returns (n_rungs, ladder).
    """
    if not citations:
        return 0, []
    sorted_desc = sorted(citations, reverse=True)
    ladder = [sorted_desc[0]]
    for c in sorted_desc[1:]:
        if ladder[-1] - c >= gap:
            ladder.append(c)
    return len(ladder), ladder


def main():
    parser = argparse.ArgumentParser(description="Build cluster windows from clusters + citations")
    parser.add_argument("--clusters", type=Path, required=True,
                        help="Rebalanced clusters JSON (list of cluster objects with paper_ids)")
    parser.add_argument("--metadata", type=Path,
                        default=PROJECT_ROOT / "data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl",
                        help="Paper metadata JSONL (needs publication_date, title, etc.)")
    parser.add_argument("--citations", type=Path,
                        default=PROJECT_ROOT / "datasets/citation_timelines.jsonl",
                        help="Citation timelines JSONL (needs paper_id, s2_citation_count)")
    parser.add_argument("--fields", type=Path, default=None,
                        help="Fields extraction JSONL (research_problem, task_formulation)")
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / "datasets/cluster_windows_all.jsonl",
                        help="Output JSONL path")
    parser.add_argument("--window-days", type=int, default=30, help="Window duration in days")
    parser.add_argument("--step-days", type=int, default=15, help="Sliding step in days")
    parser.add_argument("--min-papers", type=int, default=8,
                        help="Minimum papers in a window to emit")
    args = parser.parse_args()

    # Load clusters
    clusters = load_json(args.clusters)
    log.info("Loaded %d clusters", len(clusters))

    # Load metadata keyed by paper_id
    metadata_rows = load_jsonl(args.metadata)
    meta_by_id: dict[str, dict] = {}
    for r in metadata_rows:
        pid = r.get("paper_id") or r.get("arxiv_id")
        if pid:
            meta_by_id[pid] = r
    log.info("Loaded %d paper metadata records", len(meta_by_id))

    # Load citation data
    cite_by_id: dict[str, dict] = {}
    if args.citations.exists():
        for r in load_jsonl(args.citations):
            pid = r.get("paper_id") or r.get("arxiv_id")
            if pid:
                cite_by_id[pid] = r
        log.info("Loaded %d citation records", len(cite_by_id))
    else:
        log.warning("Citation file not found: %s", args.citations)

    # Load fields
    fields_by_id: dict[str, dict] = {}
    if args.fields and args.fields.exists():
        for r in load_jsonl(args.fields):
            pid = r.get("paper_id") or r.get("arxiv_id")
            if pid:
                fields_by_id[pid] = r
        log.info("Loaded %d field records", len(fields_by_id))

    # Determine global date range from metadata
    all_dates: list[date] = []
    for r in meta_by_id.values():
        pd = r.get("publication_date", "")
        if pd:
            try:
                all_dates.append(date.fromisoformat(pd[:10]))
            except ValueError:
                pass
    if not all_dates:
        log.error("No valid publication dates found")
        return

    global_start = min(all_dates)
    global_end = max(all_dates)
    log.info("Date range: %s to %s", global_start, global_end)

    # Build windows
    window_duration = timedelta(days=args.window_days)
    step = timedelta(days=args.step_days)
    windows: list[tuple[date, date]] = []
    ws = global_start
    while ws + window_duration <= global_end + timedelta(days=1):
        windows.append((ws, ws + window_duration))
        ws += step
    log.info("Generated %d sliding windows (duration=%dd, step=%dd)", len(windows), args.window_days, args.step_days)

    # Build cluster windows
    output_rows: list[dict] = []
    for cluster in clusters:
        cid = cluster["cluster_id"]
        label = cluster.get("label", "")
        rq = cluster.get("research_question", label)
        rq_detail = cluster.get("research_question_detail", cluster.get("description", ""))
        paper_ids = cluster.get("paper_ids", [])
        n_total = len(paper_ids)

        # Enrich papers with metadata + citations
        papers_enriched: list[dict] = []
        for pid in paper_ids:
            meta = meta_by_id.get(pid, {})
            cite = cite_by_id.get(pid, {})
            fields = fields_by_id.get(pid, {})
            pub_date_str = meta.get("publication_date", "")
            pub_date = None
            if pub_date_str:
                try:
                    pub_date = date.fromisoformat(pub_date_str[:10])
                except ValueError:
                    pass

            papers_enriched.append({
                "paper_id": pid,
                "arxiv_id": pid,
                "title": meta.get("title", ""),
                "publication_date": pub_date_str,
                "_pub_date": pub_date,
                "s2_paper_id": meta.get("s2_paper_id") or cite.get("s2_paper_id", ""),
                "s2_citation_count": cite.get("s2_citation_count") or meta.get("s2_citation_count", 0),
                "primary_category": meta.get("primary_category", ""),
                "research_problem": fields.get("research_problem", meta.get("research_problem", "")),
                "task_formulation": fields.get("task_formulation", meta.get("task_formulation", "")),
                "venue": meta.get("venue", ""),
                "iclr_type": meta.get("iclr_type", ""),
            })

        # Slide windows over this cluster
        for w_start, w_end in windows:
            in_window = [
                p for p in papers_enriched
                if p["_pub_date"] is not None and w_start <= p["_pub_date"] < w_end
            ]
            if len(in_window) < args.min_papers:
                continue

            citations = [p.get("s2_citation_count", 0) or 0 for p in in_window]
            n_rungs, ladder = compute_rungs(citations, gap=3)

            # Clean up internal fields before output
            papers_out = []
            for p in in_window:
                out = {k: v for k, v in p.items() if not k.startswith("_")}
                papers_out.append(out)

            output_rows.append({
                "cluster_id": cid,
                "cluster_label": label,
                "research_question": rq,
                "research_question_detail": rq_detail,
                "window_start": w_start.isoformat(),
                "window_end": w_end.isoformat(),
                "n_papers_in_window": len(in_window),
                "n_papers_total": n_total,
                "rungs_gap3": n_rungs,
                "ladder_gap3": ladder,
                "cite_max": max(citations),
                "cite_min": min(citations),
                "cite_median": sorted(citations)[len(citations) // 2],
                "cite_mean": round(sum(citations) / len(citations), 1),
                "n_distinct_cites": len(set(citations)),
                "papers": papers_out,
            })

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in output_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Summary stats
    unique_clusters = len(set(r["cluster_id"] for r in output_rows))
    log.info("Output: %d windows across %d clusters → %s", len(output_rows), unique_clusters, args.output)
    if output_rows:
        sizes = [r["n_papers_in_window"] for r in output_rows]
        rungs = [r["rungs_gap3"] for r in output_rows]
        log.info("  Papers/window: min=%d max=%d avg=%.1f", min(sizes), max(sizes), sum(sizes)/len(sizes))
        log.info("  Rungs(gap3): min=%d max=%d avg=%.1f", min(rungs), max(rungs), sum(rungs)/len(rungs))


if __name__ == "__main__":
    main()
