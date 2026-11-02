"""Configuration loading and validation."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from research_ideation_agent.constants import (
    DEFAULT_CODEX_API_KEY_ENV,
    DEFAULT_CODEX_BASE_URL_ENV,
    DEFAULT_CODEX_DISABLED_FEATURES,
    KNOWN_CLAUDE_TOOL_NAMES,
)
from research_ideation_agent.errors import ConfigError
from research_ideation_agent.models import (
    AdaptersConfig,
    AssetsConfig,
    BudgetsConfig,
    ClaudeAdapterConfig,
    CodexAdapterConfig,
    HooksConfig,
    LoggingConfig,
    MCPConfig,
    ModelPricingConfig,
    PermissionsConfig,
    PricingConfig,
    RepairConfig,
    RunConfig,
    ScholarGatewayConfig,
    SkillsConfig,
    StopHookSpec,
    SubagentsConfig,
    WorkspaceConfig,
)


_ANY_DICT = object()
_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|credential|base[_-]?url)", re.IGNORECASE)
_PRICING_FIELD_NAMES = {
    "input_per_million_usd",
    "cached_input_per_million_usd",
    "cache_creation_input_per_million_usd",
    "output_per_million_usd",
    "reasoning_output_per_million_usd",
    "input_includes_cached_tokens",
    "long_context_threshold_tokens",
    "long_context_input_per_million_usd",
    "long_context_cached_input_per_million_usd",
    "long_context_output_per_million_usd",
    "long_context_reasoning_output_per_million_usd",
}


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "harness": None,
    "model": None,
    "model_profile": None,
    "effort": "max",
    "workspace": {
        "root": ".runs",
        "run_id": None,
        "agent_dir": "agent_workspace",
        "clean_existing": False,
    },
    "assets": {
        "root": None,
        "version": None,
        "prompt_template": None,
        "repair_prompt_template": None,
        "skills_dir": None,
        "subagents_dir": None,
    },
    "mcp": {
        "profile": "scholar-gateway",
        "scholar_gateway": {
            "command": "scholar-gateway-mcp",
            "cutoff_date": None,
            "restrict_domains": True,
            "strict_filtering": True,
            "mode": "rules",
            "format": "markdown",
            "references_dir": "references",
            "required_env_vars": ["WTS_API_KEY"],
            "optional_env_vars": ["WTS_BASE_URL"],
            "startup_timeout_seconds": 60,
            "tool_timeout_seconds": 600,
            "approval_mode": "approve",
            "max_concurrency": 20,
            "extract_max_concurrency": 20,
        },
    },
    "permissions": {
        "approval_policy": "auto",
    },
    "budgets": {"timeout_seconds": 3600, "max_turns": None, "max_cost_usd": None},
    "pricing": {"models": {}},
    "repair": {"strategy": "external-loop", "max_attempts": 1},
    "skills": {"selected": []},
    "subagents": {"selected": []},
    "hooks": {"stop": []},
    "logging": {
        "write_run_record": True,
        "retain_event_log": True,
    },
    "adapters": {
        "claude": {
            "strict_mcp_config": True,
            "api_key_env": None,
            "base_url_env": None,
            "disallowed_tools": [],
            "setting_sources": ["project"],
            "sdk_options": {},
        },
        "codex": {
            "api_key_env": DEFAULT_CODEX_API_KEY_ENV,
            "base_url_env": DEFAULT_CODEX_BASE_URL_ENV,
            "sandbox": "workspace_write",
            "approval_mode": "auto_review",
            "network_access_enabled": True,
            "cleanup_codex_home": True,
            "reasoning_summary": "auto",
            "supports_reasoning_summaries": True,
            "disabled_features": copy.deepcopy(DEFAULT_CODEX_DISABLED_FEATURES),
        },
    },
}


SCHEMA: dict[str, Any] = {
    "version": True,
    "harness": True,
    "model": True,
    "model_profile": True,
    "effort": True,
    "workspace": {"root": True, "run_id": True, "agent_dir": True, "clean_existing": True},
    "assets": {
        "root": True,
        "version": True,
        "prompt_template": True,
        "repair_prompt_template": True,
        "skills_dir": True,
        "subagents_dir": True,
    },
    "mcp": {
        "profile": True,
        "scholar_gateway": {
            "command": True,
            "cutoff_date": True,
            "restrict_domains": True,
            "strict_filtering": True,
            "mode": True,
            "format": True,
            "references_dir": True,
            "required_env_vars": True,
            "optional_env_vars": True,
            "startup_timeout_seconds": True,
            "tool_timeout_seconds": True,
            "approval_mode": True,
            "max_concurrency": True,
            "extract_max_concurrency": True,
        },
    },
    "permissions": {"approval_policy": True},
    "budgets": {"timeout_seconds": True, "max_turns": True, "max_cost_usd": True},
    "pricing": {"models": _ANY_DICT},
    "repair": {"strategy": True, "max_attempts": True},
    "skills": {"selected": True},
    "subagents": {"selected": True},
    "hooks": {"stop": True},
    "logging": {"write_run_record": True, "retain_event_log": True},
    "adapters": {
        "claude": {
            "strict_mcp_config": True,
            "api_key_env": True,
            "base_url_env": True,
            "disallowed_tools": True,
            "setting_sources": True,
            "sdk_options": _ANY_DICT,
        },
        "codex": {
            "api_key_env": True,
            "base_url_env": True,
            "sandbox": True,
            "approval_mode": True,
            "network_access_enabled": True,
            "cleanup_codex_home": True,
            "reasoning_summary": True,
            "supports_reasoning_summaries": True,
            "disabled_features": True,
        },
    },
}


def load_config(
    path: str | Path,
    *,
    overrides: Mapping[str, object] | None = None,
    project_root: Path | None = None,
) -> RunConfig:
    """Load, resolve, and validate an MVP run config.

    Relative paths in the YAML and in `--workspace-root`-style overrides
    resolve against the project root: the directory containing
    `pyproject.toml` discovered by walking upward from the config file's
    parent directory. Pass `project_root=` (or set `RIA_PROJECT_ROOT` in
    the environment) to override discovery — required when the config
    file does not live inside a Python project tree.
    """

    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in config file: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read config file: {config_path}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping.")

    _validate_unknown_fields(raw, SCHEMA)
    _reject_secret_values(raw)

    merged = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), raw)
    if overrides:
        for dotted_key, value in overrides.items():
            if not isinstance(dotted_key, str) or not dotted_key:
                raise ConfigError("Override keys must be non-empty dotted strings.")
            _reject_secret_override(dotted_key, value)
            _set_dotted_value(merged, dotted_key, value)

    override_root = project_root if project_root is not None else _project_root_from_env()
    base_dir = find_project_root(config_path, override_root)
    resolved = _resolve_paths(merged, base_dir)
    _resolve_hook_scripts(resolved, base_dir)
    return _build_config(resolved)


def find_project_root(config_path: Path, override: Path | None = None) -> Path:
    """Resolve the directory all relative config paths are anchored at.

    Public so the CLI can mirror the same anchor for `--topic-file` etc.
    """

    if override is not None:
        override_path = Path(override).expanduser()
        if not override_path.is_dir():
            raise ConfigError(
                f"Project root override does not exist or is not a directory: {override_path}"
            )
        return override_path.resolve()
    start = config_path.resolve().parent
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise ConfigError(
        f"Could not locate project root (no pyproject.toml ancestor of {config_path}). "
        "Set the RIA_PROJECT_ROOT environment variable or pass --project-root on the CLI "
        "(or load_config(project_root=...) in the Python API)."
    )


def _project_root_from_env() -> Path | None:
    import os

    value = os.environ.get("RIA_PROJECT_ROOT")
    if not value:
        return None
    return Path(value)


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _validate_unknown_fields(value: Any, schema: Any, path: str = "") -> None:
    if schema is _ANY_DICT:
        if value is not None and not isinstance(value, dict):
            raise ConfigError(f"Config field {path} must be a mapping.")
        return
    if schema is True:
        return
    if not isinstance(value, dict):
        raise ConfigError(f"Config field {path or '<root>'} must be a mapping.")
    for key, child in value.items():
        if key not in schema:
            dotted = f"{path}.{key}" if path else str(key)
            raise ConfigError(f"Unknown config field: {dotted}")
        child_path = f"{path}.{key}" if path else str(key)
        _validate_unknown_fields(child, schema[key], child_path)


def _reject_secret_values(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            dotted = f"{path}.{key}" if path else str(key)
            # Allow leaf names that hold env-var *names* (not values) — they
            # always end in `_env`, `_env_vars`, `required_env_vars`, or
            # `optional_env_vars`.
            allowed_suffix = dotted.endswith(("_env", "_env_vars"))
            allowed_pricing_field = dotted.startswith("pricing.models.") and str(key) in _PRICING_FIELD_NAMES
            if _SENSITIVE_KEY_RE.search(str(key)) and not (allowed_suffix or allowed_pricing_field):
                raise ConfigError(f"Config must not contain secret value fields: {dotted}")
            _reject_secret_values(child, dotted)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_values(item, f"{path}[{index}]")


def _reject_secret_override(dotted_key: str, value: object) -> None:
    parts = dotted_key.split(".")
    leaf = parts[-1]
    leaf_is_env_name = leaf.endswith("_env") or leaf.endswith("_env_vars")
    leaf_is_pricing_field = dotted_key.startswith("pricing.models.") and leaf in _PRICING_FIELD_NAMES
    sensitive_part = next((part for part in parts if _SENSITIVE_KEY_RE.search(part)), None)
    if sensitive_part is not None and not (
        (sensitive_part == leaf and leaf_is_env_name) or leaf_is_pricing_field
    ):
        raise ConfigError(f"Overrides must not provide secret value fields: {dotted_key}")
    _reject_secret_values(value, dotted_key)


def _set_dotted_value(config: dict[str, Any], dotted_key: str, value: object) -> None:
    parts = dotted_key.split(".")
    schema: Any = SCHEMA
    current: Any = config
    for part in parts[:-1]:
        if schema is _ANY_DICT:
            if not isinstance(current, dict):
                raise ConfigError(f"Invalid override path: {dotted_key}")
            current = current.setdefault(part, {})
            continue
        if not isinstance(schema, dict) or part not in schema:
            raise ConfigError(f"Invalid override path: {dotted_key}")
        schema = schema[part]
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"Invalid override path: {dotted_key}")
        current = current[part]
    leaf = parts[-1]
    if schema is not _ANY_DICT:
        if not isinstance(schema, dict) or leaf not in schema:
            raise ConfigError(f"Invalid override path: {dotted_key}")
    if not isinstance(current, dict):
        raise ConfigError(f"Invalid override path: {dotted_key}")
    current[leaf] = value


def _resolve_paths(data: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved = copy.deepcopy(data)
    resolved["workspace"]["root"] = _resolve_host_path(resolved["workspace"]["root"], base_dir)
    assets = resolved["assets"]
    if assets.get("root") is not None:
        assets["root"] = _resolve_host_path(assets["root"], base_dir)
    if assets.get("version") is not None:
        _resolve_versioned_assets(assets)
    else:
        for key in ("prompt_template", "repair_prompt_template", "skills_dir", "subagents_dir"):
            value = assets.get(key)
            if value is not None:
                assets[key] = _resolve_host_path(value, base_dir)
    return resolved


def _resolve_versioned_assets(assets: dict[str, Any]) -> None:
    version = _safe_relative_name(assets["version"], "assets.version")
    root = assets.get("root")
    if root is None:
        raise ConfigError("assets.version requires assets.root.")
    if not isinstance(root, Path):
        raise ConfigError("assets.root must be a path.")

    version_dir = root / version
    assets["version"] = version
    assets["prompt_template"] = version_dir / "prompts" / "startup.md"
    assets["repair_prompt_template"] = version_dir / "prompts" / "repair.md"
    assets["skills_dir"] = version_dir / "skills"
    subagents_dir = version_dir / "subagents"
    assets["subagents_dir"] = subagents_dir if subagents_dir.exists() else None


def _resolve_hook_scripts(data: dict[str, Any], base_dir: Path) -> None:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    stop = hooks.get("stop") or []
    assets = data.get("assets", {})
    version = assets.get("version")
    root = assets.get("root")
    if version and isinstance(root, Path):
        anchor = root / _safe_relative_name(version, "assets.version")
    else:
        anchor = base_dir
    for entry in stop:
        if isinstance(entry, dict) and entry.get("script") is not None:
            script = Path(str(entry["script"])).expanduser()
            entry["script"] = script if script.is_absolute() else (anchor / script).resolve()


def _resolve_host_path(value: object, base_dir: Path) -> Path:
    if value is None:
        raise ConfigError("Required path field is missing.")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _build_config(data: dict[str, Any]) -> RunConfig:
    _validate_required(data)
    workspace = WorkspaceConfig(
        root=_expect_path(data["workspace"]["root"], "workspace.root"),
        run_id=_optional_str(data["workspace"]["run_id"], "workspace.run_id"),
        agent_dir=_safe_relative_name(data["workspace"]["agent_dir"], "workspace.agent_dir"),
        clean_existing=_expect_bool(data["workspace"]["clean_existing"], "workspace.clean_existing"),
    )
    assets = AssetsConfig(
        prompt_template=_existing_file(data["assets"]["prompt_template"], "assets.prompt_template"),
        repair_prompt_template=_existing_file(data["assets"]["repair_prompt_template"], "assets.repair_prompt_template"),
        skills_dir=_existing_dir(data["assets"]["skills_dir"], "assets.skills_dir"),
        subagents_dir=_existing_dir_or_none(data["assets"]["subagents_dir"], "assets.subagents_dir"),
        root=_existing_dir_or_none(data["assets"]["root"], "assets.root"),
        version=_optional_safe_relative_name(data["assets"]["version"], "assets.version"),
    )
    scholar_gateway = ScholarGatewayConfig(
        command=_expect_str(data["mcp"]["scholar_gateway"]["command"], "mcp.scholar_gateway.command"),
        cutoff_date=_optional_str(data["mcp"]["scholar_gateway"]["cutoff_date"], "mcp.scholar_gateway.cutoff_date"),
        restrict_domains=_expect_bool(data["mcp"]["scholar_gateway"]["restrict_domains"], "mcp.scholar_gateway.restrict_domains"),
        strict_filtering=_expect_bool(data["mcp"]["scholar_gateway"]["strict_filtering"], "mcp.scholar_gateway.strict_filtering"),
        mode=_enum(data["mcp"]["scholar_gateway"]["mode"], {"rules", "llm"}, "mcp.scholar_gateway.mode"),
        format=_enum(data["mcp"]["scholar_gateway"]["format"], {"markdown", "text"}, "mcp.scholar_gateway.format"),
        references_dir=_safe_relative_path(data["mcp"]["scholar_gateway"]["references_dir"], "mcp.scholar_gateway.references_dir"),
        required_env_vars=_str_list(data["mcp"]["scholar_gateway"]["required_env_vars"], "mcp.scholar_gateway.required_env_vars"),
        optional_env_vars=_str_list(data["mcp"]["scholar_gateway"]["optional_env_vars"], "mcp.scholar_gateway.optional_env_vars"),
        startup_timeout_seconds=_positive_int(data["mcp"]["scholar_gateway"]["startup_timeout_seconds"], "mcp.scholar_gateway.startup_timeout_seconds"),
        tool_timeout_seconds=_positive_int(data["mcp"]["scholar_gateway"]["tool_timeout_seconds"], "mcp.scholar_gateway.tool_timeout_seconds"),
        approval_mode=_enum(data["mcp"]["scholar_gateway"]["approval_mode"], {"approve"}, "mcp.scholar_gateway.approval_mode"),
        max_concurrency=_positive_int(data["mcp"]["scholar_gateway"]["max_concurrency"], "mcp.scholar_gateway.max_concurrency"),
        extract_max_concurrency=_positive_int(data["mcp"]["scholar_gateway"]["extract_max_concurrency"], "mcp.scholar_gateway.extract_max_concurrency"),
    )
    skills = SkillsConfig(selected=_str_list(data["skills"]["selected"], "skills.selected"))
    subagents = SubagentsConfig(selected=_str_list(data["subagents"]["selected"], "subagents.selected"))
    _validate_selected_assets(skills.selected, assets.skills_dir, "skill")
    if subagents.selected:
        if assets.subagents_dir is None:
            raise ConfigError("subagents.selected requires assets.subagents_dir.")
        _validate_selected_subagents(subagents.selected, assets.subagents_dir)
    hooks = HooksConfig(stop=_stop_hook_specs(data.get("hooks", {}).get("stop", [])))
    harness = _enum(data["harness"], {"claude", "codex"}, "harness")
    claude_data = data["adapters"]["claude"]
    if harness == "claude":
        api_key_env = _validated_env_var_name(claude_data["api_key_env"], "adapters.claude.api_key_env")
        base_url_env = _validated_env_var_name(claude_data["base_url_env"], "adapters.claude.base_url_env")
    else:
        # Codex runs ignore the Claude api_key_env / base_url_env fields; allow them
        # unset (or any string) in YAML so Codex configs don't have to carry dummies.
        api_key_env = claude_data["api_key_env"] or ""
        base_url_env = claude_data["base_url_env"] or ""
    return RunConfig(
        version=_enum_int(data["version"], {1}, "version"),
        harness=harness,
        model=_optional_str(data["model"], "model"),
        model_profile=_optional_str(data["model_profile"], "model_profile"),
        effort=_optional_enum(data["effort"], {"minimal", "min", "low", "medium", "high", "xhigh", "max"}, "effort"),
        workspace=workspace,
        assets=assets,
        mcp=MCPConfig(profile=_enum(data["mcp"]["profile"], {"scholar-gateway"}, "mcp.profile"), scholar_gateway=scholar_gateway),
        permissions=PermissionsConfig(
            approval_policy=_enum(
                data["permissions"]["approval_policy"],
                {"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"},
                "permissions.approval_policy",
            ),
        ),
        budgets=BudgetsConfig(
            timeout_seconds=_optional_positive_int(data["budgets"]["timeout_seconds"], "budgets.timeout_seconds"),
            max_turns=_optional_positive_int(data["budgets"]["max_turns"], "budgets.max_turns"),
            max_cost_usd=_optional_nonnegative_float(data["budgets"]["max_cost_usd"], "budgets.max_cost_usd"),
        ),
        pricing=PricingConfig(models=_pricing_models(data["pricing"]["models"])),
        repair=RepairConfig(
            strategy=_enum(data["repair"]["strategy"], {"external-loop"}, "repair.strategy"),
            max_attempts=_nonnegative_int(data["repair"]["max_attempts"], "repair.max_attempts"),
        ),
        skills=skills,
        subagents=subagents,
        logging=LoggingConfig(
            write_run_record=_expect_bool(data["logging"]["write_run_record"], "logging.write_run_record"),
            retain_event_log=_expect_bool(data["logging"]["retain_event_log"], "logging.retain_event_log"),
        ),
        adapters=AdaptersConfig(
            claude=ClaudeAdapterConfig(
                strict_mcp_config=_expect_bool(data["adapters"]["claude"]["strict_mcp_config"], "adapters.claude.strict_mcp_config"),
                api_key_env=api_key_env,
                base_url_env=base_url_env,
                disallowed_tools=_validated_disallowed_tools(data["adapters"]["claude"]["disallowed_tools"]),
                setting_sources=_validated_setting_sources(data["adapters"]["claude"]["setting_sources"]),
                sdk_options=_expect_dict(data["adapters"]["claude"]["sdk_options"], "adapters.claude.sdk_options"),
            ),
            codex=CodexAdapterConfig(
                api_key_env=_validated_env_var_name(data["adapters"]["codex"]["api_key_env"], "adapters.codex.api_key_env"),
                base_url_env=_validated_env_var_name(data["adapters"]["codex"]["base_url_env"], "adapters.codex.base_url_env"),
                sandbox=_expect_str(data["adapters"]["codex"]["sandbox"], "adapters.codex.sandbox"),
                approval_mode=_expect_str(data["adapters"]["codex"]["approval_mode"], "adapters.codex.approval_mode"),
                network_access_enabled=_expect_bool(data["adapters"]["codex"]["network_access_enabled"], "adapters.codex.network_access_enabled"),
                cleanup_codex_home=_expect_bool(data["adapters"]["codex"]["cleanup_codex_home"], "adapters.codex.cleanup_codex_home"),
                reasoning_summary=_optional_enum(data["adapters"]["codex"]["reasoning_summary"], {"auto", "concise", "detailed", "none"}, "adapters.codex.reasoning_summary"),
                supports_reasoning_summaries=_optional_bool(data["adapters"]["codex"]["supports_reasoning_summaries"], "adapters.codex.supports_reasoning_summaries"),
                disabled_features=_validated_codex_disabled_features(data["adapters"]["codex"]["disabled_features"]),
            ),
        ),
        hooks=hooks,
    )


def _stop_hook_specs(value: object) -> list[StopHookSpec]:
    if not isinstance(value, list):
        raise ConfigError("hooks.stop must be a list.")
    specs: list[StopHookSpec] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ConfigError(f"hooks.stop[{index}] must be a mapping.")
        script = _existing_file(entry.get("script"), f"hooks.stop[{index}].script")
        timeout = _positive_int(entry.get("timeout", 30), f"hooks.stop[{index}].timeout")
        specs.append(StopHookSpec(script=script, timeout=timeout))
    return specs


def _validate_required(data: dict[str, Any]) -> None:
    required = [
        ("harness", data.get("harness")),
        ("assets.prompt_template", data["assets"].get("prompt_template")),
        ("assets.repair_prompt_template", data["assets"].get("repair_prompt_template")),
        ("assets.skills_dir", data["assets"].get("skills_dir")),
    ]
    missing = [name for name, value in required if value is None]
    if missing:
        raise ConfigError(f"Missing required config field(s): {', '.join(missing)}")


def _expect_path(value: object, field: str) -> Path:
    if not isinstance(value, Path):
        raise ConfigError(f"{field} must be a path.")
    return value


def _existing_file(value: object, field: str) -> Path:
    path = _expect_path(value, field)
    if not path.is_file():
        raise ConfigError(f"{field} does not exist or is not a file: {path}")
    return path


def _existing_dir(value: object, field: str) -> Path:
    path = _expect_path(value, field)
    if not path.is_dir():
        raise ConfigError(f"{field} does not exist or is not a directory: {path}")
    return path


def _existing_dir_or_none(value: object, field: str) -> Path | None:
    if value is None:
        return None
    return _existing_dir(value, field)


def _expect_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field} must be a boolean.")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    return _expect_bool(value, field)


def _expect_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty string.")
    return value


def _optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _expect_str(value, field)


def _optional_safe_relative_name(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _safe_relative_name(value, field)


def _expect_dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a mapping.")
    _reject_secret_values(value, field)
    return copy.deepcopy(value)


def _pricing_models(value: object) -> dict[str, ModelPricingConfig]:
    if not isinstance(value, dict):
        raise ConfigError("pricing.models must be a mapping.")
    models: dict[str, ModelPricingConfig] = {}
    allowed = {
        "input_per_million_usd",
        "cached_input_per_million_usd",
        "cache_creation_input_per_million_usd",
        "output_per_million_usd",
        "reasoning_output_per_million_usd",
        "input_includes_cached_tokens",
        "long_context_threshold_tokens",
        "long_context_input_per_million_usd",
        "long_context_cached_input_per_million_usd",
        "long_context_output_per_million_usd",
        "long_context_reasoning_output_per_million_usd",
    }
    for model, raw in value.items():
        if not isinstance(model, str) or not model.strip():
            raise ConfigError("pricing.models keys must be non-empty model strings.")
        field = f"pricing.models.{model}"
        if not isinstance(raw, dict):
            raise ConfigError(f"{field} must be a mapping.")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ConfigError(f"{field} has unknown field(s): {', '.join(unknown)}")
        models[model] = ModelPricingConfig(
            input_per_million_usd=_optional_nonnegative_float(raw.get("input_per_million_usd"), f"{field}.input_per_million_usd"),
            cached_input_per_million_usd=_optional_nonnegative_float(raw.get("cached_input_per_million_usd"), f"{field}.cached_input_per_million_usd"),
            cache_creation_input_per_million_usd=_optional_nonnegative_float(raw.get("cache_creation_input_per_million_usd"), f"{field}.cache_creation_input_per_million_usd"),
            output_per_million_usd=_optional_nonnegative_float(raw.get("output_per_million_usd"), f"{field}.output_per_million_usd"),
            reasoning_output_per_million_usd=_optional_nonnegative_float(raw.get("reasoning_output_per_million_usd"), f"{field}.reasoning_output_per_million_usd"),
            input_includes_cached_tokens=_optional_bool(raw.get("input_includes_cached_tokens"), f"{field}.input_includes_cached_tokens") if "input_includes_cached_tokens" in raw else True,
            long_context_threshold_tokens=_optional_positive_int(raw.get("long_context_threshold_tokens"), f"{field}.long_context_threshold_tokens"),
            long_context_input_per_million_usd=_optional_nonnegative_float(raw.get("long_context_input_per_million_usd"), f"{field}.long_context_input_per_million_usd"),
            long_context_cached_input_per_million_usd=_optional_nonnegative_float(raw.get("long_context_cached_input_per_million_usd"), f"{field}.long_context_cached_input_per_million_usd"),
            long_context_output_per_million_usd=_optional_nonnegative_float(raw.get("long_context_output_per_million_usd"), f"{field}.long_context_output_per_million_usd"),
            long_context_reasoning_output_per_million_usd=_optional_nonnegative_float(raw.get("long_context_reasoning_output_per_million_usd"), f"{field}.long_context_reasoning_output_per_million_usd"),
        )
    return models


def _str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{field} must be a list of non-empty strings.")
    return list(value)


def _validated_setting_sources(value: object) -> list[str]:
    items = _str_list(value, "adapters.claude.setting_sources")
    allowed = {"user", "project", "local"}
    invalid = [item for item in items if item not in allowed]
    if invalid:
        raise ConfigError(
            "adapters.claude.setting_sources entries must be one of "
            f"{sorted(allowed)}; got invalid: {invalid}."
        )
    return items


def _validated_env_var_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty environment variable name (e.g. ANTHROPIC_API_KEY).")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
        raise ConfigError(
            f"{field}={value!r} is not a valid environment variable name "
            "(uppercase letters, digits, and underscores; must not start with a digit)."
        )
    return value


def _validated_disallowed_tools(value: object) -> list[str]:
    items = _str_list(value, "adapters.claude.disallowed_tools")
    invalid = [
        item
        for item in items
        if item not in KNOWN_CLAUDE_TOOL_NAMES and not item.startswith("mcp__")
    ]
    if invalid:
        raise ConfigError(
            "adapters.claude.disallowed_tools entries must be a known Claude Code tool "
            "name or an `mcp__<server>__<tool>` MCP tool name; got unknown: "
            f"{invalid}. Known names: {sorted(KNOWN_CLAUDE_TOOL_NAMES)}."
        )
    return items


def _validated_codex_disabled_features(value: object) -> list[str]:
    items = _str_list(value, "adapters.codex.disabled_features")
    invalid = [item for item in items if not re.fullmatch(r"[a-z][a-z0-9_]*", item)]
    if invalid:
        raise ConfigError(
            "adapters.codex.disabled_features entries must be Codex feature flag "
            f"names using lowercase letters, digits, and underscores; got invalid: {invalid}."
        )
    return list(dict.fromkeys(items))


def _enum(value: object, choices: set[str], field: str) -> Any:
    if not isinstance(value, str) or value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ConfigError(f"{field} must be one of: {allowed}")
    return value


def _optional_enum(value: object, choices: set[str], field: str) -> Any:
    if value is None:
        return None
    return _enum(value, choices, field)


def _enum_int(value: object, choices: set[int], field: str) -> int:
    if not isinstance(value, int) or value not in choices:
        allowed = ", ".join(str(choice) for choice in sorted(choices))
        raise ConfigError(f"{field} must be one of: {allowed}")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{field} must be a positive integer.")
    return value


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field)


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{field} must be a non-negative integer.")
    return value


def _optional_nonnegative_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{field} must be a non-negative number or null.")
    return float(value)


def _safe_relative_name(value: object, field: str) -> str:
    text = _expect_str(value, field)
    path = Path(text)
    if path.is_absolute() or len(path.parts) != 1 or text in {".", ".."}:
        raise ConfigError(f"{field} must be a single relative directory name.")
    return text


def _safe_relative_path(value: object, field: str) -> str:
    text = _expect_str(value, field)
    path = Path(text)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ConfigError(f"{field} must be a relative path inside the agent workspace.")
    return text


def _validate_selected_assets(ids: list[str], root: Path, kind: str) -> None:
    for asset_id in ids:
        skill_file = root / asset_id / "SKILL.md"
        if skill_file.is_file():
            continue
        raise ConfigError(f"Selected {kind} asset is missing: {asset_id} ({skill_file})")


def _validate_selected_subagents(ids: list[str], root: Path) -> None:
    for asset_id in ids:
        path = root / f"{asset_id}.md"
        if not path.is_file():
            raise ConfigError(f"Selected subagent asset is missing: {asset_id} ({path})")
        try:
            with path.open("r", encoding="utf-8") as handle:
                first_line = handle.readline()
        except OSError as exc:
            raise ConfigError(f"Could not read subagent asset: {path}") from exc
        if first_line.rstrip("\r\n") != "---":
            raise ConfigError(
                f"Subagent asset must start with YAML frontmatter (a `---` line): {path}"
            )
