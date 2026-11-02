from __future__ import annotations

from pathlib import Path

import pytest

from research_ideation_agent import load_config
from research_ideation_agent.errors import ConfigError


def test_load_example_config_resolves_paths_and_overrides() -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/gpt.yaml",
        overrides={
            "workspace.run_id": "unit-run",
            "repair.max_attempts": 2,
            "skills.selected": ["literature-review"],
        },
    )

    assert cfg.harness == "codex"
    assert cfg.model == "gpt-5.4"
    assert cfg.workspace.run_id == "unit-run"
    assert cfg.workspace.root.is_absolute()
    assert cfg.effort == "high"
    assert cfg.assets.prompt_template.is_file()
    assert cfg.mcp.scholar_gateway.command == "scholar-gateway-mcp"
    assert cfg.repair.max_attempts == 2
    assert cfg.skills.selected == ["literature-review"]
    assert cfg.adapters.codex.api_key_env == "GPT_API_KEY"
    assert cfg.adapters.codex.base_url_env == "GPT_API_BASE_URL"
    assert cfg.adapters.codex.cleanup_codex_home is True
    assert cfg.adapters.codex.reasoning_summary == "auto"
    assert cfg.adapters.codex.supports_reasoning_summaries is True
    assert "browser_use" in cfg.adapters.codex.disabled_features


def test_effort_accepts_codex_reasoning_names() -> None:
    minimal = load_config("configs/simple/gpt.yaml", overrides={"effort": "minimal"})
    xhigh = load_config("configs/simple/gpt.yaml", overrides={"effort": "xhigh"})

    assert minimal.effort == "minimal"
    assert xhigh.effort == "xhigh"


def test_load_simple_gpt_config_uses_codex_harness_without_optional_assets() -> None:
    cfg = load_config("configs/simple/gpt.yaml")

    assert cfg.harness == "codex"
    assert cfg.model == "gpt-5.4"
    assert cfg.effort == "max"
    assert cfg.assets.version == "simple"
    assert cfg.assets.prompt_template.name == "startup.md"
    assert cfg.assets.repair_prompt_template.name == "repair.md"
    assert cfg.assets.skills_dir.is_dir()
    assert cfg.assets.subagents_dir is None
    assert cfg.skills.selected == []
    assert cfg.subagents.selected == []
    assert cfg.adapters.codex.api_key_env == "GPT_API_KEY"
    assert cfg.adapters.codex.base_url_env == "GPT_API_BASE_URL"
    assert cfg.adapters.codex.cleanup_codex_home is True
    assert "browser_use" in cfg.adapters.codex.disabled_features


def test_scientific_base_config_can_be_overridden_by_model_profile_shape() -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/base.yaml",
        overrides={
            "harness": "claude",
            "model": "Pro/moonshotai/Kimi-K2.6",
            "adapters.claude.api_key_env": "KIMI_K2_6_API_KEY",
            "adapters.claude.base_url_env": "KIMI_K2_6_BASE_URL",
        },
    )

    assert cfg.harness == "claude"
    assert cfg.model == "Pro/moonshotai/Kimi-K2.6"
    assert cfg.adapters.claude.api_key_env == "KIMI_K2_6_API_KEY"
    assert cfg.adapters.claude.base_url_env == "KIMI_K2_6_BASE_URL"
    assert "WebSearch" in cfg.adapters.claude.disallowed_tools


def test_pricing_models_are_loaded_and_validated() -> None:
    cfg = load_config(
        "configs/scientific-hypothesis-generator/base.yaml",
        overrides={
            "pricing.models": {
                "gpt-5.4": {
                    "input_per_million_usd": 1.25,
                    "cached_input_per_million_usd": 0.125,
                    "output_per_million_usd": 10,
                    "reasoning_output_per_million_usd": 10,
                    "input_includes_cached_tokens": True,
                    "long_context_threshold_tokens": 270000,
                    "long_context_input_per_million_usd": 2.5,
                    "long_context_cached_input_per_million_usd": 0.25,
                    "long_context_output_per_million_usd": 15,
                    "long_context_reasoning_output_per_million_usd": 15,
                }
            }
        },
    )

    pricing = cfg.pricing.models["gpt-5.4"]
    assert pricing.input_per_million_usd == 1.25
    assert pricing.cached_input_per_million_usd == 0.125
    assert pricing.output_per_million_usd == 10
    assert pricing.reasoning_output_per_million_usd == 10
    assert pricing.input_includes_cached_tokens is True
    assert pricing.long_context_threshold_tokens == 270000
    assert pricing.long_context_input_per_million_usd == 2.5
    assert pricing.long_context_cached_input_per_million_usd == 0.25
    assert pricing.long_context_output_per_million_usd == 15
    assert pricing.long_context_reasoning_output_per_million_usd == 15


def test_pricing_rejects_unknown_fields() -> None:
    with pytest.raises(ConfigError, match="unknown field"):
        load_config(
            "configs/scientific-hypothesis-generator/base.yaml",
            overrides={"pricing.models": {"gpt-5.4": {"input_usd": 1.0}}},
        )


def test_assets_version_resolves_conventional_asset_paths(tmp_path: Path) -> None:
    assets_root = tmp_path / "assets"
    version_dir = assets_root / "init"
    (version_dir / "prompts").mkdir(parents=True)
    (version_dir / "prompts" / "startup.md").write_text("Topic: {{topic}}\n", encoding="utf-8")
    (version_dir / "prompts" / "repair.md").write_text("Repair: {{topic}}\n", encoding="utf-8")
    (version_dir / "skills" / "literature-grounding").mkdir(parents=True)
    (version_dir / "skills" / "literature-grounding" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (version_dir / "subagents").mkdir()
    (version_dir / "subagents" / "source-reviewer.md").write_text("---\nname: source-reviewer\n---\n", encoding="utf-8")

    legacy_dir = assets_root / "legacy"
    legacy_dir.mkdir()
    config_path = tmp_path / "versioned.yaml"
    config_path.write_text(
        f"""
version: 1
harness: codex
assets:
  root: {assets_root}
  version: init
  prompt_template: {legacy_dir / "startup.md"}
  repair_prompt_template: {legacy_dir / "repair.md"}
  skills_dir: {legacy_dir / "skills"}
skills:
  selected:
    - literature-grounding
subagents:
  selected:
    - source-reviewer
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path, project_root=tmp_path)

    assert cfg.assets.root == assets_root
    assert cfg.assets.version == "init"
    assert cfg.assets.prompt_template == version_dir / "prompts" / "startup.md"
    assert cfg.assets.repair_prompt_template == version_dir / "prompts" / "repair.md"
    assert cfg.assets.skills_dir == version_dir / "skills"
    assert cfg.assets.subagents_dir == version_dir / "subagents"


def test_assets_version_requires_root() -> None:
    with pytest.raises(ConfigError, match="assets.version requires assets.root"):
        load_config("configs/simple/gpt.yaml", overrides={"assets.root": None, "assets.version": "init"})


def test_unknown_override_is_rejected() -> None:
    with pytest.raises(ConfigError, match="Invalid override path"):
        load_config("configs/simple/gpt.yaml", overrides={"not.real": True})


def test_codex_disabled_features_must_be_safe_feature_names() -> None:
    with pytest.raises(ConfigError, match="disabled_features"):
        load_config("configs/simple/gpt.yaml", overrides={"adapters.codex.disabled_features": ["browser-use"]})


def test_secret_config_field_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        """
version: 1
harness: codex
assets:
  root: ../src/research_ideation_agent/assets
  version: init
adapters:
  claude:
    sdk_options:
      api_key: should-not-be-here
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="secret value"):
        load_config(config_path)


def test_project_root_resolves_yaml_relative_to_pyproject() -> None:
    cfg = load_config("configs/simple/glm.yaml")
    repo_root = Path(__file__).resolve().parent.parent
    assert cfg.workspace.root == (repo_root / ".runs").resolve()
    assert cfg.assets.root == (repo_root / "src/research_ideation_agent/assets").resolve()


def test_project_root_override_via_env(monkeypatch, tmp_path: Path) -> None:
    _write_minimal_yaml(tmp_path)
    monkeypatch.setenv("RIA_PROJECT_ROOT", str(tmp_path))
    cfg = load_config(tmp_path / "minimal.yaml", overrides={"skills.selected": []})
    assert cfg.workspace.root == (tmp_path / ".runs").resolve()


def test_project_root_override_via_kwarg(tmp_path: Path) -> None:
    _write_minimal_yaml(tmp_path)
    cfg = load_config(tmp_path / "minimal.yaml", project_root=tmp_path, overrides={"skills.selected": []})
    assert cfg.workspace.root == (tmp_path / ".runs").resolve()


def test_project_root_missing_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RIA_PROJECT_ROOT", raising=False)
    _write_minimal_yaml(tmp_path)
    with pytest.raises(ConfigError, match="pyproject.toml"):
        load_config(tmp_path / "minimal.yaml")


RUN_CONFIG_PATHS = [
    path for path in sorted(Path("configs").rglob("*.yaml")) if path.name != "model-profiles.yaml"
]


@pytest.mark.parametrize("config_path", RUN_CONFIG_PATHS)
def test_all_shipped_configs_load_cleanly(config_path: Path) -> None:
    """Every YAML under configs/ should parse, validate, and resolve assets.

    A typo in any shipped config would otherwise only surface when a user
    happened to point the CLI at it. This single parametrized smoke test is
    the regression guard.
    """

    cfg = load_config(config_path)

    assert cfg.harness in {"claude", "codex"}
    assert cfg.assets.prompt_template.is_file()
    assert cfg.assets.repair_prompt_template.is_file()
    if cfg.harness == "claude":
        assert cfg.adapters.claude.api_key_env, f"{config_path.name} missing api_key_env"
        assert cfg.adapters.claude.base_url_env, f"{config_path.name} missing base_url_env"
    if cfg.harness == "codex":
        assert cfg.adapters.codex.api_key_env, f"{config_path.name} missing api_key_env"
        assert cfg.adapters.codex.base_url_env, f"{config_path.name} missing base_url_env"


def _write_minimal_yaml(tmp_path: Path) -> None:
    """Write a config file plus an assets tree under tmp_path so a config
    pointed at tmp_path resolves cleanly when project_root is overridden.
    """

    assets_dir = tmp_path / "src/research_ideation_agent/assets/init"
    (assets_dir / "prompts").mkdir(parents=True)
    (assets_dir / "prompts" / "startup.md").write_text("Topic: {{topic}}\n", encoding="utf-8")
    (assets_dir / "prompts" / "repair.md").write_text("Repair: {{topic}}\n", encoding="utf-8")
    (assets_dir / "skills").mkdir()
    (tmp_path / "minimal.yaml").write_text(
        """
version: 1
harness: codex
assets:
  root: src/research_ideation_agent/assets
  version: init
workspace:
  root: .runs
""",
        encoding="utf-8",
    )
