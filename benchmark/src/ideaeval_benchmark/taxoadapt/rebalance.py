#!/usr/bin/env python3
"""Rebalance [10,50] tree's leaf nodes into [30,50] clusters via greedy merge.

Zero LLM calls — pure local computation on the existing taxonomy tree.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MIN_SIZE = 30
MAX_SIZE = 50


@dataclass
class LeafGroup:
    """A group of papers (possibly merged from multiple original leaves)."""
    label: str
    description: str
    paper_ids: list[str] = field(default_factory=list)
    source_labels: list[str] = field(default_factory=list)  # original leaf labels

    @property
    def size(self) -> int:
        return len(self.paper_ids)

    def merge(self, other: LeafGroup) -> LeafGroup:
        """Merge two groups. Keeps the larger group's label."""
        if self.size >= other.size:
            primary, secondary = self, other
        else:
            primary, secondary = other, self
        return LeafGroup(
            label=primary.label,
            description=primary.description,
            paper_ids=primary.paper_ids + secondary.paper_ids,
            source_labels=primary.source_labels + secondary.source_labels,
        )


def _collect_leaves(node: dict) -> list[LeafGroup]:
    """Collect all leaf nodes from a tree node."""
    if not node.get("children"):
        return [LeafGroup(
            label=node.get("label", ""),
            description=node.get("description", ""),
            paper_ids=list(node.get("paper_ids", [])),
            source_labels=[node.get("label", "")],
        )]
    leaves = []
    for child in node["children"]:
        leaves.extend(_collect_leaves(child))
    return leaves


def _greedy_merge(groups: list[LeafGroup], min_size: int, max_size: int) -> tuple[list[LeafGroup], list[LeafGroup]]:
    """Greedy merge small groups into [min_size, max_size] range.

    Returns (valid_groups, residual_groups_still_too_small).
    """
    if not groups:
        return [], []

    # Sort ascending by size
    groups.sort(key=lambda g: g.size)

    valid = []
    i = 0
    while i < len(groups):
        current = groups[i]
        i += 1

        # Accumulate until we reach min_size
        while current.size < min_size and i < len(groups):
            candidate = groups[i]
            if current.size + candidate.size <= max_size:
                current = current.merge(candidate)
                i += 1
            else:
                break

        if current.size >= min_size:
            valid.append(current)
        else:
            # Still too small — try merging with last valid group if it fits
            if valid and valid[-1].size + current.size <= max_size:
                valid[-1] = valid[-1].merge(current)
            else:
                # Return as residual
                valid.append(current)  # keep it, will be classified as residual later

    # Separate valid from residual
    final_valid = [g for g in valid if g.size >= min_size]
    residual = [g for g in valid if g.size < min_size]
    return final_valid, residual


def rebalance_node(node: dict, min_size: int = MIN_SIZE, max_size: int = MAX_SIZE) -> tuple[list[LeafGroup], list[LeafGroup]]:
    """Recursively rebalance a subtree. Returns (valid_clusters, residual_too_small).

    - Leaf nodes: return as-is (size 10-50 from original tree)
    - Internal nodes: collect children's results, merge smalls, pass residual up
    """
    # Leaf node
    if not node.get("children"):
        group = LeafGroup(
            label=node.get("label", ""),
            description=node.get("description", ""),
            paper_ids=list(node.get("paper_ids", [])),
            source_labels=[node.get("label", "")],
        )
        if min_size <= group.size <= max_size:
            return [group], []
        elif group.size < min_size:
            return [], [group]
        else:
            # Leaf > max_size shouldn't happen in [10,50] tree, but handle anyway
            return [group], []

    # Internal node: process children first
    all_valid = []
    all_residual = []
    for child in node["children"]:
        v, r = rebalance_node(child, min_size, max_size)
        all_valid.extend(v)
        all_residual.extend(r)

    # Try to merge residuals from this node's children
    if all_residual:
        merged_valid, still_residual = _greedy_merge(all_residual, min_size, max_size)
        all_valid.extend(merged_valid)
        all_residual = still_residual

    return all_valid, all_residual


def rebalance_tree(tree_path: Path, output_dir: Path,
                   min_size: int = MIN_SIZE, max_size: int = MAX_SIZE):
    """Load tree, rebalance, output clusters."""
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(tree_path, encoding="utf-8") as f:
        tree = json.load(f)

    log.info("Loaded tree from %s", tree_path)

    # Count original leaves
    orig_leaves = _collect_leaves(tree)
    log.info("Original: %d leaves, %d papers, sizes: [%d, %d] avg=%.1f",
             len(orig_leaves), sum(g.size for g in orig_leaves),
             min(g.size for g in orig_leaves), max(g.size for g in orig_leaves),
             sum(g.size for g in orig_leaves) / len(orig_leaves))

    # Rebalance
    valid, residual = rebalance_node(tree, min_size, max_size)

    # Handle final residuals: merge into one if possible, or keep as-is
    if residual:
        total_residual = sum(g.size for g in residual)
        log.info("Final residual: %d groups, %d papers total", len(residual), total_residual)
        # Try one more merge
        merged_v, still_r = _greedy_merge(residual, min_size, max_size)
        valid.extend(merged_v)
        # Accept remaining as oversized single group or drop
        for g in still_r:
            if g.size >= 10:  # keep anything with reasonable size
                valid.append(g)
                log.info("  Keeping residual: %d papers (%s)", g.size, g.label[:50])
            else:
                log.info("  Dropping residual: %d papers (%s)", g.size, g.label[:50])

    # Build clusters output
    clusters = []
    for i, g in enumerate(valid):
        clusters.append({
            "cluster_id": f"rb_{i+1:04d}",
            "label": g.label,
            "description": g.description,
            "paper_ids": g.paper_ids,
            "n_papers": g.size,
            "source_labels": g.source_labels,
            "n_sources": len(g.source_labels),
        })

    # Sort by size descending
    clusters.sort(key=lambda c: -c["n_papers"])

    # Stats
    sizes = [c["n_papers"] for c in clusters]
    in_range = sum(1 for s in sizes if min_size <= s <= max_size)
    below = sum(1 for s in sizes if s < min_size)
    above = sum(1 for s in sizes if s > max_size)

    stats = {
        "n_clusters": len(clusters),
        "n_papers_covered": sum(sizes),
        "n_papers_original": sum(g.size for g in orig_leaves),
        "coverage": sum(sizes) / sum(g.size for g in orig_leaves) if orig_leaves else 0,
        "size_min": min(sizes) if sizes else 0,
        "size_max": max(sizes) if sizes else 0,
        "size_avg": round(sum(sizes) / len(sizes), 1) if sizes else 0,
        "in_range": in_range,
        "below_min": below,
        "above_max": above,
        "min_size": min_size,
        "max_size": max_size,
    }

    # Save
    with open(output_dir / "clusters.json", "w", encoding="utf-8") as f:
        json.dump(clusters, f, ensure_ascii=False, indent=2)
    with open(output_dir / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    log.info("Rebalanced: %d clusters, %d papers", len(clusters), sum(sizes))
    log.info("  In [%d,%d]: %d | Below: %d | Above: %d",
             min_size, max_size, in_range, below, above)
    log.info("  Sizes: min=%d max=%d avg=%.1f", stats["size_min"], stats["size_max"], stats["size_avg"])

    return stats


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    tree_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("clustering_results_taxoadapt/taxonomy_tree.json")
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("clustering_results_taxoadapt_rebalanced")

    stats = rebalance_tree(tree_path, output_dir)
    print(json.dumps(stats, indent=2))
