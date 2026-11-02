from __future__ import annotations

from pathlib import Path


def test_claude_declared_stop_hooks_use_config_not_version(tmp_path: Path) -> None:
    from research_ideation_agent.adapters import claude as claude_mod

    assert not hasattr(claude_mod, "_HYPOTHESIS_ASSET_VERSION"), "version gating must be removed"

    script = tmp_path / "gate.py"
    script.write_text("def evaluate_stop(payload):\n    return None\n", encoding="utf-8")

    class _HookMatcher:
        def __init__(self, *, matcher, hooks, timeout):
            self.matcher = matcher
            self.hooks = hooks
            self.timeout = timeout

    def _ctx(stop):
        cfg = type("Cfg", (), {"hooks": type("H", (), {"stop": stop})()})()
        return type("Ctx", (), {"config": cfg})()

    spec = type("Spec", (), {"script": script, "timeout": 30})()
    result = claude_mod._declared_stop_hooks(_ctx([spec]), _HookMatcher)
    assert result is not None and "Stop" in result and len(result["Stop"]) == 1

    assert claude_mod._declared_stop_hooks(_ctx([]), _HookMatcher) is None


def test_codex_config_writes_declared_stop_hooks(tmp_path: Path) -> None:
    from research_ideation_agent.adapters import codex as codex_mod

    assert not hasattr(codex_mod, "_HYPOTHESIS_ASSET_VERSION"), "version gating must be removed"

    codex_home = tmp_path / "home"
    codex_home.mkdir()
    script = tmp_path / "gate.py"
    script.write_text("x", encoding="utf-8")

    codex_mod._write_codex_home_config(
        codex_home,
        tmp_path / "ws",
        stop_hook_scripts=[(script, 30)],
    )
    text = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert "[[hooks.Stop]]" in text
    assert "gate.py" in text
    assert "timeout = 30" in text


def test_codex_config_omits_hooks_when_none(tmp_path: Path) -> None:
    from research_ideation_agent.adapters import codex as codex_mod

    codex_home = tmp_path / "home"
    codex_home.mkdir()
    codex_mod._write_codex_home_config(codex_home, tmp_path / "ws")
    text = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert "[[hooks.Stop]]" not in text
