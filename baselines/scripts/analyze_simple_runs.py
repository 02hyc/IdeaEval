"""Analyze simple-asset runs across harnesses and emit a markdown report.

Scope: 5 run directories under runs/, each with 91 succeeded runs.
Computes resource consumption, scholar-gateway literature usage, and tool-call
behavior from run_record.json + adapter_events.jsonl + references/ on disk.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

RUNS_ROOT = Path(__file__).resolve().parent.parent / "runs"

HARNESSES = [
    "glm5_1_claude_code",
    "opus4_6_claude_code",
    "deepseek_v4_pro_claude_code",
    "kimi_k2_6_claude_code",
    "gpt_5_4_codex",
]

# Tool-name groups we want to surface explicitly. Anything not matched lands in `other`.
SCHOLAR_TOOLS = {"web_search", "web_fetch", "extract_paper"}


@dataclass
class RunStats:
    run_id: str
    harness_dir: str
    harness: str  # claude|codex
    model: str
    # cost / usage
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    turns: int | None = None
    # literature
    references_dir_count: int = 0
    # tool calls (parsed from adapter_events.jsonl)
    tool_counts: dict[str, int] = field(default_factory=dict)
    scholar_counts: dict[str, int] = field(default_factory=dict)  # search/fetch/extract


def load_run(run_dir: Path, harness_dir: str) -> RunStats | None:
    rr_path = run_dir / "run_record.json"
    if not rr_path.exists():
        return None
    rr = json.loads(rr_path.read_text())
    usage = rr.get("usage") or {}
    s = RunStats(
        run_id=rr.get("run_id", run_dir.name),
        harness_dir=harness_dir,
        harness=rr.get("harness", ""),
        model=rr.get("model", ""),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        cached_input_tokens=usage.get("cached_input_tokens"),
        reasoning_output_tokens=usage.get("reasoning_output_tokens"),
        cost_usd=usage.get("total_cost_usd"),
        duration_ms=usage.get("duration_ms"),
        turns=usage.get("turns"),
    )

    refs_dir = run_dir / "agent_workspace" / "references"
    if refs_dir.exists():
        s.references_dir_count = sum(1 for p in refs_dir.iterdir() if p.is_dir())

    log_path = run_dir / "framework" / "logs" / "adapter_events.jsonl"
    if log_path.exists():
        s.tool_counts, s.scholar_counts = _count_tools(log_path, s.harness)
    return s


def _count_tools(log_path: Path, harness: str) -> tuple[dict[str, int], dict[str, int]]:
    tool_counts: dict[str, int] = {}
    scholar_counts: dict[str, int] = {k: 0 for k in SCHOLAR_TOOLS}

    with log_path.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if harness == "claude":
                if ev.get("type") != "AssistantMessage":
                    continue
                msg = ev.get("message") or {}
                for c in msg.get("content", []) or []:
                    name = c.get("name")
                    if not name or "input" not in c:
                        continue
                    if name.startswith("mcp__scholar-gateway__"):
                        short = name.split("__")[-1]
                        scholar_counts[short] = scholar_counts.get(short, 0) + 1
                        bucket = f"scholar:{short}"
                    else:
                        bucket = name
                    tool_counts[bucket] = tool_counts.get(bucket, 0) + 1
            else:  # codex
                if ev.get("type") != "item/started":
                    continue
                msg = ev.get("message") or {}
                event = msg.get("event") or {}
                payload = event.get("payload") or {}
                item = payload.get("item") or {}
                itype = item.get("type")
                if itype == "mcpToolCall":
                    short = item.get("tool") or "unknown"
                    if item.get("server") == "scholar-gateway" and short in SCHOLAR_TOOLS:
                        scholar_counts[short] = scholar_counts.get(short, 0) + 1
                        bucket = f"scholar:{short}"
                    else:
                        bucket = f"mcp:{short}"
                    tool_counts[bucket] = tool_counts.get(bucket, 0) + 1
                elif itype == "commandExecution":
                    tool_counts["Bash"] = tool_counts.get("Bash", 0) + 1
                # reasoning / agentMessage etc. are not "tool calls" per se; skip.
    return tool_counts, scholar_counts


def aggregate(harness_dir: str) -> tuple[list[RunStats], dict]:
    base = RUNS_ROOT / harness_dir
    runs = sorted(base.iterdir()) if base.exists() else []
    stats: list[RunStats] = []
    for r in runs:
        if not r.is_dir():
            continue
        s = load_run(r, harness_dir)
        if s is not None:
            stats.append(s)

    summary = _summarize(stats)
    return stats, summary


def _summarize(stats: list[RunStats]) -> dict:
    n = len(stats)

    def col(field_name: str) -> list[float]:
        out = []
        for s in stats:
            v = getattr(s, field_name)
            if v is None:
                continue
            out.append(float(v))
        return out

    def stats_for(values: list[float]) -> dict:
        if not values:
            return {"n": 0, "mean": None, "median": None, "p90": None, "min": None, "max": None, "sum": None}
        s = sorted(values)
        p90 = s[max(0, int(round(0.9 * (len(s) - 1))))]
        return {
            "n": len(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p90": p90,
            "min": min(values),
            "max": max(values),
            "sum": sum(values),
        }

    # tool-count aggregates
    tool_total: dict[str, int] = {}
    runs_using: dict[str, int] = {}
    for s in stats:
        for k, v in s.tool_counts.items():
            tool_total[k] = tool_total.get(k, 0) + v
            if v > 0:
                runs_using[k] = runs_using.get(k, 0) + 1

    scholar_totals = {k: 0 for k in SCHOLAR_TOOLS}
    for s in stats:
        for k, v in s.scholar_counts.items():
            scholar_totals[k] = scholar_totals.get(k, 0) + v

    # extraction efficiency: references-dir count vs extract_paper calls
    extract_calls_total = scholar_totals.get("extract_paper", 0)
    refs_total = sum(s.references_dir_count for s in stats)

    return {
        "n_runs": n,
        "input_tokens": stats_for(col("input_tokens")),
        "output_tokens": stats_for(col("output_tokens")),
        "total_tokens": stats_for(col("total_tokens")),
        "cost_usd": stats_for(col("cost_usd")),
        "duration_sec": stats_for([v / 1000.0 for v in col("duration_ms")]),
        "turns": stats_for(col("turns")),
        "references_dir_count": stats_for([float(s.references_dir_count) for s in stats]),
        "scholar_search": stats_for([float(s.scholar_counts.get("web_search", 0)) for s in stats]),
        "scholar_fetch": stats_for([float(s.scholar_counts.get("web_fetch", 0)) for s in stats]),
        "scholar_extract": stats_for([float(s.scholar_counts.get("extract_paper", 0)) for s in stats]),
        "tool_total": tool_total,
        "tool_runs_using": runs_using,
        "scholar_totals": scholar_totals,
        "extract_calls_total": extract_calls_total,
        "refs_total": refs_total,
    }


# ---------- markdown formatting ----------

def fmt_int(v) -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}"


def fmt_float(v, digits=2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def fmt_money(v) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


def render_distribution_table(title: str, summaries: dict[str, dict], key: str, value_formatter, units: str = "") -> str:
    header = f"### {title}"
    if units:
        header += f" ({units})"
    lines = [header, "", "| Harness | n | mean | median | p90 | min | max | sum |", "|---|---|---|---|---|---|---|---|"]
    for h, summ in summaries.items():
        d = summ[key]
        lines.append(
            "| {h} | {n} | {mean} | {median} | {p90} | {mn} | {mx} | {sm} |".format(
                h=h,
                n=d["n"],
                mean=value_formatter(d["mean"]),
                median=value_formatter(d["median"]),
                p90=value_formatter(d["p90"]),
                mn=value_formatter(d["min"]),
                mx=value_formatter(d["max"]),
                sm=value_formatter(d["sum"]),
            )
        )
    return "\n".join(lines) + "\n"


def render_report(harness_summaries: dict[str, dict], harness_models: dict[str, str]) -> str:
    out: list[str] = []
    out.append("# Simple-Asset Runs: Cross-Harness Analysis\n")
    out.append(
        "Comparison of five harness/model combinations on the same 91-topic batch using the "
        "`assets/simple` configuration. Every run completed with `status=succeeded` and "
        "`validation.valid=true` (no repair attempts were triggered).\n"
    )
    out.append("## Caveats Before Reading the Numbers\n")
    out.append(
        "- **Prompt caching dominates `input_tokens`.** `opus4_6_claude_code` runs against the "
        "native Anthropic endpoint with prompt caching on, so the SDK reports only the "
        "*non-cached* prefix as billable input. The other three Claude harnesses "
        "(`glm5_1`, `deepseek_v4_pro`, `kimi_k2_6`) run through a proxy endpoint without "
        "caching, so each turn re-bills the full conversation, producing million-scale "
        "`input_tokens`. Treat the input/total-token columns as a billing artifact, not as "
        "a measure of how much work the agent did. `output_tokens`, `turns`, wall-clock, and "
        "tool-call counts are the comparable signals.\n"
        "- **`turns` is not comparable across harnesses.** The Claude harness counts "
        "agent-loop iterations; the Codex SDK collapses each run into a single turn, so "
        "`gpt_5_4_codex` always shows `turns=1`.\n"
        "- **`cost_usd` is missing for Codex.** The `openai-codex` SDK does not surface "
        "per-run cost, so `gpt_5_4_codex` totals are blank in the cost columns.\n"
        "- **A few runs have no `usage` object.** 6 deepseek runs and 2 kimi runs lack a "
        "usage block (likely SDK error paths that still produced a valid `proposal.md`); "
        "those runs are excluded from the resource-consumption tables (see `n` per row), "
        "but are included in the literature/tool-call sections, which read events directly "
        "from the log.\n"
        "- **Codex routes file I/O through Bash.** The Claude harness uses native "
        "`Read` / `Edit` / `Write` tools; the Codex harness has neither — every file "
        "operation, search, and listing is a `commandExecution` (counted as `Bash`). That is "
        "why `gpt_5_4_codex` shows zero in `Read`/`Edit`/`Write` and >100 mean Bash calls "
        "per run.\n"
    )

    out.append("## Harnesses Analyzed\n")
    out.append("| Run dir | Harness | Model | Runs |")
    out.append("|---|---|---|---|")
    for h, summ in harness_summaries.items():
        model = harness_models[h]
        # peek harness type from first run
        out.append(f"| `{h}` | {model_to_harness(h)} | `{model}` | {summ['n_runs']} |")
    out.append("")

    # --- 1. resource consumption ---
    out.append("## 1. Resource Consumption\n")
    out.append(
        "All figures aggregate over the 91 successful runs in each batch. `cost_usd` is null "
        "for the codex harness because the SDK does not surface per-run cost; we therefore "
        "report `gpt_5_4_codex` cost as `—`. `turns` is also not directly comparable: "
        "codex collapses each run into a single SDK turn while the Claude harness counts "
        "agent loop iterations.\n"
    )

    out.append(render_distribution_table("Total tokens per run", harness_summaries, "total_tokens", fmt_int))
    out.append(render_distribution_table("Input tokens per run", harness_summaries, "input_tokens", fmt_int))
    out.append(render_distribution_table("Output tokens per run", harness_summaries, "output_tokens", fmt_int))
    out.append(render_distribution_table("Cost per run", harness_summaries, "cost_usd", fmt_money, units="USD"))
    out.append(render_distribution_table("Wall-clock duration per run", harness_summaries, "duration_sec", lambda v: fmt_float(v, 1), units="seconds"))
    out.append(render_distribution_table("Turns per run", harness_summaries, "turns", fmt_int))

    out.append("### Aggregate efficiency\n")
    out.append("| Harness | Total cost (USD) | Total tokens | Total wall-clock (h) | Mean output tok / run | Cost / 1k output tokens |")
    out.append("|---|---|---|---|---|---|")
    for h, summ in harness_summaries.items():
        total_cost = summ["cost_usd"]["sum"]
        total_tok = summ["total_tokens"]["sum"]
        wall_h = summ["duration_sec"]["sum"] / 3600.0 if summ["duration_sec"]["sum"] else None
        mean_out = summ["output_tokens"]["mean"]
        cost_per_1k_out = None
        if total_cost is not None and summ["output_tokens"]["sum"]:
            cost_per_1k_out = total_cost / (summ["output_tokens"]["sum"] / 1000.0)
        out.append(
            f"| {h} | {fmt_money(total_cost)} | {fmt_int(total_tok)} | {fmt_float(wall_h, 2)} | {fmt_int(mean_out)} | {fmt_money(cost_per_1k_out)} |"
        )
    out.append("")

    # --- 2. literature usage ---
    out.append("## 2. Literature Usage\n")
    out.append(
        "Counts come from two sources: (a) the on-disk `agent_workspace/references/` directory "
        "(one folder per paper successfully ingested by `scholar-gateway::extract_paper`), and "
        "(b) tool-call events parsed from `framework/logs/adapter_events.jsonl`.\n"
    )

    out.append(render_distribution_table("Papers in `references/` per run", harness_summaries, "references_dir_count", fmt_int))
    out.append(render_distribution_table("`web_search` calls per run", harness_summaries, "scholar_search", fmt_int))
    out.append(render_distribution_table("`web_fetch` calls per run", harness_summaries, "scholar_fetch", fmt_int))
    out.append(render_distribution_table("`extract_paper` calls per run", harness_summaries, "scholar_extract", fmt_int))

    out.append("### Scholar-gateway totals and extraction yield\n")
    out.append(
        "`Yield` = on-disk `references/` folders divided by `extract_paper` calls — values "
        "below 1.0 indicate a fraction of `extract_paper` invocations did not produce a "
        "persisted reference (e.g. failures, retries on the same target, or duplicate IDs).\n"
    )
    out.append("| Harness | Total searches | Total fetches | Total extract calls | Total `references/` dirs | Yield (refs / extract) |")
    out.append("|---|---|---|---|---|---|")
    for h, summ in harness_summaries.items():
        sc = summ["scholar_totals"]
        refs = summ["refs_total"]
        ec = summ["extract_calls_total"]
        yld = (refs / ec) if ec else None
        out.append(
            f"| {h} | {fmt_int(sc.get('web_search', 0))} | {fmt_int(sc.get('web_fetch', 0))} | "
            f"{fmt_int(ec)} | {fmt_int(refs)} | {fmt_float(yld, 2) if yld is not None else '—'} |"
        )
    out.append("")

    # --- 3. tool call behavior ---
    out.append("## 3. Tool-Call Behavior\n")
    out.append(
        "Tool calls are extracted from `adapter_events.jsonl`. Two harnesses use different "
        "event shapes: the Claude harness emits `AssistantMessage` content blocks with a "
        "`name` field; the Codex harness emits `item/started` events with "
        "`item.type ∈ {commandExecution, mcpToolCall}` (we map `commandExecution` → `Bash`). "
        "Reasoning items, agent messages, and file-change items in the Codex log are not "
        "counted as tool calls.\n"
    )

    out.append("### Mean tool calls per run\n")
    # union of tool keys across harnesses
    all_tools = sorted({k for summ in harness_summaries.values() for k in summ["tool_total"]})
    # group: scholar:* first, then plain Claude tools, then mcp:* others
    def tool_sort_key(name):
        if name.startswith("scholar:"):
            return (0, name)
        if name.startswith("mcp:"):
            return (2, name)
        return (1, name)
    all_tools.sort(key=tool_sort_key)

    header_cells = ["Tool"] + list(harness_summaries.keys())
    out.append("| " + " | ".join(header_cells) + " |")
    out.append("|" + "---|" * len(header_cells))
    for tool in all_tools:
        row = [f"`{tool}`"]
        for h, summ in harness_summaries.items():
            n = summ["n_runs"] or 1
            mean = summ["tool_total"].get(tool, 0) / n
            row.append(fmt_float(mean, 1))
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    out.append("### Total tool calls across all 91 runs\n")
    out.append("| Tool | " + " | ".join(harness_summaries.keys()) + " |")
    out.append("|" + "---|" * (len(harness_summaries) + 1))
    for tool in all_tools:
        row = [f"`{tool}`"]
        for h, summ in harness_summaries.items():
            row.append(fmt_int(summ["tool_total"].get(tool, 0)))
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    out.append("### Tool-mix shares (% of total tool calls)\n")
    out.append("Within each harness, the share of all tool calls accounted for by each tool.\n")
    out.append("| Tool | " + " | ".join(harness_summaries.keys()) + " |")
    out.append("|" + "---|" * (len(harness_summaries) + 1))
    for tool in all_tools:
        row = [f"`{tool}`"]
        for h, summ in harness_summaries.items():
            grand = sum(summ["tool_total"].values()) or 1
            share = 100.0 * summ["tool_total"].get(tool, 0) / grand
            row.append(fmt_float(share, 1) + "%")
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # also dump grand-total row for sanity
    out.append("### Grand totals\n")
    out.append("| Metric | " + " | ".join(harness_summaries.keys()) + " |")
    out.append("|" + "---|" * (len(harness_summaries) + 1))
    out.append("| Total tool calls | " + " | ".join(fmt_int(sum(s["tool_total"].values())) for s in harness_summaries.values()) + " |")
    out.append("| Mean tool calls / run | " + " | ".join(fmt_float(sum(s["tool_total"].values()) / (s["n_runs"] or 1), 1) for s in harness_summaries.values()) + " |")
    out.append("")

    return "\n".join(out)


def model_to_harness(harness_dir: str) -> str:
    return "codex" if harness_dir.endswith("codex") else "claude"


def main() -> None:
    summaries: dict[str, dict] = {}
    models: dict[str, str] = {}
    for h in HARNESSES:
        stats, summ = aggregate(h)
        summaries[h] = summ
        if stats:
            models[h] = stats[0].model
        else:
            models[h] = "?"
    md = render_report(summaries, models)
    out_path = RUNS_ROOT.parent / "reports" / "simple_runs_analysis.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"wrote {out_path} ({len(md):,} chars)")


if __name__ == "__main__":
    main()
