"""Scholar-gateway MCP config rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from research_ideation_agent.models import RunConfig
from research_ideation_agent.workspace import RunWorkspace


@dataclass(frozen=True)
class MCPLaunchConfig:
    claude_mcp_servers: dict[str, object]
    codex_thread_config: dict[str, object]
    mcp_env: dict[str, str]
    redacted_artifacts: dict[str, Path]


def render_mcp_config(config: RunConfig, workspace: RunWorkspace, env: dict[str, str]) -> MCPLaunchConfig:
    sg = config.mcp.scholar_gateway
    references_dir = workspace.agent_workspace_path / sg.references_dir
    args = _scholar_gateway_args(config, references_dir)
    forwarded_names = list(sg.required_env_vars) + [name for name in sg.optional_env_vars if env.get(name)]
    mcp_env = {name: env[name] for name in forwarded_names if name in env}

    claude_mcp_servers = {
        "scholar-gateway": {
            "type": "stdio",
            "command": sg.command,
            "args": args,
            "env": mcp_env,
        }
    }
    codex_thread_config = {
        "web_search": "disabled",
        "mcp_servers": {
            "scholar-gateway": {
                "command": sg.command,
                "args": args,
                "env_vars": forwarded_names,
                "required": True,
                "default_tools_approval_mode": sg.approval_mode,
                "tools": {
                    "web_search": {"approval_mode": sg.approval_mode},
                    "web_fetch": {"approval_mode": sg.approval_mode},
                    "extract_paper": {"approval_mode": sg.approval_mode},
                    "summarize_paper": {"approval_mode": sg.approval_mode},
                },
                "startup_timeout_sec": sg.startup_timeout_seconds,
                "tool_timeout_sec": sg.tool_timeout_seconds,
            }
        }
    }

    # Mirror the SDK input shape (`mcp_servers`) rather than the CLI wire shape
    # (`mcpServers`) so that the redacted artifact can be diffed against the live
    # config or fed back into the SDK without re-keying.
    redacted_claude = {
        "mcp_servers": {
            "scholar-gateway": {
                "type": "stdio",
                "command": sg.command,
                "args": args,
                "env": {name: "<redacted>" for name in forwarded_names},
            }
        }
    }
    redacted_codex = {
        "web_search": codex_thread_config["web_search"],
        "mcp_servers": {
            "scholar-gateway": {
                **codex_thread_config["mcp_servers"]["scholar-gateway"],
                "env_vars": forwarded_names,
            }
        }
    }
    claude_path = workspace.mcp_path / "claude_mcp_config.redacted.json"
    codex_path = workspace.mcp_path / "codex_mcp_config.redacted.yaml"
    claude_path.write_text(json.dumps(redacted_claude, indent=2) + "\n", encoding="utf-8")
    codex_path.write_text(yaml.safe_dump(redacted_codex, sort_keys=False), encoding="utf-8")
    return MCPLaunchConfig(
        claude_mcp_servers=claude_mcp_servers,
        codex_thread_config=codex_thread_config,
        mcp_env=mcp_env,
        redacted_artifacts={"mcp_claude": claude_path, "mcp_codex": codex_path},
    )


def _scholar_gateway_args(config: RunConfig, references_dir: Path) -> list[str]:
    sg = config.mcp.scholar_gateway
    args: list[str] = []
    if sg.cutoff_date:
        args.extend(["--cutoff-date", sg.cutoff_date])
    args.append("--restrict-domains" if sg.restrict_domains else "--no-restrict-domains")
    args.append("--strict-filtering" if sg.strict_filtering else "--no-strict-filtering")
    args.extend(["--mode", sg.mode, "--format", sg.format, "--references-dir", str(references_dir)])
    args.extend(["--max-concurrency", str(sg.max_concurrency), "--extract-max-concurrency", str(sg.extract_max_concurrency)])
    return args
