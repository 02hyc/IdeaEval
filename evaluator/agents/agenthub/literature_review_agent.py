"""Literature Review Agent: builds a comprehensive topic-level survey."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.llm.client_factory import create_llm_client
from agents.tools.tool_registry import get_all_claude_tools
from agents.utils.skill_utils import get_available_skills_prompt


AGENT_NAME = "literature_review_agent"

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Bash family is excluded so the model cannot bypass WebSearch/Scraper by
# running raw `python -c "import requests; ..."` against arxiv/DOI endpoints.
# That bypass skips cutoff-date filtering, the WebFetch URL allowlist, and
# Scraper's auto-summarize + scraped_papers/paper_summaries cache that
# downstream research agents rely on.
_EXCLUDED_TOOL_DIRS = [
    "bash_tool",
    "bash_output_tool",
    "kill_bash_tool",
    "ls_bash_tool",
]


def _load_base_system_prompt(workspace_dir: str) -> str:
    main = (PROMPTS_DIR / "sys_literature_review_agent.md").read_text(encoding="utf-8")
    skills_instructions = (PROMPTS_DIR / "sys_literature_review_agent_skills.md").read_text(encoding="utf-8")
    skills_metadata = get_available_skills_prompt(root_path=workspace_dir)
    skills_instructions = skills_instructions.replace(
        "<available_skills></available_skills>", skills_metadata
    )
    return main + "\n\n" + skills_instructions


def _build_system_prompt(topic_label: str, topic_description: str, cutoff_date: str, workspace_dir: str) -> str:
    base = _load_base_system_prompt(workspace_dir)
    base = base.replace("{{cutoff_date}}", cutoff_date)
    base = base.replace("{{topic_label}}", topic_label)
    base = base.replace("{{topic_description}}", topic_description)
    return base


def _build_user_prompt() -> str:
    return "Now start your research."


class LiteratureReviewAgent:
    """Wrapper around LLM client for the literature review agent."""

    def __init__(self, topic_label: str, topic_description: str, cutoff_date: str, workspace_dir: str):
        self.topic_label = topic_label
        self.topic_description = topic_description
        self.agent_name = AGENT_NAME
        self.cutoff_date = cutoff_date
        self.workspace_dir = workspace_dir
        self.llm = create_llm_client("literature_review_agent")
        self._system_prompt = _build_system_prompt(topic_label, topic_description, cutoff_date, workspace_dir)
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
