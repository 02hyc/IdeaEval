from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

from research_ideation_agent import load_config
from research_ideation_agent.adapters import codex as codex_module
from research_ideation_agent.adapters.codex import CodexAdapter
from research_ideation_agent.mcp import MCPLaunchConfig
from research_ideation_agent.workspace import create_workspace


ENV = {
    "WTS_API_KEY": "wts-secret",
    "GPT_API_KEY": "gpt-secret",
    "GPT_API_BASE_URL": "https://provider.example/v1",
    "OPENAI_API_KEY": "personal-openai-secret",
    "ANTHROPIC_API_KEY": "personal-anthropic-secret",
}


class FakeCodexConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeCodex:
    instances = []

    def __init__(self, config):
        self.config = config
        self.started = []
        self.resumed = []
        self.closed = False
        FakeCodex.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True

    def thread_start(self, *, cwd=None, model=None, model_provider=None, sandbox=None, approval_mode=None, developer_instructions=None, config=None):
        thread = FakeThread("thread-1")
        self.started.append(
            {
                "cwd": cwd,
                "model": model,
                "model_provider": model_provider,
                "sandbox": sandbox,
                "approval_mode": approval_mode,
                "developer_instructions": developer_instructions,
                "config": config,
                "thread": thread,
            }
        )
        return thread

    def thread_resume(self, thread_id, *, cwd=None, model=None, model_provider=None, sandbox=None, approval_mode=None, developer_instructions=None, config=None):
        thread = FakeThread(thread_id)
        self.resumed.append(
            {
                "thread_id": thread_id,
                "cwd": cwd,
                "model": model,
                "model_provider": model_provider,
                "sandbox": sandbox,
                "approval_mode": approval_mode,
                "developer_instructions": developer_instructions,
                "config": config,
                "thread": thread,
            }
        )
        return thread


class FakeThread:
    def __init__(self, thread_id: str):
        self.id = thread_id
        self.runs = []

    turn_handle_cls = None

    def turn(self, input, *, approval_mode=None, cwd=None, effort=None, model=None, sandbox=None):
        self.runs.append(
            {
                "input": input,
                "approval_mode": approval_mode,
                "cwd": cwd,
                "effort": effort,
                "model": model,
                "sandbox": sandbox,
            }
        )
        handle_cls = self.turn_handle_cls or FakeTurnHandle
        return handle_cls(f"turn-{len(self.runs)}")

    def run(self, input, *, approval_mode=None, cwd=None, effort=None, model=None, sandbox=None):
        return self.turn(input, approval_mode=approval_mode, cwd=cwd, effort=effort, model=model, sandbox=sandbox).run()


class FakeTurnHandle:
    after_first_event = None

    def __init__(self, turn_id: str):
        self.id = turn_id

    def stream(self):
        yield FakeNotification(
            "item/completed",
            ItemCompletedNotification(
                turn_id=self.id,
                item=AgentMessageThreadItem(text="done", phase=SimpleNamespace(value="final_answer")),
            ),
        )
        if FakeTurnHandle.after_first_event is not None:
            FakeTurnHandle.after_first_event()
        yield FakeNotification(
            "thread/token_usage/updated",
            ThreadTokenUsageUpdatedNotification(
                turn_id=self.id,
                token_usage={
                    "total": {
                        "inputTokens": 11,
                        "cachedInputTokens": 3,
                        "outputTokens": 7,
                        "reasoningOutputTokens": 5,
                    }
                },
            ),
        )
        yield FakeNotification(
            "turn/completed",
            TurnCompletedNotification(
                turn=SimpleNamespace(
                    id=self.id,
                    status=SimpleNamespace(value="completed"),
                    error=None,
                    started_at=1000,
                    completed_at=1123,
                    duration_ms=123,
                )
            ),
        )

    def run(self):
        return SimpleNamespace(
            id=self.id,
            status=SimpleNamespace(value="completed"),
            error=None,
            final_response="done",
            items=[],
            usage={
                "total": {
                    "inputTokens": 11,
                    "cachedInputTokens": 3,
                    "outputTokens": 7,
                    "reasoningOutputTokens": 5,
                }
            },
            duration_ms=123,
        )


class FakeNotification:
    def __init__(self, method, payload):
        self.method = method
        self.payload = payload


class ItemCompletedNotification:
    def __init__(self, turn_id, item):
        self.turn_id = turn_id
        self.item = item


class ThreadTokenUsageUpdatedNotification:
    def __init__(self, turn_id, token_usage):
        self.turn_id = turn_id
        self.token_usage = token_usage


class TurnCompletedNotification:
    def __init__(self, turn):
        self.turn = turn


class ErrorNotification:
    def __init__(self, message):
        self.message = message


class AgentMessageThreadItem:
    def __init__(self, text, phase):
        self.text = text
        self.phase = phase


class TurnResult:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_codex_initial_run_maps_sdk_arguments_and_logs_result(tmp_path: Path, monkeypatch) -> None:
    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    context = _context(tmp_path, overrides={"hooks.stop": [], "effort": "max"})
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert outcome.succeeded
    assert outcome.session_id == "thread-1"
    assert outcome.usage is not None
    assert outcome.usage.input_tokens == 11
    assert outcome.usage.cached_input_tokens == 3
    assert outcome.usage.output_tokens == 7
    assert outcome.usage.reasoning_output_tokens == 5
    assert outcome.usage.total_tokens == 18
    assert outcome.usage.turns == 1
    assert outcome.usage.duration_ms == 123

    codex = FakeCodex.instances[0]
    config_kwargs = codex.config.kwargs
    assert config_kwargs["cwd"] == str(context.workspace.agent_workspace_path)
    assert config_kwargs["client_name"] == "research_ideation_agent"
    assert config_kwargs["client_title"] == "Research Ideation Agent"
    assert "codex_bin" not in config_kwargs
    assert config_kwargs["env"]["GPT_API_KEY"] == "gpt-secret"
    assert config_kwargs["env"]["WTS_API_KEY"] == "wts-secret"
    assert config_kwargs["env"]["OPENAI_API_KEY"] == ""
    assert config_kwargs["env"]["ANTHROPIC_API_KEY"] == ""
    assert "personal-openai-secret" not in json.dumps(config_kwargs["env"])
    assert "personal-anthropic-secret" not in json.dumps(config_kwargs["env"])
    assert config_kwargs["env"]["CODEX_HOME"].endswith("/framework/codex-home")
    assert "model_provider=\"gpt-env\"" in config_kwargs["config_overrides"]
    assert "model_providers.gpt-env.base_url=\"https://provider.example/v1\"" in config_kwargs["config_overrides"]
    assert not any(item.startswith("projects.") for item in config_kwargs["config_overrides"])
    assert "project_root_markers=[\"AGENTS.md\"]" not in config_kwargs["config_overrides"]
    assert "sandbox_workspace_write.network_access=true" in config_kwargs["config_overrides"]
    assert "features.browser_use=false" in config_kwargs["config_overrides"]
    assert "features.image_generation=false" in config_kwargs["config_overrides"]
    assert "features.multi_agent=false" not in config_kwargs["config_overrides"]
    codex_home_config = Path(config_kwargs["env"]["CODEX_HOME"]) / "config.toml"
    codex_home_data = tomllib.loads(codex_home_config.read_text(encoding="utf-8"))
    assert codex_home_data["project_root_markers"] == ["AGENTS.md"]
    assert codex_home_data["model_reasoning_summary"] == "auto"
    assert codex_home_data["model_supports_reasoning_summaries"] is True
    assert codex_home_data["projects"][str(context.workspace.agent_workspace_path)]["trust_level"] == "trusted"
    assert "hooks" not in codex_home_data

    [start] = codex.started
    assert start["cwd"] == str(context.workspace.agent_workspace_path)
    assert start["model"] == context.config.model
    assert start["model_provider"] == "gpt-env"
    assert start["sandbox"] == "sandbox-workspace"
    assert start["approval_mode"] == "approval-auto"
    assert start["developer_instructions"] == "startup prompt"
    assert start["config"]["mcp_servers"]["scholar-gateway"]["env_vars"] == ["WTS_API_KEY"]
    assert start["config"]["sandbox_workspace_write"]["network_access"] is True

    [run] = start["thread"].runs
    assert run["input"] == codex_module._INITIAL_USER_INPUT
    assert run["cwd"] == str(context.workspace.agent_workspace_path)
    assert run["model"] == context.config.model
    assert run["effort"] == "effort-xhigh"
    assert run["approval_mode"] == "approval-auto"
    assert run["sandbox"] == "sandbox-workspace"

    events = _read_jsonl(context.event_log_path)
    assert [event["event_index"] for event in events] == [1, 2, 3, 4, 5]
    assert [event["type"] for event in events] == [
        "codex_turn_start",
        "item/completed",
        "thread/token_usage/updated",
        "turn/completed",
        "TurnResult",
    ]
    assert events[0]["phase"] == "initial"
    assert events[1]["message"]["event"]["payload"]["item"]["text"] == "done"
    assert events[-1]["message"]["final_response"] == "done"

    adapter.close()
    assert codex.closed is True


def test_codex_events_are_written_while_turn_stream_is_running(tmp_path: Path, monkeypatch) -> None:
    FakeCodex.instances = []
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    context = _context(tmp_path)
    observed = {"value": False}

    def after_first_event() -> None:
        events = _read_jsonl(context.event_log_path)
        observed["value"] = any(event["type"] == "item/completed" for event in events)

    FakeTurnHandle.after_first_event = after_first_event
    try:
        adapter = CodexAdapter()
        outcome = adapter.run_initial(context)
    finally:
        FakeTurnHandle.after_first_event = None

    assert outcome.succeeded
    assert observed["value"] is True


def test_codex_uses_codex_bin_only_when_explicitly_configured(tmp_path: Path, monkeypatch) -> None:
    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    context = _context(tmp_path)
    context.env["CODEX_BIN"] = "/opt/codex"
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert outcome.succeeded
    codex = FakeCodex.instances[0]
    assert codex.config.kwargs["codex_bin"] == "/opt/codex"


def test_codex_env_names_are_configurable_and_personal_openai_is_cleared(tmp_path: Path, monkeypatch) -> None:
    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    context = _context(
        tmp_path,
        overrides={
            "adapters.codex.api_key_env": "ALT_MODEL_API_KEY",
            "adapters.codex.base_url_env": "ALT_MODEL_BASE_URL",
        },
        env={
            **ENV,
            "GPT_API_KEY": "unused-gpt-secret",
            "GPT_API_BASE_URL": "https://unused-gpt.example/v1",
            "ALT_MODEL_API_KEY": "alt-secret",
            "ALT_MODEL_BASE_URL": "https://alt-provider.example/v1",
            "OPENAI_API_KEY": "personal-openai-secret",
            "OPENAI_BASE_URL": "https://personal-openai.example/v1",
        },
    )
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert outcome.succeeded
    codex = FakeCodex.instances[0]
    config_kwargs = codex.config.kwargs
    assert config_kwargs["env"]["ALT_MODEL_API_KEY"] == "alt-secret"
    assert config_kwargs["env"]["OPENAI_API_KEY"] == ""
    assert config_kwargs["env"]["OPENAI_BASE_URL"] == ""
    serialized_env = json.dumps(config_kwargs["env"])
    assert "unused-gpt-secret" not in serialized_env
    assert "personal-openai-secret" not in serialized_env
    assert "https://personal-openai.example/v1" not in serialized_env
    assert "model_providers.gpt-env.base_url=\"https://alt-provider.example/v1\"" in config_kwargs["config_overrides"]
    assert "model_providers.gpt-env.env_key=\"ALT_MODEL_API_KEY\"" in config_kwargs["config_overrides"]


def test_codex_disables_generic_multi_agent_features_when_no_subagents_are_selected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    context = _context(tmp_path, overrides={"subagents.selected": []})
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert outcome.succeeded
    config_kwargs = FakeCodex.instances[0].config.kwargs
    assert "features.multi_agent=false" in config_kwargs["config_overrides"]
    assert "features.multi_agent_v2=false" in config_kwargs["config_overrides"]
    assert "features.enable_fanout=false" in config_kwargs["config_overrides"]


def test_scientific_hypothesis_generator_codex_writes_stop_hook_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "hypothesis-stop-hook"},
    )
    workspace = create_workspace(cfg)
    mcp = MCPLaunchConfig(
        claude_mcp_servers={},
        codex_thread_config={"mcp_servers": {"scholar-gateway": {"env_vars": ["WTS_API_KEY"]}}},
        mcp_env={"WTS_API_KEY": "wts-secret"},
        redacted_artifacts={},
    )
    context = SimpleNamespace(
        config=cfg,
        workspace=workspace,
        startup_prompt="startup prompt",
        mcp=mcp,
        env=dict(ENV),
        event_log_path=workspace.logs_path / "adapter_events.jsonl",
    )
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert outcome.succeeded
    config_kwargs = FakeCodex.instances[0].config.kwargs
    codex_home_config = Path(config_kwargs["env"]["CODEX_HOME"]) / "config.toml"
    codex_home_data = tomllib.loads(codex_home_config.read_text(encoding="utf-8"))
    [stop_hook_group] = codex_home_data["hooks"]["Stop"]
    [stop_hook] = stop_hook_group["hooks"]
    assert stop_hook["type"] == "command"
    assert stop_hook["timeout"] == 30
    assert stop_hook["statusMessage"] == "Checking hypothesis artifacts"
    assert "stop_artifact_gate.py" in stop_hook["command"]
    assert stop_hook["command"].startswith("python3 ")


def test_codex_disables_skills_that_are_not_selected(tmp_path: Path, monkeypatch) -> None:
    class SkillClient:
        def __init__(self):
            self.requests = []

        def _request_raw(self, method, params=None):
            self.requests.append((method, params))
            if method == "skills/list":
                return {
                    "data": [
                        {
                            "skills": [
                                {"name": "literature-review", "enabled": True, "path": "/repo/.agents/skills/literature-review/SKILL.md"},
                                {"name": "imagegen", "enabled": True, "path": "/home/.codex/skills/.system/imagegen/SKILL.md"},
                                {"name": "skill-installer", "enabled": False, "path": "/home/.codex/skills/.system/skill-installer/SKILL.md"},
                            ]
                        }
                    ]
                }
            if method == "skills/config/write":
                return {"effectiveEnabled": params["enabled"]}
            raise AssertionError(method)

    class SkillCodex(FakeCodex):
        def __init__(self, config):
            super().__init__(config)
            self._client = SkillClient()

    def fake_sdk():
        sdk = _fake_sdk()
        sdk["Codex"] = SkillCodex
        return sdk

    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    monkeypatch.setattr(codex_module, "_import_codex_sdk", fake_sdk)
    context = _context(tmp_path)
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert outcome.succeeded
    codex = FakeCodex.instances[0]
    assert ("skills/list", {"cwds": [str(context.workspace.agent_workspace_path)], "forceReload": True}) in codex._client.requests
    assert (
        "skills/config/write",
        {"path": "/home/.codex/skills/.system/imagegen/SKILL.md", "enabled": False},
    ) in codex._client.requests
    assert not any(
        request == ("skills/config/write", {"path": "/repo/.agents/skills/literature-review/SKILL.md", "enabled": False})
        for request in codex._client.requests
    )


def test_codex_enter_failure_does_not_leave_client_for_close(tmp_path: Path, monkeypatch) -> None:
    class EnterFailCodex(FakeCodex):
        def __enter__(self):
            raise RuntimeError("start failed")

    def fake_sdk():
        sdk = _fake_sdk()
        sdk["Codex"] = EnterFailCodex
        return sdk

    FakeCodex.instances = []
    monkeypatch.setattr(codex_module, "_import_codex_sdk", fake_sdk)
    context = _context(tmp_path)
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)
    adapter.close()

    assert not outcome.succeeded
    assert "start failed" in (outcome.error or "")
    assert FakeCodex.instances[0].closed is False


def test_codex_missing_turn_completed_includes_problem_event(tmp_path: Path, monkeypatch) -> None:
    class NoCompletionTurnHandle(FakeTurnHandle):
        def stream(self):
            yield FakeNotification("error", ErrorNotification("transport closed"))

    class NoCompletionCodex(FakeCodex):
        def thread_start(self, **kwargs):
            thread = super().thread_start(**kwargs)
            thread.turn_handle_cls = NoCompletionTurnHandle
            return thread

    def fake_sdk():
        sdk = _fake_sdk()
        sdk["Codex"] = NoCompletionCodex
        return sdk

    FakeCodex.instances = []
    monkeypatch.setattr(codex_module, "_import_codex_sdk", fake_sdk)
    context = _context(tmp_path)
    adapter = CodexAdapter()

    outcome = adapter.run_initial(context)

    assert not outcome.succeeded
    assert "Codex turn completed event not received" in (outcome.error or "")
    assert "transport closed" in (outcome.error or "")


def test_codex_repair_continues_existing_thread(tmp_path: Path, monkeypatch) -> None:
    FakeCodex.instances = []
    FakeTurnHandle.after_first_event = None
    monkeypatch.setattr(codex_module, "_import_codex_sdk", _fake_sdk)
    context = _context(tmp_path)
    adapter = CodexAdapter()

    initial = adapter.run_initial(context)
    repair = adapter.run_repair(context, "repair prompt", initial.session_id)

    assert repair.succeeded
    assert repair.continued_session is True
    codex = FakeCodex.instances[0]
    assert codex.resumed == []
    thread = codex.started[0]["thread"]
    assert codex.started[0]["developer_instructions"] == "startup prompt"
    assert [run["input"] for run in thread.runs] == [codex_module._INITIAL_USER_INPUT, "repair prompt"]
    events = _read_jsonl(context.event_log_path)
    assert [event["phase"] for event in events if event["type"] == "TurnResult"] == ["initial", "repair-resume"]


def test_codex_effort_accepts_gpt_names_and_legacy_aliases() -> None:
    sdk = {"ReasoningEffort": SimpleNamespace(minimal="effort-minimal", high="effort-high", xhigh="effort-xhigh")}

    cases = {
        "minimal": "effort-minimal",
        "min": "effort-minimal",
        "high": "effort-high",
        "xhigh": "effort-xhigh",
        "max": "effort-xhigh",
    }
    for configured, expected in cases.items():
        context = SimpleNamespace(config=SimpleNamespace(effort=configured))
        assert codex_module._effort(sdk, context) == expected


def _fake_sdk() -> dict[str, object]:
    return {
        "ApprovalMode": SimpleNamespace(auto_review="approval-auto"),
        "Codex": FakeCodex,
        "CodexConfig": FakeCodexConfig,
        "ErrorNotification": ErrorNotification,
        "ItemCompletedNotification": ItemCompletedNotification,
        "ReasoningEffort": SimpleNamespace(high="effort-high", xhigh="effort-xhigh"),
        "Sandbox": SimpleNamespace(workspace_write="sandbox-workspace"),
        "ThreadTokenUsageUpdatedNotification": ThreadTokenUsageUpdatedNotification,
        "TurnCompletedNotification": TurnCompletedNotification,
        "TurnResult": TurnResult,
    }


def _context(tmp_path: Path, *, overrides: dict[str, object] | None = None, env: dict[str, str] | None = None):
    config_overrides = {"workspace.root": str(tmp_path), "workspace.run_id": "codex-adapter"}
    if overrides:
        config_overrides.update(overrides)
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides=config_overrides,
    )
    workspace = create_workspace(cfg)
    mcp = MCPLaunchConfig(
        claude_mcp_servers={},
        codex_thread_config={"mcp_servers": {"scholar-gateway": {"env_vars": ["WTS_API_KEY"]}}},
        mcp_env={"WTS_API_KEY": "wts-secret"},
        redacted_artifacts={},
    )
    return SimpleNamespace(
        config=cfg,
        workspace=workspace,
        startup_prompt="startup prompt",
        mcp=mcp,
        env=dict(env or ENV),
        event_log_path=workspace.logs_path / "adapter_events.jsonl",
    )


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
