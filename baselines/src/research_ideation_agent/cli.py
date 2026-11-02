"""Command-line interface for the MVP runner."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml
from dotenv import load_dotenv

from research_ideation_agent import run_research_ideation
from research_ideation_agent.config import find_project_root
from research_ideation_agent.errors import ConfigError, TopicError, WorkspaceError
from research_ideation_agent.models import RunResult


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("Only the `run` command is supported.")

    try:
        # Load .env before reading any environment variable (RIA_PROJECT_ROOT,
        # ANTHROPIC_*, SILICONFLOW_*, WTS_*, GPT_*) so a single .env can
        # configure all of them. python-dotenv defaults to override=False, so
        # values already in the shell environment still win.
        load_dotenv(dotenv_path=Path.cwd() / ".env")
        env_override = args.project_root if args.project_root is not None else _env_project_root()
        project_root = find_project_root(args.config, env_override)
        topic = _read_topic(args, project_root)
        if not topic.strip():
            raise TopicError("Topic must be a non-empty string.")
        overrides = _model_profile_overrides(args.model_profile, args.model_profiles_file, project_root)
        overrides.update(_ordered_overrides(argv))
        result = run_research_ideation(
            topic, args.config, overrides=overrides, project_root=project_root
        )
    except (TopicError, ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(_json_summary(result), indent=2))
    elif not args.quiet:
        print(_human_summary(result))
    if result.error:
        print(result.error, file=sys.stderr)
    return _exit_code(result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-ideation-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    topic = run.add_mutually_exclusive_group(required=True)
    topic.add_argument("--topic")
    topic.add_argument("--topic-file", type=Path)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--set", dest="sets", action="append", default=[])
    run.add_argument("--run-id")
    run.add_argument("--workspace-root")
    run.add_argument("--harness", choices=["claude", "codex"])
    run.add_argument("--assets-version")
    run.add_argument("--cutoff-date")
    run.add_argument("--model-profile")
    run.add_argument("--model-profiles-file", type=Path, default=Path("configs/model-profiles.yaml"))
    run.add_argument("--project-root", type=Path, default=None)
    run.add_argument("--json", action="store_true")
    run.add_argument("--quiet", action="store_true")
    return parser


def _env_project_root() -> Path | None:
    value = os.environ.get("RIA_PROJECT_ROOT")
    return Path(value) if value else None


def _read_topic(args: argparse.Namespace, project_root: Path) -> str:
    if args.topic is not None:
        return args.topic.strip()
    topic_path = args.topic_file
    if not topic_path.is_absolute():
        topic_path = (project_root / topic_path).resolve()
    try:
        return topic_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TopicError(f"Could not read topic file: {topic_path}") from exc


_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _model_profile_overrides(
    profile_name: str | None,
    profiles_file: Path,
    project_root: Path,
) -> dict[str, object]:
    if not profile_name:
        return {}

    path = profiles_file.expanduser()
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        raise ConfigError(f"Model profiles file does not exist: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in model profiles file: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read model profiles file: {path}") from exc

    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        raise ConfigError(f"Model profiles file must contain a `profiles` mapping: {path}")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        available = ", ".join(sorted(str(name) for name in profiles)) or "<none>"
        raise ConfigError(f"Unknown model profile: {profile_name}. Available profiles: {available}")

    harness = _profile_str(profile, "harness", profile_name)
    if harness not in {"claude", "codex"}:
        raise ConfigError(f"Model profile {profile_name!r} has invalid harness: {harness!r}")
    model = _profile_str(profile, "model", profile_name)
    api_key_env = _profile_env_var(profile, "api_key_env", profile_name)
    base_url_env = _profile_env_var(profile, "base_url_env", profile_name)

    overrides: dict[str, object] = {
        "harness": harness,
        "model": model,
        "model_profile": profile_name,
        f"adapters.{harness}.api_key_env": api_key_env,
        f"adapters.{harness}.base_url_env": base_url_env,
    }
    if "effort" in profile:
        overrides["effort"] = _profile_str(profile, "effort", profile_name)
    return overrides


def _profile_str(profile: dict[str, Any], key: str, profile_name: str) -> str:
    value = profile.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Model profile {profile_name!r} must define a non-empty `{key}` string.")
    return value.strip()


def _profile_env_var(profile: dict[str, Any], key: str, profile_name: str) -> str:
    value = _profile_str(profile, key, profile_name)
    if not _ENV_VAR_RE.match(value):
        raise ConfigError(
            f"Model profile {profile_name!r} field `{key}` must be an environment variable name, got {value!r}."
        )
    return value


def _ordered_overrides(argv: list[str]) -> dict[str, object]:
    overrides: dict[str, object] = {}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--set":
            index += 1
            if index >= len(argv):
                raise ConfigError("--set requires KEY=VALUE.")
            key, value = _parse_set(argv[index])
            overrides[key] = value
        elif arg.startswith("--set="):
            key, value = _parse_set(arg.split("=", 1)[1])
            overrides[key] = value
        elif arg == "--run-id":
            index += 1
            if index >= len(argv):
                raise ConfigError("--run-id requires a value.")
            overrides["workspace.run_id"] = argv[index]
        elif arg.startswith("--run-id="):
            overrides["workspace.run_id"] = arg.split("=", 1)[1]
        elif arg == "--workspace-root":
            index += 1
            if index >= len(argv):
                raise ConfigError("--workspace-root requires a value.")
            overrides["workspace.root"] = argv[index]
        elif arg.startswith("--workspace-root="):
            overrides["workspace.root"] = arg.split("=", 1)[1]
        elif arg == "--harness":
            index += 1
            if index >= len(argv):
                raise ConfigError("--harness requires a value.")
            overrides["harness"] = argv[index]
        elif arg.startswith("--harness="):
            overrides["harness"] = arg.split("=", 1)[1]
        elif arg == "--assets-version":
            index += 1
            if index >= len(argv):
                raise ConfigError("--assets-version requires a value.")
            overrides["assets.version"] = argv[index]
        elif arg.startswith("--assets-version="):
            overrides["assets.version"] = arg.split("=", 1)[1]
        elif arg == "--cutoff-date":
            index += 1
            if index >= len(argv):
                raise ConfigError("--cutoff-date requires YYYY-MM-DD.")
            overrides["mcp.scholar_gateway.cutoff_date"] = _parse_cutoff_date(argv[index])
        elif arg.startswith("--cutoff-date="):
            overrides["mcp.scholar_gateway.cutoff_date"] = _parse_cutoff_date(
                arg.split("=", 1)[1]
            )
        index += 1
    return overrides


def _parse_cutoff_date(value: str) -> str:
    if not _DATE_RE.match(value):
        raise ConfigError("--cutoff-date must use YYYY-MM-DD.")
    return value


def _parse_set(value: str) -> tuple[str, object]:
    if "=" not in value:
        raise ConfigError("--set values must use KEY=VALUE.")
    key, raw = value.split("=", 1)
    if not key:
        raise ConfigError("--set keys must be non-empty.")
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed override value for {key}.") from exc
    if key == "mcp.scholar_gateway.cutoff_date":
        if parsed is None:
            return key, None
        return key, _parse_cutoff_date(str(parsed))
    return key, parsed


def _human_summary(result: RunResult) -> str:
    lines = [
        f"status: {result.status}",
        f"run_id: {result.run_id}",
        f"harness: {result.harness}",
        f"workspace: {result.workspace_path}",
        f"proposal: {result.proposal_path}",
        f"validation: {'valid' if result.validation.valid else 'invalid'}",
    ]
    if result.validation.missing:
        lines.append(f"missing: {', '.join(result.validation.missing)}")
    return "\n".join(lines)


def _json_summary(result: RunResult) -> dict[str, object]:
    return {
        "status": result.status,
        "failure_kind": result.failure_kind,
        "run_id": result.run_id,
        "harness": result.harness,
        "workspace_path": str(result.workspace_path),
        "agent_workspace_path": str(result.agent_workspace_path),
        "proposal_path": str(result.proposal_path),
        "validation": {
            "valid": result.validation.valid,
            "missing": result.validation.missing,
        },
        "repair_attempts": len(result.repair_attempts),
        "session_id": result.session_id,
        "usage": result.usage.to_dict() if result.usage is not None else None,
        "error": result.error,
    }


def _exit_code(result: RunResult) -> int:
    if result.status == "succeeded":
        return 0
    if result.failure_kind == "validation_failed":
        return 1
    if result.failure_kind in {"adapter_error", "runtime_error"}:
        return 3
    if result.failure_kind in {"timeout", "budget_exceeded", "cancelled"}:
        return 4
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
