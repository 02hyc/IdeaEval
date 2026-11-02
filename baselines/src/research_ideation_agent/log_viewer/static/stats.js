"use strict";

// Stats panel — sits at the bottom of the main column, after the event timeline.
//
// Layout: a row of compact KPI tiles (events / tokens / cost), then a
// full-width "tools" table where each row has a horizontal bar showing
// the relative call count. Server returns a flexible namespace
// {stats: {<id>: {kind, label, source, unsupported_reason, data}}};
// renderers below opt-in by id, with a tiny generic fallback.

const STATS_RUNNING_REFRESH_MS = 5000;

const statsState = {
  payload: null,
  fetchInFlight: false,
  refreshTimer: null,
  lastRunFetched: null,
};

function initStats() {
  const refresh = document.getElementById("stats-refresh-btn");
  if (refresh) refresh.addEventListener("click", () => loadStats(true));
  const jump = document.getElementById("trace-summary-stats-btn");
  if (jump) {
    jump.addEventListener("click", () => {
      const target = document.getElementById("stats-panel");
      if (target && !target.classList.contains("hidden")) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }
}

function resetStatsForRun() {
  if (statsState.refreshTimer) {
    clearTimeout(statsState.refreshTimer);
    statsState.refreshTimer = null;
  }
  statsState.payload = null;
  statsState.fetchInFlight = false;
  statsState.lastRunFetched = null;
  const panel = document.getElementById("stats-panel");
  if (panel) panel.classList.remove("hidden");
  const bar = document.getElementById("trace-summary-bar");
  if (bar) bar.classList.remove("hidden");
  renderStatsPlaceholder();
  loadStats(true);
}

function hideTraceSummary() {
  const bar = document.getElementById("trace-summary-bar");
  if (bar) bar.classList.add("hidden");
}

function refreshTraceSummaryStatus() {
  renderTraceSummary(statsState.payload);
}

async function loadStats(force) {
  if (!state.selectedRun) return;
  if (statsState.fetchInFlight) return;
  if (!force && statsState.lastRunFetched === state.selectedRun && statsState.payload) return;
  statsState.fetchInFlight = true;
  try {
    const resp = await fetch(`/api/stats?run=${encodeURIComponent(state.selectedRun)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const payload = await resp.json();
    statsState.payload = payload;
    statsState.lastRunFetched = state.selectedRun;
    renderStats(payload);
    scheduleStatsRefresh(payload);
  } catch (err) {
    renderStatsError(String(err.message || err));
  } finally {
    statsState.fetchInFlight = false;
  }
}

function scheduleStatsRefresh(payload) {
  if (statsState.refreshTimer) {
    clearTimeout(statsState.refreshTimer);
    statsState.refreshTimer = null;
  }
  if (!payload || payload.file_complete) return;
  statsState.refreshTimer = setTimeout(() => loadStats(true), STATS_RUNNING_REFRESH_MS);
}

// ----------------------------------------------------------------------
// Render
// ----------------------------------------------------------------------

function renderStatsPlaceholder() {
  const root = document.getElementById("stats-grid");
  if (!root) return;
  root.innerHTML = "";
  root.appendChild(makeKpiRow([
    ["events", "—"],
    ["elapsed", "—"],
    ["tokens", "—"],
    ["cost", "—"],
    ["tool calls", "—"],
  ]));
  const meta = document.getElementById("stats-computed-at");
  if (meta) meta.textContent = "";
  renderTraceSummary(null);
}

function renderStatsError(msg) {
  const root = document.getElementById("stats-grid");
  if (!root) return;
  root.innerHTML = "";
  const note = el("div", "stats-error");
  note.textContent = `stats unavailable: ${msg}`;
  root.appendChild(note);
  renderTraceSummary(null, msg);
}

function renderStats(payload) {
  const root = document.getElementById("stats-grid");
  if (!root) return;
  root.innerHTML = "";

  const stats = (payload && payload.stats) || {};
  const tokensEntry = stats.tokens;
  const costEntry = stats.cost;
  const toolsEntry = stats.tool_counts;
  const durationEntry = stats.duration;
  const tokens = tokensEntry && tokensEntry.data;
  const cost = costEntry && costEntry.data;
  const tools = toolsEntry && toolsEntry.data;
  const duration = durationEntry && durationEntry.data;

  // KPI strip
  root.appendChild(makeKpiRow([
    ["events", payload && typeof payload.event_total === "number" ? formatTokens(payload.event_total) : "—"],
    ["elapsed", duration && duration.elapsed_ms != null ? formatDurationMs(duration.elapsed_ms) : "—",
      durationEntry && durationEntry.unsupported_reason ? durationEntry.unsupported_reason : null],
    ["tokens", tokens && tokens.total != null ? formatTokens(tokens.total) : "—",
      tokens && tokens.total != null ? tokenSubLine(tokens) : null],
    ["cost", cost && cost.total_cost_usd != null ? formatCost(cost.total_cost_usd) : "—",
      costEntry && costEntry.unsupported_reason ? costEntry.unsupported_reason : null],
    ["tool calls", tools && tools.totals ? String(tools.totals.all) : "—",
      tools && tools.totals && tools.inside_subagent_supported
        ? `${tools.totals.top_level} top · ${tools.totals.inside_subagent} sub`
        : (toolsEntry && toolsEntry.unsupported_reason ? "top-level only" : null)],
  ]));

  // Token / cost detail row (only if we have any data)
  if ((tokens && tokens.total != null) || (cost && cost.total_cost_usd != null)) {
    const detail = renderTokenBreakdown(stats.tokens, stats.cost);
    if (detail) root.appendChild(detail);
  }

  // Tools table
  if (tools && tools.rows && tools.rows.length > 0) {
    root.appendChild(renderToolsTable(stats.tool_counts));
  }

  // Anything else the server returned that we don't have a special-case
  // renderer for — show it generically so a new server provider doesn't
  // need a frontend change to be visible.
  const known = new Set(["tokens", "cost", "tool_counts", "duration"]);
  for (const id of Object.keys(stats)) {
    if (known.has(id)) continue;
    root.appendChild(renderGenericCard(id, stats[id]));
  }

  // Footer
  const meta = document.getElementById("stats-computed-at");
  if (meta) {
    const stamp = payload && payload.computed_at ? payload.computed_at : "";
    meta.textContent = stamp;
  }
  renderTraceSummary(payload);
}

// ----------------------------------------------------------------------
// KPI strip — a single horizontal row of small metric tiles.
// ----------------------------------------------------------------------

function makeKpiRow(items) {
  const row = el("div", "kpi-row");
  for (const [label, value, hint] of items) {
    const tile = el("div", "kpi-tile");
    const lbl = el("div", "kpi-label");
    lbl.textContent = label;
    const val = el("div", "kpi-value");
    val.textContent = value;
    tile.appendChild(lbl);
    tile.appendChild(val);
    if (hint) {
      const h = el("div", "kpi-hint");
      h.textContent = hint;
      tile.appendChild(h);
    }
    row.appendChild(tile);
  }
  return row;
}

function tokenSubLine(t) {
  const parts = [];
  if (t.input != null) parts.push(`in ${formatTokens(t.input)}`);
  if (t.output != null) parts.push(`out ${formatTokens(t.output)}`);
  return parts.join(" · ");
}

function renderTraceSummary(payload, errorMessage) {
  const bar = document.getElementById("trace-summary-bar");
  const runEl = document.getElementById("trace-summary-run-id");
  const statusEl = document.getElementById("trace-summary-status");
  const itemsEl = document.getElementById("trace-summary-items");
  if (!bar || !runEl || !statusEl || !itemsEl) return;

  const runLabel = state.selectedRun || "—";
  runEl.textContent = runLabel;
  runEl.title = runLabel;

  const topStatus = document.getElementById("status-badge");
  const status = topStatus ? (topStatus.dataset.status || topStatus.textContent || "unknown") : "unknown";
  statusEl.textContent = status;
  statusEl.dataset.status = status;

  const stats = (payload && payload.stats) || {};
  const duration = stats.duration && stats.duration.data;
  const tokens = stats.tokens && stats.tokens.data;
  const cost = stats.cost && stats.cost.data;
  const tools = stats.tool_counts && stats.tool_counts.data;
  const items = [
    ["elapsed", duration && duration.elapsed_ms != null ? formatDurationMs(duration.elapsed_ms) : "—"],
    ["cost", cost && cost.total_cost_usd != null ? formatCost(cost.total_cost_usd) : "—"],
    ["tokens", tokens && tokens.total != null ? formatTokens(tokens.total) : "—"],
    ["tools", tools && tools.totals ? String(tools.totals.all) : "—"],
    ["events", payload && typeof payload.event_total === "number" ? formatTokens(payload.event_total) : "—"],
  ];

  itemsEl.innerHTML = "";
  for (const [label, value] of items) {
    const item = el("div", "trace-summary-item");
    const key = el("span", "trace-summary-label");
    key.textContent = label;
    const val = el("span", "trace-summary-value");
    val.textContent = errorMessage && label === "elapsed" ? "error" : value;
    item.appendChild(key);
    item.appendChild(val);
    itemsEl.appendChild(item);
  }

  bar.classList.remove("hidden");
}

// ----------------------------------------------------------------------
// Token / cost breakdown — a small definition-list-style block under the KPIs.
// ----------------------------------------------------------------------

function renderTokenBreakdown(tokensEntry, costEntry) {
  const block = el("div", "stats-detail");
  const t = (tokensEntry && tokensEntry.data) || {};
  const c = (costEntry && costEntry.data) || {};

  const items = [];
  if (t.input != null) items.push(["input", formatTokens(t.input)]);
  if (t.output != null) items.push(["output", formatTokens(t.output)]);
  if (t.reasoning != null) items.push(["reasoning", formatTokens(t.reasoning)]);
  if (t.cached_input != null) items.push(["cache read", formatTokens(t.cached_input)]);
  if (t.cache_creation != null) items.push(["cache create", formatTokens(t.cache_creation)]);

  if (items.length === 0 && c.total_cost_usd == null) return null;

  for (const [k, v] of items) {
    const cell = el("div", "stats-detail-cell");
    const lbl = el("span", "stats-detail-k");
    lbl.textContent = k;
    const val = el("span", "stats-detail-v");
    val.textContent = v;
    cell.appendChild(lbl);
    cell.appendChild(val);
    block.appendChild(cell);
  }

  // Source caption (small, right-aligned)
  const sources = [];
  if (tokensEntry && tokensEntry.source) sources.push(`tokens: ${tokensEntry.source}`);
  if (costEntry && costEntry.source) sources.push(`cost: ${costEntry.source}`);
  if (sources.length) {
    const note = el("div", "stats-detail-note");
    note.textContent = sources.join("  ·  ");
    block.appendChild(note);
  }
  return block;
}

// ----------------------------------------------------------------------
// Tools table — name | bar | top | sub.
// ----------------------------------------------------------------------

function renderToolsTable(entry) {
  const block = el("div", "stats-block");
  const head = el("div", "stats-block-head");
  const title = el("span", "");
  title.textContent = "tool calls";
  head.appendChild(title);
  const data = entry.data || {};
  const totals = data.totals || { top_level: 0, inside_subagent: 0, all: 0 };
  const right = el("span", "stats-block-totals");
  right.textContent = data.inside_subagent_supported
    ? `${totals.top_level} top · ${totals.inside_subagent} sub · ${totals.all} all`
    : `${totals.all} calls`;
  head.appendChild(right);
  block.appendChild(head);

  const max = data.rows.reduce((m, r) => Math.max(m, r.top_level + r.inside_subagent), 0) || 1;
  const allTotal = totals.all || 0;

  const list = el("div", "tool-rows");
  for (const r of data.rows) {
    const row = el("div", "tool-row");
    const total = r.top_level + r.inside_subagent;

    const name = el("div", "tool-name");
    name.textContent = r.name;
    row.appendChild(name);

    const barWrap = el("div", "tool-bar-wrap");
    const bar = el("div", "tool-bar");
    const topShare = total > 0 ? (r.top_level / max) : 0;
    const subShare = total > 0 ? (r.inside_subagent / max) : 0;
    if (topShare > 0) {
      const seg = el("div", "tool-bar-seg tool-bar-top");
      seg.style.width = (topShare * 100).toFixed(2) + "%";
      bar.appendChild(seg);
    }
    if (subShare > 0) {
      const seg = el("div", "tool-bar-seg tool-bar-sub");
      seg.style.width = (subShare * 100).toFixed(2) + "%";
      bar.appendChild(seg);
    }
    barWrap.appendChild(bar);
    row.appendChild(barWrap);

    const numTop = el("div", "tool-num tool-num-top");
    numTop.textContent = String(r.top_level);
    row.appendChild(numTop);

    if (data.inside_subagent_supported) {
      const numSub = el("div", "tool-num tool-num-sub");
      numSub.textContent = r.inside_subagent ? String(r.inside_subagent) : "·";
      row.appendChild(numSub);
    }

    const share = el("div", "tool-num tool-num-pct");
    share.textContent = allTotal > 0 ? formatShare(total / allTotal) : "—";
    row.appendChild(share);

    list.appendChild(row);
  }

  // Header row matching the data columns.
  const header = el("div", "tool-row tool-row-head");
  const h1 = el("div", "tool-name");
  h1.textContent = "tool";
  header.appendChild(h1);
  const h2 = el("div", "tool-bar-wrap");
  header.appendChild(h2);
  const h3 = el("div", "tool-num");
  h3.textContent = "top";
  header.appendChild(h3);
  if (data.inside_subagent_supported) {
    const h4 = el("div", "tool-num");
    h4.textContent = "sub";
    header.appendChild(h4);
  }
  const hShare = el("div", "tool-num");
  hShare.textContent = "share";
  header.appendChild(hShare);
  list.insertBefore(header, list.firstChild);

  // Mark whether we render 4 or 5 columns so CSS lays out correctly.
  list.classList.add(data.inside_subagent_supported ? "tool-rows-5" : "tool-rows-4");

  block.appendChild(list);

  if (entry.unsupported_reason) {
    const note = el("div", "stats-block-note");
    note.textContent = entry.unsupported_reason;
    block.appendChild(note);
  }
  return block;
}

function renderGenericCard(id, entry) {
  const block = el("div", "stats-block");
  const head = el("div", "stats-block-head");
  head.textContent = entry.label || id;
  block.appendChild(head);
  const pre = el("pre", "stats-generic-pre");
  pre.textContent = JSON.stringify(entry.data, null, 2);
  block.appendChild(pre);
  return block;
}

// ----------------------------------------------------------------------
// Formatting
// ----------------------------------------------------------------------

function formatTokensOr(n) {
  return n == null ? "—" : formatTokens(n);
}

function formatTokens(n) {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function formatCost(n) {
  if (n == null) return "—";
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(4)}`;
}

function formatDurationMs(ms) {
  if (ms == null || isNaN(ms)) return "—";
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}

function formatShare(frac) {
  if (frac == null || isNaN(frac)) return "—";
  const pct = frac * 100;
  if (pct === 0) return "0%";
  if (pct < 0.1) return "<0.1%";
  if (pct < 10) return `${pct.toFixed(1)}%`;
  return `${pct.toFixed(0)}%`;
}
