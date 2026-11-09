#!/usr/bin/env python3
"""Build paper pairs for evaluator ranker calibration from topic-level clusters.

Constructs pairs of contemporaneous papers within the same topic, using citation
gap as the quality signal. Outputs balanced tie / non-tie pairs across diverse topics.

Filters out "(Other)" catch-all topic nodes. Annotates each paper with its leaf node.

Usage:
    uv run python scripts/calibration/build_citation_pairs.py \
        --clusters experiments/clustering_v3/clusters_reviewed.json \
        --checkpoint experiments/clustering_v3/checkpoint_normalized.json \
        --metadata data/inputs/arxiv_metadata.jsonl \
        --output experiments/clustering_v3/ranker_pairs.json \
        --pairs-per-topic 10 \
        --tie-ratio 0.3 \
        --gap-threshold 11 \
        --max-date-diff 31
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def count_papers(node: dict) -> int:
    if not node.get("children"):
        return len(node.get("paper_ids", []))
    return sum(count_papers(c) for c in node["children"])


def is_other_node(node: dict) -> bool:
    label = node.get("label", "").lower()
    nid = node.get("id", "")
    return "other" in nid.split("_") or label.endswith("(other)") or label.startswith("other /")


def build_leaf_to_topic(node: dict) -> dict[str, str]:
    """Map each leaf cluster_id to its topic-level ancestor (total papers <= 150).
    Skips (Other) topic nodes."""
    if is_other_node(node):
        return {}
    n_papers = count_papers(node)
    if node.get("children") and n_papers <= 150:
        mapping = {}
        def collect(n):
            if not n.get("children"):
                mapping[n["id"]] = node["id"]
            else:
                for c in n["children"]:
                    collect(c)
        collect(node)
        return mapping
    mapping = {}
    for c in node.get("children", []):
        mapping.update(build_leaf_to_topic(c))
    return mapping


def build_paper_to_leaves(node: dict, result: dict[str, list] = None) -> dict[str, list]:
    """Map each paper_id to list of (leaf_id, leaf_label) it belongs to."""
    if result is None:
        result = defaultdict(list)
    if not node.get("children"):
        for pid in node.get("paper_ids", []):
            result[pid].append({
                "leaf_id": node["id"],
                "leaf_label": node.get("label", ""),
            })
    else:
        for c in node["children"]:
            build_paper_to_leaves(c, result)
    return result


def get_node_labels(node: dict) -> dict[str, str]:
    """Collect id -> label for all nodes."""
    result = {node["id"]: node.get("label", "")}
    for c in node.get("children", []):
        result.update(get_node_labels(c))
    return result


def main():
    parser = argparse.ArgumentParser(description="Build ranker calibration pairs")
    parser.add_argument("--clusters", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs-per-topic", type=int, default=10)
    parser.add_argument("--tie-ratio", type=float, default=0.3)
    parser.add_argument("--gap-threshold", type=int, default=11)
    parser.add_argument("--max-date-diff", type=int, default=31)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    n_nontie = round(args.pairs_per_topic * (1 - args.tie_ratio))
    n_tie = args.pairs_per_topic - n_nontie

    # Load tree
    with open(args.checkpoint, encoding="utf-8") as f:
        tree = json.load(f)
    tree.pop("_pipeline_stage", "")

    leaf_to_topic = build_leaf_to_topic(tree)
    node_labels = get_node_labels(tree)
    paper_to_leaves = build_paper_to_leaves(tree)

    # Load clusters
    with open(args.clusters, encoding="utf-8") as f:
        clusters = json.load(f)

    # Build topic -> paper set
    topic_papers: dict[str, set[str]] = defaultdict(set)
    for c in clusters:
        tid = leaf_to_topic.get(c["cluster_id"])
        if tid:
            for pid in c["paper_ids"]:
                topic_papers[tid].add(pid)

    # Load paper metadata
    paper_meta: dict[str, dict] = {}
    with open(args.metadata, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            pid = r.get("paper_id", "")
            cit = r.get("s2_citation_count")
            date = r.get("publication_date", "")
            if pid and cit is not None and date:
                paper_meta[pid] = {
                    "paper_id": pid,
                    "title": r.get("title", ""),
                    "citations": cit,
                    "date": date,
                }

    # Build pairs per topic
    all_pairs = []
    topics_used = 0
    topics_skipped_no_nontie = 0
    topics_skipped_other = 0

    for tid, pids in sorted(topic_papers.items()):
        papers = []
        for pid in pids:
            if pid in paper_meta:
                m = paper_meta[pid]
                try:
                    d = datetime.strptime(m["date"], "%Y-%m-%d")
                    papers.append((m, d))
                except ValueError:
                    pass

        # Generate all valid pairs
        nontie_pairs = []
        tie_pairs = []
        for i in range(len(papers)):
            for j in range(i + 1, len(papers)):
                date_diff = abs((papers[i][1] - papers[j][1]).days)
                if date_diff > args.max_date_diff:
                    continue

                c_i = papers[i][0]["citations"]
                c_j = papers[j][0]["citations"]
                gap = abs(c_i - c_j)

                if c_i >= c_j:
                    pa, pb = papers[i][0], papers[j][0]
                else:
                    pa, pb = papers[j][0], papers[i][0]

                pair_info = {
                    "paper_a": dict(pa),
                    "paper_b": dict(pb),
                    "citation_gap": abs(pa["citations"] - pb["citations"]),
                    "date_diff_days": date_diff,
                }

                if gap >= args.gap_threshold:
                    pair_info["label"] = "a_better"
                    nontie_pairs.append(pair_info)
                else:
                    pair_info["label"] = "tie"
                    tie_pairs.append(pair_info)

        if not nontie_pairs:
            topics_skipped_no_nontie += 1
            continue

        # Sample
        sampled_nontie = rng.sample(nontie_pairs, min(n_nontie, len(nontie_pairs)))
        sampled_tie = rng.sample(tie_pairs, min(n_tie, len(tie_pairs)))

        topic_label = node_labels.get(tid, "")
        for p in sampled_nontie + sampled_tie:
            p["topic_id"] = tid
            p["topic_label"] = topic_label

            # Annotate leaf nodes for each paper
            pid_a = p["paper_a"]["paper_id"]
            pid_b = p["paper_b"]["paper_id"]
            p["paper_a"]["leaf_nodes"] = paper_to_leaves.get(pid_a, [])
            p["paper_b"]["leaf_nodes"] = paper_to_leaves.get(pid_b, [])

            all_pairs.append(p)

        topics_used += 1

    # Assign pair IDs
    rng.shuffle(all_pairs)
    for i, p in enumerate(all_pairs):
        p["pair_id"] = f"pair_{i + 1:04d}"

    # Stats
    n_total = len(all_pairs)
    n_nontie_actual = sum(1 for p in all_pairs if p["label"] == "a_better")
    n_tie_actual = sum(1 for p in all_pairs if p["label"] == "tie")
    gaps = [p["citation_gap"] for p in all_pairs if p["label"] == "a_better"]
    date_diffs = [p["date_diff_days"] for p in all_pairs]

    # Check shared leaf stats
    n_shared_leaf = 0
    for p in all_pairs:
        leaves_a = {l["leaf_id"] for l in p["paper_a"]["leaf_nodes"]}
        leaves_b = {l["leaf_id"] for l in p["paper_b"]["leaf_nodes"]}
        if leaves_a & leaves_b:
            n_shared_leaf += 1

    output = {
        "metadata": {
            "total_pairs": n_total,
            "total_topics": topics_used,
            "topics_skipped_no_nontie": topics_skipped_no_nontie,
            "topics_skipped_other": topics_skipped_other,
            "non_tie_pairs": n_nontie_actual,
            "tie_pairs": n_tie_actual,
            "tie_ratio": round(n_tie_actual / max(n_total, 1), 3),
            "pairs_sharing_leaf": n_shared_leaf,
            "pairs_sharing_leaf_pct": round(n_shared_leaf / max(n_total, 1) * 100, 1),
            "gap_threshold": args.gap_threshold,
            "max_date_diff_days": args.max_date_diff,
            "pairs_per_topic_target": args.pairs_per_topic,
        },
        "pairs": all_pairs,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("=== Ranker Pairs Built ===")
    print(f"Topics used: {topics_used}")
    print(f"  Skipped (no non-tie pairs): {topics_skipped_no_nontie}")
    print(f"  Skipped (Other nodes): filtered at tree level")
    print(f"Total pairs: {n_total}")
    print(f"  Non-tie (a_better): {n_nontie_actual} ({n_nontie_actual/max(n_total,1)*100:.1f}%)")
    print(f"  Tie: {n_tie_actual} ({n_tie_actual/max(n_total,1)*100:.1f}%)")
    print(f"  Sharing a leaf node: {n_shared_leaf} ({n_shared_leaf/max(n_total,1)*100:.1f}%)")
    if gaps:
        print(f"Citation gap (non-tie): min={min(gaps)}, median={sorted(gaps)[len(gaps)//2]}, max={max(gaps)}")
    if date_diffs:
        print(f"Date diff: min={min(date_diffs)}, median={sorted(date_diffs)[len(date_diffs)//2]}, max={max(date_diffs)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
