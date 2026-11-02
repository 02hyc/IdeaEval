"""Tests for LLMOpenAI previous_response_id chain management.

Verifies:
- Incremental vs full-history mode selection
- Chain reset on compaction (message count shrinks)
- Chain failure fallback (previous_response_id rejected by server)
- Reasoning blocks converted to summary text in full-history mode
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure dummy credentials so config loading doesn't fail.
_DUMMY_CREDS = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_REGION": "us-east-1",
    "OPENAI_API_KEY": "test-key-123",
    "OPENAI_BASE_URL": "https://test.example.com/v1",
}
for k, v in _DUMMY_CREDS.items():
    os.environ.setdefault(k, v)

from agents.config import AgentConfig, OpenAIProviderConfig


def _make_client():
    """Create an LLMOpenAI instance with mocked AsyncOpenAI client."""
    from agents.llm.openai import LLMOpenAI

    provider = OpenAIProviderConfig(
        type="openai",
        api_key="test-key-123",
        base_url="https://test.example.com/v1",
        num_retries=2,
        retry_multiplier=1.0,
        retry_start_wait=0.1,
    )
    agent_cfg = AgentConfig(
        model="gpt-5.2-proxy",
        max_tokens=1000,
        max_context_tokens=100000,
        temperature=1.0,
        enable_thinking=True,
        thinking_budget_tokens=10000,
    )
    client = LLMOpenAI(provider, agent_cfg, "gpt-5.2")
    return client


def _make_response(response_id="resp_001", text="Hello", reasoning=True):
    """Build a mock response object matching OpenAI Responses API shape."""
    output_text = MagicMock()
    output_text.type = "output_text"
    output_text.text = text

    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [output_text]

    output_items = [message_item]

    if reasoning:
        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_item.id = "rs_001"
        reasoning_item.encrypted_content = "enc_abc123"
        summary_block = MagicMock()
        summary_block.type = "summary_text"
        summary_block.text = "I thought about the problem."
        reasoning_item.summary = [summary_block]
        output_items.insert(0, reasoning_item)

    response = MagicMock()
    response.id = response_id
    response.output = output_items
    response.usage = MagicMock()
    response.usage.model_dump.return_value = {
        "input_tokens": 100,
        "output_tokens": 50,
        "input_tokens_details": {"cached_tokens": 0},
    }
    response.status = "completed"
    return response


def _simple_messages(n):
    """Create n simple user/assistant message pairs."""
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"Message {i}"})
        msgs.append({
            "role": "assistant",
            "content": [{"type": "text", "text": f"Reply {i}"}],
        })
    return msgs


class TestChainStateManagement:
    """Test that _last_response_id and _last_message_count are managed correctly."""

    def test_initial_state(self):
        client = _make_client()
        assert client._last_response_id is None
        assert client._last_message_count == 0

    @pytest.mark.asyncio
    async def test_first_call_uses_full_history(self):
        """First call should NOT send previous_response_id."""
        client = _make_client()
        mock_response = _make_response("resp_first")
        client.client.responses.create = AsyncMock(return_value=mock_response)

        messages = [{"role": "user", "content": "Hello"}]
        await client.call_llm(messages, "system prompt", tools=None)

        call_kwargs = client.client.responses.create.call_args[1]
        assert "previous_response_id" not in call_kwargs
        assert call_kwargs["store"] is True
        assert client._last_response_id == "resp_first"
        assert client._last_message_count == 1

    @pytest.mark.asyncio
    async def test_second_call_uses_incremental(self):
        """Second call should send previous_response_id with only new messages."""
        client = _make_client()
        mock_resp_1 = _make_response("resp_001")
        mock_resp_2 = _make_response("resp_002")
        client.client.responses.create = AsyncMock(
            side_effect=[mock_resp_1, mock_resp_2]
        )

        msgs_turn1 = [{"role": "user", "content": "Hello"}]
        await client.call_llm(msgs_turn1, "system", tools=None)

        msgs_turn2 = msgs_turn1 + [
            {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            {"role": "user", "content": "Follow up"},
        ]
        await client.call_llm(msgs_turn2, "system", tools=None)

        call_kwargs = client.client.responses.create.call_args[1]
        assert call_kwargs["previous_response_id"] == "resp_001"
        assert client._last_response_id == "resp_002"
        assert client._last_message_count == len(msgs_turn2)

    @pytest.mark.asyncio
    async def test_compaction_resets_chain(self):
        """When message count shrinks (compaction), chain should reset."""
        client = _make_client()
        mock_resp_1 = _make_response("resp_001")
        mock_resp_2 = _make_response("resp_002")
        client.client.responses.create = AsyncMock(
            side_effect=[mock_resp_1, mock_resp_2]
        )

        msgs_long = _simple_messages(10)  # 20 messages
        await client.call_llm(msgs_long, "system", tools=None)
        assert client._last_response_id == "resp_001"
        assert client._last_message_count == 20

        # Simulate compaction: fewer messages
        msgs_short = _simple_messages(3)  # 6 messages
        await client.call_llm(msgs_short, "system", tools=None)

        call_kwargs = client.client.responses.create.call_args[1]
        assert "previous_response_id" not in call_kwargs
        assert client._last_response_id == "resp_002"
        assert client._last_message_count == 6


class TestRequestTimeoutRetry:
    """Test bounded wall-time request timeouts are retried by tenacity."""

    @pytest.mark.asyncio
    async def test_wall_timeout_is_retried(self):
        from agents.llm.openai import LLMRequestTimeoutError

        client = _make_client()
        mock_response = _make_response("resp_after_timeout")
        client.client.responses.create = AsyncMock(
            side_effect=[
                LLMRequestTimeoutError("Responses API request exceeded 1s wall timeout"),
                mock_response,
            ]
        )

        messages = [{"role": "user", "content": "Hello"}]
        await client.call_llm(messages, "system prompt", tools=None)

        assert client.client.responses.create.call_count == 2
        assert client._last_response_id == "resp_after_timeout"


class TestReasoningBlockConversion:
    """Test that reasoning blocks are handled correctly in full-history mode."""

    def test_reasoning_converted_to_summary_text(self):
        """In full-history mode, reasoning blocks become plain assistant text."""
        client = _make_client()
        # Force full-history mode
        assert client._last_response_id is None

        blocks = [
            {"type": "reasoning", "id": "rs_001",
             "encrypted_content": "enc_abc",
             "summary": [{"type": "summary_text", "text": "Thought about X."}]},
            {"type": "text", "text": "The answer is 42."},
        ]
        out: list = []
        client._convert_assistant_blocks(blocks, out)

        # Should NOT have any type:"reasoning" items
        reasoning_items = [i for i in out if isinstance(i, dict) and i.get("type") == "reasoning"]
        assert len(reasoning_items) == 0

        # Summary should appear as assistant text
        text_items = [i for i in out if isinstance(i, dict) and i.get("type") == "message"]
        texts = " ".join(i.get("content", "") for i in text_items)
        assert "Thought about X." in texts
        assert "The answer is 42." in texts

    def test_reasoning_without_summary_is_skipped(self):
        """Reasoning blocks with no summary are silently dropped."""
        client = _make_client()
        blocks = [
            {"type": "reasoning", "id": "rs_002",
             "encrypted_content": "enc_xyz", "summary": []},
            {"type": "text", "text": "Some text."},
        ]
        out: list = []
        client._convert_assistant_blocks(blocks, out)

        reasoning_items = [i for i in out if isinstance(i, dict) and i.get("type") == "reasoning"]
        assert len(reasoning_items) == 0
        assert any("Some text." in str(i) for i in out)


class TestNoEncryptedContentSent:
    """Verify encrypted_content is never sent in API requests."""

    @pytest.mark.asyncio
    async def test_no_include_param(self):
        """The 'include' parameter should not be in API calls."""
        client = _make_client()
        mock_response = _make_response("resp_001")
        client.client.responses.create = AsyncMock(return_value=mock_response)

        await client.call_llm(
            [{"role": "user", "content": "Hi"}], "system", tools=None
        )

        call_kwargs = client.client.responses.create.call_args[1]
        assert "include" not in call_kwargs

    @pytest.mark.asyncio
    async def test_no_reasoning_items_in_full_history_input(self):
        """Full-history mode should not emit reasoning items in input."""
        client = _make_client()
        mock_response = _make_response("resp_001")
        client.client.responses.create = AsyncMock(return_value=mock_response)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "reasoning", "id": "rs_x",
                 "encrypted_content": "enc_secret",
                 "summary": [{"type": "summary_text", "text": "Thinking..."}]},
                {"type": "text", "text": "Response"},
            ]},
            {"role": "user", "content": "Follow up"},
        ]
        await client.call_llm(messages, "system", tools=None)

        call_kwargs = client.client.responses.create.call_args[1]
        input_items = call_kwargs["input"]
        reasoning_in_input = [
            i for i in input_items
            if isinstance(i, dict) and i.get("type") == "reasoning"
        ]
        assert len(reasoning_in_input) == 0


class TestChainFailureFallback:
    """Test that previous_response_id errors trigger fallback to full-history."""

    @pytest.mark.asyncio
    async def test_bad_previous_response_id_falls_back(self):
        """If server rejects previous_response_id, reset and retry with full history."""
        import openai as openai_mod

        client = _make_client()
        # Simulate established chain
        client._last_response_id = "resp_expired"
        client._last_message_count = 1

        error_body = {
            "error": {
                "message": "Invalid previous_response_id: response not found",
                "type": "invalid_request_error",
            }
        }
        bad_request = openai_mod.BadRequestError(
            message="Invalid previous_response_id: response not found",
            response=MagicMock(status_code=400, headers={}),
            body=error_body,
        )
        mock_success = _make_response("resp_new")

        client.client.responses.create = AsyncMock(
            side_effect=[bad_request, mock_success]
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Follow up"},
        ]
        result = await client.call_llm(messages, "system", tools=None)

        # Should have recovered
        assert result is not None
        assert client._last_response_id == "resp_new"
        assert client._last_message_count == 2

        # Second call should NOT have previous_response_id
        second_call_kwargs = client.client.responses.create.call_args_list[1][1]
        assert "previous_response_id" not in second_call_kwargs

    @pytest.mark.asyncio
    async def test_previous_response_not_found_falls_back(self):
        """SiliconFlow returns 'Previous response with id ... not found.' — must also trigger fallback."""
        import openai as openai_mod

        client = _make_client()
        client._last_response_id = "resp_0c379b5326fbd5da"
        client._last_message_count = 1

        error_body = {
            "error": {
                "message": "Previous response with id 'resp_0c379b5326fbd5da' not found.",
                "code": "channel_error",
                "type": "invalid_request_error",
            }
        }
        bad_request = openai_mod.BadRequestError(
            message="Previous response with id 'resp_0c379b5326fbd5da' not found.",
            response=MagicMock(status_code=400, headers={}),
            body=error_body,
        )
        mock_success = _make_response("resp_recovered")

        client.client.responses.create = AsyncMock(
            side_effect=[bad_request, mock_success]
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Follow up"},
        ]
        result = await client.call_llm(messages, "system", tools=None)

        assert result is not None
        assert client._last_response_id == "resp_recovered"
        assert client._last_message_count == 2

        second_call_kwargs = client.client.responses.create.call_args_list[1][1]
        assert "previous_response_id" not in second_call_kwargs

    @pytest.mark.asyncio
    async def test_no_tool_call_found_falls_back(self):
        """SiliconFlow 'No tool call found for function call output with call_id' triggers fallback."""
        import openai as openai_mod

        client = _make_client()
        client._last_response_id = "resp_abc123"
        client._last_message_count = 3

        error_body = {
            "error": {
                "message": "No tool call found for function call output with call_id call_USCaabJ6O9RBvkJudMfx11LV.",
                "code": "channel_error",
                "type": "invalid_request_error",
            }
        }
        bad_request = openai_mod.BadRequestError(
            message="No tool call found for function call output with call_id call_USCaabJ6O9RBvkJudMfx11LV.",
            response=MagicMock(status_code=400, headers={}),
            body=error_body,
        )
        mock_success = _make_response("resp_recovered")

        client.client.responses.create = AsyncMock(
            side_effect=[bad_request, mock_success]
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_USCaabJ6O9RBvkJudMfx11LV",
                 "name": "SummarizePaper", "input": {"paper_folder": "/tmp/paper"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_USCaabJ6O9RBvkJudMfx11LV",
                 "content": "Summary done"},
            ]},
        ]
        result = await client.call_llm(messages, "system", tools=None)

        assert result is not None
        assert client._last_response_id == "resp_recovered"
        assert client._last_message_count == 3

        second_call_kwargs = client.client.responses.create.call_args_list[1][1]
        assert "previous_response_id" not in second_call_kwargs


class TestStripOrphanedFunctionCallOutputs:
    """Test _strip_orphaned_function_call_outputs helper."""

    def test_strips_orphaned_outputs(self):
        """function_call_output without matching function_call is stripped."""
        client = _make_client()
        items = [
            {"role": "user", "content": "Hello"},
            {"type": "function_call", "call_id": "call_A", "name": "Bash", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_A", "output": "ok"},
            # Orphan: call_B has no matching function_call
            {"type": "function_call_output", "call_id": "call_B", "output": "orphaned"},
        ]
        cleaned = client._strip_orphaned_function_call_outputs(items)
        assert len(cleaned) == 3
        call_ids = [i.get("call_id") for i in cleaned if i.get("type") == "function_call_output"]
        assert "call_A" in call_ids
        assert "call_B" not in call_ids

    def test_no_stripping_when_all_paired(self):
        """All function_call_output items have matching function_call — nothing stripped."""
        client = _make_client()
        items = [
            {"type": "function_call", "call_id": "call_X", "name": "View", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_X", "output": "file contents"},
        ]
        cleaned = client._strip_orphaned_function_call_outputs(items)
        assert len(cleaned) == 2

    def test_empty_input(self):
        client = _make_client()
        assert client._strip_orphaned_function_call_outputs([]) == []
