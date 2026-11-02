"""Public run API and framework-controlled execution loop."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Mapping

import yaml

from research_ideation_agent import adapters
from research_ideation_agent.adapters.base import AdapterContext, AdapterOutcome
from research_ideation_agent.adapters.usage import usage_from_codex_result
from research_ideation_agent.config import load_config
from research_ideation_agent.errors import ConfigError, RunTimeout, TopicError
from research_ideation_agent.mcp import render_mcp_config
from research_ideation_agent.models import RepairAttempt, RunConfig, RunRequest, RunResult, UsageSummary, ValidationResult
from research_ideation_agent.pricing import apply_configured_pricing
from research_ideation_agent.prompting import prepare_agent_assets, render_repair_prompt
from research_ideation_agent.records import write_json, write_run_record
from research_ideation_agent.validator import validate_proposal
from research_ideation_agent.workspace import RunWorkspace, create_workspace


def run_research_ideation(
    topic: str,
    config: RunConfig | str | Path,
    *,
    overrides: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> RunResult:
    """Run one topic-to-proposal invocation."""

    _validate_topic(topic)
    config_path: Path | None = None
    resolved_config: RunConfig
    if isinstance(config, RunConfig):
        if overrides is not None:
            raise ConfigError("overrides must be None when config is already a RunConfig.")
        if project_root is not None:
            raise ConfigError("project_root must be None when config is already a RunConfig.")
        resolved_config = config
    else:
        config_path = Path(config)
        resolved_config = load_config(config_path, overrides=overrides, project_root=project_root)
    request = RunRequest(
        topic=topic,
        config=resolved_config,
        config_path=config_path,
        overrides=dict(overrides or {}) if overrides is not None else None,
    )
    return run_request(request, env=env)


def run_request(request: RunRequest, *, env: Mapping[str, str] | None = None) -> RunResult:
    """Execute an already resolved run request."""

    _validate_topic(request.topic)
    effective_env = _effective_env(env)
    required_env_names = _required_env_names(request.config)
    missing = [name for name in required_env_names if not effective_env.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    workspace = create_workspace(request.config)
    started_at = datetime.now(UTC)
    rendered_artifacts: dict[str, Path] = {}
    repair_prompt_paths: list[Path] = []
    adapter = adapters.get_adapter(request.config.harness)
    event_log_path = workspace.logs_path / "adapter_events.jsonl" if request.config.logging.retain_event_log else None
    session_id: str | None = None
    model = request.config.model
    usage: UsageSummary | None = None
    session_usages: dict[str | None, UsageSummary] = {}
    repair_attempts: list[RepairAttempt] = []
    validation = _empty_validation(workspace)
    artifacts = _base_artifacts(workspace)
    secret_values = _secret_env_values(request.config, effective_env)
    deadline = _deadline(request.config)

    try:
        _write_framework_inputs(request, workspace)
        rendered_artifacts.update(prepare_agent_assets(request, workspace))
        mcp = render_mcp_config(request.config, workspace, effective_env)
        rendered_artifacts.update(mcp.redacted_artifacts)
        artifacts.update(rendered_artifacts)
        if event_log_path is not None:
            artifacts["event_log"] = event_log_path
        startup_prompt = rendered_artifacts["startup_prompt"].read_text(encoding="utf-8")
        context = AdapterContext(
            config=request.config,
            workspace=workspace,
            startup_prompt=startup_prompt,
            mcp=mcp,
            env=effective_env,
            event_log_path=event_log_path,
            deadline=deadline,
        )
        try:
            initial = _run_with_timeout(lambda: adapter.run_initial(context), deadline)
        except RunTimeout as exc:
            validation = validate_proposal(workspace.proposal_path)
            _write_validation(workspace, validation)
            result = _result(
                request=request,
                workspace=workspace,
                validation=validation,
                repair_attempts=repair_attempts,
                session_id=session_id,
                model=model,
                usage=usage,
                artifacts=artifacts,
                failure_kind="timeout",
                error=str(exc),
            )
            _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
            return result
        initial = _redact_outcome(initial, secret_values)
        session_id = initial.session_id
        model = initial.model or model
        initial_usage = apply_configured_pricing(request.config, initial.usage, model)
        usage = _record_session_usage(session_usages, initial.session_id, initial_usage)
        if not initial.succeeded:
            validation = validate_proposal(workspace.proposal_path)
            _write_validation(workspace, validation)
            result = _result(
                request=request,
                workspace=workspace,
                validation=validation,
                repair_attempts=repair_attempts,
                session_id=session_id,
                model=model,
                usage=usage,
                artifacts=artifacts,
                failure_kind="adapter_error",
                error=initial.error,
            )
            _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
            return result

        validation = validate_proposal(workspace.proposal_path)
        _write_validation(workspace, validation)
        gate_reason = _artifact_gate_reason(request.config, workspace)
        if _cost_exceeded(request.config, usage):
            result = _result(
                request=request,
                workspace=workspace,
                validation=validation,
                repair_attempts=repair_attempts,
                session_id=session_id,
                model=model,
                usage=usage,
                artifacts=artifacts,
                failure_kind="budget_exceeded",
                error=_budget_error(request.config, usage),
            )
            _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
            return result
        while (not validation.valid or gate_reason is not None) and len(repair_attempts) < request.config.repair.max_attempts:
            attempt_index = len(repair_attempts) + 1
            instruction, repair_prompt_path = render_repair_prompt(request, workspace, validation, attempt_index, gate_reason)
            repair_prompt_paths.append(repair_prompt_path)
            artifacts[f"repair_prompt_{attempt_index}"] = repair_prompt_path
            previous_session_id = session_id
            try:
                outcome = _run_with_timeout(lambda: adapter.run_repair(context, instruction, previous_session_id), deadline)
            except RunTimeout as exc:
                after_timeout_validation = validate_proposal(workspace.proposal_path)
                _write_validation(workspace, after_timeout_validation)
                repair_attempts.append(
                    RepairAttempt(
                        index=attempt_index,
                        instruction=instruction,
                        before_validation=validation,
                        after_validation=after_timeout_validation,
                        continued_session=None,
                        previous_session_id=previous_session_id,
                        session_id=session_id,
                        continuation_error=None,
                        usage=None,
                        error=str(exc),
                    )
                )
                result = _result(
                    request=request,
                    workspace=workspace,
                    validation=after_timeout_validation,
                    repair_attempts=repair_attempts,
                    session_id=session_id,
                    model=model,
                    usage=usage,
                    artifacts=artifacts,
                    failure_kind="timeout",
                    error=str(exc),
                )
                _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
                return result
            outcome = _redact_outcome(outcome, secret_values)
            session_id = outcome.session_id or session_id
            model = outcome.model or model
            outcome_usage = apply_configured_pricing(
                request.config,
                outcome.usage,
                model,
            )
            usage = _record_session_usage(session_usages, outcome.session_id, outcome_usage)
            if not outcome.succeeded:
                attempt = RepairAttempt(
                    index=attempt_index,
                    instruction=instruction,
                    before_validation=validation,
                    after_validation=None,
                    continued_session=outcome.continued_session,
                    previous_session_id=previous_session_id,
                    session_id=outcome.session_id,
                    continuation_error=outcome.continuation_error,
                    usage=outcome_usage,
                    error=outcome.error,
                )
                repair_attempts.append(attempt)
                result = _result(
                    request=request,
                    workspace=workspace,
                    validation=validation,
                    repair_attempts=repair_attempts,
                    session_id=session_id,
                    model=model,
                    usage=usage,
                    artifacts=artifacts,
                    failure_kind="adapter_error",
                    error=outcome.error,
                )
                _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
                return result
            after_validation = validate_proposal(workspace.proposal_path)
            _write_validation(workspace, after_validation)
            repair_attempts.append(
                RepairAttempt(
                    index=attempt_index,
                    instruction=instruction,
                    before_validation=validation,
                    after_validation=after_validation,
                    continued_session=outcome.continued_session,
                    previous_session_id=previous_session_id,
                    session_id=outcome.session_id,
                    continuation_error=outcome.continuation_error,
                    usage=outcome_usage,
                    error=None,
                )
            )
            validation = after_validation
            gate_reason = _artifact_gate_reason(request.config, workspace)
            if _cost_exceeded(request.config, usage):
                result = _result(
                    request=request,
                    workspace=workspace,
                    validation=validation,
                    repair_attempts=repair_attempts,
                    session_id=session_id,
                    model=model,
                    usage=usage,
                    artifacts=artifacts,
                    failure_kind="budget_exceeded",
                    error=_budget_error(request.config, usage),
                )
                _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
                return result

        complete = validation.valid and gate_reason is None
        failure_kind = None if complete else "validation_failed"
        error = None if complete else _incomplete_error(validation, gate_reason)
        result = _result(
            request=request,
            workspace=workspace,
            validation=validation,
            repair_attempts=repair_attempts,
            session_id=session_id,
            model=model,
            usage=usage,
            artifacts=artifacts,
            failure_kind=failure_kind,
            error=error,
        )
        _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
        return result
    except (KeyboardInterrupt, SystemExit):
        validation = validate_proposal(workspace.proposal_path)
        _write_validation(workspace, validation)
        result = _result(
            request=request,
            workspace=workspace,
            validation=validation,
            repair_attempts=repair_attempts,
            session_id=session_id,
            model=model,
            usage=usage,
            artifacts=artifacts,
            failure_kind="cancelled",
            error="Run was cancelled.",
        )
        _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
        return result
    except Exception as exc:
        error = _redact_text(f"Runtime failure: {exc.__class__.__name__}: {exc}", secret_values)
        validation = validate_proposal(workspace.proposal_path)
        _write_validation(workspace, validation)
        result = _result(
            request=request,
            workspace=workspace,
            validation=validation,
            repair_attempts=repair_attempts,
            session_id=session_id,
            model=model,
            usage=usage,
            artifacts=artifacts,
            failure_kind="runtime_error",
            error=error,
        )
        _finish_record(request, workspace, result, started_at, rendered_artifacts, repair_prompt_paths, adapter.sdk_name, adapter.sdk_version, effective_env)
        return result
    finally:
        try:
            adapter.close()
        except Exception as exc:  # Adapter teardown must not discard the run result.
            _write_adapter_close_error(workspace, _redact_text(f"{exc.__class__.__name__}: {exc}", secret_values))
        finally:
            _cleanup_codex_home_after_run(request.config, workspace)


def _validate_topic(topic: str) -> None:
    if not isinstance(topic, str) or not topic.strip():
        raise TopicError("Topic must be a non-empty string.")


def _effective_env(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)
    return dict(os.environ)


def _required_env_names(config: RunConfig) -> list[str]:
    names = list(config.mcp.scholar_gateway.required_env_vars)
    if config.harness == "claude":
        names.extend(_claude_api_env_names(config))
    else:
        names.extend(_codex_api_env_names(config))
    return _dedupe(names)


def _all_recorded_env_names(config: RunConfig, env: dict[str, str]) -> list[str]:
    names = list(config.mcp.scholar_gateway.required_env_vars)
    names.extend(name for name in config.mcp.scholar_gateway.optional_env_vars if env.get(name))
    if config.harness == "claude":
        names.extend(_claude_api_env_names(config))
    else:
        names.extend(_codex_api_env_names(config))
    return _dedupe(names)


def _claude_api_env_names(config: RunConfig) -> list[str]:
    claude_cfg = config.adapters.claude
    return [claude_cfg.api_key_env, claude_cfg.base_url_env]


def _codex_api_env_names(config: RunConfig) -> list[str]:
    codex_cfg = config.adapters.codex
    return [codex_cfg.api_key_env, codex_cfg.base_url_env]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _write_framework_inputs(request: RunRequest, workspace: RunWorkspace) -> None:
    (workspace.input_path / "topic.txt").write_text(request.topic, encoding="utf-8")
    config_yaml = yaml.safe_dump(request.config.to_redacted_dict(), sort_keys=False)
    (workspace.input_path / "resolved_config.yaml").write_text(config_yaml, encoding="utf-8")


def _write_validation(workspace: RunWorkspace, validation: ValidationResult) -> None:
    write_json(workspace.validation_path, validation.to_dict())


def _empty_validation(workspace: RunWorkspace) -> ValidationResult:
    return ValidationResult(
        valid=False,
        proposal_path=workspace.proposal_path,
        missing=["title", "introduction", "related_work", "methods", "experiments_design", "references"],
        present=[],
        errors=["Validation has not run."],
        validator_version="mvp-structure-v1",
    )


def _base_artifacts(workspace: RunWorkspace) -> dict[str, Path]:
    return {"proposal": workspace.proposal_path, "validation": workspace.validation_path, "run_record": workspace.run_record_path}


def _result(
    *,
    request: RunRequest,
    workspace: RunWorkspace,
    validation: ValidationResult,
    repair_attempts: list[RepairAttempt],
    session_id: str | None,
    model: str | None,
    usage: object,
    artifacts: dict[str, Path],
    failure_kind: str | None,
    error: str | None,
) -> RunResult:
    if usage is None:
        # An interrupted run (cancel/timeout) never produced an adapter outcome, so
        # normal usage capture was skipped. Salvage it from the streamed token events
        # so cost still lands in the run record for offline analysis.
        usage = _salvage_usage_from_event_log(request, workspace, model)
    return RunResult(
        run_id=workspace.run_id,
        status="succeeded" if failure_kind is None and validation.valid else "failed",
        failure_kind=failure_kind,  # type: ignore[arg-type]
        harness=request.config.harness,
        workspace_path=workspace.run_path,
        agent_workspace_path=workspace.agent_workspace_path,
        proposal_path=workspace.proposal_path,
        validation=validation,
        repair_attempts=list(repair_attempts),
        session_id=session_id,
        model=model,
        usage=usage,  # type: ignore[arg-type]
        artifacts=dict(artifacts),
        error=error,
    )


def _salvage_usage_from_event_log(
    request: RunRequest, workspace: RunWorkspace, model: str | None
) -> UsageSummary | None:
    """Reconstruct priced usage from the streamed codex token events.

    Used only as a fallback when a run was interrupted before any adapter outcome
    was captured. Returns ``None`` for harnesses or logs without codex token events,
    leaving behaviour unchanged.
    """
    token_usage = _last_codex_token_usage(workspace.logs_path / "adapter_events.jsonl")
    if token_usage is None:
        return None
    usage = usage_from_codex_result(SimpleNamespace(usage=token_usage, duration_ms=None))
    if usage is None:
        return None
    return apply_configured_pricing(request.config, usage, model or request.config.model)


def _last_codex_token_usage(event_log_path: Path) -> dict | None:
    if not event_log_path.exists():
        return None
    latest: dict | None = None
    with event_log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if "thread/tokenUsage/updated" not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "thread/tokenUsage/updated":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            payload = message.get("event", {}).get("payload", message)
            token_usage = payload.get("tokenUsage") if isinstance(payload, dict) else None
            if isinstance(token_usage, dict):
                latest = token_usage
    return latest


def _finish_record(
    request: RunRequest,
    workspace: RunWorkspace,
    result: RunResult,
    started_at: datetime,
    rendered_artifacts: dict[str, Path],
    repair_prompt_paths: list[Path],
    adapter_sdk: str,
    adapter_sdk_version: str | None,
    env: dict[str, str],
) -> None:
    if not request.config.logging.write_run_record:
        return
    write_run_record(
        request=request,
        workspace=workspace,
        result=result,
        created_at=workspace.created_at,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        rendered_artifacts=rendered_artifacts,
        repair_prompt_paths=repair_prompt_paths,
        adapter_sdk=adapter_sdk,
        adapter_sdk_version=adapter_sdk_version,
        env_var_names=_all_recorded_env_names(request.config, env),
    )


def _cleanup_codex_home_after_run(config: RunConfig, workspace: RunWorkspace) -> None:
    if config.harness != "codex" or not config.adapters.codex.cleanup_codex_home:
        return
    codex_home = workspace.framework_path / "codex-home"
    if not codex_home.exists():
        return

    errors: list[str] = []
    if config.logging.retain_event_log:
        try:
            _archive_codex_session_logs(codex_home, workspace.logs_path)
        except OSError as exc:
            errors.append(f"archive sessions failed: {exc}")

    try:
        shutil.rmtree(codex_home)
    except OSError as exc:
        errors.append(f"remove codex-home failed: {exc}")

    if errors:
        _write_codex_cleanup_error(workspace, errors)


def _archive_codex_session_logs(codex_home: Path, logs_path: Path) -> None:
    sessions_path = codex_home / "sessions"
    if not sessions_path.is_dir():
        return
    destination_root = logs_path / "codex_sessions"
    for source in sessions_path.rglob("*.jsonl"):
        if not source.is_file():
            continue
        destination = destination_root / source.relative_to(sessions_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _write_codex_cleanup_error(workspace: RunWorkspace, errors: list[str]) -> None:
    try:
        workspace.logs_path.mkdir(parents=True, exist_ok=True)
        message = "\n".join(errors)
        (workspace.logs_path / "codex_home_cleanup_error.txt").write_text(message + "\n", encoding="utf-8")
    except OSError:
        return


def _write_adapter_close_error(workspace: RunWorkspace, message: str | None) -> None:
    try:
        workspace.logs_path.mkdir(parents=True, exist_ok=True)
        (workspace.logs_path / "adapter_close_error.txt").write_text((message or "") + "\n", encoding="utf-8")
    except OSError:
        return


def _redact_outcome(outcome: AdapterOutcome, secret_values: list[str]) -> AdapterOutcome:
    if outcome.error is None and outcome.continuation_error is None:
        return outcome
    return AdapterOutcome(
        session_id=outcome.session_id,
        model=outcome.model,
        usage=outcome.usage,
        error=_redact_text(outcome.error, secret_values) if outcome.error else None,
        continued_session=outcome.continued_session,
        continuation_error=_redact_text(outcome.continuation_error, secret_values) if outcome.continuation_error else None,
    )


def _redact_text(text: str | None, secret_values: list[str]) -> str | None:
    if text is None:
        return None
    redacted = text
    for value in secret_values:
        redacted = redacted.replace(value, "<redacted>")
    return redacted


def _secret_env_values(config: RunConfig, env: dict[str, str]) -> list[str]:
    values: list[str] = []
    for name in _all_recorded_env_names(config, env):
        value = env.get(name)
        if value and len(value) >= 4:
            values.append(value)
    return _dedupe(values)


def _deadline(config: RunConfig) -> float | None:
    if config.budgets.timeout_seconds is None:
        return None
    return time.monotonic() + config.budgets.timeout_seconds


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _run_with_timeout(call: Callable[[], AdapterOutcome], deadline: float | None) -> AdapterOutcome:
    remaining = _remaining_seconds(deadline)
    if remaining is None:
        return call()
    if remaining <= 0:
        raise RunTimeout("Run exceeded configured timeout.")
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        outcome = call()
        if _remaining_seconds(deadline) == 0:
            raise RunTimeout("Run exceeded configured timeout.")
        return outcome

    def _handle_timeout(signum: int, frame: object) -> None:
        raise RunTimeout("Run exceeded configured timeout.")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _record_session_usage(
    session_usages: dict[str | None, UsageSummary],
    session_id: str | None,
    usage: UsageSummary | None,
) -> UsageSummary | None:
    """Track usage per adapter session and return the combined run total.

    A resumed (continued) session re-reports its *entire* cumulative token usage —
    already including the earlier turns in that thread — so token and cost fields
    must overwrite the session's prior entry, not add to it; summing them would
    double-count the initial turn. ``turns`` and ``duration_ms``, by contrast, are
    reported per turn, so they accumulate within the session. Distinct sessions are
    summed across the board.
    """
    if usage is not None:
        prior = session_usages.get(session_id)
        if prior is None:
            session_usages[session_id] = usage
        else:
            session_usages[session_id] = replace(
                usage,
                turns=_sum_optional(prior.turns, usage.turns),
                duration_ms=_sum_optional(prior.duration_ms, usage.duration_ms),
            )
    combined: UsageSummary | None = None
    for entry in session_usages.values():
        combined = _combine_usage(combined, entry)
    return combined


def _combine_usage(left: UsageSummary | None, right: UsageSummary | None) -> UsageSummary | None:
    if left is None:
        return right
    if right is None:
        return left
    return UsageSummary(
        input_tokens=_sum_optional(left.input_tokens, right.input_tokens),
        input_tokens_include_cached=_combine_optional_bool(
            left.input_tokens_include_cached,
            right.input_tokens_include_cached,
        ),
        cached_input_tokens=_sum_optional(left.cached_input_tokens, right.cached_input_tokens),
        cache_creation_input_tokens=_sum_optional(left.cache_creation_input_tokens, right.cache_creation_input_tokens),
        output_tokens=_sum_optional(left.output_tokens, right.output_tokens),
        reasoning_output_tokens=_sum_optional(left.reasoning_output_tokens, right.reasoning_output_tokens),
        total_tokens=_sum_optional(left.total_tokens, right.total_tokens),
        # The context window is a per-model constant, not an additive quantity — keep it,
        # don't sum it, so the long-context tier still resolves on the combined usage.
        model_context_window=(
            left.model_context_window
            if left.model_context_window is not None
            else right.model_context_window
        ),
        total_cost_usd=_sum_optional_float(left.total_cost_usd, right.total_cost_usd),
        turns=_sum_optional(left.turns, right.turns),
        duration_ms=_sum_optional(left.duration_ms, right.duration_ms),
    )


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _sum_optional_float(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _combine_optional_bool(left: bool | None, right: bool | None) -> bool | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if left == right else None


def _cost_exceeded(config: RunConfig, usage: UsageSummary | None) -> bool:
    if config.budgets.max_cost_usd is None or usage is None or usage.total_cost_usd is None:
        return False
    return usage.total_cost_usd > config.budgets.max_cost_usd


def _budget_error(config: RunConfig, usage: UsageSummary | None) -> str:
    actual = usage.total_cost_usd if usage and usage.total_cost_usd is not None else None
    return f"Run exceeded configured cost budget: {actual} > {config.budgets.max_cost_usd}."


def _artifact_gate_reason(config: RunConfig, workspace: RunWorkspace) -> str | None:
    """Run declared stop hooks as a post-run gate (harness-agnostic enforcement).

    Codex does not honor in-run Stop hooks, so the framework re-runs each declared
    hook script after the agent finishes and treats a ``decision: block`` result as
    an incomplete run, feeding it into the repair loop. The hook checks only artifact
    presence and reviewer-verdict status, so this stays domain-agnostic.
    """
    for spec in config.hooks.stop:
        payload = json.dumps({"cwd": str(workspace.agent_workspace_path)})
        try:
            proc = subprocess.run(
                [sys.executable, str(spec.script)],
                input=payload,
                capture_output=True,
                text=True,
                timeout=spec.timeout,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        out = (proc.stdout or "").strip()
        if not out:
            continue
        try:
            decision = json.loads(out)
        except json.JSONDecodeError:
            continue
        if isinstance(decision, dict) and decision.get("decision") == "block":
            return str(decision.get("reason") or "Artifact gate not cleared.")
    return None


def _incomplete_error(validation: ValidationResult, gate_reason: str | None) -> str:
    if not validation.valid:
        return f"Final validation failed; missing: {', '.join(validation.missing)}"
    return gate_reason or "Artifact gate not cleared."
