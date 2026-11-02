export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface TraceRunSummary {
  run_id: string;
  // Unique id = path relative to the runs root: "<model-profile>/<run-id>" when grouped
  // by model profile, or just "<run-id>" for the legacy flat layout. run_id alone is no
  // longer unique (the same run-id can exist under different model profiles).
  key?: string;
  model_profile?: string;
  status?: string;
  harness?: string;
  model?: string;
  topic?: string;
  started_at?: string;
  completed_at?: string;
  path: string;
  has_record: boolean;
  summary?: TraceSummary;
}

export interface TraceMessage {
  role: "user" | "assistant" | "system";
  kind: "text" | "thinking" | "reasoning" | "final_result" | "system";
  text: string;
  timestamp?: string;
  event_index?: number;
}

export interface TraceToolCall {
  id: string;
  name: string;
  raw_name?: string;
  type: "mcp" | "command" | "file" | "web" | "agent" | "tool";
  input: JsonValue;
  result: JsonValue | undefined;
  is_error: boolean;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  event_index?: number;
  result_event_index?: number;
  exit_code?: number;
}

export interface TraceTurn {
  index: number;
  phase?: string;
  status?: string;
  error?: JsonValue;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  messages: TraceMessage[];
  tool_calls: TraceToolCall[];
}

export interface TracePayload {
  schema_version: 1;
  harness: "claude" | "codex";
  file_complete: boolean;
  run: Omit<TraceRunSummary, "path" | "has_record">;
  summary: TraceSummary;
  turns: TraceTurn[];
  raw_events: JsonValue[];
  computed_at: string;
}

export interface TraceSummary {
  event_total: number;
  turn_count: number;
  message_count: number;
  tool_call_count: number;
  total_time_ms?: number;
  total_time_source?: string;
  total_cost_usd?: number;
  cost_source?: string;
  cost_note?: string;
}

export interface TraceRunsResponse {
  runs_root: string;
  runs: TraceRunSummary[];
}
