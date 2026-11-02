from __future__ import annotations

from types import SimpleNamespace

from research_ideation_agent.adapters.usage import usage_from_claude_result, usage_from_codex_result


def test_claude_usage_from_token_mapping() -> None:
    result = SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 20}, total_cost_usd=0.5, num_turns=3, duration_ms=100)
    summary = usage_from_claude_result(result)
    assert summary is not None
    assert summary.input_tokens == 10
    assert summary.output_tokens == 20
    assert summary.total_tokens == 30
    assert summary.total_cost_usd == 0.5
    assert summary.turns == 3
    assert summary.duration_ms == 100


def test_claude_usage_maps_cache_read_aliases() -> None:
    result = SimpleNamespace(
        usage={
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 40,
        },
        total_cost_usd=None,
        num_turns=1,
        duration_ms=None,
    )

    summary = usage_from_claude_result(result)

    assert summary is not None
    assert summary.input_tokens == 10
    assert summary.cached_input_tokens == 30
    assert summary.cache_creation_input_tokens == 40
    assert summary.output_tokens == 20


def test_claude_usage_from_metadata_only_is_populated() -> None:
    result = SimpleNamespace(usage=None, total_cost_usd=1.5, num_turns=2, duration_ms=50)
    summary = usage_from_claude_result(result)
    assert summary is not None
    assert summary.total_cost_usd == 1.5
    assert summary.turns == 2


def test_claude_usage_with_no_data_is_none() -> None:
    result = SimpleNamespace(usage=None, total_cost_usd=None, num_turns=None, duration_ms=None)
    assert usage_from_claude_result(result) is None
    assert usage_from_claude_result(None) is None


def test_codex_usage_from_nested_total_mapping() -> None:
    usage = {
        "total": {
            "input_tokens": 11,
            "cached_input_tokens": 3,
            "output_tokens": 7,
            "reasoning_output_tokens": 5,
            "total_tokens": 18,
        }
    }
    summary = usage_from_codex_result(SimpleNamespace(usage=usage, duration_ms=123))
    assert summary is not None
    assert summary.input_tokens == 11
    assert summary.cached_input_tokens == 3
    assert summary.output_tokens == 7
    assert summary.reasoning_output_tokens == 5
    assert summary.total_tokens == 18
    assert summary.turns == 1
    assert summary.duration_ms == 123


def test_codex_usage_captures_context_window_and_prices_cumulative_total() -> None:
    # Shape of a real codex token_count event (by_alias dump): a per-request ``last``
    # block, a cumulative ``total`` block, and the model context window. Pricing reads
    # token counts from ``total`` and keeps the window to gate the per-request
    # long-context tier.
    usage = {
        "last": {
            "inputTokens": 21794,
            "cachedInputTokens": 9984,
            "outputTokens": 267,
            "reasoningOutputTokens": 27,
            "totalTokens": 22061,
        },
        "total": {
            "inputTokens": 31052,
            "cachedInputTokens": 9984,
            "outputTokens": 1077,
            "reasoningOutputTokens": 270,
            "totalTokens": 32129,
        },
        "modelContextWindow": 258400,
    }

    summary = usage_from_codex_result(SimpleNamespace(usage=usage, duration_ms=None))

    assert summary is not None
    assert summary.input_tokens == 31052  # cumulative total, not the per-request last (21794)
    assert summary.cached_input_tokens == 9984
    assert summary.model_context_window == 258400


def test_openai_style_usage_preserves_total_prompt_tokens_when_available() -> None:
    usage = {
        "prompt_tokens": 100,
        "prompt_cache_hit_tokens": 70,
        "prompt_cache_miss_tokens": 30,
        "completion_tokens": 20,
        "total_tokens": 120,
    }

    summary = usage_from_codex_result(SimpleNamespace(usage=usage, duration_ms=None))

    assert summary is not None
    assert summary.input_tokens == 100
    assert summary.input_tokens_include_cached is True
    assert summary.cached_input_tokens == 70
    assert summary.output_tokens == 20
    assert summary.total_tokens == 120


def test_openai_style_usage_marks_cache_miss_only_input_as_excluding_cached() -> None:
    usage = {
        "prompt_cache_hit_tokens": 70,
        "prompt_cache_miss_tokens": 30,
        "completion_tokens": 20,
    }

    summary = usage_from_codex_result(SimpleNamespace(usage=usage, duration_ms=None))

    assert summary is not None
    assert summary.input_tokens == 30
    assert summary.input_tokens_include_cached is False
    assert summary.cached_input_tokens == 70
    assert summary.output_tokens == 20
    assert summary.total_tokens == 120


def test_codex_usage_duration_only_is_populated() -> None:
    summary = usage_from_codex_result(SimpleNamespace(usage=None, duration_ms=200))
    assert summary is not None
    assert summary.turns == 1
    assert summary.duration_ms == 200


def test_codex_usage_with_no_data_is_none() -> None:
    assert usage_from_codex_result(SimpleNamespace(usage=None, duration_ms=None)) is None
    assert usage_from_codex_result(None) is None
