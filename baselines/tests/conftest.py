"""Shared fixtures for log_viewer tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SAMPLE_EVENTS = [
    {
        "event_index": 1,
        "timestamp": "2026-06-03T09:28:06+00:00",
        "phase": "initial",
        "type": "SystemMessage",
        "message": {"subtype": "init", "data": {"model": "opus-4-6", "tools": ["Bash"]}},
    },
    {
        "event_index": 2,
        "timestamp": "2026-06-03T09:28:15+00:00",
        "phase": "initial",
        "type": "AssistantMessage",
        "message": {
            "content": [
                {"id": "tool_1", "name": "Bash", "input": {"command": "ls"}},
            ],
            "model": "opus-4-6",
            "parent_tool_use_id": None,
        },
    },
    {
        "event_index": 3,
        "timestamp": "2026-06-03T09:28:16+00:00",
        "phase": "initial",
        "type": "UserMessage",
        "message": {
            "content": [{"tool_use_id": "tool_1", "content": "file1\nfile2", "is_error": None}],
            "parent_tool_use_id": None,
        },
    },
]


@pytest.fixture
def synthetic_run_dir(tmp_path: Path) -> Path:
    """A run dir with a 3-event jsonl and a completed run_record.json."""
    logs_dir = tmp_path / "framework" / "logs"
    logs_dir.mkdir(parents=True)
    jsonl = logs_dir / "adapter_events.jsonl"
    jsonl.write_text("\n".join(json.dumps(e) for e in SAMPLE_EVENTS) + "\n")

    record = {
        "schema_version": 1,
        "run_id": "test-run",
        "status": "succeeded",
        "harness": "claude",
        "model": "opus-4-6",
        "topic": "test topic",
        "started_at": "2026-06-03T09:28:03Z",
        "completed_at": "2026-06-03T09:28:20Z",
    }
    (tmp_path / "run_record.json").write_text(json.dumps(record))
    return tmp_path


@pytest.fixture
def running_run_dir(tmp_path: Path) -> Path:
    """A run dir with jsonl but no run_record.json yet (live run)."""
    logs_dir = tmp_path / "framework" / "logs"
    logs_dir.mkdir(parents=True)
    jsonl = logs_dir / "adapter_events.jsonl"
    jsonl.write_text("\n".join(json.dumps(e) for e in SAMPLE_EVENTS[:2]) + "\n")
    return tmp_path
