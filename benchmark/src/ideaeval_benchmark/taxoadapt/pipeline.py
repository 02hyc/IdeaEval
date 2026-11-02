"""Core TaxoAdapt pipeline: density-based iterative taxonomy expansion."""

from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .llm import LLMClient, RPM_DEFAULT, llm_call, parse_json
from .models import Config, PaperRecord, TaxoNode
from .prompts import (
    ASSIGN_MULTI_SYSTEM, ASSIGN_SYSTEM, ASSIGN_USER,
    COHERENCE_CHECK_SYSTEM, COHERENCE_CHECK_USER,
    DEDUP_SYSTEM, DEDUP_USER,
    FINAL_LABEL_SYSTEM, FINAL_LABEL_USER,
    INITIAL_TAXONOMY_SYSTEM, INITIAL_TAXONOMY_USER,
    PSEUDO_LABEL_SYSTEM, PSEUDO_LABEL_USER,
    SUBTOPIC_CLUSTER_SYSTEM, SUBTOPIC_CLUSTER_USER,
)

log = logging.getLogger(__name__)


class HumanAuditRequired(Exception):
    """Raised when the pipeline needs human review before continuing."""

    def __init__(self, stage: str, audit_path: Path, message: str = ""):
        self.stage = stage
        self.audit_path = audit_path
        super().__init__(message or f"Human audit required at {stage}. Review: {audit_path}")


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _paper_to_prompt_dict(p: PaperRecord) -> dict:
    """Format a paper for LLM prompt with all available fields."""
    d: dict = {"paper_id": p.paper_id, "title": p.title}
    d["abstract"] = p.abstract
    if p.research_problem:
        d["research_problem"] = p.research_problem
    if p.task_formulation:
        d["task_formulation"] = p.task_formulation
    if p.benchmarks:
        d["benchmarks"] = p.benchmarks[:10]
    return d


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(config: Config) -> dict:
    """Run the full TaxoAdapt pipeline."""
    from .llm import RateLimiter

    config.output_dir.mkdir(parents=True, exist_ok=True)
    rate_limiter = RateLimiter(rpm=RPM_DEFAULT)
    llm = LLMClient(rate_limiter=rate_limiter)

    # Optional secondary LLM for thinking-heavy calls (subtopic discovery, dedup)
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
    from .models import load_papers, load_benchmarks, load_fields
    papers = load_papers(config.input_path)
    log.info("Loaded %d papers", len(papers))

    if config.benchmarks_path and config.benchmarks_path.exists():
        bm_map = load_benchmarks(config.benchmarks_path)
        for p in papers:
            if p.paper_id in bm_map:
                p.benchmarks = bm_map[p.paper_id]
        log.info("Attached benchmarks to %d papers",
                 sum(1 for p in papers if p.benchmarks))

    if config.fields_path and config.fields_path.exists():
        fields_map = load_fields(config.fields_path)
        for p in papers:
            if p.paper_id in fields_map:
                p.research_problem = fields_map[p.paper_id].get("research_problem", "")
                p.task_formulation = fields_map[p.paper_id].get("task_formulation", "")
        log.info("Attached fields to %d papers",
                 sum(1 for p in papers if p.research_problem))

    paper_map = {p.paper_id: p for p in papers}

    # Check for checkpoint
    checkpoint_path = config.output_dir / "checkpoint.json"
    root, stage = _load_checkpoint(checkpoint_path)

    # ---- Fresh run: Stage 1 → Stage 2 → L1 dedup audit ----
    if root is None:
        # Stage 1: Initial taxonomy
        log.info("=== Stage 1: Building initial taxonomy ===")
        sample = _sample_diverse(papers, n=min(300, len(papers)), seed=config.seed)
        root = _build_initial_taxonomy(sample, thinking_llm, config.initial_topics)
        log.info("Initial taxonomy: %d L1 nodes", len(root.children))

        # Stage 2: Assign all papers (multi-label)
        log.info("=== Stage 2: Assigning %d papers (multi-label) ===", len(papers))
        _assign_papers(papers, root, llm, config.max_workers,
                       batch_size=50, multi_label=True)
        _log_tree_stats(root)

        # Stage 2D: L1 dedup → human audit
        log.info("=== Stage 2D: L1 deduplication ===")
        audit_path = _dedup_level(root, level=1, llm=thinking_llm, output_dir=config.output_dir)
        _save_checkpoint(root, checkpoint_path, stage="audit_l1")
        raise HumanAuditRequired("L1 dedup", audit_path)

    # ---- Resume from L1 audit ----
    if stage == "audit_l1":
        log.info("=== Resuming: applying L1 dedup ===")
        audit_path = config.output_dir / "audit_L1_dedup.json"
        n_merged = _apply_dedup(root, audit_path, level=1)
        log.info("Applied %d L1 merges", n_merged)
        _log_tree_stats(root)

        # Stage 3a: Expand L1 → L2 (one level only)
        log.info("=== Stage 3a: Expanding L1 → L2 ===")
        _expand_one_level(root, paper_map, llm, config, thinking_llm=thinking_llm)
        _log_tree_stats(root)

        # Stage 3D: L2 dedup → human audit
        log.info("=== Stage 3D: L2 deduplication ===")
        audit_path = _dedup_level(root, level=2, llm=thinking_llm, output_dir=config.output_dir)
        _save_checkpoint(root, checkpoint_path, stage="audit_l2")
        raise HumanAuditRequired("L2 dedup", audit_path)

    # ---- Resume from L2 audit ----
    if stage == "audit_l2":
        log.info("=== Resuming: applying L2 dedup ===")
        audit_path = config.output_dir / "audit_L2_dedup.json"
        n_merged = _apply_dedup(root, audit_path, level=2)
        log.info("Applied %d L2 merges", n_merged)
        _log_tree_stats(root)
        _save_checkpoint(root, checkpoint_path, stage="expansion")
        stage = "expansion"

    # ---- Expansion L3+ ----
    if stage == "expansion":
        log.info("=== Stage 3b: Expansion L3+ (min_leaf=%d, max_leaf=%d, max_depth=%d) ===",
                 config.min_leaf, config.max_leaf, config.max_depth)
        _expand_loop(root, paper_map, llm, config, thinking_llm=thinking_llm)
        _log_tree_stats(root)
        _save_checkpoint(root, checkpoint_path, stage="post_expansion")
        stage = "post_expansion"

    # ---- Post-expansion stages ----
    if stage == "post_expansion":
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
        _write_json(config.output_dir / "taxonomy_tree.json", root.to_full_dict())

        # Build result — filter out clusters < min_leaf
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

        _write_json(config.output_dir / "clusters_raw.json", clusters)

        # Stage 6: Final label generation
        log.info("=== Stage 6: Generating research questions for %d clusters ===", len(clusters))
        clusters = _generate_final_labels(clusters, paper_map, thinking_llm,
                                          max_workers=config.max_workers)
        _write_json(config.output_dir / "clusters_labeled.json", clusters)

        # Stage 7: Coherence verification
        log.info("=== Stage 7: Coherence verification for %d clusters ===", len(clusters))
        clusters, n_kicked = _verify_coherence(clusters, paper_map, llm,
                                               min_leaf=config.min_leaf,
                                               max_workers=config.max_workers,
                                               batch_size=15,
                                               output_dir=config.output_dir)
        log.info("Stage 7 result: %d clusters remain, %d papers kicked", len(clusters), n_kicked)
        _write_json(config.output_dir / "clusters.json", clusters)

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
            "clusters_total": len(clusters),
            "size_min": min(sizes) if sizes else 0,
            "size_max": max(sizes) if sizes else 0,
            "size_avg": sum(sizes) / len(sizes) if sizes else 0,
            "tree_depth": root.depth(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(config.output_dir / "stats.json", result)
        _write_json(config.output_dir / "final_summary.json", result)

        log.info("=== Done ===")
        log.info("  Clusters: %d (in [%d,%d] range: %d)",
                 len(clusters), config.min_leaf, config.max_leaf, in_range)
        log.info("  Coverage: %d/%d (%.1f%%)",
                 total_covered, len(papers), result["coverage"] * 100)
        log.info("  Sizes: min=%d max=%d avg=%.1f",
                 result["size_min"], result["size_max"], result["size_avg"])

        return result

    return {}


# ---------------------------------------------------------------------------
# Stage 1: Initial taxonomy
# ---------------------------------------------------------------------------

def _build_initial_taxonomy(sample: list[PaperRecord], llm: LLMClient,
                            n_topics: int) -> TaxoNode:
    summaries = "\n\n".join(
        f"[{p.paper_id}] {p.title}\nAbstract: {p.abstract[:300]}..."
        for p in sample
    )
    raw = llm.generate(
        system=INITIAL_TAXONOMY_SYSTEM.format(n_topics=n_topics),
        user=INITIAL_TAXONOMY_USER.format(
            n_papers=len(sample), n_topics=n_topics, paper_summaries=summaries),
        max_tokens=65536, temperature=0.2,
    )
    tree_dict = parse_json(raw)
    if not isinstance(tree_dict, dict) or not tree_dict.get("children"):
        log.error("Failed to parse initial taxonomy, creating fallback")
        return TaxoNode(id="root", label="AI/ML Research")

    root = _dict_to_tree(tree_dict, level=0)
    for child in root.children:
        child.children = []
    return root


def _dict_to_tree(d: dict, level: int = 0) -> TaxoNode:
    node = TaxoNode(
        id=d.get("id", f"node_{level}"),
        label=d.get("label", "Unknown"),
        description=d.get("description", ""),
        level=level,
    )
    for child_dict in d.get("children", []):
        node.children.append(_dict_to_tree(child_dict, level=level + 1))
    return node


# ---------------------------------------------------------------------------
# Stage 2: Paper assignment (multi-label capable)
# ---------------------------------------------------------------------------

def _assign_papers(papers: list[PaperRecord], root: TaxoNode,
                   llm: LLMClient,
                   max_workers: int, batch_size: int = 50,
                   multi_label: bool = True) -> None:
    tree_json = json.dumps(root.to_dict(), ensure_ascii=False, indent=1)
    leaf_ids = {leaf.id for leaf in root.all_leaves()}
    batches = list(_chunked(papers, batch_size))
    log.info("Assigning %d papers in %d batches (workers=%d, multi_label=%s)",
             len(papers), len(batches), max_workers, multi_label)

    system_prompt = ASSIGN_MULTI_SYSTEM if multi_label else ASSIGN_SYSTEM

    def _process_batch(batch: list[PaperRecord]) -> list[dict]:
        items = [_paper_to_prompt_dict(p) for p in batch]
        papers_json = json.dumps(items, ensure_ascii=False, indent=1)
        result = llm_call(
            llm,
            system_prompt,
            ASSIGN_USER.format(tree_json=tree_json, papers_json=papers_json),
            thinking="disabled")
        return result if isinstance(result, list) else []

    assigned_count = 0
    all_results: list[tuple[int, list[dict]]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_batch, b): i for i, b in enumerate(batches)}
        done = 0
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results = fut.result()
                all_results.append((idx, results))
            except Exception as e:
                log.warning("Batch %d failed: %s", idx, e)
            done += 1
            if done % 100 == 0:
                log.info("  Assignment: %d/%d batches done", done, len(batches))

    def _place_paper(pid: str, nid: str) -> bool:
        node = root.find_node(nid)
        if node and node.is_leaf:
            node.paper_ids.append(pid)
            return True
        if nid in leaf_ids:
            for leaf in root.all_leaves():
                if leaf.id == nid:
                    leaf.paper_ids.append(pid)
                    return True
        node = root.find_node(nid)
        if node and node.children:
            leaf = node.all_leaves()[0]
            leaf.paper_ids.append(pid)
            return True
        return False

    if multi_label:
        for _idx, results in sorted(all_results, key=lambda x: x[0]):
            for item in results:
                pid = item.get("paper_id", "")
                if not pid:
                    continue
                node_ids = item.get("node_ids", [])
                if not node_ids:
                    nid = item.get("node_id", item.get("category_id", ""))
                    if nid:
                        node_ids = [nid]
                for nid in node_ids:
                    if _place_paper(pid, nid):
                        assigned_count += 1
    else:
        already_assigned: set[str] = set()
        for _idx, results in sorted(all_results, key=lambda x: x[0]):
            for item in results:
                pid = item.get("paper_id", "")
                if not pid or pid in already_assigned:
                    continue
                nid = item.get("node_id", item.get("category_id", ""))
                if nid and _place_paper(pid, nid):
                    already_assigned.add(pid)
                    assigned_count += 1

    log.info("Assigned %d paper-node pairs (%d papers)", assigned_count, len(papers))


# ---------------------------------------------------------------------------
# Stage 2D/3D: Deduplication
# ---------------------------------------------------------------------------

def _dedup_level(root: TaxoNode, level: int, llm: LLMClient,
                 output_dir: Path) -> Path:
    """Ask LLM to identify overlapping nodes at a given level, write audit report."""
    nodes = root.nodes_at_level(level)
    log.info("Dedup level %d: %d nodes", level, len(nodes))

    nodes_info = []
    for n in nodes:
        n_papers = n.density if n.children else len(n.paper_ids)
        nodes_info.append({
            "id": n.id,
            "label": n.label,
            "description": n.description,
            "n_papers": n_papers,
        })

    nodes_json = json.dumps(nodes_info, ensure_ascii=False, indent=2)

    result = llm_call(
        llm,
        DEDUP_SYSTEM.format(level=level),
        DEDUP_USER.format(level=level, n_nodes=len(nodes), nodes_json=nodes_json),
        max_tokens=65536)

    overlap_groups = result if isinstance(result, list) else []

    node_id_set = {n.id for n in nodes}
    grouped_ids: set[str] = set()
    audit_groups = []
    for i, group in enumerate(overlap_groups):
        group_node_ids = group.get("group", [])
        group_node_ids = [nid for nid in group_node_ids if nid in node_id_set]
        if len(group_node_ids) < 2:
            continue
        for nid in group_node_ids:
            grouped_ids.add(nid)

        current_labels = []
        paper_counts = []
        for nid in group_node_ids:
            node = root.find_node(nid)
            if node:
                current_labels.append(node.label)
                paper_counts.append(node.density if node.children else len(node.paper_ids))

        audit_groups.append({
            "group_id": i + 1,
            "action": "merge",
            "node_ids": group_node_ids,
            "current_labels": current_labels,
            "paper_counts": paper_counts,
            "suggested_label": group.get("suggested_label", current_labels[0] if current_labels else ""),
            "suggested_description": group.get("suggested_description", ""),
            "reason": group.get("reason", ""),
        })

    unchanged = [{"id": n.id, "label": n.label}
                 for n in nodes if n.id not in grouped_ids]

    audit_report = {
        "level": level,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instructions": (
            "Review each group below. Set 'action' to 'merge' to merge or 'skip' to keep separate. "
            "Edit 'suggested_label' and 'suggested_description' as needed. "
            "The first node_id in each group will be the primary (kept) node."
        ),
        "groups": audit_groups,
        "unchanged": unchanged,
    }

    audit_path = output_dir / f"audit_L{level}_dedup.json"
    _write_json(audit_path, audit_report)
    log.info("Dedup audit report: %d overlap groups, %d unchanged nodes -> %s",
             len(audit_groups), len(unchanged), audit_path)
    return audit_path


def _apply_dedup(root: TaxoNode, audit_path: Path, level: int) -> int:
    """Apply human-reviewed dedup merges."""
    with open(audit_path, encoding="utf-8") as f:
        audit = json.load(f)

    n_merged = 0
    for group in audit.get("groups", []):
        if group.get("action") != "merge":
            log.info("Skipping group %s (action=%s)", group.get("group_id"), group.get("action"))
            continue

        node_ids = group.get("node_ids", [])
        if len(node_ids) < 2:
            continue

        primary = root.find_node(node_ids[0])
        if not primary:
            log.warning("Primary node %s not found, skipping", node_ids[0])
            continue

        primary.label = group.get("suggested_label", primary.label)
        primary.description = group.get("suggested_description", primary.description)

        for sec_id in node_ids[1:]:
            secondary = root.find_node(sec_id)
            if not secondary:
                log.warning("Secondary node %s not found, skipping", sec_id)
                continue

            if secondary.paper_ids:
                existing = set(primary.paper_ids)
                new_pids = [pid for pid in secondary.paper_ids if pid not in existing]
                primary.paper_ids.extend(new_pids)
                n_deduped = len(secondary.paper_ids) - len(new_pids)
                if n_deduped:
                    log.info("Deduped %d overlapping papers during merge", n_deduped)
            if secondary.children:
                primary.children.extend(secondary.children)

            _remove_node_from_parent(root, sec_id)
            n_merged += 1
            log.info("Merged '%s' into '%s'", secondary.label, primary.label)

    return n_merged


def _remove_node_from_parent(root: TaxoNode, node_id: str) -> bool:
    """Remove a node from its parent's children list."""
    for child in root.children:
        if child.id == node_id:
            root.children.remove(child)
            return True
        if _remove_node_from_parent(child, node_id):
            return True
    return False


# ---------------------------------------------------------------------------
# Stage 3: Expansion
# ---------------------------------------------------------------------------

def _expand_leaves_parallel(leaves: list[TaxoNode], paper_map: dict[str, PaperRecord],
                            llm: LLMClient, min_leaf: int, max_leaf: int,
                            max_workers: int, max_parallel_leaves: int = 50,
                            thinking_llm: Optional[LLMClient] = None) -> int:
    """Expand multiple leaves in parallel. Returns count of successfully expanded."""
    expanded = 0

    def _try_expand(leaf: TaxoNode) -> tuple[TaxoNode, bool]:
        try:
            success = _expand_single_leaf(leaf, paper_map, llm,
                                          min_leaf, max_leaf, max_workers,
                                          thinking_llm=thinking_llm)
            return leaf, success
        except Exception as e:
            log.warning("Expansion of '%s' failed: %s", leaf.label, e)
            return leaf, False

    with ThreadPoolExecutor(max_workers=max_parallel_leaves) as pool:
        futs = {pool.submit(_try_expand, leaf): i for i, leaf in enumerate(leaves)}
        done = 0
        for fut in as_completed(futs):
            idx = futs[fut]
            leaf, success = fut.result()
            done += 1
            if success:
                expanded += 1
                log.info("  [%d/%d] Expanded '%s' (%d children)",
                         done, len(leaves), leaf.label, len(leaf.children))
            else:
                leaf.expansion_resistant = True
                log.info("  [%d/%d] Failed to expand '%s'", done, len(leaves), leaf.label)

    return expanded


def _expand_one_level(root: TaxoNode, paper_map: dict[str, PaperRecord],
                      llm: LLMClient, config: Config,
                      thinking_llm: Optional[LLMClient] = None) -> None:
    """Expand dense leaves ONE level only (used for L1→L2 before dedup)."""
    dense_leaves = [
        leaf for leaf in root.all_leaves()
        if len(leaf.paper_ids) > config.max_leaf
        and leaf.level < config.max_depth
        and not leaf.expansion_resistant
    ]
    if not dense_leaves:
        log.info("No dense leaves to expand")
        return

    log.info("Expanding %d dense leaves in parallel (one level)", len(dense_leaves))
    _expand_leaves_parallel(dense_leaves, paper_map, llm,
                            config.min_leaf, config.max_leaf,
                            config.max_workers,
                            max_parallel_leaves=config.max_parallel_leaves,
                            thinking_llm=thinking_llm)


def _expand_loop(root: TaxoNode, paper_map: dict[str, PaperRecord],
                 llm: LLMClient, config: Config,
                 thinking_llm: Optional[LLMClient] = None) -> None:
    iteration = 0
    while True:
        iteration += 1
        dense_leaves = [
            leaf for leaf in root.all_leaves()
            if len(leaf.paper_ids) > config.max_leaf
            and leaf.level < config.max_depth
            and not leaf.expansion_resistant
        ]
        if not dense_leaves:
            log.info("Expansion converged after %d iterations", iteration - 1)
            break

        log.info("Expansion iter %d: %d leaves exceed max_leaf=%d (max=%d)",
                 iteration, len(dense_leaves), config.max_leaf,
                 max(len(l.paper_ids) for l in dense_leaves))

        expanded = _expand_leaves_parallel(dense_leaves, paper_map, llm,
                                           config.min_leaf, config.max_leaf,
                                           config.max_workers,
                                           max_parallel_leaves=config.max_parallel_leaves,
                                           thinking_llm=thinking_llm)

        if expanded == 0:
            log.info("No successful expansions in iter %d, stopping", iteration)
            break

        _save_checkpoint(root, config.output_dir / "checkpoint.json", stage="expansion")
        _log_tree_stats(root)

        if iteration >= 20:
            log.warning("Reached max iterations (20), stopping")
            break


def _expand_single_leaf(leaf: TaxoNode, paper_map: dict[str, PaperRecord],
                        llm: LLMClient,
                        min_leaf: int, max_leaf: int,
                        max_workers: int = 100,
                        thinking_llm: Optional[LLMClient] = None) -> bool:
    n_papers = len(leaf.paper_ids)
    log.info("Expanding '%s' (level=%d, %d papers)", leaf.label, leaf.level, n_papers)

    # Step 1: Pseudo-label (batch, uses main llm with disabled thinking)
    pseudo_labels = _pseudo_label_papers(leaf, paper_map, llm, max_workers=max_workers)
    if len(pseudo_labels) < n_papers * 0.5:
        log.warning("Pseudo-labeling covered only %d/%d, skipping", len(pseudo_labels), n_papers)
        return False

    # Step 2: Discover sub-topics (quality-critical, uses thinking_llm)
    target_subtopics = max(3, min(8, math.ceil(n_papers / max(max_leaf * 0.7, 10))))
    subtopics = _discover_subtopics(leaf, pseudo_labels, thinking_llm or llm,
                                    min_subtopics=3, max_subtopics=target_subtopics,
                                    target_min=min_leaf, target_max=max_leaf)
    if not subtopics or len(subtopics) < 2:
        log.warning("Subtopic discovery failed for '%s'", leaf.label)
        return False

    # Step 3: Assign ALL papers to discovered sub-topics (multi-label)
    assignments = _assign_to_subtopics(leaf, subtopics, pseudo_labels, paper_map, llm,
                                       max_workers=max_workers)

    # Step 4: Create children from assignments (multi-label: a paper can appear in multiple children)
    children: list[TaxoNode] = []
    for i, st in enumerate(subtopics):
        st_id = st.get("id", f"st_{i+1:02d}")
        child = TaxoNode(
            id=f"{leaf.id}_{i+1:02d}",
            label=st.get("label", f"Sub-topic {i+1}"),
            description=st.get("description", ""),
            level=leaf.level + 1,
        )
        child.paper_ids = [pid for pid, sids in assignments.items() if st_id in sids]
        children.append(child)

    # Handle unclaimed papers
    assigned_pids = set(assignments.keys())
    unclaimed = [pid for pid in leaf.paper_ids if pid not in assigned_pids]
    if unclaimed:
        if len(unclaimed) <= min_leaf // 2 and children:
            largest = max(children, key=lambda c: len(c.paper_ids))
            largest.paper_ids.extend(unclaimed)
        else:
            children.append(TaxoNode(
                id=f"{leaf.id}_other",
                label=f"{leaf.label} (Other)",
                description="Papers not clearly fitting specific sub-topics",
                level=leaf.level + 1,
                paper_ids=unclaimed,
            ))

    # Step 5: Merge small children (< min_leaf)
    children = _merge_small_children(children, min_leaf, max_leaf)

    if len(children) < 2:
        log.warning("After merge, only %d children for '%s'", len(children), leaf.label)
        return False

    leaf.children = children
    leaf.paper_ids = []
    log.info("Expanded '%s' -> %d sub-topics (sizes: %s)",
             leaf.label, len(children),
             ", ".join(str(len(c.paper_ids)) for c in children))
    return True


def _merge_small_children(children: list[TaxoNode], min_leaf: int,
                          max_leaf: int = 50) -> list[TaxoNode]:
    """Greedy merge: sort by size ascending, merge small nodes until each >= min_leaf."""
    children = [c for c in children if c.paper_ids]
    if len(children) <= 1:
        return children

    if all(len(c.paper_ids) >= min_leaf for c in children):
        return children

    children.sort(key=lambda c: len(c.paper_ids))

    groups: list[list[TaxoNode]] = []
    current_group: list[TaxoNode] = []
    current_size = 0

    for c in children:
        if current_size >= min_leaf and current_size + len(c.paper_ids) > max_leaf:
            groups.append(current_group)
            current_group = [c]
            current_size = len(c.paper_ids)
        else:
            current_group.append(c)
            current_size += len(c.paper_ids)

    if current_group:
        if current_size < min_leaf and groups:
            groups[-1].extend(current_group)
        else:
            groups.append(current_group)

    merged: list[TaxoNode] = []
    for group in groups:
        if len(group) == 1:
            merged.append(group[0])
        else:
            primary = max(group, key=lambda c: len(c.paper_ids))
            combined = TaxoNode(
                id=primary.id,
                label=primary.label,
                description=primary.description,
                level=primary.level,
            )
            for c in group:
                combined.paper_ids.extend(c.paper_ids)
            merged.append(combined)

    n_merged = len(children) - len(merged)
    if n_merged > 0:
        sizes_str = ", ".join(str(len(c.paper_ids)) for c in merged)
        log.info("Greedy merge: %d -> %d children (sizes: %s)", len(children), len(merged), sizes_str)

    return merged


# ---------------------------------------------------------------------------
# Pseudo-labeling
# ---------------------------------------------------------------------------

def _pseudo_label_papers(leaf: TaxoNode, paper_map: dict[str, PaperRecord],
                         llm: LLMClient,
                         max_workers: int = 100) -> dict[str, str]:
    papers_in_leaf = [paper_map[pid] for pid in leaf.paper_ids if pid in paper_map]
    if not papers_in_leaf:
        return {}

    batches = list(_chunked(papers_in_leaf, 30))
    all_labels: dict[str, str] = {}

    def _label_batch(batch: list[PaperRecord]) -> dict[str, str]:
        items = [_paper_to_prompt_dict(p) for p in batch]
        papers_json = json.dumps(items, ensure_ascii=False, indent=1)
        result = llm_call(
            llm,
            PSEUDO_LABEL_SYSTEM.format(
                parent_label=leaf.label,
                parent_description=leaf.description or leaf.label),
            PSEUDO_LABEL_USER.format(parent_label=leaf.label, papers_json=papers_json),
            thinking="disabled")
        labels = {}
        if isinstance(result, list):
            for r in result:
                if "paper_id" in r and "pseudo_label" in r:
                    labels[r["paper_id"]] = r["pseudo_label"]
        return labels

    with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as pool:
        futures = [pool.submit(_label_batch, b) for b in batches]
        for fut in as_completed(futures):
            try:
                all_labels.update(fut.result())
            except Exception as e:
                log.warning("Pseudo-label batch failed: %s", e)

    log.info("Pseudo-labeled %d/%d papers for '%s'",
             len(all_labels), len(papers_in_leaf), leaf.label)
    return all_labels


# ---------------------------------------------------------------------------
# Subtopic clustering
# ---------------------------------------------------------------------------

def _discover_subtopics(leaf: TaxoNode, pseudo_labels: dict[str, str],
                        llm: LLMClient,
                        min_subtopics: int = 3, max_subtopics: int = 8,
                        target_min: int = 10, target_max: int = 50) -> list[dict]:
    """Discover sub-topics from a SAMPLE of pseudo-labels."""
    rng = random.Random(hash(leaf.id))
    all_labels = list(pseudo_labels.values())
    sample_labels = rng.sample(all_labels, min(100, len(all_labels)))

    labels_text = "\n".join(f"- {label}" for label in sample_labels)

    result = llm_call(
        llm,
        SUBTOPIC_CLUSTER_SYSTEM.format(
            parent_label=leaf.label,
            min_subtopics=min_subtopics,
            max_subtopics=max_subtopics,
            target_min=target_min,
            target_max=target_max),
        SUBTOPIC_CLUSTER_USER.format(
            parent_label=leaf.label,
            n_papers=len(pseudo_labels),
            pseudo_labels_json=labels_text))

    subtopics = result if isinstance(result, list) else []
    for i, st in enumerate(subtopics):
        if "id" not in st:
            st["id"] = f"st_{i+1:02d}"
    log.info("Discovered %d sub-topics for '%s'", len(subtopics), leaf.label)
    return subtopics


def _assign_to_subtopics(leaf: TaxoNode, subtopics: list[dict],
                         pseudo_labels: dict[str, str],
                         paper_map: dict[str, PaperRecord],
                         llm: LLMClient,
                         max_workers: int = 100) -> dict[str, list[str]]:
    """Assign ALL papers to 1-3 discovered sub-topics (multi-label)."""
    subtopic_desc = json.dumps(
        [{"id": st["id"], "label": st.get("label", ""),
          "description": st.get("description", "")}
         for st in subtopics],
        ensure_ascii=False, indent=1)

    valid_ids = {st["id"] for st in subtopics}
    items = [{"paper_id": pid, "pseudo_label": pseudo_labels.get(pid, "")}
             for pid in leaf.paper_ids if pid in pseudo_labels]
    batches = list(_chunked(items, 50))
    assignments: dict[str, list[str]] = {}

    def _assign_batch(batch: list[dict]) -> list[dict]:
        batch_json = json.dumps(batch, ensure_ascii=False, indent=1)
        result = llm_call(
            llm,
            "Assign each paper to 1-3 most fitting sub-topics based on its pseudo-label. "
            "Most papers fit 1 sub-topic; assign to 2-3 only if genuinely spanning multiple areas. "
            'Return JSON array: [{"paper_id": "...", "subtopic_ids": ["..."]}]\n'
            "No extra text.",
            f"Sub-topics:\n{subtopic_desc}\n\nPapers ({len(batch)}):\n{batch_json}",
            thinking="disabled")
        return result if isinstance(result, list) else []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as pool:
        futs = [pool.submit(_assign_batch, b) for b in batches]
        for fut in as_completed(futs):
            try:
                for r in fut.result():
                    pid = r.get("paper_id", "")
                    sids = r.get("subtopic_ids", [])
                    if not sids:
                        sid = r.get("subtopic_id", "")
                        if sid:
                            sids = [sid]
                    valid_sids = [s for s in sids if s in valid_ids]
                    if pid and valid_sids:
                        assignments[pid] = valid_sids
            except Exception as e:
                log.warning("Subtopic assignment batch failed: %s", e)

    log.info("Assigned %d/%d papers to sub-topics for '%s'",
             len(assignments), len(items), leaf.label)
    return assignments


# ---------------------------------------------------------------------------
# Checkpoint (with stage tracking)
# ---------------------------------------------------------------------------

def _save_checkpoint(root: TaxoNode, path: Path, stage: str = "") -> None:
    data = root.to_full_dict()
    data["_pipeline_stage"] = stage
    _write_json(path, data)
    log.info("Checkpoint saved to %s (stage=%s)", path, stage or "none")


def _load_checkpoint(path: Path) -> tuple[Optional[TaxoNode], str]:
    """Load checkpoint, returning (tree, pipeline_stage)."""
    if not path.exists():
        return None, ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        stage = data.pop("_pipeline_stage", "")
        root = TaxoNode.from_full_dict(data, level=0)
        log.info("Loaded checkpoint from %s (stage=%s)", path, stage or "none")
        return root, stage
    except Exception as e:
        log.warning("Failed to load checkpoint: %s", e)
        return None, ""


# ---------------------------------------------------------------------------
# Stage 6: Final label generation
# ---------------------------------------------------------------------------

def _generate_final_labels(clusters: list[dict], paper_map: dict[str, PaperRecord],
                           llm: LLMClient, max_workers: int = 100) -> list[dict]:
    """Generate research_question + research_question_detail for each cluster."""

    def _label_one(cluster: dict) -> dict:
        papers = [paper_map[pid] for pid in cluster["paper_ids"] if pid in paper_map]
        items = [_paper_to_prompt_dict(p) for p in papers[:25]]
        pj = json.dumps(items, ensure_ascii=False, indent=1)
        result = llm_call(
            llm, FINAL_LABEL_SYSTEM,
            FINAL_LABEL_USER.format(
                cluster_label=cluster.get("label", ""),
                n_papers=len(papers),
                papers_json=pj))
        if isinstance(result, dict):
            cluster["research_question"] = result.get("research_question",
                                                       cluster.get("label", ""))
            cluster["research_question_detail"] = result.get("research_question_detail", "")
        else:
            cluster["research_question"] = cluster.get("label", "")
            cluster["research_question_detail"] = cluster.get("description", "")
        return cluster

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_label_one, c): c for c in clusters}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                log.warning("Final label failed: %s", e)
            done += 1
            if done % 100 == 0:
                log.info("  Stage 6: %d/%d clusters labeled", done, len(clusters))

    log.info("Stage 6 complete: %d clusters labeled", len(clusters))
    return clusters


# ---------------------------------------------------------------------------
# Stage 7: Coherence verification
# ---------------------------------------------------------------------------

def _verify_coherence(clusters: list[dict], paper_map: dict[str, PaperRecord],
                      llm: LLMClient, min_leaf: int = 10,
                      max_workers: int = 100,
                      batch_size: int = 15,
                      output_dir: Optional[Path] = None) -> tuple[list[dict], int]:
    """Verify each cluster's coherence per-paper in batches."""
    total_kicked = 0
    all_verification_records: list[dict] = []
    _lock = threading.Lock()

    def _verify_one(cluster: dict) -> dict:
        rq = cluster.get("research_question", cluster.get("label", ""))
        all_pids = list(cluster["paper_ids"])
        papers = [paper_map[pid] for pid in all_pids if pid in paper_map]
        if not papers:
            cluster["_valid"] = False
            cluster["_kicked"] = []
            cluster["_verification"] = []
            return cluster

        batches = list(_chunked(papers, batch_size))
        all_results: list[dict] = []

        for batch in batches:
            items = [_paper_to_prompt_dict(p) for p in batch]
            pj = json.dumps(items, ensure_ascii=False, indent=1)
            result = llm_call(
                llm,
                COHERENCE_CHECK_SYSTEM.format(research_question=rq),
                COHERENCE_CHECK_USER.format(n_papers=len(batch), papers_json=pj),
                thinking="disabled")
            if isinstance(result, list):
                all_results.extend(result)

        belongs = [r for r in all_results if r.get("belongs", True)]
        not_belongs = [r for r in all_results if not r.get("belongs", True)]
        n_responded = len(all_results)
        ratio = len(belongs) / max(n_responded, 1)

        kicked_ids = [r["paper_id"] for r in not_belongs if "paper_id" in r]
        remaining_ids = [pid for pid in all_pids if pid not in set(kicked_ids)]

        cluster["paper_ids"] = remaining_ids
        cluster["n_papers"] = len(remaining_ids)
        cluster["_valid"] = len(remaining_ids) >= min_leaf
        cluster["_kicked"] = kicked_ids
        cluster["coherence_before"] = ratio
        cluster["coherence_after"] = 1.0 if not kicked_ids else len(belongs) / max(len(all_pids), 1)

        verification = []
        for r in all_results:
            rec = {
                "cluster_id": cluster["cluster_id"],
                "research_question": rq,
                "paper_id": r.get("paper_id", ""),
                "belongs": r.get("belongs", True),
                "reason": r.get("reason", ""),
            }
            verification.append(rec)
        cluster["_verification"] = verification

        return cluster

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_verify_one, c): c for c in clusters}
        for fut in as_completed(futs):
            try:
                result = fut.result()
                with _lock:
                    all_verification_records.extend(result.get("_verification", []))
            except Exception as e:
                log.warning("Coherence check failed: %s", e)
            done += 1
            if done % 100 == 0:
                log.info("  Stage 7: %d/%d clusters verified", done, len(clusters))

    if output_dir:
        vpath = output_dir / "verification_results.jsonl"
        with open(vpath, "w", encoding="utf-8") as f:
            for rec in sorted(all_verification_records, key=lambda r: (r["cluster_id"], r["paper_id"])):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.info("Verification results saved to %s (%d records)", vpath, len(all_verification_records))

    valid = []
    for c in clusters:
        kicked = c.pop("_kicked", [])
        is_valid = c.pop("_valid", True)
        c.pop("_verification", None)
        total_kicked += len(kicked)
        if is_valid:
            valid.append(c)

    log.info("Stage 7 complete: %d/%d clusters valid, %d papers kicked",
             len(valid), len(clusters), total_kicked)
    return valid, total_kicked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_diverse(papers: list[PaperRecord], n: int, seed: int) -> list[PaperRecord]:
    rng = random.Random(seed)
    if len(papers) <= n:
        return papers
    return rng.sample(papers, n)


def _log_tree_stats(root: TaxoNode) -> None:
    leaves = root.all_leaves()
    sizes = [len(l.paper_ids) for l in leaves]
    assigned = sum(sizes)
    non_empty = sum(1 for s in sizes if s > 0)
    log.info("Tree: %d leaves (%d non-empty), %d papers, sizes: min=%d max=%d avg=%.1f, depth=%d",
             len(leaves), non_empty, assigned,
             min(sizes) if sizes else 0,
             max(sizes) if sizes else 0,
             sum(sizes) / len(sizes) if sizes else 0,
             root.depth())
