"""Token-usage based cost calculation."""

from __future__ import annotations

from dataclasses import replace

from research_ideation_agent.models import ModelPricingConfig, RunConfig, UsageSummary


def apply_configured_pricing(
    config: RunConfig,
    usage: UsageSummary | None,
    model: str | None,
) -> UsageSummary | None:
    """Set ``usage.total_cost_usd`` from configured official model prices when possible."""

    if usage is None or model is None:
        return usage
    pricing = config.pricing.models.get(model)
    if pricing is None:
        return usage
    total = calculate_usage_cost_usd(usage, pricing)
    if total is None:
        return usage
    return replace(usage, total_cost_usd=total)


def calculate_usage_cost_usd(
    usage: UsageSummary,
    pricing: ModelPricingConfig,
) -> float | None:
    cost = 0.0
    any_priced = False

    input_tokens = usage.input_tokens
    cached_input_tokens = usage.cached_input_tokens or 0
    cache_creation_tokens = usage.cache_creation_input_tokens or 0
    input_includes_cached = (
        usage.input_tokens_include_cached
        if usage.input_tokens_include_cached is not None
        else pricing.input_includes_cached_tokens
    )
    long_context = _uses_long_context(
        usage=usage,
        pricing=pricing,
        input_includes_cached=input_includes_cached,
        cached_input_tokens=cached_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    input_price = _long_or_base(
        long_context,
        pricing.long_context_input_per_million_usd,
        pricing.input_per_million_usd,
    )
    cached_input_price = _long_or_base(
        long_context,
        pricing.long_context_cached_input_per_million_usd,
        pricing.cached_input_per_million_usd,
    )
    output_price = _long_or_base(
        long_context,
        pricing.long_context_output_per_million_usd,
        pricing.output_per_million_usd,
    )
    reasoning_price = _long_or_base(
        long_context,
        pricing.long_context_reasoning_output_per_million_usd,
        pricing.reasoning_output_per_million_usd,
    )
    if long_context and reasoning_price is None and pricing.reasoning_output_per_million_usd is not None:
        reasoning_price = output_price

    if input_tokens is not None and input_price is not None:
        regular_input_tokens = input_tokens
        if input_includes_cached:
            if cached_input_price is not None:
                regular_input_tokens -= cached_input_tokens
            if pricing.cache_creation_input_per_million_usd is not None:
                regular_input_tokens -= cache_creation_tokens
        cost += _cost(max(regular_input_tokens, 0), input_price)
        any_priced = True

    if cached_input_tokens and cached_input_price is not None:
        cost += _cost(cached_input_tokens, cached_input_price)
        any_priced = True

    if cache_creation_tokens:
        cache_creation_price = pricing.cache_creation_input_per_million_usd
        if cache_creation_price is None and not input_includes_cached:
            cache_creation_price = input_price
        if cache_creation_price is not None:
            cost += _cost(cache_creation_tokens, cache_creation_price)
            any_priced = True

    output_tokens = usage.output_tokens
    reasoning_tokens = usage.reasoning_output_tokens or 0
    if output_tokens is not None and output_price is not None:
        visible_output_tokens = output_tokens
        if reasoning_tokens and reasoning_price is not None:
            visible_output_tokens = max(output_tokens - reasoning_tokens, 0)
        cost += _cost(visible_output_tokens, output_price)
        any_priced = True

    if reasoning_tokens and reasoning_price is not None:
        cost += _cost(reasoning_tokens, reasoning_price)
        any_priced = True

    return cost if any_priced else None


def _uses_long_context(
    *,
    usage: UsageSummary,
    pricing: ModelPricingConfig,
    input_includes_cached: bool,
    cached_input_tokens: int,
    cache_creation_tokens: int,
) -> bool:
    threshold = pricing.long_context_threshold_tokens
    input_tokens = usage.input_tokens
    if threshold is None or input_tokens is None:
        return False
    # Long context is a per-request price tier: only a request whose own context
    # exceeds the threshold is billed at the higher rate. A single request cannot
    # exceed the model's context window, so when the window is at or below the
    # threshold no request can ever reach the tier — regardless of how large the
    # cumulative session total grows. (Without a window we fall back to the
    # cumulative input as the only available signal.)
    window = usage.model_context_window
    if window is not None and window <= threshold:
        return False
    total_input_tokens = input_tokens
    if not input_includes_cached:
        total_input_tokens += cached_input_tokens + cache_creation_tokens
    return total_input_tokens > threshold


def _long_or_base(long_context: bool, long_price: float | None, base_price: float | None) -> float | None:
    return long_price if long_context and long_price is not None else base_price


def _cost(tokens: int, price_per_million: float) -> float:
    return tokens * price_per_million / 1_000_000
