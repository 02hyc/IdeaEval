from __future__ import annotations

import json
import os
import time
import tomllib
from pathlib import Path

import pytest

import research_ideation_agent.adapters as adapter_module
from research_ideation_agent import load_config, run_research_ideation, run_request
from research_ideation_agent.adapters.base import AdapterContext, AdapterOutcome, AgentAdapter
from research_ideation_agent.errors import ConfigError, TopicError, WorkspaceError
from research_ideation_agent.models import RunRequest, UsageSummary


ENV = {
    "WTS_API_KEY": "wts-secret",
    "GPT_API_KEY": "gpt-secret",
    "GPT_API_BASE_URL": "https://provider.example/v1",
}

CLAUDE_ENV = {
    "WTS_API_KEY": "wts-secret",
    "ANTHROPIC_API_KEY": "claude-secret",
    "ANTHROPIC_BASE_URL": "https://claude.example/v1",
}


class SuccessAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        if context.config.hooks.stop:
            _write_gate_artifacts(context.workspace.agent_workspace_path)
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=UsageSummary(turns=1))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run")


class CodexHomeAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        codex_home = context.workspace.framework_path / "codex-home"
        session_log = codex_home / "sessions" / "2026" / "06" / "05" / "rollout-test.jsonl"
        cache_file = codex_home / ".tmp" / "plugins" / "plugin-cache.txt"
        session_log.parent.mkdir(parents=True)
        cache_file.parent.mkdir(parents=True)
        session_log.write_text('{"type":"session"}\n', encoding="utf-8")
        cache_file.write_text("cache\n", encoding="utf-8")
        (codex_home / "logs_2.sqlite").write_text("sqlite-placeholder\n", encoding="utf-8")
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=UsageSummary(turns=1))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run")


class RepairAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_proposal_without_references(), encoding="utf-8")
        return AdapterOutcome(
            session_id="thread-1",
            model=context.config.model,
            usage=UsageSummary(input_tokens=10, output_tokens=20, total_tokens=30, total_cost_usd=0.5, turns=1, duration_ms=100),
        )

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        assert "references" in instruction
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        # A resumed codex thread reports *cumulative* token usage (already including the
        # initial turn's 10/20/30 tokens and 0.5 cost), but turns and duration are per
        # turn. The run total must take the cumulative tokens as-is (not add them onto
        # the initial), while still summing turns and duration.
        return AdapterOutcome(
            session_id="thread-1",
            model=context.config.model,
            usage=UsageSummary(input_tokens=14, output_tokens=26, total_tokens=40, total_cost_usd=0.70, turns=1, duration_ms=50),
            continued_session=True,
        )


class CostlyAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=UsageSummary(total_cost_usd=2.0))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run after cost budget is exceeded")


class TokenUsageAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        if context.config.hooks.stop:
            _write_gate_artifacts(context.workspace.agent_workspace_path)
        return AdapterOutcome(
            session_id="thread-1",
            model=None,
            usage=UsageSummary(
                input_tokens=1000,
                cached_input_tokens=400,
                output_tokens=200,
                reasoning_output_tokens=50,
                total_tokens=1200,
            ),
        )

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run")


class SlowAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        time.sleep(2)
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=None)

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run after timeout")


class ErrorAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        return AdapterOutcome(
            session_id="thread-1",
            model=context.config.model,
            usage=None,
            error="Adapter saw gpt-secret and wts-secret while opening /mnt/user/workspace",
        )

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run after adapter error")


class CancelAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        raise KeyboardInterrupt

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run after cancellation")


class CloseFailureAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=UsageSummary(turns=1))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        raise AssertionError("repair should not run")

    def close(self) -> None:
        raise RuntimeError("teardown blew up after seeing gpt-secret")


class RepairErrorAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_proposal_without_references(), encoding="utf-8")
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=UsageSummary(turns=1))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        return AdapterOutcome(
            session_id="thread-1",
            model=context.config.model,
            usage=None,
            error="Repair adapter failed",
        )


class MultiRepairAdapter(AgentAdapter):
    sdk_name = "fake-sdk"

    def __init__(self) -> None:
        self.repairs = 0

    def run_initial(self, context: AdapterContext) -> AdapterOutcome:
        context.workspace.proposal_path.write_text(_proposal_without_references(), encoding="utf-8")
        return AdapterOutcome(session_id="thread-1", model=context.config.model, usage=UsageSummary(turns=1))

    def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
        self.repairs += 1
        if self.repairs >= 2:
            context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
        return AdapterOutcome(
            session_id="thread-1",
            model=context.config.model,
            usage=UsageSummary(turns=1),
            continued_session=True,
        )


def test_run_request_success_writes_workspace_and_redacted_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "success", "repair.max_attempts": 0},
    )
    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    assert result.status == "succeeded"
    assert result.validation.valid
    assert result.workspace_path.parent.parent == tmp_path  # runs nest under <root>/<model>/<run-id>
    assert result.workspace_path.name == "success"
    assert result.proposal_path == result.workspace_path / "agent_workspace" / "proposal.md"
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    serialized = json.dumps(record)
    assert "gpt-secret" not in serialized
    assert "wts-secret" not in serialized
    assert record["redaction"]["secrets_written"] is False
    assert record["adapter"]["sdk"] == "fake-sdk"
    # Fake adapter has no PyPI distribution name; sdk_version stays None.
    assert record["adapter"]["sdk_version"] is None


def test_repair_loop_records_same_session_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: RepairAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "repair", "repair.max_attempts": 1},
    )
    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "succeeded"
    assert len(result.repair_attempts) == 1
    attempt = result.repair_attempts[0]
    assert attempt.continued_session is True
    assert attempt.previous_session_id == "thread-1"
    assert attempt.after_validation is not None
    assert attempt.after_validation.valid
    assert result.usage is not None
    # Token/cost totals are the resumed thread's cumulative, NOT initial+repair summed
    # (summing would double-count: input 24, cost 1.20).
    assert result.usage.input_tokens == 14
    assert result.usage.output_tokens == 26
    assert result.usage.total_tokens == 40
    assert result.usage.total_cost_usd == pytest.approx(0.70)
    # Turns and duration are per-turn, so they still sum across the two turns.
    assert result.usage.turns == 2
    assert result.usage.duration_ms == 150
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["usage"]["turns"] == 2
    assert record["repair_attempts"][0]["usage"]["turns"] == 1


def test_cost_budget_failure_is_recorded_after_adapter_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: CostlyAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "budget",
            "budgets.max_cost_usd": 1.0,
        },
    )
    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "failed"
    assert result.failure_kind == "budget_exceeded"
    assert result.validation.valid
    assert result.usage is not None
    assert result.usage.total_cost_usd == 2.0
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["failure_kind"] == "budget_exceeded"


def test_configured_pricing_turns_usage_tokens_into_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: TokenUsageAdapter())
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "priced",
            "repair.max_attempts": 0,
            "pricing.models": {
                "gpt-5.4": {
                    "input_per_million_usd": 10,
                    "cached_input_per_million_usd": 1,
                    "output_per_million_usd": 20,
                    "reasoning_output_per_million_usd": 30,
                }
            },
        },
    )

    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "succeeded"
    assert result.model == "gpt-5.4"
    assert result.usage is not None
    assert result.usage.total_cost_usd == pytest.approx(0.0109)
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["usage"]["total_cost_usd"] == pytest.approx(0.0109)
    assert record["resolved_config"]["pricing"]["models"]["gpt-5.4"]["input_per_million_usd"] == 10


def test_timeout_failure_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SlowAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "timeout",
            "budgets.timeout_seconds": 1,
            "repair.max_attempts": 0,
        },
    )
    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "failed"
    assert result.failure_kind == "timeout"
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["failure_kind"] == "timeout"


def test_error_redaction_only_uses_known_secret_env_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: ErrorAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "redaction", "repair.max_attempts": 0},
    )
    env = dict(ENV)
    env["HOME"] = "/mnt/user"

    result = run_research_ideation("A topic", cfg, env=env)

    assert result.status == "failed"
    assert result.failure_kind == "adapter_error"
    assert result.error is not None
    assert "gpt-secret" not in result.error
    assert "wts-secret" not in result.error
    assert "/mnt/user/workspace" in result.error


def test_keyboard_interrupt_is_recorded_as_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: CancelAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "cancelled", "repair.max_attempts": 0},
    )

    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "failed"
    assert result.failure_kind == "cancelled"
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["failure_kind"] == "cancelled"


def test_api_env_default_does_not_load_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research_ideation_agent.runtime import _effective_env

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RIA_DOTENV_SENTINEL", raising=False)
    (tmp_path / ".env").write_text("RIA_DOTENV_SENTINEL=from-dotenv\n", encoding="utf-8")

    env = _effective_env(None)

    assert "RIA_DOTENV_SENTINEL" not in env
    assert os.environ.get("RIA_DOTENV_SENTINEL") is None


def test_claude_skills_are_packaged_as_named_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/scientific-hypothesis-generator/claude.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "claude-skills",
            "repair.max_attempts": 0,
            "subagents.selected": [],
        },
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=CLAUDE_ENV)

    skill_root = result.agent_workspace_path / ".claude" / "skills"
    assert (skill_root / "literature-review" / "SKILL.md").is_file()
    assert not (skill_root / "literature-review.md").exists()


def test_claude_subagents_are_packaged_into_dot_claude_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/scientific-hypothesis-generator/claude.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "claude-subagents",
            "repair.max_attempts": 0,
        },
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=CLAUDE_ENV)

    agents_dir = result.agent_workspace_path / ".claude" / "agents"
    expected_ids = ["scientific-reviewer"]
    for subagent_id in expected_ids:
        path = agents_dir / f"{subagent_id}.md"
        assert path.is_file(), f"missing {path}"
        text = path.read_text(encoding="utf-8")
        first_line = text.splitlines()[0]
        assert first_line == "---", f"{path} should start with YAML frontmatter, got {first_line!r}"
        frontmatter = text.split("---", 2)[1]
        assert "tools:" not in frontmatter

    # The legacy paths should not be created any more.
    legacy_subagents_dir = result.agent_workspace_path / "subagents"
    assert not legacy_subagents_dir.exists()


def test_codex_subagents_are_packaged_into_dot_codex_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "codex-subagents",
            "repair.max_attempts": 0,
        },
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    agents_dir = result.agent_workspace_path / ".codex" / "agents"
    expected_ids = ["scientific-reviewer"]
    for subagent_id in expected_ids:
        path = agents_dir / f"{subagent_id}.toml"
        assert path.is_file(), f"missing {path}"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == subagent_id
        assert data["description"]
        assert "sandbox_mode" not in data
        assert "model" not in data
        assert "model_reasoning_effort" not in data
        assert "mcp_servers" not in data
        assert "You are an independent reviewer" in data["developer_instructions"]

    assert not (result.agent_workspace_path / ".claude" / "agents").exists()


def test_codex_simple_assets_package_no_skills_or_subagents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "codex-simple",
            "repair.max_attempts": 0,
        },
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    assert result.status == "succeeded"
    assert (result.agent_workspace_path / "AGENTS.md").is_file()
    assert not (result.agent_workspace_path / "CLAUDE.md").exists()
    assert not (result.agent_workspace_path / ".agents").exists()
    assert not (result.agent_workspace_path / ".codex" / "agents").exists()

    guidance = (result.agent_workspace_path / "AGENTS.md").read_text(encoding="utf-8")
    startup_prompt = result.artifacts["startup_prompt"].read_text(encoding="utf-8")
    assert "proposal.md" in guidance
    assert "## Required Artifacts" in guidance
    assert "## Evidence Discipline" in guidance
    assert "## Final Quality Gate" in guidance
    assert "## Required proposal structure" not in guidance
    assert "available file-reading, file-writing, and editing tools" in startup_prompt
    assert "`Read`, `Write`, `Edit`" not in startup_prompt
    assert "summarize_paper" not in startup_prompt


def test_scientific_assets_startup_mentions_summarize_paper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "scientific-startup",
            "repair.max_attempts": 0,
        },
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    assert result.status == "succeeded"
    startup_prompt = result.artifacts["startup_prompt"].read_text(encoding="utf-8")
    assert "Cutoff date:" not in startup_prompt
    assert "cutoff" not in startup_prompt.lower()
    assert "- literature-review" in startup_prompt
    assert "- hypothesis-method" in startup_prompt
    assert "- proposal-writeup" in startup_prompt
    assert "scientific-reviewer" in startup_prompt
    assert "notes/reviews/evidence_review.md" in startup_prompt
    assert "notes/gap_and_questions.md" in startup_prompt
    assert "notes/hypothesis.md" in startup_prompt
    assert "notes/method.md" in startup_prompt
    assert "notes/experiment_plan.md" in startup_prompt
    assert "notes/reviews/proposal_review.md" in startup_prompt
    assert "summarize_paper" in startup_prompt
    assert (result.agent_workspace_path / "shared" / "rubric.md").is_file()


def test_scientific_run_blocked_when_gate_artifacts_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoReviewsAdapter(AgentAdapter):
        sdk_name = "fake-sdk"

        def run_initial(self, context: AdapterContext) -> AdapterOutcome:
            context.workspace.proposal_path.write_text(_valid_proposal(), encoding="utf-8")
            return AdapterOutcome(session_id="t", model=context.config.model, usage=UsageSummary(turns=1))

        def run_repair(self, context: AdapterContext, instruction: str, previous_session_id: str | None) -> AdapterOutcome:
            raise AssertionError("repair should not run")

    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: _NoReviewsAdapter())
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "gate-blocked", "repair.max_attempts": 0},
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    assert result.status == "failed"
    assert result.failure_kind == "validation_failed"
    assert "artifact" in (result.error or "").lower()


def test_codex_home_is_cleaned_after_run_and_session_logs_are_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: CodexHomeAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "codex-cleanup", "repair.max_attempts": 0},
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    assert result.status == "succeeded"
    assert not (result.workspace_path / "framework" / "codex-home").exists()
    archived_session = result.workspace_path / "framework" / "logs" / "codex_sessions" / "2026" / "06" / "05" / "rollout-test.jsonl"
    assert archived_session.read_text(encoding="utf-8") == '{"type":"session"}\n'
    assert not (result.workspace_path / "framework" / "logs" / "codex_home_cleanup_error.txt").exists()


def test_codex_home_cleanup_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: CodexHomeAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "codex-keep-home",
            "repair.max_attempts": 0,
            "adapters.codex.cleanup_codex_home": False,
        },
    )

    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    codex_home = result.workspace_path / "framework" / "codex-home"
    assert result.status == "succeeded"
    assert (codex_home / ".tmp" / "plugins" / "plugin-cache.txt").is_file()
    assert (codex_home / "sessions" / "2026" / "06" / "05" / "rollout-test.jsonl").is_file()


def test_missing_required_env_fails_before_workspace(tmp_path: Path) -> None:
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "missing-env"},
    )

    with pytest.raises(ConfigError, match="GPT_API_KEY"):
        run_research_ideation("A topic", cfg, env={"WTS_API_KEY": "wts"})

    assert not (tmp_path / "missing-env").exists()


def test_codex_custom_env_names_are_required_and_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "custom-env",
            "repair.max_attempts": 0,
            "adapters.codex.api_key_env": "ALT_MODEL_API_KEY",
            "adapters.codex.base_url_env": "ALT_MODEL_BASE_URL",
        },
    )

    with pytest.raises(ConfigError, match="ALT_MODEL_API_KEY"):
        run_research_ideation("A topic", cfg, env={"WTS_API_KEY": "wts"})

    assert not list(tmp_path.glob("*/custom-env"))

    result = run_research_ideation(
        "A topic",
        cfg,
        env={
            "WTS_API_KEY": "wts-secret",
            "ALT_MODEL_API_KEY": "alt-secret",
            "ALT_MODEL_BASE_URL": "https://alt-provider.example/v1",
            "GPT_API_KEY": "unused-gpt-secret",
            "GPT_API_BASE_URL": "https://unused-gpt.example/v1",
        },
    )

    assert result.status == "succeeded"
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["redaction"]["env_var_names_recorded"] == ["WTS_API_KEY", "ALT_MODEL_API_KEY", "ALT_MODEL_BASE_URL"]
    serialized = json.dumps(record)
    assert "alt-secret" not in serialized
    assert "unused-gpt-secret" not in serialized


def test_invalid_topic_fails_before_config_loading() -> None:
    with pytest.raises(TopicError):
        run_research_ideation("   ", "does-not-exist.yaml", env=ENV)


def test_clean_existing_clears_and_reuses_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    overrides = {"workspace.root": str(tmp_path), "workspace.run_id": "reuse", "repair.max_attempts": 0}
    first = run_research_ideation("A topic", load_config("configs/simple/gpt.yaml", overrides=overrides), env=ENV)
    assert first.status == "succeeded"
    stale = first.workspace_path / "stale_marker.txt"
    stale.write_text("stale", encoding="utf-8")

    second = run_research_ideation(
        "A topic",
        load_config("configs/simple/gpt.yaml", overrides={**overrides, "workspace.clean_existing": True}),
        env=ENV,
    )

    assert second.status == "succeeded"
    assert second.validation.valid
    assert not stale.exists()


def test_existing_workspace_without_clean_existing_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: SuccessAdapter())
    overrides = {"workspace.root": str(tmp_path), "workspace.run_id": "collide", "repair.max_attempts": 0}
    run_research_ideation("A topic", load_config("configs/simple/gpt.yaml", overrides=overrides), env=ENV)

    with pytest.raises(WorkspaceError, match="already exists"):
        run_research_ideation("A topic", load_config("configs/simple/gpt.yaml", overrides=overrides), env=ENV)


def test_workspace_run_id_with_separator_is_rejected(tmp_path: Path) -> None:
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "nested/run"},
    )

    with pytest.raises(WorkspaceError, match="path-safe"):
        run_research_ideation("A topic", cfg, env=ENV)

    assert not (tmp_path / "nested").exists()


def test_create_workspace_nests_run_under_model(tmp_path: Path) -> None:
    from research_ideation_agent.workspace import create_workspace, model_subdir

    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "r1"},
    )
    ws = create_workspace(cfg)
    sub = model_subdir(cfg)
    assert ws.run_path == tmp_path / sub / "r1"
    assert ws.run_path.is_dir()
    assert ws.run_record_path == tmp_path / sub / "r1" / "run_record.json"
    assert ws.agent_workspace_path == tmp_path / sub / "r1" / "agent_workspace"


def test_model_subdir_prefers_profile_and_sanitizes() -> None:
    from types import SimpleNamespace

    from research_ideation_agent.workspace import model_subdir

    def ns(**kwargs: object) -> SimpleNamespace:
        kwargs.setdefault("model_profile", None)
        return SimpleNamespace(**kwargs)

    # The model profile wins over a messy raw model id.
    assert model_subdir(ns(model_profile="claude-opus-4.6", model="global.anthropic.claude-opus-4-6-v1", harness="claude")) == "claude-opus-4.6"
    assert model_subdir(ns(model="Pro/zai-org/GLM-5.1", harness="codex")) == "Pro-zai-org-GLM-5.1"
    assert model_subdir(ns(model=None, harness="claude")) == "claude"
    assert model_subdir(ns(model="   ", harness="codex")) == "codex"
    assert model_subdir(ns(model="gpt-5.4", harness="codex")) == "gpt-5.4"


def test_model_profile_names_the_run_folder(tmp_path: Path) -> None:
    from research_ideation_agent.workspace import create_workspace, model_subdir

    cfg = load_config(
        "configs/scientific-hypothesis-generator/claude.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "r1", "model_profile": "claude-opus-4.6"},
    )
    assert cfg.model_profile == "claude-opus-4.6"
    assert model_subdir(cfg) == "claude-opus-4.6"  # profile wins over the raw model id
    ws = create_workspace(cfg)
    assert ws.run_path == tmp_path / "claude-opus-4.6" / "r1"


def test_adapter_close_failure_does_not_discard_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: CloseFailureAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "close-fail", "repair.max_attempts": 0},
    )
    result = run_request(RunRequest(topic="A topic", config=cfg), env=ENV)

    assert result.status == "succeeded"
    assert result.validation.valid
    close_error = result.workspace_path / "framework" / "logs" / "adapter_close_error.txt"
    assert close_error.is_file()
    contents = close_error.read_text(encoding="utf-8")
    assert "RuntimeError" in contents
    assert "gpt-secret" not in contents


def test_repair_adapter_error_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: RepairErrorAdapter())
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "repair-error", "repair.max_attempts": 1},
    )
    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "failed"
    assert result.failure_kind == "adapter_error"
    assert result.error == "Repair adapter failed"
    assert len(result.repair_attempts) == 1
    attempt = result.repair_attempts[0]
    assert attempt.error == "Repair adapter failed"
    assert attempt.after_validation is None
    record = json.loads((result.workspace_path / "run_record.json").read_text(encoding="utf-8"))
    assert record["failure_kind"] == "adapter_error"


def test_multiple_repair_attempts_until_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = MultiRepairAdapter()
    monkeypatch.setattr(adapter_module, "get_adapter", lambda harness: adapter)
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "multi-repair", "repair.max_attempts": 3},
    )
    result = run_research_ideation("A topic", cfg, env=ENV)

    assert result.status == "succeeded"
    assert adapter.repairs == 2
    assert len(result.repair_attempts) == 2
    assert result.repair_attempts[0].after_validation is not None
    assert not result.repair_attempts[0].after_validation.valid
    assert result.repair_attempts[1].after_validation is not None
    assert result.repair_attempts[1].after_validation.valid


def _write_gate_artifacts(agent_workspace: Path) -> None:
    """Write scientific-hypothesis-generator gate artifacts so the post-run hook gate clears."""
    review = (
        "---\nstatus: PASS\nunresolved_critical: 0\n"
        "preconditions:\n  no_fabricated_literature: pass\n  no_fake_results: pass\n---\nok\n"
    )
    files = {
        "notes/task_contract.md": "contract",
        "notes/run_metadata.json": "{}",
        "notes/literature_review.md": "lit",
        "notes/gap_and_questions.md": "gap",
        "notes/hypothesis.md": "hyp",
        "notes/novelty_check.md": "novelty",
        "notes/method.md": "method",
        "notes/experiment_plan.md": "exp",
        "notes/reviews/evidence_review.md": review,
        "notes/reviews/hypothesis_method_review.md": review,
        "notes/reviews/proposal_review.md": review,
    }
    for rel, content in files.items():
        path = agent_workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _valid_proposal() -> str:
    return """# Title
## Introduction
Body.
## Related Work
Body.
## Methods
Body.
## Experiments Design
Body.
## References
Body.
"""


def _proposal_without_references() -> str:
    return """# Title
## Introduction
Body.
## Related Work
Body.
## Methods
Body.
## Experiments Design
Body.
"""


def test_salvage_reads_last_codex_token_usage_from_event_log(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from research_ideation_agent.adapters.usage import usage_from_codex_result
    from research_ideation_agent.runtime import _last_codex_token_usage

    def _token_event(total: dict[str, int]) -> dict:
        return {
            "type": "thread/tokenUsage/updated",
            "message": {"event": {"payload": {"tokenUsage": {"total": total, "modelContextWindow": 258400}}}},
        }

    log = tmp_path / "adapter_events.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "codex_turn_start", "message": {}}),
                json.dumps(_token_event({"inputTokens": 100, "cachedInputTokens": 10})),
                json.dumps(_token_event({"inputTokens": 128596, "cachedInputTokens": 104320, "outputTokens": 2255})),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    token_usage = _last_codex_token_usage(log)
    assert token_usage is not None
    # The last token event wins and the cumulative total is preserved.
    assert token_usage["total"]["inputTokens"] == 128596

    # The salvaged shape feeds the normal codex usage normalizer (total + window).
    usage = usage_from_codex_result(SimpleNamespace(usage=token_usage, duration_ms=None))
    assert usage is not None
    assert usage.input_tokens == 128596
    assert usage.cached_input_tokens == 104320
    assert usage.model_context_window == 258400


def test_salvage_returns_none_without_codex_token_events(tmp_path: Path) -> None:
    from research_ideation_agent.runtime import _last_codex_token_usage

    assert _last_codex_token_usage(tmp_path / "missing.jsonl") is None

    log = tmp_path / "adapter_events.jsonl"
    log.write_text(json.dumps({"type": "codex_turn_start", "message": {}}) + "\n", encoding="utf-8")
    assert _last_codex_token_usage(log) is None
