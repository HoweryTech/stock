#!/usr/bin/env python3
"""Serve the local holding monitor dashboard and fixed JSON APIs."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import subprocess
import sys
import time
from argparse import Namespace
from datetime import datetime, timedelta
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
TRIGGER_REFRESH_EVENT_FILE = ROOT / "data" / "metadata" / "intraday-trigger-refresh.events.jsonl"
TRIGGER_REVIEW_STATUS_FILE = ROOT / "data" / "metadata" / "intraday-trigger-review-status.jsonl"
FLOW_HISTORY_FILE = ROOT / "data" / "metadata" / "intraday-flow-history.jsonl"
ARCHIVE_DIR = ROOT / "data" / "metadata" / "intraday-archive"
CANDIDATE_POOL_FILE = ROOT / "data" / "processed" / "candidate_pool.csv"
CANDIDATE_PORTFOLIO_FIT_FILE = ROOT / "data" / "processed" / "candidate_pool.portfolio_fit.csv"


def infer_candidate_board(code: str, exchange: str = "") -> str:
    code = (code or "").strip()
    exchange = (exchange or "").strip().upper()
    if exchange == "BSE":
        return "bse"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("600", "601", "603", "605")):
        return "sse_main"
    if code.startswith(("000", "001", "002", "003")):
        return "szse_main"
    return "unknown"


def candidate_pool_path() -> Path:
    return CANDIDATE_PORTFOLIO_FIT_FILE if CANDIDATE_PORTFOLIO_FIT_FILE.exists() else CANDIDATE_POOL_FILE


def parse_number(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def split_pipe(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def read_candidate_pool(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows: list[dict[str, object]] = []
        for row in csv.DictReader(file):
            exchange = (row.get("exchange") or "").strip().upper()
            board = (row.get("board") or infer_candidate_board(row.get("code", ""), exchange)).strip()
            item: dict[str, object] = dict(row)
            item["exchange"] = exchange
            item["board"] = board
            item["strategies_list"] = split_pipe(row.get("strategies", ""))
            for field in (
                "strategy_count",
                "combined_score",
                "strategy_confluence_score",
                "trend_score",
                "value_quality_score",
                "event_score",
                "liquidity_score",
                "industry_strength_score",
                "data_quality_score",
                "risk_penalty_score",
                "current_stock_position_pct",
                "current_industry_position_pct",
                "current_total_position_pct",
                "expected_stock_position_pct_after_buy",
                "expected_industry_position_pct_after_buy",
                "expected_total_position_pct_after_buy",
            ):
                item[field] = parse_number(row.get(field))
            rows.append(item)
        return rows


def option_counts(items: list[dict[str, object]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        values = item.get(field)
        if isinstance(values, list):
            parts = [str(value) for value in values if value]
        else:
            parts = [str(values)] if values else []
        for value in parts:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def candidate_filters(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "exchange": option_counts(items, "exchange"),
        "board": option_counts(items, "board"),
        "industry": option_counts(items, "industry"),
        "strategy": option_counts(items, "strategies_list"),
        "portfolio_fit_status": option_counts(items, "portfolio_fit_status"),
        "data_quality_status": option_counts(items, "data_quality_status"),
    }


def filtered_candidates(query: dict[str, list[str]]) -> dict[str, object]:
    path = candidate_pool_path()
    items = read_candidate_pool(path)
    search = (query.get("search", [""])[0] or "").strip().lower()
    filter_fields = {
        "exchange": query.get("exchange", [""])[0],
        "board": query.get("board", [""])[0],
        "industry": query.get("industry", [""])[0],
        "portfolio_fit_status": query.get("portfolio_fit_status", [""])[0],
        "data_quality_status": query.get("data_quality_status", [""])[0],
    }
    strategy = query.get("strategy", [""])[0]

    def matches(item: dict[str, object]) -> bool:
        if search:
            haystack = " ".join(str(item.get(field) or "") for field in ("code", "name", "industry")).lower()
            if search not in haystack:
                return False
        for field, value in filter_fields.items():
            if value and str(item.get(field) or "") != value:
                return False
        if strategy and strategy not in item.get("strategies_list", []):
            return False
        return True

    filtered = [item for item in items if matches(item)]
    sort_key = query.get("sort", ["combined_score"])[0]
    direction = query.get("direction", ["desc"])[0]
    sortable_numeric = {
        "combined_score",
        "strategy_count",
        "industry_strength_score",
        "liquidity_score",
        "risk_penalty_score",
        "expected_total_position_pct_after_buy",
    }
    if sort_key in sortable_numeric:
        filtered.sort(
            key=lambda item: (
                parse_number(item.get(sort_key)) is None,
                parse_number(item.get(sort_key)) or 0,
                str(item.get("code") or ""),
            ),
            reverse=direction != "asc",
        )
    else:
        filtered.sort(key=lambda item: (str(item.get(sort_key) or ""), str(item.get("code") or "")), reverse=direction != "asc")

    try:
        limit = min(500, max(1, int(query.get("limit", ["200"])[0])))
    except ValueError:
        limit = 200
    return {
        "source": str(path),
        "available": path.exists(),
        "total_count": len(items),
        "filtered_count": len(filtered),
        "items": filtered[:limit],
        "filters": candidate_filters(items),
        "sort": {"key": sort_key, "direction": direction},
    }


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


def append_jsonl(path: Path, item: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def recent_trigger_refresh_events(limit: int) -> list[dict[str, object]]:
    if not TRIGGER_REFRESH_EVENT_FILE.exists():
        return []
    lines = TRIGGER_REFRESH_EVENT_FILE.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, object]] = []
    for line in lines[-limit:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return list(reversed(events))


def trigger_review_key(code: str, active_path: str, event_generated_at: object) -> str:
    return f"{code}:{active_path}:{event_generated_at or ''}"


def recent_trigger_review_statuses(limit: int = 500) -> list[dict[str, object]]:
    if not TRIGGER_REVIEW_STATUS_FILE.exists():
        return []
    lines = TRIGGER_REVIEW_STATUS_FILE.read_text(encoding="utf-8").splitlines()
    statuses: list[dict[str, object]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            statuses.append(item)
    return list(reversed(statuses))


def latest_trigger_review_status_by_key(statuses: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for item in statuses:
        key = str(item.get("review_key") or "")
        if key and key not in latest:
            latest[key] = item
    return latest


def classify_trigger_review_item(snapshot: dict[str, Any]) -> dict[str, Any]:
    after = snapshot.get("after") if isinstance(snapshot.get("after"), dict) else {}
    active_path = str(snapshot.get("active_path") or "")
    primary_status = str(after.get("primary_status") or "")
    plan_type = str(after.get("plan_type") or "")
    plan_status = str(after.get("plan_status") or "")
    trade_intent = str(after.get("manual_plan_trade_intent") or "")
    if active_path == "path3_recover":
        return {
            "status": "watch_only",
            "status_label": "风险降级观察",
            "action_label": "只观察，不交易",
            "target": "manual-execution-plan",
            "priority": 2,
        }
    if (
        primary_status == "ready"
        or plan_type in {"risk_reduce", "hard_exit"}
        or (trade_intent in {"risk_exit_reduce", "risk_exit_full"} and plan_status == "ready_for_manual_confirm")
    ):
        return {
            "status": "action_required",
            "status_label": "需要处理",
            "action_label": "查看执行计划",
            "target": "manual-execution-plan",
            "priority": 0,
        }
    if after.get("available") and (
        primary_status in {"blocked", "watch"}
        and (
            plan_status in {"near_stop_review", "watch", "wait_rebound_reduce"}
            or after.get("state") in {"exit_risk_review", "risk_reduction_review", "reverse_buyback_review"}
        )
    ):
        return {
            "status": "review_required",
            "status_label": "需要复核",
            "action_label": "查看复核结果",
            "target": "decision-card",
            "priority": 1,
        }
    return {
        "status": "watch_only",
        "status_label": "只观察",
        "action_label": "查看历史",
        "target": "decision-card",
        "priority": 2,
    }


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def execution_window_seconds(status: str) -> int:
    return {
        "action_required": 180,
        "review_required": 300,
        "watch_only": 600,
    }.get(status, 300)


def build_execution_window(
    *,
    status: str,
    created_at: object,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    start = parse_datetime(created_at)
    now = as_of or datetime.now().astimezone()
    seconds = execution_window_seconds(status)
    if start is None:
        return {
            "execution_window_seconds": seconds,
            "valid_until": None,
            "remaining_seconds": None,
            "validity_status": "unknown",
            "validity_label": "有效期未知",
            "expired": False,
            "expiry_action": "无法确认建议有效期，执行前先刷新完整日内决策链。",
        }
    if start.tzinfo is None and now.tzinfo is not None:
        start = start.replace(tzinfo=now.tzinfo)
    elif start.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=start.tzinfo)
    valid_until = start + timedelta(seconds=seconds)
    remaining = int((valid_until - now).total_seconds())
    if remaining <= 0:
        status_label = "已过期"
        validity_status = "expired"
        expiry_action = "该触发计划已过有效期；不要按旧价格直接操作，先刷新完整日内决策链。"
    elif remaining <= 60:
        status_label = "即将过期"
        validity_status = "expiring"
        expiry_action = "若要处理，先确认当前价仍在计划区间内；接近过期时优先刷新。"
    else:
        status_label = "有效"
        validity_status = "valid"
        expiry_action = "仍在有效期内；执行前仍需核对当前价、数据质量和人工计划。"
    return {
        "execution_window_seconds": seconds,
        "valid_until": valid_until.isoformat(timespec="seconds"),
        "remaining_seconds": max(0, remaining),
        "validity_status": validity_status,
        "validity_label": status_label,
        "expired": remaining <= 0,
        "expiry_action": expiry_action,
    }


def build_trigger_review_queue(
    events: list[dict[str, object]],
    *,
    limit: int = 20,
    statuses: list[dict[str, object]] | None = None,
    include_closed: bool = False,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    seen: set[str] = set()
    status_by_key = latest_trigger_review_status_by_key(statuses or [])
    for event in events:
        snapshots = event.get("trigger_action_snapshots") if isinstance(event.get("trigger_action_snapshots"), list) else []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            code = str(snapshot.get("code") or "")
            active_path = str(snapshot.get("active_path") or "")
            if not code:
                continue
            dedupe_key = f"{code}:{active_path}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            classification = classify_trigger_review_item(snapshot)
            after = snapshot.get("after") if isinstance(snapshot.get("after"), dict) else {}
            confirmation = snapshot.get("confirmation") if isinstance(snapshot.get("confirmation"), dict) else {}
            review_key = trigger_review_key(code, active_path, event.get("generated_at"))
            review_status = status_by_key.get(review_key) or {}
            review_resolution = str(review_status.get("resolution") or "open")
            if review_resolution in {"handled", "ignored"} and not include_closed:
                continue
            window = build_execution_window(status=str(classification.get("status") or ""), created_at=event.get("generated_at"), as_of=as_of)
            queue.append(
                {
                    "review_key": review_key,
                    "code": code,
                    "name": snapshot.get("name"),
                    "active_path": active_path,
                    "title": snapshot.get("title"),
                    "event_generated_at": event.get("generated_at"),
                    "decision_generated_at": event.get("decision_generated_at"),
                    "current_price": snapshot.get("current_price"),
                    "confirmed_price": confirmation.get("confirmed_price") or snapshot.get("current_price"),
                    "confirmation_window_seconds": confirmation.get("window_seconds"),
                    "after_label": after.get("label"),
                    "after_state_label": after.get("state_label"),
                    "after_plan_type": after.get("plan_type"),
                    "after_plan_status": after.get("plan_status"),
                    "after_primary_status": after.get("primary_status"),
                    "after_shares": after.get("shares"),
                    "after_price": after.get("price"),
                    "review_resolution": review_resolution,
                    "review_resolution_label": review_status.get("resolution_label") or {
                        "open": "待处理",
                        "viewed": "已查看",
                        "handled": "已处理",
                        "ignored": "暂不处理",
                    }.get(review_resolution, review_resolution),
                    "review_note": review_status.get("note") or "",
                    "review_updated_at": review_status.get("updated_at"),
                    **window,
                    **classification,
                }
            )
    queue.sort(key=lambda item: int(item.get("priority", 9)))
    return queue[:limit]


def handle_trigger_review_status(payload: dict[str, object]) -> dict[str, object]:
    code = str(payload.get("code") or "")
    active_path = str(payload.get("active_path") or "")
    event_generated_at = payload.get("event_generated_at") or ""
    resolution = str(payload.get("resolution") or "")
    if not code or not active_path or not event_generated_at:
        raise ValueError("code, active_path and event_generated_at are required")
    labels = {"viewed": "已查看", "handled": "已处理", "ignored": "暂不处理", "open": "待处理"}
    if resolution not in labels:
        raise ValueError("resolution must be one of viewed, handled, ignored, open")
    item = {
        "review_key": trigger_review_key(code, active_path, event_generated_at),
        "code": code,
        "active_path": active_path,
        "event_generated_at": event_generated_at,
        "resolution": resolution,
        "resolution_label": labels[resolution],
        "note": str(payload.get("note") or ""),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "dashboard",
    }
    append_jsonl(TRIGGER_REVIEW_STATUS_FILE, item)
    events = recent_trigger_refresh_events(100)
    queue = build_trigger_review_queue(events, statuses=recent_trigger_review_statuses(), limit=20)
    return {"ok": True, "status": item, "queue": queue}


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


def compact_decision_summary(card: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(card, dict):
        return {"available": False, "label": "未找到决策卡"}
    decision = card.get("decision") if isinstance(card.get("decision"), dict) else {}
    structured = decision.get("structured_conclusion") if isinstance(decision.get("structured_conclusion"), dict) else {}
    manual_plan = card.get("manual_execution_plan") if isinstance(card.get("manual_execution_plan"), dict) else {}
    price_table = card.get("price_action_table") if isinstance(card.get("price_action_table"), dict) else {}
    primary = price_table.get("primary_action") if isinstance(price_table.get("primary_action"), dict) else {}
    action = (
        structured.get("current_action")
        or primary.get("action")
        or decision.get("action_label")
        or card.get("state_label")
        or card.get("state")
        or "--"
    )
    status = manual_plan.get("status_label") or primary.get("status_label") or card.get("state_label") or "--"
    shares = manual_plan.get("shares") or primary.get("shares")
    price = primary.get("price") or structured.get("primary_price") or ""
    label_parts = [str(action), str(status)]
    if shares:
        label_parts.append(f"{shares}股")
    if price:
        label_parts.append(str(price))
    return {
        "available": True,
        "state": card.get("state"),
        "state_label": card.get("state_label"),
        "action": action,
        "status": status,
        "plan_type": manual_plan.get("plan_type"),
        "plan_status": manual_plan.get("status"),
        "manual_plan_status_label": manual_plan.get("status_label"),
        "manual_plan_action_label": manual_plan.get("action_label"),
        "manual_plan_side": manual_plan.get("side"),
        "manual_plan_trade_intent": manual_plan.get("trade_intent"),
        "manual_plan_price_zone": manual_plan.get("price_zone"),
        "manual_plan_post_trade_shares": manual_plan.get("post_trade_shares"),
        "manual_plan_steps": list(manual_plan.get("steps") or [])[:5] if isinstance(manual_plan.get("steps"), list) else [],
        "primary_status": primary.get("status"),
        "primary_action": primary.get("action"),
        "primary_status_label": primary.get("status_label"),
        "primary_operation": primary.get("operation"),
        "primary_trigger": primary.get("trigger"),
        "shares": shares,
        "price": price,
        "label": "；".join(label_parts),
    }


def build_trigger_refresh_diffs(
    triggers: list[object],
    before_report: dict[str, object] | None,
    after_report: dict[str, object] | None,
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    before_cards = (before_report or {}).get("cards")
    after_cards = (after_report or {}).get("cards")
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        code = str(trigger.get("code") or "")
        if not code:
            continue
        before = compact_decision_summary(find_code_item(before_cards, code))
        after = compact_decision_summary(find_code_item(after_cards, code))
        changed = any(
            before.get(key) != after.get(key)
            for key in ("state", "action", "status", "plan_type", "plan_status", "primary_status", "shares", "price")
        )
        name = str(trigger.get("name") or code)
        trigger_title = str(trigger.get("title") or trigger.get("active_path") or "盘中触发")
        message = f"{name}：{trigger_title}；刷新前：{before.get('label')}；刷新后：{after.get('label')}。"
        if not changed:
            message += "结论暂未变化，继续按当前触发路径观察。"
        diffs.append(
            {
                "code": code,
                "name": name,
                "trigger": trigger_title,
                "active_path": trigger.get("active_path"),
                "changed": changed,
                "before": before,
                "after": after,
                "message": message,
            }
        )
    return diffs


def build_trigger_action_snapshots(
    triggers: list[object],
    before_report: dict[str, object] | None,
    after_report: dict[str, object] | None,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    before_cards = (before_report or {}).get("cards")
    after_cards = (after_report or {}).get("cards")
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        code = str(trigger.get("code") or "")
        if not code:
            continue
        confirmation = trigger.get("confirmation") if isinstance(trigger.get("confirmation"), dict) else {}
        snapshots.append(
            {
                "code": code,
                "name": trigger.get("name"),
                "active_path": trigger.get("active_path"),
                "title": trigger.get("title"),
                "current_price": trigger.get("current_price"),
                "confirmation": {
                    "status": confirmation.get("status") or trigger.get("confirmation_status"),
                    "first_seen_at": confirmation.get("first_seen_at") or trigger.get("confirmation_first_seen_at"),
                    "window_seconds": confirmation.get("window_seconds") or trigger.get("confirmation_window_seconds"),
                    "elapsed_seconds": confirmation.get("elapsed_seconds") or trigger.get("confirmation_elapsed_seconds"),
                    "confirmed_at": confirmation.get("confirmed_at"),
                    "confirmed_price": confirmation.get("confirmed_price") or trigger.get("current_price"),
                },
                "before": compact_decision_summary(find_code_item(before_cards, code)),
                "after": compact_decision_summary(find_code_item(after_cards, code)),
            }
        )
    return snapshots


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
    before_report = load_json(API_FILES["/api/decision-cards"]) or {}
    requested_at = datetime.now().astimezone().isoformat(timespec="seconds")
    total_assets_raw = snapshot.get("total_assets")
    total_assets = float(total_assets_raw) if isinstance(total_assets_raw, (int, float)) else 25480.0
    triggers = payload.get("triggers") if isinstance(payload.get("triggers"), list) else []
    refresh = run_refresh_commands(total_assets)
    refreshed_report = load_json(API_FILES["/api/decision-cards"]) or {}
    diffs = build_trigger_refresh_diffs(triggers, before_report, refreshed_report)
    action_snapshots = build_trigger_action_snapshots(triggers, before_report, refreshed_report)
    event = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "requested_at": requested_at,
        "trigger_count": len(triggers),
        "triggers": triggers,
        "decision_generated_at": refreshed_report.get("generated_at"),
        "state_counts": refreshed_report.get("state_counts") or {},
        "diffs": diffs,
        "trigger_action_snapshots": action_snapshots,
    }
    append_jsonl(TRIGGER_REFRESH_EVENT_FILE, event)
    return {
        "ok": True,
        "trigger_count": len(triggers),
        "refresh": refresh,
        "generated_at": refreshed_report.get("generated_at"),
        "state_counts": refreshed_report.get("state_counts") or {},
        "diffs": diffs,
        "trigger_action_snapshots": action_snapshots,
        "event": event,
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
        if parsed.path == "/api/candidate-pool":
            self.send_json(filtered_candidates(parse_qs(parsed.query)))
            return
        if parsed.path == "/api/events":
            query = parse_qs(parsed.query)
            try:
                limit = min(100, max(1, int(query.get("limit", [20])[0])))
            except ValueError:
                limit = 20
            self.send_json({"events": recent_events(limit)})
            return
        if parsed.path == "/api/intraday-trigger-refresh-events":
            query = parse_qs(parsed.query)
            try:
                limit = min(100, max(1, int(query.get("limit", [20])[0])))
            except ValueError:
                limit = 20
            self.send_json({"events": recent_trigger_refresh_events(limit)})
            return
        if parsed.path == "/api/intraday-trigger-review-queue":
            query = parse_qs(parsed.query)
            try:
                limit = min(100, max(1, int(query.get("limit", [20])[0])))
            except ValueError:
                limit = 20
            include_closed = str(query.get("include_closed", ["false"])[0]).lower() in {"1", "true", "yes"}
            events = recent_trigger_refresh_events(100)
            self.send_json({"items": build_trigger_review_queue(events, limit=limit, statuses=recent_trigger_review_statuses(), include_closed=include_closed)})
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
            "/api/intraday-trigger-review-status": handle_trigger_review_status,
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
