"use strict";

const POLL_MS = 1500;
const RUNS_REFRESH_MS = 5000;
const RESULT_PREVIEW_CHARS = 800;

const state = {
  selectedRun: null,        // run_id (URL "run" param)
  selectedRunPath: null,    // for sidebar highlight when names differ from ids
  harness: null,            // 'claude' | 'codex' | null. Set from /api/run-info,
                            // sniffed from first event if missing, defaults to 'claude'.
  lastIndex: 0,
  stopped: false,
  pollTimer: null,
  runsTimer: null,
  lastPhase: null,
  // ---- Claude renderer state ----
  toolUseRows: new Map(), // tool_use_id -> row element
  toolUseNames: new Map(),// tool_use_id -> tool name (for downstream classification)
  taskIdToToolUseId: new Map(), // subagent task_id -> Agent tool_use_id (from task_started)
  lastToolName: null,     // name of most-recent tool_use we observed
  lastToolUseId: null,    // id of most-recent tool_use (for injected-text attachment)
  // ---- Codex renderer state ----
  itemRows: new Map(),    // codex item.id -> row element
  itemKinds: new Map(),   // codex item.id -> item.type (so item/completed knows the row's kind)
  // ---- Common turn / counters ----
  currentTurn: null,       // open turn { headNode, children, counts: {thinking,text,tools} }
  turnIndex: 0,            // monotonic 1-based turn counter
  counts: { events: 0, tools: 0, errors: 0, retries: 0, phases: 0 },
  filters: { thinking: true, system: false, retries: true },
  search: "",
};

const $ = (id) => document.getElementById(id);

function init() {
  $("filter-thinking").addEventListener("change", (e) => {
    state.filters.thinking = e.target.checked;
    applyFilters();
  });
  $("filter-system").addEventListener("change", (e) => {
    state.filters.system = e.target.checked;
    applyFilters();
  });
  $("filter-retries").addEventListener("change", (e) => {
    state.filters.retries = e.target.checked;
    applyFilters();
  });
  $("jump-timeline").addEventListener("click", () => {
    const target = document.getElementById("events-section");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("jump-stats").addEventListener("click", () => {
    const target = document.getElementById("stats-panel");
    if (target && !target.classList.contains("hidden")) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  $("search").addEventListener("input", (e) => {
    state.search = e.target.value.toLowerCase();
    applyFilters();
  });
  $("refresh-runs").addEventListener("click", () => loadRuns());

  // Read selected run from URL hash, e.g. #run=foo
  const initial = readRunFromHash();
  window.addEventListener("hashchange", () => {
    const r = readRunFromHash();
    if (r !== state.selectedRun) selectRun(r);
  });

  loadRuns().then((runs) => {
    if (initial) {
      selectRun(initial);
    } else if (runs.length > 0) {
      // Auto-select the newest run (first in the sorted list).
      selectRun(runs[0].run_id);
    } else {
      showEmptyState();
    }
  });
  // Periodically refresh the sidebar so newly-started runs show up.
  state.runsTimer = setInterval(loadRuns, RUNS_REFRESH_MS);

  if (typeof initStats === "function") initStats();
}

function readRunFromHash() {
  const m = window.location.hash.match(/run=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

async function loadRuns() {
  try {
    const resp = await fetch("/api/runs");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    $("runs-root").textContent = data.runs_root || "";
    $("runs-root").title = data.runs_root || "";
    renderRunsList(data.runs || []);
    return data.runs || [];
  } catch (err) {
    showBanner("could not load run list");
    return [];
  }
}

function renderRunsList(runs) {
  const list = $("runs-list");
  list.innerHTML = "";
  if (runs.length === 0) {
    const li = document.createElement("li");
    li.className = "runs-empty";
    li.textContent = "(no runs found)";
    list.appendChild(li);
    return;
  }
  for (const r of runs) {
    const li = document.createElement("li");
    li.className = "run-item";
    li.dataset.runId = r.run_id;
    if (r.run_id === state.selectedRun) li.classList.add("selected");

    const status = r.status || "unknown";

    const statusDot = el("span", "run-status-dot");
    statusDot.dataset.status = status;
    statusDot.title = status;
    li.appendChild(statusDot);

    const main = el("div", "run-main");
    const nameRow = el("div", "run-name-row");
    const name = el("div", "run-name");
    name.textContent = r.run_id;
    nameRow.appendChild(name);
    if (r.harness) {
      const badge = el("span", "harness-badge");
      badge.textContent = r.harness;
      badge.dataset.harness = r.harness;
      nameRow.appendChild(badge);
    }
    main.appendChild(nameRow);
    if (r.topic) {
      const topic = el("div", "run-topic");
      topic.textContent = r.topic;
      topic.title = r.topic;
      main.appendChild(topic);
    }
    const meta = el("div", "run-meta");
    const metaParts = [];
    if (r.started_at) metaParts.push(formatDateShort(r.started_at));
    if (r.model) metaParts.push(r.model.replace(/^global\.anthropic\./, "").replace(/^anthropic\./, ""));
    meta.textContent = metaParts.join(" · ");
    main.appendChild(meta);
    li.appendChild(main);

    li.addEventListener("click", () => selectRun(r.run_id));
    list.appendChild(li);
  }
}

function formatDateShort(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const da = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${mo}-${da} ${hh}:${mm}`;
}

function selectRun(runId) {
  if (!runId) return;
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
  state.selectedRun = runId;
  state.harness = null;
  state.lastIndex = 0;
  state.stopped = false;
  state.lastPhase = null;
  state.toolUseRows = new Map();
  state.toolUseNames = new Map();
  state.taskIdToToolUseId = new Map();
  state.lastToolName = null;
  state.lastToolUseId = null;
  state.itemRows = new Map();
  state.itemKinds = new Map();
  state.currentTurn = null;
  state.turnIndex = 0;
  state.counts = { events: 0, tools: 0, errors: 0, retries: 0, phases: 0 };
  updateCounts();

  // Update URL hash so reload preserves selection.
  window.location.hash = `run=${encodeURIComponent(runId)}`;

  // Highlight in sidebar
  for (const li of document.querySelectorAll("#runs-list .run-item")) {
    li.classList.toggle("selected", li.dataset.runId === runId);
  }

  // Reset main view
  $("events").innerHTML = "";
  $("empty-state").classList.add("hidden");
  $("events-section").classList.remove("hidden");
  $("toolbar").classList.remove("hidden");
  $("topic").textContent = "";

  if (typeof resetStatsForRun === "function") resetStatsForRun();

  loadRunInfo();
  poll();
}

function showEmptyState() {
  $("empty-state").classList.remove("hidden");
  if (typeof hideTraceSummary === "function") hideTraceSummary();
}

async function loadRunInfo() {
  if (!state.selectedRun) return;
  try {
    const resp = await fetch(`/api/run-info?run=${encodeURIComponent(state.selectedRun)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const info = await resp.json();
    $("run-id").textContent = info.run_id || state.selectedRun;
    $("model").textContent = info.model || "";
    setStatus(info.status || "unknown");
    if (info.topic) $("topic").textContent = info.topic;
    if (info.harness) state.harness = info.harness;
  } catch (err) {
    showBanner("could not load run info");
  }
}

function setStatus(status) {
  const el = $("status-badge");
  el.textContent = status;
  el.dataset.status = status;
  if (typeof refreshTraceSummaryStatus === "function") refreshTraceSummaryStatus();
}

async function poll() {
  if (state.stopped || !state.selectedRun) return;
  try {
    const resp = await fetch(
      `/api/events?run=${encodeURIComponent(state.selectedRun)}&from=${state.lastIndex}`
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    hideBanner();
    for (const ev of data.events) renderEvent(ev);
    state.lastIndex = data.next_index;
    updateCounts();
    if (data.file_complete && data.eof !== false) {
      state.stopped = true;
      loadRunInfo();
      return;
    }
    // If the server still has more lines to send (eof === false), drain
    // them back-to-back so the user doesn't wait POLL_MS per page on a
    // big finished run. Otherwise idle-poll.
    if (data.eof === false) {
      state.pollTimer = setTimeout(poll, 0);
      return;
    }
  } catch (err) {
    showBanner("connection lost — retrying");
  }
  state.pollTimer = setTimeout(poll, POLL_MS);
}

// ----------------------------------------------------------------------
// Top-level event dispatch — picks the renderer based on harness.
// ----------------------------------------------------------------------

function renderEvent(ev) {
  state.counts.events += 1;

  // First event: if harness wasn't supplied by run_record, sniff from the event.
  if (!state.harness) {
    state.harness = detectHarnessFromEvent(ev) || "claude";
  }

  handlePhase(ev);

  if (state.harness === "codex") {
    renderCodexEvent(ev);
  } else {
    renderClaudeEvent(ev);
  }
}

function handlePhase(ev) {
  if (state.lastPhase !== null && ev.phase != null && ev.phase !== state.lastPhase) {
    appendDivider(ev.phase);
    state.counts.phases += 1;
  } else if (state.lastPhase === null && ev.phase != null) {
    state.counts.phases += 1;
  }
  if (ev.phase != null) state.lastPhase = ev.phase;
}

function detectHarnessFromEvent(ev) {
  const t = ev?.type;
  if (typeof t !== "string") return null;
  // Codex-side markers
  if (t === "codex_turn_start") return "codex";
  if (t.startsWith("turn/")) return "codex";
  if (t.startsWith("item/")) return "codex";
  if (t === "thread/tokenUsage/updated") return "codex";
  if (t === "TurnResult") return "codex";
  if (t === "adapter_error") return "codex";
  // Claude-side markers
  if (t === "AssistantMessage" || t === "UserMessage" ||
      t === "SystemMessage" || t === "ResultMessage") return "claude";
  if (t.startsWith("Task") && t.endsWith("Message")) return "claude";
  return null;
}

// ----------------------------------------------------------------------
// Compact-row builder. Every event renders to one row of:
//   <div class="line">
//     <div class="line-head">  ← always visible, single line
//        <span class="ts"/> <span class="kind"/> <span class="summary"/>
//        <span class="status"/> <span class="caret"/>
//     </div>
//     <div class="line-body" hidden>  ← shown when row is expanded
//        ...full kv + result + raw json...
//     </div>
//   </div>
// ----------------------------------------------------------------------

function buildLine(meta) {
  // meta: { ts, kind, summary (DOM or string), status (string|null), body (DOM|null), expandable }
  const line = el("div", "line");
  const head = el("div", "line-head");

  const ts = el("span", "ts");
  ts.textContent = meta.ts || "";

  const kind = el("span", "kind");
  kind.textContent = meta.kind || "";

  const summary = el("span", "summary");
  if (typeof meta.summary === "string") {
    summary.textContent = meta.summary;
  } else if (meta.summary instanceof Node) {
    summary.appendChild(meta.summary);
  }

  const status = el("span", "status");
  if (meta.status) {
    status.textContent = meta.status;
    status.dataset.state = meta.statusState || "neutral";
  }

  const caret = el("span", "caret");
  caret.textContent = meta.expandable ? "▸" : "";

  head.appendChild(ts);
  head.appendChild(kind);
  head.appendChild(summary);
  head.appendChild(status);
  head.appendChild(caret);
  line.appendChild(head);

  if (meta.body) {
    const body = el("div", "line-body");
    body.hidden = true;
    body.appendChild(meta.body);
    line.appendChild(body);
    head.classList.add("clickable");
    head.addEventListener("click", () => {
      const open = !body.hidden ? false : true;
      body.hidden = !open;
      caret.textContent = open ? "▾" : "▸";
      line.classList.toggle("open", open);
    });
  }

  return line;
}

// ----------------------------------------------------------------------
// Shared body-section helpers — used by both renderers.
// ----------------------------------------------------------------------

const URL_KEYS = new Set(["url", "uri"]);
const PATH_KEYS = new Set(["file_path", "filePath", "path", "notebook_path", "cwd"]);
const CODE_KEYS = new Set(["command", "pattern", "query", "regex"]);

function buildKvBody(input) {
  const wrap = el("div", "kv-wrap");
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    const pre = el("pre");
    pre.textContent = stringifyShort(input);
    wrap.appendChild(pre);
    return wrap;
  }
  const keys = Object.keys(input);
  if (keys.length === 0) {
    const empty = el("div", "kv-empty");
    empty.textContent = "(no arguments)";
    wrap.appendChild(empty);
    return wrap;
  }
  const kv = el("div", "kv");
  for (const k of keys) {
    const kEl = el("div", "kv-k");
    kEl.textContent = k;
    const vEl = el("div", "kv-v");
    vEl.appendChild(renderValue(k, input[k]));
    kv.appendChild(kEl);
    kv.appendChild(vEl);
  }
  wrap.appendChild(kv);
  return wrap;
}

function renderValue(key, value) {
  if (value === null || value === undefined) {
    const span = el("span", "v-null");
    span.textContent = "null";
    return span;
  }
  if (typeof value === "boolean" || typeof value === "number") {
    const span = el("span", "v-scalar");
    span.textContent = String(value);
    return span;
  }
  if (Array.isArray(value) || (typeof value === "object" && value !== null)) {
    const pre = el("pre", "v-json");
    pre.textContent = JSON.stringify(value, null, 2);
    return pre;
  }
  const str = String(value);
  if (URL_KEYS.has(key)) {
    const a = document.createElement("a");
    a.className = "v-url";
    a.href = str;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = str;
    return a;
  }
  if (PATH_KEYS.has(key)) {
    const span = el("span", "v-path");
    span.textContent = str;
    return span;
  }
  if (CODE_KEYS.has(key)) {
    const c = el("code", "v-code");
    c.textContent = str;
    return c;
  }
  // long strings → preformatted block
  if (str.length > 200 || str.includes("\n")) {
    const pre = el("pre", "v-text");
    pre.textContent = str;
    return pre;
  }
  const span = el("span", "v-text-inline");
  span.textContent = str;
  return span;
}

function renderableResultText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((c) => {
        if (typeof c === "string") return c;
        if (c && typeof c === "object" && typeof c.text === "string") return c.text;
        return JSON.stringify(c);
      })
      .join("\n");
  }
  if (content === null || content === undefined) return "";
  return JSON.stringify(content, null, 2);
}

// Append a "result"-style section to a row body. Long outputs go into a
// <details> with a preview shown by default. Used for tool results, command
// output, mcp results, diffs.
function appendResultSection(body, text, opts) {
  opts = opts || {};
  const isError = !!opts.isError;
  const labelText = opts.label || (isError ? "result · error" : "result");
  const section = el("div", isError ? "section result-section error" : "section result-section");
  const label = el("div", "section-label");
  label.textContent = labelText;
  section.appendChild(label);

  if (text.length <= RESULT_PREVIEW_CHARS) {
    const pre = el("pre", "result-pre");
    pre.textContent = text;
    section.appendChild(pre);
  } else {
    const det = document.createElement("details");
    const sum = document.createElement("summary");
    sum.textContent = `${text.length} chars · click to show full`;
    det.appendChild(sum);
    const previewPre = el("pre", "result-pre");
    previewPre.textContent = text.slice(0, RESULT_PREVIEW_CHARS) + "…";
    section.appendChild(previewPre);
    const fullPre = el("pre", "result-pre");
    fullPre.textContent = text;
    fullPre.style.display = "none";
    det.appendChild(fullPre);
    det.addEventListener("toggle", () => {
      if (det.open) {
        previewPre.style.display = "none";
        fullPre.style.display = "";
      } else {
        previewPre.style.display = "";
        fullPre.style.display = "none";
      }
    });
    section.appendChild(det);
  }
  body.appendChild(section);
  return section;
}

// ----------------------------------------------------------------------
// DOM plumbing — turn grouping, dividers, filters.
// ----------------------------------------------------------------------

function appendRow(node, meta) {
  node.dataset.kind = meta.kind;
  if (meta.role) node.dataset.role = meta.role;

  const isThinking = meta.kind === "thinking";
  const isText = meta.kind === "text";
  const isTool = meta.kind === "tool-use";
  const isAssistantBlock = meta.kind === "assistant-block";
  const isTurnContent = isThinking || isText || isTool || isAssistantBlock;

  if (isTurnContent) {
    // A turn = (optional thinking) + (optional text) + (zero or more tools).
    // Once a tool has been seen, any new thinking OR text starts a new turn.
    const needNewTurn =
      isThinking ||
      !state.currentTurn ||
      ((isText) && state.currentTurn.counts.tools > 0);
    if (needNewTurn) {
      openTurn();
    }
    state.currentTurn.children.appendChild(node);
    if (isThinking) state.currentTurn.counts.thinking += 1;
    else if (isText) state.currentTurn.counts.text += 1;
    else if (isTool) state.currentTurn.counts.tools += 1;
    updateTurnHead(state.currentTurn);
  } else {
    closeTurn();
    $("events").appendChild(node);
  }

  applyFiltersTo(node);
  if (isNearBottom()) scrollToBottom();
}

function isNearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
}

function openTurn() {
  closeTurn();
  state.turnIndex += 1;
  const idx = state.turnIndex;

  const turn = el("section", "turn");

  const head = el("div", "turn-head");
  head.title = "click to fold/unfold this turn";
  const toggle = el("span", "turn-toggle");
  toggle.textContent = "▾";
  const label = el("span", "turn-label");
  label.textContent = `turn ${idx}`;
  const counts = el("span", "turn-counts");
  counts.textContent = "";
  head.appendChild(toggle);
  head.appendChild(label);
  head.appendChild(counts);

  const children = el("div", "turn-body");

  head.addEventListener("click", () => {
    const collapsed = children.classList.toggle("collapsed");
    toggle.textContent = collapsed ? "▸" : "▾";
    turn.classList.toggle("collapsed", collapsed);
  });

  turn.appendChild(head);
  turn.appendChild(children);
  $("events").appendChild(turn);

  state.currentTurn = {
    element: turn,
    head,
    children,
    countsEl: counts,
    counts: { thinking: 0, text: 0, tools: 0 },
  };
}

function updateTurnHead(turn) {
  const parts = [];
  if (turn.counts.thinking) parts.push(`${turn.counts.thinking} thinking`);
  if (turn.counts.text) parts.push(`${turn.counts.text} message${turn.counts.text === 1 ? "" : "s"}`);
  if (turn.counts.tools) parts.push(`${turn.counts.tools} tool${turn.counts.tools === 1 ? "" : "s"}`);
  turn.countsEl.textContent = parts.join(" · ");
}

function closeTurn() {
  if (!state.currentTurn) return;
  // If the turn ended up empty (shouldn't happen, but defensive), drop it.
  const total = state.currentTurn.counts.thinking
              + state.currentTurn.counts.text
              + state.currentTurn.counts.tools;
  if (total === 0) {
    state.currentTurn.element.remove();
    state.turnIndex -= 1;
  }
  state.currentTurn = null;
}

function appendDivider(phase) {
  closeTurn();
  const div = el("div", "phase-divider");
  div.textContent = phase;
  $("events").appendChild(div);
}

function applyFilters() {
  for (const node of document.querySelectorAll("#events .line")) applyFiltersTo(node);
}

function applyFiltersTo(node) {
  const kind = node.dataset.kind;
  let visible = true;
  if (kind === "thinking" && !state.filters.thinking) visible = false;
  // The 'system' filter hides low-signal session/status rows. We deliberately
  // keep 'system-compact' visible always — context compaction is meaningful
  // and infrequent.
  const isHideableSystem =
    kind === "system" || kind === "system-status" || kind === "system-other";
  if (isHideableSystem && !state.filters.system) visible = false;
  if (kind === "api-retry" && !state.filters.retries) visible = false;
  if (kind === "token-usage" && !state.filters.system) visible = false;
  if (visible && state.search) {
    visible = node.textContent.toLowerCase().includes(state.search);
  }
  node.style.display = visible ? "" : "none";
}

function updateCounts() {
  $("count-events").textContent = state.counts.events;
  $("count-tools").textContent = state.counts.tools;
  $("count-errors").textContent = state.counts.errors;
  $("count-retries").textContent = state.counts.retries;
  $("count-phases").textContent = state.counts.phases;
}

function showBanner(msg) {
  const b = $("banner");
  b.textContent = msg;
  b.classList.remove("hidden");
}

function hideBanner() {
  $("banner").classList.add("hidden");
}

function scrollToBottom() {
  window.scrollTo(0, document.body.scrollHeight);
}

function fmtTs(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  // Local time, HH:MM:SS in the browser's timezone.
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function escape(s) {
  if (s === undefined || s === null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function stringifyShort(v) {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function el(tag, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
}

document.addEventListener("DOMContentLoaded", init);
