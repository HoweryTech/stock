#!/usr/bin/env python3
"""Serve the local holding monitor dashboard and fixed JSON APIs."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import time
from argparse import Namespace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from tools.apply_manual_trade import apply_manual_trade
    from tools.check_market_wait_refresh import build_refresh_check
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.apply_manual_trade import apply_manual_trade
    from tools.check_market_wait_refresh import build_refresh_check


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web" / "monitor-dashboard"
API_FILES = {
    "/api/snapshot": ROOT / "data" / "metadata" / "intraday-monitor.latest.json",
    "/api/research": ROOT / "data" / "metadata" / "eastmoney-holding-research.json",
    "/api/action-draft": ROOT / "data" / "metadata" / "eastmoney-holding-action-draft.json",
    "/api/reverse-t-backtest": ROOT / "data" / "metadata" / "reverse-t-backtest.json",
    "/api/reverse-t-forecast": ROOT / "data" / "metadata" / "reverse-t-forecast.json",
    "/api/decision-cards": ROOT / "data" / "metadata" / "realtime-decision-cards.json",
}
PID_FILE = ROOT / "data" / "metadata" / "intraday-monitor.pid"
EVENT_FILE = ROOT / "data" / "metadata" / "intraday-monitor.events.jsonl"
FLOW_HISTORY_FILE = ROOT / "data" / "metadata" / "intraday-flow-history.jsonl"
ARCHIVE_DIR = ROOT / "data" / "metadata" / "intraday-archive"


def dashboard_position_paths() -> list[str]:
    return [str(path) for path in sorted((ROOT / "positions").glob("POS-EASTMONEY-*.yaml"))]


def load_json(path: Path, *, retries: int = 3, delay: float = 0.05) -> dict[str, object] | None:
    if not path.exists():
        return None
    for attempt in range(retries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            return None
        except OSError:
            return None


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


def recent_flow_history(limit: int) -> dict[str, object]:
    if FLOW_HISTORY_FILE.exists():
        lines = FLOW_HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        samples: list[dict[str, object]] = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            generated_at = event.get("generated_at")
            for sample in event.get("samples", []) if isinstance(event.get("samples"), list) else []:
                if isinstance(sample, dict):
                    samples.append({"generated_at": generated_at, **sample})
        return {"samples": samples}

    paths = sorted(ARCHIVE_DIR.glob("snapshot-*.json"))[-limit:]
    latest_path = API_FILES["/api/snapshot"]
    if latest_path.exists():
        paths.append(latest_path)

    seen: set[str] = set()
    samples: list[dict[str, object]] = []
    for path in paths:
        data = load_json(path, retries=1) or {}
        generated_at = data.get("generated_at")
        if not generated_at:
            continue
        key = f"{generated_at}:{path}"
        if key in seen:
            continue
        seen.add(key)
        for item in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
            flow = item.get("capital_flow") if isinstance(item.get("capital_flow"), dict) else {}
            samples.append(
                {
                    "generated_at": generated_at,
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "latest_price": quote.get("latest_price"),
                    "high": quote.get("high"),
                    "main_net_inflow": flow.get("main_net_inflow"),
                    "main_net_inflow_ratio_pct": flow.get("main_net_inflow_ratio_pct"),
                }
            )
    return {"samples": samples}


def market_wait_refresh_status() -> dict[str, object]:
    snapshot = load_json(API_FILES["/api/snapshot"]) or {}
    total_assets = snapshot.get("total_assets")
    return build_refresh_check(
        load_json(API_FILES["/api/decision-cards"]),
        as_of=datetime.now().astimezone(),
        positions=["positions/POS-EASTMONEY-*.yaml"],
        daily_bars="data/processed/daily_bars.csv",
        total_assets=float(total_assets) if isinstance(total_assets, (int, float)) else None,
        python_bin=".venv/bin/python",
    )


def manual_trade_args(payload: dict[str, object], total_assets: float | None) -> Namespace:
    return Namespace(
        positions=dashboard_position_paths(),
        code=str(payload.get("code") or ""),
        side=str(payload.get("side") or ""),
        shares=float(payload.get("shares") or 0),
        price=float(payload.get("price") or 0),
        total_assets=float(payload.get("total_assets") or total_assets or 25480.0),
        occurred_at=payload.get("occurred_at") or None,
        note=str(payload.get("note") or ""),
        trade_intent=str(payload.get("trade_intent") or ""),
        linked_trade_id=str(payload.get("linked_trade_id") or ""),
        source="dashboard",
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
    )


def run_refresh_commands(total_assets: float) -> list[dict[str, object]]:
    commands = [
        [".venv/bin/python", "tools/forecast_reverse_t.py", "--positions", "positions/POS-EASTMONEY-*.yaml", "--output", "data/metadata/reverse-t-forecast.json"],
        [
            ".venv/bin/python",
            "tools/run_intraday_decision_pipeline.py",
            "--positions",
            "positions/POS-EASTMONEY-*.yaml",
            "--daily-bars",
            "data/processed/daily_bars.csv",
            "--total-assets",
            str(total_assets),
        ],
    ]
    results: list[dict[str, object]] = []
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=90)
        results.append(
            {
                "command": " ".join(command),
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {' '.join(command)}")
    return results


def handle_manual_trade(payload: dict[str, object]) -> dict[str, object]:
    snapshot = load_json(API_FILES["/api/snapshot"]) or {}
    total_assets_raw = snapshot.get("total_assets")
    total_assets = float(total_assets_raw) if isinstance(total_assets_raw, (int, float)) else 25480.0
    args = manual_trade_args(payload, total_assets)
    update, _ = apply_manual_trade(args)
    try:
        refresh = run_refresh_commands(float(args.total_assets))
        return {"ok": True, "update": update, "refresh": refresh}
    except Exception as exc:
        return {"ok": True, "update": update, "refresh": [], "refresh_error": str(exc)}


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
            data = load_json(path, retries=5, delay=0.06)
            if data is None:
                self.send_json({"error": f"data source is temporarily unreadable: {path.name}"}, 503)
                return
            self.send_json(data)
            return
        if parsed.path == "/api/status":
            self.send_json(monitor_status())
            return
        if parsed.path == "/api/market-wait-refresh":
            self.send_json(market_wait_refresh_status())
            return
        if parsed.path == "/api/events":
            query = parse_qs(parsed.query)
            try:
                limit = min(100, max(1, int(query.get("limit", [20])[0])))
            except ValueError:
                limit = 20
            self.send_json({"events": recent_events(limit)})
            return
        if parsed.path == "/api/flow-history":
            query = parse_qs(parsed.query)
            try:
                limit = min(100, max(3, int(query.get("limit", [30])[0])))
            except ValueError:
                limit = 30
            self.send_json(recent_flow_history(limit))
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/manual-trade":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 8192:
                raise ValueError("invalid request body")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            self.send_json(handle_manual_trade(payload))
        except Exception as exc:
            print(f"manual trade request failed: {exc}", file=sys.stderr, flush=True)
            self.send_json({"ok": False, "error": str(exc)}, 400)

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
