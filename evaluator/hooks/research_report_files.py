"""Ensure research agents write required report files before finalizing."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from message_service.local_message_service import LocalMessageService
from message_service.message import MessageType, TaskMessage

from .base import Hook, HookContext


class ResearchReportFilesHook(Hook):
    """Ask research agents to write report files when they try to finish with text only."""

    REQUIRED_FILES = (
        "evidence_gaps.md",
        "targeted_findings.md",
        "analysis_report.md",
        "revision_feedback.md",
    )

    def __init__(self, message_service: LocalMessageService, workspace_dir: str | Path):
        self.message_service = message_service
        self.workspace_dir = Path(workspace_dir)

    @property
    def name(self) -> str:
        return "research_report_files"

    @property
    def priority(self) -> int:
        return 30

    async def on_text_response_without_tools(
        self,
        context: HookContext,
        response: Optional[Dict[str, Any]],
    ) -> bool:
        missing = [
            filename
            for filename in self.REQUIRED_FILES
            if not (self.workspace_dir / filename).is_file()
        ]
        if not missing:
            return False

        missing_list = ", ".join(f"`{name}`" for name in missing)
        notification = (
            "Your previous response only output text, but the research artifacts must be persisted "
            f"in the workspace before finalizing. Missing required file(s): {missing_list}.\n\n"
            "Use the Write tool now to create these files (relative paths):\n"
            "- `evidence_gaps.md`: proposal-specific evidence gaps and targeted-search plan; "
            "each gap must say why `literature_review.md` is insufficient and what "
            "`analysis_report.md` section could change if the gap is resolved.\n"
            "- `targeted_findings.md`: running targeted-search log, initialized before "
            "targeted web search/scraping and updated after each search/read cycle with "
            "sources read, key findings, updates relative to `literature_review.md`, and "
            "remaining uncertainty.\n"
            "- `analysis_report.md`: judge-facing citation / Delta-K evidence dossier only; "
            "no factor ratings, overall potential, bottleneck, or revision advice.\n"
            "- `revision_feedback.md`: generator-facing revision spec with Preserve, "
            "Required Changes, Do Not Do, and Acceptance Checklist.\n\n"
            "After writing the missing files, respond briefly with the paths you wrote. "
            "Do not provide the full report only in chat."
        )
        message = TaskMessage(
            task_id=context.task_id,
            agent_name=context.agent_name,
            role="user",
            content=[{"type": "text", "text": notification}],
            msg_type=MessageType.NOTIFICATION,
        )
        await self.message_service.on_receive_user_message(
            task_id=context.task_id,
            message=message,
            agent_name=context.agent_name,
        )
        return True
