"""Adapter factory."""

from __future__ import annotations

from research_ideation_agent.adapters.base import AgentAdapter
from research_ideation_agent.adapters.claude import ClaudeAdapter
from research_ideation_agent.adapters.codex import CodexAdapter
from research_ideation_agent.models import Harness


def get_adapter(harness: Harness) -> AgentAdapter:
    if harness == "claude":
        return ClaudeAdapter()
    if harness == "codex":
        return CodexAdapter()
    raise ValueError(f"Unsupported harness: {harness}")
