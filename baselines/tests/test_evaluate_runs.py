from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _load() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_runs.py"
    spec = importlib.util.spec_from_file_location("evaluate_runs", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves module globals via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_run(base: Path, run_id: str, *, novelty: int, evidence_precond_fail: bool = False, status: str = "succeeded") -> None:
    reviews = base / run_id / "agent_workspace" / "notes" / "reviews"
    reviews.mkdir(parents=True)
    (base / run_id / "run_record.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "topic": "Some topic",
                "harness": "claude",
                "model": "opus-x",
                "status": status,
                "validation": {"valid": True},
            }
        ),
        encoding="utf-8",
    )
    # proposal verdict: nested score objects (the live reviewer format)
    nested = ["---", "gate: proposal", "status: PASS", "preconditions:", "  no_fabricated_literature: pass", "  no_fake_results: pass", "scores:"]
    proposal = {"problem_value": 8, "novelty": novelty, "soundness": 7, "feasibility": 6, "experimental_quality": 8}
    for dim, score in proposal.items():
        nested += [f"  {dim}:", f"    score: {score}", "    rationale: ok"]
    nested += ["binding_dimension: novelty", "unresolved_critical: 0", "---", "body"]
    (reviews / "proposal_review.md").write_text("\n".join(nested) + "\n", encoding="utf-8")
    # evidence verdict: flat scores + nested (possibly failing) preconditions + list unresolved_critical
    pc = "FAIL" if evidence_precond_fail else "PASS"
    evidence = (
        "---\ngate: evidence\nstatus: PASS\n"
        "preconditions:\n"
        f"  no_fabricated_or_ungrounded_literature:\n    status: {pc}\n"
        "  no_fake_or_implied_empirical_results:\n    status: PASS\n"
        "scores:\n  grounding: 8\n  coverage: 7\n  depth: 7\n  synthesis: 6\n  open_problems: 8\n"
        "unresolved_critical: []\n---\n"
    )
    (reviews / "evidence_review.md").write_text(evidence, encoding="utf-8")


def test_load_run_score_parses_nested_and_flat_scores(tmp_path: Path) -> None:
    ev = _load()
    _make_run(tmp_path, "run_a", novelty=6)
    score = ev.load_run_score(tmp_path / "run_a")
    assert score is not None
    assert score.topic == "Some topic" and score.model == "opus-x" and score.run_status == "succeeded"
    # nested proposal scores resolve to ints
    assert score.gates["proposal"].scores == {
        "problem_value": 8,
        "novelty": 6,
        "soundness": 7,
        "feasibility": 6,
        "experimental_quality": 8,
    }
    # flat evidence scores resolve too
    assert score.gates["evidence"].scores["grounding"] == 8
    assert score.gates["evidence"].precondition_failures == []


def test_aggregate_means_and_precondition_failures(tmp_path: Path) -> None:
    ev = _load()
    _make_run(tmp_path, "run_a", novelty=6)
    _make_run(tmp_path, "run_b", novelty=8, evidence_precond_fail=True, status="failed")
    runs = [ev.load_run_score(p) for p in ev.discover_runs(tmp_path)]
    runs = [r for r in runs if r is not None]
    agg = ev.aggregate(runs)
    assert agg["n_runs"] == 2
    assert agg["run_status"] == {"succeeded": 1, "failed": 1}
    # novelty mean across the two proposal verdicts: (6 + 8) / 2 = 7.0
    assert agg["gates"]["proposal"]["dimensions"]["novelty"]["mean"] == 7.0
    assert agg["gates"]["proposal"]["dimensions"]["novelty"]["min"] == 6
    # the nested precondition FAIL in run_b is detected
    assert agg["gates"]["evidence"]["precondition_failures"] == 1
    report = ev.format_report(runs, agg)
    assert "proposal gate" in report and "novelty" in report


def test_runs_without_verdicts_are_skipped(tmp_path: Path) -> None:
    ev = _load()
    (tmp_path / "empty" / "agent_workspace").mkdir(parents=True)
    assert ev.load_run_score(tmp_path / "empty") is None
