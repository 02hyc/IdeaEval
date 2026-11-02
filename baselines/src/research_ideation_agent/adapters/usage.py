"""Usage normalization helpers for adapter payloads."""

from __future__ import annotations

import dataclasses
from typing import Any

from research_ideation_agent.models import UsageSummary


def event_to_jsonable(value: Any, *, model_dump_kwargs: dict[str, Any] | None = None) -> Any:
    """Recursively coerce SDK message payloads into JSON-serialisable shapes.

    Handles dataclasses, pydantic-style ``model_dump`` objects, dicts, lists,
    tuples, and falls back to ``vars(...)`` for arbitrary attribute holders.
    Adapters pass ``model_dump_kwargs`` to override the default ``model_dump``
    invocation (e.g. Codex passes ``{"mode": "json", "by_alias": True}``).
    """
    return _coerce(value, model_dump_kwargs or {})


def _coerce(value: Any, model_dump_kwargs: dict[str, Any]) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if dataclasses.is_dataclass(value):
        return _coerce(dataclasses.asdict(value), model_dump_kwargs)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _coerce(model_dump(**model_dump_kwargs), model_dump_kwargs)
    if isinstance(value, dict):
        return {str(key): _coerce(item, model_dump_kwargs) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce(item, model_dump_kwargs) for item in value]
    try:
        attrs = vars(value)
    except TypeError:
        return str(value)
    return {str(key): _coerce(item, model_dump_kwargs) for key, item in attrs.items() if not str(key).startswith("_")}


def usage_from_claude_result(result: Any) -> UsageSummary | None:
    if result is None:
        return None
    usage = getattr(result, "usage", None)
    if not usage and isinstance(result, dict):
        usage = result.get("usage")
    metadata = {
        "total_cost_usd": _float_or_none(getattr(result, "total_cost_usd", None)),
        "turns": _int_or_none(getattr(result, "num_turns", None)),
        "duration_ms": _int_or_none(getattr(result, "duration_ms", None)),
    }
    if not usage:
        return _non_empty(UsageSummary(**metadata))
    return _non_empty(_usage_from_mapping(usage, **metadata))


def usage_from_codex_result(result: Any) -> UsageSummary | None:
    if result is None:
        return None
    usage = getattr(result, "usage", None)
    duration_ms = _int_or_none(getattr(result, "duration_ms", None))
    if usage is None:
        return _non_empty(UsageSummary(turns=1, duration_ms=duration_ms)) if duration_ms is not None else None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump(mode="json", by_alias=True)
    # The codex token-usage payload carries the model's context window alongside
    # cumulative ``total`` and per-request ``last`` blocks. Capture the window before
    # collapsing to ``total`` so pricing can reason about the per-request long-context
    # tier (a single request cannot exceed the window).
    model_context_window = None
    if isinstance(usage, dict):
        model_context_window = _first_int(usage, "model_context_window", "modelContextWindow")
        if isinstance(usage.get("total"), dict):
            usage = usage["total"]
    return _non_empty(
        _usage_from_mapping(
            usage,
            turns=1,
            duration_ms=duration_ms,
            default_input_tokens_include_cached=True,
            model_context_window=model_context_window,
        )
    )


def _usage_from_mapping(
    usage: Any,
    *,
    total_cost_usd: float | None = None,
    turns: int | None = None,
    duration_ms: int | None = None,
    default_input_tokens_include_cached: bool | None = None,
    model_context_window: int | None = None,
) -> UsageSummary | None:
    if not isinstance(usage, dict):
        return UsageSummary(
            total_cost_usd=total_cost_usd,
            turns=turns,
            duration_ms=duration_ms,
            model_context_window=model_context_window,
        )
    cached_input_tokens = _first_int(
        usage,
        "cached_input_tokens",
        "cachedInputTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "prompt_cache_hit_tokens",
        "promptCacheHitTokens",
    )
    cache_miss_input_tokens = _first_int(
        usage,
        "prompt_cache_miss_tokens",
        "promptCacheMissTokens",
        "cache_miss_input_tokens",
        "cacheMissInputTokens",
    )
    total_input_tokens = _first_int(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
    if total_input_tokens is not None:
        input_tokens = total_input_tokens
        input_tokens_include_cached = _first_bool(
            usage,
            "input_tokens_include_cached",
            "inputTokensIncludeCached",
        )
        if input_tokens_include_cached is None:
            input_tokens_include_cached = default_input_tokens_include_cached
    else:
        # OpenAI-compatible gateways sometimes expose only cache hits/misses
        # instead of a total prompt token count. In that shape, the miss count is
        # already the regular uncached input and must not be reduced by cached
        # tokens during pricing.
        input_tokens = cache_miss_input_tokens
        input_tokens_include_cached = False if cache_miss_input_tokens is not None else None
    cache_creation_input_tokens = _first_int(
        usage,
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
        "cache_write_input_tokens",
        "cacheWriteInputTokens",
    )
    output_tokens = _first_int(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens")
    reasoning_output_tokens = _first_int(
        usage,
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "reasoning_tokens",
        "reasoningTokens",
    )
    total_tokens = _first_int(usage, "total_tokens", "totalTokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_input_for_total = input_tokens
        if input_tokens_include_cached is False:
            total_input_for_total += cached_input_tokens or 0
            total_input_for_total += cache_creation_input_tokens or 0
        total_tokens = total_input_for_total + output_tokens
    return UsageSummary(
        input_tokens=input_tokens,
        input_tokens_include_cached=input_tokens_include_cached,
        cached_input_tokens=cached_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
        model_context_window=model_context_window
        if model_context_window is not None
        else _first_int(usage, "model_context_window", "modelContextWindow"),
        total_cost_usd=total_cost_usd if total_cost_usd is not None else _first_float(usage, "total_cost_usd", "totalCostUsd"),
        turns=turns if turns is not None else _first_int(usage, "turns", "num_turns", "numTurns"),
        duration_ms=duration_ms if duration_ms is not None else _first_int(usage, "duration_ms", "durationMs"),
    )


def _non_empty(summary: UsageSummary | None) -> UsageSummary | None:
    if summary is None:
        return None
    fields = (
        summary.input_tokens,
        summary.input_tokens_include_cached,
        summary.cached_input_tokens,
        summary.cache_creation_input_tokens,
        summary.output_tokens,
        summary.reasoning_output_tokens,
        summary.total_tokens,
        summary.total_cost_usd,
        summary.turns,
        summary.duration_ms,
    )
    return summary if any(value is not None for value in fields) else None


def _first_int(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _first_float(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _first_bool(mapping: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            return value
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
