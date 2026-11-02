# CLI Spec

> **Status:** Implemented MVP command-line contract.
> **Last updated:** 2026-06-03
> **Runtime contracts:** [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md)
> **Python API:** [`PYTHON_API_SPEC.md`](./PYTHON_API_SPEC.md)

This document defines the MVP command-line interface for the autonomous research-ideation agent. The CLI is a thin wrapper around the shared Python API and must not implement a separate execution path.

## Scope

The MVP CLI has one command: `run`. It accepts one research topic, one config file, optional simple config overrides, and an optional JSON output mode. It launches one autonomous run and reports the resulting workspace and proposal artifact.

The MVP CLI does not include batch execution, interactive topic refinement, semantic proposal scoring, config scaffolding, workspace cleanup, or historical run browsing.

## Command

```bash
uv run research-ideation-agent run [OPTIONS]
```

The installed console script also supports:

```bash
research-ideation-agent run [OPTIONS]
```

Examples use `uv run research-ideation-agent` so the command runs inside the project-managed environment.

## Required Inputs

Exactly one topic source is required:

- `--topic TEXT` supplies the topic directly.
- `--topic-file PATH` reads the topic from a UTF-8 text file.

`--config PATH` is required for the MVP. The config file must resolve to a valid `RunConfig` as defined in [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md). The framework must not silently choose a harness or MCP package path without a config or packaged profile.

## Options

| Option | Required | Repeatable | Meaning |
|---|---:|---:|---|
| `--topic TEXT` | Yes, unless `--topic-file` is used | No | High-level AI research topic. Mutually exclusive with `--topic-file`. |
| `--topic-file PATH` | Yes, unless `--topic` is used | No | File containing the high-level AI research topic. Mutually exclusive with `--topic`. |
| `--config PATH` | Yes | No | YAML config file. |
| `--set KEY=VALUE` | No | Yes | Dotted-key override applied after config defaults. |
| `--run-id ID` | No | No | Convenience alias for `--set workspace.run_id=ID`. |
| `--workspace-root PATH` | No | No | Convenience alias for `--set workspace.root=PATH`. |
| `--harness claude\|codex` | No | No | Convenience alias for `--set harness=<value>`. |
| `--assets-version VERSION` | No | No | Convenience alias for `--set assets.version=<value>`. |
| `--cutoff-date YYYY-MM-DD` | No | No | Convenience alias for `--set mcp.scholar_gateway.cutoff_date=YYYY-MM-DD`; injected into scholar-gateway MCP startup for search/fetch/scrape filtering. |
| `--project-root PATH` | No | No | Anchor for relative paths in the YAML config and in `--workspace-root` / `--topic-file`. Overrides `RIA_PROJECT_ROOT` and the default discovery walk. Required when the config file is not inside a Python project tree. |
| `--json` | No | No | Emit machine-readable JSON summary to stdout. |
| `--quiet` | No | No | Suppress progress-style human output. Error messages still go to stderr. |

The convenience flags are aliases for config overrides. If both a convenience flag and `--set` target the same field, the last occurrence on the command line wins.

## Examples

```bash
uv run research-ideation-agent run \
  --topic "Improving visual reasoning in multimodal models" \
  --config configs/scientific-hypothesis-generator/gpt.yaml
```

```bash
uv run research-ideation-agent run \
  --topic-file examples/question.txt \
  --config configs/scientific-hypothesis-generator/gpt.yaml
```

```bash
uv run research-ideation-agent run \
  --topic "Memory systems for long-horizon AI agents" \
  --config configs/init/claude.yaml \
  --harness claude \
  --assets-version scientific-hypothesis-generator \
  --cutoff-date 2025-01-01 \
  --workspace-root .runs \
  --run-id memory-agent-demo \
  --set repair.max_attempts=2 \
  --json
```

## Topic Handling

The CLI trims leading and trailing whitespace from the topic only for validation. The original topic text after file decoding is passed to the Python API and recorded by the framework. Empty topics fail before config loading or workspace creation.

`--topic-file` must read the entire file as UTF-8. The CLI must not interpret Markdown, YAML front matter, or comments in the topic file.

If both `--topic` and `--topic-file` are provided, the CLI fails with exit code `2`. If neither is provided, the CLI fails with exit code `2`.

## Config And Override Semantics

`--config` is loaded first, runtime defaults from [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md) are applied, and then CLI overrides are applied in command-line order.

`--set` uses dotted keys:

```bash
--set repair.max_attempts=2
--set effort=xhigh
--set model=gpt-5.4
--set skills.selected='["literature-grounding", "proposal-writing-and-final-check"]'
```

Values should be parsed as YAML scalars or YAML arrays where possible. A malformed override fails with exit code `2`.

Overrides may not supply secret values. Config may name required environment variables such as `WTS_API_KEY` and optional environment variables such as `WTS_BASE_URL`, but secret values must come from the process environment.

Before calling the Python API, the CLI loads `.env` from the current working directory with `python-dotenv`. This is a CLI convenience only; the public Python API does not load `.env` by itself. The selected harness still requires its own API environment variables: Claude runs read the env-var names configured by `adapters.claude.api_key_env` and `adapters.claude.base_url_env` (commonly `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`, but configurable so non-Claude models can be routed through an Anthropic-compatible gateway under different names such as `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL`); Codex runs read the env-var names configured by `adapters.codex.api_key_env` and `adapters.codex.base_url_env` (defaulting to `GPT_API_KEY` / `GPT_API_BASE_URL`).

Relative paths in YAML configs and in `--workspace-root` / `--topic-file` flag values resolve against the **project root** — the directory containing `pyproject.toml`, discovered by walking upward from the resolved config file's parent directory. Absolute paths are used as-is. The `--config` flag itself remains cwd-relative because it is the bootstrap pointer. To run a config file that does not live inside a Python project tree, pass `--project-root /path` on the CLI or set `RIA_PROJECT_ROOT=/path` in the environment; otherwise the CLI exits with `ConfigError`.

## Output

By default, the CLI writes a concise human-readable summary to stdout after the run finishes.

Successful run:

```text
status: succeeded
run_id: 20260602-153012-a8f3c1
harness: codex
workspace: .runs/20260602-153012-a8f3c1
proposal: .runs/20260602-153012-a8f3c1/agent_workspace/proposal.md
validation: valid
```

Final validation failure:

```text
status: failed
run_id: 20260602-153012-a8f3c1
harness: codex
workspace: .runs/20260602-153012-a8f3c1
proposal: .runs/20260602-153012-a8f3c1/agent_workspace/proposal.md
validation: invalid
missing: references
```

Operational errors should print a concise message to stderr. In human-readable mode, stdout should still include any run metadata that exists after a failed launch or failed run. Usage errors and config validation errors should print only to stderr because no run exists yet.

## JSON Output

With `--json`, stdout must contain one JSON object and no extra human prose. The object is a summary of `RunResult`, not raw adapter output.

```json
{
  "status": "succeeded",
  "failure_kind": null,
  "run_id": "20260602-153012-a8f3c1",
  "harness": "codex",
  "workspace_path": ".runs/20260602-153012-a8f3c1",
  "agent_workspace_path": ".runs/20260602-153012-a8f3c1/agent_workspace",
  "proposal_path": ".runs/20260602-153012-a8f3c1/agent_workspace/proposal.md",
  "validation": {
    "valid": true,
    "missing": []
  },
  "repair_attempts": 0,
  "session_id": "019e87bc-3d41-7773-be5e-48b61f6875c5",
  "usage": {
    "input_tokens": 751673,
    "output_tokens": 8883,
    "total_cost_usd": null
  },
  "error": null
}
```

In JSON mode, warnings and operational errors may still be written to stderr, but stdout must remain valid JSON.

## Exit Codes

| Code | Meaning |
|---:|---|
| `0` | Run succeeded and final validation passed. |
| `1` | Run completed but final validation failed after configured repair attempts. |
| `2` | CLI usage error, config parse error, config validation error, invalid override, missing required environment variable, or invalid topic input. |
| `3` | Adapter or runtime error before a completed final validation result was available. |
| `4` | Timeout, max-turn, budget, or externally cancelled run. |

The CLI should prefer the most specific code. For example, a timeout that leaves an invalid `proposal.md` should return `4`, not `1`, because the run was interrupted by a runtime bound.

The CLI maps failed `RunResult` values by `failure_kind`: `validation_failed` maps to `1`; `adapter_error` and `runtime_error` map to `3`; `timeout`, `budget_exceeded`, and `cancelled` map to `4`. Pre-run `TopicError` and `ConfigError` map to `2`.

## Python API Boundary

The CLI must call the shared Python API defined in [`PYTHON_API_SPEC.md`](./PYTHON_API_SPEC.md). Its normal run path parses topic and override inputs, loads current-directory `.env`, then calls `run_research_ideation(topic, config_path, overrides=overrides)`. It should not directly call Claude Code Agent SDK, Codex SDK, the final validator, or scholar-gateway-mcp. All run semantics after argument parsing belong to the shared framework.

## Non-MVP Commands

These commands are intentionally out of scope for the MVP CLI but may be added later:

- `validate` for validating an existing `proposal.md`.
- `show-config` for printing resolved config.
- `list-runs` for browsing prior workspaces.
- `clean` for deleting run workspaces.
- `batch` for running multiple topics.
