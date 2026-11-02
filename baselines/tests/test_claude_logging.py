from __future__ import annotations

import json
from types import SimpleNamespace

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from research_ideation_agent.adapters.claude import ClaudeAdapter


def test_claude_event_log_records_full_message(tmp_path) -> None:
    context = _context(tmp_path)
    adapter = ClaudeAdapter()

    adapter._log_event(
        context,  # type: ignore[arg-type]
        AssistantMessage(
            content=[
                TextBlock(text="I will search for goal misgeneralization papers."),
                ToolUseBlock(
                    id="tool-1",
                    name="mcp__scholar-gateway__web_search",
                    input={
                        "query": "goal misgeneralization language model agents",
                        "content": "x" * 2000,
                    },
                ),
            ],
            model="claude-opus-4-7",
        ),
        phase="initial",
    )
    adapter._log_event(
        context,  # type: ignore[arg-type]
        UserMessage(content=[ToolResultBlock(tool_use_id="tool-1", content="Found paper metadata.")]),
        phase="initial",
    )

    lines = _read_jsonl(tmp_path / "adapter_events.jsonl")
    assistant = lines[0]
    result = lines[1]

    assert assistant["event_index"] == 1
    assert assistant["phase"] == "initial"
    assert assistant["type"] == "AssistantMessage"
    blocks = assistant["message"]["content"]
    assert blocks[0]["text"] == "I will search for goal misgeneralization papers."
    assert blocks[1]["name"] == "mcp__scholar-gateway__web_search"
    assert blocks[1]["input"]["content"] == "x" * 2000

    assert result["event_index"] == 2
    assert result["message"]["content"][0]["tool_use_id"] == "tool-1"
    assert result["message"]["content"][0]["content"] == "Found paper metadata."


def test_claude_event_log_preserves_thinking_content(tmp_path) -> None:
    context = _context(tmp_path)
    adapter = ClaudeAdapter()

    thinking_text = "Let me reason about this step by step..." * 100

    adapter._log_event(
        context,  # type: ignore[arg-type]
        AssistantMessage(
            content=[ThinkingBlock(thinking=thinking_text, signature="sig")],
            model="claude-opus-4-7",
        ),
        phase="initial",
    )

    [event] = _read_jsonl(tmp_path / "adapter_events.jsonl")
    assert event["message"]["content"][0]["thinking"] == thinking_text


def test_claude_event_log_serializes_non_dataclass_object(tmp_path) -> None:
    """Covers the `vars()` fallback in `event_to_jsonable` for objects that are
    neither dataclasses nor pydantic-style models."""

    class _Custom:
        def __init__(self) -> None:
            self.field = "value"
            self._private = "ignored"

    context = _context(tmp_path)
    adapter = ClaudeAdapter()
    adapter._log_event(context, _Custom(), phase="initial")  # type: ignore[arg-type]

    [event] = _read_jsonl(tmp_path / "adapter_events.jsonl")
    assert event["message"] == {"field": "value"}


def _context(tmp_path):
    config = SimpleNamespace(
        logging=SimpleNamespace(),
    )
    return SimpleNamespace(
        event_log_path=tmp_path / "adapter_events.jsonl",
        config=config,
        env={},
    )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
