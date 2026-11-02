"""Command-line entry for the adapter-events log viewer."""

from __future__ import annotations

import argparse
import errno
import sys
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from research_ideation_agent.log_viewer.server import build_handler_class, list_runs, resolve_runs_root

PORT_RETRIES = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viz",
        description=(
            "Visualize Claude Code adapter_events.jsonl logs in a browser. "
            "If no path is given, autodiscovers a .runs/ directory by walking "
            "up from the current working directory."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional: path to a runs-root directory (containing one run dir per "
             "subfolder), or to a single run dir (the parent .runs/ is then used). "
             "If omitted, autodiscover .runs/ from the current working directory.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default 8765).")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not auto-open the browser.",
    )
    return parser


def _bind(host: str, start_port: int, handler_cls) -> ThreadingHTTPServer:
    last_err: OSError | None = None
    for port in range(start_port, start_port + PORT_RETRIES + 1):
        try:
            server = ThreadingHTTPServer((host, port), handler_cls)
            # Daemon threads so Ctrl+C doesn't wait on in-flight requests.
            server.daemon_threads = True
            return server
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last_err = exc
    msg = f"could not bind {host}:{start_port}..{start_port + PORT_RETRIES}: {last_err}"
    raise SystemExit(msg)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    arg_path = Path(args.path) if args.path else None
    try:
        runs_root = resolve_runs_root(arg_path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"viz: error: {exc}", file=sys.stderr)
        return 2

    handler_cls = build_handler_class(runs_root)
    httpd = _bind(args.host, args.port, handler_cls)
    host, port = httpd.server_address[:2]
    url = f"http://{host}:{port}/"

    runs = list_runs(runs_root)
    print(f"viz: runs root  {runs_root}", file=sys.stderr)
    if not runs_root.exists():
        print("viz: warning: runs root does not exist yet — sidebar will be empty", file=sys.stderr)
    else:
        print(f"viz: found {len(runs)} run(s)", file=sys.stderr)
    print(f"viz: serving    {url}", file=sys.stderr)
    sys.stderr.flush()

    if not args.no_open:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("viz: shutting down", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0
