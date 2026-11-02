"""Prompt rendering and quality-layer asset packaging."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from research_ideation_agent.constants import CANONICAL_HEADINGS
from research_ideation_agent.errors import ConfigError
from research_ideation_agent.models import RunConfig, RunRequest, ValidationResult
from research_ideation_agent.workspace import RunWorkspace


def prepare_agent_assets(request: RunRequest, workspace: RunWorkspace) -> dict[str, Path]:
    """Render startup assets and package selected skills/subagents."""

    artifacts: dict[str, Path] = {}
    artifacts["startup_prompt"] = render_startup_prompt(request, workspace)
    artifacts.update(package_skills(request.config, workspace.agent_workspace_path))
    artifacts.update(package_subagents(request.config, workspace))
    artifacts.update(package_shared(request.config, workspace.agent_workspace_path))
    write_always_on_guidance(request.config, workspace.agent_workspace_path)
    return artifacts


def render_startup_prompt(request: RunRequest, workspace: RunWorkspace) -> Path:
    template = request.config.assets.prompt_template.read_text(encoding="utf-8")
    rendered = render_template(
        template,
        {
            "topic": request.topic,
            "proposal_path": "proposal.md",
            "selected_skills": _bullet_list(request.config.skills.selected),
            "selected_subagents": _bullet_list(request.config.subagents.selected) if request.config.subagents.selected else "None selected.",
            "mcp_tool_overview": _mcp_tool_overview(request.config),
        },
    )
    path = workspace.input_path / "startup_prompt.md"
    path.write_text(rendered, encoding="utf-8")
    return path


def render_repair_prompt(
    request: RunRequest,
    workspace: RunWorkspace,
    validation: ValidationResult,
    attempt_index: int,
    gate_reason: str | None = None,
) -> tuple[str, Path]:
    missing_headings = [CANONICAL_HEADINGS[intent] for intent in validation.missing]
    template = request.config.assets.repair_prompt_template.read_text(encoding="utf-8")
    errors = list(validation.errors)
    if gate_reason:
        errors.append(f"Artifact gate not cleared: {gate_reason}")
    instruction = render_template(
        template,
        {
            "topic": request.topic,
            "proposal_path": "proposal.md",
            "missing_intents": _bullet_list(validation.missing),
            "missing_headings": _bullet_list(missing_headings),
            "validation_errors": _bullet_list(errors) if errors else "None.",
            "attempt_index": str(attempt_index),
            "max_attempts": str(request.config.repair.max_attempts),
        },
    )
    latest = workspace.input_path / "repair_prompt.md"
    attempt_path = workspace.input_path / f"repair_prompt_{attempt_index}.md"
    latest.write_text(instruction, encoding="utf-8")
    attempt_path.write_text(instruction, encoding="utf-8")
    return instruction, attempt_path


def render_template(template: str, variables: dict[str, str]) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", value)
        rendered = rendered.replace("{{ " + key + " }}", value)
    return rendered


def _mcp_tool_overview(config: RunConfig) -> str:
    lines = [
        "- `web_search` / `mcp__scholar-gateway__web_search` searches for papers and web pages; use `query` for one search or `queries` for a parallel batch.",
        "- `web_fetch` / `mcp__scholar-gateway__web_fetch` fetches web pages or paper abstract pages — not full papers; use `url` for one page or `urls` for a parallel batch.",
        "- `extract_paper` / `mcp__scholar-gateway__extract_paper` downloads papers into `references/` (split into raw sections) and writes a best-effort summary in `papers/paper_summaries/`; read the summary first and open a raw section only to verify a cited figure. Use `url` for one paper or `urls` for a parallel batch.",
    ]
    forwarded_env_names = set(config.mcp.scholar_gateway.required_env_vars) | set(
        config.mcp.scholar_gateway.optional_env_vars
    )
    if {"DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY"}.issubset(forwarded_env_names):
        lines.append(
            "- `summarize_paper` / `mcp__scholar-gateway__summarize_paper` condenses an extracted paper "
            "folder into one markdown summary; use it to rerun or repair summaries. Work from the summary by default and open a raw section only to verify a specific figure you cite."
        )
    return "\n".join(lines)


def package_skills(config: RunConfig, agent_workspace: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    if not config.skills.selected:
        return artifacts
    if config.harness == "claude":
        target = agent_workspace / ".claude" / "skills"
        for skill_id in config.skills.selected:
            source_dir = config.assets.skills_dir / skill_id
            destination_dir = target / skill_id
            shutil.copytree(source_dir, destination_dir)
            artifacts[f"skill:{skill_id}"] = destination_dir / "SKILL.md"
    else:
        target = agent_workspace / ".agents" / "skills"
        for skill_id in config.skills.selected:
            source_dir = config.assets.skills_dir / skill_id
            destination_dir = target / skill_id
            shutil.copytree(source_dir, destination_dir)
            artifacts[f"skill:{skill_id}"] = destination_dir / "SKILL.md"
    return artifacts


def package_shared(config: RunConfig, agent_workspace: Path) -> dict[str, Path]:
    """Copy the asset version's `shared/` directory into the workspace (e.g. `shared/rubric.md`).

    The reviewer subagent and other assets reference `shared/...` relative to the
    agent workspace, so the directory must be present on disk for those references
    to resolve. Domain-agnostic: it copies whatever `shared/` ships, if any.
    """
    source_dir = config.assets.skills_dir.parent / "shared"
    if not source_dir.is_dir():
        return {}
    destination_dir = agent_workspace / "shared"
    shutil.copytree(source_dir, destination_dir, dirs_exist_ok=True)
    artifacts: dict[str, Path] = {}
    for path in sorted(destination_dir.rglob("*")):
        if path.is_file():
            artifacts[f"shared:{path.relative_to(destination_dir).as_posix()}"] = path
    return artifacts


def package_subagents(config: RunConfig, workspace: RunWorkspace) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    if not config.subagents.selected:
        return artifacts
    if config.assets.subagents_dir is None:
        raise ConfigError("subagents.selected requires assets.subagents_dir.")
    if config.harness == "claude":
        return _package_claude_subagents(config, workspace.agent_workspace_path)
    return _package_codex_subagents(config, workspace.agent_workspace_path)


def _package_claude_subagents(config: RunConfig, agent_workspace: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    if config.assets.subagents_dir is None:
        raise ConfigError("subagents.selected requires assets.subagents_dir.")
    target = agent_workspace / ".claude" / "agents"
    target.mkdir(parents=True, exist_ok=True)
    for subagent_id in config.subagents.selected:
        source = config.assets.subagents_dir / f"{subagent_id}.md"
        if not source.is_file():
            raise ConfigError(f"Selected subagent asset is missing: {subagent_id} ({source})")
        destination = target / f"{subagent_id}.md"
        shutil.copyfile(source, destination)
        artifacts[f"subagent:{subagent_id}"] = destination
    return artifacts


def _package_codex_subagents(config: RunConfig, agent_workspace: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    if config.assets.subagents_dir is None:
        raise ConfigError("subagents.selected requires assets.subagents_dir.")
    target = agent_workspace / ".codex" / "agents"
    target.mkdir(parents=True, exist_ok=True)
    for subagent_id in config.subagents.selected:
        source = config.assets.subagents_dir / f"{subagent_id}.md"
        if not source.is_file():
            raise ConfigError(f"Selected subagent asset is missing: {subagent_id} ({source})")
        frontmatter, instructions = _read_subagent_markdown(source)
        name = _required_frontmatter_string(frontmatter, "name", source)
        if name != subagent_id:
            raise ConfigError(
                f"Subagent frontmatter name must match selected ID for Codex packaging: {source} "
                f"has name {name!r}, selected ID is {subagent_id!r}."
            )
        description = _required_frontmatter_string(frontmatter, "description", source)
        if not instructions.strip():
            raise ConfigError(f"Subagent asset must include a prompt body: {source}")
        destination = target / f"{subagent_id}.toml"
        destination.write_text(
            _render_codex_subagent_toml(
                name=name,
                description=description,
                developer_instructions=instructions.strip(),
            ),
            encoding="utf-8",
        )
        artifacts[f"subagent:{subagent_id}"] = destination
    return artifacts


def write_always_on_guidance(config: RunConfig, agent_workspace: Path) -> Path:
    text = "\n".join(
        [
            "# Research Ideation Run Contract",
            "",
            "This workspace is a single research hypothesis generation run.",
            "",
            "## Authority",
            "",
            "Follow the startup prompt for the concrete topic and run configuration. Follow the selected skill files for detailed workflows. This file only defines always-on workspace and evidence constraints.",
            "",
            "## Required Artifacts",
            "",
            "By the end of the run, write `proposal.md` in this directory. Also write the intermediate notes required by the startup prompt and selected skills.",
            "",
            "Do not stop with only chat output. Required artifacts must be on disk.",
            "",
            "## Evidence Discipline",
            "",
            "Do not fabricate references, paper metadata, claims, benchmarks, or experimental results.",
            "",
            "Every important literature claim must be grounded in an inspected source: a fetched page, an extracted paper folder, or a paper summary backed by an extracted folder. The paper summary is the default working layer — it is sufficient to ground most claims and to describe a method at mechanism level. Open a raw `references/` section only to verify a specific figure you actually cite (an exact number, equation, ablation, or mechanism step), not as the default for load-bearing details. Reading every raw section of every paper is the main avoidable cost of a run.",
            "",
            "## Tool And File Boundaries",
            "",
            "Use scholar-gateway tools for discovery and paper evidence when they are available. `references/` is tool-written raw evidence; read it but do not edit it by hand.",
            "",
            "## Final Quality Gate",
            "",
            "Before finishing, run the proposal audit required by the selected skills. Check novelty, soundness, feasibility, falsifiability, benchmark/model/metric choices, and potential impact. Revise `proposal.md` if the audit finds problems.",
            "",
        ]
    )
    filename = "CLAUDE.md" if config.harness == "claude" else "AGENTS.md"
    path = agent_workspace / filename
    path.write_text(text, encoding="utf-8")
    return path


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "None."
    return "\n".join(f"- {item}" for item in items)


def _read_subagent_markdown(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read subagent asset: {path}") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ConfigError(f"Subagent asset must start with YAML frontmatter (a `---` line): {path}")
    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            closing_index = index
            break
    if closing_index is None:
        raise ConfigError(f"Subagent asset must close YAML frontmatter with a `---` line: {path}")
    yaml_text = "".join(lines[1:closing_index])
    try:
        frontmatter = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse subagent frontmatter: {path}") from exc
    if not isinstance(frontmatter, dict):
        raise ConfigError(f"Subagent frontmatter must be a mapping: {path}")
    body = "".join(lines[closing_index + 1 :]).lstrip("\r\n")
    return frontmatter, body


def _required_frontmatter_string(frontmatter: dict[str, Any], field: str, path: Path) -> str:
    value = frontmatter.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Subagent frontmatter must include non-empty `{field}`: {path}")
    return value.strip()


def _render_codex_subagent_toml(*, name: str, description: str, developer_instructions: str) -> str:
    return "\n".join(
        [
            f"name = {_toml_string(name)}",
            f"description = {_toml_string(description)}",
            # Omit optional config keys so Codex custom agents inherit the leader
            # session's model, reasoning effort, sandbox, MCP servers, and skills.
            f"developer_instructions = {_toml_string(developer_instructions)}",
            "",
        ]
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)
