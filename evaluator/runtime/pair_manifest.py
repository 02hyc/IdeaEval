"""Shared helpers for pair-manifest based ranker runs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def _proposal_root_candidate_from_manifest_path(proposal_root: Path, path: Path) -> Path | None:
    """Map an absolute manifest path back under the caller-provided proposal root."""
    if not path.is_absolute():
        return proposal_root / path
    parts = path.parts
    if "proposals" not in parts:
        return None
    idx = len(parts) - 1 - list(reversed(parts)).index("proposals")
    rel_parts = parts[idx + 1 :]
    if not rel_parts:
        return None
    return proposal_root.joinpath(*rel_parts)


def paper_id_from_pair_item(pair_item: dict[str, Any], side: str) -> str:
    """Return a stable paper/proposal id for one side of a pair manifest item."""
    for container_name in (f"paper_{side}", f"proposal_{side}"):
        container = pair_item.get(container_name)
        if isinstance(container, dict):
            paper_id = (
                container.get("paper_id")
                or container.get("proposal_id")
                or container.get("ai_run_id")
                or container.get("run_id")
            )
            if paper_id:
                return re.sub(r"[^A-Za-z0-9._-]+", "_", str(paper_id)).strip("_")
            proposal_path = container.get("proposal_path")
            if proposal_path:
                path = Path(str(proposal_path))
                fallback = path.parent.name if path.parent.name else path.stem
                if fallback:
                    return re.sub(r"[^A-Za-z0-9._-]+", "_", fallback).strip("_")
    raise KeyError(f"Could not find paper/proposal id for side {side!r} in pair {pair_item.get('pair_id')!r}")


def find_pair_item_proposal_file(
    proposal_root: Path,
    pair_item: dict[str, Any],
    side: str,
    paper_id: str,
) -> Path:
    """Resolve a proposal markdown file from a pair manifest item."""
    candidates: list[Path] = []
    for container_name in (f"paper_{side}", f"proposal_{side}"):
        container = pair_item.get(container_name)
        if not isinstance(container, dict):
            continue
        proposal_path = container.get("proposal_path")
        if not proposal_path:
            continue
        path = Path(str(proposal_path))
        root_candidate = _proposal_root_candidate_from_manifest_path(proposal_root, path)
        if root_candidate is not None:
            candidates.append(root_candidate)
        if path.is_absolute():
            candidates.append(path)

    candidates.extend(
        [
            proposal_root / f"{paper_id}_proposal.md",
            proposal_root / paper_id / f"{paper_id}_proposal.md",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("No proposal file found: " + ", ".join(str(p) for p in candidates))


def ground_truth_from_pair_item(pair_item: dict[str, Any]) -> str:
    """Return directional ground truth in display-neutral canonical form."""
    label = str(pair_item.get("label") or "").strip().lower()
    if label in {"a_better", "a", "a_wins", "a > b"}:
        return "A > B"
    if label in {"b_better", "b", "b_wins", "a < b"}:
        return "A < B"
    if "paper_a" in pair_item and "paper_b" in pair_item:
        ca = pair_item["paper_a"].get("citations")
        cb = pair_item["paper_b"].get("citations")
        if ca is not None and cb is not None and ca != cb:
            return "A > B" if ca > cb else "A < B"
    raise ValueError(f"Could not derive directional ground truth for pair {pair_item.get('pair_id')!r}")


def normalize_result(result: Any) -> str:
    """Normalize a judge result to `A > B` or `A < B`."""
    text = str(result or "").strip().upper().replace(" ", "")
    if text in {"A>B", "A>B."}:
        return "A > B"
    if text in {"A<B", "A<B."}:
        return "A < B"
    if "A" in text and "B" in text:
        if ">" in text:
            return "A > B"
        if "<" in text:
            return "A < B"
    raise ValueError(f"Invalid result: {result!r}")


def mirror_result(result: str) -> str:
    """Mirror a displayed result back to the opposite proposal order."""
    if result == "A > B":
        return "A < B"
    if result == "A < B":
        return "A > B"
    return result


def random_swap_for_pair(pair_id: str, seed: int) -> bool:
    """Deterministically choose whether to swap display order for one pair."""
    digest = hashlib.sha256(f"{seed}:{pair_id}".encode("utf-8")).hexdigest()
    value = int(digest[:16], 16) / float(16**16)
    return value < 0.5


def strip_proposal_title(text: str) -> str:
    """Remove the first Markdown H1 title from a proposal, if present."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            rest = lines[i + 1 :]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "\n".join(rest).lstrip()
        if line.strip():
            break
    return text
