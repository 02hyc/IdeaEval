"use strict";

// ----------------------------------------------------------------------
// Codex adapter event renderer.
//
// Codex events live in three families:
//
// 1. Adapter-level events emitted by research_ideation_agent's codex.py:
//      codex_turn_start  - one per turn at start
//      TurnResult        - one per turn at end, post-stream
//      adapter_error     - rare, on Python-side exceptions
//
// 2. Top-level SDK notifications keyed by `event.method`:
//      turn/started, turn/completed, turn/diff/updated, turn/plan/updated
//      thread/tokenUsage/updated, error
//
// 3. Per-item lifecycle events keyed by `item.type`:
//      item/started, item/completed, item/updated for each of:
//        userMessage, agentMessage, reasoning, mcpToolCall, commandExecution,
//        fileChange, collabAgentToolCall, contextCompaction, webSearch
//      plus the high-volume streaming deltas:
//        item/agentMessage/delta, item/commandExecution/outputDelta
//      and ancillary:
//        item/commandExecution/terminalInteraction
//        item/autoApprovalReview/{started,completed}
//
// Streaming deltas are dropped — `item/completed` carries the final text,
// so rendering the deltas would explode the timeline (deltas are 65% of
// events in the largest baselines).
//
// Shared building blocks (buildLine, buildKvBody, renderValue,
// renderableResultText, appendResultSection, appendRow, fmtTs, el,
// stringifyShort) live in app.js — this file references them as globals.
// ----------------------------------------------------------------------

function renderCodexEvent(ev) {
  const t = ev.type;

  // Adapter-level events
  if (t === "codex_turn_start") {
    appendRow(buildLineCodexTurnStart(ev), { kind: "codex-turn-start" });
    return;
  }
  if (t === "TurnResult") {
    appendRow(buildLineTurnResult(ev), { kind: "result" });
    return;
  }
  if (t === "adapter_error") {
    state.counts.errors += 1;
    appendRow(buildLineAdapterError(ev), { kind: "adapter-error" });
    return;
  }

  // Top-level SDK events
  if (t === "turn/started") {
    // No-op: codex_turn_start already announced the turn.
    return;
  }
  if (t === "turn/completed") {
    appendRow(buildLineCodexTurnCompleted(ev), { kind: "turn-completed" });
    return;
  }
  if (t === "turn/diff/updated") {
    appendRow(buildLineCodexTurnDiff(ev), { kind: "turn-diff" });
    return;
  }
  if (t === "turn/plan/updated") {
    appendRow(buildLineCodexTurnPlan(ev), { kind: "turn-plan" });
    return;
  }
  if (t === "thread/tokenUsage/updated") {
    appendRow(buildLineCodexTokenUsage(ev), { kind: "token-usage" });
    return;
  }
  if (t === "error") {
    state.counts.retries += 1;
    appendRow(buildLineCodexError(ev), { kind: "api-retry" });
    return;
  }

  // Streaming deltas — drop, intentional.
  if (t === "item/agentMessage/delta" || t === "item/commandExecution/outputDelta") {
    return;
  }

  // Ancillary item-level events
  if (t === "item/commandExecution/terminalInteraction") {
    attachTerminalInteraction(ev);
    return;
  }
  if (t === "item/autoApprovalReview/started") {
    appendRow(buildLineApprovalReviewStarted(ev), { kind: "approval-review" });
    return;
  }
  if (t === "item/autoApprovalReview/completed") {
    finalizeApprovalReview(ev);
    return;
  }

  // item/started, item/completed, item/updated
  if (t === "item/started" || t === "item/completed" || t === "item/updated") {
    handleItemEvent(ev, t);
    return;
  }

  // Anything else: passthrough so it's at least visible.
  appendRow(buildLineCodexUnknown(ev), { kind: "unknown" });
}

// ----------------------------------------------------------------------
// Item dispatch — keyed by item.type
// ----------------------------------------------------------------------

function handleItemEvent(ev, lifecycle) {
  const item = ev.message?.event?.payload?.item;
  if (!item || typeof item !== "object") {
    appendRow(buildLineCodexUnknown(ev), { kind: "unknown" });
    return;
  }
  const itemType = item.type;
  if (lifecycle === "item/started") {
    handleItemStarted(ev, item, itemType);
  } else {
    handleItemCompleted(ev, item, itemType, lifecycle);
  }
}

function handleItemStarted(ev, item, itemType) {
  if (itemType === "userMessage") {
    const row = buildLineCodexUserMessage(ev, item);
    rememberItemRow(item, row, "user");
    appendRow(row, { kind: "user", role: "injected" });
  } else if (itemType === "agentMessage") {
    const row = buildLineCodexAgentMessageStarted(ev, item);
    rememberItemRow(item, row, "text");
    appendRow(row, { kind: "text" });
  } else if (itemType === "reasoning") {
    const row = buildLineCodexReasoningStarted(ev, item);
    rememberItemRow(item, row, "thinking");
    appendRow(row, { kind: "thinking" });
  } else if (itemType === "mcpToolCall") {
    const row = buildLineCodexMcpToolCallStarted(ev, item);
    rememberItemRow(item, row, "tool-use");
    state.counts.tools += 1;
    appendRow(row, { kind: "tool-use" });
  } else if (itemType === "commandExecution") {
    const row = buildLineCodexCommandStarted(ev, item);
    rememberItemRow(item, row, "tool-use");
    state.counts.tools += 1;
    appendRow(row, { kind: "tool-use" });
  } else if (itemType === "fileChange") {
    const row = buildLineCodexFileChangeStarted(ev, item);
    rememberItemRow(item, row, "tool-use");
    state.counts.tools += 1;
    appendRow(row, { kind: "tool-use" });
  } else if (itemType === "collabAgentToolCall") {
    const row = buildLineCodexCollabAgentStarted(ev, item);
    rememberItemRow(item, row, "tool-use");
    state.counts.tools += 1;
    appendRow(row, { kind: "tool-use" });
  } else if (itemType === "contextCompaction") {
    const row = buildLineCodexCompactionStarted(ev, item);
    rememberItemRow(item, row, "system-compact");
    appendRow(row, { kind: "system-compact" });
  } else if (itemType === "webSearch") {
    const row = buildLineCodexWebSearchStarted(ev, item);
    rememberItemRow(item, row, "tool-use");
    state.counts.tools += 1;
    appendRow(row, { kind: "tool-use" });
  } else {
    // unknown item.type — render lossless
    const row = buildLineCodexItemUnknown(ev, item, "started");
    rememberItemRow(item, row, "codex-item");
    appendRow(row, { kind: "codex-item" });
  }
}

function handleItemCompleted(ev, item, itemType, lifecycle) {
  // Find the row that item/started created.
  const row = item.id ? state.itemRows.get(item.id) : null;
  if (itemType === "agentMessage") {
    finalizeAgentMessage(ev, item, row);
  } else if (itemType === "reasoning") {
    finalizeReasoning(ev, item, row);
  } else if (itemType === "mcpToolCall") {
    finalizeMcpToolCall(ev, item, row);
  } else if (itemType === "commandExecution") {
    finalizeCommand(ev, item, row);
  } else if (itemType === "fileChange") {
    finalizeFileChange(ev, item, row);
  } else if (itemType === "collabAgentToolCall") {
    finalizeCollabAgent(ev, item, row);
  } else if (itemType === "contextCompaction") {
    finalizeCompaction(ev, item, row);
  } else if (itemType === "webSearch") {
    finalizeWebSearch(ev, item, row);
  } else if (itemType === "userMessage") {
    // Already rendered on started; no-op on completed.
  } else if (row) {
    // Unknown item.type but we have a row — append a small completed marker.
    const body = row.querySelector(".line-body");
    if (body) appendResultSection(body, stringifyShort(item), { label: `${lifecycle} payload` });
  } else {
    appendRow(buildLineCodexItemUnknown(ev, item, lifecycle), { kind: "codex-item" });
  }
}

function rememberItemRow(item, row, kind) {
  if (item && item.id) {
    state.itemRows.set(item.id, row);
    state.itemKinds.set(item.id, kind);
  }
}

// ----------------------------------------------------------------------
// Adapter-level event builders
// ----------------------------------------------------------------------

function buildLineCodexTurnStart(ev) {
  const m = ev.message || {};
  const summary = `model=${m.model || "?"} · effort=${m.effort || "?"} · ${m.input_length || 0} chars`;

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "turn start";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["thread", m.thread_id],
    ["model", m.model],
    ["effort", m.effort],
    ["input length", m.input_length],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "turn-start",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineTurnResult(ev) {
  const m = ev.message || {};
  const usage = m.usage || {};
  const parts = [];
  if (m.duration_ms != null) parts.push(`${(m.duration_ms / 1000).toFixed(1)}s`);
  if (usage && usage.total != null && usage.total.totalTokens != null) {
    parts.push(`${usage.total.totalTokens.toLocaleString()} tok`);
  }
  const summary = parts.join(" · ") || (typeof m.status === "string" ? m.status : "");

  const body = el("div", "tool-body");

  const stats = el("div", "section");
  const statsLabel = el("div", "section-label");
  statsLabel.textContent = "stats";
  stats.appendChild(statsLabel);
  const kv = el("div", "kv");
  const totalTokens = usage?.total?.totalTokens;
  const inputTokens = usage?.total?.inputTokens;
  const outputTokens = usage?.total?.outputTokens;
  const cachedTokens = usage?.total?.cachedInputTokens;
  const reasoningTokens = usage?.total?.reasoningOutputTokens;
  const rows = [
    ["status", typeof m.status === "string" ? m.status : (m.status ? JSON.stringify(m.status) : null)],
    ["error", m.error ? "yes" : "no"],
    ["duration", m.duration_ms != null ? `${(m.duration_ms / 1000).toFixed(1)}s` : null],
    ["total tokens", totalTokens],
    ["input tokens", inputTokens],
    ["output tokens", outputTokens],
    ["reasoning tokens", reasoningTokens],
    ["cached tokens", cachedTokens],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  stats.appendChild(kv);
  body.appendChild(stats);

  if (typeof m.final_response === "string" && m.final_response.length > 0) {
    const section = el("div", "section");
    const label = el("div", "section-label");
    label.textContent = `final response · ${m.final_response.length} chars`;
    section.appendChild(label);
    const pre = el("pre", "result-pre");
    pre.textContent = m.final_response;
    section.appendChild(pre);
    body.appendChild(section);
  }

  const isError = !!m.error;
  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "final",
    summary,
    status: isError ? "ERR" : (typeof m.status === "string" ? m.status : "ok"),
    statusState: isError ? "error" : "ok",
    body,
    expandable: true,
  });
}

function buildLineAdapterError(ev) {
  const m = ev.message || {};
  const summary = `${m.error_type || "Error"}: ${m.error || ""}`.slice(0, 200);

  const body = el("div", "tool-body");
  const section = el("div", "section result-section error");
  const label = el("div", "section-label");
  label.textContent = "adapter error";
  section.appendChild(label);
  const pre = el("pre", "result-pre");
  pre.textContent = m.error || "";
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "adapter-error",
    summary,
    status: "ERR",
    statusState: "error",
    body,
    expandable: true,
  });
}

// ----------------------------------------------------------------------
// SDK top-level event builders
// ----------------------------------------------------------------------

function buildLineCodexTurnCompleted(ev) {
  const turn = ev.message?.event?.payload?.turn || {};
  const parts = [];
  if (typeof turn.status === "string") parts.push(turn.status);
  if (turn.durationMs != null) parts.push(`${(turn.durationMs / 1000).toFixed(1)}s`);
  const summary = parts.join(" · ");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "turn";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["id", turn.id],
    ["status", turn.status],
    ["startedAt", turn.startedAt],
    ["completedAt", turn.completedAt],
    ["duration", turn.durationMs != null ? `${turn.durationMs} ms` : null],
    ["error", turn.error],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  const isError = !!turn.error;
  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "turn-completed",
    summary,
    status: isError ? "ERR" : (turn.status === "completed" ? "ok" : (turn.status || "ok")),
    statusState: isError ? "error" : (turn.status === "completed" ? "ok" : "neutral"),
    body,
    expandable: true,
  });
}

function buildLineCodexTurnDiff(ev) {
  const diff = ev.message?.event?.payload?.diff;
  const text = typeof diff === "string" ? diff : stringifyShort(diff);
  // Summarise: count changed files and total + / - lines (cheap regex scan).
  let added = 0, removed = 0;
  const fileMatches = (text.match(/^diff --git /gm) || []).length;
  for (const line of text.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) added += 1;
    else if (line.startsWith("-")) removed += 1;
  }
  const parts = [];
  if (fileMatches) parts.push(`${fileMatches} file${fileMatches === 1 ? "" : "s"}`);
  parts.push(`+${added} -${removed}`);
  const summary = parts.join(" · ");

  const body = el("div", "tool-body");
  appendResultSection(body, text, { label: `diff · ${text.length} chars` });

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "turn-diff",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineCodexTurnPlan(ev) {
  const payload = ev.message?.event?.payload || {};
  const plan = Array.isArray(payload.plan) ? payload.plan : [];
  const inProgress = plan.filter(p => p && p.status === "inProgress").length;
  const pending = plan.filter(p => p && p.status === "pending").length;
  const completed = plan.filter(p => p && p.status === "completed").length;
  const summary = `${plan.length} steps · ${completed} done · ${inProgress} active · ${pending} pending`;

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "plan";
  section.appendChild(label);
  const ul = el("ul", "codex-plan");
  for (const step of plan) {
    if (!step || typeof step !== "object") continue;
    const li = el("li", "codex-plan-step");
    li.dataset.status = step.status || "unknown";
    const status = el("span", "codex-plan-status");
    status.textContent = step.status || "?";
    const text = el("span", "codex-plan-text");
    text.textContent = step.step || "";
    li.appendChild(status);
    li.appendChild(text);
    ul.appendChild(li);
  }
  section.appendChild(ul);
  body.appendChild(section);

  if (payload.explanation) {
    const note = el("div", "section");
    const noteLabel = el("div", "section-label");
    noteLabel.textContent = "explanation";
    note.appendChild(noteLabel);
    const pre = el("pre", "result-pre");
    pre.textContent = payload.explanation;
    note.appendChild(pre);
    body.appendChild(note);
  }

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "turn-plan",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineCodexTokenUsage(ev) {
  const u = ev.message?.event?.payload?.tokenUsage || {};
  const last = u.last || {};
  const total = u.total || {};
  const summary =
    `last: in ${formatNum(last.inputTokens)} · out ${formatNum(last.outputTokens)} · total ${formatNum(last.totalTokens)}` +
    (total.totalTokens != null ? ` · all ${formatNum(total.totalTokens)}` : "");

  const body = el("div", "tool-body");

  const lastSection = el("div", "section");
  const lastLabel = el("div", "section-label");
  lastLabel.textContent = "last call";
  lastSection.appendChild(lastLabel);
  const kvLast = el("div", "kv");
  for (const [k, v] of Object.entries(last)) {
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kvLast.appendChild(kEl); kvLast.appendChild(vEl);
  }
  lastSection.appendChild(kvLast);
  body.appendChild(lastSection);

  const totalSection = el("div", "section");
  const totalLabel = el("div", "section-label");
  totalLabel.textContent = "cumulative";
  totalSection.appendChild(totalLabel);
  const kvTotal = el("div", "kv");
  for (const [k, v] of Object.entries(total)) {
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kvTotal.appendChild(kEl); kvTotal.appendChild(vEl);
  }
  totalSection.appendChild(kvTotal);
  body.appendChild(totalSection);

  if (u.modelContextWindow != null) {
    const ctx = el("div", "section");
    const ctxLabel = el("div", "section-label");
    ctxLabel.textContent = "context window";
    ctx.appendChild(ctxLabel);
    const kv = el("div", "kv");
    const kEl = el("div", "kv-k"); kEl.textContent = "tokens";
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue("tokens", u.modelContextWindow));
    kv.appendChild(kEl); kv.appendChild(vEl);
    ctx.appendChild(kv);
    body.appendChild(ctx);
  }

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "token-usage",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineCodexError(ev) {
  const payload = ev.message?.event?.payload || {};
  const err = payload.error || {};
  const willRetry = !!payload.willRetry;
  const parts = [];
  if (err.message) parts.push(err.message);
  if (willRetry) parts.push("will retry");
  const summary = parts.join(" · ");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "error detail";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["message", err.message],
    ["details", err.additionalDetails],
    ["http status", err.codexErrorInfo?.responseStreamDisconnected?.httpStatusCode],
    ["will retry", willRetry ? "yes" : "no"],
    ["thread", payload.threadId],
    ["turn", payload.turnId],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "api-retry",
    summary,
    status: willRetry ? "RETRY" : "ERR",
    statusState: willRetry ? "warn" : "error",
    body,
    expandable: true,
  });
}

// ----------------------------------------------------------------------
// Item-type builders — userMessage / agentMessage / reasoning
// ----------------------------------------------------------------------

function extractUserMessageText(item) {
  const content = item.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((c) => (c && typeof c === "object" && typeof c.text === "string") ? c.text : "")
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function buildLineCodexUserMessage(ev, item) {
  const text = extractUserMessageText(item);
  const summary = text.replace(/\s+/g, " ").slice(0, 200) + (text.length > 200 ? "…" : "");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const pre = el("pre", "text-pre");
  pre.textContent = text;
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "user",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineCodexAgentMessageStarted(ev, item) {
  // Streaming starts with empty text; we'll fill it in on completed.
  const text = typeof item.text === "string" ? item.text : "";
  const summary = text ? text.replace(/\s+/g, " ").slice(0, 200) + (text.length > 200 ? "…" : "") : "(streaming…)";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const pre = el("pre", "text-pre");
  pre.textContent = text;
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "assistant",
    summary,
    status: item.phase ? item.phase : null,
    statusState: "neutral",
    body,
    expandable: true,
  });
}

function finalizeAgentMessage(ev, item, row) {
  if (!row) {
    // No started row: render fresh as a completed message.
    appendRow(buildLineCodexAgentMessageStarted(ev, item), { kind: "text" });
    return;
  }
  const text = typeof item.text === "string" ? item.text : "";
  // Replace summary
  const summary = row.querySelector(".summary");
  if (summary) {
    summary.textContent = text.replace(/\s+/g, " ").slice(0, 200) + (text.length > 200 ? "…" : "");
  }
  // Replace text-pre body with final text
  const pre = row.querySelector("pre.text-pre");
  if (pre) pre.textContent = text;
  // Update status if phase set
  if (item.phase) {
    const status = row.querySelector(".status");
    if (status) {
      status.textContent = item.phase;
      status.dataset.state = "neutral";
    }
  }
}

function buildLineCodexReasoningStarted(ev, item) {
  // Reasoning streams as deltas which we drop; the started item has empty
  // content/summary. The final summary text (if any) is filled in on completed.
  const text = extractReasoningText(item);
  const encrypted = Boolean(item.encrypted_content);
  const summary = text
    ? text.replace(/\s+/g, " ").slice(0, 160) + (text.length > 160 ? "…" : "")
    : encrypted
      ? "(reasoning summary unavailable; encrypted)"
      : "(reasoning…)";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = text
    ? `reasoning · ${text.length} chars`
    : encrypted
      ? "reasoning · encrypted; summary unavailable"
      : "reasoning";
  section.appendChild(label);
  const pre = el("pre", "thinking-pre");
  pre.textContent = text || (encrypted ? "Reasoning tokens were present, but Codex logged only encrypted reasoning content and no public summary for this turn." : "");
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "thinking",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function extractReasoningText(item) {
  const summary = item.summary;
  if (Array.isArray(summary)) {
    const parts = summary.map((s) => {
      if (typeof s === "string") return s;
      if (s && typeof s === "object" && typeof s.text === "string") return s.text;
      return "";
    }).filter(Boolean);
    if (parts.length) return parts.join("\n");
  }
  if (typeof item.text === "string") return item.text;
  return "";
}

function finalizeReasoning(ev, item, row) {
  if (!row) {
    appendRow(buildLineCodexReasoningStarted(ev, item), { kind: "thinking" });
    return;
  }
  const text = extractReasoningText(item);
  const encrypted = Boolean(item.encrypted_content);
  if (!text) {
    if (encrypted) {
      const summary = row.querySelector(".summary");
      if (summary) summary.textContent = "(reasoning summary unavailable; encrypted)";
      const pre = row.querySelector("pre.thinking-pre");
      if (pre) pre.textContent = "Reasoning tokens were present, but Codex logged only encrypted reasoning content and no public summary for this turn.";
      const label = row.querySelector(".section-label");
      if (label) label.textContent = "reasoning · encrypted; summary unavailable";
    }
    return;
  }
  const summary = row.querySelector(".summary");
  if (summary) summary.textContent = text.replace(/\s+/g, " ").slice(0, 160) + (text.length > 160 ? "…" : "");
  const pre = row.querySelector("pre.thinking-pre");
  if (pre) pre.textContent = text;
  const label = row.querySelector(".section-label");
  if (label) label.textContent = `reasoning · ${text.length} chars`;
}

// ----------------------------------------------------------------------
// Item-type builders — mcpToolCall
// ----------------------------------------------------------------------

function buildLineCodexMcpToolCallStarted(ev, item) {
  const tool = item.tool || "?";
  const server = item.server || "?";
  const args = item.arguments || {};
  const argSummary = mcpArgSummary(tool, args);
  const summary = argSummary || `${server}.${tool}`;

  const body = el("div", "tool-body");
  const inputBlock = el("div", "section");
  const inputLabel = el("div", "section-label");
  inputLabel.textContent = "arguments";
  inputBlock.appendChild(inputLabel);
  inputBlock.appendChild(buildKvBody(args));
  body.appendChild(inputBlock);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: tool,
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function mcpArgSummary(tool, args) {
  if (!args || typeof args !== "object") return "";
  if (typeof tool === "string") {
    if (tool.endsWith("web_search")) return args.query || "";
    if (tool.endsWith("web_fetch")) return args.url || "";
    if (tool.endsWith("extract_paper")) return args.url || "";
    if (tool.endsWith("summarize_paper")) return `${args.paper_dir || ""} -> ${args.output_path || ""}`;
  }
  const candidates = ["query", "url", "file_path", "path", "pattern", "subject", "command"];
  for (const k of candidates) {
    if (args[k] !== undefined) return String(args[k]);
  }
  const firstKey = Object.keys(args)[0];
  return firstKey ? `${firstKey}=${String(args[firstKey]).slice(0, 80)}` : "";
}

function finalizeMcpToolCall(ev, item, row) {
  if (!row) return;
  const isError = !!item.error;
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = isError ? "ERR" : "ok";
    status.dataset.state = isError ? "error" : "ok";
  }
  if (item.durationMs != null) {
    // Append duration to the kind label as a small meta hint.
    const kind = row.querySelector(".kind");
    if (kind && !kind.textContent.endsWith("s")) {
      kind.title = `${(item.durationMs / 1000).toFixed(1)}s`;
    }
  }
  const body = row.querySelector(".line-body");
  if (!body) return;
  let resultText;
  if (isError) {
    resultText = typeof item.error === "string" ? item.error : stringifyShort(item.error);
  } else {
    resultText = renderableResultText(item.result?.content ?? item.result);
  }
  appendResultSection(body, resultText, { isError });
}

// ----------------------------------------------------------------------
// Item-type builders — commandExecution
// ----------------------------------------------------------------------

function buildLineCodexCommandStarted(ev, item) {
  const cmd = item.command || "";
  const summary = cmd.replace(/\s+/g, " ").slice(0, 200);

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "command";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["command", cmd],
    ["cwd", item.cwd],
    ["pid", item.processId],
    ["source", item.source],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "Bash",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function finalizeCommand(ev, item, row) {
  if (!row) return;
  const isError = item.exitCode != null && item.exitCode !== 0;
  const status = row.querySelector(".status");
  if (status) {
    if (item.exitCode != null) {
      status.textContent = isError ? `exit ${item.exitCode}` : "ok";
      status.dataset.state = isError ? "error" : "ok";
    }
  }
  const body = row.querySelector(".line-body");
  if (!body) return;
  const out = typeof item.aggregatedOutput === "string"
    ? item.aggregatedOutput
    : stringifyShort(item.aggregatedOutput);
  if (out) {
    const durLabel = item.durationMs != null
      ? `output · ${out.length} chars · ${(item.durationMs / 1000).toFixed(1)}s`
      : `output · ${out.length} chars`;
    appendResultSection(body, out, { isError, label: durLabel });
  }
}

function attachTerminalInteraction(ev) {
  const data = ev.message?.event?.payload || {};
  const itemId = data.itemId;
  if (!itemId) return;
  const row = state.itemRows.get(itemId);
  if (!row) return;
  const body = row.querySelector(".line-body");
  if (!body) return;
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "terminal stdin";
  section.appendChild(label);
  const pre = el("pre", "result-pre");
  pre.textContent = data.stdin || "(empty stdin)";
  section.appendChild(pre);
  body.appendChild(section);
}

// ----------------------------------------------------------------------
// Item-type builders — fileChange
// ----------------------------------------------------------------------

function buildLineCodexFileChangeStarted(ev, item) {
  const changes = Array.isArray(item.changes) ? item.changes : [];
  const summary = changes.length
    ? `${changes.length} change${changes.length === 1 ? "" : "s"}`
    : "(no changes)";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = `${changes.length} change(s)`;
  section.appendChild(label);
  const ul = el("ul", "codex-changes");
  for (const c of changes) {
    if (!c || typeof c !== "object") continue;
    const li = el("li", "codex-change");
    const path = el("span", "codex-change-path");
    path.textContent = c.path || c.filePath || "(unknown path)";
    li.appendChild(path);
    if (c.kind || c.changeType) {
      const kind = el("span", "codex-change-kind");
      kind.textContent = c.kind || c.changeType;
      li.appendChild(kind);
    }
    ul.appendChild(li);
  }
  section.appendChild(ul);
  body.appendChild(section);

  // If diffs are inline, surface them as a foldable section.
  for (const c of changes) {
    if (c && typeof c.diff === "string") {
      appendResultSection(body, c.diff, { label: `diff · ${c.path || ""}`.trim() });
    }
  }

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "Edit",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function finalizeFileChange(ev, item, row) {
  if (!row) return;
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = "ok";
    status.dataset.state = "ok";
  }
}

// ----------------------------------------------------------------------
// Item-type builders — collabAgentToolCall (subagent)
// ----------------------------------------------------------------------

function buildLineCodexCollabAgentStarted(ev, item) {
  const prompt = typeof item.prompt === "string" ? item.prompt : "";
  const summary = prompt.replace(/\s+/g, " ").slice(0, 200) || "(subagent)";

  const body = el("div", "tool-body");

  const promptSection = el("div", "section");
  const promptLabel = el("div", "section-label");
  promptLabel.textContent = `prompt · ${prompt.length} chars`;
  promptSection.appendChild(promptLabel);
  const pre = el("pre", "result-pre");
  pre.textContent = prompt;
  promptSection.appendChild(pre);
  body.appendChild(promptSection);

  appendCollabAgentStateSection(body, item);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "Agent",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function appendCollabAgentStateSection(body, item) {
  const states = item.agentsStates;
  if (!states || typeof states !== "object") return;
  const keys = Object.keys(states);
  if (keys.length === 0) return;
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = `subagent states (${keys.length})`;
  section.appendChild(label);
  const kv = el("div", "kv");
  for (const k of keys) {
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, states[k]));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);
}

function finalizeCollabAgent(ev, item, row) {
  if (!row) return;
  // Update model field on the kind label
  const states = item.agentsStates || {};
  const allStatuses = Object.values(states)
    .map((s) => s && s.status)
    .filter(Boolean);
  let overall = "ok";
  if (allStatuses.some(s => s === "failed" || s === "error")) overall = "error";
  else if (allStatuses.some(s => s === "running" || s === "pendingInit")) overall = "neutral";
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = overall === "error" ? "ERR" : (allStatuses.length === 1 ? allStatuses[0] : "ok");
    status.dataset.state = overall;
  }
  const body = row.querySelector(".line-body");
  if (body) appendCollabAgentStateSection(body, item);
}

// ----------------------------------------------------------------------
// Item-type builders — contextCompaction
// ----------------------------------------------------------------------

function buildLineCodexCompactionStarted(ev, item) {
  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "system-compact",
    summary: "compaction started",
    status: "running",
    statusState: "warn",
    body: null,
    expandable: false,
  });
}

function finalizeCompaction(ev, item, row) {
  if (!row) {
    // Render a one-shot completed line.
    appendRow(buildLineCodexCompactionCompleted(ev, item), { kind: "system-compact" });
    return;
  }
  const summary = row.querySelector(".summary");
  if (summary) summary.textContent = "compaction completed";
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = "ok";
    status.dataset.state = "ok";
  }
}

function buildLineCodexCompactionCompleted(ev, item) {
  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "system-compact",
    summary: "compaction completed",
    status: "ok",
    statusState: "ok",
    body: null,
    expandable: false,
  });
}

// ----------------------------------------------------------------------
// Item-type builders — webSearch
// ----------------------------------------------------------------------

function buildLineCodexWebSearchStarted(ev, item) {
  const query = typeof item.query === "string" ? item.query : "";
  const summary = query || "(web search)";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "web search";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["query", query],
    ["action", item.action ? JSON.stringify(item.action) : null],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "WebSearch",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function finalizeWebSearch(ev, item, row) {
  if (!row) return;
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = "ok";
    status.dataset.state = "ok";
  }
  const body = row.querySelector(".line-body");
  if (!body) return;
  if (item.action && item.action.type === "openPage" && item.action.url) {
    const section = el("div", "section");
    const label = el("div", "section-label");
    label.textContent = "result";
    section.appendChild(label);
    const kv = el("div", "kv");
    const kEl = el("div", "kv-k"); kEl.textContent = "url";
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue("url", item.action.url));
    kv.appendChild(kEl); kv.appendChild(vEl);
    section.appendChild(kv);
    body.appendChild(section);
  }
}

// ----------------------------------------------------------------------
// Item-type builders — autoApprovalReview
// ----------------------------------------------------------------------

function buildLineApprovalReviewStarted(ev) {
  const payload = ev.message?.event?.payload || {};
  const action = payload.action || {};
  const summary = action.command
    ? `approve? ${action.command}`.slice(0, 200)
    : "(approval review)";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "approval review";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["type", action.type],
    ["command", action.command],
    ["cwd", action.cwd],
    ["source", action.source],
    ["targetItemId", payload.targetItemId],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  const row = buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "approval",
    summary,
    status: "pending",
    statusState: "warn",
    body,
    expandable: true,
  });
  if (payload.reviewId) {
    state.itemRows.set(`review:${payload.reviewId}`, row);
  }
  return row;
}

function finalizeApprovalReview(ev) {
  const payload = ev.message?.event?.payload || {};
  const review = payload.review || {};
  const reviewId = payload.reviewId;
  const row = reviewId ? state.itemRows.get(`review:${reviewId}`) : null;
  if (!row) {
    appendRow(buildLineApprovalReviewCompletedFresh(ev), { kind: "approval-review" });
    return;
  }
  const isDenied = review.status === "denied" || review.status === "error";
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = review.status || "done";
    status.dataset.state = isDenied ? "error" : "ok";
  }
  const body = row.querySelector(".line-body");
  if (body) {
    const section = el("div", "section");
    const label = el("div", "section-label");
    label.textContent = "decision";
    section.appendChild(label);
    const kv = el("div", "kv");
    const rows = [
      ["status", review.status],
      ["risk", review.riskLevel],
      ["source", payload.decisionSource],
      ["userAuthorization", review.userAuthorization],
      ["rationale", review.rationale],
    ];
    for (const [k, v] of rows) {
      if (v === undefined || v === null || v === "") continue;
      const kEl = el("div", "kv-k"); kEl.textContent = k;
      const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
      kv.appendChild(kEl); kv.appendChild(vEl);
    }
    section.appendChild(kv);
    body.appendChild(section);
  }
}

function buildLineApprovalReviewCompletedFresh(ev) {
  const payload = ev.message?.event?.payload || {};
  const review = payload.review || {};
  const action = payload.action || {};
  const summary = `${review.status || "decision"}: ${action.command || ""}`.slice(0, 200);
  const isDenied = review.status === "denied" || review.status === "error";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "approval review (orphan)";
  section.appendChild(label);
  const pre = el("pre", "v-json");
  pre.textContent = stringifyShort(payload);
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "approval",
    summary,
    status: review.status || "done",
    statusState: isDenied ? "error" : "ok",
    body,
    expandable: true,
  });
}

// ----------------------------------------------------------------------
// Fallback builders
// ----------------------------------------------------------------------

function buildLineCodexUnknown(ev) {
  const body = el("div", "tool-body");
  const pre = el("pre", "v-json");
  pre.textContent = stringifyShort(ev);
  body.appendChild(pre);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: ev.type || "unknown",
    summary: "(unrecognised codex event)",
    status: null,
    body,
    expandable: true,
  });
}

function buildLineCodexItemUnknown(ev, item, lifecycle) {
  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = `${lifecycle} · item.type=${item?.type || "?"}`;
  section.appendChild(label);
  const pre = el("pre", "v-json");
  pre.textContent = stringifyShort(item);
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "codex-item",
    summary: `unknown item: ${item?.type || "?"}`,
    status: null,
    body,
    expandable: true,
  });
}

function formatNum(n) {
  if (n == null) return "?";
  if (typeof n === "number") return n.toLocaleString();
  return String(n);
}
