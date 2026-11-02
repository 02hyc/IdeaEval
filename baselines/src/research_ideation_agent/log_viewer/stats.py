"""Stats aggregation for the log viewer.

Single streaming pass over a run's adapter_events.jsonl, dispatching
each event through a list of registered StatProviders. Each provider
owns one stat entry in the response (e.g. ``tool_counts``, ``tokens``,
``cost``). To add a new stat, append a provider — no edits to the
HTTP layer or the framework code.

Public API
----------

``compute_stats(run_dir, harness=None) -> dict``
    Returns a JSON-serialisable payload with shape::

        {
          "schema_version": 1,
          "harness": "claude" | "codex" | None,
          "file_complete": bool,
          "computed_at": iso8601,
          "event_total": int,
          "stats": {
            "<stat_id>": {
              "kind": "kv" | "value" | "table",
              "label": str,
              "source": str | None,
              "unsupported_reason": str | None,
              "data": ...,        # stat-specific
            },
            ...
          }
        }

The ``stats`` dict is a flexible namespace map. Clients render unknown
``stat_id``s with a generic fallback renderer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research_ideation_agent.log_viewer.server import file_complete, locate_jsonl
from research_ideation_agent.models import ModelPricingConfig, UsageSummary
from research_ideation_agent.pricing import calculate_usage_cost_usd

SCHEMA_VERSION = 1


# ----------------------------------------------------------------------
# Harness sniffing — mirror the JS detectHarnessFromEvent fallback.
# ----------------------------------------------------------------------


def _detect_harness_from_event(ev: dict[str, Any]) -> str | None:
    t = ev.get("type")
    if not isinstance(t, str):
        return None
    if t == "codex_turn_start":
        return "codex"
    if t.startswith("turn/") or t.startswith("item/"):
        return "codex"
    if t in ("thread/tokenUsage/updated", "TurnResult", "adapter_error"):
        return "codex"
    if t in ("AssistantMessage", "UserMessage", "SystemMessage", "ResultMessage"):
        return "claude"
    if t.startswith("Task") and t.endswith("Message"):
        return "claude"
    return None


# ----------------------------------------------------------------------
# StatProvider protocol — minimal, duck-typed.
# Each provider:
#   - id: str  (key under "stats" in the response)
#   - supports(harness) -> bool
#   - observe(event)    -> None  (called once per parsed event)
#   - finalize(run_record) -> dict | None  (None to omit from response)
# ----------------------------------------------------------------------


class _ToolCountsProvider:
    id = "tool_counts"

    def __init__(self) -> None:
        self.harness: str | None = None
        # by_tool: {<short_name>: {"top_level": int, "inside_subagent": int}}
        self.by_tool: dict[str, dict[str, int]] = {}
        self.unsupported_reason: str | None = None

    def supports(self, harness: str | None) -> bool:
        self.harness = harness
        return True

    def observe(self, ev: dict[str, Any]) -> None:
        if self.harness == "codex":
            self._observe_codex(ev)
        else:
            self._observe_claude(ev)

    def _bump(self, name: str, *, in_subagent: bool) -> None:
        bucket = self.by_tool.setdefault(name, {"top_level": 0, "inside_subagent": 0})
        key = "inside_subagent" if in_subagent else "top_level"
        bucket[key] += 1

    def _observe_claude(self, ev: dict[str, Any]) -> None:
        if ev.get("type") != "AssistantMessage":
            return
        msg = ev.get("message") or {}
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if not isinstance(content, list):
            return
        in_sub = msg.get("parent_tool_use_id") is not None
        for block in content:
            if not isinstance(block, dict):
                continue
            # Claude tool_use blocks carry name + input + id; thinking/text blocks don't.
            if "name" in block and "input" in block and "id" in block:
                name = block.get("name")
                if isinstance(name, str) and name:
                    self._bump(_short_tool_name(name), in_subagent=in_sub)

    def _observe_codex(self, ev: dict[str, Any]) -> None:
        if ev.get("type") != "item/started":
            return
        msg = ev.get("message") or {}
        if not isinstance(msg, dict):
            return
        payload = (msg.get("event") or {}).get("payload") or {}
        item = payload.get("item")
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        name = _canonicalize_codex_tool_name(kind, item)
        if name is None:
            return
        # Codex SDK does not emit nested item/started events under a
        # subagent — collabAgentToolCall summarises inner activity in
        # agentsStates instead. So inside_subagent stays at 0; we set
        # the unsupported_reason in finalize().
        self._bump(name, in_subagent=False)

    def finalize(self, run_record: dict[str, Any] | None) -> dict[str, Any]:
        # Sort by total desc, then top_level desc, then name for stability.
        rows = []
        total_top = 0
        total_sub = 0
        for name, b in self.by_tool.items():
            total_top += b["top_level"]
            total_sub += b["inside_subagent"]
            rows.append({
                "name": name,
                "top_level": b["top_level"],
                "inside_subagent": b["inside_subagent"],
            })
        rows.sort(key=lambda r: (-(r["top_level"] + r["inside_subagent"]), -r["top_level"], r["name"]))

        unsupported = None
        if self.harness == "codex":
            # Inner subagent activity is rolled into collabAgentToolCall.agentsStates;
            # the top-level item stream does not surface nested tool calls, so we
            # cannot count them here.
            unsupported = "Codex SDK does not stream inner subagent tool calls"

        return {
            "kind": "table",
            "label": "tool calls",
            "source": "events",
            "unsupported_reason": unsupported,
            "data": {
                "rows": rows,
                "totals": {
                    "top_level": total_top,
                    "inside_subagent": total_sub,
                    "all": total_top + total_sub,
                },
                "inside_subagent_supported": unsupported is None,
            },
        }


class _TokensProvider:
    """Cumulative input/output/cache/reasoning tokens.

    Source priority:
      Claude: ResultMessage.model_usage (sum across models) >
              ResultMessage.usage > run_record.usage > None
      Codex:  run_record.usage (already cumulative via adapters/usage.py) >
              last thread/tokenUsage/updated.tokenUsage.total >
              last TurnResult.usage.total > None

    For Claude we deliberately prefer model_usage over both usage (a per-final-turn
    snapshot in this SDK) and run_record.usage (which propagates that same snapshot)
    so the displayed numbers match the displayed cost.
    """

    id = "tokens"

    def __init__(self) -> None:
        self.harness: str | None = None
        # Claude
        self.last_result_message: dict[str, Any] | None = None
        # Codex
        self.last_token_usage_event: dict[str, Any] | None = None
        self.last_turn_result: dict[str, Any] | None = None

    def supports(self, harness: str | None) -> bool:
        self.harness = harness
        return True

    def observe(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if self.harness == "codex":
            if t == "thread/tokenUsage/updated":
                self.last_token_usage_event = ev
            elif t == "TurnResult":
                self.last_turn_result = ev
        else:
            if t == "ResultMessage":
                self.last_result_message = ev

    def finalize(self, run_record: dict[str, Any] | None) -> dict[str, Any]:
        record_usage = (run_record or {}).get("usage") if run_record else None

        data: dict[str, int | None]
        source: str | None

        if self.harness == "codex":
            data, source = self._extract_codex(record_usage)
        else:
            data, source = self._extract_claude(record_usage)

        return {
            "kind": "kv",
            "label": "tokens",
            "source": source,
            "unsupported_reason": None,
            "data": data,
        }

    # -- Claude ---------------------------------------------------------

    def _extract_claude(
        self, record_usage: dict[str, Any] | None
    ) -> tuple[dict[str, int | None], str | None]:
        # Try ResultMessage.model_usage first (cumulative across the whole run).
        if self.last_result_message is not None:
            msg = self.last_result_message.get("message") or {}
            mu = msg.get("model_usage") or msg.get("modelUsage")
            if isinstance(mu, dict) and mu:
                summed = self._sum_model_usage(mu)
                if summed["total"] is not None or summed["input"] is not None:
                    return summed, "result_message.model_usage"
            # Fall back to ResultMessage.usage (this is a per-final-turn snapshot,
            # but if model_usage is missing it's the next-best signal).
            usage = msg.get("usage")
            if isinstance(usage, dict):
                converted = self._claude_usage_to_kv(usage)
                if converted["total"] is not None or converted["input"] is not None:
                    return converted, "result_message.usage"

        # Last-resort fallback — run_record may carry the summary written by
        # adapters/usage.py. For Claude this currently mirrors the per-final-turn
        # snapshot rather than the cumulative model_usage, but it's better than
        # nothing for runs with no ResultMessage.
        if isinstance(record_usage, dict):
            converted = self._record_usage_to_kv(record_usage)
            if converted["total"] is not None or converted["input"] is not None:
                return converted, "run_record"

        return self._empty_kv(), None

    def _sum_model_usage(self, mu: dict[str, Any]) -> dict[str, int | None]:
        """Sum the per-model dict into a single set of cumulative totals."""
        keys = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        sums: dict[str, int] = {k: 0 for k in keys}
        any_seen = False
        for entry in mu.values():
            if not isinstance(entry, dict):
                continue
            for k in keys:
                v = entry.get(k)
                if isinstance(v, int):
                    sums[k] += v
                    any_seen = True
        if not any_seen:
            return self._empty_kv()
        inp = sums["inputTokens"] or 0
        out = sums["outputTokens"] or 0
        return {
            "input": inp,
            "output": out,
            "cached_input": sums["cacheReadInputTokens"] or None,
            "cache_creation": sums["cacheCreationInputTokens"] or None,
            "reasoning": None,
            "total": inp + out,
        }

    def _claude_usage_to_kv(self, usage: dict[str, Any]) -> dict[str, int | None]:
        inp = _int_or_none(usage.get("input_tokens"))
        out = _int_or_none(usage.get("output_tokens"))
        cache_read = _int_or_none(usage.get("cache_read_input_tokens"))
        cache_create = _int_or_none(usage.get("cache_creation_input_tokens"))
        total = (inp or 0) + (out or 0) if (inp is not None and out is not None) else None
        return {
            "input": inp,
            "output": out,
            "cached_input": cache_read,
            "cache_creation": cache_create,
            "reasoning": None,
            "total": total,
        }

    # -- Codex ----------------------------------------------------------

    def _extract_codex(
        self, record_usage: dict[str, Any] | None
    ) -> tuple[dict[str, int | None], str | None]:
        # Priority 1: run_record.usage (already cumulative).
        if isinstance(record_usage, dict):
            converted = self._record_usage_to_kv(record_usage)
            if converted["total"] is not None or converted["input"] is not None:
                return converted, "run_record"

        # Priority 2: last thread/tokenUsage/updated payload.tokenUsage.total
        if self.last_token_usage_event is not None:
            payload = (self.last_token_usage_event.get("message") or {}).get("event", {}).get("payload", {})
            tu = (payload.get("tokenUsage") or {}).get("total")
            if isinstance(tu, dict):
                converted = self._codex_total_to_kv(tu)
                if converted["total"] is not None or converted["input"] is not None:
                    return converted, "token_usage_event"

        # Priority 3: last TurnResult.usage.total
        if self.last_turn_result is not None:
            usage = (self.last_turn_result.get("message") or {}).get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("total"), dict):
                usage = usage["total"]
            if isinstance(usage, dict):
                converted = self._codex_total_to_kv(usage)
                if converted["total"] is not None or converted["input"] is not None:
                    return converted, "turn_result"

        return self._empty_kv(), None

    def _codex_total_to_kv(self, total: dict[str, Any]) -> dict[str, int | None]:
        inp = _int_or_none(total.get("inputTokens") or total.get("input_tokens"))
        out = _int_or_none(total.get("outputTokens") or total.get("output_tokens"))
        cached = _int_or_none(total.get("cachedInputTokens") or total.get("cached_input_tokens"))
        reasoning = _int_or_none(total.get("reasoningOutputTokens") or total.get("reasoning_output_tokens"))
        total_v = _int_or_none(total.get("totalTokens") or total.get("total_tokens"))
        if total_v is None and inp is not None and out is not None:
            total_v = inp + out
        return {
            "input": inp,
            "output": out,
            "cached_input": cached,
            "cache_creation": None,
            "reasoning": reasoning,
            "total": total_v,
        }

    # -- Shared ---------------------------------------------------------

    def _record_usage_to_kv(self, usage: dict[str, Any]) -> dict[str, int | None]:
        inp = _int_or_none(usage.get("input_tokens"))
        out = _int_or_none(usage.get("output_tokens"))
        cached = _int_or_none(usage.get("cached_input_tokens"))
        cache_creation = _int_or_none(usage.get("cache_creation_input_tokens"))
        reasoning = _int_or_none(usage.get("reasoning_output_tokens"))
        total = _int_or_none(usage.get("total_tokens"))
        if total is None and inp is not None and out is not None:
            total = inp + out
        return {
            "input": inp,
            "output": out,
            "cached_input": cached,
            "cache_creation": cache_creation,
            "reasoning": reasoning,
            "total": total,
        }

    @staticmethod
    def _empty_kv() -> dict[str, int | None]:
        return {
            "input": None,
            "output": None,
            "cached_input": None,
            "cache_creation": None,
            "reasoning": None,
            "total": None,
        }


class _CostProvider:
    """Total cost in USD.

    Source priority:
      All:    configured official token pricing from run_record.resolved_config.pricing
      Claude: sum of ResultMessage.model_usage[*].costUSD >
              ResultMessage.total_cost_usd > run_record.usage.total_cost_usd
    """

    id = "cost"

    def __init__(self) -> None:
        self.harness: str | None = None
        self.last_result_message: dict[str, Any] | None = None

    def supports(self, harness: str | None) -> bool:
        self.harness = harness
        return True

    def observe(self, ev: dict[str, Any]) -> None:
        if self.harness != "codex" and ev.get("type") == "ResultMessage":
            self.last_result_message = ev

    def finalize(self, run_record: dict[str, Any] | None) -> dict[str, Any]:
        cost: float | None = None
        source: str | None = None

        if isinstance(run_record, dict):
            calculated = _cost_from_run_record_pricing(run_record)
            if calculated is not None:
                cost = calculated
                source = "run_record.pricing"

        if cost is None and self.last_result_message is not None:
            msg = self.last_result_message.get("message") or {}
            mu = msg.get("model_usage") or msg.get("modelUsage")
            if isinstance(mu, dict) and mu:
                summed = 0.0
                any_seen = False
                for entry in mu.values():
                    if isinstance(entry, dict):
                        v = entry.get("costUSD")
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            summed += float(v)
                            any_seen = True
                if any_seen:
                    cost = summed
                    source = "result_message.model_usage"
            if cost is None:
                v = msg.get("total_cost_usd")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cost = float(v)
                    source = "result_message.total_cost_usd"

        if cost is None and isinstance(run_record, dict):
            usage = run_record.get("usage")
            if isinstance(usage, dict):
                v = usage.get("total_cost_usd")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cost = float(v)
                    source = "run_record"

        return {
            "kind": "value",
            "label": "cost",
            "source": source,
            "unsupported_reason": (
                None
                if cost is not None
                else "No cost returned by provider and no matching pricing.models entry configured."
            ),
            "data": {"total_cost_usd": cost},
        }


class _DurationProvider:
    """Trace elapsed time.

    Source priority:
      run_record.started_at/completed_at > run_record.usage.duration_ms >
      first/last event timestamps.
    """

    id = "duration"

    def __init__(self) -> None:
        self.first_event_at: datetime | None = None
        self.last_event_at: datetime | None = None

    def supports(self, harness: str | None) -> bool:
        return True

    def observe(self, ev: dict[str, Any]) -> None:
        ts = _parse_timestamp(ev.get("timestamp"))
        if ts is None:
            return
        if self.first_event_at is None or ts < self.first_event_at:
            self.first_event_at = ts
        if self.last_event_at is None or ts > self.last_event_at:
            self.last_event_at = ts

    def finalize(self, run_record: dict[str, Any] | None) -> dict[str, Any]:
        elapsed_ms: int | None = None
        source: str | None = None
        started_at: str | None = None
        completed_at: str | None = None

        if isinstance(run_record, dict):
            started = _parse_timestamp(run_record.get("started_at"))
            completed = _parse_timestamp(run_record.get("completed_at"))
            if started is not None:
                started_at = _iso_z(started)
            if completed is not None:
                completed_at = _iso_z(completed)
            if started is not None and completed is not None:
                elapsed_ms = max(0, int((completed - started).total_seconds() * 1000))
                source = "run_record"
            if elapsed_ms is None:
                usage = run_record.get("usage")
                if isinstance(usage, dict):
                    duration_ms = _int_or_none(usage.get("duration_ms"))
                    if duration_ms is not None:
                        elapsed_ms = duration_ms
                        source = "run_record.usage"

        if elapsed_ms is None and self.first_event_at is not None and self.last_event_at is not None:
            elapsed_ms = max(0, int((self.last_event_at - self.first_event_at).total_seconds() * 1000))
            source = "events"
            started_at = _iso_z(self.first_event_at)
            completed_at = _iso_z(self.last_event_at)

        return {
            "kind": "value",
            "label": "duration",
            "source": source,
            "unsupported_reason": None if elapsed_ms is not None else "No timestamps or duration metadata available.",
            "data": {
                "elapsed_ms": elapsed_ms,
                "started_at": started_at,
                "completed_at": completed_at,
            },
        }


# ----------------------------------------------------------------------
# Registry. To add a new stat: append a provider class with the same
# duck-typed interface (id, supports, observe, finalize).
# ----------------------------------------------------------------------


def _build_providers() -> list[Any]:
    return [_ToolCountsProvider(), _TokensProvider(), _CostProvider(), _DurationProvider()]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


_MCP_PREFIX = "mcp__"


def _short_tool_name(name: str) -> str:
    """Strip mcp__<server>__ prefix (e.g. mcp__scholar-gateway__web_search → web_search)."""
    if not isinstance(name, str):
        return str(name)
    if name.startswith(_MCP_PREFIX):
        rest = name[len(_MCP_PREFIX):]
        idx = rest.find("__")
        if idx >= 0:
            return rest[idx + 2 :]
    return name


def _canonicalize_codex_tool_name(kind: Any, item: dict[str, Any]) -> str | None:
    if kind == "mcpToolCall":
        tool = item.get("tool")
        if isinstance(tool, str) and tool:
            return tool
        return "mcp"
    if kind == "commandExecution":
        return "Bash"
    if kind == "fileChange":
        return "Edit"
    if kind == "webSearch":
        return "WebSearch"
    if kind == "collabAgentToolCall":
        return "Agent"
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


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _cost_from_run_record_pricing(run_record: dict[str, Any]) -> float | None:
    usage_raw = run_record.get("usage")
    model = run_record.get("model")
    if not isinstance(usage_raw, dict) or not isinstance(model, str) or not model:
        return None

    resolved_config = run_record.get("resolved_config")
    if not isinstance(resolved_config, dict):
        return None
    pricing_root = resolved_config.get("pricing")
    if not isinstance(pricing_root, dict):
        return None
    models = pricing_root.get("models")
    if not isinstance(models, dict):
        return None
    model_pricing = models.get(model)
    if not isinstance(model_pricing, dict):
        return None

    usage = UsageSummary(
        input_tokens=_int_or_none(usage_raw.get("input_tokens")),
        input_tokens_include_cached=(
            usage_raw["input_tokens_include_cached"]
            if isinstance(usage_raw.get("input_tokens_include_cached"), bool)
            else None
        ),
        cached_input_tokens=_int_or_none(usage_raw.get("cached_input_tokens")),
        cache_creation_input_tokens=_int_or_none(usage_raw.get("cache_creation_input_tokens")),
        output_tokens=_int_or_none(usage_raw.get("output_tokens")),
        reasoning_output_tokens=_int_or_none(usage_raw.get("reasoning_output_tokens")),
        total_tokens=_int_or_none(usage_raw.get("total_tokens")),
        model_context_window=_int_or_none(usage_raw.get("model_context_window")),
    )
    pricing = ModelPricingConfig(
        input_per_million_usd=_float_or_none(model_pricing.get("input_per_million_usd")),
        cached_input_per_million_usd=_float_or_none(model_pricing.get("cached_input_per_million_usd")),
        cache_creation_input_per_million_usd=_float_or_none(
            model_pricing.get("cache_creation_input_per_million_usd")
        ),
        output_per_million_usd=_float_or_none(model_pricing.get("output_per_million_usd")),
        reasoning_output_per_million_usd=_float_or_none(
            model_pricing.get("reasoning_output_per_million_usd")
        ),
        input_includes_cached_tokens=(
            model_pricing["input_includes_cached_tokens"]
            if isinstance(model_pricing.get("input_includes_cached_tokens"), bool)
            else True
        ),
        long_context_threshold_tokens=_int_or_none(model_pricing.get("long_context_threshold_tokens")),
        long_context_input_per_million_usd=_float_or_none(model_pricing.get("long_context_input_per_million_usd")),
        long_context_cached_input_per_million_usd=_float_or_none(
            model_pricing.get("long_context_cached_input_per_million_usd")
        ),
        long_context_output_per_million_usd=_float_or_none(model_pricing.get("long_context_output_per_million_usd")),
        long_context_reasoning_output_per_million_usd=_float_or_none(
            model_pricing.get("long_context_reasoning_output_per_million_usd")
        ),
    )
    return calculate_usage_cost_usd(usage, pricing)


def _iter_events(jsonl_path: Path) -> Iterable[dict[str, Any]]:
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                yield ev


def _load_run_record(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "run_record.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def compute_stats(run_dir: Path, harness: str | None = None) -> dict[str, Any]:
    """Read events once, fan out to providers, return a JSON payload."""
    record = _load_run_record(run_dir)
    if harness is None and record is not None:
        h = record.get("harness")
        if isinstance(h, str) and h:
            harness = h

    try:
        jsonl_path = locate_jsonl(run_dir)
    except FileNotFoundError:
        # No events yet; still emit an empty payload so the panel can render placeholders.
        providers = _build_providers()
        for p in providers:
            p.supports(harness)
        return _finalize(providers, harness, record, run_dir, event_total=0)

    providers = _build_providers()
    for p in providers:
        p.supports(harness)

    event_total = 0
    sniffed = harness
    for ev in _iter_events(jsonl_path):
        event_total += 1
        if sniffed is None:
            sniffed = _detect_harness_from_event(ev)
            if sniffed is not None:
                for p in providers:
                    p.supports(sniffed)
        for p in providers:
            p.observe(ev)

    if sniffed is None:
        # Default to claude if we couldn't tell — matches the JS dispatch fallback.
        sniffed = "claude"
        for p in providers:
            p.supports(sniffed)

    return _finalize(providers, sniffed, record, run_dir, event_total=event_total)


def _finalize(
    providers: list[Any],
    harness: str | None,
    run_record: dict[str, Any] | None,
    run_dir: Path,
    *,
    event_total: int,
) -> dict[str, Any]:
    stats: dict[str, dict[str, Any]] = {}
    for p in providers:
        out = p.finalize(run_record)
        if out is not None:
            stats[p.id] = out
    return {
        "schema_version": SCHEMA_VERSION,
        "harness": harness,
        "file_complete": file_complete(run_dir),
        "event_total": event_total,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": stats,
    }
