# Python API Spec

> **Status:** Implemented MVP Python API contract.
> **Last updated:** 2026-06-03
> **Runtime contracts:** [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md)
> **CLI spec:** [`CLI_SPEC.md`](./CLI_SPEC.md)

This document defines the public Python API for the MVP autonomous research-ideation agent. The API is the shared execution surface used by the CLI, tests, future automation, and future UI layers. The CLI must call this API rather than reimplementing run setup, adapter dispatch, validation, or repair behavior.

## Scope

The MVP Python API exposes one blocking run function, one lower-level request runner, one config loader, and the typed runtime records defined in [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md). It does not expose harness-specific SDK objects, direct MCP clients, prompt-rendering internals, validator internals, or adapter-specific event streams as public MVP API.

The public API is synchronous from the caller's perspective. Adapters may use async SDKs internally, but the MVP caller receives a completed `RunResult` or a pre-run exception from a normal blocking function call.

## Public Import Surface

The stable package import root is `research_ideation_agent`. The implementation may organize internals under subpackages, but these public symbols must be available through documented import paths:

```python
from research_ideation_agent import load_config, run_research_ideation, run_request
from research_ideation_agent.models import RunConfig, RunRequest, RunResult, ValidationResult, RepairAttempt, UsageSummary
from research_ideation_agent.errors import ConfigError, ResearchIdeationError, TopicError, WorkspaceError
```

The CLI should use only these public imports plus CLI-specific argument parsing helpers. It must not import adapters, validators, prompt renderers, or MCP renderers directly.

## Data Model Contract

`RunConfig`, `RunRequest`, `RunResult`, `ValidationResult`, `RepairAttempt`, and `UsageSummary` are the public data models. Their fields and meanings are defined in [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md).

The implementation may use `dataclasses`, `attrs`, Pydantic, or another validation mechanism, but public model instances must provide attribute access using the documented field names and deterministic serialization through `to_dict()` or an equivalent documented method. Serialization must convert `Path` values to strings and must not include secret values.

The public model objects should be immutable after construction or treated as immutable by API callers. If the implementation allows mutation for convenience, `run_research_ideation` and `run_request` must not mutate caller-provided model objects in place.

## Entrypoints

### `load_config`

```python
def load_config(
    path: str | Path,
    *,
    overrides: Mapping[str, object] | None = None,
    project_root: Path | None = None,
) -> RunConfig:
    ...
```

`load_config` reads a YAML config file, applies runtime defaults, applies API overrides, resolves host filesystem paths, validates the resolved config, and returns a `RunConfig`.

`overrides` uses the same dotted-key semantics as CLI `--set`, but values are already typed Python values rather than strings:

```python
config = load_config(
    "configs/scientific-hypothesis-generator/gpt.yaml",
    overrides={
        "harness": "codex",
        "repair.max_attempts": 2,
        "skills.selected": ["literature-grounding", "proposal-writing-and-final-check"],
    },
)
```

`load_config` must reject unknown fields, invalid enum values, invalid override paths, malformed YAML, missing required config fields, and config attempts to provide secret values. It must not create a run workspace, render prompts, launch adapters, or read secret values from the environment.

### `run_research_ideation`

```python
def run_research_ideation(
    topic: str,
    config: RunConfig | str | Path,
    *,
    overrides: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> RunResult:
    ...
```

`run_research_ideation` is the main public MVP API. It validates the topic, resolves config when `config` is a path, builds a `RunRequest`, creates the run workspace, launches the selected harness through the adapter boundary, runs final validation, performs configured repair attempts, writes run records, and returns a `RunResult`.

When `config` is a path, `run_research_ideation` must call `load_config(config, overrides=overrides, project_root=project_root)` and record the config path and applied overrides in the request metadata. When `config` is already a `RunConfig`, `overrides` and `project_root` must both be `None`; callers that need overrides should apply them through `load_config` before calling.

`load_config` and `run_research_ideation` accept an optional `project_root: Path | None` keyword. Relative paths in the YAML and in dotted overrides are resolved against the project root. When `project_root` is `None`, `load_config` reads `RIA_PROJECT_ROOT` from the environment if set, and otherwise discovers the project root by walking upward from the config file's parent directory looking for `pyproject.toml`. If neither an explicit `project_root` nor a discovered `pyproject.toml` ancestor is available, `load_config` raises `ConfigError`. Absolute paths in the YAML or overrides are used as-is.

`env` defaults to the process environment. The public Python API does not load `.env`; current-directory `.env` loading is owned by the CLI before it calls this API. The framework uses the effective environment to look up required environment variables named by config, such as `WTS_API_KEY`, and selected-harness API variables. Claude runs read the env-var names configured by `adapters.claude.api_key_env` and `adapters.claude.base_url_env` (commonly `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`, but configurable per-config to point at different name pairs such as `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL`); Codex runs read the env-var names configured by `adapters.codex.api_key_env` and `adapters.codex.base_url_env` (defaulting to `GPT_API_KEY` / `GPT_API_BASE_URL`). Optional environment variables such as `WTS_BASE_URL` are forwarded only when present. Secret values from `env` must not be written to `resolved_config.yaml`, `run_record.json`, generated redacted MCP artifacts, or error strings. Adapter event logs are a deliberate exception: Claude event logs may contain full SDK messages and tool payloads, while Codex event logs contain serialized SDK turn notifications and completed-turn `TurnResult` summaries. Codex native session JSONL files may also be archived under `framework/logs/codex_sessions/` when per-run `CODEX_HOME` cleanup is enabled. The framework must not add adapter launch environment maps or config secret values to those events, but it does not redact arbitrary model or tool message content; callers should treat event logs as sensitive debugging artifacts.

### `run_request`

```python
def run_request(
    request: RunRequest,
    *,
    env: Mapping[str, str] | None = None,
) -> RunResult:
    ...
```

`run_request` is the lower-level public execution API for tests and automation that already construct a resolved `RunRequest`. It must enforce the same workspace, adapter, validation, repair, logging, and secret-handling semantics as `run_research_ideation`.

`run_research_ideation` is a convenience wrapper around config resolution plus `run_request`. The two paths must not diverge behaviorally after the `RunRequest` exists.

## Topic Rules

The API validates that `topic` is a string and is non-empty after trimming leading and trailing whitespace. The original topic string is preserved in run metadata and prompt rendering; the framework must not rewrite, summarize, classify, or ask the user to refine it.

An invalid topic raises `TopicError` before workspace creation.

## Config And Override Rules

Public API overrides are mappings from dotted config paths to typed Python values. List overrides replace whole lists; they are not merged. Overrides may select skills or subagents by ID, but they may not provide secret values or harness-specific raw config blobs beyond explicitly documented adapter passthrough fields.

Host filesystem paths in resolved `RunConfig` should be absolute `Path` values where practical. Agent-workspace-internal paths, such as `mcp.scholar_gateway.references_dir`, remain relative path strings and are resolved under the per-run agent workspace by the workspace/MCP renderer.

Missing required environment variables are treated as pre-run config errors and raise `ConfigError` before adapter launch. Required variables include both `mcp.scholar_gateway.required_env_vars` and the selected harness API variables configured above. Missing optional environment variables do not fail the run and are simply omitted from adapter or MCP launch environments. The implementation checks required variables before workspace creation and before writing adapter config that would contain unresolved secret placeholders.

## Result And Failure Semantics

The API returns `RunResult` for completed run attempts and for run-level failures that occur after a run workspace has been created. `RunResult.status` is `succeeded` only when final validation passes. `RunResult.status` is `failed` when validation fails after configured repair attempts, the adapter fails, a timeout or budget bound interrupts the run, or another runtime failure prevents a valid proposal.

`RunResult.failure_kind` is the machine-readable failure category. It is `None` on success and one of these values on failure:

| Value | Meaning |
|---|---|
| `validation_failed` | The run completed and all configured repair attempts were exhausted, but final mechanical validation still failed. |
| `adapter_error` | The selected harness adapter failed before a completed validation result was available. |
| `timeout` | A configured timeout interrupted the run. |
| `budget_exceeded` | A configured budget or turn bound interrupted the run. |
| `cancelled` | The run was externally cancelled. |
| `runtime_error` | A non-adapter runtime failure occurred after a run workspace existed. |

`RunResult.error` is a concise human-readable explanation. Callers must use `failure_kind` for control flow and reserve `error` for display or logs.

## Exceptions

Pre-run caller, topic, and configuration errors raise typed exceptions and do not return `RunResult`.

| Exception | When raised | CLI mapping |
|---|---|---:|
| `ResearchIdeationError` | Base class for project API errors. | Depends on subclass |
| `TopicError` | Topic is not a valid non-empty string. | `2` |
| `ConfigError` | Config parse, validation, override, missing env var, or forbidden secret-in-config error. | `2` |
| `WorkspaceError` | Workspace cannot be created before a valid run record can be written. | `3` |

After a run workspace exists, the API should prefer a failed `RunResult` over raising for expected operational failures, because callers need artifact paths, logs, and validation results. Unexpected programmer bugs may still propagate as normal Python exceptions.

## Side Effects

`load_config` has no run side effects beyond reading the config file and resolving paths. It must not create `.runs/`, render prompts, package skills, write MCP config, or touch adapter state.

`run_research_ideation` and `run_request` create exactly one per-run directory under `workspace.root` unless they fail during pre-run validation. They write framework-owned metadata under `framework/`, pass `<workspace.agent_dir>/` as the adapter working directory, and write or expect `proposal.md` under that agent workspace.

The API must not write outside the configured per-run directory except for harness-controlled caches or SDK behavior that cannot be avoided and is documented by the adapter. Project-owned artifacts belong under the run directory.

## Serialization

Every public model must be serializable to JSON-compatible dictionaries for `run_record.json` and CLI `--json` output. Serialization must include only harness-neutral fields unless a value is stored under an explicit `artifacts` path. Adapter event details may be stored only in `adapter_events.jsonl` when `logging.retain_event_log` is enabled; they must not be embedded in `RunResult`, `run_record.json`, or CLI `--json`. Claude event details are full raw SDK messages, while Codex event details are serialized SDK turn notifications plus completed-turn `TurnResult` summaries.

`RunResult.to_dict()` or the equivalent serializer must include:

```json
{
  "run_id": "20260602-153012-a8f3c1",
  "status": "succeeded",
  "failure_kind": null,
  "harness": "codex",
  "workspace_path": ".runs/20260602-153012-a8f3c1",
  "agent_workspace_path": ".runs/20260602-153012-a8f3c1/agent_workspace",
  "proposal_path": ".runs/20260602-153012-a8f3c1/agent_workspace/proposal.md",
  "validation": {
    "valid": true,
    "missing": []
  },
  "repair_attempts": [],
  "session_id": "019e87bc-3d41-7773-be5e-48b61f6875c5",
  "model": null,
  "usage": null,
  "artifacts": {},
  "error": null
}
```

The serializer may include additional documented harness-neutral fields later, but it must not require callers to parse raw Claude or Codex event payloads.

## CLI Integration Contract

The CLI should parse arguments, read topic text, parse CLI override strings into typed dotted-key values, load current-directory `.env`, then call `run_research_ideation(topic, config_path, overrides=overrides)`.

The CLI maps API outcomes as follows:

| API outcome | CLI exit code |
|---|---:|
| `RunResult.status == "succeeded"` | `0` |
| `RunResult.failure_kind == "validation_failed"` | `1` |
| `TopicError` or `ConfigError` | `2` |
| `WorkspaceError`, `RunResult.failure_kind == "adapter_error"`, or `RunResult.failure_kind == "runtime_error"` | `3` |
| `RunResult.failure_kind == "timeout"`, `RunResult.failure_kind == "budget_exceeded"`, or `RunResult.failure_kind == "cancelled"` | `4` |

The CLI must summarize `RunResult`; it must not parse adapter output, inspect raw logs, or run a second validator path.

## Non-Goals

The MVP Python API does not provide streaming progress callbacks, interactive clarification hooks, direct search or paper-fetch methods, semantic proposal scoring, batch execution, workspace browsing, or public adapter configuration objects. Those surfaces can be added later after the single-run contract is implemented and tested.
