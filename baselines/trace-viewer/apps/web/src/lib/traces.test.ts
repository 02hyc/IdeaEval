import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET as getTrace } from "@/app/api/traces/[...runId]/route";
import { GET as getRuns } from "@/app/api/traces/runs/route";
import {
  buildTrace,
  findTraceRunDir,
  listTraceRuns,
  TraceError,
} from "./traces";

const LOG_PATH = path.join("framework", "logs", "adapter_events.jsonl");
const EXPECTED_CODEX_COST = 0.0109;

let tempDir: string;

beforeEach(() => {
  tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "ideaeval-trace-viewer-"));
  vi.stubEnv("TRACE_RUNS_DIR", tempDir);
  vi.stubEnv("INIT_CWD", tempDir);
});

afterEach(() => {
  vi.unstubAllEnvs();
  fs.rmSync(tempDir, { recursive: true, force: true });
});

describe("trace parsing", () => {
  it("normalizes Claude Agent SDK events into turns, tools, time, and cost", () => {
    const runDir = writeRun("claude-folder", claudeRecord(), claudeEvents());

    const trace = buildTrace(runDir);

    expect(trace.harness).toBe("claude");
    expect(trace.file_complete).toBe(true);
    expect(trace.summary.total_time_ms).toBe(17_000);
    expect(trace.summary.total_time_source).toBe("run_record");
    expect(trace.summary.total_cost_usd).toBe(0.25);
    expect(trace.summary.cost_source).toBe("result_message.model_usage");
    expect(trace.summary.turn_count).toBe(1);
    expect(trace.summary.tool_call_count).toBe(1);
    expect(trace.raw_events).toHaveLength(4);
    expect(trace.turns[0].duration_ms).toBe(2_000);
    expect(trace.turns[0].messages.map((message) => message.role)).toEqual([
      "assistant",
      "assistant",
    ]);
    expect(trace.turns[0].messages.map((message) => message.kind)).toEqual([
      "text",
      "final_result",
    ]);
    expect(trace.turns[0].tool_calls[0]).toMatchObject({
      id: "tool-1",
      name: "WebSearch",
      type: "mcp",
      is_error: false,
      duration_ms: 1_000,
    });
    expect(trace.turns[0].tool_calls[0].result).toEqual([
      { type: "text", text: "paper search results" },
    ]);
  });

  it("prefers configured official pricing over Claude SDK provider cost", () => {
    const runDir = writeRun("claude-configured-cost", claudeRecord({
      usage: {
        input_tokens: 1000,
        output_tokens: 200,
        total_cost_usd: 0.25,
      },
      resolved_config: {
        pricing: {
          models: {
            "claude-opus-4.6": {
              input_per_million_usd: 5,
              output_per_million_usd: 25,
              input_includes_cached_tokens: false,
            },
          },
        },
      },
    }), claudeEvents());

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(0.01);
    expect(trace.summary.cost_source).toBe("run_record.pricing");
  });

  it("prices live Claude usage from deduplicated AssistantMessage usage", () => {
    const runDir = writeLiveRun("live-claude-no-record", claudeLiveUsageEvents(), claudeResolvedConfigYaml());

    const trace = buildTrace(runDir);
    const runs = listTraceRuns(tempDir);

    expect(trace.harness).toBe("claude");
    expect(trace.run.model).toBe("global.anthropic.claude-opus-4-6-v1");
    expect(trace.summary.total_cost_usd).toBeCloseTo(0.008575);
    expect(trace.summary.cost_source).toBe("adapter_events.pricing");
    expect(runs[0]).toMatchObject({
      run_id: "live-claude-no-record",
      status: "running",
      has_record: false,
      summary: {
        total_cost_usd: 0.008575,
      },
    });
  });

  it("normalizes Codex item events into user/assistant turns and tool calls", () => {
    const runDir = writeRun("codex-folder", codexRecord(), codexEvents());

    const trace = buildTrace(runDir);

    expect(trace.harness).toBe("codex");
    expect(trace.summary.total_time_ms).toBe(2_000);
    expect(trace.summary.total_time_source).toBe("run_record");
    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("run_record.pricing");
    expect(trace.summary.turn_count).toBe(1);
    expect(trace.turns[0].duration_ms).toBe(1_200);
    expect(trace.turns[0].messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
    ]);
    expect(trace.turns[0].tool_calls[0]).toMatchObject({
      id: "mcp-1",
      name: "web_search",
      type: "mcp",
      duration_ms: 250,
      is_error: false,
    });
    expect(trace.turns[0].tool_calls[0].result).toEqual([
      { type: "text", text: "search results" },
    ]);
  });

  it("lists trace runs and resolves run ids from run_record.json", () => {
    const runDir = writeRun("physical-dir", codexRecord({
      run_id: "logical-run",
      status: "succeeded",
      topic: "auxiliary information",
      started_at: "2026-06-18T00:00:00.000Z",
      completed_at: undefined,
    }), codexEvents());
    fs.mkdirSync(path.join(tempDir, "no-log"), { recursive: true });

    const runs = listTraceRuns(tempDir);

    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      run_id: "logical-run",
      status: "succeeded",
      harness: "codex",
      model: "gpt-5.4",
      topic: "auxiliary information",
      path: runDir,
      has_record: true,
      summary: {
        total_time_ms: 2000,
        total_cost_usd: EXPECTED_CODEX_COST,
        tool_call_count: 1,
      },
    });
    expect(findTraceRunDir(tempDir, "logical-run")).toBe(runDir);
    expect(() => findTraceRunDir(tempDir, "../bad")).toThrow(TraceError);
  });

  it("lists and resolves runs nested under a model-profile dir", () => {
    const runDir = writeRun(
      path.join("gpt-5.4", "nested-run"),
      codexRecord({ run_id: "nested-run", status: "succeeded" }),
      codexEvents()
    );

    const runs = listTraceRuns(tempDir);

    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      run_id: "nested-run",
      key: "gpt-5.4/nested-run",
      model_profile: "gpt-5.4",
      path: runDir,
    });
    // resolves by the unique key and by the bare run-id (legacy)
    expect(findTraceRunDir(tempDir, "gpt-5.4/nested-run")).toBe(runDir);
    expect(findTraceRunDir(tempDir, "nested-run")).toBe(runDir);
  });

  it("distinguishes same run-id runs under different model profiles", () => {
    const a = writeRun(path.join("claude-opus-4.6", "dup"), claudeRecord({ run_id: "dup" }), claudeEvents());
    const b = writeRun(path.join("deepseek-v4-pro", "dup"), claudeRecord({ run_id: "dup" }), claudeEvents());

    const runs = listTraceRuns(tempDir);

    expect(runs).toHaveLength(2);
    expect(runs.map((run) => run.key).sort()).toEqual([
      "claude-opus-4.6/dup",
      "deepseek-v4-pro/dup",
    ]);
    expect(findTraceRunDir(tempDir, "claude-opus-4.6/dup")).toBe(a);
    expect(findTraceRunDir(tempDir, "deepseek-v4-pro/dup")).toBe(b);
  });

  it("prices live Codex usage from adapter events before run_record usage exists", () => {
    const runDir = writeRun("live-codex", codexRecord({ usage: undefined }), [
      ...codexEvents(),
      codexTokenUsageEvent(),
    ]);

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("adapter_events.pricing");
  });

  it("prices live Codex usage before run_record.json is written", () => {
    const runDir = writeLiveRun("live-codex-no-record", codexEvents(), resolvedConfigYaml());
    fs.writeFileSync(path.join(runDir, "framework", "input", "topic.txt"), "live hypothesis topic\n");
    const sessionPath = path.join(runDir, "framework", "codex-home", "sessions", "2026", "06", "18", "rollout.jsonl");
    fs.mkdirSync(path.dirname(sessionPath), { recursive: true });
    fs.writeFileSync(sessionPath, `${JSON.stringify(codexSessionTokenCountEvent())}\n`);

    const trace = buildTrace(runDir);
    const runs = listTraceRuns(tempDir);

    expect(trace.run.model).toBe("gpt-5.4");
    expect(trace.run.topic).toBe("live hypothesis topic");
    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("codex_sessions.pricing");
    expect(runs[0]).toMatchObject({
      run_id: "live-codex-no-record",
      status: "running",
      has_record: false,
      summary: {
        total_cost_usd: EXPECTED_CODEX_COST,
      },
    });
  });

  it("prices Codex usage from archived session token_count events", () => {
    const runDir = writeRun("session-codex", codexRecord({ usage: undefined }), codexEvents());
    const sessionPath = path.join(runDir, "framework", "logs", "codex_sessions", "2026", "06", "18", "rollout.jsonl");
    fs.mkdirSync(path.dirname(sessionPath), { recursive: true });
    fs.writeFileSync(sessionPath, `${JSON.stringify(codexSessionTokenCountEvent())}\n`);

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("codex_sessions.pricing");
  });

  it("prices live Codex usage from isolated codex-home session logs", () => {
    const runDir = writeRun("codex-home-session", codexRecord({ usage: undefined }), codexEvents());
    const sessionPath = path.join(runDir, "framework", "codex-home", "sessions", "2026", "06", "18", "rollout.jsonl");
    fs.mkdirSync(path.dirname(sessionPath), { recursive: true });
    fs.writeFileSync(sessionPath, `${JSON.stringify(codexSessionTokenCountEvent())}\n`);

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("codex_sessions.pricing");
  });

  it("prices OpenAI prompt cache fields from Codex session logs", () => {
    const runDir = writeRun("openai-cache-session", codexRecord({ usage: undefined }), codexEvents());
    const sessionPath = path.join(runDir, "framework", "codex-home", "sessions", "2026", "06", "18", "rollout.jsonl");
    fs.mkdirSync(path.dirname(sessionPath), { recursive: true });
    fs.writeFileSync(sessionPath, `${JSON.stringify(codexOpenAICacheSessionEvent())}\n`);

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("codex_sessions.pricing");
  });

  it("does not subtract cached tokens from OpenAI cache-miss-only input", () => {
    const runDir = writeRun("openai-cache-miss-only", codexRecord({ usage: undefined }), codexEvents());
    const sessionPath = path.join(runDir, "framework", "codex-home", "sessions", "2026", "06", "18", "rollout.jsonl");
    fs.mkdirSync(path.dirname(sessionPath), { recursive: true });
    fs.writeFileSync(sessionPath, `${JSON.stringify(codexOpenAICacheSessionEvent({ prompt_tokens: undefined, total_tokens: undefined }))}\n`);

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("codex_sessions.pricing");
  });

  it("prices cache creation as regular input when no cache-write price is configured", () => {
    const runDir = writeRun("cache-create-regular-input", codexRecord({
      usage: {
        input_tokens: 600,
        input_tokens_include_cached: false,
        cache_creation_input_tokens: 100,
        cached_input_tokens: 300,
        output_tokens: 200,
        total_cost_usd: null,
      },
      resolved_config: {
        pricing: {
          models: {
            "gpt-5.4": {
              input_per_million_usd: 10,
              cached_input_per_million_usd: 1,
              output_per_million_usd: 20,
              input_includes_cached_tokens: false,
            },
          },
        },
      },
    }), codexEvents());

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(0.0113);
    expect(trace.summary.cost_source).toBe("run_record.pricing");
  });

  it("uses long-context pricing for GPT traces above the configured input threshold", () => {
    const runDir = writeRun("long-context-gpt", codexRecord({
      usage: {
        input_tokens: 300_000,
        cached_input_tokens: 100_000,
        output_tokens: 20_000,
        reasoning_output_tokens: 5_000,
        total_cost_usd: null,
      },
      resolved_config: {
        pricing: {
          models: {
            "gpt-5.4": {
              input_per_million_usd: 2.5,
              cached_input_per_million_usd: 0.25,
              output_per_million_usd: 15,
              reasoning_output_per_million_usd: 15,
              input_includes_cached_tokens: true,
              long_context_threshold_tokens: 270_000,
              long_context_input_per_million_usd: 5,
              long_context_cached_input_per_million_usd: 0.5,
              long_context_output_per_million_usd: 22.5,
              long_context_reasoning_output_per_million_usd: 22.5,
            },
          },
        },
      },
    }), codexEvents());

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(1.5);
    expect(trace.summary.cost_source).toBe("run_record.pricing");
  });

  it("does not apply long-context pricing when the model context window is below the threshold", () => {
    const runDir = writeRun("window-below-threshold", codexRecord({
      usage: {
        input_tokens: 13_000_000,
        cached_input_tokens: 4_000_000,
        output_tokens: 100_000,
        reasoning_output_tokens: 60_000,
        model_context_window: 258_400,
        total_cost_usd: null,
      },
      resolved_config: {
        pricing: {
          models: {
            "gpt-5.4": {
              input_per_million_usd: 2.5,
              cached_input_per_million_usd: 0.25,
              output_per_million_usd: 15,
              reasoning_output_per_million_usd: 15,
              input_includes_cached_tokens: true,
              long_context_threshold_tokens: 272_000,
              long_context_input_per_million_usd: 5,
              long_context_cached_input_per_million_usd: 0.5,
              long_context_output_per_million_usd: 22.5,
              long_context_reasoning_output_per_million_usd: 22.5,
            },
          },
        },
      },
    }), codexEvents());

    const trace = buildTrace(runDir);

    // Base rates despite 13M cumulative: a single request can't exceed the 258,400 window,
    // so it never reaches the 272,000 tier. 9M*2.5 + 4M*0.25 + 40k*15 + 60k*15 (per M) = 25.0
    expect(trace.summary.total_cost_usd).toBeCloseTo(25.0);
    expect(trace.summary.cost_source).toBe("run_record.pricing");
  });

  it("prices legacy runs from config_path when resolved_config has no pricing", () => {
    const configPath = path.join(tempDir, "configs", "scientific-hypothesis-generator", "base.yaml");
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.writeFileSync(configPath, [
      "pricing:",
      "  models:",
      "    gpt-5.4:",
      "      input_per_million_usd: 10",
      "      cached_input_per_million_usd: 1",
      "      output_per_million_usd: 20",
      "      reasoning_output_per_million_usd: 30",
      "      input_includes_cached_tokens: true",
      "",
    ].join("\n"));
    const runDir = writeRun("legacy-codex", codexRecord({
      config_path: "configs/scientific-hypothesis-generator/base.yaml",
      resolved_config: { version: 1 },
    }), codexEvents());

    const trace = buildTrace(runDir);

    expect(trace.summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
    expect(trace.summary.cost_source).toBe("config_path.pricing");
  });
});

describe("trace API routes", () => {
  it("returns trace run summaries", async () => {
    writeRun("api-run", codexRecord(), codexEvents());

    const response = await getRuns();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.runs_root).toBe(tempDir);
    expect(body.runs).toHaveLength(1);
    expect(body.runs[0].run_id).toBe("codex-run");
    expect(body.runs[0].summary.total_cost_usd).toBeCloseTo(EXPECTED_CODEX_COST);
  });

  it("returns a single trace by run id", async () => {
    writeRun("api-trace", codexRecord(), codexEvents());

    const response = await getTrace(new Request("http://localhost/api/traces/codex-run"), {
      params: Promise.resolve({ runId: ["codex-run"] }),
    });
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.run.run_id).toBe("codex-run");
    expect(body.summary.tool_call_count).toBe(1);
  });

  it("returns 404 for unknown trace ids", async () => {
    const response = await getTrace(new Request("http://localhost/api/traces/missing"), {
      params: Promise.resolve({ runId: ["missing"] }),
    });
    const body = await response.json();

    expect(response.status).toBe(404);
    expect(body.error).toContain("no run");
  });
});

function writeRun(runName: string, record: unknown, events: unknown[]): string {
  const runDir = path.join(tempDir, runName);
  const logFile = path.join(runDir, LOG_PATH);
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.writeFileSync(path.join(runDir, "run_record.json"), `${JSON.stringify(record, null, 2)}\n`);
  fs.writeFileSync(logFile, events.map((event) => JSON.stringify(event)).join("\n") + "\n");
  return runDir;
}

function writeLiveRun(runName: string, events: unknown[], resolvedConfig: string): string {
  const runDir = path.join(tempDir, runName);
  const logFile = path.join(runDir, LOG_PATH);
  const configFile = path.join(runDir, "framework", "input", "resolved_config.yaml");
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.mkdirSync(path.dirname(configFile), { recursive: true });
  fs.writeFileSync(configFile, resolvedConfig);
  fs.writeFileSync(logFile, events.map((event) => JSON.stringify(event)).join("\n") + "\n");
  return runDir;
}

function resolvedConfigYaml(): string {
  return [
    "version: 1",
    "harness: codex",
    "model: gpt-5.4",
    "pricing:",
    "  models:",
    "    gpt-5.4:",
    "      input_per_million_usd: 10",
    "      cached_input_per_million_usd: 1",
    "      output_per_million_usd: 20",
    "      reasoning_output_per_million_usd: 30",
    "      input_includes_cached_tokens: true",
    "",
  ].join("\n");
}

function claudeResolvedConfigYaml(): string {
  return [
    "version: 1",
    "harness: claude",
    "model: global.anthropic.claude-opus-4-6-v1",
    "pricing:",
    "  models:",
    "    global.anthropic.claude-opus-4-6-v1:",
    "      input_per_million_usd: 5",
    "      cached_input_per_million_usd: 0.5",
    "      cache_creation_input_per_million_usd: 6.25",
    "      output_per_million_usd: 25",
    "      input_includes_cached_tokens: false",
    "",
  ].join("\n");
}

function claudeRecord(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "claude-run",
    status: "succeeded",
    harness: "claude",
    model: "claude-opus-4.6",
    topic: "hypothesis generation",
    started_at: "2026-06-18T00:00:00.000Z",
    completed_at: "2026-06-18T00:00:17.000Z",
    ...overrides,
  };
}

function codexRecord(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "codex-run",
    status: "failed",
    harness: "codex",
    model: "gpt-5.4",
    topic: "hypothesis generation",
    started_at: "2026-06-18T00:00:00.000Z",
    completed_at: "2026-06-18T00:00:02.000Z",
    usage: {
      input_tokens: 1000,
      cached_input_tokens: 400,
      cache_creation_input_tokens: null,
      output_tokens: 200,
      reasoning_output_tokens: 50,
      total_tokens: 1200,
      total_cost_usd: null,
      turns: 1,
      duration_ms: 2000,
    },
    resolved_config: {
      pricing: {
        models: {
          "gpt-5.4": {
            input_per_million_usd: 10,
            cached_input_per_million_usd: 1,
            output_per_million_usd: 20,
            reasoning_output_per_million_usd: 30,
            input_includes_cached_tokens: true,
          },
        },
      },
    },
    ...overrides,
  };
}

function claudeEvents() {
  return [
    {
      event_index: 0,
      timestamp: "2026-06-18T00:00:01.000Z",
      type: "SystemMessage",
      message: { content: "system ready" },
    },
    {
      event_index: 1,
      timestamp: "2026-06-18T00:00:10.000Z",
      type: "AssistantMessage",
      message: {
        content: [
          { type: "text", text: "I will search the literature." },
          {
            type: "tool_use",
            id: "tool-1",
            name: "mcp__research__WebSearch",
            input: { query: "LLM reasoning distraction" },
          },
        ],
      },
    },
    {
      event_index: 2,
      timestamp: "2026-06-18T00:00:11.000Z",
      type: "UserMessage",
      message: {
        content: [
          {
            type: "tool_result",
            tool_use_id: "tool-1",
            content: [{ type: "text", text: "paper search results" }],
            is_error: false,
          },
        ],
      },
    },
    {
      event_index: 3,
      timestamp: "2026-06-18T00:00:12.000Z",
      type: "ResultMessage",
      message: {
        result: "Done.",
        model_usage: {
          claude: { costUSD: 0.25 },
        },
      },
    },
  ];
}

function claudeLiveUsageEvents() {
  return [
    {
      event_index: 0,
      timestamp: "2026-06-18T00:00:00.000Z",
      type: "SystemMessage",
      message: {
        subtype: "init",
        data: {
          model: "global.anthropic.claude-opus-4-6-v1",
        },
      },
    },
    {
      event_index: 1,
      timestamp: "2026-06-18T00:00:01.000Z",
      type: "AssistantMessage",
      message: {
        content: [{ thinking: "thinking" }],
        model: "claude-opus-4-6",
        message_id: "msg-1",
        usage: {
          input_tokens: 10,
          cache_creation_input_tokens: 1000,
          cache_read_input_tokens: 0,
          output_tokens: 20,
        },
      },
    },
    {
      event_index: 2,
      timestamp: "2026-06-18T00:00:02.000Z",
      type: "AssistantMessage",
      message: {
        content: [{ text: "same message split across events" }],
        model: "claude-opus-4-6",
        message_id: "msg-1",
        usage: {
          input_tokens: 10,
          cache_creation_input_tokens: 1000,
          cache_read_input_tokens: 0,
          output_tokens: 20,
        },
      },
    },
    {
      event_index: 3,
      timestamp: "2026-06-18T00:00:03.000Z",
      type: "AssistantMessage",
      message: {
        content: [{ text: "new response" }],
        model: "claude-opus-4-6",
        message_id: "msg-2",
        usage: {
          input_tokens: 5,
          cache_creation_input_tokens: 0,
          cache_read_input_tokens: 2000,
          output_tokens: 30,
        },
      },
    },
  ];
}

function codexEvents() {
  const payload = (item: unknown) => ({
    message: {
      event: {
        payload: item,
      },
    },
  });

  return [
    {
      event_index: 0,
      timestamp: "2026-06-18T00:00:00.100Z",
      type: "codex_turn_start",
      phase: "main",
      message: {},
    },
    {
      event_index: 1,
      timestamp: "2026-06-18T00:00:00.200Z",
      type: "item/completed",
      ...payload({
        item: {
          id: "user-1",
          type: "userMessage",
          content: [{ text: "Generate a hypothesis." }],
        },
      }),
    },
    {
      event_index: 2,
      timestamp: "2026-06-18T00:00:00.300Z",
      type: "item/started",
      ...payload({
        item: {
          id: "mcp-1",
          type: "mcpToolCall",
          tool: "mcp__research__web_search",
          arguments: { query: "LLM reasoning" },
        },
      }),
    },
    {
      event_index: 3,
      timestamp: "2026-06-18T00:00:00.550Z",
      type: "item/completed",
      ...payload({
        item: {
          id: "mcp-1",
          type: "mcpToolCall",
          tool: "mcp__research__web_search",
          arguments: { query: "LLM reasoning" },
          durationMs: 250,
          result: {
            content: [{ type: "text", text: "search results" }],
          },
        },
      }),
    },
    {
      event_index: 4,
      timestamp: "2026-06-18T00:00:00.900Z",
      type: "item/completed",
      ...payload({
        item: {
          id: "agent-1",
          type: "agentMessage",
          text: "Here is the proposal.",
        },
      }),
    },
    {
      event_index: 5,
      timestamp: "2026-06-18T00:00:01.300Z",
      type: "turn/completed",
      ...payload({
        turn: {
          status: "completed",
          startedAt: "2026-06-18T00:00:00.100Z",
          completedAt: "2026-06-18T00:00:01.300Z",
          durationMs: 1200,
        },
      }),
    },
  ];
}

function codexTokenUsageEvent() {
  return {
    event_index: 6,
    timestamp: "2026-06-18T00:00:01.400Z",
    type: "ThreadTokenUsageUpdatedNotification",
    message: {
      event: {
        payload: {
          token_usage: {
            total: {
              inputTokens: 1000,
              cachedInputTokens: 400,
              outputTokens: 200,
              reasoningOutputTokens: 50,
              totalTokens: 1200,
            },
          },
        },
      },
    },
  };
}

function codexSessionTokenCountEvent() {
  return {
    timestamp: "2026-06-18T00:00:01.500Z",
    type: "event_msg",
    payload: {
      type: "token_count",
      info: {
        total_token_usage: {
          input_tokens: 1000,
          cached_input_tokens: 400,
          output_tokens: 200,
          reasoning_output_tokens: 50,
          total_tokens: 1200,
        },
      },
    },
  };
}

function codexOpenAICacheSessionEvent(overrides: Record<string, unknown> = {}) {
  const totalTokenUsage: Record<string, unknown> = {
    prompt_tokens: 1000,
    prompt_cache_hit_tokens: 400,
    prompt_cache_miss_tokens: 600,
    completion_tokens: 200,
    reasoning_tokens: 50,
    total_tokens: 1200,
    ...overrides,
  };
  for (const [key, value] of Object.entries(totalTokenUsage)) {
    if (value === undefined) delete totalTokenUsage[key];
  }
  return {
    timestamp: "2026-06-18T00:00:01.500Z",
    type: "event_msg",
    payload: {
      type: "token_count",
      info: {
        total_token_usage: totalTokenUsage,
      },
    },
  };
}
