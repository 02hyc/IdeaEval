"""Research Agent: investigates prior art for one proposal."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.llm.client_factory import create_llm_client
from agents.tools.tool_registry import get_all_claude_tools
from agents.utils.skill_utils import get_available_skills_prompt


AGENT_NAME_PREFIX = "research_agent"

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Bash family is excluded so the model cannot bypass WebSearch/Scraper by
# running raw `python -c "import requests; ..."` against arxiv/DOI endpoints.
# That bypass skips cutoff-date filtering, the WebFetch URL allowlist, and
# Scraper's auto-summarize + scraped_papers/paper_summaries cache.
_EXCLUDED_TOOL_DIRS = [
    "bash_tool",
    "bash_output_tool",
    "kill_bash_tool",
    "ls_bash_tool",
]


def _load_base_system_prompt(workspace_dir: str) -> str:
    main = (PROMPTS_DIR / "sys_research_agent.md").read_text(encoding="utf-8")
    skills_instructions = (PROMPTS_DIR / "sys_research_agent_skills.md").read_text(encoding="utf-8")
    skills_metadata = get_available_skills_prompt(root_path=workspace_dir)
    skills_instructions = skills_instructions.replace(
        "<available_skills></available_skills>", skills_metadata
    )
    return main + "\n\n" + skills_instructions


def _build_system_prompt(cutoff_date: str, workspace_dir: str) -> str:
    base = _load_base_system_prompt(workspace_dir)
    base = base.replace("{{cutoff_date}}", cutoff_date)
    return base


def _build_user_prompt() -> str:
    return "Now start your research."


class ResearchAgent:
    """Wrapper around LLM client for the research agent."""

    def __init__(self, role: str, cutoff_date: str, workspace_dir: str):
        self.role = role
        self.label = "A" if role == "a" else "B"
        self.agent_name = f"{AGENT_NAME_PREFIX}_{role}"
        self.cutoff_date = cutoff_date
        self.workspace_dir = workspace_dir
        self.llm = create_llm_client("research_agent")
        self._system_prompt = _build_system_prompt(cutoff_date, workspace_dir)
        self._user_prompt = _build_user_prompt()
        self._tools = get_all_claude_tools(exclude=_EXCLUDED_TOOL_DIRS)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def user_prompt(self) -> str:
        return self._user_prompt

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    async def call_llm(self, messages: list[dict[str, Any]], use_tools: bool = True) -> dict[str, Any]:
        """Make a single LLM call. Returns the raw response dict."""
        tools = self._tools if use_tools else None
        response = await self.llm.call_llm(
            messages=messages,
            system_prompt=self._system_prompt,
            tools=tools,
            use_cache=True,
        )
        return response
