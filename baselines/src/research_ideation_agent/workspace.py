"""Workspace creation and filesystem layout helpers."""

from __future__ import annotations

import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from research_ideation_agent.errors import WorkspaceError
from research_ideation_agent.models import RunConfig


@dataclass(frozen=True)
class RunWorkspace:
    run_id: str
    created_at: datetime
    run_path: Path
    agent_workspace_path: Path
    framework_path: Path
    input_path: Path
    mcp_path: Path
    logs_path: Path
    validation_path: Path
    run_record_path: Path
    proposal_path: Path


def create_workspace(config: RunConfig) -> RunWorkspace:
    """Create the per-run workspace and framework-owned directories."""

    run_id = config.workspace.run_id or _generate_run_id()
    _validate_run_id(run_id)
    root = config.workspace.root
    run_path = root / model_subdir(config) / run_id
    try:
        if run_path.exists():
            if not config.workspace.clean_existing:
                raise WorkspaceError(f"Run workspace already exists: {run_path}")
            shutil.rmtree(run_path)
        agent_workspace_path = run_path / config.workspace.agent_dir
        framework_path = run_path / "framework"
        input_path = framework_path / "input"
        mcp_path = framework_path / "mcp"
        logs_path = framework_path / "logs"
        for directory in (agent_workspace_path, input_path, mcp_path, logs_path):
            directory.mkdir(parents=True, exist_ok=False)
        references_dir = agent_workspace_path / config.mcp.scholar_gateway.references_dir
        references_dir.mkdir(parents=True, exist_ok=True)
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceError(f"Could not create run workspace: {run_path}") from exc

    return RunWorkspace(
        run_id=run_id,
        created_at=datetime.now(UTC),
        run_path=run_path,
        agent_workspace_path=agent_workspace_path,
        framework_path=framework_path,
        input_path=input_path,
        mcp_path=mcp_path,
        logs_path=logs_path,
        validation_path=framework_path / "validation.json",
        run_record_path=run_path / "run_record.json",
        proposal_path=agent_workspace_path / "proposal.md",
    )


def model_subdir(config: RunConfig) -> str:
    """Directory grouping runs by model profile: ``<root>/<model-profile>/<run-id>``.

    Prefers the named model profile (set by ``--model-profile``), falling back to
    the raw model id and then the harness, sanitized into one path-safe component
    (profile/model ids may contain ``/`` etc.).
    """
    raw = (getattr(config, "model_profile", None) or config.model or "").strip() or config.harness
    return _sanitize_component(raw)


def _sanitize_component(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name).strip()).strip("-.")
    return safe or "unknown-model"


def _generate_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def _validate_run_id(run_id: str) -> None:
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise WorkspaceError("workspace.run_id must be a single path-safe name.")
