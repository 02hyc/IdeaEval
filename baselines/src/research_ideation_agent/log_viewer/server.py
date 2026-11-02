"""Read-side helpers for the adapter-events log viewer.

This module is HTTP-agnostic. The HTTP request handler imports these
functions; tests cover them directly.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock
from typing import Any

JSONL_RELATIVE = Path("framework") / "logs" / "adapter_events.jsonl"
RUN_RECORD_NAME = "run_record.json"

# Pagination on /api/events. Default page size aims for fast first paint
# without producing too many round-trips on huge logs. Hard cap protects
# the server from absurd ?limit=... values.
DEFAULT_EVENT_LIMIT = 2000
MAX_EVENT_LIMIT = 50000

# A run is "complete" when run_record.json exists with one of these statuses.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timeout", "error"})

# Fields exposed via /api/run-info when run_record.json is present.
RUN_INFO_FIELDS = (
    "run_id",
    "status",
    "harness",
    "model",
    "topic",
    "started_at",
    "completed_at",
)

# Compact summary fields used in /api/runs.
RUN_LIST_FIELDS = (
    "run_id",
    "status",
    "harness",
    "model",
    "topic",
    "started_at",
    "completed_at",
)

STATIC_DIR = Path(__file__).parent / "static"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


# ----------------------------------------------------------------------
# Run-directory utilities
# ----------------------------------------------------------------------


def is_run_dir(path: Path) -> bool:
    """A run dir contains framework/logs/adapter_events.jsonl."""
    return (path / JSONL_RELATIVE).is_file()


def discover_runs_root(start: Path) -> Path:
    """Walk up from `start` to find a directory named '.runs' containing run dirs.

    Returns the first match. Falls back to `start / '.runs'` even if missing,
    so callers can handle the empty case in CLI/error paths.
    """
    cur = start.resolve()
    while True:
        candidate = cur / ".runs"
        if candidate.is_dir():
            return candidate
        if cur.parent == cur:
            return start.resolve() / ".runs"
        cur = cur.parent


def resolve_runs_root(arg: Path | None, cwd: Path | None = None) -> Path:
    """Resolve the runs-root from a CLI argument.

    Rules:
    - None              → autodiscover up from cwd
    - existing dir that IS itself a run_dir → return its parent
    - any other existing dir → that dir
    - non-existent path → raise FileNotFoundError
    """
    if cwd is None:
        cwd = Path.cwd()
    if arg is None:
        return discover_runs_root(cwd)
    arg = arg.expanduser().resolve()
    if not arg.exists():
        raise FileNotFoundError(f"path does not exist: {arg}")
    if not arg.is_dir():
        raise NotADirectoryError(f"not a directory: {arg}")
    if is_run_dir(arg):
        return arg.parent
    return arg


def _iter_run_dirs(runs_root: Path) -> list[Path]:
    """Run dirs under runs_root for both layouts: flat (<run-id>/) and grouped by
    model (<model>/<run-id>/). A run dir is identified by is_run_dir()."""
    dirs: list[Path] = []
    if not runs_root.is_dir():
        return dirs
    for child in sorted(runs_root.iterdir()):
        if not child.is_dir():
            continue
        if is_run_dir(child):
            dirs.append(child)
        else:
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and is_run_dir(grandchild):
                    dirs.append(grandchild)
    return dirs


def list_runs(runs_root: Path) -> list[dict[str, Any]]:
    """Return run summaries for every run dir under `runs_root` (flat or model-grouped).

    Sorted by started_at descending (newest first); runs without a record fall
    back to the directory mtime.
    """
    if not runs_root.is_dir():
        return []
    rows: list[tuple[str, dict[str, Any]]] = []
    for child in _iter_run_dirs(runs_root):
        record = _load_run_record(child)
        summary: dict[str, Any] = {
            "run_id": child.name,
            "path": str(child),
            "has_record": record is not None,
        }
        if record is not None:
            for field in RUN_LIST_FIELDS:
                if field in record:
                    summary[field] = record.get(field)
            # If the record has its own run_id, prefer it; otherwise fall back to dir name.
            summary["run_id"] = record.get("run_id") or child.name
            sort_key = record.get("started_at") or ""
        else:
            summary["status"] = "running"
            sort_key = ""
        # Stable secondary sort by mtime so unrecorded runs still group naturally.
        sort_key = sort_key or child.stat().st_mtime
        rows.append((str(sort_key), summary))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


def find_run_dir(runs_root: Path, run_id: str) -> Path:
    """Locate a run dir by id (match on directory name, then on record run_id)."""
    direct = runs_root / run_id
    if is_run_dir(direct):
        return direct
    for nested in sorted(runs_root.glob(f"*/{run_id}")):
        if is_run_dir(nested):
            return nested
    # Fallback: scan and match record.run_id
    for child in _iter_run_dirs(runs_root):
        record = _load_run_record(child)
        if record and record.get("run_id") == run_id:
            return child
    raise FileNotFoundError(f"no run with id {run_id!r} under {runs_root}")


# ----------------------------------------------------------------------
# Single-run readers
# ----------------------------------------------------------------------


def locate_jsonl(run_dir: Path) -> Path:
    """Return the adapter_events.jsonl path inside run_dir, or raise FileNotFoundError."""
    candidate = run_dir / JSONL_RELATIVE
    if not candidate.is_file():
        raise FileNotFoundError(f"adapter_events.jsonl not found at {candidate}")
    return candidate


def read_events(jsonl_path: Path, start: int, limit: int | None = None) -> tuple[list[dict[str, Any]], int, int]:
    """Read JSONL lines from index `start` (0-based) onward.

    If `limit` is provided, returns at most `limit` events and `next_index`
    points at the line just past the last consumed one (so callers can
    keep paging). Malformed lines are skipped (with a stderr warning) but
    still advance next_index, so the caller's cursor stays in sync with
    file line numbering.

    Returns (events, next_index, total_lines).
    """
    text = jsonl_path.read_text()
    if not text:
        return [], start, 0
    lines = text.splitlines()
    total = len(lines)
    events: list[dict[str, Any]] = []
    line_no = start
    while line_no < total:
        if limit is not None and len(events) >= limit:
            break
        raw = lines[line_no]
        line_no += 1
        if not raw.strip():
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            print(
                f"log_viewer: skipping malformed line {line_no - 1} of {jsonl_path}: {exc}",
                file=sys.stderr,
            )
    return events, line_no, total


def _load_run_record(run_dir: Path) -> dict[str, Any] | None:
    record_path = run_dir / RUN_RECORD_NAME
    if not record_path.is_file():
        return None
    try:
        return json.loads(record_path.read_text())
    except json.JSONDecodeError:
        return None


def read_run_info(run_dir: Path) -> dict[str, Any]:
    """Return a small dict of run metadata for /api/run-info."""
    record = _load_run_record(run_dir)
    if record is None:
        return {"status": "running"}
    return {field: record.get(field) for field in RUN_INFO_FIELDS if field in record}


def file_complete(run_dir: Path) -> bool:
    """True iff run_record.json exists with a terminal status."""
    record = _load_run_record(run_dir)
    if record is None:
        return False
    return record.get("status") in TERMINAL_STATUSES


# ----------------------------------------------------------------------
# Stats cache — keyed by jsonl mtime+size and run_record mtime so a
# terminal run hits in O(1) and a still-running run naturally re-keys
# on each modification.
# ----------------------------------------------------------------------

_STATS_CACHE_MAX = 32
_stats_cache: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
_stats_cache_lock = Lock()


def _stats_cache_key(run_dir: Path) -> tuple | None:
    try:
        jsonl_path = run_dir / JSONL_RELATIVE
        jsonl_stat = jsonl_path.stat()
    except OSError:
        return None
    record_path = run_dir / RUN_RECORD_NAME
    try:
        record_mtime = record_path.stat().st_mtime_ns
    except OSError:
        record_mtime = 0
    return (
        str(jsonl_path),
        jsonl_stat.st_size,
        jsonl_stat.st_mtime_ns,
        record_mtime,
    )


def get_cached_stats(run_dir: Path) -> dict[str, Any] | None:
    key = _stats_cache_key(run_dir)
    if key is None:
        return None
    with _stats_cache_lock:
        return _stats_cache.get(key)


def store_cached_stats(run_dir: Path, payload: dict[str, Any]) -> None:
    key = _stats_cache_key(run_dir)
    if key is None:
        return
    with _stats_cache_lock:
        _stats_cache[key] = payload
        _stats_cache.move_to_end(key)
        while len(_stats_cache) > _STATS_CACHE_MAX:
            _stats_cache.popitem(last=False)


def _stats_cache_clear() -> None:
    """Test hook — wipe the cache between runs."""
    with _stats_cache_lock:
        _stats_cache.clear()


# ----------------------------------------------------------------------
# HTTP handler factory
# ----------------------------------------------------------------------


def build_handler_class(runs_root: Path) -> type[BaseHTTPRequestHandler]:
    """Return a BaseHTTPRequestHandler subclass bound to a runs-root directory.

    Endpoints:
        GET /                       → static SPA
        GET /static/<path>          → static asset
        GET /api/runs               → list of runs
        GET /api/run-info?run=<id>  → metadata for one run
        GET /api/events?run=<id>&from=<N>  → events for one run
    """
    # We don't require runs_root to exist at startup — a fresh project may
    # not have produced any runs yet. The /api/runs endpoint will return [].
    runs_root = runs_root.resolve()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: HTTPStatus, payload: Any) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"error": message})

        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._send_static(STATIC_DIR / "index.html")
                return
            if parsed.path.startswith("/static/"):
                self._serve_static(parsed.path[len("/static/"):])
                return
            if parsed.path == "/api/runs":
                self._send_json(
                    HTTPStatus.OK,
                    {"runs_root": str(runs_root), "runs": list_runs(runs_root)},
                )
                return
            if parsed.path == "/api/run-info":
                self._handle_run_info(parsed.query)
                return
            if parsed.path == "/api/events":
                self._handle_events(parsed.query)
                return
            if parsed.path == "/api/stats":
                self._handle_stats(parsed.query)
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, f"unknown path: {parsed.path}")

        def _resolve_run(self, query: str) -> Path | None:
            params = urllib.parse.parse_qs(query)
            run_id = params.get("run", [None])[0]
            if not run_id:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "missing 'run' query param")
                return None
            try:
                return find_run_dir(runs_root, run_id)
            except FileNotFoundError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return None

        def _handle_run_info(self, query: str) -> None:
            run_dir = self._resolve_run(query)
            if run_dir is None:
                return
            self._send_json(HTTPStatus.OK, read_run_info(run_dir))

        def _handle_stats(self, query: str) -> None:
            run_dir = self._resolve_run(query)
            if run_dir is None:
                return
            cached = get_cached_stats(run_dir)
            if cached is not None:
                self._send_json(HTTPStatus.OK, cached)
                return
            # Lazy import — keeps server.py import-light.
            from research_ideation_agent.log_viewer.stats import compute_stats

            try:
                payload = compute_stats(run_dir)
            except Exception as exc:  # noqa: BLE001 — we want a clean 500
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"stats computation failed: {exc}",
                )
                return
            store_cached_stats(run_dir, payload)
            self._send_json(HTTPStatus.OK, payload)

        def _handle_events(self, query: str) -> None:
            run_dir = self._resolve_run(query)
            if run_dir is None:
                return
            params = urllib.parse.parse_qs(query)
            raw_from = params.get("from", ["0"])[0]
            try:
                start = int(raw_from)
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, f"invalid from: {raw_from!r}")
                return
            if start < 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, f"from must be >= 0: {start}")
                return
            limit: int | None = DEFAULT_EVENT_LIMIT
            raw_limit = params.get("limit", [None])[0]
            if raw_limit is not None:
                try:
                    parsed = int(raw_limit)
                except ValueError:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, f"invalid limit: {raw_limit!r}")
                    return
                if parsed <= 0:
                    limit = None  # explicit opt-out for callers that want everything
                else:
                    limit = min(parsed, MAX_EVENT_LIMIT)
            try:
                jsonl_path = locate_jsonl(run_dir)
            except FileNotFoundError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            events, next_index, total_lines = read_events(jsonl_path, start=start, limit=limit)
            # eof = "no more events to read right now". When file_complete is also
            # true, the run is fully done.
            eof = next_index >= total_lines
            self._send_json(
                HTTPStatus.OK,
                {
                    "events": events,
                    "next_index": next_index,
                    "eof": eof,
                    "file_complete": file_complete(run_dir),
                },
            )

        def _serve_static(self, rel_path: str) -> None:
            target = (STATIC_DIR / rel_path).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not target.is_file():
                self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            self._send_static(target)

        def _send_static(self, path: Path) -> None:
            data = path.read_bytes()
            content_type = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

    return _Handler
