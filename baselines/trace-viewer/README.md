# Trace Viewer

A web UI for inspecting baseline runs. For each run directory it reads:

- `run_record.json`
- `framework/logs/adapter_events.jsonl`

and shows every user/assistant turn, tool call and tool result, along with per-turn latency, total duration and USD cost.

Runs that are still in progress are re-read every 3 seconds; polling stops once a run reaches `succeeded`, `failed`, `cancelled`, `timeout` or `error`. Auto-refresh can be paused from the top-right corner.

Stack: Next.js 15, React 19, TypeScript, Tailwind CSS, pnpm workspace, Turbo.

## Install

```bash
cd baselines/trace-viewer
pnpm install
```

## Run

```bash
# Reads ./.runs by default; point it at the baselines run directory explicitly:
TRACE_RUNS_DIR=../.runs pnpm --filter web dev --hostname 127.0.0.1
```

Then open <http://127.0.0.1:3000/traces>.

| Env var | Description |
|---|---|
| `TRACE_RUNS_DIR` | Root directory of runs (usually `baselines/.runs`). Defaults to `.runs` under the current directory. |

## Test and build

```bash
pnpm --filter web test
pnpm --filter web build
```

## API

```text
GET /api/traces/runs       # list of runs
GET /api/traces/:runId     # normalized trace: summary, turns, tool calls, raw events
```
