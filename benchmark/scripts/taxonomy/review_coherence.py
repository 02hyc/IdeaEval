#!/usr/bin/env python3
"""Re-run coherence verification with a relaxed prompt.

Only kicks papers that are clearly unrelated to the cluster topic.
Does NOT overwrite existing files — outputs to new files.

Usage:
    uv run python scripts/taxonomy/review_coherence.py \\
        --checkpoint experiments/clustering_v3/checkpoint_normalized.json \\
        --output-dir experiments/clustering_v3 \\
        --input      $SHARED_INPUTS_DIR/arxiv_metadata.jsonl \\
        --fields     $SHARED_INPUTS_DIR/fields_extracted.jsonl \\
        --benchmarks $SHARED_INPUTS_DIR/benchmarks_extracted.jsonl \\
        --max-workers 100
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

COHERENCE_SYSTEM = """\
You are a research paper reviewer. Given a cluster's research topic and a list of \
papers, determine for EACH paper whether it is RELEVANT to this topic.

Topic: "{topic}"

A paper BELONGS if it is related to the topic in any reasonable way — it could be a \
method paper, a dataset paper, a benchmark paper, a survey, or an application paper \
within this area. Only mark a paper as NOT belonging if it is clearly about a \
completely different research problem with no meaningful connection to the topic.

Be LENIENT — when in doubt, keep the paper. Only kick obvious mismatches.

Return JSON array: [{{"paper_id": "...", "belongs": true/false, "reason": "brief reason if false"}}]
No extra text outside the JSON."""

COHERENCE_USER = """\
Papers to verify ({n_papers}):
{papers_json}"""


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Re-run coherence with relaxed criteria")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True,
                        help="Papers JSONL (same input that was passed to taxoadapt)")
    parser.add_argument("--fields", type=Path, required=True,
                        help="fields_extracted.jsonl path")
    parser.add_argument("--benchmarks", type=Path, required=True,
                        help="benchmarks_extracted.jsonl path")
    parser.add_argument("--max-workers", type=int, default=100)
    parser.add_argument("--min-leaf", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=15)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from ideaeval_benchmark.taxoadapt.llm import LLMClient, RateLimiter, RPM_DEFAULT, llm_call
    from ideaeval_benchmark.taxoadapt.models import load_papers, load_fields, load_benchmarks

    # Load tree
    with open(args.checkpoint, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_pipeline_stage", "")

    # Load papers
    papers = load_papers(args.input)
    fields_map = load_fields(args.fields)
    bm_map = load_benchmarks(args.benchmarks)
    for p in papers:
        if p.paper_id in fields_map:
            p.research_problem = fields_map[p.paper_id].get("research_problem", "")
            p.task_formulation = fields_map[p.paper_id].get("task_formulation", "")
        if p.paper_id in bm_map:
            p.benchmarks = bm_map[p.paper_id]
    paper_map = {p.paper_id: p for p in papers}

    # Build clusters from tree leaves
    def get_leaves(node):
        if not node.get("children"):
            return [node]
        results = []
        for c in node["children"]:
            results.extend(get_leaves(c))
        return results

    leaves = get_leaves(data)
    clusters = []
    for leaf in leaves:
        if len(leaf.get("paper_ids", [])) >= args.min_leaf:
            clusters.append({
                "cluster_id": leaf["id"],
                "label": leaf.get("label", ""),
                "description": leaf.get("description", ""),
                "paper_ids": list(leaf.get("paper_ids", [])),
                "n_papers": len(leaf.get("paper_ids", [])),
                "depth": leaf.get("level", 0),
            })

    log.info("Loaded %d clusters from tree", len(clusters))

    # Generate research questions for clusters that don't have one
    # Use the label as the topic
    llm = LLMClient(rate_limiter=RateLimiter(rpm=RPM_DEFAULT))

    def paper_to_dict(p):
        d = {"paper_id": p.paper_id, "title": p.title, "abstract": p.abstract}
        if p.research_problem:
            d["research_problem"] = p.research_problem
        if p.task_formulation:
            d["task_formulation"] = p.task_formulation
        if p.benchmarks:
            d["benchmarks"] = p.benchmarks[:10]
        return d

    # Coherence verification
    total_kicked = 0
    all_records = []
    lock = threading.Lock()

    def chunked(items, size):
        for i in range(0, len(items), size):
            yield items[i:i + size]

    def verify_one(cluster):
        topic = cluster.get("label", "")
        all_pids = list(cluster["paper_ids"])
        cluster_papers = [paper_map[pid] for pid in all_pids if pid in paper_map]
        if not cluster_papers:
            cluster["_valid"] = False
            cluster["_kicked"] = []
            return cluster

        batches = list(chunked(cluster_papers, args.batch_size))
        all_results = []

        for batch in batches:
            items = [paper_to_dict(p) for p in batch]
            pj = json.dumps(items, ensure_ascii=False, indent=1)
            result = llm_call(
                llm,
                COHERENCE_SYSTEM.format(topic=topic),
                COHERENCE_USER.format(n_papers=len(batch), papers_json=pj),
                thinking="disabled")
            if isinstance(result, list):
                all_results.extend(result)

        kicked_ids = [r["paper_id"] for r in all_results
                      if not r.get("belongs", True) and "paper_id" in r]
        remaining = [pid for pid in all_pids if pid not in set(kicked_ids)]

        cluster["paper_ids"] = remaining
        cluster["n_papers"] = len(remaining)
        cluster["_valid"] = len(remaining) >= args.min_leaf
        cluster["_kicked"] = kicked_ids

        records = []
        for r in all_results:
            records.append({
                "cluster_id": cluster["cluster_id"],
                "topic": topic,
                "paper_id": r.get("paper_id", ""),
                "belongs": r.get("belongs", True),
                "reason": r.get("reason", ""),
            })
        cluster["_records"] = records
        return cluster

    done = 0
    log.info("Verifying %d clusters (workers=%d, batch_size=%d)",
             len(clusters), args.max_workers, args.batch_size)

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futs = {pool.submit(verify_one, c): c for c in clusters}
        for fut in as_completed(futs):
            try:
                result = fut.result()
                with lock:
                    all_records.extend(result.pop("_records", []))
            except Exception as e:
                log.warning("Verification failed: %s", e)
            done += 1
            if done % 100 == 0:
                log.info("  %d/%d clusters verified", done, len(clusters))

    # Collect results
    valid_clusters = []
    for c in clusters:
        kicked = c.pop("_kicked", [])
        is_valid = c.pop("_valid", True)
        total_kicked += len(kicked)
        if is_valid:
            valid_clusters.append(c)

    # Save
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_clusters = args.output_dir / "clusters_reviewed.json"
    with open(out_clusters, "w", encoding="utf-8") as f:
        json.dump(valid_clusters, f, ensure_ascii=False, indent=2)

    out_records = args.output_dir / "review_verification_results.jsonl"
    with open(out_records, "w", encoding="utf-8") as f:
        for rec in sorted(all_records, key=lambda r: (r["cluster_id"], r["paper_id"])):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Stats
    all_pids = set()
    total_pairs = 0
    for c in valid_clusters:
        all_pids.update(c["paper_ids"])
        total_pairs += c["n_papers"]

    sizes = [c["n_papers"] for c in valid_clusters]

    log.info("=== Done ===")
    log.info("Clusters: %d -> %d valid", len(clusters), len(valid_clusters))
    log.info("Paper-cluster pairs kicked: %d", total_kicked)
    log.info("Valid clusters: %d", len(valid_clusters))
    log.info("Total paper-cluster pairs: %d", total_pairs)
    log.info("Unique papers: %d", len(all_pids))
    log.info("Papers not in any cluster: %d", len(papers) - len(all_pids))
    log.info("Sizes: min=%d max=%d avg=%.1f", min(sizes), max(sizes), sum(sizes) / len(sizes))
    log.info("In [10,50]: %d/%d", sum(1 for s in sizes if 10 <= s <= 50), len(valid_clusters))

    print(f"\nResults saved to:")
    print(f"  {out_clusters}")
    print(f"  {out_records}")


if __name__ == "__main__":
    main()
