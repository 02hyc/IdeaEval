"""Ensure literature review agent writes literature_review.md before finalizing."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from message_service.local_message_service import LocalMessageService
from message_service.message import MessageType, TaskMessage

from .base import Hook, HookContext


class LiteratureReviewFilesHook(Hook):
    """Ask the literature review agent to write literature_review.md when it tries to finish with text only."""

    REQUIRED_FILES = ("literature_review.md",)

    def __init__(self, message_service: LocalMessageService, workspace_dir: str | Path):
        self.message_service = message_service
        self.workspace_dir = Path(workspace_dir)

    @property
    def name(self) -> str:
        return "literature_review_files"

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

        notification = (
            "Your previous response only output text, but the literature review must be persisted "
            "in the workspace before finalizing. Missing required file: `literature_review.md`.\n\n"
            "Use the Write tool now to create `literature_review.md` (relative path). "
            "This file should be the comprehensive topic-level survey you have built.\n\n"
            "After writing the file, respond briefly with the path you wrote. "
            "Do not provide the full review only in chat."
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
