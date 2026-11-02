#!/usr/bin/env python3
"""Stop hook (Claude + Codex) for scientific hypothesis generator artifact gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_ARTIFACTS = [
    "notes/task_contract.md",
    "notes/run_metadata.json",
    "notes/literature_review.md",
    "notes/reviews/evidence_review.md",
    "notes/gap_and_questions.md",
    "notes/hypothesis.md",
    "notes/novelty_check.md",
    "notes/method.md",
    "notes/experiment_plan.md",
    "notes/reviews/hypothesis_method_review.md",
    "proposal.md",
    "notes/reviews/proposal_review.md",
]

REVIEW_ARTIFACTS = [
    "notes/reviews/evidence_review.md",
    "notes/reviews/hypothesis_method_review.md",
    "notes/reviews/proposal_review.md",
]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = evaluate_stop(payload)
    except Exception as exc:  # Keep hook failures from trapping a completed run.
        result = {
            "systemMessage": f"Scientific hypothesis Stop hook skipped after {exc.__class__.__name__}: {exc}",
        }
    if result:
        print(json.dumps(result, ensure_ascii=False))
    return 0


def evaluate_stop(payload: dict[str, Any]) -> dict[str, Any] | None:
    cwd = payload.get("cwd")
    if not cwd:
        return {"systemMessage": "Stop hook skipped: no cwd in hook payload; cannot locate the run workspace."}
    workspace = Path(str(cwd)).resolve()
    missing_or_empty = _missing_or_empty_artifacts(workspace)
    if missing_or_empty:
        return _block(_missing_artifacts_reason(missing_or_empty))
    unresolved = _unresolved_reviews(workspace)
    if unresolved:
        return _block(_unresolved_reviews_reason(unresolved))
    return None


def _missing_or_empty_artifacts(workspace: Path) -> list[str]:
    missing = []
    for rel_path in REQUIRED_ARTIFACTS:
        path = workspace / rel_path
        if not path.is_file():
            missing.append(rel_path)
            continue
        try:
            if not path.read_text(encoding="utf-8").strip():
                missing.append(rel_path)
        except UnicodeDecodeError:
            if path.stat().st_size == 0:
                missing.append(rel_path)
    return missing


def _unresolved_reviews(workspace: Path) -> list[str]:
    problems: list[str] = []
    for rel in REVIEW_ARTIFACTS:
        meta = _read_frontmatter(workspace / rel)
        status = str(meta.get("status", "")).strip().upper()
        if status not in {"PASS", "UNAVAILABLE"}:
            problems.append(f"{rel}: status={status or 'MISSING'} (must be PASS)")
            continue
        if status == "PASS":
            if _has_unresolved_critical(meta.get("unresolved_critical")):
                problems.append(f"{rel}: has unresolved critical findings")
            for name in _failed_preconditions(meta.get("preconditions")):
                problems.append(f"{rel}: precondition {name} != pass")
    return problems


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse a verdict file's leading YAML frontmatter into a dict.

    Verdict files begin with a ``---``-fenced YAML block (some reviewers write the
    keys bare at the top with no fence). Reviewers emit either flat or nested YAML
    — ``unresolved_critical`` as an int or a list, ``preconditions`` as
    ``name: pass`` or ``name: {status: pass}`` — so we parse real YAML rather than
    scanning lines. Falls back to a tolerant flat parse if PyYAML is unavailable
    or the block does not parse as a mapping.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    block = _frontmatter_block(text)
    try:
        import yaml

        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _flat_frontmatter(block)


def _frontmatter_block(text: str) -> str:
    """Return the leading frontmatter text: the ``---``-fenced block, or the
    leading run of non-blank lines when there is no fence."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        body: list[str] = []
        for line in lines[1:]:
            if line.strip() == "---":
                break
            body.append(line)
        return "\n".join(body)
    body = []
    for line in lines:
        if not line.strip():
            break
        body.append(line)
    return "\n".join(body)


def _flat_frontmatter(block: str) -> dict[str, str]:
    """Degraded fallback when YAML parsing is unavailable: scan ``key: value``
    lines (last wins). Nesting is lost, but the value interpreters below still
    read the common scalar forms correctly."""
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _has_unresolved_critical(value: Any) -> bool:
    """True if the verdict still carries unresolved critical findings.

    Reviewers represent "none" as ``0``, ``[]``, an empty value, or by omitting
    the field; any of those clears. A positive count or a non-empty list does not.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"", "0", "0.0", "[]", "{}", "none", "null", "~", "false", "n/a", "na", "nil"}:
        return False
    if text.startswith("[") and text.endswith("]"):
        return bool(text[1:-1].strip())
    try:
        return int(text) != 0
    except ValueError:
        return True


def _failed_preconditions(value: Any) -> list[str]:
    """Names of precondition entries whose verdict is not a pass.

    Tolerates flat (``name: pass``) and nested (``name: {status: pass, ...}``)
    forms and whatever key names the reviewer chooses."""
    failed: list[str] = []
    if not isinstance(value, dict):
        return failed
    for name, entry in value.items():
        verdict = entry.get("status", entry.get("verdict", "")) if isinstance(entry, dict) else entry
        if str(verdict).strip().lower() not in {"pass", "passed", "ok", "true", "yes"}:
            failed.append(str(name))
    return failed


def _missing_artifacts_reason(missing_or_empty: list[str]) -> str:
    bullets = "\n".join(f"- {path}" for path in missing_or_empty)
    return (
        "Do not stop yet. This scientific-hypothesis-generator run is missing required "
        "artifacts or has empty required artifacts:\n"
        f"{bullets}\n\n"
        "Create or repair these files in the run workspace. Then re-read "
        "`notes/task_contract.md`, inspect the existing `notes/` files, and continue from "
        "the next unfinished stage before trying to stop again."
    )


def _unresolved_reviews_reason(problems: list[str]) -> str:
    bullets = "\n".join(f"- {problem}" for problem in problems)
    return (
        "Do not stop yet. One or more gate reviews are not cleared:\n"
        f"{bullets}\n\n"
        "Address each finding in the named review file (revise the artifact, or downgrade the "
        "claim and record the limitation), then re-invoke the `scientific-reviewer` subagent for "
        "that gate so it updates the verdict to PASS."
    )


def _block(reason: str) -> dict[str, str]:
    return {"decision": "block", "reason": reason}


if __name__ == "__main__":
    raise SystemExit(main())
