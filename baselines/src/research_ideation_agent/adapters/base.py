"""Shared adapter protocol and result normalization."""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from pathlib import Path

from research_ideation_agent.mcp import MCPLaunchConfig
from research_ideation_agent.models import RunConfig, UsageSummary
from research_ideation_agent.workspace import RunWorkspace


@dataclass(frozen=True)
class AdapterContext:
    config: RunConfig
    workspace: RunWorkspace
    startup_prompt: str
    mcp: MCPLaunchConfig
    env: dict[str, str]
    event_log_path: Path | None
    # Monotonic deadline (time.monotonic()) for the entire run, or None when no
    # framework timeout is configured. Adapters that can cooperatively time-out
    # (e.g. inside an asyncio loop) should honor this; the runtime's SIGALRM
    # backstop still applies when an adapter does not.
    deadline: float | None = None


@dataclass(frozen=True)
class AdapterOutcome:
    session_id: str | None
    model: str | None
    usage: UsageSummary | None
    error: str | None = None
    continued_session: bool | None = None
    continuation_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class AgentAdapter:
    sdk_name = "unknown"
    # Distribution name on PyPI (the project name in `pyproject.toml`), used
    # to look up the installed version via importlib.metadata. Subclasses set
    # it; None means "no recorded version".
    sdk_distribution_name: str | None = None

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        raise NotImplementedError

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise NotImplementedError

    def close(self) -> None:
        return None

    @property
    def sdk_version(self) -> str | None:
        if self.sdk_distribution_name is None:
            return None
        try:
            return importlib.metadata.version(self.sdk_distribution_name)
        except importlib.metadata.PackageNotFoundError:
            return None
