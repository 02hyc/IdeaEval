import tempfile
from pathlib import Path

import pytest

from agents.utils.exceptions import ContentPolicyViolationError
from hooks import ContentFilterRecoveryHook, HookExecutor
from message_service.local_message_service import LocalMessageService
from message_service.message import MessageType
from runtime.rank_runner import _run_tool_loop


class _FakeAgent:
    user_prompt = "Initial task"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def call_llm(self, messages, use_tools=True):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _text_response(text: str):
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.asyncio
async def test_content_filter_recovery_retries_once():
    with tempfile.TemporaryDirectory(prefix="content_filter_recovery_") as tmp:
        message_service = LocalMessageService(data_dir=str(Path(tmp) / "data"))
        task_id = await message_service.create_task(working_dir=tmp)
        hook_executor = HookExecutor()
        hook_executor.register(ContentFilterRecoveryHook(message_service))
        agent = _FakeAgent([
            ContentPolicyViolationError("blocked"),
            _text_response("recovered report"),
        ])

        result = await _run_tool_loop(
            agent=agent,
            toolkit=None,
            max_tool_calls_per_turn=0,
            message_service=message_service,
            task_id=task_id,
            agent_name="research_agent_a",
            system_prompt="system",
            tools=[],
            hook_executor=hook_executor,
        )

        assert result == "recovered report"
        assert agent.calls == 2
        messages = await message_service.fetch_in_context_messages(task_id, "research_agent_a")
        recovery_messages = [
            msg for msg in messages
            if msg.msg_type == MessageType.NOTIFICATION
            and "content filter" in msg.content[0]["text"].lower()
        ]
        assert len(recovery_messages) == 1


@pytest.mark.asyncio
async def test_content_filter_recovery_only_retries_once(monkeypatch):
    monkeypatch.setenv("RANKER_CONTENT_FILTER_RECOVERY_ATTEMPTS", "1")
    with tempfile.TemporaryDirectory(prefix="content_filter_recovery_") as tmp:
        message_service = LocalMessageService(data_dir=str(Path(tmp) / "data"))
        task_id = await message_service.create_task(working_dir=tmp)
        hook_executor = HookExecutor()
        hook_executor.register(ContentFilterRecoveryHook(message_service))
        agent = _FakeAgent([
            ContentPolicyViolationError("blocked once"),
            ContentPolicyViolationError("blocked twice"),
        ])

        with pytest.raises(ContentPolicyViolationError):
            await _run_tool_loop(
                agent=agent,
                toolkit=None,
                max_tool_calls_per_turn=0,
                message_service=message_service,
                task_id=task_id,
                agent_name="research_agent_a",
                system_prompt="system",
                tools=[],
                hook_executor=hook_executor,
            )

        assert agent.calls == 2
