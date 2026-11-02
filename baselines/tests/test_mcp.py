from __future__ import annotations

import json
from pathlib import Path

import yaml

from research_ideation_agent import load_config
from research_ideation_agent.mcp import render_mcp_config
from research_ideation_agent.workspace import create_workspace


def test_mcp_config_does_not_override_uv_cache_dir(tmp_path: Path) -> None:
    cfg = load_config(
        "configs/simple/gpt.yaml",
        overrides={"workspace.root": str(tmp_path), "workspace.run_id": "mcp-cache"},
    )
    workspace = create_workspace(cfg)

    launch = render_mcp_config(
        cfg,
        workspace,
        {"WTS_API_KEY": "wts-secret", "WTS_BASE_URL": "https://wts.example"},
    )

    assert not (workspace.framework_path / "uv-cache").exists()
    assert "UV_CACHE_DIR" not in launch.claude_mcp_servers["scholar-gateway"]["env"]  # type: ignore[index]
    assert launch.codex_thread_config["web_search"] == "disabled"
    assert "env" not in launch.codex_thread_config["mcp_servers"]["scholar-gateway"]  # type: ignore[index]
    assert launch.codex_thread_config["mcp_servers"]["scholar-gateway"]["required"] is True  # type: ignore[index]
    redacted_claude = json.loads((workspace.mcp_path / "claude_mcp_config.redacted.json").read_text(encoding="utf-8"))
    redacted_codex = yaml.safe_load((workspace.mcp_path / "codex_mcp_config.redacted.yaml").read_text(encoding="utf-8"))
    assert "UV_CACHE_DIR" not in redacted_claude["mcp_servers"]["scholar-gateway"]["env"]
    assert redacted_codex["web_search"] == "disabled"
    assert "env" not in redacted_codex["mcp_servers"]["scholar-gateway"]
    assert redacted_codex["mcp_servers"]["scholar-gateway"]["required"] is True


def test_scientific_hypothesis_generator_forwards_summarizer_env(tmp_path: Path) -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "workspace.root": str(tmp_path),
            "workspace.run_id": "mcp-summarizer",
            "mcp.scholar_gateway.cutoff_date": "2026-06-09",
        },
    )
    workspace = create_workspace(cfg)

    launch = render_mcp_config(
        cfg,
        workspace,
        {
            "WTS_API_KEY": "wts-secret",
            "WTS_BASE_URL": "https://wts.example",
            "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_MODEL": "deepseek-test-model",
        },
    )

    server = launch.codex_thread_config["mcp_servers"]["scholar-gateway"]  # type: ignore[index]
    assert "--cutoff-date" in server["args"]
    assert server["args"][server["args"].index("--cutoff-date") + 1] == "2026-06-09"
    assert "--max-concurrency" in server["args"]
    assert server["args"][server["args"].index("--max-concurrency") + 1] == "20"
    assert "--extract-max-concurrency" in server["args"]
    assert server["env_vars"] == [
        "WTS_API_KEY",
        "WTS_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
    ]
    assert "summarize_paper" in server["tools"]
    assert launch.mcp_env["DEEPSEEK_API_KEY"] == "deepseek-secret"
