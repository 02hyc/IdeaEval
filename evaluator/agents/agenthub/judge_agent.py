"""Judge Agent: compares two proposals using literature context and local paper archives."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.llm.client_factory import create_llm_client
from agents.tools.tool_registry import get_all_claude_tools


AGENT_NAME = "judge_agent"

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Judge is a read-and-report agent. External research tools are disabled; it
# should ground itself in the local workspace: literature_review.md, the two
# proposals, and the literature agent's local paper archive copied into
# paper_summaries/ and scraped_papers/. This ReAct-style flow lets the model
# inspect named prior work from the literature review instead of relying on a
# pre-digested research report. Large archives may be symlinked into the
# workspace rather than physically copied.
_EXCLUDED_TOOL_DIRS = [
    "web_search_tool",
    "web_fetch_tool",
    "scraper_tool",
]


def _load_base_system_prompt() -> str:
    return (PROMPTS_DIR / "sys_judge_agent.md").read_text(encoding="utf-8")


def _build_system_prompt(cutoff_date: str, workspace_dir: str) -> str:
    base = _load_base_system_prompt()
    base = base.replace("{{cutoff_date}}", cutoff_date)
    return base


def _build_user_prompt(
    proposal_a: str,
    proposal_b: str,
    report_a: str,
    report_b: str,
    literature_review: str,
) -> str:
    """Workspace-oriented user prompt: literature, proposals, and reports live in files on
    disk, not inline. Caller (rank_runner._run_judge) is responsible for
    materialising the files in the judge workspace BEFORE the agent loop starts.
    The content arguments are accepted so the function signature stays stable for
    callers, but their content is no longer embedded in the prompt — the
    workspace is the source of truth."""

    return (
        "## Workspace\n\n"
        "Your workspace contains these comparison inputs:\n"
        "- `literature_review.md` — broad field review and shared landscape context\n"
        "- `proposal_a.md` — the full text of Proposal A\n"
        "- `proposal_b.md` — the full text of Proposal B\n"
        "- `paper_summaries/` — paper summaries generated during literature review\n"
        "- `scraped_papers/` — locally scraped paper sections from literature review\n"
        "- optional `key_findings.md`, `coverage_gaps.md`, and `research_plan.md` from the literature-review workspace\n\n"
        "Path discipline: the current working directory is already this workspace. "
        "Use relative paths exactly as listed above, such as `literature_review.md`, "
        "`proposal_a.md`, `paper_summaries/`, and `scraped_papers/`. Do not use "
        "`/workspace`. Do not run Glob/Grep/View from `/`; if you need discovery, "
        "scope it to `.` or a specific local subdirectory.\n\n"
        "External research tools (WebSearch, WebFetch, Scraper, SummarizePaper, AnnaResearch) "
        "are DISABLED. All reasoning must be grounded in these local workspace files. "
        "You have View/Write/Edit/Bash/Glob/Grep/TodoWrite — use them to re-read evidence "
        "actively and to inspect named prior work from the literature review when needed.\n\n"
        "## Required workflow\n\n"
        "1. **Read the core inputs**: View `literature_review.md`, `proposal_a.md`, "
        "and `proposal_b.md`.\n\n"
        "2. **Audit each central claim before choosing a winner**: For Proposal A and "
        "Proposal B, identify the central claim, claimed scope, mechanism, required "
        "signals/assumptions, minimum evidence needed, whether the proposed experiments "
        "directly verify that claim, and any thesis-threatening method/evidence risks. "
        "If a proposal makes a broad or unified claim, check whether its mechanism and "
        "evidence plan actually support that breadth; if not, shrink the credited claim "
        "scope or record a severe gate.\n\n"
        "3. **Inspect relevant prior work locally**: When the literature review names "
        "a paper/system/benchmark that may preempt, cover, or materially contextualize "
        "either proposal's central contribution, use Grep/Glob/View to open the "
        "corresponding file in `paper_summaries/` and, if needed, relevant sections in "
        "`scraped_papers/`. Do not read every paper mechanically; inspect the few papers "
        "that can change the pairwise judgment. Treat named local archive papers as "
        "prior art/context, not as the proposal itself; do not create a same-work-family "
        "exemption for near-exact coverage.\n\n"
        "4. **Write one judge report**: Before final output, write `judge_report.md` in "
        "the workspace. It should include the shared field baseline, Proposal A and B "
        "positioning summaries, a central-claim contract audit for both proposals, "
        "pairwise comparisons for Potential Impact "
        "(`problem_importance`, `novelty_and_contribution`, `generality_reference_value`) and "
        "Feasibility (`method_plausibility`, `experimental_sufficiency`, "
        "`execution_publication_credibility`), a prior-art coverage check, a disqualifying gate check, and "
        "a multiplicative synthesis comparing Potential Impact x Feasibility.\n\n"
        "5. **Final output**: After `judge_report.md` is written, emit the final JSON "
        "specified in your system instructions. The JSON factor results and final "
        "verdict must match `judge_report.md`.\n\n"
        "## Output\n\n"
        "Emit ONLY the JSON comparison result as specified in your system instructions — "
        "no other text, no markdown fences. The detailed reasoning belongs in "
        "`judge_report.md`, not in chat outside the JSON fields."
    )


class JudgeAgent:
    """Wrapper around LLM client for the judge agent."""

    def __init__(self, cutoff_date: str, workspace_dir: str):
        self.agent_name = AGENT_NAME
        self.cutoff_date = cutoff_date
        self.workspace_dir = workspace_dir
        self.llm = create_llm_client("judge_agent")
        self._system_prompt = _build_system_prompt(cutoff_date, workspace_dir)
        self._tools = get_all_claude_tools(exclude=_EXCLUDED_TOOL_DIRS)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    def build_user_prompt(
        self,
        proposal_a: str,
        proposal_b: str,
        report_a: str,
        report_b: str,
        literature_review: str,
    ) -> str:
        return _build_user_prompt(proposal_a, proposal_b, report_a, report_b, literature_review)

    async def call_llm(self, messages: list[dict[str, Any]], use_tools: bool = True) -> dict[str, Any]:
        """Make a single LLM call."""
        tools = self._tools if use_tools else None
        response = await self.llm.call_llm(
            messages=messages,
            system_prompt=self._system_prompt,
            tools=tools,
            use_cache=True,
        )
        return response
