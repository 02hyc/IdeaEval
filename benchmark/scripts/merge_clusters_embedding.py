#!/usr/bin/env python3
"""Merge rebalanced clusters using embedding similarity within level-1 subtree constraints.

Halves the cluster count (~959 → ~480) by iteratively merging the most similar
pair of clusters within each level-1 domain, subject to a max-size cap.

Usage:
    uv run python scripts/merge_clusters_embedding.py \
        --clusters experiments/clustering_results_taxoadapt_rebalanced/clusters.json \
        --tree experiments/clustering_results_taxoadapt/taxonomy_tree.json \
        --output-dir experiments/clustering_results_merged \
        --target-count 480 \
        --max-size 100
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)


def build_leaf_to_l1(node: dict, l1_label: str | None = None) -> dict[str, str]:
    mapping = {}
    if node.get("children"):
        for child in node["children"]:
            child_l1 = l1_label if l1_label else child.get("label", "")
            mapping.update(build_leaf_to_l1(child, child_l1))
    else:
        mapping[node.get("label", "")] = l1_label or ""
    return mapping


def assign_cluster_to_l1(cluster: dict, leaf_to_l1: dict[str, str]) -> str:
    """Assign a cluster to its dominant level-1 domain."""
    from collections import Counter
    domains = [leaf_to_l1[sl] for sl in cluster["source_labels"] if sl in leaf_to_l1]
    if not domains:
        return "Unknown"
    return Counter(domains).most_common(1)[0][0]


def merge_clusters(c1: dict, c2: dict, new_id: str) -> dict:
    """Merge two clusters into one, keeping the larger cluster's label."""
    if c1["n_papers"] >= c2["n_papers"]:
        primary, secondary = c1, c2
    else:
        primary, secondary = c2, c1
    return {
        "cluster_id": new_id,
        "label": primary["label"],
        "description": primary["description"],
        "paper_ids": primary["paper_ids"] + secondary["paper_ids"],
        "n_papers": primary["n_papers"] + secondary["n_papers"],
        "source_labels": primary["source_labels"] + secondary["source_labels"],
        "n_sources": primary["n_sources"] + secondary["n_sources"],
    }


def run_merge(
    clusters_path: Path,
    tree_path: Path,
    output_dir: Path,
    target_count: int = 480,
    max_size: int = 100,
    model_name: str = "BAAI/bge-large-en-v1.5",
):
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(clusters_path, encoding="utf-8") as f:
        clusters = json.load(f)
    with open(tree_path, encoding="utf-8") as f:
        tree = json.load(f)

    log.info("Loaded %d clusters, target: %d", len(clusters), target_count)

    # Build level-1 mapping
    leaf_to_l1 = build_leaf_to_l1(tree)

    # Assign each cluster to a level-1 domain
    for c in clusters:
        c["_domain"] = assign_cluster_to_l1(c, leaf_to_l1)

    # Group clusters by domain
    from collections import defaultdict
    domain_groups: dict[str, list[dict]] = defaultdict(list)
    for c in clusters:
        domain_groups[c["_domain"]].append(c)

    log.info("Domains: %d, cluster distribution: %s",
             len(domain_groups),
             {k: len(v) for k, v in sorted(domain_groups.items(), key=lambda x: -len(x[1]))})

    # Compute embeddings for all clusters
    log.info("Loading embedding model: %s", model_name)
    model = SentenceTransformer(model_name)

    texts = [f"{c['label']}. {c['description']}" for c in clusters]
    log.info("Encoding %d cluster descriptions...", len(texts))
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True, batch_size=64)
    embeddings = np.array(embeddings)

    # Build index: cluster_id -> position
    id_to_idx = {c["cluster_id"]: i for i, c in enumerate(clusters)}

    # Agglomerative merge within each domain
    merged_clusters = []
    total_merges = 0

    for domain, group in sorted(domain_groups.items(), key=lambda x: -len(x[1])):
        n_in_domain = len(group)
        # Proportional target for this domain
        domain_target = max(1, round(target_count * n_in_domain / len(clusters)))

        log.info("Domain '%s': %d clusters -> target %d", domain[:50], n_in_domain, domain_target)

        # Extract embeddings for this domain
        indices = [id_to_idx[c["cluster_id"]] for c in group]
        domain_embs = embeddings[indices]  # shape: (n, dim)

        # Compute pairwise cosine similarity (embeddings are normalized)
        sim_matrix = domain_embs @ domain_embs.T

        # Active set tracking
        active = list(range(n_in_domain))
        active_clusters = list(group)
        active_embs = domain_embs.copy()

        while len(active) > domain_target:
            n = len(active)
            if n <= 1:
                break

            # Find best merge pair (highest similarity, respecting max_size)
            best_sim = -1.0
            best_i, best_j = -1, -1

            # Recompute sim for active set
            sim = active_embs @ active_embs.T
            np.fill_diagonal(sim, -np.inf)

            # Sort by similarity descending, find first valid pair
            flat_indices = np.argsort(sim.ravel())[::-1]
            for flat_idx in flat_indices:
                i, j = divmod(int(flat_idx), n)
                if i >= j:
                    continue
                merged_size = active_clusters[i]["n_papers"] + active_clusters[j]["n_papers"]
                if merged_size <= max_size:
                    best_i, best_j = i, j
                    best_sim = sim[i, j]
                    break

            if best_i == -1:
                log.warning("Domain '%s': no valid merge pair found, stopping at %d clusters",
                           domain[:40], len(active))
                break

            # Merge
            c_new = merge_clusters(
                active_clusters[best_i],
                active_clusters[best_j],
                f"mg_{total_merges:04d}",
            )
            total_merges += 1

            # New embedding: weighted average of merged pair
            w_i = active_clusters[best_i]["n_papers"]
            w_j = active_clusters[best_j]["n_papers"]
            new_emb = (active_embs[best_i] * w_i + active_embs[best_j] * w_j) / (w_i + w_j)
            new_emb = new_emb / np.linalg.norm(new_emb)

            # Remove old, add new
            keep = [k for k in range(n) if k != best_i and k != best_j]
            active_clusters = [active_clusters[k] for k in keep] + [c_new]
            active_embs = np.vstack([active_embs[keep], new_emb.reshape(1, -1)])
            active = list(range(len(active_clusters)))

        merged_clusters.extend(active_clusters)

    # Clean up internal fields and re-number
    final_clusters = []
    for i, c in enumerate(sorted(merged_clusters, key=lambda x: -x["n_papers"])):
        c.pop("_domain", None)
        c["cluster_id"] = f"mg_{i+1:04d}"
        final_clusters.append(c)

    # Stats
    sizes = [c["n_papers"] for c in final_clusters]
    stats = {
        "n_clusters": len(final_clusters),
        "n_papers_covered": sum(sizes),
        "size_min": min(sizes),
        "size_max": max(sizes),
        "size_avg": round(sum(sizes) / len(sizes), 1),
        "in_range_60_100": sum(1 for s in sizes if 60 <= s <= 100),
        "below_60": sum(1 for s in sizes if s < 60),
        "above_100": sum(1 for s in sizes if s > 100),
        "target_count": target_count,
        "max_size_constraint": max_size,
        "model": model_name,
    }

    # Save
    with open(output_dir / "clusters.json", "w", encoding="utf-8") as f:
        json.dump(final_clusters, f, ensure_ascii=False, indent=2)
    with open(output_dir / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    log.info("Done: %d clusters, %d papers", stats["n_clusters"], stats["n_papers_covered"])
    log.info("Sizes: min=%d max=%d avg=%.1f", stats["size_min"], stats["size_max"], stats["size_avg"])
    log.info("In [60,100]: %d | Below 60: %d | Above 100: %d",
             stats["in_range_60_100"], stats["below_60"], stats["above_100"])

    return stats


def main():
    parser = argparse.ArgumentParser(description="Merge clusters using embedding similarity")
    parser.add_argument("--clusters", type=Path, required=True, help="Rebalanced clusters.json")
    parser.add_argument("--tree", type=Path, required=True, help="Original taxonomy_tree.json")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--target-count", type=int, default=480, help="Target number of clusters")
    parser.add_argument("--max-size", type=int, default=100, help="Max papers per merged cluster")
    parser.add_argument("--model", type=str, default="BAAI/bge-large-en-v1.5", help="Embedding model")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    stats = run_merge(
        clusters_path=args.clusters,
        tree_path=args.tree,
        output_dir=args.output_dir,
        target_count=args.target_count,
        max_size=args.max_size,
        model_name=args.model,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
