"""Tests for log_viewer.stats — provider-based aggregator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_ideation_agent.log_viewer.stats import compute_stats


def _write_jsonl(run_dir: Path, events: list[dict]) -> None:
    logs = run_dir / "framework" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "adapter_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )


def _write_record(run_dir: Path, record: dict) -> None:
    (run_dir / "run_record.json").write_text(json.dumps(record))


# ----------------------------------------------------------------------
# Tool counts
# ----------------------------------------------------------------------


def test_claude_tool_counts_top_level_only(tmp_path: Path) -> None:
    events = [
        {
            "type": "AssistantMessage",
            "message": {
                "content": [
                    {"id": "t1", "name": "Bash", "input": {"command": "ls"}},
                    {"id": "t2", "name": "Read", "input": {"file_path": "x"}},
                ],
                "parent_tool_use_id": None,
            },
        },
        # An AssistantMessage with both thinking and tool_use blocks.
        {
            "type": "AssistantMessage",
            "message": {
                "content": [
                    {"thinking": "...", "signature": "..."},
                    {"id": "t3", "name": "Bash", "input": {"command": "pwd"}},
                ],
                "parent_tool_use_id": None,
            },
        },
    ]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="claude")
    tc = payload["stats"]["tool_counts"]["data"]
    by = {r["name"]: r for r in tc["rows"]}
    assert by["Bash"]["top_level"] == 2
    assert by["Bash"]["inside_subagent"] == 0
    assert by["Read"]["top_level"] == 1
    assert tc["totals"] == {"top_level": 3, "inside_subagent": 0, "all": 3}
    assert tc["inside_subagent_supported"] is True


def test_claude_tool_counts_inside_subagent(tmp_path: Path) -> None:
    events = [
        # A top-level Agent invocation
        {
            "type": "AssistantMessage",
            "message": {
                "content": [{"id": "ag1", "name": "Agent", "input": {"prompt": "..."}}],
                "parent_tool_use_id": None,
            },
        },
        # A nested AssistantMessage from inside the subagent
        {
            "type": "AssistantMessage",
            "message": {
                "content": [{"id": "n1", "name": "Read", "input": {"file_path": "x"}}],
                "parent_tool_use_id": "ag1",
            },
        },
        {
            "type": "AssistantMessage",
            "message": {
                "content": [{"id": "n2", "name": "Bash", "input": {"command": "ls"}}],
                "parent_tool_use_id": "ag1",
            },
        },
    ]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="claude")
    tc = payload["stats"]["tool_counts"]["data"]
    by = {r["name"]: r for r in tc["rows"]}
    assert by["Agent"]["top_level"] == 1
    assert by["Agent"]["inside_subagent"] == 0
    assert by["Read"]["inside_subagent"] == 1
    assert by["Bash"]["inside_subagent"] == 1
    assert tc["totals"] == {"top_level": 1, "inside_subagent": 2, "all": 3}


def test_claude_short_tool_name_strips_mcp_prefix(tmp_path: Path) -> None:
    events = [
        {
            "type": "AssistantMessage",
            "message": {
                "content": [
                    {"id": "m1", "name": "mcp__scholar-gateway__web_search", "input": {"q": "x"}},
                ],
                "parent_tool_use_id": None,
            },
        },
    ]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="claude")
    by = {r["name"]: r for r in payload["stats"]["tool_counts"]["data"]["rows"]}
    assert "web_search" in by
    assert "mcp__scholar-gateway__web_search" not in by


def test_codex_tool_counts_canonicalisation(tmp_path: Path) -> None:
    def started(item):
        return {
            "type": "item/started",
            "message": {"event": {"method": "item/started", "payload": {"item": item}}},
        }

    events = [
        started({"type": "userMessage", "id": "u1", "content": []}),
        started({"type": "mcpToolCall", "id": "m1", "tool": "web_search", "server": "scholar-gateway"}),
        started({"type": "mcpToolCall", "id": "m2", "tool": "extract_paper", "server": "scholar-gateway"}),
        started({"type": "commandExecution", "id": "c1", "command": "ls"}),
        started({"type": "commandExecution", "id": "c2", "command": "pwd"}),
        started({"type": "fileChange", "id": "f1"}),
        started({"type": "webSearch", "id": "w1", "query": "foo"}),
        started({"type": "collabAgentToolCall", "id": "a1", "prompt": "..."}),
    ]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="codex")
    tc = payload["stats"]["tool_counts"]["data"]
    by = {r["name"]: r["top_level"] for r in tc["rows"]}
    assert by == {
        "web_search": 1,
        "extract_paper": 1,
        "Bash": 2,
        "Edit": 1,
        "WebSearch": 1,
        "Agent": 1,
    }
    assert tc["inside_subagent_supported"] is False
    # All counted as top_level; subagent stays at 0
    assert tc["totals"]["inside_subagent"] == 0
    assert payload["stats"]["tool_counts"]["unsupported_reason"]


# ----------------------------------------------------------------------
# Tokens — Claude
# ----------------------------------------------------------------------


def test_claude_tokens_prefer_model_usage_over_usage(tmp_path: Path) -> None:
    """ResultMessage.message.usage is a per-final-turn snapshot; model_usage is cumulative.
    The aggregator must surface the larger model_usage numbers and the cost that matches them.
    """
    events = [
        {
            "type": "ResultMessage",
            "message": {
                "subtype": "success",
                "usage": {
                    "input_tokens": 5178,  # last-turn snapshot
                    "output_tokens": 15145,
                    "cache_read_input_tokens": 1657930,
                    "cache_creation_input_tokens": 942104,
                },
                "model_usage": {
                    "global.anthropic.claude-opus-4-6-v1": {
                        "inputTokens": 11530,  # cumulative
                        "outputTokens": 18378,
                        "cacheReadInputTokens": 1803037,
                        "cacheCreationInputTokens": 950982,
                        "costUSD": 7.362256,
                    }
                },
                "total_cost_usd": 7.362256,
            },
        },
    ]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="claude")
    tokens = payload["stats"]["tokens"]
    assert tokens["source"] == "result_message.model_usage"
    assert tokens["data"]["input"] == 11530
    assert tokens["data"]["output"] == 18378
    assert tokens["data"]["cached_input"] == 1803037
    assert tokens["data"]["cache_creation"] == 950982
    assert tokens["data"]["total"] == 11530 + 18378
    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] == pytest.approx(7.362256)
    assert cost["source"] == "result_message.model_usage"


def test_claude_tokens_fallback_to_usage(tmp_path: Path) -> None:
    """When model_usage is missing, fall back to ResultMessage.usage."""
    events = [
        {
            "type": "ResultMessage",
            "message": {
                "subtype": "success",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                },
                "model_usage": None,
                "total_cost_usd": 0.123,
            },
        },
    ]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="claude")
    tokens = payload["stats"]["tokens"]
    assert tokens["source"] == "result_message.usage"
    assert tokens["data"]["input"] == 100
    assert tokens["data"]["total"] == 150
    assert payload["stats"]["cost"]["data"]["total_cost_usd"] == pytest.approx(0.123)
    assert payload["stats"]["cost"]["source"] == "result_message.total_cost_usd"


def test_claude_tokens_fallback_to_run_record(tmp_path: Path) -> None:
    """When there is no ResultMessage event, fall back to run_record.usage."""
    _write_jsonl(tmp_path, [])
    _write_record(
        tmp_path,
        {
            "harness": "claude",
            "status": "succeeded",
            "usage": {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33, "total_cost_usd": 1.5},
        },
    )
    payload = compute_stats(tmp_path)
    tokens = payload["stats"]["tokens"]
    assert tokens["source"] == "run_record"
    assert tokens["data"]["input"] == 11
    assert tokens["data"]["total"] == 33
    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] == pytest.approx(1.5)
    assert cost["source"] == "run_record"


def test_cost_prefers_run_record_official_pricing_over_provider_cost(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path,
        [
            {
                "type": "ResultMessage",
                "message": {
                    "subtype": "success",
                    "usage": {"input_tokens": 1000, "output_tokens": 200},
                    "model_usage": {"claude-opus-4.6": {"costUSD": 0.25}},
                    "total_cost_usd": 0.25,
                },
            }
        ],
    )
    _write_record(
        tmp_path,
        {
            "harness": "claude",
            "model": "claude-opus-4.6",
            "status": "succeeded",
            "usage": {"input_tokens": 1000, "output_tokens": 200, "total_cost_usd": 0.25},
            "resolved_config": {
                "pricing": {
                    "models": {
                        "claude-opus-4.6": {
                            "input_per_million_usd": 5,
                            "output_per_million_usd": 25,
                            "input_includes_cached_tokens": False,
                        }
                    }
                }
            },
        },
    )

    payload = compute_stats(tmp_path)

    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] == pytest.approx(0.01)
    assert cost["source"] == "run_record.pricing"


# ----------------------------------------------------------------------
# Tokens — Codex
# ----------------------------------------------------------------------


def test_codex_tokens_prefer_run_record(tmp_path: Path) -> None:
    """Codex's run_record.usage is already cumulative (via adapters/usage.py); use it first."""
    events = [
        # A stale token-usage event with smaller numbers — must NOT win.
        {
            "type": "thread/tokenUsage/updated",
            "message": {
                "event": {
                    "method": "thread/tokenUsage/updated",
                    "payload": {
                        "tokenUsage": {
                            "total": {
                                "inputTokens": 1,
                                "outputTokens": 2,
                                "totalTokens": 3,
                            }
                        }
                    },
                }
            },
        },
    ]
    _write_jsonl(tmp_path, events)
    _write_record(
        tmp_path,
        {
            "harness": "codex",
            "status": "succeeded",
            "usage": {
                "input_tokens": 5079919,
                "output_tokens": 36393,
                "cached_input_tokens": 3160576,
                "reasoning_output_tokens": 20489,
                "total_tokens": 5116312,
                "total_cost_usd": None,
            },
        },
    )
    payload = compute_stats(tmp_path)
    tokens = payload["stats"]["tokens"]
    assert tokens["source"] == "run_record"
    assert tokens["data"]["input"] == 5079919
    assert tokens["data"]["total"] == 5116312
    assert tokens["data"]["reasoning"] == 20489
    # Without a pricing table, Codex has no provider-side cost to show.
    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] is None
    assert cost["unsupported_reason"]


def test_codex_tokens_fallback_to_last_token_usage(tmp_path: Path) -> None:
    """Without a run_record, use the LAST thread/tokenUsage/updated event."""
    def usage_event(total_in):
        return {
            "type": "thread/tokenUsage/updated",
            "message": {
                "event": {
                    "method": "thread/tokenUsage/updated",
                    "payload": {
                        "tokenUsage": {
                            "total": {
                                "inputTokens": total_in,
                                "outputTokens": 100,
                                "totalTokens": total_in + 100,
                            }
                        }
                    },
                }
            },
        }

    events = [usage_event(10), usage_event(20), usage_event(30)]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path, harness="codex")
    tokens = payload["stats"]["tokens"]
    assert tokens["source"] == "token_usage_event"
    assert tokens["data"]["input"] == 30  # last wins
    assert tokens["data"]["total"] == 130


def test_codex_cost_is_unsupported(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, [{"type": "codex_turn_start", "message": {}}])
    payload = compute_stats(tmp_path, harness="codex")
    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] is None
    assert cost["source"] is None
    assert cost["unsupported_reason"]


def test_codex_cost_uses_run_record_pricing_table(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, [{"type": "codex_turn_start", "message": {}}])
    _write_record(
        tmp_path,
        {
            "harness": "codex",
            "model": "gpt-5.4",
            "status": "succeeded",
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 400,
                "output_tokens": 200,
                "reasoning_output_tokens": 50,
                "total_tokens": 1200,
                "total_cost_usd": None,
            },
            "resolved_config": {
                "pricing": {
                    "models": {
                        "gpt-5.4": {
                            "input_per_million_usd": 10,
                            "cached_input_per_million_usd": 1,
                            "output_per_million_usd": 20,
                            "reasoning_output_per_million_usd": 30,
                        }
                    }
                }
            },
        },
    )

    payload = compute_stats(tmp_path)

    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] == pytest.approx(0.0109)
    assert cost["source"] == "run_record.pricing"
    assert cost["unsupported_reason"] is None


def test_cost_uses_long_context_pricing_from_run_record(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, [{"type": "codex_turn_start", "message": {}}])
    _write_record(
        tmp_path,
        {
            "harness": "codex",
            "model": "gpt-5.4",
            "status": "succeeded",
            "usage": {
                "input_tokens": 300_000,
                "cached_input_tokens": 100_000,
                "output_tokens": 20_000,
                "reasoning_output_tokens": 5_000,
                "total_cost_usd": None,
            },
            "resolved_config": {
                "pricing": {
                    "models": {
                        "gpt-5.4": {
                            "input_per_million_usd": 2.5,
                            "cached_input_per_million_usd": 0.25,
                            "output_per_million_usd": 15,
                            "reasoning_output_per_million_usd": 15,
                            "long_context_threshold_tokens": 270_000,
                            "long_context_input_per_million_usd": 5,
                            "long_context_cached_input_per_million_usd": 0.5,
                            "long_context_output_per_million_usd": 22.5,
                            "long_context_reasoning_output_per_million_usd": 22.5,
                        }
                    }
                }
            },
        },
    )

    payload = compute_stats(tmp_path)

    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] == pytest.approx(1.5)
    assert cost["source"] == "run_record.pricing"


def test_cost_does_not_apply_long_context_when_window_below_threshold(tmp_path: Path) -> None:
    # A huge cumulative session whose per-request context window is below the threshold
    # must be billed at base rates by the viewer's recompute, matching the runtime.
    _write_jsonl(tmp_path, [{"type": "codex_turn_start", "message": {}}])
    _write_record(
        tmp_path,
        {
            "harness": "codex",
            "model": "gpt-5.4",
            "status": "succeeded",
            "usage": {
                "input_tokens": 13_000_000,
                "cached_input_tokens": 4_000_000,
                "output_tokens": 100_000,
                "reasoning_output_tokens": 60_000,
                "model_context_window": 258_400,
                "total_cost_usd": None,
            },
            "resolved_config": {
                "pricing": {
                    "models": {
                        "gpt-5.4": {
                            "input_per_million_usd": 2.5,
                            "cached_input_per_million_usd": 0.25,
                            "output_per_million_usd": 15,
                            "reasoning_output_per_million_usd": 15,
                            "long_context_threshold_tokens": 272_000,
                            "long_context_input_per_million_usd": 5,
                            "long_context_cached_input_per_million_usd": 0.5,
                            "long_context_output_per_million_usd": 22.5,
                            "long_context_reasoning_output_per_million_usd": 22.5,
                        }
                    }
                }
            },
        },
    )

    payload = compute_stats(tmp_path)

    cost = payload["stats"]["cost"]
    assert cost["data"]["total_cost_usd"] == pytest.approx(25.0)
    assert cost["source"] == "run_record.pricing"


def test_duration_uses_run_record_timestamps(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path,
        [
            {"type": "codex_turn_start", "timestamp": "2026-06-03T09:28:06Z", "message": {}},
            {"type": "TurnResult", "timestamp": "2026-06-03T09:28:08Z", "message": {}},
        ],
    )
    _write_record(
        tmp_path,
        {
            "harness": "codex",
            "status": "succeeded",
            "started_at": "2026-06-03T09:28:03Z",
            "completed_at": "2026-06-03T09:28:20Z",
        },
    )

    payload = compute_stats(tmp_path)

    duration = payload["stats"]["duration"]
    assert duration["source"] == "run_record"
    assert duration["data"]["elapsed_ms"] == 17_000
    assert duration["data"]["started_at"] == "2026-06-03T09:28:03Z"
    assert duration["data"]["completed_at"] == "2026-06-03T09:28:20Z"


def test_duration_falls_back_to_event_span(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path,
        [
            {"type": "codex_turn_start", "timestamp": "2026-06-03T09:28:06Z", "message": {}},
            {"type": "TurnResult", "timestamp": "2026-06-03T09:28:16Z", "message": {}},
        ],
    )

    payload = compute_stats(tmp_path, harness="codex")

    duration = payload["stats"]["duration"]
    assert duration["source"] == "events"
    assert duration["data"]["elapsed_ms"] == 10_000


# ----------------------------------------------------------------------
# Harness sniffing
# ----------------------------------------------------------------------


def test_harness_sniffed_from_first_event_when_record_missing(tmp_path: Path) -> None:
    events = [{"type": "codex_turn_start", "message": {}}]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path)
    assert payload["harness"] == "codex"


def test_harness_default_claude_when_unknown(tmp_path: Path) -> None:
    events = [{"type": "MysteryEvent", "message": {}}]
    _write_jsonl(tmp_path, events)
    payload = compute_stats(tmp_path)
    assert payload["harness"] == "claude"


def test_payload_shape_minimum(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, [])
    payload = compute_stats(tmp_path)
    for key in ("schema_version", "harness", "file_complete", "computed_at", "event_total", "stats"):
        assert key in payload
    for stat_id in ("tokens", "cost", "tool_counts", "duration"):
        assert stat_id in payload["stats"]
        entry = payload["stats"][stat_id]
        assert "kind" in entry
        assert "label" in entry
        assert "data" in entry
