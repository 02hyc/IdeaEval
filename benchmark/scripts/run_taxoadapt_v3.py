#!/usr/bin/env python3
"""TaxoAdapt V3: Fixed pipeline with "dedup before assign" at each level.

Key change vs the original TaxoAdapt:
- At each level: generate subtopics → dedup → THEN assign papers
- Papers are never locked to a subtree before dedup

Two entry modes:
  (a) From scratch — builds an initial L1 taxonomy by sampling 300 diverse
      papers and asking the LLM to propose ~`--initial-topics` broad areas,
      then dedups L1 with a human-in-the-loop audit before continuing.
  (b) Warm start (`--v2-checkpoint`) — skip L1 build + L1 dedup, use the
      L1 nodes from a previously deduped checkpoint.

Stage machine (driven by checkpoint._pipeline_stage):
  Stage 0   Load data
  Stage 1a  Build initial L1 from sample (skipped if --v2-checkpoint)
  Stage 1D  L1 dedup → audit_L1_dedup.json → HumanAuditRequired (skipped if --v2-checkpoint)
  Stage 2   Assign papers to L1 (multi-label, global)
  Stage 3   Per-L1 parallel pseudo-label + L2 subtopic discovery (no paper assign)
  Stage 3D  L2 dedup → audit_L2_dedup.json → HumanAuditRequired
  Stage 3A  Apply dedup → globally re-assign papers to L2 leaves
  Stage 4+  Density-based expansion L3..L6
  Stage 5-7 Reassign unassigned, drop small clusters, RQ generation, coherence

Usage (from scratch):
    uv run python scripts/run_taxoadapt_v3.py \\
        --input <papers.jsonl> --fields <fields.jsonl> --benchmarks <benchmarks.jsonl> \\
        --output-dir experiments/clustering_v3 \\
        --initial-topics 20 --max-workers 100

Usage (warm start from V2):
    uv run python scripts/run_taxoadapt_v3.py \\
        --v2-checkpoint experiments/clustering_v2/checkpoint_normalized.json \\
        --input <papers.jsonl> ... (same as above)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="TaxoAdapt V3: dedup-before-assign")
    parser.add_argument("--input", required=True, help="Path to papers JSONL")
    parser.add_argument("--output-dir", default="experiments/clustering_v3")
    parser.add_argument("--benchmarks", default=None)
    parser.add_argument("--fields", default=None)
    parser.add_argument("--v2-checkpoint", type=Path, default=None,
                        help="Optional: warm-start from a deduped L1 checkpoint "
                             "(skips initial L1 build + L1 audit). "
                             "Omit to run from scratch.")
    parser.add_argument("--initial-topics", type=int, default=20,
                        help="Target number of L1 categories when building from scratch")
    parser.add_argument("--min-leaf", type=int, default=10)
    parser.add_argument("--max-leaf", type=int, default=50)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--max-workers", type=int, default=100,
                        help="Inner batch concurrency for each LLM stage")
    parser.add_argument("--max-parallel-leaves", type=int, default=50,
                        help="Outer concurrency: how many L1/leaf nodes expand "
                             "simultaneously during L2 discovery and L3+ expansion. "
                             "Peak LLM concurrency ≈ max_parallel_leaves × max_workers, "
                             "bounded above by OPENAI_MAX_CONNECTIONS. Lower this if "
                             "you hit 429s.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from ideaeval_benchmark.taxoadapt.models import (
        Config, TaxoNode, load_papers, load_benchmarks, load_fields,
    )
    from ideaeval_benchmark.taxoadapt.llm import LLMClient, RateLimiter, RPM_DEFAULT
    from ideaeval_benchmark.taxoadapt.pipeline import (
        HumanAuditRequired, _assign_papers, _dedup_level, _apply_dedup,
        _expand_leaves_parallel, _expand_loop,
        _generate_final_labels, _verify_coherence,
        _save_checkpoint, _load_checkpoint, _log_tree_stats, _write_json,
        _pseudo_label_papers, _discover_subtopics, _merge_small_children,
        _build_initial_taxonomy, _sample_diverse,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "checkpoint.json"

    # LLM clients
    rate_limiter = RateLimiter(rpm=RPM_DEFAULT)
    llm = LLMClient(rate_limiter=rate_limiter)

    thinking_api_key = os.environ.get("THINKING_API_KEY", "")
    thinking_base_url = os.environ.get("THINKING_BASE_URL", "")
    thinking_model = os.environ.get("THINKING_MODEL", "")
    if thinking_api_key and thinking_base_url:
        thinking_llm = LLMClient(
            api_key=thinking_api_key,
            base_url=thinking_base_url,
            model=thinking_model or None,
        )
        log.info("Using separate thinking LLM: %s @ %s", thinking_model, thinking_base_url)
    else:
        thinking_llm = llm

    # Stage 0: Load data
    log.info("=== Stage 0: Loading data ===")
    papers = load_papers(Path(args.input))
    log.info("Loaded %d papers", len(papers))

    if args.benchmarks:
        bm_map = load_benchmarks(Path(args.benchmarks))
        for p in papers:
            if p.paper_id in bm_map:
                p.benchmarks = bm_map[p.paper_id]
        log.info("Attached benchmarks to %d papers", sum(1 for p in papers if p.benchmarks))

    if args.fields:
        fields_map = load_fields(Path(args.fields))
        for p in papers:
            if p.paper_id in fields_map:
                p.research_problem = fields_map[p.paper_id].get("research_problem", "")
                p.task_formulation = fields_map[p.paper_id].get("task_formulation", "")
        log.info("Attached fields to %d papers", sum(1 for p in papers if p.research_problem))

    paper_map = {p.paper_id: p for p in papers}

    config = Config(
        input_path=Path(args.input),
        output_dir=output_dir,
        min_leaf=args.min_leaf,
        max_leaf=args.max_leaf,
        max_depth=args.max_depth,
        seed=args.seed,
        max_workers=args.max_workers,
        max_parallel_leaves=args.max_parallel_leaves,
    )

    # Check checkpoint
    root, stage = _load_checkpoint(checkpoint_path)

    # ---- Fresh entry: build initial L1 (either from scratch or warm-start from V2) ----
    if root is None:
        if args.v2_checkpoint:
            log.info("=== Warm-start: loading L1 from %s ===", args.v2_checkpoint)
            with open(args.v2_checkpoint, encoding="utf-8") as f:
                v2_data = json.load(f)
            v2_data.pop("_pipeline_stage", "")

            root = TaxoNode(id="root", label="AI/ML Research", level=0)
            for child_data in v2_data.get("children", []):
                child = TaxoNode(
                    id=child_data["id"],
                    label=child_data.get("label", ""),
                    description=child_data.get("description", ""),
                    level=1,
                )
                root.children.append(child)
            log.info("L1 tree (warm-start): %d nodes", len(root.children))
            _save_checkpoint(root, checkpoint_path, stage="assign_l1")
            stage = "assign_l1"
        else:
            log.info("=== Stage 1a: Building initial L1 from sample (~%d topics) ===",
                     args.initial_topics)
            sample = _sample_diverse(papers, n=min(300, len(papers)), seed=config.seed)
            root = _build_initial_taxonomy(sample, thinking_llm, args.initial_topics)
            for child in root.children:
                child.children = []
            log.info("Initial L1 tree: %d nodes", len(root.children))

            # Dedup-before-assign: dedup L1 labels on semantic overlap (no paper
            # counts needed — the prompt asks for label/description overlap).
            log.info("=== Stage 1D: L1 deduplication (before paper assign) ===")
            audit_path = _dedup_level(root, level=1, llm=thinking_llm, output_dir=output_dir)
            _save_checkpoint(root, checkpoint_path, stage="audit_l1")
            raise HumanAuditRequired("L1 dedup", audit_path)

    # ---- Resume from L1 audit ----
    if stage == "audit_l1":
        log.info("=== Resuming: applying L1 dedup ===")
        audit_path = output_dir / "audit_L1_dedup.json"
        n_merged = _apply_dedup(root, audit_path, level=1)
        log.info("Applied %d L1 merges", n_merged)
        _log_tree_stats(root)
        _save_checkpoint(root, checkpoint_path, stage="assign_l1")
        stage = "assign_l1"

    # ---- Assign L1 (if not done already) + discover L2 subtopics ----
    if stage == "assign_l1":
        # If papers haven't been assigned to L1 yet (warm-start path), do it now.
        # In the from-scratch path papers were already assigned pre-dedup; merge
        # carried their paper_ids forward, so we re-run only if any L1 has none.
        if not any(n.paper_ids for n in root.children):
            log.info("=== Stage 2: Assigning %d papers to L1 (multi-label) ===", len(papers))
            _assign_papers(papers, root, llm, config.max_workers,
                           batch_size=50, multi_label=True)
            _log_tree_stats(root)

        # Stage 3: Expand L1→L2 (discovery only — generate subtopics but keep papers at L1)
        # Parallel: pseudo-label + discovery for all L1 nodes concurrently
        log.info("=== Stage 3: Discovering L2 subtopics in parallel (no paper assign yet) ===")
        import math
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _discover_for_l1(l1_node):
            if not l1_node.paper_ids:
                return l1_node, None, "no papers"
            n_papers = len(l1_node.paper_ids)

            pseudo_labels = _pseudo_label_papers(l1_node, paper_map, llm,
                                                 max_workers=config.max_workers)
            if len(pseudo_labels) < n_papers * 0.3:
                return l1_node, None, f"pseudo-label coverage too low ({len(pseudo_labels)}/{n_papers})"

            target_subtopics = max(3, min(8, math.ceil(
                n_papers / max(config.max_leaf * 0.7, 10))))
            subtopics = _discover_subtopics(l1_node, pseudo_labels, thinking_llm or llm,
                                            min_subtopics=3, max_subtopics=target_subtopics,
                                            target_min=config.min_leaf,
                                            target_max=config.max_leaf)
            if not subtopics or len(subtopics) < 2:
                return l1_node, None, "discovery failed"
            return l1_node, subtopics, "ok"

        l1_nodes_with_papers = [n for n in root.children if n.paper_ids]
        log.info("Discovering subtopics for %d L1 nodes in parallel", len(l1_nodes_with_papers))

        with ThreadPoolExecutor(max_workers=min(config.max_parallel_leaves, len(l1_nodes_with_papers))) as pool:
            futs = {pool.submit(_discover_for_l1, n): n for n in l1_nodes_with_papers}
            done = 0
            for fut in as_completed(futs):
                l1_node, subtopics, status = fut.result()
                done += 1
                if subtopics:
                    children = []
                    for j, st in enumerate(subtopics):
                        child = TaxoNode(
                            id=f"{l1_node.id}_{j+1:02d}",
                            label=st.get("label", f"Sub-topic {j+1}"),
                            description=st.get("description", ""),
                            level=l1_node.level + 1,
                        )
                        children.append(child)
                    l1_node.children = children
                    log.info("  [%d/%d] '%s' -> %d subtopics (%s)",
                             done, len(l1_nodes_with_papers), l1_node.label,
                             len(children), status)
                else:
                    log.warning("  [%d/%d] '%s' -> skipped (%s)",
                                done, len(l1_nodes_with_papers), l1_node.label, status)

        _log_tree_stats(root)

        # Stage 3D: L2 dedup → human audit
        log.info("=== Stage 3D: L2 deduplication (before paper assign) ===")
        audit_path = _dedup_level(root, level=2, llm=thinking_llm, output_dir=output_dir)
        _save_checkpoint(root, checkpoint_path, stage="audit_l2")
        raise HumanAuditRequired("L2 dedup", audit_path)

    # ---- Resume from L2 audit ----
    if stage == "audit_l2":
        log.info("=== Resuming: applying L2 dedup ===")
        audit_path = output_dir / "audit_L2_dedup.json"
        n_merged = _apply_dedup(root, audit_path, level=2)
        log.info("Applied %d L2 merges", n_merged)

        # NOW assign papers to L2 leaves (global multi-label)
        # First, collect all papers from L1 nodes that have children
        all_papers_to_assign = []
        for l1_node in root.children:
            if l1_node.children and l1_node.paper_ids:
                # Papers are still in l1_node from Stage 1, move them out for reassignment
                all_papers_to_assign.extend(l1_node.paper_ids)
                l1_node.paper_ids = []
            elif not l1_node.children:
                # L1 without subtopics — papers stay here
                pass

        # Also include papers from L1 nodes without children? No — they stay.
        unique_pids = set(all_papers_to_assign)
        papers_to_assign = [paper_map[pid] for pid in unique_pids if pid in paper_map]

        log.info("=== Stage 3A: Assigning %d papers to L2 leaves (global multi-label) ===",
                 len(papers_to_assign))
        _assign_papers(papers_to_assign, root, llm, config.max_workers,
                       batch_size=50, multi_label=True)
        _log_tree_stats(root)
        _save_checkpoint(root, checkpoint_path, stage="expansion")
        stage = "expansion"

    # ---- Expansion L3+ ----
    if stage == "expansion":
        log.info("=== Stage 4: Expansion L3+ (min_leaf=%d, max_leaf=%d, max_depth=%d) ===",
                 config.min_leaf, config.max_leaf, config.max_depth)
        _expand_loop(root, paper_map, llm, config, thinking_llm=thinking_llm)
        _log_tree_stats(root)
        _save_checkpoint(root, checkpoint_path, stage="post_expansion")
        stage = "post_expansion"

    # ---- Post-expansion ----
    if stage == "post_expansion":
        from datetime import datetime, timezone

        # Stage 4: Handle unassigned
        assigned_ids = set()
        for leaf in root.all_leaves():
            assigned_ids.update(leaf.paper_ids)
        unassigned = [p for p in papers if p.paper_id not in assigned_ids]
        if unassigned:
            log.info("=== Stage 4: Reassigning %d unassigned papers ===", len(unassigned))
            _assign_papers(unassigned, root, llm, config.max_workers,
                           batch_size=50, multi_label=True)

        # Stage 5: Output
        log.info("=== Stage 5: Output ===")
        _write_json(output_dir / "taxonomy_tree.json", root.to_full_dict())

        leaves = root.all_leaves()
        clusters = []
        dropped_papers = 0
        for leaf in leaves:
            if len(leaf.paper_ids) >= config.min_leaf:
                clusters.append({
                    "cluster_id": leaf.id,
                    "label": leaf.label,
                    "description": leaf.description,
                    "paper_ids": leaf.paper_ids,
                    "n_papers": len(leaf.paper_ids),
                    "depth": leaf.level,
                })
            elif leaf.paper_ids:
                dropped_papers += len(leaf.paper_ids)
        if dropped_papers:
            log.info("Dropped %d papers from %d clusters below min_leaf=%d",
                     dropped_papers,
                     sum(1 for l in leaves if 0 < len(l.paper_ids) < config.min_leaf),
                     config.min_leaf)
        _write_json(output_dir / "clusters_raw.json", clusters)

        # Stage 6: Final labels
        log.info("=== Stage 6: Generating research questions for %d clusters ===", len(clusters))
        clusters = _generate_final_labels(clusters, paper_map, thinking_llm,
                                          max_workers=config.max_workers)
        _write_json(output_dir / "clusters_labeled.json", clusters)

        # Stage 7: Coherence
        log.info("=== Stage 7: Coherence verification for %d clusters ===", len(clusters))
        clusters, n_kicked = _verify_coherence(clusters, paper_map, llm,
                                               min_leaf=config.min_leaf,
                                               max_workers=config.max_workers,
                                               batch_size=15,
                                               output_dir=output_dir)
        log.info("Stage 7 result: %d clusters remain, %d papers kicked", len(clusters), n_kicked)
        _write_json(output_dir / "clusters.json", clusters)

        # Stats
        sizes = [c["n_papers"] for c in clusters]
        in_range = sum(1 for s in sizes if config.min_leaf <= s <= config.max_leaf)
        total_covered = sum(sizes)

        result = {
            "n_clusters": len(clusters),
            "n_papers_covered": total_covered,
            "n_papers_total": len(papers),
            "coverage": total_covered / len(papers) if papers else 0,
            "clusters_in_range": in_range,
            "size_min": min(sizes) if sizes else 0,
            "size_max": max(sizes) if sizes else 0,
            "size_avg": sum(sizes) / len(sizes) if sizes else 0,
            "tree_depth": root.depth(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(output_dir / "stats.json", result)

        log.info("=== Done ===")
        log.info("  Clusters: %d (in [%d,%d] range: %d)",
                 len(clusters), config.min_leaf, config.max_leaf, in_range)
        log.info("  Coverage: %d/%d (%.1f%%)",
                 total_covered, len(papers), result["coverage"] * 100)

        print(f"\n{'='*60}")
        print(f"Clusters: {result['n_clusters']}")
        print(f"Coverage: {result['n_papers_covered']}/{result['n_papers_total']} ({result['coverage']*100:.1f}%)")
        print(f"Sizes: min={result['size_min']} max={result['size_max']} avg={result['size_avg']:.1f}")
        print(f"Output: {output_dir}")


if __name__ == "__main__":
    # Import here so it's available at module scope without triggering
    # the heavy imports inside main() on syntax check
    from ideaeval_benchmark.taxoadapt.pipeline import HumanAuditRequired as _HAR

    try:
        main()
    except _HAR as e:
        print(f"\n{'='*60}")
        print(f"HUMAN AUDIT REQUIRED — {e.stage}")
        print(f"Review and edit: {e.audit_path}")
        print(f"")
        print(f"  - Set 'action' to 'skip' for groups you do NOT want to merge")
        print(f"  - Edit 'suggested_label' / 'suggested_description' as needed")
        print(f"  - The first node_id in each group is the primary (kept) node")
        print(f"")
        print(f"Then re-run the same command to continue.")
        print(f"{'='*60}")
        sys.exit(0)
