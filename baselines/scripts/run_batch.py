#!/usr/bin/env python3
"""Run a batch of inputs through the research-ideation CLI with concurrency, retries, and k-sample replication.

Input JSON shape (default location: ``inputs/<file>.json``):

    {"inputs": [
      {"id": "c001", "topic": "...", "date": "2024-12-31"},
      ...
    ]}

When ``--samples > 1``, each input expands to ``--samples`` runs with ids
``<id>-01..<id>-kk`` (the suffix width auto-widens for k > 99). When
``--samples == 1`` the run id is just ``<id>``. ``date`` is forwarded to the
CLI as ``--set mcp.scholar_gateway.cutoff_date=<date>`` and ``topic`` is
written to a per-run topic file passed via ``--topic-file``.

Outputs land under ``runs/<batch_id>/`` by default (override with ``--name``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = REPO_ROOT / "inputs"
DEFAULT_OUTPUT_PARENT = REPO_ROOT / "runs"
DEFAULT_BATCH_PREFIX = "batch"
STATE_FILENAME = "state.json"
EVENTS_FILENAME = "events.jsonl"


@dataclass
class RunItem:
    index: int
    run_id: str
    source_id: str
    sample: int
    topic: str
    date: str
    attempts_started: int = 0


@dataclass
class RunningRun:
    item: RunItem
    attempt: int
    process: subprocess.Popen[bytes]
    stdout_path: Path
    stderr_path: Path
    command: list[str]
    started_at: str


class Supervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.input_path = Path(args.input).resolve()
        self.config_path = Path(args.config).resolve()
        self.workspace_root = Path(args.workspace_root).resolve()
        self.batch_dir = Path(args.batch_dir).resolve()
        self.logs_dir = self.batch_dir / "logs"
        self.topic_files_dir = self.batch_dir / "topic_files"
        self.state_path = self.batch_dir / STATE_FILENAME
        self.events_path = self.batch_dir / EVENTS_FILENAME
        self.uv = args.uv
        self.model_profile = getattr(args, "model_profile", None)
        self.stop_requested = False
        self.running: dict[str, RunningRun] = {}
        self.state: dict[str, Any] = {}

    def run(self) -> int:
        self._install_signal_handlers()
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        self._guard_existing_batch_metadata()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.topic_files_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        items = expand_items(load_inputs(self.input_path), self.args.samples)
        pending: deque[RunItem] = deque(items)
        self.state = self._initial_state(items)
        self._write_state()
        self._event(
            "batch_started",
            total=len(items),
            concurrency=self.args.concurrency,
            samples=self.args.samples,
            config=str(self.config_path),
            model_profile=self.model_profile,
            workspace_root=str(self.workspace_root),
        )

        try:
            while (pending or self.running) and not self.stop_requested:
                launched = self._launch_available(pending)
                if launched:
                    self._write_state()
                self._collect_finished(pending)
                if self.running:
                    time.sleep(self.args.poll_interval_seconds)
                elif pending:
                    time.sleep(0.2)
        finally:
            if self.stop_requested and self.running:
                self._terminate_running()
            self._finalize_state()
            self._write_state()
            self._event("batch_finished", counts=self._counts())

        counts = self._counts()
        if counts.get("failed", 0) or counts.get("cancelled", 0):
            return 1
        return 0

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

        def _request_stop(signum: int, _frame: object) -> None:
            self.stop_requested = True
            self._event("stop_requested", signal=signum)

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

    def _guard_existing_batch_metadata(self) -> None:
        """Refuse to clobber a previous batch's metadata unless --overwrite is given.

        The supervisor writes state.json (atomic replace) and events.jsonl (append-mode).
        Re-using the same --name without warning would silently lose the prior state.json
        and intermix two batches' events. --overwrite explicitly opts into rotation.
        """
        if not self.state_path.is_file() and not self.events_path.is_file():
            return
        if not self.args.overwrite:
            raise SystemExit(
                f"batch metadata already exists at {self.batch_dir}\n"
                "Re-running with the same --name would overwrite state.json and append to events.jsonl.\n"
                "Pass --overwrite to rotate the prior batch metadata to a timestamped backup, "
                "or pick a different --name."
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        for path in (self.state_path, self.events_path):
            if path.is_file():
                path.rename(path.with_name(f"{path.name}.{stamp}.bak"))

    def _initial_state(self, items: list[RunItem]) -> dict[str, Any]:
        now = utc_now()
        return {
            "batch_id": self.args.batch_id,
            "pid": os.getpid(),
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "input_file": str(self.input_path),
            "config": str(self.config_path),
            "workspace_root": str(self.workspace_root),
            "batch_dir": str(self.batch_dir),
            "concurrency": self.args.concurrency,
            "retries": self.args.retries,
            "samples": self.args.samples,
            "force": self.args.force,
            "total": len(items),
            "counts": {},
            "metrics": empty_batch_metrics(),
            "items": {
                item.run_id: {
                    "index": item.index,
                    "run_id": item.run_id,
                    "source_id": item.source_id,
                    "sample": item.sample,
                    "topic": item.topic,
                    "date": item.date,
                    "status": "queued",
                    "attempts_started": 0,
                    "exit_code": None,
                    "failure_kind": None,
                    "validation_valid": None,
                    "started_at": None,
                    "finished_at": None,
                    "proposal_path": fallback_proposal_path(self.workspace_root, item.run_id),
                    "run_record_path": str(run_record_path(self.workspace_root, item.run_id)),
                    "topic_file_path": str(topic_file_path(self.topic_files_dir, item.run_id)),
                    "stdout_path": None,
                    "stderr_path": None,
                    "error": None,
                    "metrics": empty_run_metrics(),
                }
                for item in items
            },
        }

    def _launch_available(self, pending: deque[RunItem]) -> bool:
        launched = False
        while pending and len(self.running) < self.args.concurrency and not self.stop_requested:
            item = pending.popleft()
            if self._should_skip_success(item):
                launched = True
                continue
            self._start_item(item)
            launched = True
            if self.args.launch_delay_seconds > 0 and len(self.running) < self.args.concurrency:
                time.sleep(self.args.launch_delay_seconds)
        return launched

    def _should_skip_success(self, item: RunItem) -> bool:
        if self.args.force:
            return False
        inspection = inspect_run(self.workspace_root, item.run_id)
        if not inspection["success"]:
            return False
        entry = self.state["items"][item.run_id]
        entry.update(
            {
                "status": "skipped_existing_success",
                "validation_valid": inspection.get("validation_valid"),
                "failure_kind": inspection.get("failure_kind"),
                "finished_at": utc_now(),
                "error": None,
                "metrics": inspection.get("metrics") or empty_run_metrics(),
            }
        )
        self._event("skipped_existing_success", run_id=item.run_id)
        return True

    def _start_item(self, item: RunItem) -> None:
        item.attempts_started += 1
        attempt = item.attempts_started
        stdout_path = self.logs_dir / f"{item.run_id}.attempt{attempt}.stdout.json"
        stderr_path = self.logs_dir / f"{item.run_id}.attempt{attempt}.stderr.log"
        input_topic_path = topic_file_path(self.topic_files_dir, item.run_id)
        input_topic_path.write_text(item.topic, encoding="utf-8")
        run_path = find_run_dir(self.workspace_root, item.run_id)
        clean_existing = self.args.force or run_path.exists() or attempt > 1
        command = build_run_command(
            uv=self.uv,
            config_path=self.config_path,
            workspace_root=self.workspace_root,
            item=item,
            input_topic_path=input_topic_path,
            clean_existing=clean_existing,
            model_profile=self.model_profile,
        )
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
        started_at = utc_now()
        self.running[item.run_id] = RunningRun(
            item=item,
            attempt=attempt,
            process=process,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            command=command,
            started_at=started_at,
        )
        entry = self.state["items"][item.run_id]
        entry.update(
            {
                "status": "running",
                "attempts_started": attempt,
                "started_at": started_at,
                "finished_at": None,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "error": None,
            }
        )
        self._event(
            "run_started",
            run_id=item.run_id,
            attempt=attempt,
            pid=process.pid,
            cutoff_date=item.date,
            command=shell_join(command),
        )

    def _collect_finished(self, pending: deque[RunItem]) -> None:
        finished = [run_id for run_id, run in self.running.items() if run.process.poll() is not None]
        for run_id in finished:
            run = self.running.pop(run_id)
            exit_code = run.process.returncode
            inspection = inspect_run(self.workspace_root, run.item.run_id)
            success = bool(exit_code == 0 and inspection["success"])
            entry = self.state["items"][run_id]
            entry.update(
                {
                    "exit_code": exit_code,
                    "failure_kind": inspection.get("failure_kind"),
                    "validation_valid": inspection.get("validation_valid"),
                    "finished_at": utc_now(),
                    "proposal_path": inspection.get("proposal_path") or fallback_proposal_path(self.workspace_root, run_id),
                    "run_record_path": str(run_record_path(self.workspace_root, run_id)),
                    "metrics": inspection.get("metrics") or empty_run_metrics(),
                }
            )
            if success:
                entry["status"] = "succeeded"
                entry["error"] = None
                self._event("run_succeeded", run_id=run_id, attempt=run.attempt, exit_code=exit_code)
            elif run.item.attempts_started <= self.args.retries:
                entry["status"] = "queued"
                entry["error"] = failure_message(exit_code, inspection, run.stderr_path)
                pending.append(run.item)
                self._event(
                    "run_failed_requeued",
                    run_id=run_id,
                    attempt=run.attempt,
                    exit_code=exit_code,
                    next_attempt=run.item.attempts_started + 1,
                    error=entry["error"],
                )
            else:
                entry["status"] = "failed"
                entry["error"] = failure_message(exit_code, inspection, run.stderr_path)
                self._event(
                    "run_failed_final",
                    run_id=run_id,
                    attempt=run.attempt,
                    exit_code=exit_code,
                    error=entry["error"],
                )
            self._write_state()

    def _terminate_running(self) -> None:
        for run_id, run in list(self.running.items()):
            self._event("terminating_run", run_id=run_id, pid=run.process.pid)
            terminate_process_group(run.process)
        deadline = time.monotonic() + 30
        for run_id, run in list(self.running.items()):
            while run.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if run.process.poll() is None:
                kill_process_group(run.process)
            try:
                exit_code = run.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                exit_code = run.process.returncode
            entry = self.state["items"][run_id]
            entry.update(
                {
                    "status": "cancelled",
                    "exit_code": exit_code,
                    "finished_at": utc_now(),
                    "error": "Supervisor stopped before this run completed.",
                }
            )
        self.running.clear()

    def _finalize_state(self) -> None:
        if self.stop_requested:
            for entry in self.state["items"].values():
                if entry["status"] == "queued":
                    entry["status"] = "cancelled"
                    entry["error"] = "Supervisor stopped before this run started."
        self.state["finished_at"] = utc_now()
        self.state["updated_at"] = utc_now()
        self.state["counts"] = self._counts()
        self.state["metrics"] = aggregate_batch_metrics(self.state.get("items", {}))

    def _counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.state.get("items", {}).values():
            status = entry["status"]
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _write_state(self) -> None:
        if not self.state:
            return
        self.state["updated_at"] = utc_now()
        self.state["counts"] = self._counts()
        self.state["metrics"] = aggregate_batch_metrics(self.state.get("items", {}))
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self.state_path)

    def _event(self, event_type: str, **payload: Any) -> None:
        if not hasattr(self, "events_path"):
            return
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": utc_now(), "event": event_type, **payload}
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, sort_keys=True) + "\n")


def build_run_command(
    *,
    uv: str,
    config_path: Path,
    workspace_root: Path,
    item: RunItem,
    input_topic_path: Path,
    clean_existing: bool,
    model_profile: str | None = None,
) -> list[str]:
    command = [
        uv,
        "run",
        "research-ideation-agent",
        "run",
        "--config",
        str(config_path),
        "--topic-file",
        str(input_topic_path),
        "--workspace-root",
        str(workspace_root),
        "--run-id",
        item.run_id,
        "--set",
        f"mcp.scholar_gateway.cutoff_date={json.dumps(item.date)}",
        "--json",
    ]
    if model_profile:
        command.extend(["--model-profile", model_profile])
    if clean_existing:
        command.extend(["--set", "workspace.clean_existing=true"])
    return command


def load_inputs(path: Path) -> list[tuple[int, str, str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Could not read input file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Input file is not valid JSON: {path}") from exc

    raw_items = data.get("inputs") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise SystemExit("Input JSON must contain an `inputs` list.")

    out: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SystemExit(f"Input item {index} must be an object.")
        source_id = required_string(raw, "id", index)
        topic = required_string(raw, "topic", index)
        date = required_string(raw, "date", index)
        validate_run_id(source_id)
        validate_date(date, index)
        if source_id in seen:
            raise SystemExit(f"Duplicate id in input JSON: {source_id}")
        seen.add(source_id)
        out.append((index, source_id, topic, date))
    return out


def expand_items(rows: list[tuple[int, str, str, str]], samples: int) -> list[RunItem]:
    items: list[RunItem] = []
    width = max(2, len(str(samples)))
    for index, source_id, topic, date in rows:
        for k in range(1, samples + 1):
            run_id = f"{source_id}-{k:0{width}d}" if samples > 1 else source_id
            items.append(
                RunItem(
                    index=index * samples + (k - 1),
                    run_id=run_id,
                    source_id=source_id,
                    sample=k,
                    topic=topic,
                    date=date,
                )
            )
    return items


def required_string(raw: dict[str, Any], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"Input item {index} has missing or empty `{key}`.")
    return value


_RUN_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def validate_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise SystemExit(
            f"id is not a safe run id: {run_id!r}; allowed chars are [A-Za-z0-9_.-] "
            "and the first char must not be `-` or `.`."
        )


def validate_date(date: str, index: int) -> None:
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"Input item {index} has invalid date {date!r}; expected YYYY-MM-DD.") from exc


def inspect_run(workspace_root: Path, run_id: str) -> dict[str, Any]:
    record = run_record_path(workspace_root, run_id)
    result: dict[str, Any] = {
        "success": False,
        "proposal_path": None,
        "run_record_exists": record.is_file(),
        "status": None,
        "failure_kind": None,
        "validation_valid": None,
        "metrics": empty_run_metrics(),
    }
    if not record.is_file():
        return result
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["failure_kind"] = "unreadable_run_record"
        result["error"] = str(exc)
        return result
    validation = data.get("validation") or {}
    proposal = (data.get("artifacts") or {}).get("proposal")
    result.update(
        {
            "status": data.get("status"),
            "failure_kind": data.get("failure_kind"),
            "validation_valid": validation.get("valid"),
            "proposal_path": proposal,
            "metrics": extract_run_metrics(data, record.parent),
        }
    )
    result["success"] = bool(data.get("status") == "succeeded" and validation.get("valid") is True)
    return result


def extract_run_metrics(run_record: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    usage = run_record.get("usage") if isinstance(run_record.get("usage"), dict) else {}
    log_path = run_dir / "framework" / "logs" / "adapter_events.jsonl"
    tool_counts, scholar_counts, event_count = count_tool_calls(log_path, run_record.get("harness"))
    references_dir = run_dir / "agent_workspace" / "references"
    references_dir_count = 0
    if references_dir.is_dir():
        references_dir_count = sum(1 for path in references_dir.iterdir() if path.is_dir())
    return {
        "harness": run_record.get("harness"),
        "model": run_record.get("model"),
        "started_at": run_record.get("started_at"),
        "completed_at": run_record.get("completed_at"),
        "wall_time_ms": duration_between_ms(run_record.get("started_at"), run_record.get("completed_at")),
        "duration_ms": usage.get("duration_ms"),
        "turns": usage.get("turns"),
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "total_cost_usd": usage.get("total_cost_usd"),
        "tool_call_count": sum(tool_counts.values()) if log_path.is_file() else None,
        "tool_call_counts": tool_counts,
        "scholar_tool_call_counts": scholar_counts,
        "event_count": event_count if log_path.is_file() else None,
        "references_dir_count": references_dir_count,
    }


def empty_run_metrics() -> dict[str, Any]:
    return {
        "harness": None,
        "model": None,
        "started_at": None,
        "completed_at": None,
        "wall_time_ms": None,
        "duration_ms": None,
        "turns": None,
        "input_tokens": None,
        "cached_input_tokens": None,
        "cache_creation_input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "total_tokens": None,
        "total_cost_usd": None,
        "tool_call_count": None,
        "tool_call_counts": {},
        "scholar_tool_call_counts": {name: 0 for name in SCHOLAR_TOOLS},
        "event_count": None,
        "references_dir_count": 0,
    }


def empty_batch_metrics() -> dict[str, Any]:
    return {
        "completed_run_count": 0,
        "total_wall_time_ms": None,
        "total_agent_duration_ms": None,
        "total_cost_usd": None,
        "total_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "tool_call_count": None,
        "tool_call_counts": {},
        "scholar_tool_call_counts": {name: 0 for name in SCHOLAR_TOOLS},
        "missing_cost_count": 0,
        "missing_usage_count": 0,
    }


def aggregate_batch_metrics(items: dict[str, Any]) -> dict[str, Any]:
    metrics = empty_batch_metrics()
    any_wall = any_duration = any_cost = any_tokens = any_tools = False
    tool_counts: dict[str, int] = {}
    scholar_counts: dict[str, int] = {name: 0 for name in SCHOLAR_TOOLS}

    for entry in items.values():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        run_metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        if status in {"succeeded", "failed", "cancelled", "skipped_existing_success"}:
            metrics["completed_run_count"] += 1

        wall = int_or_none(run_metrics.get("wall_time_ms"))
        if wall is not None:
            metrics["total_wall_time_ms"] = (metrics["total_wall_time_ms"] or 0) + wall
            any_wall = True

        duration = int_or_none(run_metrics.get("duration_ms"))
        if duration is not None:
            metrics["total_agent_duration_ms"] = (metrics["total_agent_duration_ms"] or 0) + duration
            any_duration = True

        cost = float_or_none(run_metrics.get("total_cost_usd"))
        if cost is not None:
            metrics["total_cost_usd"] = (metrics["total_cost_usd"] or 0.0) + cost
            any_cost = True
        elif status in {"succeeded", "failed"}:
            metrics["missing_cost_count"] += 1

        if not isinstance(run_metrics, dict) or run_metrics.get("total_tokens") is None:
            if status in {"succeeded", "failed"}:
                metrics["missing_usage_count"] += 1

        for source_key, target_key in (
            ("total_tokens", "total_tokens"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("reasoning_output_tokens", "reasoning_output_tokens"),
        ):
            value = int_or_none(run_metrics.get(source_key))
            if value is not None:
                metrics[target_key] = (metrics[target_key] or 0) + value
                any_tokens = True

        tool_total = int_or_none(run_metrics.get("tool_call_count"))
        if tool_total is not None:
            metrics["tool_call_count"] = (metrics["tool_call_count"] or 0) + tool_total
            any_tools = True

        for name, value in (run_metrics.get("tool_call_counts") or {}).items():
            count = int_or_none(value)
            if count is not None:
                tool_counts[str(name)] = tool_counts.get(str(name), 0) + count
        for name, value in (run_metrics.get("scholar_tool_call_counts") or {}).items():
            count = int_or_none(value)
            if count is not None:
                scholar_counts[str(name)] = scholar_counts.get(str(name), 0) + count

    if not any_wall:
        metrics["total_wall_time_ms"] = None
    if not any_duration:
        metrics["total_agent_duration_ms"] = None
    if not any_cost:
        metrics["total_cost_usd"] = None
    if not any_tokens:
        for key in ("total_tokens", "input_tokens", "output_tokens", "reasoning_output_tokens"):
            metrics[key] = None
    if not any_tools:
        metrics["tool_call_count"] = None
    metrics["tool_call_counts"] = tool_counts
    metrics["scholar_tool_call_counts"] = scholar_counts
    return metrics


SCHOLAR_TOOLS = {"web_search", "web_fetch", "extract_paper"}


def count_tool_calls(log_path: Path, harness: Any) -> tuple[dict[str, int], dict[str, int], int]:
    tool_counts: dict[str, int] = {}
    scholar_counts: dict[str, int] = {name: 0 for name in SCHOLAR_TOOLS}
    event_count = 0
    if not log_path.is_file():
        return tool_counts, scholar_counts, event_count
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return tool_counts, scholar_counts, event_count
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_count += 1
        if harness == "claude":
            for name in claude_tool_names(event):
                record_tool_call(tool_counts, scholar_counts, name)
        elif harness == "codex":
            name = codex_tool_name(event)
            if name is not None:
                record_tool_call(tool_counts, scholar_counts, name)
    return tool_counts, scholar_counts, event_count


def claude_tool_names(event: dict[str, Any]) -> list[str]:
    if event.get("type") != "AssistantMessage":
        return []
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    names: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        name = block.get("name")
        if isinstance(name, str) and "input" in block:
            names.append(name)
    return names


def codex_tool_name(event: dict[str, Any]) -> str | None:
    if event.get("type") != "item/started":
        return None
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sdk_event = message.get("event") if isinstance(message.get("event"), dict) else {}
    payload = sdk_event.get("payload") if isinstance(sdk_event.get("payload"), dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    item_type = item.get("type")
    if item_type == "mcpToolCall":
        return str(item.get("tool") or item.get("name") or "mcp")
    if item_type == "commandExecution":
        return "Bash"
    if item_type == "fileChange":
        return "Edit"
    if item_type == "webSearch":
        return "WebSearch"
    if item_type == "collabAgentToolCall":
        return "Agent"
    return None


def record_tool_call(tool_counts: dict[str, int], scholar_counts: dict[str, int], raw_name: str) -> None:
    bucket = tool_bucket(raw_name)
    tool_counts[bucket] = tool_counts.get(bucket, 0) + 1
    short = short_tool_name(raw_name)
    if short in SCHOLAR_TOOLS:
        scholar_counts[short] = scholar_counts.get(short, 0) + 1


def tool_bucket(raw_name: str) -> str:
    short = short_tool_name(raw_name)
    if raw_name.startswith("mcp__"):
        return f"mcp:{short}"
    return short


def short_tool_name(raw_name: str) -> str:
    if raw_name.startswith("mcp__"):
        parts = raw_name.split("__")
        if len(parts) >= 3:
            return parts[-1]
    return raw_name


def duration_between_ms(start: Any, end: Any) -> int | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        start_dt = parse_iso_datetime(start)
        end_dt = parse_iso_datetime(end)
    except ValueError:
        return None
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def parse_iso_datetime(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def failure_message(exit_code: int | None, inspection: dict[str, Any], stderr_path: Path) -> str:
    parts = [f"exit_code={exit_code}"]
    if inspection.get("status") is not None:
        parts.append(f"status={inspection.get('status')}")
    if inspection.get("failure_kind") is not None:
        parts.append(f"failure_kind={inspection.get('failure_kind')}")
    if inspection.get("validation_valid") is not None:
        parts.append(f"validation_valid={inspection.get('validation_valid')}")
    stderr_tail = read_tail(stderr_path)
    if stderr_tail:
        parts.append(f"stderr_tail={stderr_tail}")
    return "; ".join(parts)


def read_tail(path: Path, max_chars: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def find_run_dir(workspace_root: Path, run_id: str) -> Path:
    """Locate a run dir under workspace_root for either layout: flat (<run-id>/)
    or grouped by model (<model>/<run-id>/). Returns the existing dir, or the flat
    path as a best-guess default before the run has created it."""
    direct = workspace_root / run_id
    if direct.is_dir():
        return direct
    for nested in sorted(workspace_root.glob(f"*/{run_id}")):
        if nested.is_dir():
            return nested
    return direct


def fallback_proposal_path(workspace_root: Path, run_id: str) -> str:
    """Best-guess proposal path for state.json before run_record.json is written.

    The framework places the proposal under <run_path>/<workspace.agent_dir>/proposal.md
    where <run_path> is <workspace_root>/<model>/<run-id>; once the run finishes we
    replace this with the artifacts.proposal recorded in run_record.json.
    """
    return str(find_run_dir(workspace_root, run_id) / "agent_workspace" / "proposal.md")


def run_record_path(workspace_root: Path, run_id: str) -> Path:
    return find_run_dir(workspace_root, run_id) / "run_record.json"


def topic_file_path(topic_files_dir: Path, run_id: str) -> Path:
    return topic_files_dir / f"{run_id}.txt"


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def resolve_uv(explicit: str | None) -> str:
    if explicit:
        return explicit
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("Could not find `uv` on PATH.")
    return uv


def default_batch_id(name: str | None, config_path: Path) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if name:
        return name
    return f"{DEFAULT_BATCH_PREFIX}-{config_path.stem}-{stamp}"


def resolve_input_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    candidate = DEFAULT_INPUT_DIR / path
    if candidate.exists():
        return candidate.resolve()
    return (REPO_ROOT / path).resolve()


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="Run config YAML passed to the CLI.")
    parser.add_argument(
        "--model-profile",
        default=None,
        help="Model profile from configs/model-profiles.yaml, applied on top of --config (harness/model/api keys).",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input JSON. Plain filename resolves under inputs/; relative paths resolve against repo root.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Batch name. Output goes to runs/<name>/. Default: runs/batch-<config-stem>-<UTC-stamp>/.",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Override the workspace root. Default: runs/<batch_id>/.",
    )
    parser.add_argument("--concurrency", type=int, default=8, help="Max concurrent runs (default 8).")
    parser.add_argument("--retries", type=int, default=1, help="Retries after the first failed attempt (default 1).")
    parser.add_argument(
        "--samples",
        "-k",
        type=int,
        default=1,
        help="Generate k runs per input (run ids: <id>-01..<id>-kk). Default 1.",
    )
    parser.add_argument("--force", action="store_true", help="Rerun successful existing run workspaces.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="If state.json/events.jsonl already exist for this batch dir, rotate them to .<UTC-stamp>.bak instead of failing.",
    )
    parser.add_argument("--uv", default=None, help="Path to uv executable.")
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--launch-delay-seconds", type=float, default=0.0)
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run in the foreground attached to this terminal. Default detaches into the background, immune to SIGHUP, so the batch survives SSH disconnects.",
    )


def normalize_run_args(args: argparse.Namespace) -> None:
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1.")
    if args.retries < 0:
        raise SystemExit("--retries must be >= 0.")
    if args.samples < 1:
        raise SystemExit("--samples must be >= 1.")

    args.uv = resolve_uv(args.uv)
    args.input = str(resolve_input_path(args.input))
    args.config = str(resolve_path(args.config))

    args.batch_id = default_batch_id(args.name, Path(args.config))

    if args.workspace_root is None:
        args.workspace_root = str(DEFAULT_OUTPUT_PARENT / args.batch_id)
    args.workspace_root = str(resolve_path(args.workspace_root))

    args.batch_dir = str(Path(args.workspace_root) / "_batch")


def run_foreground(args: argparse.Namespace) -> int:
    normalize_run_args(args)
    return Supervisor(args).run()


def run_command(args: argparse.Namespace) -> int:
    normalize_run_args(args)
    if args.foreground:
        return Supervisor(args).run()
    print(f"batch_id={args.batch_id}")
    print(f"workspace_root={args.workspace_root}")
    print(f"status: uv run python scripts/run_batch.py status --name {args.batch_id}")
    sys.stdout.flush()
    _detach_to_background()
    return Supervisor(args).run()


def _detach_to_background() -> None:
    """Double-fork into a new session so the batch survives SSH disconnects.

    The grandchild becomes the supervisor. stdin/stdout/stderr are redirected
    to /dev/null because run_batch's `run` subcommand is otherwise silent and
    inherited fds would keep the controlling terminal pinned.
    """
    if os.fork() != 0:
        os._exit(0)
    os.setsid()
    if os.fork() != 0:
        os._exit(0)
    devnull_in = os.open(os.devnull, os.O_RDONLY)
    devnull_out = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_in, 0)
    os.dup2(devnull_out, 1)
    os.dup2(devnull_out, 2)
    os.close(devnull_in)
    os.close(devnull_out)


def status(args: argparse.Namespace) -> int:
    if args.batch_dir:
        batch_dir = resolve_path(args.batch_dir)
    elif args.name:
        batch_dir = (DEFAULT_OUTPUT_PARENT / args.name / "_batch").resolve()
    elif args.workspace_root:
        batch_dir = (resolve_path(args.workspace_root) / "_batch").resolve()
    else:
        batch_dir = latest_batch_dir(DEFAULT_OUTPUT_PARENT)
    state_path = batch_dir / STATE_FILENAME
    if not state_path.is_file():
        raise SystemExit(f"No state file found: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    counts = state.get("counts", {})
    print(f"batch_id={state.get('batch_id')}")
    print(f"config={state.get('config')}")
    print(f"input_file={state.get('input_file')}")
    print(f"workspace_root={state.get('workspace_root')}")
    print(f"pid={state.get('pid')}")
    print(f"started_at={state.get('started_at')}")
    print(f"updated_at={state.get('updated_at')}")
    print(f"finished_at={state.get('finished_at')}")
    print(f"total={state.get('total')}  samples={state.get('samples')}  concurrency={state.get('concurrency')}  retries={state.get('retries')}")
    print("counts=" + json.dumps(counts, sort_keys=True))
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    if metrics:
        print(
            "metrics="
            + json.dumps(
                {
                    "total_cost_usd": metrics.get("total_cost_usd"),
                    "total_wall_time_ms": metrics.get("total_wall_time_ms"),
                    "total_agent_duration_ms": metrics.get("total_agent_duration_ms"),
                    "total_tokens": metrics.get("total_tokens"),
                    "tool_call_count": metrics.get("tool_call_count"),
                    "missing_cost_count": metrics.get("missing_cost_count"),
                    "missing_usage_count": metrics.get("missing_usage_count"),
                },
                sort_keys=True,
            )
        )
    print(f"state={state_path}")
    print(f"events={batch_dir / EVENTS_FILENAME}")
    failed = [entry for entry in state.get("items", {}).values() if entry.get("status") == "failed"]
    if failed:
        print("failed_runs=" + ",".join(entry["run_id"] for entry in failed))
    return 0


def latest_batch_dir(output_parent: Path) -> Path:
    if not output_parent.is_dir():
        raise SystemExit(f"No batch directory found under {output_parent}")
    candidates = [path / "_batch" for path in output_parent.iterdir() if (path / "_batch" / STATE_FILENAME).is_file()]
    if not candidates:
        raise SystemExit(f"No batch state files found under {output_parent}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the batch. Detaches into the background by default; pass --foreground to attach to this terminal.",
    )
    add_run_args(run_parser)
    run_parser.set_defaults(func=run_command)

    status_parser = subparsers.add_parser("status", help="Show a batch state summary.")
    status_parser.add_argument("--name", default=None, help="Batch name (matches --name from `run`).")
    status_parser.add_argument("--workspace-root", default=None)
    status_parser.add_argument("--batch-dir", default=None)
    status_parser.set_defaults(func=status)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
