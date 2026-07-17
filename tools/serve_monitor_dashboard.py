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
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from tools.apply_stop_loss_confirmation import apply_stop_loss_confirmation
    from tools.apply_manual_trade import apply_manual_trade
    from tools.check_market_wait_refresh import build_refresh_check
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.apply_stop_loss_confirmation import apply_stop_loss_confirmation
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


def stop_loss_confirmation_args(payload: dict[str, object]) -> Namespace:
    return Namespace(
        positions=dashboard_position_paths(),
        code=str(payload.get("code") or ""),
        action=str(payload.get("action") or ""),
        stop_loss_price=float(payload.get("stop_loss_price") or 0),
        current_price=payload.get("current_price"),
        dynamic_source=str(payload.get("dynamic_source") or ""),
        reason=str(payload.get("reason") or ""),
        note=str(payload.get("note") or ""),
        source="dashboard",
        confirmed_at=payload.get("confirmed_at") or None,
        audit_output=str(ROOT / "data" / "metadata" / "stop-loss-confirmations.jsonl"),
    )


def append_optional_file_arg(command: list[str], option: str, relative_path: str) -> None:
    path = ROOT / relative_path
    if path.exists():
        command.extend([option, relative_path])


def run_refresh_commands(total_assets: float) -> list[dict[str, object]]:
    pipeline_command = [
        ".venv/bin/python",
        "tools/run_intraday_decision_pipeline.py",
        "--positions",
        "positions/POS-EASTMONEY-*.yaml",
        "--daily-bars",
        "data/processed/daily_bars.csv",
        "--total-assets",
        str(total_assets),
    ]
    append_optional_file_arg(pipeline_command, "--minute-cache-dir", "data/processed/minute-bars")
    append_optional_file_arg(pipeline_command, "--action-backtests", "data/metadata/portfolio-action-matrix-backtests.after-plan.json")
    append_optional_file_arg(pipeline_command, "--reverse-t-backtest", "data/metadata/reverse-t-backtest.json")
    append_optional_file_arg(pipeline_command, "--reverse-t-forecast", "data/metadata/reverse-t-forecast.json")
    append_optional_file_arg(pipeline_command, "--technical-indicators", "data/metadata/technical-indicators.json")
    commands = [
        [".venv/bin/python", "tools/forecast_reverse_t.py", "--positions", "positions/POS-EASTMONEY-*.yaml", "--output", "data/metadata/reverse-t-forecast.json"],
        pipeline_command,
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


def as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def find_code_item(items: object, code: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and str(item.get("code") or "") == code:
            return item
    return None


def intent_label(intent: str) -> str:
    return {
        "reverse_t_open": "反T卖出腿",
        "reverse_t_close": "反T回补",
        "positive_t_open": "正T买入腿",
        "positive_t_close": "正T目标卖出",
        "risk_exit_reduce": "风控减仓",
        "risk_exit_full": "风控清仓",
    }.get(intent, "普通手工成交")


def build_post_trade_tracking(update: dict[str, Any], snapshot: dict[str, object] | None, decision_report: dict[str, object] | None) -> dict[str, Any]:
    trade = update.get("trade") if isinstance(update.get("trade"), dict) else {}
    position = update.get("position") if isinstance(update.get("position"), dict) else {}
    code = str(trade.get("code") or "")
    item = find_code_item((snapshot or {}).get("items"), code)
    card = find_code_item((decision_report or {}).get("cards"), code)
    shares_after = as_float(trade.get("shares_after")) or as_float(position.get("shares")) or 0.0
    next_steps: list[str] = []
    warnings: list[str] = []

    reverse_closure = trade.get("reverse_t_closure") if isinstance(trade.get("reverse_t_closure"), dict) else None
    positive_closure = trade.get("positive_t_closure") if isinstance(trade.get("positive_t_closure"), dict) else None
    if reverse_closure:
        next_steps.append(str(reverse_closure.get("next_plan") or "反T闭环已完成，刷新后只按新的区间观察。"))
    elif positive_closure:
        next_steps.append(str(positive_closure.get("next_plan") or "正T闭环已完成，刷新后只按新的候选计划观察。"))
    elif trade.get("trade_intent") == "reverse_t_open":
        next_steps.append("已记录反T卖出腿；未到系统回补上限前不要追价买回。")
    elif trade.get("trade_intent") == "positive_t_open":
        next_steps.append("已记录正T买入腿；未到目标卖出区不急于卖出，跌破失败价先复核。")
    elif trade.get("trade_intent") == "risk_exit_reduce":
        next_steps.append("已记录风控减仓；刷新后只按剩余仓位重新评估，不用这笔卖出立刻做T买回。")
    elif trade.get("trade_intent") == "risk_exit_full":
        next_steps.append("已记录风控清仓；该股后续只进入观察，除非重新生成新的买入计划。")

    if shares_after <= 0:
        next_steps.append("该股持仓已归零；后续只观察，不再围绕该股做T，除非重新生成买入计划。")
    elif shares_after < 200:
        warnings.append("成交后持仓少于200股，不支持保留底仓反T。")
        next_steps.append("低于200股时不再开放反T；后续只按风险复核或重新买入计划处理。")
    else:
        next_steps.append(f"成交后仍持有 {shares_after:g} 股；系统会按剩余仓位重新评估止损、正T和反T。")

    decision = card.get("decision") if isinstance(card, dict) and isinstance(card.get("decision"), dict) else {}
    if decision.get("action_label") or decision.get("next_step"):
        next_steps.append(f"刷新后当前建议：{decision.get('action_label') or '--'}；{decision.get('next_step') or '--'}")
    minute_confirmation = card.get("minute_confirmation") if isinstance(card, dict) and isinstance(card.get("minute_confirmation"), dict) else {}
    if minute_confirmation:
        next_steps.append(
            "分钟级二次确认："
            f"{minute_confirmation.get('status_label') or minute_confirmation.get('status') or '--'}，"
            f"{minute_confirmation.get('summary') or '--'}"
        )
    action_arbitration = decision.get("action_arbitration") if isinstance(decision.get("action_arbitration"), dict) else {}
    if action_arbitration.get("summary"):
        next_steps.append(f"动作仲裁：{action_arbitration.get('summary')}")
    primary_action = None
    price_table = card.get("price_action_table") if isinstance(card, dict) and isinstance(card.get("price_action_table"), dict) else {}
    if isinstance(price_table.get("primary_action"), dict):
        primary_action = price_table["primary_action"]
        next_steps.append(
            "下一价格动作："
            f"{primary_action.get('action') or '--'}，"
            f"{primary_action.get('status_label') or primary_action.get('status') or '--'}，"
            f"{primary_action.get('price') or '--'}。"
        )

    return {
        "trade_id": trade.get("id"),
        "code": code,
        "name": (item or {}).get("name") or trade.get("name") or code,
        "side": trade.get("side"),
        "side_label": "卖出" if trade.get("side") == "sell" else "买入",
        "intent": trade.get("trade_intent") or "",
        "intent_label": intent_label(str(trade.get("trade_intent") or "")),
        "price": trade.get("price"),
        "shares": trade.get("shares"),
        "fees_total": (trade.get("fees") or {}).get("total_fees") if isinstance(trade.get("fees"), dict) else None,
        "realized_pnl": trade.get("realized_pnl"),
        "shares_after": shares_after,
        "entry_price_after": position.get("entry_price"),
        "can_reverse_t": shares_after >= 200,
        "current_price": ((item or {}).get("quote") or {}).get("latest_price") if isinstance((item or {}).get("quote"), dict) else None,
        "refreshed_state": card.get("state_label") if isinstance(card, dict) else None,
        "refreshed_action": decision.get("action_label"),
        "primary_action": primary_action,
        "minute_confirmation": minute_confirmation,
        "action_arbitration": action_arbitration,
        "closure": reverse_closure or positive_closure,
        "execution_quality_review": trade.get("execution_quality_review") if isinstance(trade.get("execution_quality_review"), dict) else None,
        "warnings": warnings,
        "next_steps": next_steps,
    }


def handle_manual_trade(payload: dict[str, object]) -> dict[str, object]:
    snapshot = load_json(API_FILES["/api/snapshot"]) or {}
    total_assets_raw = snapshot.get("total_assets")
    total_assets = float(total_assets_raw) if isinstance(total_assets_raw, (int, float)) else 25480.0
    args = manual_trade_args(payload, total_assets)
    update, _ = apply_manual_trade(args)
    try:
        refresh = run_refresh_commands(float(args.total_assets))
        tracking = build_post_trade_tracking(update, load_json(API_FILES["/api/snapshot"]), load_json(API_FILES["/api/decision-cards"]))
        return {"ok": True, "update": update, "refresh": refresh, "post_trade_tracking": tracking}
    except Exception as exc:
        tracking = build_post_trade_tracking(update, load_json(API_FILES["/api/snapshot"]), load_json(API_FILES["/api/decision-cards"]))
        return {"ok": True, "update": update, "refresh": [], "refresh_error": str(exc), "post_trade_tracking": tracking}


def handle_stop_loss_confirmation(payload: dict[str, object]) -> dict[str, object]:
    snapshot = load_json(API_FILES["/api/snapshot"]) or {}
    total_assets_raw = snapshot.get("total_assets")
    total_assets = float(total_assets_raw) if isinstance(total_assets_raw, (int, float)) else 25480.0
    args = stop_loss_confirmation_args(payload)
    update, _ = apply_stop_loss_confirmation(args)
    try:
        refresh = run_refresh_commands(total_assets)
        return {"ok": True, "update": update, "refresh": refresh}
    except Exception as exc:
        return {"ok": True, "update": update, "refresh": [], "refresh_error": str(exc)}


def handle_intraday_trigger_refresh(payload: dict[str, object]) -> dict[str, object]:
    snapshot = load_json(API_FILES["/api/snapshot"]) or {}
    total_assets_raw = snapshot.get("total_assets")
    total_assets = float(total_assets_raw) if isinstance(total_assets_raw, (int, float)) else 25480.0
    triggers = payload.get("triggers") if isinstance(payload.get("triggers"), list) else []
    refresh = run_refresh_commands(total_assets)
    refreshed_report = load_json(API_FILES["/api/decision-cards"]) or {}
    return {
        "ok": True,
        "trigger_count": len(triggers),
        "refresh": refresh,
        "generated_at": refreshed_report.get("generated_at"),
        "state_counts": refreshed_report.get("state_counts") or {},
    }


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
        handlers = {
            "/api/manual-trade": handle_manual_trade,
            "/api/stop-loss-confirmation": handle_stop_loss_confirmation,
            "/api/intraday-trigger-refresh": handle_intraday_trigger_refresh,
        }
        handler = handlers.get(parsed.path)
        if handler is None:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 8192:
                raise ValueError("invalid request body")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            self.send_json(handler(payload))
        except Exception as exc:
            print(f"post request failed for {parsed.path}: {exc}", file=sys.stderr, flush=True)
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
