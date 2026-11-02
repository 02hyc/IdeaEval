"""Hook for recovering from content-filtered LLM outputs."""

from __future__ import annotations

from collections import defaultdict

from message_service.message import MessageType, TaskMessage
from utils.logger import Logger

from .base import Hook, HookContext


_RECOVERY_PROMPT = """\
Your previous response was blocked by a content filter.

Continue the same task, but rewrite safely:
- Use neutral, concise academic language.
- Do not quote or reproduce blocked text verbatim.
- Summarize sensitive or potentially unsafe content at a high level.
- Focus on research evidence, limitations, and conclusions needed for the report.
- If a specific detail may have triggered the filter, generalize it instead of repeating it.
"""

_STRICT_RECOVERY_PROMPT = """\
Your previous response was still blocked by a content filter.

Continue the same task with stricter safety constraints:
- Discuss security, attack, poisoning, jailbreak, or misuse-related work only as high-level research context.
- Do not include procedural steps, operational details, exploit recipes, payloads, optimization recipes, or implementation guidance.
- Prefer abstract phrases such as "the paper studies a vulnerability class" or "the method evaluates robustness" instead of describing how to execute an attack.
- If a source is sensitive, record only its bibliographic role, empirical claim, and relevance to the proposal.
- Write the required report files using concise, non-operational academic summaries.
"""


class ContentFilterRecoveryHook(Hook):
    """Persist a sanitized retry instruction after a content-filter block."""

    def __init__(self, message_service):
        self.message_service = message_service
        self.logger = Logger()
        self._attempts = defaultdict(int)

    @property
    def name(self) -> str:
        return "content_filter_recovery"

    @property
    def priority(self) -> int:
        return 20

    async def on_content_filter(self, context: HookContext, error: Exception) -> bool:
        key = (context.task_id, context.agent_name)
        self._attempts[key] += 1
        prompt = _RECOVERY_PROMPT if self._attempts[key] == 1 else _STRICT_RECOVERY_PROMPT
        message = TaskMessage(
            task_id=context.task_id,
            agent_name=context.agent_name,
            role="user",
            content=[{"type": "text", "text": prompt}],
            msg_type=MessageType.NOTIFICATION,
            content_extend={
                "content_filter_recovery": {
                    "attempt": self._attempts[key],
                    "error": str(error)[:500],
                }
            },
        )
        await self.message_service.on_receive_user_message(
            task_id=context.task_id,
            message=message,
            agent_name=context.agent_name,
        )
        self.logger.warning(
            f"[ContentFilterRecovery] Added sanitized retry notification "
            f"attempt={self._attempts[key]} for {context.agent_name}."
        )
        return True
