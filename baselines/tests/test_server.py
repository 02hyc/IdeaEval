"""Unit tests for log_viewer.server helpers."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest

from research_ideation_agent.log_viewer import server
from research_ideation_agent.log_viewer.server import build_handler_class


def test_read_events_from_zero_returns_all(synthetic_run_dir: Path) -> None:
    jsonl = synthetic_run_dir / "framework" / "logs" / "adapter_events.jsonl"
    events, next_index, total = server.read_events(jsonl, start=0)
    assert len(events) == 3
    assert events[0]["event_index"] == 1
    assert next_index == 3
    assert total == 3


def test_read_events_from_midpoint(synthetic_run_dir: Path) -> None:
    jsonl = synthetic_run_dir / "framework" / "logs" / "adapter_events.jsonl"
    events, next_index, total = server.read_events(jsonl, start=2)
    assert len(events) == 1
    assert events[0]["event_index"] == 3
    assert next_index == 3
    assert total == 3


def test_read_events_from_eof(synthetic_run_dir: Path) -> None:
    jsonl = synthetic_run_dir / "framework" / "logs" / "adapter_events.jsonl"
    events, next_index, total = server.read_events(jsonl, start=3)
    assert events == []
    assert next_index == 3
    assert total == 3


def test_read_events_skips_malformed_but_advances_index(tmp_path: Path) -> None:
    jsonl = tmp_path / "broken.jsonl"
    jsonl.write_text(
        json.dumps({"event_index": 1}) + "\n"
        + "this is not json\n"
        + json.dumps({"event_index": 3}) + "\n"
    )
    events, next_index, total = server.read_events(jsonl, start=0)
    assert [e["event_index"] for e in events] == [1, 3]
    assert next_index == 3
    assert total == 3


def test_read_events_with_limit_pages(synthetic_run_dir: Path) -> None:
    jsonl = synthetic_run_dir / "framework" / "logs" / "adapter_events.jsonl"
    events, next_index, total = server.read_events(jsonl, start=0, limit=2)
    assert len(events) == 2
    assert next_index == 2
    assert total == 3
    events2, next_index2, _ = server.read_events(jsonl, start=next_index, limit=2)
    assert len(events2) == 1
    assert next_index2 == 3


def test_read_run_info_with_completed_record(synthetic_run_dir: Path) -> None:
    info = server.read_run_info(synthetic_run_dir)
    assert info["run_id"] == "test-run"
    assert info["status"] == "succeeded"
    assert info["model"] == "opus-4-6"
    assert info["topic"] == "test topic"


def test_read_run_info_running_when_no_record(running_run_dir: Path) -> None:
    info = server.read_run_info(running_run_dir)
    assert info == {"status": "running"}


def test_read_run_info_running_when_record_unparseable(tmp_path: Path) -> None:
    (tmp_path / "run_record.json").write_text("{ partial json")
    info = server.read_run_info(tmp_path)
    assert info == {"status": "running"}


def test_locate_jsonl_returns_expected_path(synthetic_run_dir: Path) -> None:
    found = server.locate_jsonl(synthetic_run_dir)
    assert found == synthetic_run_dir / "framework" / "logs" / "adapter_events.jsonl"


def test_locate_jsonl_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        server.locate_jsonl(tmp_path)


def test_file_complete_true_when_status_terminal(synthetic_run_dir: Path) -> None:
    assert server.file_complete(synthetic_run_dir) is True


def test_file_complete_false_when_status_running(running_run_dir: Path) -> None:
    assert server.file_complete(running_run_dir) is False


# ----------------------------------------------------------------------
# Run-list helpers
# ----------------------------------------------------------------------


def test_is_run_dir_true_for_synthetic(synthetic_run_dir: Path) -> None:
    assert server.is_run_dir(synthetic_run_dir) is True


def test_is_run_dir_false_for_empty(tmp_path: Path) -> None:
    assert server.is_run_dir(tmp_path) is False


def test_resolve_runs_root_with_runs_root_dir(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    assert server.resolve_runs_root(runs_root) == runs_root.resolve()


def test_resolve_runs_root_with_run_dir_returns_parent(synthetic_run_dir: Path) -> None:
    assert server.resolve_runs_root(synthetic_run_dir) == synthetic_run_dir.parent.resolve()


def test_resolve_runs_root_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        server.resolve_runs_root(tmp_path / "does-not-exist")


def test_resolve_runs_root_autodiscover(tmp_path: Path) -> None:
    runs = tmp_path / ".runs"
    runs.mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert server.resolve_runs_root(None, cwd=nested) == runs.resolve()


def test_list_runs_returns_summaries(tmp_path: Path) -> None:
    for i, status in enumerate(["succeeded", "running"]):
        rd = tmp_path / f"run-{i}"
        (rd / "framework" / "logs").mkdir(parents=True)
        (rd / "framework" / "logs" / "adapter_events.jsonl").write_text("")
        if status != "running":
            (rd / "run_record.json").write_text(json.dumps({
                "run_id": f"run-{i}",
                "status": status,
                "started_at": f"2026-06-{10 + i}T00:00:00Z",
                "topic": f"topic {i}",
                "model": "opus-4-6",
            }))
    runs = server.list_runs(tmp_path)
    assert len(runs) == 2
    statuses = {r["run_id"]: r["status"] for r in runs}
    assert statuses["run-0"] == "succeeded"
    assert statuses["run-1"] == "running"


def test_list_runs_skips_non_run_subdirs(tmp_path: Path) -> None:
    (tmp_path / "not-a-run").mkdir()
    rd = tmp_path / "real"
    (rd / "framework" / "logs").mkdir(parents=True)
    (rd / "framework" / "logs" / "adapter_events.jsonl").write_text("")
    runs = server.list_runs(tmp_path)
    assert [r["run_id"] for r in runs] == ["real"]


def test_list_runs_empty_for_missing_root(tmp_path: Path) -> None:
    assert server.list_runs(tmp_path / "no-such-thing") == []


def test_find_run_dir_by_directory_name(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    found = server.find_run_dir(runs_root, synthetic_run_dir.name)
    assert found == synthetic_run_dir


def test_find_run_dir_unknown_raises(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    with pytest.raises(FileNotFoundError):
        server.find_run_dir(runs_root, "no-such-run")


# ----------------------------------------------------------------------
# HTTP handler — multi-run protocol
# ----------------------------------------------------------------------


def _serve(runs_root: Path) -> tuple[HTTPServer, str]:
    handler_cls = build_handler_class(runs_root)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{port}"


def _get(base: str, path: str) -> tuple[int, dict[str, Any]]:
    with urllib.request.urlopen(base + path) as resp:
        body = resp.read().decode()
        return resp.status, json.loads(body)


def test_api_runs_lists_runs(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        status, body = _get(base, "/api/runs")
        assert status == 200
        # synthetic_run_dir has a record with run_id="test-run"
        assert any(r["run_id"] == "test-run" for r in body["runs"])
    finally:
        httpd.shutdown()


def test_api_runs_includes_harness(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        _, body = _get(base, "/api/runs")
        match = next(r for r in body["runs"] if r["run_id"] == "test-run")
        assert match["harness"] == "claude"
    finally:
        httpd.shutdown()


def test_api_runs_empty_when_root_missing(tmp_path: Path) -> None:
    httpd, base = _serve(tmp_path / "no-such-thing")
    try:
        _, body = _get(base, "/api/runs")
        assert body["runs"] == []
    finally:
        httpd.shutdown()


def test_api_run_info_with_run_query(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        status, body = _get(base, f"/api/run-info?run={synthetic_run_dir.name}")
        assert status == 200
        assert body["run_id"] == "test-run"
        assert body["status"] == "succeeded"
    finally:
        httpd.shutdown()


def test_api_run_info_missing_run_param_returns_400(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/api/run-info")
        assert exc_info.value.code == 400
    finally:
        httpd.shutdown()


def test_api_run_info_unknown_run_returns_404(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/api/run-info?run=does-not-exist")
        assert exc_info.value.code == 404
    finally:
        httpd.shutdown()


def test_api_events_with_run(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        status, body = _get(base, f"/api/events?run={synthetic_run_dir.name}&from=0")
        assert status == 200
        assert len(body["events"]) == 3
        assert body["next_index"] == 3
        assert body["file_complete"] is True
    finally:
        httpd.shutdown()


def test_api_events_from_midpoint(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        status, body = _get(base, f"/api/events?run={synthetic_run_dir.name}&from=2")
        assert status == 200
        assert len(body["events"]) == 1
        assert body["events"][0]["event_index"] == 3
        assert body["next_index"] == 3
    finally:
        httpd.shutdown()


def test_api_events_file_complete_false_for_running(running_run_dir: Path) -> None:
    runs_root = running_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        _, body = _get(base, f"/api/events?run={running_run_dir.name}&from=0")
        assert body["file_complete"] is False
    finally:
        httpd.shutdown()


def test_api_events_default_from_is_zero(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        status, body = _get(base, f"/api/events?run={synthetic_run_dir.name}")
        assert status == 200
        assert len(body["events"]) == 3
    finally:
        httpd.shutdown()


def test_api_events_invalid_from_returns_400(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(
                base + f"/api/events?run={synthetic_run_dir.name}&from=abc"
            )
        assert exc_info.value.code == 400
    finally:
        httpd.shutdown()


def test_unknown_path_returns_404(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/nope")
        assert exc_info.value.code == 404
    finally:
        httpd.shutdown()


def test_root_returns_html(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with urllib.request.urlopen(base + "/") as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
            body = resp.read().decode()
            assert "<title>" in body
            assert "/static/app.js" in body
    finally:
        httpd.shutdown()


def test_static_js_served(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with urllib.request.urlopen(base + "/static/app.js") as resp:
            assert resp.status == 200
            assert "javascript" in resp.headers["Content-Type"]
    finally:
        httpd.shutdown()


def test_static_css_served(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with urllib.request.urlopen(base + "/static/style.css") as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/css")
    finally:
        httpd.shutdown()


def test_static_traversal_blocked(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/static/../server.py")
        assert exc_info.value.code in (400, 404)
    finally:
        httpd.shutdown()


# ----------------------------------------------------------------------
# /api/stats
# ----------------------------------------------------------------------


def test_api_stats_returns_payload(synthetic_run_dir: Path) -> None:
    server._stats_cache_clear()
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        status, body = _get(base, f"/api/stats?run={synthetic_run_dir.name}")
        assert status == 200
        assert body["schema_version"] == 1
        assert body["harness"] == "claude"
        assert "tokens" in body["stats"]
        assert "cost" in body["stats"]
        assert "tool_counts" in body["stats"]
        # Bash counted once at top level
        rows = body["stats"]["tool_counts"]["data"]["rows"]
        names = {r["name"] for r in rows}
        assert "Bash" in names
        assert body["stats"]["tool_counts"]["data"]["totals"]["all"] == 1
    finally:
        httpd.shutdown()


def test_api_stats_caches_until_jsonl_changes(synthetic_run_dir: Path) -> None:
    """A second request without file mutations must not re-run compute_stats."""
    server._stats_cache_clear()
    runs_root = synthetic_run_dir.parent
    import research_ideation_agent.log_viewer.stats as stats_mod

    call_count = {"n": 0}
    real_compute = stats_mod.compute_stats

    def counting_compute(*args, **kwargs):
        call_count["n"] += 1
        return real_compute(*args, **kwargs)

    stats_mod.compute_stats = counting_compute
    try:
        httpd, base = _serve(runs_root)
        try:
            _get(base, f"/api/stats?run={synthetic_run_dir.name}")
            _get(base, f"/api/stats?run={synthetic_run_dir.name}")
            assert call_count["n"] == 1
        finally:
            httpd.shutdown()
    finally:
        stats_mod.compute_stats = real_compute


def test_api_stats_cache_invalidates_on_jsonl_append(synthetic_run_dir: Path) -> None:
    server._stats_cache_clear()
    runs_root = synthetic_run_dir.parent
    import research_ideation_agent.log_viewer.stats as stats_mod

    call_count = {"n": 0}
    real_compute = stats_mod.compute_stats

    def counting_compute(*args, **kwargs):
        call_count["n"] += 1
        return real_compute(*args, **kwargs)

    stats_mod.compute_stats = counting_compute
    try:
        httpd, base = _serve(runs_root)
        try:
            _get(base, f"/api/stats?run={synthetic_run_dir.name}")
            jsonl = synthetic_run_dir / "framework" / "logs" / "adapter_events.jsonl"
            with jsonl.open("a") as fh:
                fh.write(json.dumps({"type": "AssistantMessage", "message": {"content": [{"id": "x", "name": "Read", "input": {}}], "parent_tool_use_id": None}}) + "\n")
            _get(base, f"/api/stats?run={synthetic_run_dir.name}")
            assert call_count["n"] == 2
        finally:
            httpd.shutdown()
    finally:
        stats_mod.compute_stats = real_compute


def test_api_stats_missing_run_param_returns_400(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/api/stats")
        assert exc_info.value.code == 400
    finally:
        httpd.shutdown()


def test_api_stats_unknown_run_returns_404(synthetic_run_dir: Path) -> None:
    runs_root = synthetic_run_dir.parent
    httpd, base = _serve(runs_root)
    try:
        with pytest.raises(urllib.request.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/api/stats?run=does-not-exist")
        assert exc_info.value.code == 404
    finally:
        httpd.shutdown()

