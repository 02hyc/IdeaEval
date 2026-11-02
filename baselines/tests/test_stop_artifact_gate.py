from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


NEW_REQUIRED = [
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


def _load_gate() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "research_ideation_agent"
        / "assets"
        / "scientific-hypothesis-generator"
        / "hooks"
        / "stop_artifact_gate.py"
    )
    spec = importlib.util.spec_from_file_location("stop_artifact_gate", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cleared_review(status: str = "PASS") -> str:
    return (
        "---\n"
        "gate: proposal\n"
        f"status: {status}\n"
        "preconditions:\n"
        "  no_fabricated_literature: pass\n"
        "  no_fake_results: pass\n"
        "unresolved_critical: 0\n"
        "---\nok\n"
    )


def _write_required(workspace: Path, *, review_status: str = "PASS") -> None:
    for rel in NEW_REQUIRED:
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.startswith("notes/reviews/"):
            path.write_text(_cleared_review(review_status), encoding="utf-8")
        else:
            path.write_text("content\n", encoding="utf-8")


def test_blocks_when_a_required_artifact_is_missing(tmp_path: Path) -> None:
    gate = _load_gate()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "task_contract.md").write_text("contract", encoding="utf-8")
    result = gate.evaluate_stop({"cwd": str(tmp_path)})
    assert result is not None and result["decision"] == "block"
    assert "proposal.md" in result["reason"]


def test_blocks_when_review_not_cleared(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_required(tmp_path, review_status="REVISE")
    result = gate.evaluate_stop({"cwd": str(tmp_path)})
    assert result is not None and result["decision"] == "block"
    assert "not cleared" in result["reason"]


def test_passes_when_all_reviews_cleared(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_required(tmp_path, review_status="PASS")
    assert gate.evaluate_stop({"cwd": str(tmp_path)}) is None


def test_unavailable_review_is_treated_as_cleared(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_required(tmp_path, review_status="UNAVAILABLE")
    assert gate.evaluate_stop({"cwd": str(tmp_path)}) is None


def test_blocks_when_critical_unresolved(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_required(tmp_path, review_status="PASS")
    review = tmp_path / "notes" / "reviews" / "proposal_review.md"
    review.write_text(
        "---\ngate: proposal\nstatus: PASS\nunresolved_critical: 1\n---\n",
        encoding="utf-8",
    )
    result = gate.evaluate_stop({"cwd": str(tmp_path)})
    assert result is not None and result["decision"] == "block"


def test_missing_cwd_is_explicit(tmp_path: Path) -> None:
    gate = _load_gate()
    result = gate.evaluate_stop({})
    assert result is not None
    blob = (result.get("systemMessage", "") + result.get("reason", "")).lower()
    assert "cwd" in blob


def test_passes_with_unfenced_frontmatter(tmp_path: Path) -> None:
    # Reproduces the live reviewer output: bare `status: PASS` keys with no `---` fence.
    gate = _load_gate()
    _write_required(tmp_path, review_status="PASS")
    for name in ("evidence_review", "hypothesis_method_review", "proposal_review"):
        (tmp_path / "notes" / "reviews" / f"{name}.md").write_text(
            "gate: x\nstatus: PASS\nsummary: ok\n\n## Findings\nnone\n", encoding="utf-8"
        )
    assert gate.evaluate_stop({"cwd": str(tmp_path)}) is None


def test_passes_with_empty_list_unresolved_and_nested_preconditions(tmp_path: Path) -> None:
    # Reproduces the live gpt-5.4 verdicts: unresolved_critical as an empty list and
    # nested, renamed preconditions. A genuinely-cleared gate must not be blocked by
    # how the frontmatter represents "none".
    gate = _load_gate()
    _write_required(tmp_path)
    nested_pass = (
        "---\n"
        "gate: evidence\n"
        "status: PASS\n"
        "review_round: 2\n"
        "preconditions:\n"
        "  no_fabricated_or_ungrounded_literature:\n"
        "    status: PASS\n"
        '    details: "verified against raw sections"\n'
        "  no_fake_or_implied_empirical_results:\n"
        "    status: PASS\n"
        "binding_dimension: coverage\n"
        "unresolved_critical: []\n"
        "---\nThis round clears the gate.\n"
    )
    for name in ("evidence_review", "hypothesis_method_review", "proposal_review"):
        (tmp_path / "notes" / "reviews" / f"{name}.md").write_text(nested_pass, encoding="utf-8")
    assert gate.evaluate_stop({"cwd": str(tmp_path)}) is None


def test_blocks_when_unresolved_critical_list_nonempty(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_required(tmp_path)
    (tmp_path / "notes" / "reviews" / "proposal_review.md").write_text(
        "---\ngate: proposal\nstatus: PASS\n"
        "preconditions:\n  no_fabricated_literature: pass\n  no_fake_results: pass\n"
        "unresolved_critical:\n  - F1\n  - F2\n---\n",
        encoding="utf-8",
    )
    result = gate.evaluate_stop({"cwd": str(tmp_path)})
    assert result is not None and result["decision"] == "block"


def test_blocks_when_nested_precondition_fails(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_required(tmp_path)
    (tmp_path / "notes" / "reviews" / "evidence_review.md").write_text(
        "---\ngate: evidence\nstatus: PASS\nunresolved_critical: []\n"
        "preconditions:\n"
        "  no_fabricated_or_ungrounded_literature:\n    status: FAIL\n"
        "  no_fake_or_implied_empirical_results:\n    status: PASS\n---\n",
        encoding="utf-8",
    )
    result = gate.evaluate_stop({"cwd": str(tmp_path)})
    assert result is not None and result["decision"] == "block"
