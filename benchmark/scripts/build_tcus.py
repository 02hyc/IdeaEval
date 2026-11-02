#!/usr/bin/env python3
"""Build Topic Comparison Units (TCUs) from the TaxoAdapt taxonomy tree.

Self-contained script — no imports from ideaeval_benchmark.
Walks the taxonomy tree with size-adaptive splitting and embedding-based
sibling grouping to produce balanced mid-level topic clusters.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TCUConfig:
    min_papers: int = 60
    max_papers: int = 220
    min_leaves: int = 2
    max_leaves: int = 9


@dataclass
class TreeNode:
    id: str
    label: str
    description: str
    paper_ids: list[str]
    level: int
    children: list[TreeNode] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def all_leaves(self) -> list[TreeNode]:
        if self.is_leaf:
            return [self]
        leaves: list[TreeNode] = []
        for c in self.children:
            leaves.extend(c.all_leaves())
        return leaves

    def count_papers(self) -> int:
        if self.is_leaf:
            return len(self.paper_ids)
        return sum(c.count_papers() for c in self.children)

    @staticmethod
    def from_dict(d: dict) -> TreeNode:
        node = TreeNode(
            id=d.get("id", ""),
            label=d.get("label", ""),
            description=d.get("description", ""),
            paper_ids=d.get("paper_ids", []),
            level=d.get("level", 0),
        )
        for cd in d.get("children", []):
            node.children.append(TreeNode.from_dict(cd))
        return node


@dataclass
class TCUPiece:
    node_id: str | None
    label: str
    description: str
    leaves: list[TreeNode]
    paper_ids: list[str]
    ancestor_path: list[str]

    @property
    def n_papers(self) -> int:
        return len(self.paper_ids)

    @property
    def n_leaves(self) -> int:
        return len(self.leaves)


# ---------------------------------------------------------------------------
# Tree loading
# ---------------------------------------------------------------------------

def load_tree(path: Path) -> TreeNode:
    with open(path, encoding="utf-8") as f:
        return TreeNode.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def precompute_embeddings(root: TreeNode, model) -> dict[str, np.ndarray]:
    nodes: list[TreeNode] = []

    def _collect(node: TreeNode):
        nodes.append(node)
        for c in node.children:
            _collect(c)

    for c in root.children:
        _collect(c)

    texts = [f"{n.label}. {n.description}" for n in nodes]
    log.info("Encoding %d node descriptions...", len(texts))
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=True, batch_size=64)
    vecs = np.array(vecs)

    cache: dict[str, np.ndarray] = {}
    for node, vec in zip(nodes, vecs):
        cache[node.id] = vec
    return cache


# ---------------------------------------------------------------------------
# Piece construction
# ---------------------------------------------------------------------------

def make_piece(node: TreeNode, ancestor_path: list[str]) -> TCUPiece:
    leaves = node.all_leaves()
    paper_ids: list[str] = []
    for lf in leaves:
        paper_ids.extend(lf.paper_ids)
    return TCUPiece(
        node_id=node.id,
        label=node.label,
        description=node.description,
        leaves=leaves,
        paper_ids=paper_ids,
        ancestor_path=ancestor_path,
    )


# ---------------------------------------------------------------------------
# Embedding-based sibling grouping
# ---------------------------------------------------------------------------

def _piece_embedding(piece: TCUPiece, emb_cache: dict[str, np.ndarray]) -> np.ndarray:
    if piece.node_id and piece.node_id in emb_cache:
        return emb_cache[piece.node_id]
    vecs = []
    weights = []
    for lf in piece.leaves:
        if lf.id in emb_cache:
            vecs.append(emb_cache[lf.id])
            weights.append(len(lf.paper_ids))
    if not vecs:
        return np.zeros(list(emb_cache.values())[0].shape)
    arr = np.array(vecs)
    w = np.array(weights, dtype=float)
    avg = (arr * w[:, None]).sum(axis=0) / w.sum()
    norm = np.linalg.norm(avg)
    return avg / norm if norm > 0 else avg


def group_pieces(
    pieces: list[TCUPiece],
    config: TCUConfig,
    emb_cache: dict[str, np.ndarray],
    l1_label: str,
) -> tuple[list[dict], list[TCUPiece]]:
    if not pieces:
        return [], []

    if len(pieces) == 1:
        p = pieces[0]
        if p.n_papers >= config.min_papers and p.n_leaves >= config.min_leaves:
            return [_piece_to_tcu(p, "", l1_label)], []
        return [], pieces

    active = list(pieces)
    active_embs = np.array([_piece_embedding(p, emb_cache) for p in active])

    merged_any = True
    while merged_any:
        merged_any = False
        n = len(active)
        if n <= 1:
            break

        sim = active_embs @ active_embs.T
        np.fill_diagonal(sim, -np.inf)

        flat_order = np.argsort(sim.ravel())[::-1]
        for flat_idx in flat_order:
            i, j = divmod(int(flat_idx), n)
            if i >= j:
                continue
            if sim[i, j] <= -np.inf:
                break

            combined_papers = active[i].n_papers + active[j].n_papers
            combined_leaves = active[i].n_leaves + active[j].n_leaves
            if combined_papers > config.max_papers or combined_leaves > config.max_leaves:
                continue

            new_piece = TCUPiece(
                node_id=None,
                label=active[i].label if active[i].n_papers >= active[j].n_papers else active[j].label,
                description=active[i].description if active[i].n_papers >= active[j].n_papers else active[j].description,
                leaves=active[i].leaves + active[j].leaves,
                paper_ids=active[i].paper_ids + active[j].paper_ids,
                ancestor_path=active[i].ancestor_path,
            )

            w_i = active[i].n_papers
            w_j = active[j].n_papers
            new_emb = (active_embs[i] * w_i + active_embs[j] * w_j) / (w_i + w_j)
            norm = np.linalg.norm(new_emb)
            if norm > 0:
                new_emb = new_emb / norm

            keep = [k for k in range(n) if k != i and k != j]
            active = [active[k] for k in keep] + [new_piece]
            active_embs = np.vstack([active_embs[keep], new_emb.reshape(1, -1)])

            log.debug("  Merged '%s' + '%s' -> %d papers, %d leaves (sim=%.3f)",
                       active[-1].leaves[0].label[:30] if active[-1].leaves else "?",
                       "", new_piece.n_papers, new_piece.n_leaves, float(sim[i, j]))
            merged_any = True
            break

    tcus: list[dict] = []
    residuals: list[TCUPiece] = []
    for p in active:
        if p.n_papers >= config.min_papers and p.n_leaves >= config.min_leaves:
            tcus.append(_piece_to_tcu(p, "", l1_label))
        else:
            residuals.append(p)

    return tcus, residuals


# ---------------------------------------------------------------------------
# Core recursive walk
# ---------------------------------------------------------------------------

def walk(
    node: TreeNode,
    ancestor_path: list[str],
    config: TCUConfig,
    emb_cache: dict[str, np.ndarray],
    l1_label: str,
) -> tuple[list[dict], list[TCUPiece]]:
    n_papers = node.count_papers()
    n_leaves = len(node.all_leaves())

    if node.is_leaf:
        return [], [make_piece(node, ancestor_path)]

    if config.min_papers <= n_papers <= config.max_papers and config.min_leaves <= n_leaves <= config.max_leaves:
        tcu = _piece_to_tcu(make_piece(node, ancestor_path), node.id, l1_label)
        return [tcu], []

    if n_papers < config.min_papers:
        return [], [make_piece(node, ancestor_path)]

    child_path = ancestor_path + [node.label]
    all_tcus: list[dict] = []
    all_pieces: list[TCUPiece] = []

    for child in node.children:
        child_tcus, child_pieces = walk(child, child_path, config, emb_cache, l1_label)
        all_tcus.extend(child_tcus)
        all_pieces.extend(child_pieces)

    if all_pieces:
        grouped_tcus, still_small = group_pieces(all_pieces, config, emb_cache, l1_label)
        all_tcus.extend(grouped_tcus)
        all_pieces = still_small

    return all_tcus, all_pieces


# ---------------------------------------------------------------------------
# Force-attach residuals
# ---------------------------------------------------------------------------

def force_attach_residuals(
    residuals: list[TCUPiece],
    tcus: list[dict],
    emb_cache: dict[str, np.ndarray],
    l1_label: str,
):
    if not residuals or not tcus:
        return

    same_domain_tcus = [t for t in tcus if t["l1_domain"] == l1_label]
    if not same_domain_tcus:
        same_domain_tcus = tcus

    tcu_embs = []
    for t in same_domain_tcus:
        leaf_vecs = []
        leaf_weights = []
        for lf in t["rq_leaves"]:
            lid = lf["leaf_id"]
            if lid in emb_cache:
                leaf_vecs.append(emb_cache[lid])
                leaf_weights.append(lf["n_papers"])
        if leaf_vecs:
            arr = np.array(leaf_vecs)
            w = np.array(leaf_weights, dtype=float)
            avg = (arr * w[:, None]).sum(axis=0) / w.sum()
            norm = np.linalg.norm(avg)
            tcu_embs.append(avg / norm if norm > 0 else avg)
        else:
            tcu_embs.append(np.zeros_like(list(emb_cache.values())[0]))
    tcu_emb_arr = np.array(tcu_embs)

    for piece in residuals:
        p_emb = _piece_embedding(piece, emb_cache).reshape(1, -1)
        sims = (p_emb @ tcu_emb_arr.T).ravel()
        best_idx = int(np.argmax(sims))
        target = same_domain_tcus[best_idx]

        log.warning("Force-attaching '%s' (%d papers, %d leaves) to TCU '%s' (sim=%.3f)",
                     piece.label, piece.n_papers, piece.n_leaves,
                     target["label"][:50], sims[best_idx])

        for lf in piece.leaves:
            target["rq_leaves"].append({
                "leaf_id": lf.id,
                "label": lf.label,
                "n_papers": len(lf.paper_ids),
            })
        target["paper_ids"].extend(piece.paper_ids)
        target["n_papers"] = len(target["paper_ids"])
        target["n_leaves"] = len(target["rq_leaves"])


# ---------------------------------------------------------------------------
# TCU record construction
# ---------------------------------------------------------------------------

def _piece_to_tcu(piece: TCUPiece, tree_source_node: str, l1_label: str) -> dict:
    return {
        "tcu_id": "",
        "label": piece.label,
        "description": piece.description,
        "rq_leaves": [
            {"leaf_id": lf.id, "label": lf.label, "n_papers": len(lf.paper_ids)}
            for lf in piece.leaves
        ],
        "paper_ids": list(piece.paper_ids),
        "n_papers": piece.n_papers,
        "n_leaves": piece.n_leaves,
        "l1_domain": l1_label,
        "tree_source_node": tree_source_node or None,
        "ancestor_path": list(piece.ancestor_path),
    }


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_stats(tcus: list[dict], n_total_leaves: int, n_total_papers: int) -> dict:
    papers = [t["n_papers"] for t in tcus]
    leaves = [t["n_leaves"] for t in tcus]

    from collections import Counter
    l1_counts: dict[str, dict] = {}
    for t in tcus:
        d = t["l1_domain"]
        if d not in l1_counts:
            l1_counts[d] = {"n_tcus": 0, "n_papers": 0, "n_leaves": 0}
        l1_counts[d]["n_tcus"] += 1
        l1_counts[d]["n_papers"] += t["n_papers"]
        l1_counts[d]["n_leaves"] += t["n_leaves"]

    n_direct = sum(1 for t in tcus if t["tree_source_node"] is not None)
    n_merged = sum(1 for t in tcus if t["tree_source_node"] is None)

    return {
        "n_tcus": len(tcus),
        "n_papers_covered": sum(papers),
        "n_papers_total": n_total_papers,
        "n_leaves_covered": sum(leaves),
        "n_leaves_total": n_total_leaves,
        "papers_per_tcu": {
            "min": min(papers) if papers else 0,
            "max": max(papers) if papers else 0,
            "avg": round(sum(papers) / len(papers), 1) if papers else 0,
            "median": sorted(papers)[len(papers) // 2] if papers else 0,
        },
        "leaves_per_tcu": {
            "min": min(leaves) if leaves else 0,
            "max": max(leaves) if leaves else 0,
            "avg": round(sum(leaves) / len(leaves), 1) if leaves else 0,
            "median": sorted(leaves)[len(leaves) // 2] if leaves else 0,
        },
        "n_direct_tree_nodes": n_direct,
        "n_merged_groups": n_merged,
        "per_l1_domain": l1_counts,
    }


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("Wrote %s", path)


# ---------------------------------------------------------------------------
# HTML visualization
# ---------------------------------------------------------------------------

def generate_html(tcus: list[dict]) -> str:
    from collections import defaultdict
    by_l1: dict[str, list[dict]] = defaultdict(list)
    for t in tcus:
        by_l1[t["l1_domain"]].append(t)

    tcus_json = json.dumps(by_l1, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TCU Tree Visualization</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, monospace; font-size: 14px; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #e94560; margin-bottom: 10px; }}
.stats {{ color: #888; margin-bottom: 20px; font-size: 13px; }}
.l1 {{ margin-bottom: 8px; }}
.l1-header {{ padding: 6px 10px; background: #16213e; border-radius: 4px; cursor: pointer; font-weight: 600; color: #4ea8de; font-size: 16px; display: flex; gap: 8px; align-items: center; }}
.l1-header:hover {{ background: #1a3050; }}
.l1-body {{ display: none; margin-left: 20px; }}
.l1-body.open {{ display: block; }}
.tcu {{ margin: 4px 0; }}
.tcu-header {{ padding: 4px 10px; cursor: pointer; display: flex; gap: 8px; align-items: center; border-radius: 4px; }}
.tcu-header:hover {{ background: #16213e; }}
.tcu-label {{ color: #48bfe3; font-weight: 600; }}
.tcu-body {{ display: none; margin-left: 24px; }}
.tcu-body.open {{ display: block; }}
.leaf {{ padding: 2px 10px; color: #adb5bd; }}
.badge {{ padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
.badge-papers {{ background: #1a3a5c; color: #4ea8de; }}
.badge-leaves {{ background: #1a3a3a; color: #48bfe3; }}
.toggle {{ width: 16px; color: #e94560; font-weight: bold; }}
</style>
</head>
<body>
<h1>Topic Comparison Units (TCUs)</h1>
<div class="stats" id="stats"></div>
<div id="tree"></div>
<script>
const data = {tcus_json};
const treeEl = document.getElementById('tree');
let totalTCUs = 0, totalPapers = 0;

for (const [l1, tcus] of Object.entries(data).sort((a,b) => b[1].length - a[1].length)) {{
  const l1Div = document.createElement('div');
  l1Div.className = 'l1';
  const l1Papers = tcus.reduce((s,t) => s + t.n_papers, 0);
  totalTCUs += tcus.length;
  totalPapers += l1Papers;

  const header = document.createElement('div');
  header.className = 'l1-header';
  header.innerHTML = '<span class="toggle">+</span>' + l1 +
    ' <span class="badge badge-leaves">' + tcus.length + ' TCUs</span>' +
    ' <span class="badge badge-papers">' + l1Papers + ' papers</span>';
  l1Div.appendChild(header);

  const body = document.createElement('div');
  body.className = 'l1-body';

  for (const tcu of tcus.sort((a,b) => b.n_papers - a.n_papers)) {{
    const tcuDiv = document.createElement('div');
    tcuDiv.className = 'tcu';
    const th = document.createElement('div');
    th.className = 'tcu-header';
    th.innerHTML = '<span class="toggle">+</span><span class="tcu-label">' + tcu.label + '</span>' +
      ' <span class="badge badge-leaves">' + tcu.n_leaves + ' leaves</span>' +
      ' <span class="badge badge-papers">' + tcu.n_papers + ' papers</span>';
    tcuDiv.appendChild(th);

    const tb = document.createElement('div');
    tb.className = 'tcu-body';
    for (const lf of tcu.rq_leaves) {{
      const ld = document.createElement('div');
      ld.className = 'leaf';
      ld.textContent = '\\u00b7 ' + lf.label + ' (' + lf.n_papers + ' papers)';
      tb.appendChild(ld);
    }}
    tcuDiv.appendChild(tb);

    th.addEventListener('click', () => {{
      tb.classList.toggle('open');
      th.querySelector('.toggle').textContent = tb.classList.contains('open') ? '-' : '+';
    }});
    body.appendChild(tcuDiv);
  }}
  l1Div.appendChild(body);

  header.addEventListener('click', () => {{
    body.classList.toggle('open');
    header.querySelector('.toggle').textContent = body.classList.contains('open') ? '-' : '+';
  }});
  treeEl.appendChild(l1Div);
}}

document.getElementById('stats').textContent =
  'Total: ' + totalTCUs + ' TCUs, ' + totalPapers + ' papers across ' + Object.keys(data).length + ' L1 domains';
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build Topic Comparison Units from taxonomy tree")
    parser.add_argument("--tree", type=Path, required=True, help="Path to taxonomy_tree.json")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--min-papers", type=int, default=60)
    parser.add_argument("--max-papers", type=int, default=220)
    parser.add_argument("--min-leaves", type=int, default=2)
    parser.add_argument("--max-leaves", type=int, default=9)
    parser.add_argument("--model", type=str, default="BAAI/bge-large-en-v1.5")
    parser.add_argument("--html", action="store_true", help="Generate tcu_tree.html visualization")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s", stream=sys.stderr)

    config = TCUConfig(
        min_papers=args.min_papers,
        max_papers=args.max_papers,
        min_leaves=args.min_leaves,
        max_leaves=args.max_leaves,
    )
    log.info("Config: %s", config)

    log.info("Loading tree from %s", args.tree)
    root = load_tree(args.tree)
    total_leaves = len(root.all_leaves())
    total_papers = root.count_papers()
    log.info("Tree: %d L1 domains, %d leaves, %d papers", len(root.children), total_leaves, total_papers)

    from sentence_transformers import SentenceTransformer
    log.info("Loading embedding model: %s", args.model)
    model = SentenceTransformer(args.model)
    emb_cache = precompute_embeddings(root, model)
    log.info("Cached embeddings for %d nodes", len(emb_cache))

    all_tcus: list[dict] = []
    all_residuals: list[tuple[str, TCUPiece]] = []

    for l1_child in root.children:
        l1_label = l1_child.label
        l1_papers = l1_child.count_papers()
        l1_leaves = len(l1_child.all_leaves())
        log.info("=== L1: %s (%d papers, %d leaves) ===", l1_label, l1_papers, l1_leaves)

        tcus, residuals = walk(l1_child, [root.label], config, emb_cache, l1_label)
        log.info("  -> %d TCUs, %d residual pieces", len(tcus), len(residuals))

        if residuals:
            grouped, still_small = group_pieces(residuals, config, emb_cache, l1_label)
            tcus.extend(grouped)
            log.info("  -> L1-level grouping: %d more TCUs, %d still residual", len(grouped), len(still_small))
            for p in still_small:
                all_residuals.append((l1_label, p))

        all_tcus.extend(tcus)

    if all_residuals:
        log.warning("Force-attaching %d residual pieces", len(all_residuals))
        for l1_label, piece in all_residuals:
            force_attach_residuals([piece], all_tcus, emb_cache, l1_label)

    for i, t in enumerate(sorted(all_tcus, key=lambda x: (-len(x["paper_ids"])))):
        t["tcu_id"] = f"tcu_{i+1:04d}"

    # Validation
    leaf_ids_in_tcus = set()
    paper_ids_in_tcus = set()
    for t in all_tcus:
        for lf in t["rq_leaves"]:
            assert lf["leaf_id"] not in leaf_ids_in_tcus, f"Duplicate leaf {lf['leaf_id']}"
            leaf_ids_in_tcus.add(lf["leaf_id"])
        for pid in t["paper_ids"]:
            paper_ids_in_tcus.add(pid)

    all_tree_leaf_ids = {lf.id for lf in root.all_leaves()}
    missing = all_tree_leaf_ids - leaf_ids_in_tcus
    if missing:
        log.error("MISSING %d leaves not in any TCU: %s", len(missing), list(missing)[:5])
    else:
        log.info("Partition check PASSED: all %d leaves covered", len(leaf_ids_in_tcus))

    log.info("Paper conservation: %d in TCUs vs %d in tree", len(paper_ids_in_tcus), total_papers)

    n_violations = 0
    for t in all_tcus:
        if t["n_papers"] < config.min_papers or t["n_papers"] > config.max_papers:
            n_violations += 1
        if t["n_leaves"] < config.min_leaves or t["n_leaves"] > config.max_leaves:
            n_violations += 1
    if n_violations:
        log.warning("Constraint violations: %d TCUs outside target range", n_violations)
    else:
        log.info("Constraint check PASSED: all TCUs within [%d,%d] papers, [%d,%d] leaves",
                 config.min_papers, config.max_papers, config.min_leaves, config.max_leaves)

    # Output
    args.output_dir.mkdir(parents=True, exist_ok=True)

    write_json(args.output_dir / "tcus.json", all_tcus)

    stats = compute_stats(all_tcus, total_leaves, total_papers)
    write_json(args.output_dir / "tcu_stats.json", stats)
    log.info("Stats: %s", json.dumps({k: v for k, v in stats.items() if k != "per_l1_domain"}, indent=2))

    if args.html:
        html = generate_html(all_tcus)
        html_path = args.output_dir / "tcu_tree.html"
        html_path.write_text(html, encoding="utf-8")
        log.info("Wrote %s", html_path)

    log.info("Done: %d TCUs covering %d leaves and %d papers",
             len(all_tcus), len(leaf_ids_in_tcus), len(paper_ids_in_tcus))


if __name__ == "__main__":
    main()
