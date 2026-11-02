#!/usr/bin/env python3
"""Offline evaluator: aggregate scientific-hypothesis-generator gate verdicts across runs.

Harvests the per-dimension scores the ``scientific-reviewer`` already wrote into each
run's ``notes/reviews/*.md`` (plus ``run_record.json`` status) into a quality scorecard
across many runs or k-samples. It makes no new LLM calls — it reads what the gates
produced — so an asset-version change can be compared against a baseline with evidence
rather than intuition (improvement area #6).

Usage:
    uv run python scripts/evaluate_runs.py [--runs-dir .runs] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

GATES = ("evidence", "hypothesis_method", "proposal")
REVIEW_FILES = {
    "evidence": "evidence_review.md",
    "hypothesis_method": "hypothesis_method_review.md",
    "proposal": "proposal_review.md",
}
DIMENSIONS = {
    "evidence": ["grounding", "coverage", "depth", "synthesis", "open_problems"],
    "hypothesis_method": ["problem_value", "novelty", "soundness", "feasibility", "experimental_quality"],
    "proposal": ["problem_value", "novelty", "soundness", "feasibility", "experimental_quality"],
}


@dataclass
class GateVerdict:
    gate: str
    status: str | None
    scores: dict[str, int]
    binding_dimension: str | None
    precondition_failures: list[str]


@dataclass
class RunScore:
    run_id: str
    topic: str | None
    harness: str | None
    model: str | None
    run_status: str | None
    validation_valid: bool | None
    gates: dict[str, GateVerdict]


def _frontmatter_block(text: str) -> str:
    """Return the leading YAML frontmatter: the ``---``-fenced block, or the leading
    run of non-blank lines when there is no fence."""
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


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(_frontmatter_block(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _score_value(raw: Any) -> int | None:
    """A dimension score may be a bare int (`novelty: 6`) or a nested object
    (`novelty: {score: 6, rationale: ...}`); return the integer either way."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, dict):
        return _score_value(raw.get("score"))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _precondition_failures(value: Any) -> list[str]:
    """Names of precondition entries whose verdict is not a pass, tolerating flat
    (`name: pass`) and nested (`name: {status: pass}`) forms."""
    failed: list[str] = []
    if not isinstance(value, dict):
        return failed
    for name, entry in value.items():
        verdict = entry.get("status", entry.get("verdict", "")) if isinstance(entry, dict) else entry
        if str(verdict).strip().lower() not in {"pass", "passed", "ok", "true", "yes", ""}:
            failed.append(str(name))
    return failed


def parse_gate_verdict(path: Path, gate: str) -> GateVerdict | None:
    if not path.is_file():
        return None
    meta = _frontmatter(path)
    if not meta:
        return None
    raw_scores = meta.get("scores", {})
    scores: dict[str, int] = {}
    if isinstance(raw_scores, dict):
        for dim in DIMENSIONS[gate]:
            value = _score_value(raw_scores.get(dim))
            if value is not None:
                scores[dim] = value
    status = meta.get("status")
    return GateVerdict(
        gate=gate,
        status=str(status).upper() if status is not None else None,
        scores=scores,
        binding_dimension=meta.get("binding_dimension"),
        precondition_failures=_precondition_failures(meta.get("preconditions")),
    )


def load_run_score(run_dir: Path) -> RunScore | None:
    reviews = run_dir / "agent_workspace" / "notes" / "reviews"
    record_path = run_dir / "run_record.json"
    record: dict[str, Any] = {}
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}
    gates: dict[str, GateVerdict] = {}
    for gate in GATES:
        verdict = parse_gate_verdict(reviews / REVIEW_FILES[gate], gate)
        if verdict is not None:
            gates[gate] = verdict
    if not gates and not record:
        return None
    validation = record.get("validation")
    return RunScore(
        run_id=record.get("run_id") or run_dir.name,
        topic=record.get("topic"),
        harness=record.get("harness"),
        model=record.get("model"),
        run_status=record.get("status"),
        validation_valid=validation.get("valid") if isinstance(validation, dict) else None,
        gates=gates,
    )


def discover_runs(runs_dir: Path) -> list[Path]:
    """Run dirs under runs_dir, for either layout: flat (<run-id>/) or grouped by
    model (<model>/<run-id>/). A run dir contains agent_workspace/."""
    candidates = list(runs_dir.glob("*/agent_workspace")) + list(runs_dir.glob("*/*/agent_workspace"))
    return sorted({c.parent for c in candidates if c.is_dir()})


def _count(values: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def aggregate(runs: list[RunScore]) -> dict[str, Any]:
    out: dict[str, Any] = {"n_runs": len(runs), "run_status": _count([r.run_status for r in runs]), "gates": {}}
    for gate in GATES:
        verdicts = [r.gates[gate] for r in runs if gate in r.gates]
        if not verdicts:
            continue
        dim_stats: dict[str, Any] = {}
        for dim in DIMENSIONS[gate]:
            vals = [v.scores[dim] for v in verdicts if dim in v.scores]
            if vals:
                dim_stats[dim] = {
                    "n": len(vals),
                    "mean": round(statistics.fmean(vals), 2),
                    "min": min(vals),
                    "max": max(vals),
                }
        out["gates"][gate] = {
            "n": len(verdicts),
            "status": _count([v.status for v in verdicts]),
            "dimensions": dim_stats,
            "binding": _count([v.binding_dimension for v in verdicts]),
            "precondition_failures": sum(len(v.precondition_failures) for v in verdicts),
        }
    return out


def format_report(runs: list[RunScore], agg: dict[str, Any]) -> str:
    lines = [f"Evaluated {agg['n_runs']} run(s)."]
    if agg["run_status"]:
        lines.append("Run status: " + ", ".join(f"{k}={v}" for k, v in sorted(agg["run_status"].items())))
    lines.append("")
    for gate in GATES:
        gate_agg = agg["gates"].get(gate)
        if not gate_agg:
            continue
        lines.append(f"== {gate} gate ({gate_agg['n']} verdict(s)) ==")
        lines.append("  status: " + (", ".join(f"{k}={v}" for k, v in sorted(gate_agg["status"].items())) or "n/a"))
        for dim, stat in gate_agg["dimensions"].items():
            lines.append(f"  {dim:<20} mean={stat['mean']:<5} min={stat['min']} max={stat['max']} (n={stat['n']})")
        if gate_agg["binding"]:
            ranked = sorted(gate_agg["binding"].items(), key=lambda kv: (-kv[1], kv[0]))
            lines.append("  binds on: " + ", ".join(f"{k}={v}" for k, v in ranked))
        if gate_agg["precondition_failures"]:
            lines.append(f"  precondition failures: {gate_agg['precondition_failures']}")
        lines.append("")
    lines.append("== per run ==")
    for run in runs:
        prop = run.gates.get("proposal")
        scores = ""
        if prop and prop.scores:
            scores = " ".join(f"{dim[:4]}={prop.scores[dim]}" for dim in DIMENSIONS["proposal"] if dim in prop.scores)
        lines.append(f"  {run.run_id} [{run.run_status}] {run.model or ''} | proposal: {scores or 'n/a'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate gate verdicts across runs into a quality scorecard.")
    parser.add_argument("--runs-dir", default=Path(".runs"), type=Path, help="Directory of run workspaces (default: .runs).")
    parser.add_argument("--json", type=Path, default=None, help="Also write the aggregate as JSON to this path.")
    args = parser.parse_args(argv)
    if not args.runs_dir.is_dir():
        raise SystemExit(f"runs dir not found: {args.runs_dir}")
    runs = [score for path in discover_runs(args.runs_dir) if (score := load_run_score(path)) is not None]
    agg = aggregate(runs)
    if args.json is not None:
        args.json.write_text(json.dumps(agg, indent=2), encoding="utf-8")
    print(format_report(runs, agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
