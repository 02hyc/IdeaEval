from __future__ import annotations

import pytest

from research_ideation_agent import load_config
from research_ideation_agent.models import ModelPricingConfig, UsageSummary
from research_ideation_agent.pricing import apply_configured_pricing, calculate_usage_cost_usd


def test_calculate_usage_cost_with_cache_and_reasoning_breakdown() -> None:
    usage = UsageSummary(
        input_tokens=1000,
        cached_input_tokens=400,
        output_tokens=200,
        reasoning_output_tokens=50,
    )
    pricing = ModelPricingConfig(
        input_per_million_usd=10,
        cached_input_per_million_usd=1,
        output_per_million_usd=20,
        reasoning_output_per_million_usd=30,
    )

    cost = calculate_usage_cost_usd(usage, pricing)

    # 600 regular input * 10 + 400 cached input * 1 +
    # 150 visible output * 20 + 50 reasoning output * 30, all per million.
    assert cost == pytest.approx(0.0109)


def test_calculate_usage_cost_does_not_subtract_cache_from_cache_miss_only_input() -> None:
    usage = UsageSummary(
        input_tokens=600,
        input_tokens_include_cached=False,
        cached_input_tokens=400,
        output_tokens=200,
        reasoning_output_tokens=50,
    )
    pricing = ModelPricingConfig(
        input_per_million_usd=10,
        cached_input_per_million_usd=1,
        output_per_million_usd=20,
        reasoning_output_per_million_usd=30,
        input_includes_cached_tokens=True,
    )

    cost = calculate_usage_cost_usd(usage, pricing)

    # 600 regular input * 10 + 400 cached input * 1 +
    # 150 visible output * 20 + 50 reasoning output * 30, all per million.
    assert cost == pytest.approx(0.0109)


def test_calculate_usage_cost_charges_cache_creation_as_regular_input_without_write_price() -> None:
    usage = UsageSummary(
        input_tokens=600,
        input_tokens_include_cached=False,
        cache_creation_input_tokens=100,
        cached_input_tokens=300,
        output_tokens=200,
    )
    pricing = ModelPricingConfig(
        input_per_million_usd=10,
        cached_input_per_million_usd=1,
        output_per_million_usd=20,
        input_includes_cached_tokens=False,
    )

    cost = calculate_usage_cost_usd(usage, pricing)

    # 600 regular input * 10 + 100 cache-create/cache-miss input * 10 +
    # 300 cached input * 1 + 200 output * 20, all per million.
    assert cost == pytest.approx(0.0113)


def test_calculate_usage_cost_switches_to_long_context_pricing_for_full_request() -> None:
    usage = UsageSummary(
        input_tokens=300_000,
        cached_input_tokens=100_000,
        output_tokens=20_000,
        reasoning_output_tokens=5_000,
    )
    pricing = ModelPricingConfig(
        input_per_million_usd=2.5,
        cached_input_per_million_usd=0.25,
        output_per_million_usd=15,
        reasoning_output_per_million_usd=15,
        input_includes_cached_tokens=True,
        long_context_threshold_tokens=270_000,
        long_context_input_per_million_usd=5,
        long_context_cached_input_per_million_usd=0.5,
        long_context_output_per_million_usd=22.5,
        long_context_reasoning_output_per_million_usd=22.5,
    )

    cost = calculate_usage_cost_usd(usage, pricing)

    # Long context is a whole-request price switch: 200k regular input * 5 +
    # 100k cached input * 0.5 + 20k output/reasoning * 22.5, all per million.
    assert cost == pytest.approx(1.5)


def _long_context_pricing() -> ModelPricingConfig:
    return ModelPricingConfig(
        input_per_million_usd=2.5,
        cached_input_per_million_usd=0.25,
        output_per_million_usd=15,
        reasoning_output_per_million_usd=15,
        input_includes_cached_tokens=True,
        long_context_threshold_tokens=272_000,
        long_context_input_per_million_usd=5,
        long_context_cached_input_per_million_usd=0.5,
        long_context_output_per_million_usd=22.5,
        long_context_reasoning_output_per_million_usd=22.5,
    )


def test_long_context_not_applied_when_request_cannot_exceed_context_window() -> None:
    # Long context is a per-request tier. A 13M-token cumulative session whose model
    # context window (258,400) is below the 272K threshold can never have a single
    # request reach the tier, so every token is billed at base rates.
    usage = UsageSummary(
        input_tokens=13_000_000,
        cached_input_tokens=4_000_000,
        output_tokens=100_000,
        reasoning_output_tokens=60_000,
        model_context_window=258_400,
    )

    cost = calculate_usage_cost_usd(usage, _long_context_pricing())

    # base rates: 9M regular input * 2.5 + 4M cached * 0.25 + 40k output * 15 + 60k reasoning * 15
    assert cost == pytest.approx(25.0)


def test_long_context_still_applies_when_window_allows_a_request_over_threshold() -> None:
    # When the context window exceeds the threshold, a request larger than the
    # threshold is billed at the long-context tier as before.
    usage = UsageSummary(
        input_tokens=300_000,
        cached_input_tokens=100_000,
        output_tokens=20_000,
        reasoning_output_tokens=5_000,
        model_context_window=400_000,
    )

    cost = calculate_usage_cost_usd(usage, _long_context_pricing())

    assert cost == pytest.approx(1.5)


def test_calculate_usage_cost_returns_none_without_any_priced_dimension() -> None:
    usage = UsageSummary(input_tokens=1000, output_tokens=200)

    assert calculate_usage_cost_usd(usage, ModelPricingConfig()) is None


def test_apply_configured_pricing_fills_missing_cost_for_matching_model() -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "pricing.models": {
                "gpt-5.4": {
                    "input_per_million_usd": 10,
                    "output_per_million_usd": 20,
                }
            }
        },
    )
    usage = UsageSummary(input_tokens=1000, output_tokens=200)

    priced = apply_configured_pricing(cfg, usage, "gpt-5.4")

    assert priced is not None
    assert priced.total_cost_usd == pytest.approx(0.014)
    assert usage.total_cost_usd is None


def test_apply_configured_pricing_prefers_configured_official_price_over_provider_cost() -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "pricing.models": {
                "gpt-5.4": {
                    "input_per_million_usd": 10,
                    "output_per_million_usd": 20,
                }
            }
        },
    )
    usage = UsageSummary(input_tokens=1000, output_tokens=200, total_cost_usd=0.5)

    priced = apply_configured_pricing(cfg, usage, "gpt-5.4")

    assert priced is not usage
    assert priced.total_cost_usd == pytest.approx(0.014)
