#!/usr/bin/env python3
"""Build topic-level clusters from a TaxoAdapt taxonomy tree.

Walks the tree and finds, for each surviving leaf cluster, the deepest
ancestor whose total paper count is <= --max-topic-size (default 150).
That ancestor is the "topic". Multiple leaves under the same topic are
grouped together.

"Other" handling:
  - Leaf "Other" buckets (those auto-created with id suffix `_other`,
    e.g. `l1_001_02_other`) are skipped.
  - L1 "Other / Cross-cutting" (a real L1 category whose id is just
    `l1_NNN` without an "other" segment) is kept; only its segment-name
    matters, not its label.

Usage:
    uv run python scripts/taxonomy/build_topics.py \\
        --tree     experiments/clustering_v3/checkpoint_normalized.json \\
        --clusters experiments/clustering_v3/clusters.json \\
        --output   experiments/clustering_v3/topic_level_clusters.json \\
        --max-topic-size 150
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)


def is_other_id(node_id: str) -> bool:
    """ID-only check: True iff any underscore-separated segment is literally 'other'.

    Catches auto-created leaf buckets like 'l1_001_03_other', but NOT
    legitimate L1 categories whose id is 'l1_020' even if their label
    is 'Other / Cross-cutting'.
    """
    return "other" in node_id.split("_")


def index_tree(node: dict, by_id: dict[str, dict] | None = None,
               parent_of: dict[str, str] | None = None) -> tuple[dict, dict]:
    """Return (id -> node, id -> parent_id) maps via DFS."""
    if by_id is None:
        by_id = {}
        parent_of = {}
    by_id[node["id"]] = node
    for child in node.get("children", []):
        parent_of[child["id"]] = node["id"]
        index_tree(child, by_id, parent_of)
    return by_id, parent_of


def collect_descendant_leaf_ids(node: dict) -> list[str]:
    """All leaf node IDs under `node` (including itself if it's a leaf)."""
    if not node.get("children"):
        return [node["id"]]
    out: list[str] = []
    for c in node["children"]:
        out.extend(collect_descendant_leaf_ids(c))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tree", type=Path, required=True,
                        help="Taxonomy tree JSON (taxonomy_tree.json or "
                             "checkpoint_normalized.json)")
    parser.add_argument("--clusters", type=Path, required=True,
                        help="Cleaned leaf clusters JSON (clusters.json from "
                             "Stage 7 — has the post-coherence paper_ids)")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output path for topic_level_clusters.json")
    parser.add_argument("--max-topic-size", type=int, default=150,
                        help="Aggregate ancestors until total paper count <= this")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Load tree (drop any pipeline-internal field)
    with open(args.tree, encoding="utf-8") as f:
        tree = json.load(f)
    tree.pop("_pipeline_stage", None)
    by_id, parent_of = index_tree(tree)
    log.info("Tree nodes: %d", len(by_id))

    # Load cleaned leaf clusters (Stage 7 output: outliers already kicked)
    with open(args.clusters, encoding="utf-8") as f:
        clusters = json.load(f)
    leaf_papers: dict[str, list[str]] = {c["cluster_id"]: list(c.get("paper_ids", []))
                                          for c in clusters}
    leaf_label: dict[str, str] = {c["cluster_id"]: c.get("label", "") for c in clusters}
    leaf_desc: dict[str, str] = {c["cluster_id"]: c.get("description", "") for c in clusters}
    log.info("Leaf clusters from clusters.json: %d", len(leaf_papers))

    # Compute total cleaned papers under each tree node (union, dedup across multi-label)
    def collect_papers(node: dict) -> set[str]:
        nid = node["id"]
        if not node.get("children"):
            return set(leaf_papers.get(nid, []))
        out: set[str] = set()
        for c in node["children"]:
            out |= collect_papers(c)
        return out
    papers_under: dict[str, set[str]] = {}
    def fill(node: dict) -> None:
        papers_under[node["id"]] = collect_papers(node)
        for c in node.get("children", []):
            fill(c)
    fill(tree)

    # For each surviving leaf cluster, walk UP until the ancestor's total
    # paper count <= max_topic_size. Skip ancestors that are an Other bucket.
    leaf_to_topic: dict[str, str] = {}
    skipped_other_leaves: list[str] = []
    for leaf_id in leaf_papers:
        if is_other_id(leaf_id):
            skipped_other_leaves.append(leaf_id)
            continue
        # Walk upward
        cur = leaf_id
        chosen = leaf_id  # fall back to the leaf itself if no ancestor qualifies
        while True:
            parent = parent_of.get(cur)
            if parent is None or parent == tree["id"]:
                break
            if is_other_id(parent):
                # Don't aggregate through an Other ancestor — anchor here
                break
            if len(papers_under.get(parent, set())) > args.max_topic_size:
                break
            chosen = parent
            cur = parent
        leaf_to_topic[leaf_id] = chosen

    # Group leaves by topic
    topics: dict[str, list[str]] = defaultdict(list)
    for leaf_id, topic_id in leaf_to_topic.items():
        topics[topic_id].append(leaf_id)

    # Build output records
    out_records = []
    for topic_id, leaf_ids in sorted(topics.items()):
        node = by_id.get(topic_id, {})
        # Union of paper_ids (cleaned) across all descendant leaves of this topic
        paper_ids: list[str] = []
        seen: set[str] = set()
        for lid in leaf_ids:
            for pid in leaf_papers.get(lid, []):
                if pid not in seen:
                    seen.add(pid)
                    paper_ids.append(pid)

        # Ancestor breadcrumb (skip root, stop at the topic itself)
        ancestors = []
        cur = parent_of.get(topic_id)
        while cur and cur != tree["id"]:
            anc = by_id.get(cur, {})
            ancestors.append({"id": cur, "label": anc.get("label", "")})
            cur = parent_of.get(cur)
        ancestors.reverse()  # root-ward → topic-ward

        # Leaf info
        leaves_info = []
        for lid in sorted(leaf_ids):
            leaves_info.append({
                "leaf_id": lid,
                "label": leaf_label.get(lid, ""),
                "description": leaf_desc.get(lid, ""),
                "n_papers": len(leaf_papers.get(lid, [])),
            })

        depth = topic_id.count("_")  # l1_NNN → 1, l1_NNN_MM → 2, ...
        out_records.append({
            "topic_id": topic_id,
            "label": node.get("label", ""),
            "description": node.get("description", ""),
            "depth": depth,
            "ancestors": ancestors,
            "leaves": leaves_info,
            "n_leaves": len(leaves_info),
            "n_papers": len(paper_ids),
            "paper_ids": paper_ids,
        })

    # Sort by depth then paper count desc
    out_records.sort(key=lambda r: (r["depth"], -r["n_papers"], r["topic_id"]))

    # Stats
    sizes = [r["n_papers"] for r in out_records]
    depths = [r["depth"] for r in out_records]
    log.info("Topic-level clusters: %d", len(out_records))
    if sizes:
        log.info("  paper-count: min=%d max=%d avg=%.1f total=%d (sum across topics)",
                 min(sizes), max(sizes), sum(sizes) / len(sizes), sum(sizes))
        unique_papers = set()
        for r in out_records:
            unique_papers.update(r["paper_ids"])
        log.info("  unique papers across all topics: %d", len(unique_papers))
    if depths:
        from collections import Counter
        c = Counter(depths)
        log.info("  depth distribution: %s", dict(sorted(c.items())))
    log.info("  skipped %d leaf 'Other' buckets", len(skipped_other_leaves))

    out = {
        "metadata": {
            "max_topic_size": args.max_topic_size,
            "n_topics": len(out_records),
            "n_leaves_included": sum(r["n_leaves"] for r in out_records),
            "n_leaves_skipped_other": len(skipped_other_leaves),
            "n_unique_papers": len({pid for r in out_records for pid in r["paper_ids"]}),
            "tree_source": str(args.tree),
            "clusters_source": str(args.clusters),
        },
        "topics": out_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log.info("Wrote %s (%.1f MB)", args.output, args.output.stat().st_size / 1024 / 1024)


if __name__ == "__main__":
    main()
