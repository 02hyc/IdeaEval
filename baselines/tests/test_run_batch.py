from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from run_batch import (
    Supervisor,
    aggregate_batch_metrics,
    expand_items,
    inspect_run,
    load_inputs,
    normalize_run_args,
    validate_run_id,
)


def test_load_inputs_parses_id_topic_date(tmp_path: Path) -> None:
    input_path = tmp_path / "inputs.json"
    input_path.write_text(
        json.dumps(
            {
                "inputs": [
                    {"id": "c001", "topic": "Topic A.", "date": "2025-11-11"},
                    {"id": "c002", "topic": "Topic B.", "date": "2025-10-15"},
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = load_inputs(input_path)

    assert rows == [
        (0, "c001", "Topic A.", "2025-11-11"),
        (1, "c002", "Topic B.", "2025-10-15"),
    ]


def test_expand_items_with_samples_replicates_each_input(tmp_path: Path) -> None:
    rows = [(0, "c001", "Topic A.", "2025-11-11")]

    items = expand_items(rows, samples=3)

    assert [item.run_id for item in items] == ["c001-01", "c001-02", "c001-03"]
    assert all(item.source_id == "c001" for item in items)
    assert [item.sample for item in items] == [1, 2, 3]


def test_expand_items_with_single_sample_keeps_bare_id(tmp_path: Path) -> None:
    rows = [(0, "c001", "Topic A.", "2025-11-11")]

    items = expand_items(rows, samples=1)

    assert [item.run_id for item in items] == ["c001"]


def test_expand_items_widens_suffix_for_large_k(tmp_path: Path) -> None:
    rows = [(0, "c001", "T", "2025-11-11")]

    items = expand_items(rows, samples=100)
    ids = [item.run_id for item in items]

    assert ids[0] == "c001-001"
    assert ids[99] == "c001-100"
    assert ids == sorted(ids), "lexicographic sort must match sample order"


def test_load_inputs_rejects_duplicate_id(tmp_path: Path) -> None:
    input_path = tmp_path / "inputs.json"
    input_path.write_text(
        json.dumps(
            {
                "inputs": [
                    {"id": "c001", "topic": "A", "date": "2025-11-11"},
                    {"id": "c001", "topic": "B", "date": "2025-11-12"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Duplicate id"):
        load_inputs(input_path)


def test_load_inputs_rejects_invalid_date(tmp_path: Path) -> None:
    input_path = tmp_path / "inputs.json"
    input_path.write_text(
        json.dumps({"inputs": [{"id": "c001", "topic": "A", "date": "not-a-date"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="invalid date"):
        load_inputs(input_path)


@pytest.mark.parametrize("rid", ["c001", "c001-01", "a_b.c-d", "C001"])
def test_validate_run_id_accepts_safe_ids(rid: str) -> None:
    validate_run_id(rid)


@pytest.mark.parametrize(
    "rid",
    ["", "-foo", ".foo", "foo bar", "foo/bar", "foo\\bar", "foo;bar", "foo\nbar", "..", "."],
)
def test_validate_run_id_rejects_unsafe_ids(rid: str) -> None:
    with pytest.raises(SystemExit, match="not a safe run id"):
        validate_run_id(rid)


def test_inspect_run_uses_proposal_path_from_record(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    run_dir = workspace_root / "c001"
    run_dir.mkdir(parents=True)
    custom_proposal = "/some/agent_dir_override/proposal.md"
    (run_dir / "run_record.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "validation": {"valid": True},
                "artifacts": {"proposal": custom_proposal},
            }
        ),
        encoding="utf-8",
    )

    result = inspect_run(workspace_root, "c001")

    assert result["success"] is True
    assert result["proposal_path"] == custom_proposal


def test_inspect_run_extracts_resource_metrics_and_tool_counts(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    run_dir = workspace_root / "c001"
    log_dir = run_dir / "framework" / "logs"
    refs_dir = run_dir / "agent_workspace" / "references" / "paper-a"
    log_dir.mkdir(parents=True)
    refs_dir.mkdir(parents=True)
    (run_dir / "run_record.json").write_text(
        json.dumps(
            {
                "run_id": "c001",
                "status": "succeeded",
                "harness": "codex",
                "model": "gpt-5.4",
                "started_at": "2026-06-18T00:00:00Z",
                "completed_at": "2026-06-18T00:00:03Z",
                "validation": {"valid": True},
                "artifacts": {"proposal": "/proposal.md"},
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                    "reasoning_output_tokens": 10,
                    "total_tokens": 130,
                    "total_cost_usd": 0.42,
                    "turns": 1,
                    "duration_ms": 2500,
                },
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "adapter_events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "item/started",
                        "message": {
                            "event": {
                                "payload": {
                                    "item": {
                                        "type": "mcpToolCall",
                                        "tool": "mcp__scholar-gateway__web_search",
                                    }
                                }
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "item/started",
                        "message": {
                            "event": {
                                "payload": {
                                    "item": {
                                        "type": "commandExecution",
                                    }
                                }
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = inspect_run(workspace_root, "c001")

    assert result["success"] is True
    assert result["metrics"] == {
        "harness": "codex",
        "model": "gpt-5.4",
        "started_at": "2026-06-18T00:00:00Z",
        "completed_at": "2026-06-18T00:00:03Z",
        "wall_time_ms": 3000,
        "duration_ms": 2500,
        "turns": 1,
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "cache_creation_input_tokens": None,
        "output_tokens": 30,
        "reasoning_output_tokens": 10,
        "total_tokens": 130,
        "total_cost_usd": 0.42,
        "tool_call_count": 2,
        "tool_call_counts": {"Bash": 1, "mcp:web_search": 1},
        "scholar_tool_call_counts": {"extract_paper": 0, "web_fetch": 0, "web_search": 1},
        "event_count": 2,
        "references_dir_count": 1,
    }


def test_aggregate_batch_metrics_sums_completed_items() -> None:
    items = {
        "a": {
            "status": "succeeded",
            "metrics": {
                "wall_time_ms": 1000,
                "duration_ms": 800,
                "total_cost_usd": 0.5,
                "total_tokens": 10,
                "input_tokens": 6,
                "output_tokens": 4,
                "reasoning_output_tokens": 1,
                "tool_call_count": 2,
                "tool_call_counts": {"mcp:web_search": 2},
                "scholar_tool_call_counts": {"web_search": 2},
            },
        },
        "b": {
            "status": "failed",
            "metrics": {
                "wall_time_ms": 3000,
                "duration_ms": None,
                "total_cost_usd": None,
                "total_tokens": None,
                "tool_call_count": 1,
                "tool_call_counts": {"Bash": 1},
                "scholar_tool_call_counts": {},
            },
        },
    }

    metrics = aggregate_batch_metrics(items)

    assert metrics["completed_run_count"] == 2
    assert metrics["total_wall_time_ms"] == 4000
    assert metrics["total_agent_duration_ms"] == 800
    assert metrics["total_cost_usd"] == 0.5
    assert metrics["total_tokens"] == 10
    assert metrics["tool_call_count"] == 3
    assert metrics["tool_call_counts"] == {"Bash": 1, "mcp:web_search": 2}
    assert metrics["scholar_tool_call_counts"]["web_search"] == 2
    assert metrics["missing_cost_count"] == 1
    assert metrics["missing_usage_count"] == 1


def test_inspect_run_without_record_returns_no_success(tmp_path: Path) -> None:
    result = inspect_run(tmp_path, "c001")

    assert result["success"] is False
    assert result["run_record_exists"] is False
    assert result["proposal_path"] is None


def test_inspect_run_returns_unreadable_when_record_is_malformed_json(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    run_dir = workspace_root / "c001"
    run_dir.mkdir(parents=True)
    (run_dir / "run_record.json").write_text("{not-json", encoding="utf-8")

    result = inspect_run(workspace_root, "c001")

    assert result["success"] is False
    assert result["failure_kind"] == "unreadable_run_record"
    assert result.get("error")


def _make_supervisor_args(tmp_path: Path, *, overwrite: bool, batch_dir: Path) -> argparse.Namespace:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dummy", encoding="utf-8")
    input_path = tmp_path / "inputs.json"
    input_path.write_text(json.dumps({"inputs": []}), encoding="utf-8")
    return argparse.Namespace(
        config=str(config_path),
        input=str(input_path),
        name=batch_dir.name,
        workspace_root=str(tmp_path / "ws"),
        concurrency=1,
        retries=0,
        samples=1,
        force=False,
        overwrite=overwrite,
        uv="/usr/bin/true",
        poll_interval_seconds=0.1,
        launch_delay_seconds=0.0,
        batch_id=batch_dir.name,
        batch_dir=str(batch_dir),
    )


def test_overwrite_guard_refuses_existing_state_without_flag(tmp_path: Path) -> None:
    batch_dir = tmp_path / "ws" / "_batch"
    batch_dir.mkdir(parents=True)
    (batch_dir / "state.json").write_text("{}", encoding="utf-8")

    args = _make_supervisor_args(tmp_path, overwrite=False, batch_dir=batch_dir)
    sup = Supervisor(args)

    with pytest.raises(SystemExit, match="batch metadata already exists"):
        sup._guard_existing_batch_metadata()

    assert (batch_dir / "state.json").is_file(), "state.json must remain untouched"


def test_overwrite_guard_rotates_existing_state_when_flag_is_set(tmp_path: Path) -> None:
    batch_dir = tmp_path / "ws" / "_batch"
    batch_dir.mkdir(parents=True)
    (batch_dir / "state.json").write_text("{\"old\": true}", encoding="utf-8")
    (batch_dir / "events.jsonl").write_text("{\"e\": 1}\n", encoding="utf-8")

    args = _make_supervisor_args(tmp_path, overwrite=True, batch_dir=batch_dir)
    sup = Supervisor(args)
    sup._guard_existing_batch_metadata()

    assert not (batch_dir / "state.json").exists()
    assert not (batch_dir / "events.jsonl").exists()
    rotated = sorted(p.name for p in batch_dir.iterdir())
    assert any(name.startswith("state.json.") and name.endswith(".bak") for name in rotated)
    assert any(name.startswith("events.jsonl.") and name.endswith(".bak") for name in rotated)


def test_overwrite_guard_is_noop_when_no_prior_state(tmp_path: Path) -> None:
    batch_dir = tmp_path / "ws" / "_batch"
    batch_dir.mkdir(parents=True)
    args = _make_supervisor_args(tmp_path, overwrite=False, batch_dir=batch_dir)

    Supervisor(args)._guard_existing_batch_metadata()


def test_normalize_run_args_validates_concurrency_retries_samples(tmp_path: Path) -> None:
    base = argparse.Namespace(
        config=str(tmp_path / "configs.yaml"),
        input=str(tmp_path / "inputs.json"),
        name=None,
        workspace_root=None,
        concurrency=1,
        retries=0,
        samples=1,
        force=False,
        overwrite=False,
        uv="/usr/bin/true",
        poll_interval_seconds=5.0,
        launch_delay_seconds=0.0,
    )
    (tmp_path / "configs.yaml").write_text("", encoding="utf-8")
    (tmp_path / "inputs.json").write_text("{\"inputs\": []}", encoding="utf-8")

    for field, value, msg in (
        ("concurrency", 0, "--concurrency must be >= 1"),
        ("retries", -1, "--retries must be >= 0"),
        ("samples", 0, "--samples must be >= 1"),
    ):
        ns = argparse.Namespace(**vars(base))
        setattr(ns, field, value)
        with pytest.raises(SystemExit, match=msg):
            normalize_run_args(ns)


def test_build_run_command_passes_model_profile_through() -> None:
    from run_batch import build_run_command

    item = expand_items([(0, "c001", "Topic A.", "2025-11-11")], samples=1)[0]
    common = dict(
        uv="uv",
        config_path=Path("configs/base.yaml"),
        workspace_root=Path("/tmp/ws"),
        item=item,
        input_topic_path=Path("/tmp/topic.txt"),
        clean_existing=False,
    )

    with_profile = build_run_command(**common, model_profile="gpt-5.4")
    assert "--model-profile" in with_profile
    assert with_profile[with_profile.index("--model-profile") + 1] == "gpt-5.4"

    without_profile = build_run_command(**common)
    assert "--model-profile" not in without_profile
