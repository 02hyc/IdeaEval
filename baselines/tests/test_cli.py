from __future__ import annotations

import json
from pathlib import Path

from research_ideation_agent import cli
from research_ideation_agent.errors import ConfigError
from research_ideation_agent.models import RunResult, ValidationResult


def _succeeded_result() -> RunResult:
    proposal = Path(".runs/run-1/agent_workspace/proposal.md")
    return RunResult(
        run_id="run-1",
        status="succeeded",
        failure_kind=None,
        harness="codex",
        workspace_path=Path(".runs/run-1"),
        agent_workspace_path=Path(".runs/run-1/agent_workspace"),
        proposal_path=proposal,
        validation=ValidationResult(
            valid=True,
            proposal_path=proposal,
            missing=[],
            present=["title", "introduction", "related_work", "methods", "experiments_design", "references"],
            errors=[],
            validator_version="mvp-structure-v1",
        ),
        repair_attempts=[],
        session_id="thread-1",
        model=None,
        usage=None,
        artifacts={},
        error=None,
    )


def test_cli_calls_public_api_with_ordered_overrides(monkeypatch, capsys) -> None:
    captured = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["topic"] = topic
        captured["config"] = config
        captured["overrides"] = overrides
        return RunResult(
            run_id="run-1",
            status="succeeded",
            failure_kind=None,
            harness="codex",
            workspace_path=Path(".runs/run-1"),
            agent_workspace_path=Path(".runs/run-1/agent_workspace"),
            proposal_path=Path(".runs/run-1/agent_workspace/proposal.md"),
            validation=ValidationResult(
                valid=True,
                proposal_path=Path(".runs/run-1/agent_workspace/proposal.md"),
                missing=[],
                present=["title", "introduction", "related_work", "methods", "experiments_design", "references"],
                errors=[],
                validator_version="mvp-structure-v1",
            ),
            repair_attempts=[],
            session_id="thread-1",
            model=None,
            usage=None,
            artifacts={},
            error=None,
        )

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)
    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            "config.yaml",
            "--run-id",
            "first",
            "--set",
            "repair.max_attempts=2",
            "--set",
            "workspace.run_id=second",
            "--assets-version",
            "init",
            "--set",
            "assets.version=lead-sub",
            "--json",
        ]
    )

    assert code == 0
    assert captured["topic"] == "Topic"
    assert captured["config"] == Path("config.yaml")
    assert captured["overrides"] == {
        "workspace.run_id": "second",
        "repair.max_attempts": 2,
        "assets.version": "lead-sub",
    }
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "succeeded"


def test_cli_cutoff_date_maps_to_scholar_gateway_override(monkeypatch) -> None:
    captured = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["overrides"] = overrides
        return _succeeded_result()

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            "config.yaml",
            "--cutoff-date",
            "2026-06-09",
        ]
    )

    assert code == 0
    assert captured["overrides"] == {"mcp.scholar_gateway.cutoff_date": "2026-06-09"}


def test_cli_cutoff_date_participates_in_ordered_overrides(monkeypatch) -> None:
    captured = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["overrides"] = overrides
        return _succeeded_result()

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            "config.yaml",
            "--cutoff-date",
            "2026-06-09",
            "--set",
            "mcp.scholar_gateway.cutoff_date=2026-06-01",
        ]
    )

    assert code == 0
    assert captured["overrides"]["mcp.scholar_gateway.cutoff_date"] == "2026-06-01"

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            "config.yaml",
            "--set",
            "mcp.scholar_gateway.cutoff_date=2026-06-01",
            "--cutoff-date=2026-06-09",
        ]
    )

    assert code == 0
    assert captured["overrides"]["mcp.scholar_gateway.cutoff_date"] == "2026-06-09"


def test_cli_cutoff_date_requires_iso_date(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "run_research_ideation", lambda *args, **kwargs: _succeeded_result())

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            "config.yaml",
            "--cutoff-date",
            "2026/06/09",
        ]
    )

    assert code == 2
    assert "--cutoff-date must use YYYY-MM-DD" in capsys.readouterr().err


def test_cli_model_profile_maps_harness_model_and_env_names(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["topic"] = topic
        captured["config"] = config
        captured["overrides"] = overrides
        captured["project_root"] = project_root
        return _succeeded_result()

    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        """
profiles:
  kimi-k2.6:
    harness: claude
    model: Pro/moonshotai/Kimi-K2.6
    api_key_env: KIMI_K2_6_API_KEY
    base_url_env: KIMI_K2_6_BASE_URL
""",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(cli, "run_research_ideation", fake_run)

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            str(tmp_path / "config.yaml"),
            "--project-root",
            str(tmp_path),
            "--model-profiles-file",
            str(profiles),
            "--model-profile",
            "kimi-k2.6",
        ]
    )

    assert code == 0
    assert captured["overrides"] == {
        "harness": "claude",
        "model": "Pro/moonshotai/Kimi-K2.6",
        "model_profile": "kimi-k2.6",
        "adapters.claude.api_key_env": "KIMI_K2_6_API_KEY",
        "adapters.claude.base_url_env": "KIMI_K2_6_BASE_URL",
    }


def test_cli_explicit_overrides_can_override_model_profile(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["overrides"] = overrides
        return _succeeded_result()

    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        """
profiles:
  gpt-5.4:
    harness: codex
    model: gpt-5.4
    api_key_env: GPT_5_4_API_KEY
    base_url_env: GPT_5_4_BASE_URL
""",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(cli, "run_research_ideation", fake_run)

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            str(tmp_path / "config.yaml"),
            "--project-root",
            str(tmp_path),
            "--model-profiles-file",
            str(profiles),
            "--model-profile",
            "gpt-5.4",
            "--set",
            "model=gpt-override",
        ]
    )

    assert code == 0
    assert captured["overrides"]["model"] == "gpt-override"


def test_cli_unknown_model_profile_returns_config_error(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "run_research_ideation", lambda *args, **kwargs: _succeeded_result())
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text("profiles: {}\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("dummy", encoding="utf-8")

    code = cli.main(
        [
            "run",
            "--topic",
            "Topic",
            "--config",
            str(tmp_path / "config.yaml"),
            "--project-root",
            str(tmp_path),
            "--model-profiles-file",
            str(profiles),
            "--model-profile",
            "missing",
        ]
    )

    assert code == 2
    assert "Unknown model profile" in capsys.readouterr().err


def test_cli_reads_topic_from_file(tmp_path, monkeypatch, capsys) -> None:
    captured = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["topic"] = topic
        return _succeeded_result()

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)
    topic_file = tmp_path / "topic.txt"
    topic_file.write_text("Topic from file\n", encoding="utf-8")

    code = cli.main(["run", "--topic-file", str(topic_file), "--config", "config.yaml"])

    assert code == 0
    assert captured["topic"] == "Topic from file"


def test_cli_quiet_suppresses_human_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "run_research_ideation", lambda topic, config, *, overrides=None, project_root=None: _succeeded_result())

    code = cli.main(["run", "--topic", "Topic", "--config", "config.yaml", "--quiet"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


def test_cli_returns_exit_code_2_on_config_error(monkeypatch, capsys) -> None:
    def fake_run(topic, config, *, overrides=None, project_root=None):
        raise ConfigError("bad config field")

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)

    code = cli.main(["run", "--topic", "Topic", "--config", "missing.yaml"])

    captured = capsys.readouterr()
    assert code == 2
    assert "bad config field" in captured.err


def test_cli_resolves_topic_file_against_explicit_project_root(tmp_path, monkeypatch) -> None:
    """--project-root anchors --topic-file resolution to the supplied directory,
    not cwd, so a config + topic file outside any pyproject.toml tree still works."""

    captured: dict[str, object] = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["topic"] = topic
        captured["project_root"] = project_root
        return _succeeded_result()

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)
    (tmp_path / "topic.txt").write_text("anchored topic\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("dummy", encoding="utf-8")

    code = cli.main([
        "run",
        "--config", str(tmp_path / "config.yaml"),
        "--topic-file", "topic.txt",
        "--project-root", str(tmp_path),
    ])

    assert code == 0
    assert captured["topic"] == "anchored topic"
    assert captured["project_root"] == tmp_path


def test_cli_honours_ria_project_root_env_var(tmp_path, monkeypatch) -> None:
    """RIA_PROJECT_ROOT works exactly like --project-root when the flag is omitted.
    This is the dotenv path: a .env line `RIA_PROJECT_ROOT=...` sets the anchor."""

    captured: dict[str, object] = {}

    def fake_run(topic, config, *, overrides=None, project_root=None):
        captured["project_root"] = project_root
        return _succeeded_result()

    monkeypatch.setattr(cli, "run_research_ideation", fake_run)
    monkeypatch.setenv("RIA_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "topic.txt").write_text("env-anchored\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("dummy", encoding="utf-8")

    code = cli.main([
        "run",
        "--config", str(tmp_path / "config.yaml"),
        "--topic-file", "topic.txt",
    ])

    assert code == 0
    assert captured["project_root"] == tmp_path
