"""Smoke tests for the viz CLI."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import SAMPLE_EVENTS


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(url: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5).read()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.1)
    raise TimeoutError(f"server at {url} did not start: {last_err}")


def _make_runs_root(tmp_path: Path) -> Path:
    runs_root = tmp_path / ".runs"
    rd = runs_root / "test-run-1"
    (rd / "framework" / "logs").mkdir(parents=True)
    (rd / "framework" / "logs" / "adapter_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in SAMPLE_EVENTS) + "\n"
    )
    (rd / "run_record.json").write_text(
        json.dumps({"run_id": "test-run-1", "status": "succeeded"})
    )
    return runs_root


def test_cli_serves_runs_list_and_events(tmp_path: Path) -> None:
    runs_root = _make_runs_root(tmp_path)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "research_ideation_agent.log_viewer", "--no-open", f"--port={port}", str(runs_root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_for(base + "/api/runs")
        with urllib.request.urlopen(base + "/api/runs") as resp:
            body = json.loads(resp.read())
        assert any(r["run_id"] == "test-run-1" for r in body["runs"])
        with urllib.request.urlopen(base + "/api/events?run=test-run-1&from=0") as resp:
            body = json.loads(resp.read())
        assert len(body["events"]) == 3
        assert body["file_complete"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_cli_accepts_run_dir_uses_parent(tmp_path: Path) -> None:
    runs_root = _make_runs_root(tmp_path)
    run_dir = runs_root / "test-run-1"
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "research_ideation_agent.log_viewer", "--no-open", f"--port={port}", str(run_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_for(base + "/api/runs")
        with urllib.request.urlopen(base + "/api/runs") as resp:
            body = json.loads(resp.read())
        assert any(r["run_id"] == "test-run-1" for r in body["runs"])
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_cli_autodiscovers_when_no_arg(tmp_path: Path) -> None:
    runs_root = _make_runs_root(tmp_path)
    nested = tmp_path / "child"
    nested.mkdir()
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "research_ideation_agent.log_viewer", "--no-open", f"--port={port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(nested),
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_for(base + "/api/runs")
        with urllib.request.urlopen(base + "/api/runs") as resp:
            body = json.loads(resp.read())
        assert any(r["run_id"] == "test-run-1" for r in body["runs"])
        # autodiscover returned a path under runs_root
        assert body["runs_root"].endswith(".runs")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_cli_errors_when_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    proc = subprocess.run(
        [sys.executable, "-m", "research_ideation_agent.log_viewer", "--no-open", str(missing)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "does not exist" in proc.stderr.lower() or "not a directory" in proc.stderr.lower()


def test_cli_retries_busy_port(tmp_path: Path) -> None:
    runs_root = _make_runs_root(tmp_path)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy_port = blocker.getsockname()[1]

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "research_ideation_agent.log_viewer",
            "--no-open", f"--port={busy_port}", str(runs_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        chosen_port = None
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line:
                time.sleep(0.05)
                continue
            if "http://127.0.0.1:" in line:
                # Strip trailing /, whitespace, ANSI etc.
                tail = line.rsplit(":", 1)[-1].strip().rstrip("/")
                chosen_port = int(tail)
                break
        assert chosen_port is not None
        assert chosen_port != busy_port
        _wait_for(f"http://127.0.0.1:{chosen_port}/api/runs")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        blocker.close()
