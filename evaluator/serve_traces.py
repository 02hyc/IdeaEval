"""Serve the trace viewer with embedded data from a benchmark run.

Supports:
- Completed traces: full_ranking_result.json with ranking + reports + trace
- In-progress traces: reads agent message store JSON directly
- Live mode: SSE streaming of trace_live.jsonl events

Usage:
    python serve_traces.py ./experiments/calibration_v2_20260520_204043
    python serve_traces.py ./experiments/calibration_v2_20260520_204043 --port 8742
"""
import http.server
import json
import os
import re
import sys
import time
import threading
from pathlib import Path
from urllib.parse import unquote

PORT = 8742


def find_pair_dirs(directory: str) -> list[dict]:
    """Find all pair directories (completed or in-progress)."""
    results = []
    root = Path(directory)

    # Check if root itself is a pair directory
    if _is_pair_dir(root):
        results.append(_build_pair_info(root))
        return results

    # Scan subdirectories
    for d in sorted(root.iterdir()):
        if d.is_dir() and _is_pair_dir(d):
            results.append(_build_pair_info(d))

    return results


def _is_pair_dir(d: Path) -> bool:
    """Check if a directory is a pair directory (has agents/ subdir or full_ranking_result.json)."""
    return (d / "full_ranking_result.json").exists() or (d / "agents").is_dir()


def _latest_activity_mtime(d: Path) -> float | None:
    """Latest write time for an in-progress pair/topic directory."""
    candidates = [
        d / "agent.log",
        d / "trace.json",
        d / "research_judge_summary.json",
        d / "ranking_result.json",
    ]
    agents_dir = d / "agents"
    if agents_dir.is_dir():
        candidates.extend(agents_dir.glob("*/agent.log"))
        candidates.extend(agents_dir.glob("*/workspace/*.md"))
        candidates.extend(agents_dir.glob("*/.runtime/data_storage/*/*.json"))

    mtimes = []
    for p in candidates:
        try:
            if p.is_file():
                mtimes.append(p.stat().st_mtime)
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def _in_progress_status(d: Path) -> dict:
    """Classify no-marker directories as active or stale for the index."""
    stale_after = int(os.environ.get("TRACE_STALE_SECONDS", "900"))
    latest = _latest_activity_mtime(d)
    if latest is None:
        return {"status": "stale", "activity_age_sec": None}
    age = max(0, int(time.time() - latest))
    status = "running" if age <= stale_after else "stale"
    return {"status": status, "activity_age_sec": age, "stale_after_sec": stale_after}


def _format_age(seconds: int | None) -> str:
    if seconds is None:
        return "no activity"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago"


def _build_pair_info(d: Path) -> dict:
    """Build info dict for a pair directory."""
    info = {"path": str(d), "name": d.name, "completed": False, "status": "running", "agents": []}

    full_result = d / "full_ranking_result.json"
    if full_result.exists():
        info["completed"] = True
        info["status"] = "done"
        try:
            data = json.loads(full_result.read_text(encoding="utf-8"))
            ranking = data.get("ranking", {})
            info["result"] = ranking.get("result", "?")
            info["confidence"] = ranking.get("confidence", 0)
            info["meta"] = data.get("meta", {})
        except Exception:
            info["result"] = "?"
            info["confidence"] = 0
    elif (d / "DONE").exists():
        # Phase-0-only prebuild (no ranker pipeline). Mark complete and surface
        # meta.json so the index can show topic_label + elapsed_sec.
        info["completed"] = True
        info["status"] = "done"
        info["result"] = "lit_review"
        meta_path = d / "meta.json"
        if meta_path.exists():
            try:
                info["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                info["meta"] = {}
    elif (d / "FAILED").exists():
        info["completed"] = True
        info["status"] = "failed"
        info["result"] = "FAILED"
        try:
            info["meta"] = {"error": (d / "FAILED").read_text(encoding="utf-8")[:300]}
        except Exception:
            info["meta"] = {}
    else:
        info.update(_in_progress_status(d))

    # Find agent message stores
    agents_dir = d / "agents"
    if agents_dir.is_dir():
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_name = agent_dir.name
            msg_store = _find_message_store(agent_dir)
            if msg_store:
                info["agents"].append({"name": agent_name, "store_path": str(msg_store)})

    return info


def _find_message_store(agent_dir: Path) -> Path | None:
    """Find the message store JSON for an agent."""
    runtime_dir = agent_dir / ".runtime" / "data_storage"
    if not runtime_dir.is_dir():
        return None
    # Find the task directory (timestamp-based)
    for task_dir in sorted(runtime_dir.iterdir(), reverse=True):
        if not task_dir.is_dir():
            continue
        # Find the agent JSON file (not task_info.json)
        for f in task_dir.iterdir():
            if f.suffix == ".json" and f.name != "task_info.json":
                return f
    return None


def build_index_html(pairs: list[dict], base_dir: str) -> str:
    """Build an index page listing all pair traces."""
    items = []
    for p in pairs:
        status = p.get("status", "done" if p.get("completed") else "running")
        status_label = {
            "done": "done",
            "failed": "failed",
            "running": "running",
            "stale": "stale",
        }.get(status, status)
        status_badge = f'<span class="badge {status}">{status_label}</span>'
        result_text = ""
        if p["completed"]:
            r = p.get("result", "?")
            try:
                conf = float(p.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                conf = 0.0
            gt = p.get("meta", {}).get("ground_truth", "")
            correct = (r == gt) if gt else None
            r_class = "correct" if correct else "wrong" if correct is False else "unknown"
            result_text = f'<span class="result {r_class}">{r}</span> <span class="conf">{conf:.2f}</span>'
            if gt:
                result_text += f' <span class="gt">gt: {gt}</span>'
        else:
            result_text = (
                f'<span class="conf">last: {_format_age(p.get("activity_age_sec"))}</span>'
            )

        agents_text = ", ".join(a["name"] for a in p.get("agents", []))

        items.append(
            f'<a href="/pair/{p["name"]}" class="pair-row">'
            f'<div class="pair-status">{status_badge}</div>'
            f'<div class="pair-name">{p["name"]}</div>'
            f'<div class="pair-result">{result_text}</div>'
            f'<div class="pair-agents">{agents_text}</div>'
            f'</a>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="10"><title>Trace Index</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Instrument+Sans:wght@400;500;600;700&family=Instrument+Serif:ital@1&display=swap" rel="stylesheet">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Instrument Sans', -apple-system, sans-serif; background: #fafaf8; color: #1a1a18; padding: 48px; }}
.container {{ max-width: 840px; margin: 0 auto; }}
h1 {{ font-family: 'Instrument Serif', Georgia, serif; font-style: italic; font-size: 1.6rem; font-weight: 400; margin-bottom: 4px; }}
.subtitle {{ color: #8a8880; font-size: 0.82rem; margin-bottom: 32px; font-family: 'IBM Plex Mono', monospace; }}
.pair-row {{
  display: grid; grid-template-columns: 72px 1fr auto auto;
  align-items: center; gap: 16px;
  padding: 14px 20px; margin: 0;
  background: #ffffff; border: 1px solid #e5e3db; border-radius: 8px;
  text-decoration: none; color: #1a1a18;
  font-size: 0.82rem;
  transition: all 0.12s ease;
  margin-bottom: 6px;
}}
.pair-row:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.06); border-color: #d4d3cc; }}
.pair-status {{ font-size: 0.72rem; }}
.badge {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.65rem; font-weight: 500; padding: 2px 8px; border-radius: 99px; text-transform: uppercase; letter-spacing: 0.04em; }}
.badge.done {{ background: #f0fdf4; color: #166534; }}
.badge.running {{ background: #fffbeb; color: #92400e; }}
.badge.stale {{ background: #f4f4f5; color: #52525b; }}
.badge.failed {{ background: #fef2f2; color: #991b1b; }}
.pair-name {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; font-weight: 500; color: #2563eb; }}
.pair-result {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; }}
.result {{ font-weight: 600; }}
.result.correct {{ color: #16783a; }}
.result.wrong {{ color: #b44c1c; }}
.result.unknown {{ color: #8a8880; }}
.gt {{ color: #8a8880; font-size: 0.68rem; font-weight: 400; }}
.conf {{ color: #8a8880; }}
.pair-agents {{ font-family: 'IBM Plex Mono', monospace; color: #8a8880; font-size: 0.68rem; }}
</style></head><body>
<div class="container">
<h1>Trace Index</h1>
<p class="subtitle">{len(pairs)} pairs &middot; {base_dir}</p>
{''.join(items)}
</div>
</body></html>"""


_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _agent_runtime(agent_dir: Path) -> dict:
    """Wall-clock runtime for one agent, derived from agent.log first/last
    timestamped lines. Returns {} when the log is missing or unparseable."""
    log_path = agent_dir / "agent.log"
    if not log_path.is_file():
        return {}
    first_ts = last_ts = None
    try:
        # Stream-read so 10 MB+ logs don't bloat memory.
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _LOG_TS_RE.match(line)
                if not m:
                    continue
                ts = m.group(1)
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
    except OSError:
        return {}
    if not first_ts or not last_ts:
        return {}
    try:
        from datetime import datetime
        t0 = datetime.strptime(first_ts, "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S")
        wall_ms = int((t1 - t0).total_seconds() * 1000)
    except ValueError:
        return {}
    return {"started_at": first_ts, "ended_at": last_ts, "wall_clock_ms": wall_ms}


def _collect_workspace_data(pair_path: Path) -> dict:
    """Collect workspace files for all agents: scraped papers info + proposals."""
    workspace_data = {}
    agents_dir = pair_path / "agents"
    if not agents_dir.is_dir():
        return workspace_data

    for agent_dir in sorted(agents_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_name = agent_dir.name
        ws = agent_dir / "workspace"
        if not ws.is_dir():
            continue

        agent_ws = {
            "proposal": None,
            "scraped_papers": [],
            "deliverables": {},
            "paper_summaries": [],
            "runtime": _agent_runtime(agent_dir),
        }

        # Proposal
        proposal_file = ws / "proposal.md"
        if proposal_file.exists():
            try:
                agent_ws["proposal"] = proposal_file.read_text(encoding="utf-8")
            except Exception:
                pass

        # Deliverables written by the research agent (mid-flight files visible
        # before the run completes).
        for deliverable in (
            "literature_review.md",
            "evidence_gaps.md",
            "targeted_findings.md",
            "analysis_report.md",
            "revision_feedback.md",
        ):
            f = ws / deliverable
            if f.exists():
                try:
                    agent_ws["deliverables"][deliverable] = f.read_text(encoding="utf-8")
                except Exception:
                    pass

        # SummarizePaper outputs (one .md per scraped paper).
        summaries_dir = ws / "paper_summaries"
        if summaries_dir.is_dir():
            for sf in sorted(summaries_dir.iterdir()):
                if sf.suffix == ".md" and sf.is_file():
                    try:
                        agent_ws["paper_summaries"].append({
                            "id": sf.stem,
                            "content": sf.read_text(encoding="utf-8"),
                        })
                    except Exception:
                        pass

        # Scraped papers
        scraped_dir = ws / "scraped_papers"
        if scraped_dir.is_dir():
            for paper_dir in sorted(scraped_dir.iterdir()):
                if not paper_dir.is_dir():
                    continue
                paper_entry = {"id": paper_dir.name, "title": "", "toc": []}
                info_file = paper_dir / "paper_info.json"
                if info_file.exists():
                    try:
                        info = json.loads(info_file.read_text(encoding="utf-8"))
                        paper_entry["title"] = info.get("title", "")
                        paper_entry["toc"] = info.get("toc", [])
                    except Exception:
                        pass
                # Load all sections content
                sections_dir = paper_dir / "sections"
                if sections_dir.is_dir():
                    sections = {}
                    for sf in sorted(sections_dir.iterdir()):
                        if sf.suffix == ".md":
                            try:
                                sections[sf.name] = sf.read_text(encoding="utf-8")
                            except Exception:
                                pass
                    paper_entry["sections"] = sections
                agent_ws["scraped_papers"].append(paper_entry)

        workspace_data[agent_name] = agent_ws

    return workspace_data


def _collect_reports(pair_path: Path) -> dict:
    """Collect report files from the reports/ directory."""
    reports = {}
    reports_dir = pair_path / "reports"
    if reports_dir.is_dir():
        for f in sorted(reports_dir.iterdir()):
            if f.suffix == ".md":
                try:
                    content = f.read_text(encoding="utf-8").strip()
                    if content and "no output" not in content:
                        reports[f.stem] = content
                except Exception:
                    pass
    return reports


def build_pair_viewer_html(pair_info: dict, viewer_html: str) -> str:
    """Build viewer HTML with injected data for a specific pair."""
    # Load message stores for all agents
    agent_data = {}
    for agent in pair_info.get("agents", []):
        store_path = agent.get("store_path")
        if store_path and os.path.exists(store_path):
            try:
                with open(store_path, encoding="utf-8") as f:
                    agent_data[agent["name"]] = json.load(f)
            except Exception:
                pass

    # Load full ranking result if completed
    pair_path = Path(pair_info["path"])
    full_result = None
    if pair_info["completed"]:
        try:
            full_result = json.loads((pair_path / "full_ranking_result.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    # Collect workspace data and reports
    workspace_data = _collect_workspace_data(pair_path)
    reports_data = _collect_reports(pair_path)

    # Inject data (escape </ sequences to prevent breaking <script> tags)
    def safe_json(obj):
        return json.dumps(obj).replace("</", "<\\/")

    inject_parts = []
    if full_result:
        inject_parts.append(f"var TRACE_DATA = {safe_json(full_result)};")
    if agent_data:
        inject_parts.append(f"var MESSAGE_STORE_DATA = {safe_json(agent_data)};")
    if workspace_data:
        inject_parts.append(f"var WORKSPACE_DATA = {safe_json(workspace_data)};")
    if reports_data:
        inject_parts.append(f"var REPORTS_DATA = {safe_json(reports_data)};")

    inject = f"<script>{' '.join(inject_parts)}</script>"
    return viewer_html.replace("</head>", inject + "</head>")


class TraceHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, pairs=None, viewer_path=None, base_dir=None, **kwargs):
        self.pairs = pairs or []
        self.viewer_path = viewer_path
        self.base_dir = base_dir or "."
        super().__init__(*args, **kwargs)

    def do_GET(self):
        path = unquote(self.path)

        if path == "/" or path == "/index":
            self.pairs = find_pair_dirs(self.base_dir)
            content = build_index_html(self.pairs, self.base_dir)
            self._respond_html(content)
            return

        if path.startswith("/pair/"):
            name = path[len("/pair/"):]
            self.pairs = find_pair_dirs(self.base_dir)
            match = next((p for p in self.pairs if p["name"] == name), None)
            if match:
                viewer_html = Path(self.viewer_path).read_text(encoding="utf-8")
                html = build_pair_viewer_html(match, viewer_html)
                self._respond_html(html)
                return
            self._respond_404()
            return

        if path == "/api/pairs":
            self.pairs = find_pair_dirs(self.base_dir)
            self._respond_json(self.pairs)
            return

        if path.startswith("/api/pair/"):
            name = path[len("/api/pair/"):]
            self.pairs = find_pair_dirs(self.base_dir)
            match = next((p for p in self.pairs if p["name"] == name), None)
            if match:
                pair_path = Path(match["path"])
                agent_data = {}
                for agent in match.get("agents", []):
                    store_path = agent.get("store_path")
                    if store_path and os.path.exists(store_path):
                        try:
                            with open(store_path, encoding="utf-8") as f:
                                agent_data[agent["name"]] = json.load(f)
                        except Exception:
                            pass
                full_result = None
                if match["completed"]:
                    try:
                        full_result = json.loads((pair_path / "full_ranking_result.json").read_text(encoding="utf-8"))
                    except Exception:
                        pass
                workspace_data = _collect_workspace_data(pair_path)
                reports_data = _collect_reports(pair_path)
                self._respond_json({
                    "trace_data": full_result,
                    "message_store_data": agent_data,
                    "workspace_data": workspace_data,
                    "reports_data": reports_data,
                })
                return
            self._respond_404()
            return

        # Serve the raw viewer HTML (for debugging)
        if path == "/viewer":
            content = Path(self.viewer_path).read_text(encoding="utf-8")
            self._respond_html(content)
            return

        self._respond_404()

    def _respond_html(self, content: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _respond_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _respond_404(self):
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        pass


def main():
    if len(sys.argv) < 2:
        print("Usage: python serve_traces.py <directory> [--port PORT]")
        sys.exit(1)

    directory = sys.argv[1]
    port = PORT

    # Parse --port flag
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    viewer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trace_viewer.html")
    if not os.path.exists(viewer_path):
        print(f"ERROR: trace_viewer.html not found at {viewer_path}")
        sys.exit(1)

    pairs = find_pair_dirs(directory)
    completed = sum(1 for p in pairs if p["completed"])
    in_progress = len(pairs) - completed

    print(f"Scanning: {directory}")
    print(f"Found {len(pairs)} pair(s): {completed} completed, {in_progress} in-progress")
    for p in pairs[:10]:
        status = "DONE" if p["completed"] else "..."
        print(f"  [{status}] {p['name']}")
    if len(pairs) > 10:
        print(f"  ... and {len(pairs) - 10} more")

    def handler_factory(*args, **kwargs):
        return TraceHandler(*args, pairs=pairs, viewer_path=viewer_path, base_dir=directory, **kwargs)

    class ThreadedHTTPServer(http.server.HTTPServer):
        daemon_threads = True
        def process_request(self, request, client_address):
            t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
            t.daemon = True
            t.start()
        def process_request_thread(self, request, client_address):
            try:
                self.finish_request(request, client_address)
            except Exception:
                self.handle_error(request, client_address)
            finally:
                self.shutdown_request(request)

    server = ThreadedHTTPServer(("0.0.0.0", port), handler_factory)
    url = f"http://localhost:{port}"
    print(f"\nServing at {url}")
    print("Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
