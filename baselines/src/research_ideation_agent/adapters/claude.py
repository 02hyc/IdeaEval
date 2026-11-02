"""Claude Agent SDK adapter."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from research_ideation_agent.adapters.base import AdapterContext, AdapterOutcome, AgentAdapter
from research_ideation_agent.adapters.usage import event_to_jsonable, usage_from_claude_result
from research_ideation_agent.errors import ConfigError, RunTimeout


class ClaudeAdapter(AgentAdapter):
    sdk_name = "claude-agent-sdk"
    sdk_distribution_name = "claude-agent-sdk"

    def __init__(self) -> None:
        self._event_index = 0
        self._last_result_message: Any = None
        self._last_assistant_error: str | None = None
        self._last_rate_limit_event: str | None = None

    def close(self) -> None:
        # Each `query()` invocation is a one-shot async iterator; the SDK's
        # subprocess transport tears its CLI process down when the iterator is
        # exhausted (or its task is cancelled by `asyncio.wait_for`). We do not
        # hold any client across runs, so close() only needs to drop the
        # transient stream-state references collected during the last attempt.
        self._last_result_message = None
        self._last_assistant_error = None
        self._last_rate_limit_event = None

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        # The startup contract is delivered via system_prompt.append (see _run); the
        # first user message just needs to hand off control. Sending the contract again
        # here would double-bill the input prefix on every run.
        return asyncio.run(self._run(context, "Begin.", resume=None, continuation=False, phase="initial"))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        if previous_session_id:
            first = asyncio.run(self._run(context, instruction, resume=previous_session_id, continuation=True, phase="repair-resume"))
            if first.succeeded:
                return first
            fallback = asyncio.run(self._run(context, instruction, resume=None, continuation=False, phase="repair-fresh"))
            if fallback.succeeded:
                return AdapterOutcome(
                    session_id=fallback.session_id,
                    model=fallback.model,
                    usage=fallback.usage,
                    continued_session=False,
                    continuation_error=first.error,
                )
            return first
        return asyncio.run(self._run(context, instruction, resume=None, continuation=False, phase="repair-fresh"))

    async def _run(
        self,
        context: AdapterContext,
        prompt: str,
        *,
        resume: str | None,
        continuation: bool,
        phase: str,
    ) -> AdapterOutcome:
        try:
            from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher, ResultMessage, query
        except ImportError as exc:
            return AdapterOutcome(
                session_id=None,
                model=context.config.model,
                usage=None,
                error=f"Claude Agent SDK import failed: {exc.__class__.__name__}: {exc}",
                continued_session=continuation if resume else None,
            )

        try:
            env = _claude_env(context.env, context.config.adapters.claude)
        except RuntimeError as exc:
            return AdapterOutcome(
                session_id=resume,
                model=context.config.model,
                usage=None,
                error=str(exc),
                continued_session=continuation if resume else None,
            )
        options_kwargs: dict[str, Any] = {
            "cwd": context.workspace.agent_workspace_path,
            "mcp_servers": context.mcp.claude_mcp_servers,
            "disallowed_tools": context.config.adapters.claude.disallowed_tools,
            "setting_sources": list(context.config.adapters.claude.setting_sources),
            "model": context.config.model,
            "max_turns": context.config.budgets.max_turns,
            "env": env,
            "resume": resume,
            # The startup prompt is the agent's full contract. Append it to the claude_code
            # preset (preset first, contract appended after) so the agent keeps Claude Code's
            # built-in tool-usage/agentic guidance; the whole system prompt survives context
            # compaction verbatim. The first user message is a short hand-off ("Begin."), so
            # the contract is delivered exactly once per run.
            "system_prompt": {"type": "preset", "preset": "claude_code", "append": context.startup_prompt},
        }
        # `skills` is the SDK's documented entry point for enabling Skill loading: setting
        # it auto-allows the `Skill` tool and extends `setting_sources` to include the
        # project source so packaged `.claude/skills/<id>/SKILL.md` files are discoverable.
        selected_skills = list(context.config.skills.selected)
        if selected_skills:
            options_kwargs["skills"] = selected_skills
        # Reviewer subagents are also packaged as `.claude/agents/*.md` (see
        # prompting.py) so they remain inspectable. Register them programmatically via
        # `ClaudeAgentOptions.agents` as well — the SDK sends these via the initialize
        # control request and binds them to the session deterministically, which does
        # not depend on CLI version or `setting_sources` defaults.
        try:
            programmatic_agents = _load_programmatic_agents(
                context.workspace.agent_workspace_path,
                context.config.subagents.selected,
                AgentDefinition,
            )
        except ConfigError as exc:
            return AdapterOutcome(
                session_id=resume,
                model=context.config.model,
                usage=None,
                error=str(exc),
                continued_session=continuation if resume else None,
            )
        if programmatic_agents is None:
            programmatic_agents = {}
        # Always seed a `general-purpose` definition so that the lead agent's
        # built-in `Agent`/`Task` dispatch (which routes unnamed subagent calls
        # to whichever entry is named `general-purpose`) inherits the lead's
        # model. The CLI's default subagent model is a Claude alias like
        # "haiku" or "sonnet" — fine when talking to Anthropic directly, but it
        # 400s under gateways that only expose one specific model id (e.g.
        # SiliconFlow exposing `Pro/zai-org/GLM-5.1`). User-supplied subagents
        # win on name collision.
        programmatic_agents.setdefault(
            "general-purpose",
            AgentDefinition(
                description=(
                    "General-purpose subagent dispatched by the Agent / Task tools "
                    "for autonomous research, retrieval, and analysis."
                ),
                prompt=(
                    "You are a general-purpose subagent of the lead research-ideation "
                    "agent. Carry out the briefed task end-to-end and report back. "
                    "You inherit the lead's model and tool environment unless the "
                    "task brief says otherwise."
                ),
                model="inherit",
            ),
        )
        if programmatic_agents:
            options_kwargs["agents"] = programmatic_agents
        if context.config.permissions.approval_policy:
            options_kwargs["permission_mode"] = context.config.permissions.approval_policy
        if context.config.effort:
            # SDK EffortLevel = Literal["low", "medium", "high", "xhigh", "max"].
            # The project's Effort literal is wider to also cover Codex (minimal/min);
            # reject those here so the error surfaces at the framework boundary rather
            # than as a less-actionable SDK-layer Literal mismatch.
            if context.config.effort in {"minimal", "min"}:
                return AdapterOutcome(
                    session_id=resume,
                    model=context.config.model,
                    usage=None,
                    error=(
                        f"Claude adapter does not accept effort={context.config.effort!r}; "
                        "use one of: low, medium, high, xhigh, max."
                    ),
                    continued_session=continuation if resume else None,
                )
            options_kwargs["effort"] = context.config.effort
        options_kwargs["strict_mcp_config"] = context.config.adapters.claude.strict_mcp_config
        extra_args: dict[str, str | None] = {}
        # SDK extra_args takes dict[str, str | None]; a None value is emitted as a
        # bare CLI flag (e.g. {"fork-session": None} -> --fork-session). Preserve
        # explicit None passed through YAML so it can express bool-style flags;
        # stringify everything else.
        for key, value in context.config.adapters.claude.sdk_options.items():
            extra_args[str(key)] = None if value is None else str(value)
        if extra_args:
            options_kwargs["extra_args"] = extra_args

        declared_hooks = _declared_stop_hooks(context, HookMatcher)
        if declared_hooks is not None:
            options_kwargs["hooks"] = declared_hooks
            options_kwargs["include_hook_events"] = True

        options = ClaudeAgentOptions(**options_kwargs)
        result_message = None
        try:
            await self._stream_query(query, prompt, options, context, phase, ResultMessage)
        except RunTimeout:
            # Adapter-level cooperative timeout: re-raise so the runtime classifies
            # this as a timeout. The async generator has already been closed, which
            # signals the SDK transport to terminate the CLI subprocess.
            raise
        except Exception as exc:  # SDK exceptions are operational adapter failures.
            last_result = self._last_result_message
            self._last_result_message = None
            result_text = getattr(last_result, "result", None) if last_result is not None else None
            suffix = f" — {result_text}" if result_text else ""
            return AdapterOutcome(
                session_id=resume,
                model=context.config.model,
                usage=None,
                error=f"Claude adapter failed: {exc.__class__.__name__}: {exc}{suffix}",
                continued_session=continuation if resume else None,
            )
        else:
            result_message = self._last_result_message
            self._last_result_message = None
        if result_message is None:
            details = []
            if self._last_assistant_error:
                details.append(f"assistant_error={self._last_assistant_error}")
            if self._last_rate_limit_event:
                details.append(f"rate_limit={self._last_rate_limit_event}")
            self._last_assistant_error = None
            self._last_rate_limit_event = None
            base = "Claude adapter completed without a ResultMessage."
            error_message = base if not details else f"{base} ({'; '.join(details)})"
            return AdapterOutcome(
                session_id=resume,
                model=context.config.model,
                usage=None,
                error=error_message,
                continued_session=continuation if resume else None,
            )
        error = None
        if getattr(result_message, "is_error", False):
            error = "Claude run ended with an SDK error result."
            result_text = getattr(result_message, "result", None)
            if result_text:
                error = f"{error} — {result_text}"
        return AdapterOutcome(
            session_id=getattr(result_message, "session_id", None),
            model=context.config.model,
            usage=usage_from_claude_result(result_message),
            error=error,
            continued_session=continuation if resume else None,
        )

    async def _stream_query(
        self,
        query: Any,
        prompt: str,
        options: Any,
        context: AdapterContext,
        phase: str,
        result_message_cls: Any,
    ) -> None:
        """Drive the SDK's `query()` async iterator, honoring `context.deadline`.

        When a deadline is set, wraps the message stream in `asyncio.wait_for` so
        cancellation runs through the event loop's normal cancellation path: the
        async generator is closed cleanly, which signals the SDK transport to
        terminate the underlying CLI subprocess. SIGALRM in the runtime remains
        as a backstop for adapters that cannot cooperate.
        """

        async def _drive() -> None:
            self._last_result_message = None
            self._last_assistant_error = None
            self._last_rate_limit_event = None
            async for message in query(prompt=prompt, options=options):
                self._log_event(context, message, phase=phase)
                if isinstance(message, result_message_cls):
                    self._last_result_message = message
                else:
                    error = getattr(message, "error", None)
                    if error is not None and type(message).__name__ == "AssistantMessage":
                        self._last_assistant_error = str(error)
                    if type(message).__name__ == "RateLimitEvent":
                        info = getattr(message, "rate_limit_info", None)
                        self._last_rate_limit_event = str(info) if info is not None else "rate-limit event"

        if context.deadline is None:
            await _drive()
            return
        remaining = max(0.0, context.deadline - time.monotonic())
        if remaining <= 0:
            raise RunTimeout("Run exceeded configured timeout.")
        try:
            await asyncio.wait_for(_drive(), timeout=remaining)
        except asyncio.TimeoutError as exc:  # noqa: UP041
            raise RunTimeout("Run exceeded configured timeout.") from exc

    def _log_event(self, context: AdapterContext, message: Any, *, phase: str) -> None:
        if context.event_log_path is None:
            return
        # `thinking_tokens` is a high-frequency CLI counter emission (one per
        # extended-thinking chunk) — useful only as live progress, not as audit
        # signal. Drop it here so the persisted event log stays diff-friendly.
        if getattr(message, "subtype", None) == "thinking_tokens":
            return
        self._event_index += 1
        payload = {
            "event_index": self._event_index,
            "timestamp": datetime.now(UTC).isoformat(),
            "phase": phase,
            "type": type(message).__name__,
            "message": event_to_jsonable(message),
        }
        with context.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")


def _declared_stop_hooks(context: AdapterContext, hook_matcher_cls: Any) -> dict[str, list[Any]] | None:
    specs = list(context.config.hooks.stop)
    if not specs:
        return None
    matchers: list[Any] = []
    for spec in specs:
        script = Path(spec.script)

        async def stop_hook(input_data: Any, _tool_use_id: str | None, _hook_context: Any, _script: Path = script) -> dict[str, object]:
            try:
                result = _run_stop_artifact_gate(_script, input_data)
            except Exception as exc:  # Hook failures must not trap an otherwise completed run.
                return {
                    "systemMessage": f"Stop hook skipped after {exc.__class__.__name__}: {exc}",
                    "suppressOutput": True,
                }
            return result or {}

        matchers.append(hook_matcher_cls(matcher=None, hooks=[stop_hook], timeout=float(spec.timeout)))
    return {"Stop": matchers}


def _run_stop_artifact_gate(script: Path, input_data: Any) -> dict[str, object] | None:
    spec = importlib.util.spec_from_file_location("scientific_hypothesis_stop_artifact_gate", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Stop hook script: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evaluate_stop = getattr(module, "evaluate_stop", None)
    if not callable(evaluate_stop):
        raise RuntimeError(f"Stop hook script does not define evaluate_stop: {script}")
    result = evaluate_stop(_hook_input_to_payload(input_data))
    return result if isinstance(result, dict) else None


def _hook_input_to_payload(input_data: Any) -> dict[str, object]:
    if isinstance(input_data, dict):
        return dict(input_data)
    payload: dict[str, object] = {}
    for key in ("cwd", "session_id", "transcript_path", "permission_mode", "hook_event_name", "stop_hook_active"):
        value = getattr(input_data, key, None)
        if value is not None:
            payload[key] = value
    return payload


_CLAUDE_PASSTHROUGH_ENV = (
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "ANTHROPIC_BETAS",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
)


def _claude_env(env: dict[str, str], adapter_config: Any) -> dict[str, str]:
    """Build the env dict the Claude SDK passes to its CLI subprocess.

    The actual env-var names that hold the API key and base URL are chosen by
    the user via `adapters.claude.api_key_env` / `adapters.claude.base_url_env`
    in YAML — typically `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` for direct
    Anthropic, but pointing at a different pair (e.g. `SILICONFLOW_API_KEY` /
    `SILICONFLOW_BASE_URL`) lets the same harness route to a third-party
    Anthropic-compatible gateway. Whatever name is configured, its *value* is
    read from the supplied env mapping and rewritten under the canonical
    `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` names that the Claude Code CLI
    expects.
    """

    api_key_var = adapter_config.api_key_env
    base_url_var = adapter_config.base_url_env
    cli_env = {
        "ANTHROPIC_API_KEY": _required_env(env, api_key_var).strip(),
        "ANTHROPIC_BASE_URL": _validated_base_url(_required_env(env, base_url_var), base_url_var),
    }
    # Forward Claude Code's own beta / Bedrock tuning variables when the caller set
    # them, so a Bedrock-backed or restricted gateway that rejects an experimental
    # `anthropic-beta` flag can be remediated (e.g. DISABLE_EXPERIMENTAL_BETAS=1 or
    # ANTHROPIC_BETAS=...) without leaking other environment variables to the CLI.
    for name in _CLAUDE_PASSTHROUGH_ENV:
        value = env.get(name)
        if value:
            cli_env[name] = value
    return cli_env


def _load_programmatic_agents(
    agent_workspace: Path,
    selected: list[str],
    agent_definition_cls: Any,
) -> dict[str, Any]:
    """Read packaged `.claude/agents/<id>.md` files and build SDK AgentDefinitions.

    `prompting.py` writes each selected reviewer subagent to disk; we re-read them
    here so the SDK can register them via the initialize control request. Returns
    an empty dict when no subagents are selected.
    """

    if not selected:
        return {}
    agents_dir = agent_workspace / ".claude" / "agents"
    definitions: dict[str, Any] = {}
    for subagent_id in selected:
        path = agents_dir / f"{subagent_id}.md"
        if not path.is_file():
            raise ConfigError(f"Packaged subagent file is missing: {path}")
        frontmatter, body = _split_frontmatter(path)
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ConfigError(f"Subagent frontmatter must include non-empty `description`: {path}")
        if not body.strip():
            raise ConfigError(f"Subagent file must include a prompt body: {path}")
        kwargs: dict[str, Any] = {
            "description": description.strip(),
            "prompt": body.strip(),
        }
        tools = frontmatter.get("tools")
        if isinstance(tools, list) and all(isinstance(t, str) for t in tools):
            kwargs["tools"] = list(tools)
        model = frontmatter.get("model")
        if isinstance(model, str) and model.strip():
            kwargs["model"] = model.strip()
        definitions[subagent_id] = agent_definition_cls(**kwargs)
    return definitions


def _split_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ConfigError(f"Subagent file must start with YAML frontmatter (a `---` line): {path}")
    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            closing_index = index
            break
    if closing_index is None:
        raise ConfigError(f"Subagent file must close YAML frontmatter with a `---` line: {path}")
    yaml_text = "".join(lines[1:closing_index])
    try:
        frontmatter = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse subagent frontmatter: {path}") from exc
    if not isinstance(frontmatter, dict):
        raise ConfigError(f"Subagent frontmatter must be a mapping: {path}")
    body = "".join(lines[closing_index + 1 :]).lstrip("\r\n")
    return frontmatter, body


def _required_env(env: dict[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise RuntimeError(f"Claude adapter requires environment variable {name}.")
    return value


def _validated_base_url(value: str, var_name: str) -> str:
    """Normalize and validate the gateway base URL.

    The Claude Code CLI appends ``/v1/messages`` to ``ANTHROPIC_BASE_URL`` and parses
    the result as a URL, so a missing scheme or stray whitespace (a trailing newline
    from a copy-pasted ``.env`` is common) produces an opaque "cannot be parsed as a
    URL" failure mid-run. Strip whitespace and require an absolute http(s) URL here so
    the problem surfaces early with the offending variable named.
    """
    url = value.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(
            f"Environment variable {var_name} must be a full http(s) base URL such as "
            f"https://host[/path] — check for a missing scheme or stray whitespace (the "
            f"Claude Code CLI appends /v1/messages to it)."
        )
    return url
