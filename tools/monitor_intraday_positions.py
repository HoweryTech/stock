#!/usr/bin/env python3
"""Continuously monitor holding quotes and write quasi-real-time reports."""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.fetch_holding_research import fetch_realtime_quotes
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from fetch_holding_research import fetch_realtime_quotes
    from risk_check import as_float, load_yaml, value_at


def read_close_history(path: Path) -> dict[str, list[float]]:
    histories: dict[str, list[tuple[str, float]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            close = as_float(row.get("close"))
            code = str(row.get("code") or "")
            if code and close is not None:
                histories.setdefault(code, []).append((str(row.get("trade_date") or ""), close))
    return {code: [close for _, close in sorted(rows)] for code, rows in histories.items()}


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def moving_averages(closes: list[float]) -> tuple[float | None, float | None]:
    ma5 = average(closes[-5:]) if len(closes) >= 5 else None
    ma20 = average(closes[-20:]) if len(closes) >= 20 else None
    return ma5, ma20


def analyze_quote(
    position: dict[str, Any],
    quote: dict[str, Any],
    closes: list[float],
    *,
    total_assets: float,
    max_stale_seconds: int,
    now_timestamp: float,
) -> dict[str, Any]:
    code = str(value_at(position, "stock.code") or "")
    shares = as_float(value_at(position, "entry.shares"), 0.0) or 0.0
    entry_price = as_float(value_at(position, "entry.entry_price"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    price = as_float(quote.get("latest_price"))
    change_pct = as_float(quote.get("change_pct"))
    quote_timestamp = as_float(quote.get("quote_timestamp"))
    quote_lag_seconds = None if quote_timestamp is None else max(0.0, now_timestamp - quote_timestamp)
    ma5, ma20 = moving_averages(closes)

    market_value = price * shares if price is not None else None
    unrealized_pnl = (price - entry_price) * shares if price is not None and entry_price is not None else None
    return_pct = (price / entry_price - 1) * 100 if price is not None and entry_price else None
    live_position_pct = market_value / total_assets * 100 if market_value is not None and total_assets > 0 else None
    signals: list[dict[str, str]] = []

    if quote_lag_seconds is None or quote_lag_seconds > max_stale_seconds:
        signals.append({"code": "stale_quote", "severity": "block", "message": "行情时间戳缺失或超过允许延迟，暂停盘中判断。"})
    if change_pct is not None and change_pct <= -9.8:
        signals.append({"code": "limit_down_or_near", "severity": "risk", "message": f"当日涨跌幅 {change_pct:.2f}%，接近或达到跌停。"})
    elif change_pct is not None and change_pct <= -5:
        signals.append({"code": "intraday_drop", "severity": "risk", "message": f"当日跌幅 {change_pct:.2f}%，波动风险较高。"})
    if change_pct is not None and change_pct >= 9.8:
        signals.append({"code": "limit_up_or_near", "severity": "warning", "message": f"当日涨跌幅 {change_pct:.2f}%，正T不追价。"})
    if price is not None and ma20 is not None and price < ma20:
        signals.append({"code": "below_ma20", "severity": "warning", "message": f"现价低于20日均线 {ma20:.2f} 元。"})
    if price is not None and ma5 is not None and price < ma5:
        signals.append({"code": "below_ma5", "severity": "info", "message": f"现价低于5日均线 {ma5:.2f} 元。"})
    if position_pct > 10:
        signals.append({"code": "position_limit_exceeded", "severity": "risk", "message": f"原始单票仓位 {position_pct:.2f}% 超过10%上限。"})

    signal_codes = {item["code"] for item in signals}
    if "stale_quote" in signal_codes:
        state = "data_stale"
    elif signal_codes & {"limit_down_or_near", "position_limit_exceeded"}:
        state = "risk_review"
    elif "intraday_drop" in signal_codes or "below_ma20" in signal_codes:
        state = "no_add_watch"
    else:
        state = "observe"

    return {
        "code": code,
        "name": quote.get("name") or value_at(position, "stock.name"),
        "state": state,
        "quote": {**quote, "quote_lag_seconds": None if quote_lag_seconds is None else round(quote_lag_seconds, 3)},
        "position": {
            "shares": shares,
            "entry_price": entry_price,
            "market_value": None if market_value is None else round(market_value, 2),
            "unrealized_pnl": None if unrealized_pnl is None else round(unrealized_pnl, 2),
            "return_pct": None if return_pct is None else round(return_pct, 4),
            "original_position_pct": position_pct,
            "live_position_pct": None if live_position_pct is None else round(live_position_pct, 4),
        },
        "technicals": {"ma5": ma5, "ma20": ma20},
        "signals": signals,
        "guardrails": {"add_allowed": False, "t_trade_allowed": False, "auto_order": False},
    }


def build_snapshot(
    position_paths: list[Path],
    daily_bars: Path,
    *,
    total_assets: float,
    max_stale_seconds: int,
) -> dict[str, Any]:
    positions = [load_yaml(path) for path in position_paths]
    codes = [str(value_at(position, "stock.code") or "") for position in positions]
    quotes = {quote["code"]: quote for quote in fetch_realtime_quotes(codes)}
    histories = read_close_history(daily_bars)
    now = datetime.now().astimezone()
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path, position, code in zip(position_paths, positions, codes):
        quote = quotes.get(code)
        if not quote:
            errors.append({"code": code, "message": "本轮未返回行情。"})
            continue
        item = analyze_quote(
            position,
            quote,
            histories.get(code, []),
            total_assets=total_assets,
            max_stale_seconds=max_stale_seconds,
            now_timestamp=now.timestamp(),
        )
        item["position_path"] = str(path)
        items.append(item)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": "eastmoney_public_quote_snapshot",
        "mode": "quasi_realtime_non_guaranteed",
        "interval_note": "公开网页接口无时效和可用性保证，不用于自动下单。",
        "total_assets": total_assets,
        "position_count": len(position_paths),
        "success_count": len(items),
        "errors": errors,
        "items": items,
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def render_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        "# 持仓准实时监控",
        "",
        f"更新时间：{snapshot['generated_at']}",
        "",
        "公开行情接口无时效保证；本报告只用于监控，不自动下单。",
        "",
        "| 代码 | 名称 | 现价 | 涨跌幅 | 行情延迟 | 持仓收益 | 状态 | 信号 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in snapshot["items"]:
        quote = item["quote"]
        position = item["position"]
        lag = quote.get("quote_lag_seconds")
        signal_text = "、".join(signal["code"] for signal in item["signals"]) or "无"
        return_text = "-" if position["return_pct"] is None else f"{position['return_pct']:.2f}%"
        lines.append(
            f"| {item['code']} | {item['name']} | {quote.get('latest_price', '-')} | "
            f"{quote.get('change_pct', '-')}% | {'-' if lag is None else f'{lag:.1f}s'} | "
            f"{return_text} | "
            f"{item['state']} | {signal_text} |"
        )
    if snapshot["errors"]:
        lines.extend(["", "## 本轮错误", ""])
        lines.extend(f"- {item['code']}: {item['message']}" for item in snapshot["errors"])
    lines.append("")
    return "\n".join(lines)


def state_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        item["code"]: {"state": item["state"], "signals": sorted(signal["code"] for signal in item["signals"])}
        for item in snapshot["items"]
    }


def append_event(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "generated_at": snapshot["generated_at"],
        "signature": state_signature(snapshot),
        "prices": {item["code"]: item["quote"].get("latest_price") for item in snapshot["items"]},
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def ensure_single_instance(pid_path: Path) -> None:
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(existing_pid, 0)
        except (ValueError, ProcessLookupError):
            pid_path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"monitor already running with pid {existing_pid}")
    atomic_write(pid_path, f"{os.getpid()}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor holdings with quasi-real-time public quotes.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--total-assets", type=float, required=True)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--archive-interval", type=float, default=300.0)
    parser.add_argument("--max-stale-seconds", type=int, default=60)
    parser.add_argument("--latest-json", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--latest-markdown", default="reports/intraday-monitor.latest.md")
    parser.add_argument("--event-log", default="data/metadata/intraday-monitor.events.jsonl")
    parser.add_argument("--archive-dir", default="data/metadata/intraday-archive")
    parser.add_argument("--pid-file", default="data/metadata/intraday-monitor.pid")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--iterations", type=int, help="Stop after N iterations; intended for verification.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pid_path = Path(args.pid_file)
    ensure_single_instance(pid_path)
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    paths = expand_position_paths(args.positions)
    latest_json = Path(args.latest_json)
    latest_markdown = Path(args.latest_markdown)
    event_log = Path(args.event_log)
    archive_dir = Path(args.archive_dir)
    previous_signature: dict[str, Any] | None = None
    last_archive = 0.0
    iteration = 0
    try:
        while not stop_requested:
            started = time.time()
            try:
                snapshot = build_snapshot(
                    paths,
                    Path(args.daily_bars),
                    total_assets=args.total_assets,
                    max_stale_seconds=args.max_stale_seconds,
                )
                write_json(latest_json, snapshot)
                atomic_write(latest_markdown, render_markdown(snapshot))
                signature = state_signature(snapshot)
                if signature != previous_signature:
                    append_event(event_log, snapshot)
                    previous_signature = signature
                if started - last_archive >= args.archive_interval:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    write_json(archive_dir / f"snapshot-{stamp}.json", snapshot)
                    last_archive = started
                print(f"[{snapshot['generated_at']}] updated {snapshot['success_count']}/{snapshot['position_count']}", flush=True)
            except Exception as exc:
                print(f"[{datetime.now().isoformat(timespec='seconds')}] monitor iteration failed: {exc}", file=sys.stderr, flush=True)
            iteration += 1
            if args.once or (args.iterations is not None and iteration >= args.iterations):
                break
            remaining = max(0.0, args.interval - (time.time() - started))
            if remaining:
                time.sleep(remaining)
    finally:
        pid_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
