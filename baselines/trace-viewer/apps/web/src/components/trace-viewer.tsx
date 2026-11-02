"use client";

import * as React from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  DollarSign,
  FileJson,
  MessageSquare,
  Pause,
  Play,
  RefreshCw,
  Search,
  Terminal,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type {
  JsonValue,
  TraceMessage,
  TracePayload,
  TraceRunSummary,
  TraceSummary,
  TraceToolCall,
  TraceTurn,
} from "@/lib/types";

interface RunsBody {
  runs_root: string;
  runs: TraceRunSummary[];
}

const AUTO_REFRESH_MS = 3000;

// run_id is no longer unique across model-profile folders, so identify runs by their
// unique key (path relative to the runs root), falling back to run_id for safety.
function runKey(run: TraceRunSummary | undefined): string | undefined {
  return run?.key ?? run?.run_id;
}

function readSelectedRun(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("run");
}

function writeSelectedRun(runId: string | null) {
  const params = new URLSearchParams(window.location.search);
  if (runId) params.set("run", runId);
  else params.delete("run");
  const query = params.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

export function TraceViewer() {
  const [runsRoot, setRunsRoot] = React.useState("");
  const [runs, setRuns] = React.useState<TraceRunSummary[]>([]);
  const [runsError, setRunsError] = React.useState<string | null>(null);
  const [runsLoading, setRunsLoading] = React.useState(false);
  const [selectedRun, setSelectedRun] = React.useState<string | null>(null);
  const [trace, setTrace] = React.useState<TracePayload | null>(null);
  const [traceError, setTraceError] = React.useState<string | null>(null);
  const [traceLoading, setTraceLoading] = React.useState(false);
  const [autoRefresh, setAutoRefresh] = React.useState(true);
  const [lastRefreshedAt, setLastRefreshedAt] = React.useState<string | null>(null);
  const [query, setQuery] = React.useState("");
  const traceRef = React.useRef<TracePayload | null>(null);
  const selectedRunRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    selectedRunRef.current = selectedRun;
  }, [selectedRun]);

  const loadRuns = React.useCallback(async () => {
    setRunsLoading(true);
    setRunsError(null);
    try {
      const res = await fetch("/api/traces/runs", { cache: "no-store" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      const data = body as RunsBody;
      setRunsRoot(data.runs_root);
      setRuns(
        traceRef.current && selectedRunRef.current
          ? mergeTraceSummaryIntoRuns(data.runs, traceRef.current, selectedRunRef.current)
          : data.runs
      );
      const urlRun = readSelectedRun();
      const next =
        urlRun && data.runs.some((run) => runKey(run) === urlRun)
          ? urlRun
          : runKey(data.runs[0]) ?? null;
      setSelectedRun((current) => current ?? next);
      if (!urlRun && next) writeSelectedRun(next);
    } catch (exc) {
      setRunsError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const loadTrace = React.useCallback(async (runId: string) => {
    setTraceLoading(true);
    setTraceError(null);
    try {
      const res = await fetch(`/api/traces/${runId.split("/").map(encodeURIComponent).join("/")}`, {
        cache: "no-store",
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      const nextTrace = body as TracePayload;
      traceRef.current = nextTrace;
      setTrace(nextTrace);
      setRuns((current) => mergeTraceSummaryIntoRuns(current, nextTrace, runId));
      setLastRefreshedAt(new Date().toISOString());
    } catch (exc) {
      traceRef.current = null;
      setTrace(null);
      setTraceError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setTraceLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  React.useEffect(() => {
    if (!selectedRun) return;
    writeSelectedRun(selectedRun);
    loadTrace(selectedRun);
  }, [selectedRun, loadTrace]);

  const runsWithLiveSummary = React.useMemo(
    () =>
      runs.map((run) =>
        trace && runKey(run) === selectedRun
          ? {
              ...run,
              status: trace.run.status ?? run.status,
              harness: trace.run.harness ?? run.harness,
              model: trace.run.model ?? run.model,
              topic: trace.run.topic ?? run.topic,
              started_at: trace.run.started_at ?? run.started_at,
              completed_at: trace.run.completed_at ?? run.completed_at,
              summary: trace.summary,
            }
          : run
      ),
    [runs, trace, selectedRun]
  );
  const autoRefreshActive = autoRefresh && Boolean(selectedRun);

  React.useEffect(() => {
    if (!selectedRun || !autoRefreshActive) return;
    const timer = window.setInterval(() => {
      loadTrace(selectedRun);
      loadRuns();
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [autoRefreshActive, selectedRun, loadTrace, loadRuns]);

  const visibleRuns = React.useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return runsWithLiveSummary;
    return runsWithLiveSummary.filter((run) =>
      [run.run_id, run.status, run.harness, run.model, run.topic, run.model_profile]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(needle)
    );
  }, [runsWithLiveSummary, query]);

  return (
    <div className="flex h-screen min-h-0 bg-background text-foreground">
      <aside className="flex w-[22rem] shrink-0 flex-col border-r bg-card">
        <div className="border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <h1 className="text-sm font-semibold">IdeaEval Trace Viewer</h1>
              <p className="truncate text-xs text-muted-foreground" title={runsRoot}>
                {runsRoot || "loading runs root"}
              </p>
            </div>
            <Button
              size="icon"
              variant="outline"
              title="Refresh runs"
              onClick={loadRuns}
              disabled={runsLoading}
            >
              <RefreshCw className={cn(runsLoading && "animate-spin")} />
            </Button>
          </div>
          <div className="relative mt-3">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search runs, models, topics"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
        <RunList
          runs={visibleRuns}
          selectedRun={selectedRun}
          loading={runsLoading}
          error={runsError}
          onSelect={setSelectedRun}
        />
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        {!selectedRun ? (
          <EmptyState title="No run selected" detail="Select a run from the sidebar." />
        ) : traceLoading && !trace ? (
          <EmptyState title="Loading trace" detail={selectedRun} />
        ) : traceError ? (
          <EmptyState title="Trace load failed" detail={traceError} destructive />
        ) : trace ? (
          <TraceDetail
            trace={trace}
            autoRefresh={autoRefresh}
            autoRefreshActive={autoRefreshActive}
            lastRefreshedAt={lastRefreshedAt}
            onToggleAutoRefresh={() => setAutoRefresh((value) => !value)}
            onRefresh={() => selectedRun && loadTrace(selectedRun)}
          />
        ) : (
          <EmptyState title="No trace" detail="Trace data is unavailable." />
        )}
      </main>
    </div>
  );
}

function RunList(props: {
  runs: TraceRunSummary[];
  selectedRun: string | null;
  loading: boolean;
  error: string | null;
  onSelect: (runId: string) => void;
}) {
  if (props.error) {
    return (
      <div className="p-4 text-sm text-destructive">
        <AlertCircle className="mb-2 h-4 w-4" />
        {props.error}
      </div>
    );
  }
  if (!props.runs.length) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        {props.loading ? "Loading runs..." : "No runs found."}
      </div>
    );
  }
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto p-2">
      {props.runs.map((run) => (
        <li key={runKey(run)}>
          <button
            className={cn(
              "mb-1 w-full rounded-md border px-3 py-2 text-left transition-colors hover:bg-accent",
              props.selectedRun === runKey(run)
                ? "border-primary bg-accent"
                : "border-transparent bg-card"
            )}
            onClick={() => props.onSelect(runKey(run) ?? run.run_id)}
          >
            <div className="flex min-w-0 items-center gap-2">
              <StatusDot status={run.status} />
              <span className="min-w-0 flex-1 truncate font-mono text-xs font-semibold">
                {run.run_id}
              </span>
              {run.model_profile && (
                <Badge variant="secondary" className="shrink-0 font-mono">
                  {run.model_profile}
                </Badge>
              )}
              {run.harness && (
                <Badge variant="outline" className="shrink-0 font-mono">
                  {run.harness}
                </Badge>
              )}
            </div>
            {run.topic && (
              <p className="mt-1 max-h-10 overflow-hidden text-xs text-muted-foreground">
                {run.topic}
              </p>
            )}
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {[formatDate(run.started_at), run.model].filter(Boolean).join(" · ")}
            </p>
            <RunMetrics summary={run.summary} />
          </button>
        </li>
      ))}
    </ul>
  );
}

function TraceDetail(props: {
  trace: TracePayload;
  autoRefresh: boolean;
  autoRefreshActive: boolean;
  lastRefreshedAt: string | null;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
}) {
  const { trace } = props;
  return (
    <div className="mx-auto flex max-w-[96rem] flex-col gap-4 px-6 py-5">
      <header className="flex flex-wrap items-start gap-3 border-b pb-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-xl font-semibold">{trace.run.run_id}</h2>
            <StatusBadge status={trace.run.status} />
            <Badge variant="outline">{trace.harness}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {[trace.run.model, trace.run.topic].filter(Boolean).join(" · ") ||
              "No model/topic metadata"}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {props.autoRefreshActive
              ? `Live refresh every ${AUTO_REFRESH_MS / 1000}s`
              : props.lastRefreshedAt
                ? `Last refreshed ${formatTime(props.lastRefreshedAt)}`
                : "Not refreshed yet"}
          </p>
        </div>
        <Button
          variant={props.autoRefresh ? "default" : "outline"}
          size="sm"
          title={props.autoRefresh ? "Pause auto refresh" : "Resume auto refresh"}
          onClick={props.onToggleAutoRefresh}
        >
          {props.autoRefresh ? <Pause /> : <Play />}
          {props.autoRefreshActive ? "Live" : "Auto"}
        </Button>
        <Button variant="outline" size="sm" onClick={props.onRefresh}>
          <RefreshCw /> Refresh
        </Button>
      </header>

      <section className="grid gap-3">
        <SectionTitle icon={<MessageSquare />} title="Turns" detail={`${trace.turns.length} turns`} />
        {trace.turns.length ? (
          trace.turns.map((turn) => <TurnPanel key={turn.index} turn={turn} />)
        ) : (
          <MutedBox>No normalized user/assistant turns found.</MutedBox>
        )}
      </section>

      <section className="grid gap-3 pb-10">
        <SectionTitle icon={<FileJson />} title="Raw Events" detail={`${trace.raw_events.length} events`} />
        <details className="rounded-md border bg-card">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Show raw adapter_events.jsonl JSON
          </summary>
          <pre className="max-h-[34rem] overflow-auto border-t bg-muted/40 p-4 text-xs leading-relaxed">
            {jsonText(trace.raw_events)}
          </pre>
        </details>
      </section>
    </div>
  );
}

function RunMetrics({ summary }: { summary?: TraceSummary }) {
  return (
    <div className="mt-2 grid grid-cols-3 gap-1 font-mono text-[11px] text-muted-foreground">
      <MetricPill
        icon={<Clock />}
        value={formatDuration(summary?.total_time_ms)}
        title={summary?.total_time_source ? `time source: ${summary.total_time_source}` : "time unavailable"}
      />
      <MetricPill
        icon={<DollarSign />}
        value={formatCost(summary?.total_cost_usd)}
        title={summary?.cost_source ?? summary?.cost_note ?? "cost unavailable"}
      />
      <MetricPill
        icon={<Wrench />}
        value={summary?.tool_call_count === undefined ? "N/A" : String(summary.tool_call_count)}
        title={summary ? `${summary.event_total} events` : "tool calls unavailable"}
      />
    </div>
  );
}

function mergeTraceSummaryIntoRuns(
  runs: TraceRunSummary[],
  trace: TracePayload,
  key: string
): TraceRunSummary[] {
  let seen = false;
  const next = runs.map((run) => {
    if (runKey(run) !== key) return run;
    seen = true;
    return traceRunSummary(run, trace);
  });
  if (seen) return next;
  return [
    traceRunSummary(
      {
        run_id: trace.run.run_id,
        key,
        path: "",
        has_record: true,
      },
      trace
    ),
    ...next,
  ];
}

function traceRunSummary(run: TraceRunSummary, trace: TracePayload): TraceRunSummary {
  return {
    ...run,
    status: trace.run.status ?? run.status,
    harness: trace.run.harness ?? run.harness,
    model: trace.run.model ?? run.model,
    topic: trace.run.topic ?? run.topic,
    started_at: trace.run.started_at ?? run.started_at,
    completed_at: trace.run.completed_at ?? run.completed_at,
    summary: trace.summary,
  };
}

function MetricPill(props: { icon: React.ReactNode; value: string; title: string }) {
  return (
    <div
      className="flex min-w-0 items-center gap-1 rounded border bg-background/60 px-1.5 py-1"
      title={props.title}
    >
      <span className="shrink-0 [&_svg]:h-3.5 [&_svg]:w-3.5">{props.icon}</span>
      <span className="min-w-0 truncate">{props.value}</span>
    </div>
  );
}

function TurnPanel({ turn }: { turn: TraceTurn }) {
  return (
    <details className="rounded-md border bg-card" open={turn.index <= 3}>
      <summary className="cursor-pointer border-b bg-muted/30 px-4 py-3">
        <div className="inline-flex w-full flex-wrap items-center gap-2 pr-2">
          <span className="font-semibold">Turn {turn.index}</span>
          {turn.phase && <Badge variant="outline">{turn.phase}</Badge>}
          {turn.status && <Badge variant="secondary">{turn.status}</Badge>}
          <span className="ml-auto font-mono text-xs text-muted-foreground">
            {formatDuration(turn.duration_ms)} · {turn.messages.length} messages · {turn.tool_calls.length} tools
          </span>
        </div>
      </summary>
      <div className="grid gap-4 p-4">
        {turn.error && (
          <pre className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
            {jsonText(turn.error)}
          </pre>
        )}
        <div className="grid gap-2">
          <SectionTitle title="Messages" detail={`${turn.messages.length}`} />
          {turn.messages.length ? (
            turn.messages.map((message, idx) => (
              <MessageBlock key={`${message.event_index ?? idx}-${idx}`} message={message} />
            ))
          ) : (
            <MutedBox>No messages.</MutedBox>
          )}
        </div>
        {turn.tool_calls.length > 0 && (
          <div className="grid gap-2">
            <SectionTitle title="Tool Calls" detail={`${turn.tool_calls.length}`} />
            {turn.tool_calls.map((tool, idx) => (
              <ToolBlock key={`${tool.id || tool.name}-${idx}`} tool={tool} />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

function MessageBlock({ message }: { message: TraceMessage }) {
  return (
    <article
      className={cn(
        "overflow-hidden rounded-md border",
        message.role === "assistant" && "border-l-4 border-l-primary",
        message.role === "user" && "border-l-4 border-l-green-600"
      )}
    >
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        <Badge variant="outline">{message.role}</Badge>
        <span className="font-mono text-xs text-muted-foreground">{message.kind}</span>
        <span className="ml-auto font-mono text-xs text-muted-foreground">
          {formatTime(message.timestamp)}
        </span>
      </div>
      <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap p-3 text-sm leading-relaxed">
        {message.text}
      </pre>
    </article>
  );
}

function ToolBlock({ tool }: { tool: TraceToolCall }) {
  return (
    <article
      className={cn(
        "overflow-hidden rounded-md border",
        tool.is_error ? "border-l-4 border-l-destructive" : "border-l-4 border-l-green-600"
      )}
    >
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        <Terminal className="h-4 w-4 text-muted-foreground" />
        <span className="font-mono text-sm font-semibold">{tool.name}</span>
        <Badge variant="outline">{tool.type}</Badge>
        <span className="font-mono text-xs text-muted-foreground">{formatDuration(tool.duration_ms)}</span>
        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
          {tool.is_error ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-green-600" />
          )}
          {tool.completed_at ? (tool.is_error ? "error" : "ok") : "running"}
        </span>
      </div>
      <JsonDetails label="input" value={tool.input} defaultOpen />
      {tool.result === undefined ? (
        <MutedBox className="rounded-none border-0 border-t">No tool result.</MutedBox>
      ) : (
        <JsonDetails label={tool.is_error ? "result · error" : "result"} value={tool.result} defaultOpen />
      )}
    </article>
  );
}

function JsonDetails(props: { label: string; value: JsonValue; defaultOpen?: boolean }) {
  return (
    <details className="border-t first:border-t-0" open={props.defaultOpen}>
      <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase text-muted-foreground">
        {props.label}
      </summary>
      <pre className="max-h-[28rem] overflow-auto bg-muted/30 p-3 text-xs leading-relaxed">
        {jsonText(props.value)}
      </pre>
    </details>
  );
}

function SectionTitle(props: { icon?: React.ReactNode; title: string; detail?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
      {props.icon && <span className="[&_svg]:h-4 [&_svg]:w-4">{props.icon}</span>}
      <span>{props.title}</span>
      {props.detail && <span className="font-mono font-normal">{props.detail}</span>}
    </div>
  );
}

function EmptyState(props: { title: string; detail: string; destructive?: boolean }) {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className={cn("max-w-xl rounded-md border bg-card p-6 text-center", props.destructive && "border-destructive/40")}>
        <h2 className="text-lg font-semibold">{props.title}</h2>
        <p className={cn("mt-2 text-sm text-muted-foreground", props.destructive && "text-destructive")}>
          {props.detail}
        </p>
      </div>
    </div>
  );
}

function MutedBox(props: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground", props.className)}>
      {props.children}
    </div>
  );
}

function StatusDot({ status }: { status?: string }) {
  return (
    <span
      className={cn(
        "h-2.5 w-2.5 shrink-0 rounded-full bg-muted-foreground",
        status === "succeeded" && "bg-green-600",
        status === "running" && "bg-yellow-500",
        ["failed", "error", "cancelled", "timeout"].includes(status ?? "") && "bg-destructive"
      )}
      title={status ?? "unknown"}
    />
  );
}

function StatusBadge({ status }: { status?: string }) {
  const failed = ["failed", "error", "cancelled", "timeout"].includes(status ?? "");
  return (
    <Badge
      variant={status === "succeeded" || failed ? "outline" : "secondary"}
      className={cn(
        status === "succeeded" && "border-green-600 text-green-600",
        failed && "border-destructive text-destructive"
      )}
    >
      {status ?? "unknown"}
    </Badge>
  );
}

function formatDuration(ms: number | undefined): string {
  if (ms === undefined) return "N/A";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const min = Math.floor(ms / 60_000);
  const sec = Math.round((ms % 60_000) / 1000);
  return `${min}m ${sec}s`;
}

function formatCost(value: number | undefined): string {
  if (value === undefined) return "N/A";
  if (value === 0) return "$0.0000";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 100) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function formatDate(value: string | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatTime(value: string | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function jsonText(value: JsonValue): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
