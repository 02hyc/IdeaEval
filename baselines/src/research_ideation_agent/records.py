"""Run-record serialization."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_ideation_agent.models import RunRequest, RunResult, to_jsonable
from research_ideation_agent.workspace import RunWorkspace


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write a JSON document to ``path``.

    Writes to ``<path>.tmp`` and ``os.replace``s into place so a SIGKILL
    or full disk mid-write cannot leave callers (e.g. ``scripts/run_batch.py``
    polling ``run_record.json``) staring at a half-written file.
    """
    text = json.dumps(payload, indent=2, default=str) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_run_record(
    *,
    request: RunRequest,
    workspace: RunWorkspace,
    result: RunResult,
    created_at: datetime,
    started_at: datetime,
    completed_at: datetime,
    rendered_artifacts: dict[str, Path],
    repair_prompt_paths: list[Path],
    adapter_sdk: str,
    adapter_sdk_version: str | None,
    env_var_names: list[str],
) -> None:
    record = {
        "schema_version": 1,
        "run_id": result.run_id,
        "status": result.status,
        "failure_kind": result.failure_kind,
        "created_at": _iso_z(created_at),
        "started_at": _iso_z(started_at),
        "completed_at": _iso_z(completed_at),
        "topic": request.topic,
        "harness": result.harness,
        "model": result.model,
        "config_path": str(request.config_path) if request.config_path is not None else None,
        "overrides": to_jsonable(request.overrides or {}),
        "resolved_config": request.config.to_redacted_dict(),
        "redaction": {
            "secrets_written": False,
            "redacted_fields": [f"env.{name}" for name in env_var_names],
            "env_var_names_recorded": env_var_names,
        },
        "workspace": {
            "run_path": str(workspace.run_path),
            "agent_workspace_path": str(workspace.agent_workspace_path),
            "framework_path": str(workspace.framework_path),
        },
        "rendered": {
            "startup_prompt_path": str(rendered_artifacts.get("startup_prompt")) if rendered_artifacts.get("startup_prompt") else None,
            "repair_prompt_paths": [str(path) for path in repair_prompt_paths],
        },
        "adapter": {
            "sdk": adapter_sdk,
            "sdk_version": adapter_sdk_version,
            "session_id": result.session_id,
        },
        "validation": result.validation.to_dict(),
        "repair_attempts": [attempt.to_dict() for attempt in result.repair_attempts],
        "usage": result.usage.to_dict() if result.usage is not None else None,
        "artifacts": {key: str(path) for key, path in result.artifacts.items()},
        "error": result.error,
    }
    write_json(workspace.run_record_path, record)


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
