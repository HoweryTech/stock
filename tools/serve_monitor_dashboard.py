#!/usr/bin/env python3
"""Serve the local holding monitor dashboard and fixed JSON APIs."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web" / "monitor-dashboard"
API_FILES = {
    "/api/snapshot": ROOT / "data" / "metadata" / "intraday-monitor.latest.json",
    "/api/research": ROOT / "data" / "metadata" / "eastmoney-holding-research.json",
    "/api/action-draft": ROOT / "data" / "metadata" / "eastmoney-holding-action-draft.json",
}
PID_FILE = ROOT / "data" / "metadata" / "intraday-monitor.pid"
EVENT_FILE = ROOT / "data" / "metadata" / "intraday-monitor.events.jsonl"


def monitor_status() -> dict[str, object]:
    if not PID_FILE.exists():
        return {"running": False, "pid": None}
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError):
        return {"running": False, "pid": None}
    return {"running": True, "pid": pid}


def recent_events(limit: int) -> list[dict[str, object]]:
    if not EVENT_FILE.exists():
        return []
    lines = EVENT_FILE.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, object]] = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(events))


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in API_FILES:
            path = API_FILES[parsed.path]
            if not path.exists():
                self.send_json({"error": f"missing data source: {path.name}"}, 404)
                return
            try:
                self.send_json(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/status":
            self.send_json(monitor_status())
            return
        if parsed.path == "/api/events":
            query = parse_qs(parsed.query)
            try:
                limit = min(100, max(1, int(query.get("limit", [20])[0])))
            except ValueError:
                limit = 20
            self.send_json({"events": recent_events(limit)})
            return

        relative = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        payload = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the holding monitor dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"dashboard: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
