# MVP Runtime Contracts

> **Status:** Implemented MVP runtime contract for configuration loading, the Python API, adapter dispatch, validation, repair, and run records.
> **Last updated:** 2026-06-04
> **Architecture:** [`ARCHITECTURE.md`](../design/ARCHITECTURE.md)
> **Python API:** [`PYTHON_API_SPEC.md`](./PYTHON_API_SPEC.md)
> **Validation and repair:** [`VALIDATION_REPAIR_AND_RUN_RECORD.md`](./VALIDATION_REPAIR_AND_RUN_RECORD.md)

This document defines the MVP runtime contracts for the autonomous research-ideation agent. It is intentionally narrower than the full architecture: it specifies the implemented data shapes the shared Python framework and harness adapters agree on.

## Scope

The contracts here are normative for the MVP until replaced by a later requirements or API specification. They do not define prompt wording, skill contents, proposal-quality rubrics, or semantic validators; those belong to the quality layer. They also do not define the internal APIs of Claude Code Agent SDK, Codex SDK, or scholar-gateway-mcp; adapters translate these project contracts into harness-specific options.

Production Claude and Codex adapters must use the official Python SDK surfaces for their harnesses. The MVP implementation must not fall back to shelling out to alternate CLI-backed or TypeScript surfaces.

The MVP uses YAML for config files. Config values are topic-independent. The user topic belongs in `RunRequest`, not in `RunConfig`, so the same config can be reused across topics, tests, and future batch runs.

## Naming Conventions

- `RunConfig` is the resolved execution configuration after reading a YAML file, applying defaults, expanding relative paths, and applying CLI/API overrides.
- `RunRequest` is one invocation: one user topic plus one resolved `RunConfig`.
- `RunResult` is the shared framework result returned by the Python API and summarized by the CLI.
- `ValidationResult`, `RepairAttempt`, and `UsageSummary` are common records nested inside `RunResult` and included in `run_record.json`.
- Harness IDs are exactly `claude` and `codex`.
- Skill IDs are stable lowercase kebab-case strings.
- Subagent IDs are stable lowercase kebab-case strings.
- Section intent IDs are lowercase snake_case strings, independent of the exact Markdown heading text.

## Config Resolution Rules

1. Load the config YAML file into a structured object.
2. Reject unknown top-level fields and unknown nested fields unless a field is explicitly marked as harness-specific passthrough.
3. Apply documented defaults for missing optional fields.
4. Apply CLI or API overrides after the file and defaults.
5. Resolve relative paths against the config file directory, except agent-workspace-internal paths such as `mcp.scholar_gateway.references_dir`, which are resolved relative to the per-run agent workspace.
6. Validate the resolved config before creating a workspace or launching an adapter.
7. Never read secret values from the config file. The config may name required environment variables, but secret values are read from the process environment only when rendering adapter or MCP launch options.

After resolution, `harness`, `workspace.root`, `workspace.agent_dir`, `mcp.profile`, `repair.max_attempts`, and `skills.selected` must all be present. The framework must not silently choose a harness if no resolved config provides one.

## RunConfig

`RunConfig` selects how the run executes. It must not contain the user topic, generated prompt text, or final proposal content.

### Top-Level Shape

This example assumes config files live under `configs/` and use project-root-relative paths (the project root is the directory containing `pyproject.toml`, discovered by walking upward from the config file). The scholar-gateway MCP server is shipped as a folded-in subpackage at `src/research_ideation_agent/scholar_gateway/`, exposed as the `scholar-gateway-mcp` console script (declared in `pyproject.toml`). Agent-workspace-internal paths such as `references` remain relative to the per-run agent workspace.

```yaml
version: 1
harness: codex

model: gpt-5.4
effort: max

workspace:
  root: .runs
  run_id: null
  agent_dir: agent_workspace
  clean_existing: false

assets:
  root: src/research_ideation_agent/assets
  version: init

mcp:
  profile: scholar-gateway
  scholar_gateway:
    command: scholar-gateway-mcp
    cutoff_date: null
    restrict_domains: true
    strict_filtering: true
    mode: rules
    format: markdown
    references_dir: references
    required_env_vars:
      - WTS_API_KEY
    optional_env_vars:
      - WTS_BASE_URL
    startup_timeout_seconds: 60
    tool_timeout_seconds: 600
    approval_mode: approve

permissions:
  approval_policy: auto

budgets:
  timeout_seconds: 3600
  max_turns: null
  max_cost_usd: null

repair:
  strategy: external-loop
  max_attempts: 1

skills:
  selected:
    - literature-grounding
    - gap-selection
    - method-design
    - experiment-design
    - proposal-writing-and-final-check

subagents:
  selected:
    - source-grounding-reviewer
    - gap-method-challenger
    - experiment-plan-reviewer
    - proposal-final-reviewer

logging:
  write_run_record: true
  retain_event_log: true

  adapters:
    claude:
      strict_mcp_config: true
      api_key_env: ANTHROPIC_API_KEY
    base_url_env: ANTHROPIC_BASE_URL
    disallowed_tools: []
      setting_sources: ["project"]
      sdk_options: {}
    codex:
      api_key_env: GPT_API_KEY
      base_url_env: GPT_API_BASE_URL
      sandbox: workspace_write
      approval_mode: auto_review
      network_access_enabled: true
    cleanup_codex_home: true
    reasoning_summary: auto
    supports_reasoning_summaries: true
```

### Field Reference

| Field | Type | Required after resolution | Meaning |
|---|---:|---:|---|
| `version` | integer | Yes | Config schema version. MVP value is `1`. |
| `harness` | enum | Yes | Selected harness for this run: `claude` or `codex`. |
| `model` | string or null | No | Concrete model identifier passed to the selected harness. The shared MVP default is `null`; example configs pin harness-specific model IDs. `null` means use the harness default. |
| `effort` | enum or null | No | Optional reasoning-effort hint. Accepted values are `minimal`, `min`, `low`, `medium`, `high`, `xhigh`, and `max`; the MVP default is `max`. Codex uses GPT/Codex names and normalizes legacy `min` to `minimal` and `max` to `xhigh`. Claude receives this through adapter extra args when set. |
| `workspace.root` | path | Yes | Parent directory where per-run workspaces are created. |
| `workspace.run_id` | string or null | No | Optional caller-supplied run ID. `null` means the framework generates one. |
| `workspace.agent_dir` | string | Yes | Name of the agent-visible working directory inside the per-run directory. Default is `agent_workspace`. |
| `workspace.clean_existing` | boolean | Yes | Whether an existing workspace with the same run ID may be cleared before launch. Default is `false`. |
| `assets.root` | path | Required when `assets.version` is set | Root directory containing versioned asset subdirectories. |
| `assets.version` | string or null | No | Optional asset version directory name under `assets.root`. When set, prompt, skill, and subagent paths are derived from `<assets.root>/<assets.version>/prompts/startup.md`, `prompts/repair.md`, `skills/`, and optional `subagents/`. |
| `assets.prompt_template` | path | Yes after resolution | Startup prompt template asset path. Normally derived from `assets.version`; explicit paths are supported only when `assets.version` is null. |
| `assets.repair_prompt_template` | path | Yes after resolution | Repair prompt template asset path. Normally derived from `assets.version`; explicit paths are supported only when `assets.version` is null. |
| `assets.skills_dir` | path | Yes after resolution | Canonical shared skill source directory. Normally derived from `assets.version`; explicit paths are supported only when `assets.version` is null. |
| `assets.subagents_dir` | path | No | Canonical shared subagent prompt source directory. Normally derived from `assets.version`; the versioned `subagents/` directory is optional unless `subagents.selected` is non-empty. |
| `mcp.profile` | enum | Yes | MCP profile. MVP supported value is `scholar-gateway`. |
| `mcp.scholar_gateway.command` | string | Yes when profile is `scholar-gateway` | Command used to launch the scholar-gateway MCP server. Default `scholar-gateway-mcp` (the console script entry point shipped by this package). |
| `mcp.scholar_gateway.cutoff_date` | date string or null | No | Optional cutoff date passed to scholar-gateway-mcp. |
| `mcp.scholar_gateway.restrict_domains` | boolean | No | Whether to pass the domain restriction flag when the MCP server supports it. |
| `mcp.scholar_gateway.strict_filtering` | boolean | No | Whether ambiguous-date search results should be filtered when the MCP server supports it. |
| `mcp.scholar_gateway.mode` | enum | No | Scholar gateway fetch date extraction mode, currently `rules` or `llm`. |
| `mcp.scholar_gateway.format` | enum | No | Fetch output format, currently `markdown` or `text`. |
| `mcp.scholar_gateway.references_dir` | agent-workspace-relative path | Yes | Directory inside the agent workspace where extracted paper files are written. |
| `mcp.scholar_gateway.required_env_vars` | list[string] | Yes | Environment variable names that must exist before adapter launch. Values are forwarded to the MCP process but never stored in config or run records. |
| `mcp.scholar_gateway.optional_env_vars` | list[string] | Yes | Environment variable names forwarded to the MCP process only when present. Missing optional variables do not fail the run. |
| `mcp.scholar_gateway.startup_timeout_seconds` | integer | Yes | MCP server startup timeout. |
| `mcp.scholar_gateway.tool_timeout_seconds` | integer | Yes | Per-tool timeout. Must be high enough for `extract_paper`; the example configs use `600`. |
| `mcp.scholar_gateway.approval_mode` | enum | Yes | Project-level approval intent for MCP tools. MVP value is `approve`, mapped per harness by the adapter. Codex thread config marks the scholar-gateway MCP server as required so runs fail rather than silently continuing without literature tools. |
| `permissions.approval_policy` | enum | Yes | Project-level approval policy. Accepted values are `default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk`, and `auto`. The Claude adapter maps `auto` to its permission-mode handling; other values pass through unchanged. |
| `budgets.timeout_seconds` | integer or null | No | Framework wall-clock timeout for the overall run budget. On the main thread with POSIX timer support this interrupts adapter calls; otherwise it is checked after an adapter call returns. |
| `budgets.max_turns` | integer or null | No | Optional harness turn limit. The implemented Claude adapter passes this to the SDK; Codex enforcement depends on SDK support and is not separately implemented by the framework. |
| `budgets.max_cost_usd` | number or null | No | Optional post-call cost guard. This uses provider-returned cost when available, or configured token pricing when `pricing.models[model]` is present. The framework fails the run with `budget_exceeded` after usage is available and exceeds this value. |
| `pricing.models` | mapping | No | Optional per-model token price table used to derive `usage.total_cost_usd` when the provider returns token usage but no USD cost. Keys must match the resolved run `model`. Prices are USD per million tokens. Supported per-model fields are `input_per_million_usd`, `cached_input_per_million_usd`, `cache_creation_input_per_million_usd`, `output_per_million_usd`, `reasoning_output_per_million_usd`, and `input_includes_cached_tokens`. |
| `repair.strategy` | enum | Yes | MVP value is `external-loop`. The shared framework controls validation and repair attempts. Claude stop hooks are not the MVP control path. |
| `repair.max_attempts` | integer | Yes | Maximum number of repair prompts after the initial run. `0` disables repair. |
| `skills.selected` | list[string] | Yes | Skill IDs packaged for this run. Empty list means no optional skills. |
| `subagents.selected` | list[string] | Yes | Subagent IDs exposed for this run. Empty list means no subagents. Selected subagent assets are packaged as Claude `.claude/agents/*.md` files or Codex `.codex/agents/*.toml` files depending on the harness. |
| `logging.write_run_record` | boolean | Yes | Whether to write `run_record.json`. |
| `logging.retain_event_log` | boolean | Yes | Whether adapter event streams should be persisted. Claude logs are JSONL with full SDK message payloads (`event_index`, `timestamp`, `phase`, `type`, and the complete serialized message); Codex logs use the same envelope, append SDK turn notifications while the turn is running, and append a collected `TurnResult` summary after completion. |
| `adapters.claude.strict_mcp_config` | boolean | No | Whether the Claude adapter passes the strict MCP config flag through SDK extra args. |
| `adapters.claude.api_key_env` | string | Yes when `harness` is `claude` | Name of the environment variable holding the Anthropic-compatible API key. The adapter reads this name from the resolved env mapping and rewrites the value into the canonical `ANTHROPIC_API_KEY` the Claude Code CLI subprocess expects (so a single `.env` can hold both `ANTHROPIC_API_KEY` and gateway-specific keys like `SILICONFLOW_API_KEY`, and switching configs picks the right one). |
| `adapters.claude.base_url_env` | string | Yes when `harness` is `claude` | Name of the environment variable holding the paired base URL. The adapter rewrites the value into the canonical `ANTHROPIC_BASE_URL`. |
| `adapters.claude.disallowed_tools` | list[string] | No | Claude built-in tools to block. The default is `[]`; `configs/init/claude.yaml` disallows `AskUserQuestion`, `PushNotification`, `WebFetch`, and `WebSearch` so runs stay autonomous and use the MCP retrieval tools rather than built-in web tools. |
| `adapters.claude.setting_sources` | list[string] | No | Which Claude SDK setting layers the adapter forwards. Accepted values are `user`, `project`, and `local`; default is `["project"]`. |
| `adapters.claude.sdk_options` | mapping | No | Extra Claude SDK CLI-style option keys passed through as `extra_args` when values are not null. |
| `adapters.codex.api_key_env` | string | Yes when `harness` is `codex` | Name of the environment variable holding the OpenAI-compatible provider API key. The adapter passes this name through Codex's custom provider `env_key` and forwards only that selected key value into the isolated Codex app-server environment. Default `GPT_API_KEY`. |
| `adapters.codex.base_url_env` | string | Yes when `harness` is `codex` | Name of the environment variable holding the OpenAI-compatible provider base URL. The adapter reads this value and writes it into the generated `model_providers.gpt-env.base_url` override; it is not forwarded as a generic base-url environment variable. Default `GPT_API_BASE_URL`. |
| `adapters.codex.sandbox` | string | No | Codex sandbox setting. The example configs use `workspace_write`. |
| `adapters.codex.approval_mode` | string | No | Codex approval mode setting. The example configs use `auto_review`. |
| `adapters.codex.network_access_enabled` | boolean | No | Whether Codex thread startup should enable network access when the SDK supports the argument. |
| `adapters.codex.cleanup_codex_home` | boolean | No | Whether to remove the isolated per-run `framework/codex-home` after the adapter closes. When enabled and `logging.retain_event_log` is true, native Codex session JSONL files are archived under `framework/logs/codex_sessions/` before cleanup. |
| `adapters.codex.reasoning_summary` | enum or null | No | Codex `model_reasoning_summary` setting written into the isolated per-run `CODEX_HOME/config.toml`. Accepted values are `auto`, `concise`, `detailed`, and `none`; default is `auto` so native traces can include reasoning summaries when the model/provider supports them. |
| `adapters.codex.supports_reasoning_summaries` | boolean or null | No | Codex `model_supports_reasoning_summaries` setting written into the isolated per-run `CODEX_HOME/config.toml`. Default `true` forces Codex to request reasoning metadata for the configured Responses API provider. |
| `adapters.codex.disabled_features` | list[string] | No | Codex app-server feature flags to force off with `features.<name>=false` overrides before launch. Codex does not expose Claude Code's arbitrary `disallowed_tools` API, so this is the supported project-level mechanism for disabling native browser/web, image/computer, app/plugin, user-elicitation, and similar tool surfaces while leaving shell/file/edit and configured MCP tools available. When `subagents.selected` is empty, the adapter also disables Codex's generic multi-agent feature flags for that run so no unselected subagent surface is exposed. |

In addition to the MCP environment variable names configured under `mcp.scholar_gateway`, the runtime requires harness API environment variables before adapter launch. For Claude runs the env-var names are taken from `adapters.claude.api_key_env` and `adapters.claude.base_url_env` (typically `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`, but a config may point at a different pair such as `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` to route the same harness through an Anthropic-compatible gateway); the adapter rewrites whichever pair is configured into the canonical `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` it hands to the Claude Code CLI subprocess. For Codex runs the env-var names are taken from `adapters.codex.api_key_env` and `adapters.codex.base_url_env` (defaulting to `GPT_API_KEY` / `GPT_API_BASE_URL`, but a config may point at another provider-specific pair). Codex uses these variables as the credentials for a configured third-party OpenAI-compatible provider, not the user's locally logged-in Codex account; the adapter must isolate `CODEX_HOME`, force the configured provider, set `project_root_markers` so the generated `agent_workspace/AGENTS.md` is the Codex project root marker, mark that generated agent workspace as trusted so project-scoped `.codex/agents/*.toml` files are loaded, use the `openai-codex` SDK's pinned Codex runtime by default, disable Codex's built-in `web_search` and configured native feature flags so retrieval goes through scholar-gateway and unselected tool families are hidden, clear unselected personal provider environment variables such as `OPENAI_API_KEY`, and avoid falling back to a `codex` binary found on `PATH`. `CODEX_BIN` may be set only to intentionally override the pinned runtime with a specific local executable. These values come from the effective process environment or explicit API `env` mapping and are never stored in config files or run records.

Relative paths in the resolved config — including `workspace.root`, `assets.root`, `assets.prompt_template`, `assets.repair_prompt_template`, `assets.skills_dir`, `assets.subagents_dir` — are anchored at the project root, defined as the directory containing `pyproject.toml`. Discovery walks upward from the config file's parent directory; an explicit `project_root` argument to `load_config` or the `RIA_PROJECT_ROOT` environment variable overrides discovery. If neither resolves to an existing directory, `load_config` raises `ConfigError`. Absolute paths in YAML are used unchanged.

## RunRequest

`RunRequest` is the input to the shared Python framework. It is constructed by the CLI, tests, automation, or future UI layers.

```python
RunRequest(
    topic: str,
    config: RunConfig,
    config_path: Path | None = None,
    overrides: dict[str, object] | None = None,
)
```

### RunRequest Rules

- `topic` is required and must be a non-empty string after trimming only leading and trailing whitespace.
- The framework records the topic verbatim in framework-owned run metadata before launching the agent.
- The framework must not rewrite, summarize, classify, or ask the user to refine the topic.
- `config` must already be resolved and validated before adapter dispatch.
- `config_path` is optional metadata used for run records and relative-path diagnostics.
- `overrides` is optional metadata showing the caller-supplied overrides after parsing.

## Run Workspace Contract

The workspace manager creates one isolated per-run directory under `workspace.root`. If `workspace.run_id` is null, the framework generates a stable unique ID such as a timestamp plus short random suffix.

The per-run directory has two ownership zones:

- `<workspace.agent_dir>/` is the working directory passed to the selected harness. The default directory name is `agent_workspace`. The lead agent may read and write here, subject to harness sandbox controls.
- `framework/` is framework-owned metadata and configuration. The lead agent should not receive this directory as its working directory, and adapters should not rely on the agent reading files from it.

```text
<workspace.root>/<run_id>/
  <workspace.agent_dir>/              # default: agent_workspace/
    proposal.md
    references/
      <paper extraction outputs from scholar-gateway-mcp>
    .claude/
      <Claude-specific packaged skills or settings, if needed>
    .agents/
      <Codex-specific packaged skills, if needed>
    .codex/
      agents/
        <Codex-specific custom agent TOML files, if selected>
    CLAUDE.md or AGENTS.md
      <harness-specific always-on guidance, if needed>
  framework/
    input/
      topic.txt
      resolved_config.yaml
      startup_prompt.md
      repair_prompt.md
    mcp/
      <harness-specific MCP or settings files>
    logs/
      adapter_events.jsonl
        <full serialized adapter SDK events when logging.retain_event_log is true>
      codex_sessions/
        <archived Codex native session JSONL files when adapters.codex.cleanup_codex_home and logging.retain_event_log are true>
    validation.json
  run_record.json
```

The adapter working directory must be `<workspace.root>/<run_id>/<workspace.agent_dir>`. The lead agent must be instructed to write the final proposal at `proposal.md` relative to that working directory. The scholar-gateway `references_dir` is resolved under this same agent workspace so extracted paper sections are readable by the agent.

## RunResult

`RunResult` is returned by the Python API and included in `run_record.json`. The full run-record shape is defined in [`VALIDATION_REPAIR_AND_RUN_RECORD.md`](./VALIDATION_REPAIR_AND_RUN_RECORD.md). The CLI should summarize `RunResult` rather than parsing adapter-specific output.

```python
RunResult(
    run_id: str,
    status: Literal["succeeded", "failed"],
    failure_kind: Literal[
        "validation_failed",
        "adapter_error",
        "timeout",
        "budget_exceeded",
        "cancelled",
        "runtime_error",
    ] | None,
    harness: Literal["claude", "codex"],
    workspace_path: Path,
    agent_workspace_path: Path,
    proposal_path: Path,
    validation: ValidationResult,
    repair_attempts: list[RepairAttempt],
    session_id: str | None,
    model: str | None,
    usage: UsageSummary | None,
    artifacts: dict[str, Path],
    error: str | None,
)
```

### RunResult Rules

- `status` is `succeeded` only when final validation passes.
- `status` is `failed` when the initial run and all configured repair attempts fail validation, the adapter errors, a timeout or budget bound is reached, cancellation occurs, or another runtime error prevents a valid proposal after a run workspace exists.
- `failure_kind` is `null` on success and a machine-readable failure category on failure.
- `session_id` stores the Claude session ID, Codex thread ID, or `null` if unavailable.
- `workspace_path` is the per-run directory under `workspace.root`; `agent_workspace_path` is the harness working directory inside it.
- `artifacts` must include at least `proposal`, `validation`, and `run_record` when those paths exist.
- `error` is `null` on success and a concise user-facing failure reason on failure.
- Adapter event logs are stored under `logs/` and referenced in `artifacts`, but `RunResult` must remain harness-neutral. Claude event logs contain full serialized SDK message payloads; Codex event logs contain serialized SDK turn notifications as they arrive plus a collected `TurnResult` summary for each completed turn. Codex native session JSONL files may also be archived under `framework/logs/codex_sessions/` when per-run `CODEX_HOME` cleanup is enabled. Treat all event logs as sensitive debugging artifacts.
- Pre-run topic, config, override, missing environment variable, and workspace-creation errors may raise typed Python API exceptions before a `RunResult` exists; see [`PYTHON_API_SPEC.md`](./PYTHON_API_SPEC.md).

## ValidationResult

`ValidationResult` records the mechanical final validator output. It does not represent semantic proposal quality.

```python
ValidationResult(
    valid: bool,
    proposal_path: Path,
    missing: list[str],
    present: list[str],
    errors: list[str],
    validator_version: str,
)
```

`missing` and `present` use only MVP-checked section intent IDs: `title`, `introduction`, `related_work`, `methods`, `experiments_design`, and `references`. The full proposal should still include `Impact Statement` and `Success Criteria`, but those two sections are not mechanical validation gates in the MVP. The repair controller maps missing checked IDs to canonical Markdown headings when constructing repair instructions. The detailed tolerant matching rules are defined in [`VALIDATION_REPAIR_AND_RUN_RECORD.md`](./VALIDATION_REPAIR_AND_RUN_RECORD.md).

## RepairAttempt

`RepairAttempt` records one framework-controlled repair prompt after a failed validation.

```python
RepairAttempt(
    index: int,
    instruction: str,
    before_validation: ValidationResult,
    after_validation: ValidationResult | None,
    continued_session: bool | None,
    previous_session_id: str | None,
    session_id: str | None,
    continuation_error: str | None,
    usage: UsageSummary | None,
    error: str | None,
)
```

Attempt indexes are one-based. `repair.max_attempts` counts only repair prompts, not the initial lead-agent run. If `repair.max_attempts` is `1`, the framework may run the initial attempt plus one focused repair prompt.

`continued_session` is `true` when the adapter repaired in the same Claude session or Codex thread, `false` when it had to start a fresh repair run, and `null` only when no adapter repair call was attempted. `previous_session_id` records the session/thread before repair when available, `session_id` records the session/thread after repair when available, and `continuation_error` records why same-session continuation failed when the adapter had to fall back to a fresh run.

## UsageSummary

Harnesses expose different usage fields. The shared framework should normalize known values. Full raw adapter payloads belong only in adapter event logs when `logging.retain_event_log` is enabled, not in `RunResult`, `run_record.json`, or CLI JSON summaries.

```python
UsageSummary(
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_output_tokens: int | None = None,
    total_tokens: int | None = None,
    total_cost_usd: float | None = None,
    turns: int | None = None,
    duration_ms: int | None = None,
)
```

`cached_input_tokens` tracks prompt-cache/cache-read input when exposed; `cache_creation_input_tokens` tracks prompt-cache writes when exposed. `reasoning_output_tokens` tracks reasoning tokens when the harness exposes them, but reasoning text/summary may still be unavailable in the raw trace.

Unknown or unavailable usage values must be `None`, not guessed. Codex and Claude adapters may fill different subsets. `total_cost_usd` is provider-returned cost when available; otherwise it may be derived from configured `pricing.models[model]`.

## Config File Example

This is a complete MVP config example for a Codex run. A Claude config uses the same shared fields and changes `harness` plus any adapter-specific values.

The repository-local host filesystem paths in this example assume the file lives under `configs/` and resolve against the project root (the directory containing `pyproject.toml`).

```yaml
version: 1
harness: codex

model: gpt-5.4
effort: max

workspace:
  root: .runs
  run_id: null
  agent_dir: agent_workspace
  clean_existing: false

assets:
  root: src/research_ideation_agent/assets
  version: init

mcp:
  profile: scholar-gateway
  scholar_gateway:
    command: scholar-gateway-mcp
    cutoff_date: null
    restrict_domains: true
    strict_filtering: true
    mode: rules
    format: markdown
    references_dir: references
    required_env_vars:
      - WTS_API_KEY
    optional_env_vars:
      - WTS_BASE_URL
    startup_timeout_seconds: 60
    tool_timeout_seconds: 600
    approval_mode: approve

permissions:
  approval_policy: auto

budgets:
  timeout_seconds: 3600
  max_turns: null
  max_cost_usd: null

repair:
  strategy: external-loop
  max_attempts: 1

skills:
  selected:
    - literature-grounding
    - gap-selection
    - method-design
    - experiment-design
    - proposal-writing-and-final-check

subagents:
  selected: []

logging:
  write_run_record: true
  retain_event_log: true

  adapters:
    claude:
      strict_mcp_config: true
      api_key_env: ANTHROPIC_API_KEY
    base_url_env: ANTHROPIC_BASE_URL
    disallowed_tools: []
      setting_sources: ["project"]
      sdk_options: {}
    codex:
      api_key_env: GPT_API_KEY
      base_url_env: GPT_API_BASE_URL
      sandbox: workspace_write
      approval_mode: auto_review
      network_access_enabled: true
    cleanup_codex_home: true
    reasoning_summary: auto
    supports_reasoning_summaries: true
```

## CLI Override Semantics

The CLI supports simple dotted-key overrides that apply after config loading and defaults. The concrete CLI flag syntax is defined in [`CLI_SPEC.md`](./CLI_SPEC.md), and the runtime semantics are fixed here.

- A scalar override replaces the resolved scalar value at that dotted path.
- A list override replaces the whole list; lists are not merged.
- A repeated override for the same key uses the last value.
- Overrides must be parsed as YAML scalars or YAML arrays where possible.
- Overrides may not provide secret values. Config and overrides may name required or optional environment variables, but they must not provide a map from environment variable names to secret values.
- Invalid keys fail before workspace creation.

Examples of allowed override paths include `harness`, `model`, `effort`, `workspace.root`, `workspace.run_id`, `workspace.agent_dir`, `budgets.timeout_seconds`, `repair.max_attempts`, `skills.selected`, and `subagents.selected`.

## Defaults

The MVP implementation should use these defaults when fields are omitted from a config file, except for required fields marked as required after resolution.

| Field | Default |
|---|---|
| `version` | `1` |
| `model` | `null` |
| `effort` | `max` |
| `workspace.root` | `.runs` |
| `workspace.run_id` | `null` |
| `workspace.agent_dir` | `agent_workspace` |
| `workspace.clean_existing` | `false` |
| `mcp.profile` | `scholar-gateway` |
| `mcp.scholar_gateway.command` | `scholar-gateway-mcp` |
| `mcp.scholar_gateway.cutoff_date` | `null` |
| `mcp.scholar_gateway.references_dir` | `references` |
| `mcp.scholar_gateway.required_env_vars` | `[WTS_API_KEY]` |
| `mcp.scholar_gateway.optional_env_vars` | `[WTS_BASE_URL]` |
| `mcp.scholar_gateway.startup_timeout_seconds` | `60` |
| `mcp.scholar_gateway.tool_timeout_seconds` | `600` |
| `mcp.scholar_gateway.approval_mode` | `approve` |
| `permissions.approval_policy` | `auto` |
| `budgets.timeout_seconds` | `3600` |
| `budgets.max_turns` | `null` |
| `budgets.max_cost_usd` | `null` |
| `pricing.models` | `{}` |
| `repair.strategy` | `external-loop` |
| `repair.max_attempts` | `1` |
| `skills.selected` | all five MVP core skills |
| `subagents.selected` | `[]` |
| `logging.write_run_record` | `true` |
| `logging.retain_event_log` | `true` |
| `adapters.claude.strict_mcp_config` | `true` |
| `adapters.claude.api_key_env` | `null` (must be set in Claude configs) |
| `adapters.claude.base_url_env` | `null` (must be set in Claude configs) |
| `adapters.claude.disallowed_tools` | `[]` |
| `adapters.claude.setting_sources` | `["project"]` |
| `adapters.claude.sdk_options` | `{}` |
| `adapters.codex.api_key_env` | `GPT_API_KEY` |
| `adapters.codex.base_url_env` | `GPT_API_BASE_URL` |
| `adapters.codex.sandbox` | `workspace_write` |
| `adapters.codex.approval_mode` | `auto_review` |
| `adapters.codex.network_access_enabled` | `true` |
| `adapters.codex.cleanup_codex_home` | `true` |
| `adapters.codex.reasoning_summary` | `auto` |
| `adapters.codex.supports_reasoning_summaries` | `true` |
| `adapters.codex.disabled_features` | Native browser/web, image/computer, app/plugin, tool-suggestion, and user-elicitation features are disabled by default; generic multi-agent features are also disabled automatically when no subagents are selected. |

`harness` has no implicit default and must be set in a config file or override. For Claude runs, `adapters.claude.api_key_env` and `adapters.claude.base_url_env` are also required and validated at load time.

## Non-Goals For This Contract

This document does not require deterministic verification that the lead agent used every selected skill, called every MCP tool, read enough papers, or produced a semantically strong proposal. Those are quality-layer concerns unless a later architecture decision promotes specific checks into validators.
