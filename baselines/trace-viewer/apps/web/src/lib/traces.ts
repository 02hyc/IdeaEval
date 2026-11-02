import fs from "node:fs";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type {
  JsonValue,
  TraceMessage,
  TracePayload,
  TraceRunSummary,
  TraceSummary,
  TraceToolCall,
  TraceTurn,
} from "./types";

export class TraceError extends Error {}

const JSONL_RELATIVE = path.join("framework", "logs", "adapter_events.jsonl");
const TERMINAL_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "timeout",
  "error",
]);

type Dict = Record<string, unknown>;
type Harness = "claude" | "codex";
type EventRecord = Dict & {
  event_index?: number;
  timestamp?: string;
  phase?: string;
  type?: string;
  message?: unknown;
};
type InternalTool = TraceToolCall & { _turn?: InternalTurn };
type InternalTurn = TraceTurn & {
  _completedMs?: number;
  tool_calls: InternalTool[];
};
type TraceCore = {
  record: Dict | null;
  events: EventRecord[];
  harness: Harness;
  turns: TraceTurn[];
  run: Omit<TraceRunSummary, "path" | "has_record" | "summary">;
  summary: TraceSummary;
};

export function isRunDir(runDir: string): boolean {
  return fs.existsSync(path.join(runDir, JSONL_RELATIVE));
}

// Run dirs under runsRoot, for either layout: flat (<run-id>/) or grouped by
// model profile (<model-profile>/<run-id>/). A run dir is identified by isRunDir().
function collectRunDirs(runsRoot: string): string[] {
  if (!fs.existsSync(runsRoot)) return [];
  const out: string[] = [];
  for (const entry of fs.readdirSync(runsRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const childDir = path.join(runsRoot, entry.name);
    if (isRunDir(childDir)) {
      out.push(childDir);
      continue;
    }
    // A model-profile grouping dir: descend one level for run dirs.
    let grandchildren: fs.Dirent[];
    try {
      grandchildren = fs.readdirSync(childDir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const sub of grandchildren) {
      if (!sub.isDirectory()) continue;
      const grandDir = path.join(childDir, sub.name);
      if (isRunDir(grandDir)) out.push(grandDir);
    }
  }
  return out;
}

export function listTraceRuns(runsRoot: string): TraceRunSummary[] {
  return collectRunDirs(runsRoot)
    .map((runDir) => {
      const core = buildTraceCore(runDir);
      const relKey = path.relative(runsRoot, runDir).split(path.sep).join("/");
      const segments = relKey.split("/");
      const run: TraceRunSummary = {
        ...core.run,
        run_id: core.run.run_id ?? path.basename(runDir),
        key: relKey,
        model_profile: segments.length > 1 ? segments[0] : undefined,
        status: core.run.status ?? "running",
        path: runDir,
        has_record: core.record !== null,
        summary: core.summary,
      };
      const sortKey = run.started_at ?? String(fs.statSync(runDir).mtimeMs);
      return { sortKey, run };
    })
    .sort((a, b) => b.sortKey.localeCompare(a.sortKey))
    .map((row) => row.run);
}

export function findTraceRunDir(runsRoot: string, runId: string): string {
  if (!runId || runId.includes("\\") || runId.split("/").includes("..")) {
    throw new TraceError(`invalid run id: ${JSON.stringify(runId)}`);
  }
  // Primary: treat the id as a path relative to runsRoot (flat "<run-id>" or grouped
  // "<model-profile>/<run-id>"), guarding against escaping the runs root.
  const root = path.resolve(runsRoot);
  const resolved = path.resolve(root, runId);
  if ((resolved === root || resolved.startsWith(root + path.sep)) && isRunDir(resolved)) {
    return resolved;
  }
  // Fallback: a bare run-id matched by directory name or record run_id (legacy urls).
  for (const runDir of collectRunDirs(runsRoot)) {
    if (path.basename(runDir) === runId) return runDir;
    if (loadRecord(runDir)?.run_id === runId) return runDir;
  }
  throw new TraceError(`no run with id ${JSON.stringify(runId)} under ${runsRoot}`);
}

export function buildTrace(runDir: string): TracePayload {
  const core = buildTraceCore(runDir);
  return {
    schema_version: 1,
    harness: core.harness,
    file_complete: fileComplete(runDir),
    run: core.run,
    summary: core.summary,
    turns: core.turns,
    raw_events: core.events.map(toJsonValue),
    computed_at: new Date().toISOString(),
  };
}

function buildTraceCore(runDir: string): TraceCore {
  const record = loadRecord(runDir);
  const resolvedConfig = loadResolvedConfig(runDir);
  const events = readEvents(runDir);
  const harness = resolveHarness(record, resolvedConfig, events);
  const turns = harness === "codex" ? buildCodexTurns(events) : buildClaudeTurns(events);
  const [totalTimeMs, totalTimeSource] = totalTime(record, events, turns);
  const model = stringValue(record?.model) ?? stringValue(resolvedConfig?.model) ?? modelFromEvents(events);
  const [cost, costSource, costNote] = extractCost(harness, events, record, runDir, model, resolvedConfig);
  const toolCount = turns.reduce((sum, turn) => sum + turn.tool_calls.length, 0);
  const messageCount = turns.reduce((sum, turn) => sum + turn.messages.length, 0);
  return {
    record,
    events,
    harness,
    turns,
    run: {
      run_id: stringValue(record?.run_id) ?? path.basename(runDir),
      status: stringValue(record?.status),
      harness: stringValue(record?.harness) ?? stringValue(resolvedConfig?.harness) ?? harness,
      model,
      topic: stringValue(record?.topic) ?? loadInputTopic(runDir),
      started_at: stringValue(record?.started_at) ?? firstEventTimestamp(events),
      completed_at: stringValue(record?.completed_at),
    },
    summary: {
      event_total: events.length,
      turn_count: turns.length,
      message_count: messageCount,
      tool_call_count: toolCount,
      total_time_ms: totalTimeMs,
      total_time_source: totalTimeSource,
      total_cost_usd: cost,
      cost_source: costSource,
      cost_note: costNote,
    },
  };
}

function buildClaudeTurns(events: EventRecord[]): TraceTurn[] {
  const turns: InternalTurn[] = [];
  const tools = new Map<string, InternalTool>();
  let current: InternalTurn | null = null;
  const startTurn = (event: EventRecord) => {
    current = newTurn(turns.length + 1, event);
    turns.push(current);
    return current;
  };
  const ensureTurn = (event: EventRecord) => current ?? startTurn(event);

  for (const event of events) {
    const message = dictValue(event.message);
    if (message?.parent_tool_use_id !== undefined && message.parent_tool_use_id !== null) continue;
    if (event.type === "AssistantMessage") {
      const turn = startTurn(event);
      for (const block of contentBlocks(message)) {
        if (typeof block.thinking === "string") addMessage(turn, event, "assistant", "thinking", block.thinking);
        else if (typeof block.text === "string") addMessage(turn, event, "assistant", "text", block.text);
        else if (isClaudeToolUse(block)) {
          const name = stringValue(block.name) ?? "tool";
          const tool = newTool(event, {
            id: stringValue(block.id) ?? "",
            name,
            type: toolType(name),
            input: toJsonValue(dictValue(block.input) ?? {}),
          });
          tool._turn = turn;
          turn.tool_calls.push(tool);
          if (tool.id) tools.set(tool.id, tool);
          touchTurn(turn, event.timestamp);
        } else if (typeof block.tool_use_id === "string") {
          attachClaudeToolResult(tools, turns, current, event, block);
        }
      }
    } else if (event.type === "UserMessage") {
      for (const block of contentBlocks(message)) {
        if (typeof block.tool_use_id === "string") attachClaudeToolResult(tools, turns, current, event, block);
        else if (typeof block.text === "string") addMessage(ensureTurn(event), event, "user", "text", block.text);
      }
    } else if (event.type === "ResultMessage" && (current || stringValue(message?.result))) {
      const turn = ensureTurn(event);
      const result = stringValue(message?.result);
      if (result && !hasDuplicateAssistantText(turn, result)) {
        addMessage(turn, event, "assistant", "final_result", result);
      }
      touchTurn(turn, event.timestamp);
    }
  }
  return finalizeTurns(turns);
}

function attachClaudeToolResult(
  tools: Map<string, InternalTool>,
  turns: InternalTurn[],
  current: InternalTurn | null,
  event: EventRecord,
  block: Dict
) {
  const id = stringValue(block.tool_use_id) ?? "";
  let tool = tools.get(id);
  if (!tool) {
    const turn = current ?? newTurn(turns.length + 1, event);
    if (!turns.includes(turn)) turns.push(turn);
    tool = newTool(event, { id, name: "unknown_tool", type: "tool", input: {} });
    tool._turn = turn;
    turn.tool_calls.push(tool);
    if (id) tools.set(id, tool);
  }
  tool.result = jsonValueOrUndefined(block.content);
  tool.is_error = Boolean(block.is_error);
  tool.completed_at = event.timestamp;
  tool.result_event_index = numberValue(event.event_index);
  tool.duration_ms = durationBetween(tool.started_at, tool.completed_at);
  if (tool._turn) touchTurn(tool._turn, event.timestamp);
}

function buildCodexTurns(events: EventRecord[]): TraceTurn[] {
  const turns: InternalTurn[] = [];
  const tools = new Map<string, InternalTool>();
  const messages = new Map<string, TraceMessage>();
  let current: InternalTurn | null = null;
  const startTurn = (event: EventRecord) => {
    current = newTurn(turns.length + 1, event);
    turns.push(current);
    return current;
  };
  const ensureTurn = (event: EventRecord) => current ?? startTurn(event);

  for (const event of events) {
    if (event.type === "codex_turn_start" || event.type === "turn/started") {
      startTurn(event);
      continue;
    }
    if (event.type === "turn/completed") {
      applyCodexTurnCompleted(ensureTurn(event), event);
      continue;
    }
    if (event.type === "TurnResult") {
      applyCodexTurnResult(ensureTurn(event), event);
      continue;
    }
    if (event.type !== "item/started" && event.type !== "item/completed" && event.type !== "item/updated") continue;
    const item = dictValue(codexPayload(event).item);
    if (!item) continue;
    const turn = ensureTurn(event);
    const itemType = stringValue(item.type);
    const itemId = stringValue(item.id) ?? "";
    if (itemType === "userMessage" || itemType === "agentMessage" || itemType === "reasoning") {
      upsertCodexMessage(turn, event, item, itemId, messages);
      continue;
    }
    if (!isCodexToolType(itemType)) continue;
    let tool = tools.get(itemId);
    if (!tool) {
      tool = codexToolFromItem(event, item);
      tool._turn = turn;
      turn.tool_calls.push(tool);
      if (itemId) tools.set(itemId, tool);
    }
    if (event.type !== "item/started") completeCodexTool(tool, event, item);
    touchTurn(turn, event.timestamp);
  }
  return finalizeTurns(turns);
}

function upsertCodexMessage(
  turn: InternalTurn,
  event: EventRecord,
  item: Dict,
  itemId: string,
  messages: Map<string, TraceMessage>
) {
  const itemType = stringValue(item.type);
  const text =
    itemType === "userMessage"
      ? codexUserText(item)
      : itemType === "reasoning"
        ? codexReasoningText(item)
        : stringValue(item.text) ?? "";
  if (!text && itemType !== "reasoning") return;
  const message = messages.get(itemId);
  if (message) {
    message.text = text;
    message.timestamp = event.timestamp;
    message.event_index = numberValue(event.event_index);
    touchTurn(turn, event.timestamp);
    return;
  }
  const next: TraceMessage = {
    role: itemType === "userMessage" ? "user" : "assistant",
    kind: itemType === "reasoning" ? "reasoning" : "text",
    text,
    timestamp: event.timestamp,
    event_index: numberValue(event.event_index),
  };
  turn.messages.push(next);
  if (itemId) messages.set(itemId, next);
  touchTurn(turn, event.timestamp);
}

function codexToolFromItem(event: EventRecord, item: Dict): InternalTool {
  const itemType = stringValue(item.type);
  if (itemType === "mcpToolCall") {
    return newTool(event, {
      id: stringValue(item.id) ?? "",
      name: stringValue(item.tool) ?? "mcp",
      rawName: stringValue(item.tool),
      type: "mcp",
      input: toJsonValue(dictValue(item.arguments) ?? {}),
    });
  }
  if (itemType === "commandExecution") {
    return newTool(event, {
      id: stringValue(item.id) ?? "",
      name: "Bash",
      type: "command",
      input: toJsonValue({ command: item.command, cwd: item.cwd, source: item.source }),
    });
  }
  if (itemType === "fileChange") {
    return newTool(event, {
      id: stringValue(item.id) ?? "",
      name: "Edit",
      type: "file",
      input: toJsonValue({ changes: item.changes }),
    });
  }
  if (itemType === "webSearch") {
    return newTool(event, {
      id: stringValue(item.id) ?? "",
      name: "WebSearch",
      type: "web",
      input: toJsonValue({ query: item.query, action: item.action }),
    });
  }
  if (itemType === "collabAgentToolCall") {
    return newTool(event, {
      id: stringValue(item.id) ?? "",
      name: "Agent",
      type: "agent",
      input: toJsonValue({ prompt: item.prompt }),
    });
  }
  return newTool(event, {
    id: stringValue(item.id) ?? "",
    name: itemType ?? "tool",
    type: "tool",
    input: toJsonValue(item),
  });
}

function completeCodexTool(tool: InternalTool, event: EventRecord, item: Dict) {
  const itemType = stringValue(item.type);
  tool.completed_at = event.timestamp;
  tool.result_event_index = numberValue(event.event_index);
  tool.duration_ms = numberValue(item.durationMs) ?? durationBetween(tool.started_at, tool.completed_at);
  if (itemType === "mcpToolCall") {
    tool.is_error = item.error !== undefined && item.error !== null;
    tool.result = tool.is_error ? jsonValueOrUndefined(item.error) : codexResultPayload(item.result);
  } else if (itemType === "commandExecution") {
    const exitCode = numberValue(item.exitCode);
    tool.exit_code = exitCode;
    tool.is_error = exitCode !== undefined && exitCode !== 0;
    tool.result = jsonValueOrUndefined(item.aggregatedOutput);
  } else if (itemType === "collabAgentToolCall") {
    const states = dictValue(item.agentsStates) ?? {};
    tool.is_error = Object.values(states).some((state) => {
      const status = stringValue(dictValue(state)?.status);
      return status === "failed" || status === "error";
    });
    tool.result = jsonValueOrUndefined({ agentsStates: states });
  } else {
    tool.is_error = false;
    tool.result = jsonValueOrUndefined(itemType === "fileChange" ? { changes: item.changes } : { action: item.action, results: item.results });
  }
}

function applyCodexTurnCompleted(turn: InternalTurn, event: EventRecord) {
  const payload = dictValue(codexPayload(event).turn) ?? {};
  turn.status = stringValue(payload.status);
  turn.error = jsonValueOrUndefined(payload.error);
  turn.started_at = stringValue(payload.startedAt) ?? turn.started_at;
  turn.completed_at = stringValue(payload.completedAt) ?? event.timestamp;
  turn.duration_ms = numberValue(payload.durationMs) ?? durationBetween(turn.started_at, turn.completed_at);
}

function applyCodexTurnResult(turn: InternalTurn, event: EventRecord) {
  const message = dictValue(event.message) ?? {};
  turn.status = stringValue(message.status) ?? turn.status;
  turn.error = jsonValueOrUndefined(message.error);
  turn.started_at = stringValue(message.started_at) ?? turn.started_at;
  turn.completed_at = stringValue(message.completed_at) ?? turn.completed_at;
  turn.duration_ms = numberValue(message.duration_ms) ?? turn.duration_ms;
  const finalResponse = stringValue(message.final_response);
  if (finalResponse && !hasDuplicateAssistantText(turn, finalResponse)) {
    addMessage(turn, event, "assistant", "final_result", finalResponse);
  }
  touchTurn(turn, event.timestamp);
}

function newTurn(index: number, event: EventRecord): InternalTurn {
  return {
    index,
    phase: event.phase,
    started_at: event.timestamp,
    completed_at: event.timestamp,
    messages: [],
    tool_calls: [],
    _completedMs: timeMs(event.timestamp),
  };
}

function newTool(
  event: EventRecord,
  opts: {
    id: string;
    name: string;
    rawName?: string;
    type: TraceToolCall["type"];
    input: JsonValue;
  }
): InternalTool {
  return {
    id: opts.id,
    name: shortToolName(opts.name),
    raw_name: opts.rawName ?? opts.name,
    type: opts.type,
    input: opts.input,
    result: undefined,
    is_error: false,
    started_at: event.timestamp,
    event_index: numberValue(event.event_index),
  };
}

function addMessage(
  turn: InternalTurn,
  event: EventRecord,
  role: TraceMessage["role"],
  kind: TraceMessage["kind"],
  text: string
) {
  if (!text) return;
  turn.messages.push({ role, kind, text, timestamp: event.timestamp, event_index: numberValue(event.event_index) });
  touchTurn(turn, event.timestamp);
}

function touchTurn(turn: InternalTurn, timestamp: string | undefined) {
  const ms = timeMs(timestamp);
  if (ms === undefined) return;
  if (turn._completedMs === undefined || ms >= turn._completedMs) {
    turn.completed_at = timestamp;
    turn._completedMs = ms;
  }
}

function finalizeTurns(turns: InternalTurn[]): TraceTurn[] {
  return turns
    .filter((turn) => turn.messages.length || turn.tool_calls.length)
    .map((turn, idx) => ({
      index: idx + 1,
      phase: turn.phase,
      status: turn.status,
      error: turn.error,
      started_at: turn.started_at,
      completed_at: turn.completed_at,
      duration_ms: turn.duration_ms ?? durationBetween(turn.started_at, turn.completed_at),
      messages: turn.messages,
      tool_calls: turn.tool_calls.map((tool) => {
        const clean: TraceToolCall = {
          ...tool,
          duration_ms: tool.duration_ms ?? durationBetween(tool.started_at, tool.completed_at),
        };
        delete (clean as InternalTool)._turn;
        return clean;
      }),
    }));
}

function totalTime(record: Dict | null, events: EventRecord[], turns: TraceTurn[]): [number | undefined, string | undefined] {
  const recordDuration = durationBetween(stringValue(record?.started_at), stringValue(record?.completed_at));
  if (recordDuration !== undefined) return [recordDuration, "run_record"];
  const usageDuration = numberValue(dictValue(record?.usage)?.duration_ms);
  if (usageDuration !== undefined) return [usageDuration, "run_record.usage"];
  if (events.length) {
    const eventDuration = durationBetween(events[0].timestamp, events[events.length - 1].timestamp);
    if (eventDuration !== undefined) return [eventDuration, "events"];
  }
  if (turns.length) {
    const turnDuration = durationBetween(turns[0].started_at, turns[turns.length - 1].completed_at);
    if (turnDuration !== undefined) return [turnDuration, "turns"];
  }
  return [undefined, undefined];
}

function extractCost(
  harness: Harness,
  events: EventRecord[],
  record: Dict | null,
  runDir: string,
  model: string | undefined,
  resolvedConfig: Dict | undefined
): [number | undefined, string | undefined, string | undefined] {
  const [usage, usageSource] = usageForCost(harness, events, record, runDir);
  const usageCost = firstNumberValue(usage, "total_cost_usd", "totalCostUsd", "costUSD", "cost_usd");
  const configured = costFromConfiguredPricing(record, resolvedConfig, runDir, usage, usageSource, model);
  if (configured[0] !== undefined) return configured;
  let result: Dict | undefined;
  for (const event of events) if (event.type === "ResultMessage") result = dictValue(event.message);
  if (harness === "claude") {
    const modelUsage = dictValue(result?.model_usage) ?? dictValue(result?.modelUsage);
    if (modelUsage) {
      let total = 0;
      let seen = false;
      for (const value of Object.values(modelUsage)) {
        const cost = firstNumberValue(dictValue(value), "costUSD", "cost_usd");
        if (cost !== undefined) {
          total += cost;
          seen = true;
        }
      }
      if (seen) return [total, "result_message.model_usage", undefined];
    }
    const resultCost = firstNumberValue(result, "total_cost_usd", "totalCostUsd");
    if (resultCost !== undefined) return [resultCost, "result_message.total_cost_usd", undefined];
  }
  if (usageCost !== undefined) return [usageCost, usageSource, undefined];
  return [undefined, undefined, "No cost returned by provider and no matching pricing.models entry configured."];
}

function usageForCost(harness: Harness, events: EventRecord[], record: Dict | null, runDir: string): [Dict | undefined, string | undefined] {
  if (harness === "claude") {
    const eventUsage = usageFromEvents(harness, events);
    if (hasUsageData(eventUsage)) return [eventUsage, "adapter_events.usage"];
    const recordUsage = normalizedRecordUsage(record);
    if (hasUsageData(recordUsage)) return [recordUsage, "run_record.usage"];
    return [undefined, undefined];
  }

  const recordUsage = normalizedRecordUsage(record);
  if (hasUsageData(recordUsage)) return [recordUsage, "run_record.usage"];
  const eventUsage = usageFromEvents(harness, events);
  if (hasUsageData(eventUsage)) return [eventUsage, "adapter_events.usage"];
  if (harness === "codex") {
    const sessionUsage = usageFromCodexSessionLogs(runDir);
    if (hasUsageData(sessionUsage)) return [sessionUsage, "codex_sessions.token_count"];
  }
  return [undefined, undefined];
}

function costFromConfiguredPricing(
  record: Dict | null,
  resolvedConfig: Dict | undefined,
  runDir: string,
  usage: Dict | undefined,
  usageSource: string | undefined,
  model: string | undefined
): [number | undefined, string | undefined, string | undefined] {
  if (!usage || !model) return [undefined, undefined, undefined];

  const resolvedModels = dictValue(dictValue(dictValue(record?.resolved_config)?.pricing)?.models);
  const resolvedPricing = dictValue(resolvedModels?.[model]);
  if (resolvedPricing) {
    const total = calculateUsageCostUsd(usage, resolvedPricing);
    if (total !== undefined) return [total, pricingCostSource(usageSource, "run_record.pricing"), undefined];
  }

  const liveResolvedModels = dictValue(dictValue(resolvedConfig?.pricing)?.models);
  const liveResolvedPricing = dictValue(liveResolvedModels?.[model]);
  if (liveResolvedPricing) {
    const total = calculateUsageCostUsd(usage, liveResolvedPricing);
    if (total !== undefined) return [total, pricingCostSource(usageSource, "resolved_config.pricing"), undefined];
  }

  const configPricing = pricingFromConfigPath(record, runDir, model);
  if (configPricing) {
    const total = calculateUsageCostUsd(usage, configPricing);
    if (total !== undefined) return [total, pricingCostSource(usageSource, "config_path.pricing"), undefined];
  }

  return [undefined, undefined, undefined];
}

function pricingCostSource(usageSource: string | undefined, pricingSource: string): string {
  if (usageSource === "adapter_events.usage") return "adapter_events.pricing";
  if (usageSource === "codex_sessions.token_count") return "codex_sessions.pricing";
  return pricingSource;
}

function usageFromEvents(harness: Harness, events: EventRecord[]): Dict | undefined {
  if (harness === "claude") return usageFromClaudeMessages(events);

  let latest: Dict | undefined;
  for (const event of events) {
    if (harness === "codex") {
      const message = dictValue(event.message);
      const direct = usageCandidateFromValue(message?.usage);
      if (hasUsageData(direct)) latest = direct;
      const recursive = usageCandidateFromValue(message);
      if (hasUsageData(recursive)) latest = recursive;
    }
  }
  return latest;
}

function usageFromClaudeMessages(events: EventRecord[]): Dict | undefined {
  const byMessageId = new Map<string, Dict>();
  let fallbackIndex = 0;

  for (const event of events) {
    const message = dictValue(event.message);
    if (event.type === "AssistantMessage") {
      const usage = usageCandidateFromValue(message?.usage);
      if (!hasUsageData(usage)) continue;
      const messageId = stringValue(message?.message_id);
      const key = messageId ?? `event:${event.event_index ?? fallbackIndex}`;
      byMessageId.set(key, usage);
      fallbackIndex += 1;
    } else if (event.type === "ResultMessage" && byMessageId.size === 0) {
      const usage = usageCandidateFromValue(message?.usage);
      if (hasUsageData(usage)) byMessageId.set(`result:${event.event_index ?? fallbackIndex}`, usage);
    }
  }

  if (!byMessageId.size) return undefined;

  let inputTokens = 0;
  let inputSeen = false;
  let cacheCreationTokens = 0;
  let cacheCreationSeen = false;
  let cacheReadTokens = 0;
  let cacheReadSeen = false;
  let outputTokens = 0;
  let outputSeen = false;

  for (const usage of byMessageId.values()) {
    const input = firstNumberValue(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens");
    if (input !== undefined) {
      inputTokens += input;
      inputSeen = true;
    }
    const cacheCreation = firstNumberValue(usage, "cache_creation_input_tokens", "cacheCreationInputTokens");
    if (cacheCreation !== undefined) {
      cacheCreationTokens += cacheCreation;
      cacheCreationSeen = true;
    }
    const cacheRead = firstNumberValue(
      usage,
      "cache_read_input_tokens",
      "cacheReadInputTokens",
      "cached_input_tokens",
      "cachedInputTokens",
      "prompt_cache_hit_tokens",
      "promptCacheHitTokens"
    );
    if (cacheRead !== undefined) {
      cacheReadTokens += cacheRead;
      cacheReadSeen = true;
    }
    const output = firstNumberValue(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens");
    if (output !== undefined) {
      outputTokens += output;
      outputSeen = true;
    }
  }

  const totalTokens =
    (inputSeen ? inputTokens : 0) +
    (cacheCreationSeen ? cacheCreationTokens : 0) +
    (cacheReadSeen ? cacheReadTokens : 0) +
    (outputSeen ? outputTokens : 0);

  return {
    ...(inputSeen ? { input_tokens: inputTokens } : {}),
    input_tokens_include_cached: false,
    ...(cacheCreationSeen ? { cache_creation_input_tokens: cacheCreationTokens } : {}),
    ...(cacheReadSeen ? { cache_read_input_tokens: cacheReadTokens } : {}),
    ...(outputSeen ? { output_tokens: outputTokens } : {}),
    total_tokens: totalTokens,
    turns: byMessageId.size,
  };
}

function usageFromCodexSessionLogs(runDir: string): Dict | undefined {
  const roots = [
    path.join(runDir, "framework", "codex-home", "sessions"),
    path.join(runDir, "framework", "logs", "codex_sessions"),
  ].filter((root) => fs.existsSync(root));
  if (!roots.length) return undefined;
  let latest: { mtimeMs: number; lineNo: number; usage: Dict } | undefined;
  for (const root of roots) {
    for (const file of listJsonlFiles(root)) {
      const mtimeMs = fs.statSync(file).mtimeMs;
      let lineNo = 0;
      for (const line of safeReadLines(file)) {
        lineNo += 1;
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          const usage = usageCandidateFromValue(event);
          if (!hasUsageData(usage)) continue;
          if (!latest || mtimeMs > latest.mtimeMs || (mtimeMs === latest.mtimeMs && lineNo > latest.lineNo)) {
            latest = { mtimeMs, lineNo, usage };
          }
        } catch {
          // Ignore partial JSONL lines from live Codex session logs.
        }
      }
    }
  }
  return latest?.usage;
}

function usageCandidateFromValue(value: unknown, depth = 0): Dict | undefined {
  if (depth > 8) return undefined;
  const dict = dictValue(value);
  if (!dict) return undefined;

  for (const key of ["total_token_usage", "totalTokenUsage", "token_usage", "tokenUsage", "usage", "total"]) {
    const nested = usageCandidateFromValue(dict[key], depth + 1);
    if (hasUsageData(nested)) return nested;
  }
  if (hasUsageData(dict)) return dict;

  for (const nested of Object.values(dict)) {
    const candidate = usageCandidateFromValue(nested, depth + 1);
    if (hasUsageData(candidate)) return candidate;
  }
  return undefined;
}

function hasUsageData(usage: Dict | undefined): usage is Dict {
  if (!usage) return false;
  return firstNumberValue(
    usage,
    "total_cost_usd",
    "totalCostUsd",
    "input_tokens",
    "inputTokens",
    "prompt_tokens",
    "promptTokens",
    "prompt_cache_miss_tokens",
    "promptCacheMissTokens",
    "cache_miss_input_tokens",
    "cacheMissInputTokens",
    "cached_input_tokens",
    "cachedInputTokens",
    "cache_read_input_tokens",
    "cacheReadInputTokens",
    "prompt_cache_hit_tokens",
    "promptCacheHitTokens",
    "output_tokens",
    "outputTokens",
    "completion_tokens",
    "completionTokens",
    "total_tokens",
    "totalTokens"
  ) !== undefined;
}

function listJsonlFiles(root: string): string[] {
  const out: string[] = [];
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(file);
      else if (entry.isFile() && entry.name.endsWith(".jsonl")) out.push(file);
    }
  }
  return out.sort();
}

function safeReadLines(file: string): string[] {
  try {
    return fs.readFileSync(file, "utf-8").split("\n");
  } catch {
    return [];
  }
}

function calculateUsageCostUsd(usage: Dict, pricing: Dict): number | undefined {
  let cost = 0;
  let anyPriced = false;

  const totalInputTokens = firstNumberValue(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens");
  const cacheMissInputTokens = firstNumberValue(
    usage,
    "prompt_cache_miss_tokens",
    "promptCacheMissTokens",
    "cache_miss_input_tokens",
    "cacheMissInputTokens"
  );
  const inputTokens = totalInputTokens ?? cacheMissInputTokens;
  const cachedInputTokens =
    firstNumberValue(
      usage,
      "cached_input_tokens",
      "cachedInputTokens",
      "cache_read_input_tokens",
      "cacheReadInputTokens",
      "prompt_cache_hit_tokens",
      "promptCacheHitTokens"
    ) ?? 0;
  const cacheCreationTokens = firstNumberValue(usage, "cache_creation_input_tokens", "cacheCreationInputTokens") ?? 0;
  const inputPrice = firstNumberValue(pricing, "input_per_million_usd", "inputPerMillionUsd");
  const cachedInputPrice = firstNumberValue(pricing, "cached_input_per_million_usd", "cachedInputPerMillionUsd");
  const cacheCreationPrice = firstNumberValue(pricing, "cache_creation_input_per_million_usd", "cacheCreationInputPerMillionUsd");
  const usageInputIncludesCached = firstBooleanValue(usage, "input_tokens_include_cached", "inputTokensIncludeCached");
  const inputIncludesCached =
    usageInputIncludesCached ?? (totalInputTokens === undefined && cacheMissInputTokens !== undefined ? false : booleanValue(pricing.input_includes_cached_tokens, true));
  const longContextThreshold = firstNumberValue(pricing, "long_context_threshold_tokens", "longContextThresholdTokens");
  const modelContextWindow = firstNumberValue(usage, "model_context_window", "modelContextWindow");
  const totalContextInputTokens =
    inputTokens === undefined ? undefined : inputIncludesCached ? inputTokens : inputTokens + cachedInputTokens + cacheCreationTokens;
  // Long context is a per-request price tier: a single request cannot exceed the
  // model's context window, so when the window is at or below the threshold no request
  // can reach the tier regardless of how large the cumulative session total grows.
  const longContext =
    longContextThreshold !== undefined &&
    totalContextInputTokens !== undefined &&
    totalContextInputTokens > longContextThreshold &&
    (modelContextWindow === undefined || modelContextWindow > longContextThreshold);
  const effectiveInputPrice = longContext
    ? firstNumberValue(pricing, "long_context_input_per_million_usd", "longContextInputPerMillionUsd") ?? inputPrice
    : inputPrice;
  const effectiveCachedInputPrice = longContext
    ? firstNumberValue(pricing, "long_context_cached_input_per_million_usd", "longContextCachedInputPerMillionUsd") ?? cachedInputPrice
    : cachedInputPrice;

  if (inputTokens !== undefined && effectiveInputPrice !== undefined) {
    let regularInputTokens = inputTokens;
    if (inputIncludesCached) {
      if (effectiveCachedInputPrice !== undefined) regularInputTokens -= cachedInputTokens;
      if (cacheCreationPrice !== undefined) regularInputTokens -= cacheCreationTokens;
    }
    cost += tokenCost(Math.max(regularInputTokens, 0), effectiveInputPrice);
    anyPriced = true;
  }

  if (cachedInputTokens && effectiveCachedInputPrice !== undefined) {
    cost += tokenCost(cachedInputTokens, effectiveCachedInputPrice);
    anyPriced = true;
  }

  if (cacheCreationTokens) {
    const effectiveCacheCreationPrice = cacheCreationPrice ?? (!inputIncludesCached ? effectiveInputPrice : undefined);
    if (effectiveCacheCreationPrice !== undefined) {
      cost += tokenCost(cacheCreationTokens, effectiveCacheCreationPrice);
      anyPriced = true;
    }
  }

  const outputTokens = firstNumberValue(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens");
  const reasoningTokens = firstNumberValue(usage, "reasoning_output_tokens", "reasoningOutputTokens", "reasoning_tokens", "reasoningTokens") ?? 0;
  const outputPrice = firstNumberValue(pricing, "output_per_million_usd", "outputPerMillionUsd");
  const reasoningPrice = firstNumberValue(pricing, "reasoning_output_per_million_usd", "reasoningOutputPerMillionUsd");
  const effectiveOutputPrice = longContext
    ? firstNumberValue(pricing, "long_context_output_per_million_usd", "longContextOutputPerMillionUsd") ?? outputPrice
    : outputPrice;
  const effectiveReasoningPrice = longContext
    ? firstNumberValue(pricing, "long_context_reasoning_output_per_million_usd", "longContextReasoningOutputPerMillionUsd") ??
      (reasoningPrice !== undefined ? effectiveOutputPrice : undefined)
    : reasoningPrice;

  if (outputTokens !== undefined && effectiveOutputPrice !== undefined) {
    const visibleOutputTokens = reasoningTokens && effectiveReasoningPrice !== undefined ? Math.max(outputTokens - reasoningTokens, 0) : outputTokens;
    cost += tokenCost(visibleOutputTokens, effectiveOutputPrice);
    anyPriced = true;
  }

  if (reasoningTokens && effectiveReasoningPrice !== undefined) {
    cost += tokenCost(reasoningTokens, effectiveReasoningPrice);
    anyPriced = true;
  }

  return anyPriced ? cost : undefined;
}

function pricingFromConfigPath(record: Dict | null, runDir: string, model: string): Dict | undefined {
  const configPath = stringValue(record?.config_path);
  if (!configPath) return undefined;
  for (const candidate of configPathCandidates(configPath, runDir)) {
    if (!fs.existsSync(candidate)) continue;
    try {
      const parsed = parseYaml(fs.readFileSync(candidate, "utf-8"));
      const models = dictValue(dictValue(dictValue(parsed)?.pricing)?.models);
      const pricing = dictValue(models?.[model]);
      if (pricing) return pricing;
    } catch {
      return undefined;
    }
  }
  return undefined;
}

function configPathCandidates(configPath: string, runDir: string): string[] {
  if (path.isAbsolute(configPath)) return [configPath];
  const bases: string[] = [];
  if (process.env.INIT_CWD) bases.push(process.env.INIT_CWD);
  bases.push(process.cwd());
  let current = runDir;
  for (let i = 0; i < 8; i += 1) {
    bases.push(current);
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return [...new Set(bases.map((base) => path.resolve(base, configPath)))];
}

function normalizedRecordUsage(record: Dict | null): Dict | undefined {
  const usage = dictValue(record?.usage);
  return dictValue(usage?.total) ?? usage;
}

function tokenCost(tokens: number, pricePerMillion: number): number {
  return tokens * pricePerMillion / 1_000_000;
}

function readEvents(runDir: string): EventRecord[] {
  const file = path.join(runDir, JSONL_RELATIVE);
  if (!fs.existsSync(file)) return [];
  const events: EventRecord[] = [];
  for (const line of fs.readFileSync(file, "utf-8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const parsed = JSON.parse(line);
      if (isDict(parsed)) events.push(parsed as EventRecord);
    } catch {
      // Ignore partial JSONL lines from live runs.
    }
  }
  return events;
}

function loadRecord(runDir: string): Dict | null {
  const file = path.join(runDir, "run_record.json");
  if (!fs.existsSync(file)) return null;
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
    return isDict(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function loadResolvedConfig(runDir: string): Dict | undefined {
  const file = path.join(runDir, "framework", "input", "resolved_config.yaml");
  if (!fs.existsSync(file)) return undefined;
  try {
    return dictValue(parseYaml(fs.readFileSync(file, "utf-8")));
  } catch {
    return undefined;
  }
}

function loadInputTopic(runDir: string): string | undefined {
  const file = path.join(runDir, "framework", "input", "topic.txt");
  if (!fs.existsSync(file)) return undefined;
  try {
    const topic = fs.readFileSync(file, "utf-8").trim();
    return topic || undefined;
  } catch {
    return undefined;
  }
}

function fileComplete(runDir: string): boolean {
  const status = stringValue(loadRecord(runDir)?.status);
  return status !== undefined && TERMINAL_STATUSES.has(status);
}

function resolveHarness(record: Dict | null, resolvedConfig: Dict | undefined, events: EventRecord[]): Harness {
  const fromRecord = stringValue(record?.harness);
  if (fromRecord === "claude" || fromRecord === "codex") return fromRecord;
  const fromConfig = stringValue(resolvedConfig?.harness);
  if (fromConfig === "claude" || fromConfig === "codex") return fromConfig;
  for (const event of events) {
    const type = event.type ?? "";
    if (type === "codex_turn_start" || type.startsWith("turn/") || type.startsWith("item/") || type === "TurnResult") return "codex";
    if (type === "AssistantMessage" || type === "UserMessage" || type === "SystemMessage" || type === "ResultMessage") return "claude";
  }
  return "claude";
}

function modelFromEvents(events: EventRecord[]): string | undefined {
  for (const event of events) {
    const message = dictValue(event.message);
    const initModel = stringValue(dictValue(message?.data)?.model);
    if (initModel) return initModel;
    const direct = stringValue(message?.model);
    if (direct) return direct;
    const payloadModel = stringValue(codexPayload(event).model);
    if (payloadModel) return payloadModel;
  }
  return undefined;
}

function firstEventTimestamp(events: EventRecord[]): string | undefined {
  for (const event of events) {
    if (event.timestamp) return event.timestamp;
  }
  return undefined;
}

function contentBlocks(message: Dict | undefined): Dict[] {
  return Array.isArray(message?.content) ? message.content.filter(isDict) : [];
}

function codexPayload(event: EventRecord): Dict {
  return dictValue(dictValue(dictValue(event.message)?.event)?.payload) ?? {};
}

function codexUserText(item: Dict): string {
  if (typeof item.content === "string") return item.content;
  if (!Array.isArray(item.content)) return "";
  return item.content.map((entry) => stringValue(dictValue(entry)?.text) ?? "").filter(Boolean).join("\n");
}

function codexReasoningText(item: Dict): string {
  if (Array.isArray(item.summary)) {
    const text = item.summary.map((entry) => stringValue(dictValue(entry)?.text) ?? (typeof entry === "string" ? entry : "")).filter(Boolean).join("\n");
    if (text) return text;
  }
  return stringValue(item.text) ?? "";
}

function codexResultPayload(result: unknown): JsonValue | undefined {
  const dict = dictValue(result);
  return dict && dict.content !== undefined ? toJsonValue(dict.content) : jsonValueOrUndefined(result);
}

function isClaudeToolUse(block: Dict): boolean {
  return block.id !== undefined && block.name !== undefined && block.input !== undefined;
}

function isCodexToolType(value: string | undefined): boolean {
  return value === "mcpToolCall" || value === "commandExecution" || value === "fileChange" || value === "webSearch" || value === "collabAgentToolCall";
}

function toolType(name: string): TraceToolCall["type"] {
  const short = shortToolName(name);
  if (name.startsWith("mcp__")) return "mcp";
  if (short === "Bash") return "command";
  if (["Read", "Write", "Edit", "NotebookEdit"].includes(short)) return "file";
  if (["WebSearch", "WebFetch"].includes(short)) return "web";
  if (["Agent", "Task"].includes(short)) return "agent";
  return "tool";
}

function shortToolName(name: string): string {
  if (!name.startsWith("mcp__")) return name;
  const rest = name.slice("mcp__".length);
  const idx = rest.indexOf("__");
  return idx >= 0 ? rest.slice(idx + 2) : name;
}

function hasDuplicateAssistantText(turn: TraceTurn, text: string): boolean {
  return turn.messages.some((message) => message.role === "assistant" && message.text === text);
}

function durationBetween(start: string | undefined, end: string | undefined): number | undefined {
  const s = timeMs(start);
  const e = timeMs(end);
  return s === undefined || e === undefined ? undefined : Math.max(0, Math.round(e - s));
}

function timeMs(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string" || !value) return undefined;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : undefined;
}

function isDict(value: unknown): value is Dict {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function dictValue(value: unknown): Dict | undefined {
  return isDict(value) ? value : undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function firstNumberValue(source: Dict | undefined, ...keys: string[]): number | undefined {
  if (!source) return undefined;
  for (const key of keys) {
    const value = numberValue(source[key]);
    if (value !== undefined) return value;
  }
  return undefined;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function firstBooleanValue(mapping: Dict | undefined, ...keys: string[]): boolean | undefined {
  if (!mapping) return undefined;
  for (const key of keys) {
    const value = mapping[key];
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function jsonValueOrUndefined(value: unknown): JsonValue | undefined {
  return value === undefined ? undefined : toJsonValue(value);
}

function toJsonValue(value: unknown): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.map(toJsonValue);
  if (isDict(value)) {
    const out: Record<string, JsonValue> = {};
    for (const [key, item] of Object.entries(value)) {
      if (item !== undefined) out[key] = toJsonValue(item);
    }
    return out;
  }
  return String(value);
}
