"""Codex Python SDK adapter."""

from __future__ import annotations

import copy
import inspect
import json
import logging
import shlex
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from research_ideation_agent.adapters.base import AdapterContext, AdapterOutcome, AgentAdapter
from research_ideation_agent.adapters.usage import event_to_jsonable, usage_from_codex_result
from research_ideation_agent.constants import CODEX_NO_SUBAGENT_DISABLED_FEATURES


logger = logging.getLogger(__name__)


_PERSONAL_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)

_CODEX_PROVIDER_ID = "gpt-env"
_PROBLEM_EVENT_MAX_CHARS = 1000

_INITIAL_USER_INPUT = (
    "Begin the research-ideation run now. "
    "Follow the system instructions and produce the required workspace artifacts, including proposal.md."
)


class CodexAdapter(AgentAdapter):
    sdk_name = "codex-python"
    sdk_distribution_name = "openai-codex"

    def __init__(self) -> None:
        self._codex: Any = None
        self._thread: Any = None
        self._event_index = 0

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        try:
            sdk = _import_codex_sdk()
            self._ensure_codex(sdk, context)
            self._thread = _start_thread(sdk, self._codex, context)
            result = self._run_turn(sdk, context, _INITIAL_USER_INPUT, "initial")
            return AdapterOutcome(
                session_id=getattr(self._thread, "id", None),
                model=context.config.model,
                usage=usage_from_codex_result(result),
                error=_result_error(result),
            )
        except Exception as exc:
            self._log_adapter_error(context, exc, "initial")
            return AdapterOutcome(
                session_id=getattr(self._thread, "id", None) if self._thread is not None else None,
                model=context.config.model,
                usage=None,
                error=f"Codex adapter failed: {exc.__class__.__name__}: {exc}",
            )

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        if previous_session_id:
            first = self._run_same_thread_repair(context, instruction, previous_session_id)
            if first.succeeded:
                return first
            fallback = self._run_fresh_repair(context, instruction, first.error or "Codex same-thread repair failed.")
            if fallback.succeeded:
                return fallback
            return first
        return self._run_fresh_repair(context, instruction, "No previous Codex thread id was available for repair.")

    def _run_same_thread_repair(self, context: AdapterContext, instruction: str, previous_session_id: str) -> AdapterOutcome:
        try:
            sdk = _import_codex_sdk()
            self._ensure_codex(sdk, context)
            if self._thread is None or getattr(self._thread, "id", None) != previous_session_id:
                self._thread = _resume_thread(sdk, self._codex, context, previous_session_id)
            result = self._run_turn(sdk, context, instruction, "repair-resume")
            return AdapterOutcome(
                session_id=getattr(self._thread, "id", previous_session_id),
                model=context.config.model,
                usage=usage_from_codex_result(result),
                error=_result_error(result),
                continued_session=True,
            )
        except Exception as exc:
            self._log_adapter_error(context, exc, "repair-resume")
            return AdapterOutcome(
                session_id=previous_session_id,
                model=context.config.model,
                usage=None,
                error=f"Codex same-thread repair failed: {exc.__class__.__name__}: {exc}",
                continued_session=True,
            )

    def close(self) -> None:
        if self._codex is not None:
            self._codex.__exit__(None, None, None)
            self._codex = None
            self._thread = None

    def _ensure_codex(self, sdk: dict[str, Any], context: AdapterContext) -> None:
        if self._codex is not None:
            return
        codex = sdk["Codex"](_codex_config(sdk, context))
        codex.__enter__()
        _sync_selected_skills(codex, context)
        self._codex = codex

    def _run_fresh_repair(self, context: AdapterContext, instruction: str, continuation_error: str) -> AdapterOutcome:
        try:
            sdk = _import_codex_sdk()
            self._ensure_codex(sdk, context)
            self._thread = _start_thread(sdk, self._codex, context)
            result = self._run_turn(sdk, context, instruction, "repair-fresh")
            return AdapterOutcome(
                session_id=getattr(self._thread, "id", None),
                model=context.config.model,
                usage=usage_from_codex_result(result),
                error=_result_error(result),
                continued_session=False,
                continuation_error=continuation_error,
            )
        except Exception as exc:
            self._log_adapter_error(context, exc, "repair-fresh")
            return AdapterOutcome(
                session_id=getattr(self._thread, "id", None) if self._thread is not None else None,
                model=context.config.model,
                usage=None,
                error=f"Codex repair fallback failed: {exc.__class__.__name__}: {exc}",
                continued_session=False,
                continuation_error=continuation_error,
            )

    def _run_turn(self, sdk: dict[str, Any], context: AdapterContext, instruction: str, phase: str) -> Any:
        if self._thread is None:
            raise RuntimeError("Codex thread is not initialized.")
        self._log_adapter_event(
            context,
            phase,
            "codex_turn_start",
            {
                "thread_id": getattr(self._thread, "id", None),
                "input_length": len(instruction),
                "model": context.config.model,
                "effort": context.config.effort,
            },
        )
        if hasattr(self._thread, "turn"):
            turn = _call_with_supported_kwargs(self._thread.turn, _turn_kwargs(sdk, context, instruction))
            return self._collect_logged_turn_result(sdk, context, turn, phase)

        result = _call_with_supported_kwargs(self._thread.run, _turn_kwargs(sdk, context, instruction))
        self._log_result(context, result, phase)
        return result

    def _collect_logged_turn_result(self, sdk: dict[str, Any], context: AdapterContext, turn: Any, phase: str) -> Any:
        stream = turn.stream()
        try:
            result = self._collect_turn_result_from_stream(sdk, stream, getattr(turn, "id", None), context, phase)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        self._log_result(context, result, phase)
        return result

    def _collect_turn_result_from_stream(
        self,
        sdk: dict[str, Any],
        stream: Any,
        turn_id: str | None,
        context: AdapterContext,
        phase: str,
    ) -> Any:
        completed = None
        items: list[Any] = []
        usage = None
        last_problem_event = None

        for event in stream:
            self._log_sdk_event(context, event, phase, turn_id)
            last_problem_event = _problem_event_summary(event, last_problem_event)
            payload = getattr(event, "payload", None)
            payload_turn_id = getattr(payload, "turn_id", None)
            if _is_payload(sdk, payload, "ItemCompletedNotification") and _same_turn(payload_turn_id, turn_id):
                items.append(getattr(payload, "item", None))
                continue
            if _is_payload(sdk, payload, "ThreadTokenUsageUpdatedNotification") and _same_turn(payload_turn_id, turn_id):
                usage = getattr(payload, "token_usage", None)
                continue
            if _is_payload(sdk, payload, "TurnCompletedNotification"):
                turn = getattr(payload, "turn", None)
                if _same_turn(getattr(turn, "id", None), turn_id):
                    completed = payload

        if completed is None:
            message = "Codex turn completed event not received."
            if last_problem_event:
                message = f"{message} Last SDK problem event: {last_problem_event}"
            raise RuntimeError(message)

        turn = getattr(completed, "turn", None)
        _raise_for_failed_turn(turn)
        result_type = sdk.get("TurnResult")
        fields = {
            "id": getattr(turn, "id", turn_id),
            "status": getattr(turn, "status", None),
            "error": getattr(turn, "error", None),
            "started_at": getattr(turn, "started_at", None),
            "completed_at": getattr(turn, "completed_at", None),
            "duration_ms": getattr(turn, "duration_ms", None),
            "final_response": _final_assistant_response_from_items(items),
            "items": items,
            "usage": usage,
        }
        if callable(result_type):
            return result_type(**fields)
        return SimpleNamespace(**fields)

    def _log_sdk_event(self, context: AdapterContext, event: Any, phase: str, turn_id: str | None) -> None:
        event_type = getattr(event, "method", None) or type(event).__name__
        self._log_adapter_event(
            context,
            phase,
            str(event_type),
            {
                "turn_id": turn_id,
                "event": event_to_jsonable(event, model_dump_kwargs={"mode": "json", "by_alias": True}),
            },
        )

    def _log_result(self, context: AdapterContext, result: Any, phase: str) -> None:
        self._log_adapter_event(
            context,
            phase,
            type(result).__name__,
            event_to_jsonable(result, model_dump_kwargs={"mode": "json", "by_alias": True}),
        )

    def _log_adapter_error(self, context: AdapterContext, exc: Exception, phase: str) -> None:
        self._log_adapter_event(
            context,
            phase,
            "adapter_error",
            {
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            },
        )

    def _log_adapter_event(self, context: AdapterContext, phase: str, event_type: str, message: Any) -> None:
        if context.event_log_path is None:
            return
        self._event_index += 1
        payload = {
            "event_index": self._event_index,
            "timestamp": datetime.now(UTC).isoformat(),
            "phase": phase,
            "type": event_type,
            "message": message,
        }
        context.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with context.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
            handle.flush()


def _import_codex_sdk() -> dict[str, Any]:
    try:
        from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, TurnResult
        from openai_codex.generated.v2_all import (
            ConfigWarningNotification,
            ErrorNotification,
            ItemCompletedNotification,
            ThreadTokenUsageUpdatedNotification,
            TurnCompletedNotification,
            WarningNotification,
            ReasoningEffort,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Official Codex Python SDK package `openai-codex` is not importable. "
            "Install the published SDK build; set CODEX_BIN only when intentionally overriding the SDK's pinned Codex runtime."
        ) from exc
    return {
        "ApprovalMode": ApprovalMode,
        "Codex": Codex,
        "CodexConfig": CodexConfig,
        "ConfigWarningNotification": ConfigWarningNotification,
        "ErrorNotification": ErrorNotification,
        "ItemCompletedNotification": ItemCompletedNotification,
        "ReasoningEffort": ReasoningEffort,
        "Sandbox": Sandbox,
        "ThreadTokenUsageUpdatedNotification": ThreadTokenUsageUpdatedNotification,
        "TurnCompletedNotification": TurnCompletedNotification,
        "TurnResult": TurnResult,
        "WarningNotification": WarningNotification,
    }


def _codex_config(sdk: dict[str, Any], context: AdapterContext) -> Any:
    codex_home = context.workspace.framework_path / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    _write_codex_home_config(
        codex_home,
        context.workspace.agent_workspace_path,
        reasoning_summary=context.config.adapters.codex.reasoning_summary,
        supports_reasoning_summaries=context.config.adapters.codex.supports_reasoning_summaries,
        stop_hook_scripts=[(Path(spec.script), spec.timeout) for spec in context.config.hooks.stop],
    )
    api_key_env = context.config.adapters.codex.api_key_env
    base_url_env = context.config.adapters.codex.base_url_env
    env = {
        api_key_env: context.env[api_key_env],
        "CODEX_HOME": str(codex_home),
    }
    env.update({name: "" for name in _PERSONAL_PROVIDER_ENV_VARS if name not in env})
    env.update(context.mcp.mcp_env)
    config_overrides = [
        f"model_provider={_toml_string(_CODEX_PROVIDER_ID)}",
        f"model_providers.{_CODEX_PROVIDER_ID}.name={_toml_string('Configured OpenAI-compatible API')}",
        f"model_providers.{_CODEX_PROVIDER_ID}.base_url={_toml_string(context.env[base_url_env])}",
        f"model_providers.{_CODEX_PROVIDER_ID}.env_key={_toml_string(api_key_env)}",
        f"model_providers.{_CODEX_PROVIDER_ID}.wire_api={_toml_string('responses')}",
    ]
    config_overrides.extend(_codex_disabled_feature_overrides(context))
    if context.config.adapters.codex.network_access_enabled:
        config_overrides.append("sandbox_workspace_write.network_access=true")
    kwargs: dict[str, Any] = {
        "cwd": str(context.workspace.agent_workspace_path),
        "env": env,
        "config_overrides": tuple(config_overrides),
        "client_name": "research_ideation_agent",
        "client_title": "Research Ideation Agent",
    }
    codex_bin = context.env.get("CODEX_BIN")
    if codex_bin:
        kwargs["codex_bin"] = codex_bin
    return sdk["CodexConfig"](**kwargs)


def _codex_disabled_feature_overrides(context: AdapterContext) -> list[str]:
    features = list(context.config.adapters.codex.disabled_features)
    if not context.config.subagents.selected:
        features.extend(CODEX_NO_SUBAGENT_DISABLED_FEATURES)
    return [f"features.{feature}=false" for feature in sorted(set(features))]


def _write_codex_home_config(
    codex_home: Path,
    agent_workspace: Path,
    *,
    reasoning_summary: str | None = None,
    supports_reasoning_summaries: bool | None = None,
    stop_hook_scripts: list[tuple[Path, int]] = (),
) -> None:
    """Write non-secret Codex config that cannot be expressed safely as -c dotted keys.

    Codex's `--config key=value` override parser treats dotted paths literally; a
    quoted project path such as `projects."/tmp/a.b".trust_level` becomes a key
    containing quote characters, while an unquoted path breaks on dots in path
    components like `.runs`. A real TOML config file preserves the path key.
    """

    path = codex_home / "config.toml"
    lines = [f"project_root_markers = [{_toml_string('AGENTS.md')}]"]
    if reasoning_summary is not None:
        lines.append(f"model_reasoning_summary = {_toml_string(reasoning_summary)}")
    if supports_reasoning_summaries is not None:
        lines.append(
            f"model_supports_reasoning_summaries = {_toml_bool(supports_reasoning_summaries)}"
        )
    lines.extend(
        [
            "",
            f"[projects.{_toml_string(str(agent_workspace))}]",
            f"trust_level = {_toml_string('trusted')}",
            "",
        ]
    )
    for script, timeout in stop_hook_scripts:
        lines.extend(
            [
                "[[hooks.Stop]]",
                "",
                "[[hooks.Stop.hooks]]",
                'type = "command"',
                f"command = {_toml_string(f'python3 {shlex.quote(str(script))}')}",
                f"timeout = {int(timeout)}",
                'statusMessage = "Checking hypothesis artifacts"',
                "",
            ]
        )
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")


def _sync_selected_skills(codex: Any, context: AdapterContext) -> None:
    """Hide Codex skills that this run did not select.

    The Codex app-server auto-loads system and user skills into a fresh
    `CODEX_HOME`; Claude receives only `ClaudeAgentOptions.skills`. Keep the
    shared run contract consistent by disabling any discovered Codex skill whose
    name is outside `config.skills.selected`.
    """

    request_raw = _codex_request_raw(codex)
    if request_raw is None:
        return
    selected = set(context.config.skills.selected)
    try:
        response = request_raw(
            "skills/list",
            {"cwds": [str(context.workspace.agent_workspace_path)], "forceReload": True},
        )
        for skill in _iter_codex_skill_entries(response):
            name = skill.get("name")
            if not isinstance(name, str) or not name:
                continue
            enabled = skill.get("enabled")
            should_enable = name in selected
            if enabled is should_enable:
                continue
            selector = _skill_selector(skill)
            if selector is None:
                continue
            request_raw(
                "skills/config/write",
                {**selector, "enabled": should_enable},
            )
    except Exception as exc:  # Skill filtering should not prevent a run from starting.
        logger.warning("Could not synchronize Codex skill visibility: %s", exc)


def _codex_request_raw(codex: Any) -> Callable[[str, dict[str, Any] | None], Any] | None:
    client = getattr(codex, "_client", None)
    request_raw = getattr(client, "_request_raw", None)
    return request_raw if callable(request_raw) else None


def _iter_codex_skill_entries(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    entries: list[dict[str, Any]] = []
    for row in response.get("data", []):
        if not isinstance(row, dict):
            continue
        for skill in row.get("skills", []):
            if isinstance(skill, dict):
                entries.append(skill)
    return entries


def _skill_selector(skill: dict[str, Any]) -> dict[str, str] | None:
    path = skill.get("path")
    if isinstance(path, str) and path:
        return {"path": path}
    name = skill.get("name")
    if isinstance(name, str) and name:
        return {"name": name}
    return None


def _start_thread(sdk: dict[str, Any], codex: Any, context: AdapterContext) -> Any:
    kwargs = {
        "cwd": str(context.workspace.agent_workspace_path),
        "model": context.config.model,
        "model_provider": _CODEX_PROVIDER_ID,
        "sandbox": _sandbox(sdk, context),
        "approval_mode": _approval_mode(sdk, context),
        "developer_instructions": context.startup_prompt,
        "config": _codex_thread_config(context),
    }
    return _call_with_supported_kwargs(
        codex.thread_start,
        kwargs,
        required=("developer_instructions", "model_provider", "sandbox", "approval_mode", "config"),
    )


def _resume_thread(sdk: dict[str, Any], codex: Any, context: AdapterContext, thread_id: str) -> Any:
    kwargs = {
        "thread_id": thread_id,
        "cwd": str(context.workspace.agent_workspace_path),
        "model": context.config.model,
        "model_provider": _CODEX_PROVIDER_ID,
        "sandbox": _sandbox(sdk, context),
        "approval_mode": _approval_mode(sdk, context),
        "developer_instructions": context.startup_prompt,
        "config": _codex_thread_config(context),
    }
    return _call_with_supported_kwargs(
        codex.thread_resume,
        kwargs,
        required=("developer_instructions", "model_provider", "sandbox", "approval_mode", "config"),
    )


def _turn_kwargs(sdk: dict[str, Any], context: AdapterContext, instruction: str) -> dict[str, Any]:
    return {
        "input": instruction,
        "approval_mode": _approval_mode(sdk, context),
        "cwd": str(context.workspace.agent_workspace_path),
        "effort": _effort(sdk, context),
        "model": context.config.model,
        "sandbox": _sandbox(sdk, context),
    }


def _codex_thread_config(context: AdapterContext) -> dict[str, object]:
    config = copy.deepcopy(context.mcp.codex_thread_config)
    if context.config.adapters.codex.network_access_enabled:
        workspace_write = config.setdefault("sandbox_workspace_write", {})
        if isinstance(workspace_write, dict):
            workspace_write["network_access"] = True
    return config


def _sandbox(sdk: dict[str, Any], context: AdapterContext) -> Any:
    sandbox = sdk["Sandbox"]
    return _enum_value(sandbox, context.config.adapters.codex.sandbox, "Codex sandbox")


def _approval_mode(sdk: dict[str, Any], context: AdapterContext) -> Any:
    approval = sdk["ApprovalMode"]
    return _enum_value(approval, context.config.adapters.codex.approval_mode, "Codex approval_mode")


def _effort(sdk: dict[str, Any], context: AdapterContext) -> Any:
    if context.config.effort is None:
        return None
    name = {"min": "minimal", "max": "xhigh"}.get(context.config.effort, context.config.effort)
    reasoning_effort = sdk.get("ReasoningEffort")
    if reasoning_effort is None:
        return name
    return _enum_value(reasoning_effort, name, "Codex reasoning effort")


def _enum_value(enum_type: Any, configured: str, label: str) -> Any:
    normalized = configured.replace("-", "_")
    if hasattr(enum_type, normalized):
        return getattr(enum_type, normalized)
    try:
        members = list(enum_type)
    except TypeError:
        members = []
    for member in members:
        value = getattr(member, "value", None)
        name = getattr(member, "name", None)
        if configured in {value, name} or normalized in {str(value).replace("-", "_"), name}:
            return member
    allowed = ", ".join(str(getattr(member, "value", member)) for member in members) or "unknown"
    raise RuntimeError(f"Unsupported {label}: {configured}. Supported values: {allowed}.")


def _is_payload(sdk: dict[str, Any], payload: Any, class_name: str) -> bool:
    expected = sdk.get(class_name)
    if isinstance(expected, type):
        return isinstance(payload, expected)
    return type(payload).__name__ == class_name


def _problem_event_summary(event: Any, current: str | None) -> str | None:
    payload = getattr(event, "payload", None)
    payload_type = type(payload).__name__
    method = getattr(event, "method", None)
    if payload_type not in {"ErrorNotification", "WarningNotification", "ConfigWarningNotification"} and method not in {
        "error",
        "warning",
        "config/warning",
    }:
        return current
    data = event_to_jsonable(event, model_dump_kwargs={"mode": "json", "by_alias": True})
    try:
        text = json.dumps(data, default=str, sort_keys=True)
    except TypeError:
        text = str(data)
    if len(text) > _PROBLEM_EVENT_MAX_CHARS:
        text = text[: _PROBLEM_EVENT_MAX_CHARS - 3] + "..."
    return text


def _call_with_supported_kwargs(func: Callable[..., Any], kwargs: dict[str, Any], *, required: tuple[str, ...] = ()) -> Any:
    clean = {key: value for key, value in kwargs.items() if value is not None}
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(**clean)
    accepts_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    supported = clean if accepts_var_kwargs else {key: value for key, value in clean.items() if key in signature.parameters}
    missing_required = sorted(set(required) - set(supported))
    if missing_required:
        name = getattr(func, "__name__", repr(func))
        raise RuntimeError(f"Codex SDK callable {name} does not support required argument(s): {', '.join(missing_required)}.")
    dropped = sorted(set(clean) - set(supported))
    if dropped:
        logger.warning("Dropping unsupported Codex SDK kwargs for %s: %s", getattr(func, "__name__", repr(func)), ", ".join(dropped))
    try:
        return func(**supported)
    except TypeError:
        if "input" in supported:
            input_value = supported.pop("input")
            return func(input_value, **supported)
        if "prompt" in supported:
            prompt = supported.pop("prompt")
            return func(prompt, **supported)
        raise


def _result_error(result: Any) -> str | None:
    error = getattr(result, "error", None)
    if error is not None:
        message = getattr(error, "message", None)
        return str(message) if message else f"Codex turn ended with an error: {error}"
    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)
    if status_value in {None, "completed", "succeeded", "success"}:
        return None
    return f"Codex turn ended with status: {status_value}."


def _same_turn(candidate: Any, turn_id: str | None) -> bool:
    return turn_id is None or candidate == turn_id


def _raise_for_failed_turn(turn: Any) -> None:
    status = getattr(turn, "status", None)
    status_value = getattr(status, "value", status)
    if status_value != "failed":
        return
    error = getattr(turn, "error", None)
    message = getattr(error, "message", None)
    if message:
        raise RuntimeError(str(message))
    raise RuntimeError("Codex turn failed.")


def _final_assistant_response_from_items(items: list[Any]) -> str | None:
    last_unknown_phase_response: str | None = None
    for item in reversed(items):
        thread_item = getattr(item, "root", item)
        if type(thread_item).__name__ != "AgentMessageThreadItem":
            continue
        text = getattr(thread_item, "text", None)
        if text is None:
            continue
        phase = getattr(thread_item, "phase", None)
        phase_value = getattr(phase, "value", phase)
        if phase_value == "final_answer":
            return str(text)
        if phase_value is None and last_unknown_phase_response is None:
            last_unknown_phase_response = str(text)
    return last_unknown_phase_response


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
