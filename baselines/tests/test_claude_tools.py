from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_ideation_agent import load_config
from research_ideation_agent.adapters.claude import ClaudeAdapter
from research_ideation_agent.config import DEFAULT_CONFIG
from research_ideation_agent.mcp import MCPLaunchConfig
from research_ideation_agent.workspace import create_workspace


def test_claude_default_and_example_disallow_user_facing_tools() -> None:
    expected_disallowed = {"AskUserQuestion", "PushNotification", "WebFetch", "WebSearch"}

    config_disallowed = set(load_config("configs/simple/claude.yaml").adapters.claude.disallowed_tools)

    assert expected_disallowed <= config_disallowed


def test_claude_example_selects_reviewer_subagents() -> None:
    cfg = load_config("configs/scientific-hypothesis-generator/claude.yaml")

    assert cfg.subagents.selected == ["scientific-reviewer"]


def test_codex_example_selects_reviewer_subagents() -> None:
    cfg = load_config("configs/scientific-hypothesis-generator/gpt.yaml")

    assert cfg.subagents.selected == ["scientific-reviewer"]


def test_claude_adapter_seeds_general_purpose_subagent_to_inherit_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Claude Code CLI routes unnamed `Agent` / `Task` dispatch to a
    subagent registered as `general-purpose`. Without an explicit registration,
    the CLI uses an internal default model (typically a Claude alias) — which
    400s under gateways that only expose one specific model id (e.g.
    SiliconFlow exposing `Pro/zai-org/GLM-5.1`). The adapter must always seed
    `options.agents["general-purpose"]` with `model="inherit"` so subagent
    dispatch reuses the lead's configured model.
    """

    cfg = load_config(
        "configs/simple/glm.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "subagent-inherit"},
    )
    workspace = create_workspace(cfg)
    mcp = MCPLaunchConfig(
        claude_mcp_servers={},
        codex_thread_config={},
        mcp_env={},
        redacted_artifacts={},
    )
    context = SimpleNamespace(
        config=cfg,
        workspace=workspace,
        startup_prompt="startup",
        mcp=mcp,
        env={"SILICONFLOW_API_KEY": "k", "SILICONFLOW_BASE_URL": "https://example/v1"},
        event_log_path=None,
        deadline=None,
    )

    captured: dict = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        from claude_agent_sdk import ResultMessage

        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="sess-x",
        )

    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    adapter = ClaudeAdapter()
    outcome = adapter.run_initial(context)  # type: ignore[arg-type]

    assert outcome.error is None
    options = captured["options"]
    agents = getattr(options, "agents", None)
    assert agents is not None
    assert "general-purpose" in agents
    assert agents["general-purpose"].model == "inherit"
    assert getattr(options, "hooks", None) is None


def test_scientific_hypothesis_generator_claude_registers_stop_artifact_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/claude.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "claude-stop-hook"},
    )
    workspace = create_workspace(cfg)
    from research_ideation_agent.prompting import package_subagents

    package_subagents(cfg, workspace)
    mcp = MCPLaunchConfig(
        claude_mcp_servers={},
        codex_thread_config={},
        mcp_env={},
        redacted_artifacts={},
    )
    context = SimpleNamespace(
        config=cfg,
        workspace=workspace,
        startup_prompt="startup",
        mcp=mcp,
        env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://example/v1"},
        event_log_path=None,
        deadline=None,
    )

    captured: dict = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        from claude_agent_sdk import ResultMessage

        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="sess-x",
        )

    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    adapter = ClaudeAdapter()
    outcome = adapter.run_initial(context)  # type: ignore[arg-type]

    assert outcome.error is None
    options = captured["options"]
    assert getattr(options, "include_hook_events", False) is True
    hooks = getattr(options, "hooks", None)
    assert hooks is not None
    matcher = hooks["Stop"][0]
    callback = matcher.hooks[0]

    result = asyncio.run(
        callback(
            {
                "cwd": str(workspace.agent_workspace_path),
                "session_id": "sess-hook",
                "hook_event_name": "Stop",
                "stop_hook_active": False,
            },
            None,
            SimpleNamespace(signal=None),
        )
    )

    assert result["decision"] == "block"
    assert "proposal.md" in result["reason"]
    assert "notes/literature_review.md" in result["reason"]


def test_claude_adapter_reports_installed_sdk_version() -> None:
    """sdk_version reads from importlib.metadata so run records carry the real
    installed claude-agent-sdk version, not None."""

    import importlib.metadata

    adapter = ClaudeAdapter()
    expected = importlib.metadata.version("claude-agent-sdk")
    assert adapter.sdk_version == expected
    assert adapter.sdk_version is not None and adapter.sdk_version[0].isdigit()


def test_claude_env_rewrites_configured_var_names_into_canonical_anthropic_keys() -> None:
    """The Claude adapter reads whichever env-var names the YAML names and
    rewrites them under the canonical `ANTHROPIC_*` keys the CLI subprocess
    expects. This is what lets one .env hold both ANTHROPIC_* and gateway-
    specific keys (SILICONFLOW_*) and switching configs picks the right one."""

    from research_ideation_agent.adapters.claude import _claude_env

    adapter_config = SimpleNamespace(api_key_env="SILICONFLOW_API_KEY", base_url_env="SILICONFLOW_BASE_URL")
    env = {"SILICONFLOW_API_KEY": "sk-test", "SILICONFLOW_BASE_URL": "https://gateway.example/v1"}

    result = _claude_env(env, adapter_config)

    assert result == {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_BASE_URL": "https://gateway.example/v1"}


def test_claude_env_raises_when_configured_env_var_is_missing() -> None:
    from research_ideation_agent.adapters.claude import _claude_env

    adapter_config = SimpleNamespace(api_key_env="SILICONFLOW_API_KEY", base_url_env="SILICONFLOW_BASE_URL")
    env = {"SILICONFLOW_BASE_URL": "https://x"}  # missing API key

    with pytest.raises(RuntimeError, match="SILICONFLOW_API_KEY"):
        _claude_env(env, adapter_config)


def test_claude_adapter_surfaces_api_error_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Reproduces an auth failure: the SDK yields an is_error ResultMessage carrying the real
    # reason, then raises. The adapter must surface that reason, not a bare "error result: success".
    from research_ideation_agent.prompting import package_subagents

    cfg = load_config(
        "configs/scientific-hypothesis-generator/claude.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "claude-api-error"},
    )
    workspace = create_workspace(cfg)
    package_subagents(cfg, workspace)
    context = SimpleNamespace(
        config=cfg,
        workspace=workspace,
        startup_prompt="startup",
        mcp=MCPLaunchConfig(claude_mcp_servers={}, codex_thread_config={}, mcp_env={}, redacted_artifacts={}),
        env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://example/v1"},
        event_log_path=None,
        deadline=None,
    )

    async def fake_query(*, prompt, options):
        from claude_agent_sdk import ResultMessage

        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="s",
            result="Failed to authenticate. API Error: 403 channel disabled",
        )
        raise Exception("Claude Code returned an error result: success")

    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    outcome = ClaudeAdapter().run_initial(context)  # type: ignore[arg-type]

    assert outcome.error is not None
    assert "Failed to authenticate" in outcome.error
    assert "403" in outcome.error


def test_claude_env_forwards_beta_controls() -> None:
    from research_ideation_agent.adapters.claude import _claude_env

    adapter_config = SimpleNamespace(api_key_env="ANTHROPIC_API_KEY", base_url_env="ANTHROPIC_BASE_URL")
    base = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://x"}

    forwarded = _claude_env({**base, "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1"}, adapter_config)
    assert forwarded["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"

    # not forwarded when unset, and unrelated env never leaks
    plain = _claude_env({**base, "SOME_OTHER_VAR": "leak"}, adapter_config)
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" not in plain
    assert "SOME_OTHER_VAR" not in plain


def test_claude_env_strips_and_validates_base_url() -> None:
    from research_ideation_agent.adapters.claude import _claude_env
    from research_ideation_agent.errors import ConfigError

    adapter_config = SimpleNamespace(api_key_env="DS_KEY", base_url_env="DS_URL")

    # Trailing whitespace / newline (a common .env copy-paste error) is stripped,
    # so it no longer becomes an unparsable "<url>\n/v1/messages" request URL.
    result = _claude_env({"DS_KEY": "sk-x\n", "DS_URL": " https://gw.example/v1\n"}, adapter_config)
    assert result["ANTHROPIC_BASE_URL"] == "https://gw.example/v1"
    assert result["ANTHROPIC_API_KEY"] == "sk-x"

    # A base URL with no scheme fails early with the offending variable named.
    with pytest.raises(ConfigError, match="DS_URL"):
        _claude_env({"DS_KEY": "sk-x", "DS_URL": "gw.example/v1"}, adapter_config)
