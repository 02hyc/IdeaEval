"use strict";

// ----------------------------------------------------------------------
// Claude Code adapter event renderer.
//
// Handles event types AssistantMessage / UserMessage / SystemMessage /
// ResultMessage / Task*Message. Shared building blocks (buildLine,
// buildKvBody, renderValue, renderableResultText, appendRow, fmtTs, el,
// escape, stringifyShort) live in app.js — this file references them as
// globals.
// ----------------------------------------------------------------------

function renderClaudeEvent(ev) {
  // Skip events emitted from inside a running Agent (subagent) tool call.
  // The subagent's own AssistantMessage/UserMessage events carry
  // message.parent_tool_use_id pointing at the Agent tool_use that spawned
  // them; rendering them on the main timeline duplicates the activity that
  // already shows up inside the parent Agent row via SystemMessage
  // task_started / task_progress / task_notification events.
  if (ev.message && ev.message.parent_tool_use_id) {
    return;
  }

  if (ev.type === "SystemMessage") {
    const subtype = ev.message?.subtype;
    if (subtype === "api_retry") {
      state.counts.retries += 1;
      appendRow(buildLineApiRetry(ev), { kind: "api-retry" });
    } else if (subtype === "task_started" || subtype === "task_progress" || subtype === "task_notification" || subtype === "task_updated") {
      // Mirror the top-level Task* path: if attachment fails (no parent Agent
      // row in scope), render the event on the timeline rather than dropping it.
      if (!attachSubagentEvent(ev)) {
        appendRow(buildLineSubagentFallback(ev), { kind: "subagent" });
      }
    } else if (subtype === "status") {
      appendRow(buildLineSystemStatus(ev), { kind: "system-status" });
    } else if (subtype === "compact_boundary") {
      appendRow(buildLineSystemCompactBoundary(ev), { kind: "system-compact" });
    } else if (subtype === "init") {
      appendRow(buildLineSystem(ev), { kind: "system" });
    } else {
      appendRow(buildLineSystemGeneric(ev), { kind: "system-other" });
    }
    return;
  }
  if (isTaskMessage(ev)) {
    if (!attachSubagentEvent(ev)) {
      appendRow(buildLineSubagentFallback(ev), { kind: "subagent" });
    }
    return;
  }
  if (ev.type === "ResultMessage") {
    appendRow(buildLineResult(ev), { kind: "result" });
    return;
  }
  if (ev.type === "AssistantMessage") {
    renderAssistant(ev);
    return;
  }
  if (ev.type === "UserMessage") {
    renderUser(ev);
    return;
  }
  appendRow(buildLineUnknown(ev), { kind: "unknown" });
}

function renderAssistant(ev) {
  const content = ev.message?.content;
  if (!Array.isArray(content)) return;
  for (const block of content) {
    if (block.thinking !== undefined) {
      appendRow(buildLineThinking(ev, block.thinking), { kind: "thinking" });
    } else if (block.text !== undefined) {
      appendRow(buildLineText(ev, block.text, "assistant"), { kind: "text" });
    } else if (block.name !== undefined && block.input !== undefined) {
      const row = buildLineToolUse(ev, block);
      state.toolUseRows.set(block.id, row);
      state.toolUseNames.set(block.id, block.name);
      state.lastToolName = block.name;
      state.lastToolUseId = block.id;
      state.counts.tools += 1;
      appendRow(row, { kind: "tool-use" });
    } else if (block.tool_use_id !== undefined) {
      // Some SDK paths emit ToolResultBlock inside AssistantMessage.content
      // (e.g. wrapped subagent output). Attach to the matching tool-use row
      // rather than rendering as an "unhandled" panel.
      attachToolResult(block);
      if (block.is_error) state.counts.errors += 1;
    } else {
      appendRow(buildLineUnknownBlock(ev, block, "assistant"), { kind: "assistant-block" });
    }
  }
}

function renderUser(ev) {
  const content = ev.message?.content;
  if (!Array.isArray(content)) return;
  for (const block of content) {
    if (block.tool_use_id !== undefined) {
      attachToolResult(block);
      if (block.is_error) state.counts.errors += 1;
    } else if (block.text !== undefined) {
      // UserMessage.text is almost never an actual user turn during an
      // autonomous run — it's harness-injected content. Most commonly: a Skill
      // tool just resolved and the harness is now feeding the skill's body
      // back to the model. Attach it onto the previous tool row instead of
      // creating a "user" row that suggests a human spoke.
      if (state.lastToolName === "Skill" && state.lastToolUseId) {
        const row = state.toolUseRows.get(state.lastToolUseId);
        if (row) {
          attachInjectedTextToToolRow(row, block.text, "skill content");
          continue;
        }
      }
      appendRow(
        buildLineText(ev, block.text, "injected"),
        { kind: "text", role: "injected" }
      );
    } else {
      appendRow(buildLineUnknownBlock(ev, block, "user"), { kind: "user-block" });
    }
  }
}

function attachInjectedTextToToolRow(row, text, label) {
  const body = row.querySelector(".line-body");
  if (!body) return;
  const section = el("div", "section");
  const sectionLabel = el("div", "section-label");
  sectionLabel.textContent = label;
  section.appendChild(sectionLabel);
  const pre = el("pre", "result-pre");
  pre.textContent = text;
  section.appendChild(pre);
  body.appendChild(section);
  row.classList.add("has-injected");
}

// ----------------------------------------------------------------------
// Subagent SystemMessage events (task_started / task_progress / task_notification)
//
// These are emitted while an `Agent` tool call is running. They share the
// tool_use_id of that Agent call. We attach them as a "subagent activity"
// panel inside that Agent row, instead of rendering them on the timeline.
// ----------------------------------------------------------------------

function ensureSubagentPanel(row) {
  // Lazily create a subagent activity section inside the Agent row's body.
  let panel = row.querySelector(".subagent-panel");
  if (panel) return panel;
  const body = row.querySelector(".line-body");
  if (!body) return null;
  const section = el("div", "section subagent-panel");
  const label = el("div", "section-label");
  label.textContent = "subagent activity";
  section.appendChild(label);

  const status = el("div", "subagent-status");
  status.textContent = "(waiting)";
  section.appendChild(status);

  const list = el("ul", "subagent-events");
  section.appendChild(list);

  body.appendChild(section);
  row.classList.add("has-subagent");
  return section;
}

function attachSubagentEvent(ev) {
  const data = ev.message?.data || {};
  const subtype = ev.message?.subtype;

  // Resolve the parent Agent tool_use_id. task_started/task_progress/task_notification
  // carry it directly; task_updated only carries task_id, so we look it up via the
  // mapping recorded at task_started time.
  let tuid = data.tool_use_id;
  if (!tuid && data.task_id) {
    tuid = state.taskIdToToolUseId.get(data.task_id);
  }
  if (!tuid) return false;

  if (subtype === "task_started" && data.task_id) {
    state.taskIdToToolUseId.set(data.task_id, tuid);
  }

  const row = state.toolUseRows.get(tuid);
  if (!row) return false;  // Agent tool row hasn't been seen yet (shouldn't happen)
  const panel = ensureSubagentPanel(row);
  if (!panel) return false;
  const list = panel.querySelector(".subagent-events");
  const status = panel.querySelector(".subagent-status");

  if (subtype === "task_started") {
    status.textContent = `running · ${data.subagent_type || "subagent"}`;
    status.dataset.state = "running";
    if (data.description) {
      const li = el("li", "subagent-line started");
      li.innerHTML = `<span class="ts">${escape(fmtTs(ev.timestamp))}</span> <span class="ev-kind">started</span> <span class="ev-detail">${escape(data.description)}</span>`;
      list.appendChild(li);
    }
  } else if (subtype === "task_progress") {
    const tool = data.last_tool_name || "(no tool)";
    const detail = data.description || "";
    const usage = data.usage || {};
    const tokens = usage.total_tokens != null ? `${usage.total_tokens.toLocaleString()} tok` : "";
    const duration = usage.duration_ms != null ? `${(usage.duration_ms / 1000).toFixed(1)}s` : "";
    const meta = [tokens, duration].filter(Boolean).join(" · ");
    const li = el("li", "subagent-line progress");
    li.innerHTML = `
      <span class="ts">${escape(fmtTs(ev.timestamp))}</span>
      <span class="ev-tool">${escape(tool)}</span>
      <span class="ev-detail">${escape(detail)}</span>
      <span class="ev-meta">${escape(meta)}</span>
    `;
    list.appendChild(li);
  } else if (subtype === "task_notification") {
    const usage = data.usage || {};
    const tools = usage.tool_uses != null ? `${usage.tool_uses} tools` : "";
    const tokens = usage.total_tokens != null ? `${usage.total_tokens.toLocaleString()} tok` : "";
    const duration = usage.duration_ms != null ? `${(usage.duration_ms / 1000).toFixed(1)}s` : "";
    const meta = [tools, tokens, duration].filter(Boolean).join(" · ");
    status.textContent = `${data.status || "done"} · ${meta}`;
    status.dataset.state = data.status === "completed" ? "ok" : "neutral";
    const li = el("li", "subagent-line completed");
    li.innerHTML = `
      <span class="ts">${escape(fmtTs(ev.timestamp))}</span>
      <span class="ev-kind">${escape(data.status || "done")}</span>
      <span class="ev-detail">${escape(data.summary || "")}</span>
      <span class="ev-meta">${escape(meta)}</span>
    `;
    list.appendChild(li);
  } else if (subtype === "task_updated") {
    // Backwards-compatible completion signal. Carries only `patch` with
    // {status, end_time}; tool_use_id is absent so we resolved it via task_id above.
    const patch = data.patch || {};
    const newStatus = patch.status || "updated";
    status.textContent = newStatus;
    status.dataset.state = newStatus === "completed" ? "ok" : "neutral";
    const li = el("li", "subagent-line completed");
    li.innerHTML = `
      <span class="ts">${escape(fmtTs(ev.timestamp))}</span>
      <span class="ev-kind">${escape(newStatus)}</span>
    `;
    list.appendChild(li);
  }
  return true;
}

function isTaskMessage(ev) {
  return ev.type === "TaskStartedMessage" ||
    ev.type === "TaskProgressMessage" ||
    ev.type === "TaskNotificationMessage" ||
    ev.type === "TaskUpdatedMessage";
}

// ----------------------------------------------------------------------
// Tool name helpers
// ----------------------------------------------------------------------

function shortToolName(name) {
  if (typeof name !== "string") return String(name ?? "");
  if (name.startsWith("mcp__")) {
    const rest = name.slice("mcp__".length);
    const parts = rest.split("__");
    return parts.slice(1).join("__"); // drop server prefix; e.g. web_search
  }
  return name;
}

const PRIMARY_KEY_BY_TOOL = {
  Bash: "command",
  Read: "file_path",
  Write: "file_path",
  Edit: "file_path",
  Grep: "pattern",
  Glob: "pattern",
  TaskCreate: "subject",
  TaskUpdate: null, // custom: combines taskId + status
  TaskGet: "taskId",
  Skill: "skill",
};

function summarizeToolUse(name, input) {
  // Returns a short string: the most informative single field.
  if (typeof name !== "string") return "";
  if (!input || typeof input !== "object") return "";
  // mcp tools — special-case common shapes
  if (name.endsWith("web_search")) return input.query || "";
  if (name.endsWith("web_fetch")) return input.url || "";
  if (name.endsWith("extract_paper")) return input.url || "";
  if (name.endsWith("summarize_paper")) return `${input.paper_dir || ""} -> ${input.output_path || ""}`;

  if (name === "TaskUpdate") {
    const parts = [];
    if (input.taskId !== undefined) parts.push(`#${input.taskId}`);
    if (input.status) parts.push(`→ ${input.status}`);
    if (input.subject) parts.push(input.subject);
    return parts.join(" ");
  }

  const primaryKey = PRIMARY_KEY_BY_TOOL[name];
  if (primaryKey && input[primaryKey] !== undefined) {
    return String(input[primaryKey]);
  }
  // Generic fallback: try a few common keys, else first key
  const candidates = ["query", "url", "file_path", "path", "pattern", "subject", "command"];
  for (const k of candidates) {
    if (input[k] !== undefined) return String(input[k]);
  }
  const firstKey = Object.keys(input)[0];
  return firstKey ? `${firstKey}=${String(input[firstKey]).slice(0, 80)}` : "";
}

// ----------------------------------------------------------------------
// Per-event-type line builders
// ----------------------------------------------------------------------

function buildLineToolUse(ev, block) {
  const summary = summarizeToolUse(block.name, block.input || {});
  const body = el("div", "tool-body");
  const inputBlock = el("div", "section");
  const inputLabel = el("div", "section-label");
  inputLabel.textContent = "input";
  inputBlock.appendChild(inputLabel);
  inputBlock.appendChild(buildKvBody(block.input || {}));
  body.appendChild(inputBlock);

  // Result section is appended later by attachToolResult.

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: shortToolName(block.name),
    summary: summary,
    status: null, // filled by attachToolResult
    body,
    expandable: true,
  });
}

function attachToolResult(block) {
  const row = state.toolUseRows.get(block.tool_use_id);
  const isError = !!block.is_error;
  if (!row) {
    appendRow(buildLineOrphanResult(block), { kind: "tool-result-orphan" });
    return;
  }
  // Update inline status
  const status = row.querySelector(".status");
  if (status) {
    status.textContent = isError ? "ERR" : "ok";
    status.dataset.state = isError ? "error" : "ok";
  }
  // Append result section to the body
  const body = row.querySelector(".line-body");
  if (!body) return;
  const text = renderableResultText(block.content);
  appendResultSection(body, text, { isError });
}

function buildLineSystem(ev) {
  const data = ev.message?.data || {};
  const summary = `model=${data.model || "?"} · ${(data.tools || []).length} tools · ${(data.mcp_servers || []).length} mcp`;

  const body = el("div", "tool-body");

  const overview = el("div", "section");
  const overviewLabel = el("div", "section-label");
  overviewLabel.textContent = "session";
  overview.appendChild(overviewLabel);
  const kv = el("div", "kv");
  const rows = [
    ["model", data.model],
    ["session", data.session_id],
    ["cwd", data.cwd],
    ["tools", `${(data.tools || []).length} available`],
    ["mcp", (data.mcp_servers || []).map((m) => `${m.name} (${m.status})`).join(", ") || "none"],
    ["permission", data.permissionMode],
    ["agents", `${(data.agents || []).length} available`],
    ["skills", `${(data.skills || []).length} available`],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  overview.appendChild(kv);
  body.appendChild(overview);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "session-init",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineSystemStatus(ev) {
  const data = ev.message?.data || {};
  const status = data.status || "(no status)";
  const summary = `status=${status}`;

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "system status";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["status", data.status],
    ["session", data.session_id],
    ["uuid", data.uuid],
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
    kind: "system-status",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineSystemCompactBoundary(ev) {
  const data = ev.message?.data || {};
  const meta = data.compact_metadata || {};
  const parts = [];
  if (meta.trigger) parts.push(meta.trigger);
  if (meta.pre_tokens != null && meta.post_tokens != null) {
    parts.push(`${meta.pre_tokens.toLocaleString()} → ${meta.post_tokens.toLocaleString()} tok`);
  }
  if (meta.duration_ms != null) parts.push(`${(meta.duration_ms / 1000).toFixed(1)}s`);
  const preserved = meta.preserved_messages?.uuids;
  if (Array.isArray(preserved)) parts.push(`${preserved.length} preserved`);
  const summary = parts.join(" · ") || "compact boundary";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "compact metadata";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["trigger", meta.trigger],
    ["pre tokens", meta.pre_tokens],
    ["post tokens", meta.post_tokens],
    ["duration", meta.duration_ms != null ? `${meta.duration_ms} ms` : null],
    ["preserved", Array.isArray(preserved) ? `${preserved.length} message(s)` : null],
    ["session", data.session_id],
    ["uuid", data.uuid],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  section.appendChild(kv);
  body.appendChild(section);

  if (meta.preserved_segment || meta.preserved_messages) {
    const detail = el("div", "section");
    const detailLabel = el("div", "section-label");
    detailLabel.textContent = "preserved segment";
    detail.appendChild(detailLabel);
    const pre = el("pre", "v-json");
    pre.textContent = JSON.stringify(
      { preserved_segment: meta.preserved_segment, preserved_messages: meta.preserved_messages },
      null,
      2,
    );
    detail.appendChild(pre);
    body.appendChild(detail);
  }

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "system-compact",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineSystemGeneric(ev) {
  // Fallback for SystemMessage subtypes we don't recognise. Don't pretend to
  // be 'session-init' — surface the subtype and a JSON dump of data so the
  // viewer is at least lossless.
  const subtype = ev.message?.subtype || "system";
  const data = ev.message?.data || {};
  const summary = subtype;

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = `system ${subtype} payload`;
  section.appendChild(label);
  const pre = el("pre", "v-json");
  pre.textContent = stringifyShort(data);
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: `system-${subtype}`,
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineApiRetry(ev) {
  const data = ev.message?.data || {};
  const attempt = data.attempt;
  const maxRetries = data.max_retries;
  const errStatus = data.error_status;
  const delayMs = data.retry_delay_ms;
  const errMsg = data.error;

  const parts = [];
  if (errStatus) parts.push(`HTTP ${errStatus}`);
  if (attempt && maxRetries) parts.push(`attempt ${attempt}/${maxRetries}`);
  else if (attempt) parts.push(`attempt ${attempt}`);
  if (delayMs != null) parts.push(`retry in ${(delayMs / 1000).toFixed(1)}s`);
  const summary = parts.join(" · ");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "retry detail";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["status", errStatus],
    ["attempt", attempt != null ? `${attempt}${maxRetries ? ` of ${maxRetries}` : ""}` : null],
    ["delay", delayMs != null ? `${delayMs} ms` : null],
    ["session", data.session_id],
    ["error", errMsg],
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
    status: "RETRY",
    statusState: "warn",
    body,
    expandable: true,
  });
}

function buildLineSubagentFallback(ev) {
  const data = ev.message?.data || {};
  const subtype = ev.message?.subtype || "task";
  const usage = data.usage || {};
  const tool = data.last_tool_name ? shortToolName(data.last_tool_name) : "";
  const description = data.description || data.subagent_type || "";
  const tools = usage.tool_uses != null ? `${usage.tool_uses} tools` : "";
  const tokens = usage.total_tokens != null ? `${usage.total_tokens.toLocaleString()} tok` : "";
  const duration = usage.duration_ms != null ? `${(usage.duration_ms / 1000).toFixed(1)}s` : "";
  const meta = [tool, tools, tokens, duration].filter(Boolean).join(" · ");
  const summary = [description, meta].filter(Boolean).join(" · ");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = "subagent event";
  section.appendChild(label);
  const kv = el("div", "kv");
  const rows = [
    ["type", ev.type],
    ["subtype", subtype],
    ["subagent", data.subagent_type],
    ["description", data.description],
    ["task_id", data.task_id],
    ["tool_use_id", data.tool_use_id],
    ["last_tool", data.last_tool_name],
    ["tools", usage.tool_uses],
    ["tokens", usage.total_tokens],
    ["duration", usage.duration_ms != null ? `${usage.duration_ms} ms` : null],
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
    kind: subtype.replace(/^task_/, "subagent-"),
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineResult(ev) {
  const m = ev.message || {};
  const usage = m.usage || {};
  const parts = [];
  if (m.num_turns !== undefined) parts.push(`${m.num_turns} turns`);
  if (m.duration_ms !== undefined) parts.push(`${(m.duration_ms / 1000).toFixed(1)}s`);
  if (m.total_cost_usd !== undefined) parts.push(`$${m.total_cost_usd.toFixed(4)}`);
  const summary = parts.join(" · ");

  const body = el("div", "tool-body");

  const stats = el("div", "section");
  const statsLabel = el("div", "section-label");
  statsLabel.textContent = "stats";
  stats.appendChild(statsLabel);
  const kv = el("div", "kv");
  const rows = [
    ["status", m.subtype || "?"],
    ["error", m.is_error ? "yes" : "no"],
    ["duration", m.duration_ms != null
      ? (m.duration_api_ms != null
          ? `${(m.duration_ms / 1000).toFixed(1)}s (api ${(m.duration_api_ms / 1000).toFixed(1)}s)`
          : `${(m.duration_ms / 1000).toFixed(1)}s`)
      : null],
    ["turns", m.num_turns],
    ["cost", m.total_cost_usd != null ? `$${m.total_cost_usd.toFixed(4)}` : null],
    ["input tokens", usage.input_tokens],
    ["output tokens", usage.output_tokens],
    ["cache read", usage.cache_read_input_tokens],
    ["cache create", usage.cache_creation_input_tokens],
  ];
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === "") continue;
    const kEl = el("div", "kv-k"); kEl.textContent = k;
    const vEl = el("div", "kv-v"); vEl.appendChild(renderValue(k, v));
    kv.appendChild(kEl); kv.appendChild(vEl);
  }
  stats.appendChild(kv);
  body.appendChild(stats);

  if (typeof m.result === "string" && m.result.length > 0) {
    const section = el("div", "section");
    const label = el("div", "section-label");
    label.textContent = `final result text · ${m.result.length} chars`;
    section.appendChild(label);
    const pre = el("pre", "result-pre");
    pre.textContent = m.result;
    section.appendChild(pre);
    body.appendChild(section);
  }

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: "final",
    summary,
    status: m.is_error ? "ERR" : (m.subtype || "ok"),
    statusState: m.is_error ? "error" : "ok",
    body,
    expandable: true,
  });
}

function buildLineThinking(ev, thinking) {
  const summary = thinking.replace(/\s+/g, " ").slice(0, 160) + (thinking.length > 160 ? "…" : "");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = `${thinking.length} chars`;
  section.appendChild(label);
  const pre = el("pre", "thinking-pre");
  pre.textContent = thinking;
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

function buildLineText(ev, text, role) {
  const summary = text.replace(/\s+/g, " ").slice(0, 200) + (text.length > 200 ? "…" : "");

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const pre = el("pre", "text-pre");
  pre.textContent = text;
  section.appendChild(pre);
  body.appendChild(section);

  // Always expandable: the head's .summary is single-line and CSS-truncated
  // by column width, so even short messages can be visually clipped.
  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: role === "user" ? "user" : "assistant",
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineUnknownBlock(ev, block, role) {
  const keys = block && typeof block === "object" ? Object.keys(block) : [];
  const summary = keys.length ? `keys: ${keys.join(", ")}` : "(empty block)";

  const body = el("div", "tool-body");
  const section = el("div", "section");
  const label = el("div", "section-label");
  label.textContent = `unhandled ${role} block`;
  section.appendChild(label);
  const pre = el("pre", "v-json");
  pre.textContent = stringifyShort(block);
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: `${role}-block`,
    summary,
    status: null,
    body,
    expandable: true,
  });
}

function buildLineOrphanResult(block) {
  const isError = !!block.is_error;
  const text = renderableResultText(block.content);
  const summary = text.replace(/\s+/g, " ").slice(0, 160);
  const body = el("div", "tool-body");
  const section = el("div", "section result-section");
  const label = el("div", "section-label");
  label.textContent = "orphan result · no matching tool_use";
  section.appendChild(label);
  const pre = el("pre", "result-pre");
  pre.textContent = text;
  section.appendChild(pre);
  body.appendChild(section);

  return buildLine({
    ts: "",
    kind: "orphan",
    summary,
    status: isError ? "ERR" : "ok",
    statusState: isError ? "error" : "ok",
    body,
    expandable: true,
  });
}

function buildLineUnknown(ev) {
  const body = el("div", "tool-body");
  const pre = el("pre");
  pre.textContent = stringifyShort(ev);
  body.appendChild(pre);

  return buildLine({
    ts: fmtTs(ev.timestamp),
    kind: ev.type || "unknown",
    summary: "(unrecognized event type)",
    status: null,
    body,
    expandable: true,
  });
}
