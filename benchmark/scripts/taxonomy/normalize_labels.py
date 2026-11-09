#!/usr/bin/env python3
"""Normalize taxonomy tree labels: short tags for internal nodes, questions for leaves.

Reads a checkpoint tree, sends internal question-form labels to LLM for conversion
to concise noun-phrase labels, writes normalized checkpoint + HTML visualization.

Does NOT overwrite original files.

Usage:
    uv run python scripts/taxonomy/normalize_labels.py \
        --checkpoint experiments/clustering_v3/checkpoint.json \
        --output-dir experiments/clustering_v3 \
        --max-workers 50
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a research taxonomy editor. Convert each question-form node label into a \
concise noun-phrase topic label (3-10 words) suitable for a taxonomy tree.

Rules:
- Use noun phrases, not questions. E.g. "How can we detect..." → "Hallucination Detection Methods"
- The label should accurately reflect the node's scope based on its description and children.
- Preserve specificity — don't make it too generic.
- If the label is already a good short tag (not a question), return it unchanged.

For each node you will see: id, current_label, description, parent_label, \
children_labels, n_papers.

Return JSON array: [{"id": "...", "new_label": "..."}]
No extra text outside the JSON."""


def is_question_label(label: str) -> bool:
    """Check if a label is in question form."""
    lower = label.lower().strip()
    return any(lower.startswith(q) for q in [
        "how ", "what ", "can ", "why ", "when ", "where ",
        "to what extent", "in what ways",
    ])


def collect_internal_nodes(node: dict, parent_label: str = "") -> list[dict]:
    """Collect all internal (non-leaf) nodes with their context."""
    results = []
    children = node.get("children", [])
    if not children:
        return results

    results.append({
        "id": node["id"],
        "current_label": node.get("label", ""),
        "description": node.get("description", ""),
        "parent_label": parent_label,
        "children_labels": [c.get("label", "") for c in children],
        "n_papers": count_papers(node),
    })
    for child in children:
        results.extend(collect_internal_nodes(child, node.get("label", "")))
    return results


def count_papers(node: dict) -> int:
    if not node.get("children"):
        return len(node.get("paper_ids", []))
    return sum(count_papers(c) for c in node["children"])


def count_leaves(node: dict) -> int:
    if not node.get("children"):
        return 1
    return sum(count_leaves(c) for c in node["children"])


def normalize_labels(nodes_to_fix: list[dict], llm, max_workers: int = 50) -> dict[str, str]:
    """Send batches to LLM, return {id: new_label} mapping."""
    from ideaeval_benchmark.taxoadapt.llm import llm_call

    batches = []
    batch_size = 20
    for i in range(0, len(nodes_to_fix), batch_size):
        batches.append(nodes_to_fix[i:i + batch_size])

    log.info("Normalizing %d labels in %d batches (workers=%d)",
             len(nodes_to_fix), len(batches), max_workers)

    id_to_label: dict[str, str] = {}

    def process_batch(batch: list[dict]) -> list[dict]:
        items = []
        for n in batch:
            items.append({
                "id": n["id"],
                "current_label": n["current_label"],
                "description": n["description"],
                "parent_label": n["parent_label"],
                "children_labels": n["children_labels"],
                "n_papers": n["n_papers"],
            })
        user_msg = json.dumps(items, ensure_ascii=False, indent=1)
        result = llm_call(llm, SYSTEM_PROMPT, user_msg, thinking="disabled")
        return result if isinstance(result, list) else []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(process_batch, b): i for i, b in enumerate(batches)}
        done = 0
        for fut in as_completed(futs):
            try:
                results = fut.result()
                for r in results:
                    nid = r.get("id", "")
                    new_label = r.get("new_label", "")
                    if nid and new_label:
                        id_to_label[nid] = new_label
            except Exception as e:
                log.warning("Batch failed: %s", e)
            done += 1
            if done % 10 == 0:
                log.info("  %d/%d batches done", done, len(batches))

    log.info("Got %d/%d label mappings", len(id_to_label), len(nodes_to_fix))
    return id_to_label


def apply_labels(node: dict, label_map: dict[str, str]) -> int:
    """Apply new labels to internal nodes. Returns count of changes."""
    changed = 0
    if node["id"] in label_map and node.get("children"):
        old = node["label"]
        node["label"] = label_map[node["id"]]
        if old != node["label"]:
            changed += 1
    for child in node.get("children", []):
        changed += apply_labels(child, label_map)
    return changed


def generate_html(tree: dict, output_path: Path) -> None:
    """Generate interactive HTML visualization."""
    n_leaves = count_leaves(tree)
    n_papers = count_papers(tree)

    def max_depth(n):
        if not n.get("children"):
            return 0
        return 1 + max(max_depth(c) for c in n["children"])
    depth = max_depth(tree)

    def convert_for_html(node):
        result = {
            "id": node.get("id", ""),
            "label": node.get("label", ""),
            "description": node.get("description", ""),
            "level": node.get("level", 0),
            "children": [],
        }
        if node.get("children"):
            for c in node["children"]:
                result["children"].append(convert_for_html(c))
        else:
            result["n_papers"] = len(node.get("paper_ids", []))
        return result

    tree_json = json.dumps(convert_for_html(tree), ensure_ascii=False)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TaxoAdapt V2 Taxonomy Tree (Normalized)</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace; font-size: 14px; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #e94560; margin-bottom: 10px; }}
.stats {{ color: #888; margin-bottom: 20px; font-size: 13px; }}
.search-box {{ margin-bottom: 20px; }}
.search-box input {{ width: 400px; padding: 8px 12px; background: #16213e; border: 1px solid #444; color: #e0e0e0; border-radius: 4px; font-size: 14px; }}
.search-box input::placeholder {{ color: #666; }}
.search-info {{ color: #888; font-size: 12px; margin-top: 4px; }}
.tree-node {{ margin-left: 20px; }}
.tree-node.root {{ margin-left: 0; }}
.node-header {{
  display: flex; align-items: flex-start; gap: 6px;
  padding: 4px 8px; border-radius: 4px; cursor: pointer;
  transition: background 0.15s;
}}
.node-header:hover {{ background: #16213e; }}
.node-header.highlight {{ background: #2a1a3e; }}
.toggle {{ width: 18px; height: 18px; flex-shrink: 0; text-align: center; color: #e94560; font-weight: bold; user-select: none; margin-top: 2px; }}
.leaf-dot {{ width: 18px; height: 18px; flex-shrink: 0; text-align: center; color: #555; margin-top: 2px; }}
.node-label {{ font-weight: 600; }}
.node-label.l0 {{ color: #e94560; font-size: 18px; }}
.node-label.l1 {{ color: #4ea8de; font-size: 16px; }}
.node-label.l2 {{ color: #48bfe3; }}
.node-label.l3 {{ color: #72efdd; }}
.node-label.leaf {{ color: #adb5bd; font-weight: 400; }}
.node-badge {{
  flex-shrink: 0; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600; margin-top: 2px;
}}
.badge-papers {{ background: #1a3a5c; color: #4ea8de; }}
.badge-leaves {{ background: #1a3a3a; color: #48bfe3; }}
.node-children {{ display: none; }}
.node-children.open {{ display: block; }}
.node-info {{
  display: none; margin: 4px 0 8px 44px; padding: 10px 14px;
  background: #16213e; border-left: 3px solid #4ea8de; border-radius: 4px;
  font-size: 13px; line-height: 1.5; color: #bbb;
}}
.node-info.open {{ display: block; }}
.node-info strong {{ color: #e0e0e0; }}
</style>
</head>
<body>
<h1>TaxoAdapt V2 Taxonomy Tree (Normalized Labels)</h1>
<div class="stats">{n_leaves} leaf clusters | {n_papers} papers | depth {depth}</div>
<div class="search-box">
  <input type="text" id="search" placeholder="Search labels..." oninput="doSearch(this.value)">
  <div class="search-info" id="search-info"></div>
</div>
<div id="tree"></div>
<script>
const treeData = {tree_json};

function countLeaves(n) {{
  if (!n.children || n.children.length === 0) return 1;
  return n.children.reduce((s, c) => s + countLeaves(c), 0);
}}
function countPapers(n) {{
  if (!n.children || n.children.length === 0) return n.n_papers || 0;
  return n.children.reduce((s, c) => s + countPapers(c), 0);
}}

function renderNode(node, isRoot) {{
  const div = document.createElement('div');
  div.className = 'tree-node' + (isRoot ? ' root' : '');
  const isLeaf = !node.children || node.children.length === 0;
  const header = document.createElement('div');
  header.className = 'node-header';
  const nPapers = isLeaf ? (node.n_papers || 0) : countPapers(node);
  const nLeaves = isLeaf ? 0 : countLeaves(node);
  const level = node.level || 0;
  const levelClass = isLeaf ? 'leaf' : ('l' + Math.min(level, 3));

  if (!isLeaf) {{
    const toggle = document.createElement('span');
    toggle.className = 'toggle';
    toggle.textContent = '+';
    header.appendChild(toggle);
  }} else {{
    const dot = document.createElement('span');
    dot.className = 'leaf-dot';
    dot.textContent = '·';
    header.appendChild(dot);
  }}

  const label = document.createElement('span');
  label.className = 'node-label ' + levelClass;
  label.textContent = node.label;
  header.appendChild(label);

  const badge = document.createElement('span');
  badge.className = 'node-badge badge-papers';
  badge.textContent = nPapers + ' papers';
  header.appendChild(badge);

  if (!isLeaf) {{
    const lbadge = document.createElement('span');
    lbadge.className = 'node-badge badge-leaves';
    lbadge.textContent = nLeaves + ' clusters';
    header.appendChild(lbadge);
  }}

  div.appendChild(header);

  if (node.description) {{
    const info = document.createElement('div');
    info.className = 'node-info';
    info.innerHTML = '<strong>Description:</strong> ' + node.description;
    div.appendChild(info);
  }}

  if (!isLeaf) {{
    const childrenDiv = document.createElement('div');
    childrenDiv.className = 'node-children';
    node.children.forEach(c => childrenDiv.appendChild(renderNode(c, false)));
    div.appendChild(childrenDiv);
    header.addEventListener('click', (e) => {{
      e.stopPropagation();
      const isOpen = childrenDiv.classList.toggle('open');
      header.querySelector('.toggle').textContent = isOpen ? '\\u2212' : '+';
      const infoEl = div.querySelector(':scope > .node-info');
      if (infoEl) infoEl.classList.toggle('open', isOpen);
    }});
  }} else {{
    header.addEventListener('click', (e) => {{
      e.stopPropagation();
      const infoEl = div.querySelector(':scope > .node-info');
      if (infoEl) infoEl.classList.toggle('open');
    }});
  }}

  return div;
}}

document.getElementById('tree').appendChild(renderNode(treeData, true));
const rootChildren = document.querySelector('.tree-node.root > .node-children');
if (rootChildren) {{
  rootChildren.classList.add('open');
  const rootToggle = document.querySelector('.tree-node.root > .node-header .toggle');
  if (rootToggle) rootToggle.textContent = '\\u2212';
}}

function doSearch(query) {{
  document.querySelectorAll('.node-header.highlight').forEach(el => el.classList.remove('highlight'));
  const info = document.getElementById('search-info');
  if (!query || query.length < 2) {{ info.textContent = ''; return; }}
  const q = query.toLowerCase();
  let count = 0;
  document.querySelectorAll('.node-label').forEach(el => {{
    if (el.textContent.toLowerCase().includes(q)) {{
      el.closest('.node-header').classList.add('highlight');
      count++;
      let parent = el.closest('.tree-node').parentElement;
      while (parent && parent.classList) {{
        if (parent.classList.contains('node-children')) parent.classList.add('open');
        const prevToggle = parent.previousElementSibling?.querySelector('.toggle');
        if (prevToggle) prevToggle.textContent = '\\u2212';
        parent = parent.parentElement;
      }}
    }}
  }});
  info.textContent = count + ' matches';
}}
</script>
</body>
</html>'''

    output_path.write_text(html, encoding="utf-8")
    log.info("HTML written to %s", output_path)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Normalize taxonomy tree labels")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    with open(args.checkpoint, encoding="utf-8") as f:
        data = json.load(f)
    stage = data.pop("_pipeline_stage", "")

    # Collect all internal nodes
    all_internal = collect_internal_nodes(data)
    question_nodes = [n for n in all_internal if is_question_label(n["current_label"])]
    log.info("Total internal nodes: %d, question-form: %d", len(all_internal), len(question_nodes))

    if not question_nodes:
        log.info("No question-form labels found, nothing to do")
        return

    # Create LLM client
    from ideaeval_benchmark.taxoadapt.llm import LLMClient, RateLimiter, RPM_DEFAULT
    llm = LLMClient(rate_limiter=RateLimiter(rpm=RPM_DEFAULT))

    # Normalize
    label_map = normalize_labels(question_nodes, llm, max_workers=args.max_workers)

    # Apply to a deep copy
    normalized = copy.deepcopy(data)
    n_changed = apply_labels(normalized, label_map)
    log.info("Changed %d labels", n_changed)

    # Print some examples
    for n in question_nodes[:10]:
        old = n["current_label"]
        new = label_map.get(n["id"], old)
        if old != new:
            print(f"  {n['id']}: {old[:60]}  →  {new}")

    # Save normalized checkpoint
    args.output_dir.mkdir(parents=True, exist_ok=True)
    normalized["_pipeline_stage"] = stage
    out_ckpt = args.output_dir / "checkpoint_normalized.json"
    with open(out_ckpt, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    log.info("Normalized checkpoint saved to %s", out_ckpt)

    # Generate HTML
    out_html = args.output_dir / "taxonomy_tree_normalized.html"
    generate_html(normalized, out_html)


if __name__ == "__main__":
    main()
