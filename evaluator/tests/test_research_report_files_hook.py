from pathlib import Path

import pytest

from hooks import HookExecutor, ResearchReportFilesHook
from message_service.local_message_service import LocalMessageService
from message_service.message import MessageType
from runtime.rank_runner import _load_research_workspace_report, _run_tool_loop


def _text_response(text: str):
    return {"content": [{"type": "text", "text": text}]}


class _ReportWritingAgent:
    user_prompt = "Research this proposal"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.calls = 0

    async def call_llm(self, messages, use_tools=True):
        self.calls += 1
        if self.calls == 1:
            return _text_response("Here is my report, but I forgot to write files.")

        (self.workspace / "evidence_gaps.md").write_text("gaps", encoding="utf-8")
        (self.workspace / "targeted_findings.md").write_text("findings", encoding="utf-8")
        (self.workspace / "analysis_report.md").write_text("analysis", encoding="utf-8")
        (self.workspace / "revision_feedback.md").write_text("improvements", encoding="utf-8")
        return _text_response("Reports written to workspace.")


@pytest.mark.asyncio
async def test_research_report_hook_retries_text_only_response_until_files_exist(tmp_path):
    message_service = LocalMessageService(data_dir=str(tmp_path / "data"))
    task_id = await message_service.create_task(working_dir=str(tmp_path))
    hook_executor = HookExecutor()
    hook_executor.register(ResearchReportFilesHook(message_service=message_service, workspace_dir=tmp_path))
    agent = _ReportWritingAgent(tmp_path)

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

    assert result == "Reports written to workspace."
    assert agent.calls == 2

    messages = await message_service.fetch_in_context_messages(task_id, "research_agent_a")
    notifications = [
        msg for msg in messages
        if msg.msg_type == MessageType.NOTIFICATION
        and "evidence_gaps.md" in msg.content[0]["text"]
        and "targeted_findings.md" in msg.content[0]["text"]
        and "analysis_report.md" in msg.content[0]["text"]
        and "revision_feedback.md" in msg.content[0]["text"]
    ]
    assert len(notifications) == 1


def test_load_research_workspace_report_uses_only_judge_facing_analysis(tmp_path):
    (tmp_path / "evidence_gaps.md").write_text("gaps body", encoding="utf-8")
    (tmp_path / "targeted_findings.md").write_text("findings body", encoding="utf-8")
    (tmp_path / "analysis_report.md").write_text("analysis body", encoding="utf-8")
    (tmp_path / "revision_feedback.md").write_text("improvement body", encoding="utf-8")

    report = _load_research_workspace_report(tmp_path, fallback_text="chat-only paths")

    assert "analysis body" in report
    assert "improvement body" not in report
    assert "gaps body" not in report
    assert "findings body" not in report
    assert "chat-only paths" not in report
