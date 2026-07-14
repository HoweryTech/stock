#!/usr/bin/env python3
"""Check whether a holding is suitable for T+0-style manual watchlist review."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml, value_at


@dataclass
class CheckItem:
    code: str
    message: str


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def read_bars(path: Path, code: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if (row.get("code") or "").strip() != code:
                continue
            rows.append(
                {
                    "trade_date": (row.get("trade_date") or "").strip(),
                    "open": as_float(row.get("open")),
                    "high": as_float(row.get("high")),
                    "low": as_float(row.get("low")),
                    "close": as_float(row.get("close")),
                    "turnover": as_float(row.get("turnover"), 0.0) or 0.0,
                    "is_limit_up": parse_bool(row.get("is_limit_up")),
                    "is_limit_down": parse_bool(row.get("is_limit_down")),
                    "is_suspended": parse_bool(row.get("is_suspended")),
                }
            )
    rows.sort(key=lambda item: item["trade_date"])
    return rows


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def pct_change(current: float | None, base: float | None) -> float | None:
    if current is None or base in (None, 0):
        return None
    return (current / base - 1) * 100


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def latest_metrics(rows: list[dict[str, Any]], short_window: int, mid_window: int) -> dict[str, Any]:
    latest = rows[-1]
    short_rows = rows[-short_window:]
    mid_rows = rows[-mid_window:]
    close = latest["close"]
    high_values = [row["high"] for row in mid_rows if row["high"] is not None]
    low_values = [row["low"] for row in mid_rows if row["low"] is not None]
    close_short = [row["close"] for row in short_rows if row["close"] is not None]
    close_mid = [row["close"] for row in mid_rows if row["close"] is not None]
    turnover_short = [row["turnover"] for row in short_rows if row["turnover"] is not None]
    range_mid = [
        (row["high"] - row["low"]) / row["close"] * 100
        for row in mid_rows
        if row["high"] is not None and row["low"] is not None and row["close"]
    ]

    recent_high = max(high_values) if high_values else None
    recent_low = min(low_values) if low_values else None
    ma_short = average(close_short)
    ma_mid = average(close_mid)
    avg_range_pct = average(range_mid)
    latest_range_pct = None
    if latest["high"] is not None and latest["low"] is not None and close:
        latest_range_pct = (latest["high"] - latest["low"]) / close * 100

    return {
        "trade_date": latest["trade_date"],
        "latest_close": close,
        "latest_high": latest["high"],
        "latest_low": latest["low"],
        "ma_short": ma_short,
        "ma_mid": ma_mid,
        "return_short_pct": pct_change(close, rows[-short_window]["close"] if len(rows) >= short_window else None),
        "return_mid_pct": pct_change(close, rows[-mid_window]["close"] if len(rows) >= mid_window else None),
        "distance_to_ma_short_pct": pct_change(close, ma_short),
        "distance_to_ma_mid_pct": pct_change(close, ma_mid),
        "drawdown_from_recent_high_pct": pct_change(close, recent_high),
        "bounce_from_recent_low_pct": pct_change(close, recent_low),
        "recent_high": recent_high,
        "recent_low": recent_low,
        "latest_range_pct": latest_range_pct,
        "avg_range_pct": avg_range_pct,
        "turnover_ratio_vs_short_avg": latest["turnover"] / average(turnover_short) if average(turnover_short) else None,
        "is_limit_up": latest["is_limit_up"],
        "is_limit_down": latest["is_limit_down"],
        "is_suspended": latest["is_suspended"],
    }


def check_t_opportunity(
    profile: dict[str, Any],
    position: dict[str, Any],
    bars: list[dict[str, Any]],
    *,
    short_window: int = 5,
    mid_window: int = 20,
    near_stop_pct: float = 3.0,
    pullback_pct: float = 3.0,
    overextended_pct: float = 6.0,
    min_spread_pct: float = 1.2,
) -> dict[str, Any]:
    code = value_at(position, "stock.code")
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []
    positive_t_evidence: list[CheckItem] = []
    reverse_t_evidence: list[CheckItem] = []

    if len(bars) < mid_window:
        blockers.append(CheckItem("insufficient_daily_bars", f"日线数量 {len(bars)} 少于中期窗口 {mid_window}，无法验证做T环境。"))
        metrics: dict[str, Any] = {"bars_count": len(bars)}
    else:
        metrics = latest_metrics(bars, short_window, mid_window)

    latest_close = as_float(metrics.get("latest_close"))
    stop_loss_price = as_float(value_at(position, "risk.stop_loss_price"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    current_price = as_float(value_at(position, "tracking.current_price"))
    max_stock_pct = as_float(value_at(profile, "risk.max_position_pct_per_stock"), 100.0) or 100.0

    if latest_close is not None and current_price is not None and abs(latest_close - current_price) / latest_close > 0.03:
        warnings.append(CheckItem("position_price_stale", "持仓 current_price 与最新日线收盘价偏离超过 3%，建议先更新持仓价格。"))

    if metrics.get("is_suspended"):
        blockers.append(CheckItem("stock_suspended", "最新交易日停牌，不能做T。"))
    if metrics.get("is_limit_down"):
        blockers.append(CheckItem("limit_down", "最新交易日跌停，优先处理流动性和止损风险。"))
    if metrics.get("is_limit_up"):
        warnings.append(CheckItem("limit_up", "最新交易日涨停，正T买入腿不应追价。"))

    distance_to_stop_pct = None
    if latest_close is not None and stop_loss_price is not None:
        if latest_close <= stop_loss_price:
            blockers.append(CheckItem("stop_loss_triggered", f"最新收盘价 {latest_close:.2f} 已触发止损价 {stop_loss_price:.2f}，不做T。"))
        else:
            distance_to_stop_pct = (latest_close - stop_loss_price) / latest_close * 100
            if distance_to_stop_pct <= near_stop_pct:
                blockers.append(CheckItem("near_stop_loss", f"最新收盘价距离止损价仅 {distance_to_stop_pct:.2f}%，不做T，先处理退出风险。"))
    else:
        blockers.append(CheckItem("missing_price_or_stop_loss", "缺少最新价格或止损价，无法验证做T风险。"))

    if position_pct <= 0:
        blockers.append(CheckItem("missing_base_position", "没有可识别底仓，A股做T无法验证。"))
    if position_pct > max_stock_pct:
        blockers.append(CheckItem("stock_position_limit_exceeded", f"单票仓位 {position_pct:.2f}% 超过上限 {max_stock_pct:.2f}%，不做T，优先降风险。"))

    distance_to_ma_mid = as_float(metrics.get("distance_to_ma_mid_pct"))
    distance_to_ma_short = as_float(metrics.get("distance_to_ma_short_pct"))
    return_short = as_float(metrics.get("return_short_pct"))
    return_mid = as_float(metrics.get("return_mid_pct"))
    drawdown = as_float(metrics.get("drawdown_from_recent_high_pct"))
    latest_range = as_float(metrics.get("latest_range_pct"))
    avg_range = as_float(metrics.get("avg_range_pct"))

    if avg_range is not None and avg_range < min_spread_pct:
        warnings.append(CheckItem("spread_too_small", f"近 {mid_window} 日平均振幅 {avg_range:.2f}% 低于默认价差要求 {min_spread_pct:.2f}%。"))
    elif avg_range is not None:
        info.append(CheckItem("spread_available", f"近 {mid_window} 日平均振幅 {avg_range:.2f}%，具备人工观察价差的基础。"))

    trend_intact = return_mid is not None and return_mid > 0 and distance_to_ma_mid is not None and distance_to_ma_mid >= 0
    pulled_back = drawdown is not None and drawdown <= -pullback_pct
    near_short_ma = distance_to_ma_short is not None and abs(distance_to_ma_short) <= 2.0
    overextended = (
        (return_short is not None and return_short >= overextended_pct)
        or (distance_to_ma_short is not None and distance_to_ma_short >= overextended_pct)
    )

    if trend_intact:
        info.append(CheckItem("mid_trend_intact", "中期趋势未破，允许继续观察T机会。"))
    elif return_mid is not None:
        warnings.append(CheckItem("mid_trend_not_confirmed", "中期趋势未确认，做T胜率基础不足。"))

    if trend_intact and (pulled_back or near_short_ma) and avg_range is not None and avg_range >= min_spread_pct:
        positive_t_evidence.append(CheckItem("positive_t_setup", "中期趋势未破，且出现回踩或靠近短期均线，可列入正T观察。"))
    if overextended and avg_range is not None and avg_range >= min_spread_pct:
        reverse_t_evidence.append(CheckItem("reverse_t_setup", "短线涨幅或短期均线偏离较高，可列入反T观察。"))
    if latest_range is not None and avg_range is not None and latest_range > avg_range * 1.8:
        warnings.append(CheckItem("range_expanded", "最新交易日振幅显著放大，日内执行需要降低仓位或等待确认。"))

    if reverse_t_evidence and positive_t_evidence:
        market_setup = "both_setups_watch_only"
    elif reverse_t_evidence:
        market_setup = "reverse_t_candidate"
    elif positive_t_evidence:
        market_setup = "positive_t_candidate"
    else:
        market_setup = "no_clear_t_setup"

    if blockers:
        conclusion = "blocked"
        action = "no_t_trade"
        positive_t_evidence = []
        reverse_t_evidence = []
    elif reverse_t_evidence and positive_t_evidence:
        conclusion = "needs_manual_review"
        action = "both_setups_watch_only"
    elif reverse_t_evidence:
        conclusion = "reverse_t_candidate"
        action = "watch_sell_high_buy_back"
    elif positive_t_evidence:
        conclusion = "positive_t_candidate"
        action = "watch_buy_low_sell_base"
    else:
        conclusion = "watch_only"
        action = "no_clear_t_setup"

    metrics["distance_to_stop_pct"] = distance_to_stop_pct
    metrics["bars_count"] = len(bars)

    return {
        "stock_code": code,
        "stock_name": value_at(position, "stock.name"),
        "position_id": value_at(position, "position.id"),
        "market_setup": market_setup,
        "conclusion": conclusion,
        "action": action,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
        "positive_t_evidence": [item.__dict__ for item in positive_t_evidence],
        "reverse_t_evidence": [item.__dict__ for item in reverse_t_evidence],
        "calculations": metrics,
        "execution_guardrails": [
            "日线结论只用于盘前/盘后筛查，不能替代分时确认。",
            "正T必须先定义买入价、卖出价、失败后是否转为加仓，以及最大新增仓位。",
            "反T必须先定义卖出价、买回价、失败后是否接受减仓，以及保留底仓比例。",
            "触发止损、接近止损、仓位超限或停牌跌停时不做T。",
        ],
    }


def print_text(result: dict[str, Any]) -> None:
    print(f"标的：{result.get('stock_code') or '-'} {result.get('stock_name') or '-'}")
    print(f"持仓编号：{result.get('position_id') or '-'}")
    print(f"结论：{result['conclusion']}")
    print(f"动作：{result['action']}")
    for title, key in (
        ("阻断项", "blockers"),
        ("提醒项", "warnings"),
        ("正T证据", "positive_t_evidence"),
        ("反T证据", "reverse_t_evidence"),
        ("信息", "info"),
    ):
        print(f"\n{title}：")
        items = result[key]
        if not items:
            print("- 无")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")
    print("\n核心计算：")
    for key in (
        "trade_date",
        "latest_close",
        "ma_short",
        "ma_mid",
        "return_short_pct",
        "return_mid_pct",
        "distance_to_ma_short_pct",
        "distance_to_ma_mid_pct",
        "drawdown_from_recent_high_pct",
        "avg_range_pct",
        "distance_to_stop_pct",
        "bars_count",
    ):
        value = result["calculations"].get(key)
        print(f"- {key}: {'-' if value is None else value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check T-trade watchlist opportunity for one holding.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--position", required=True, help="Path to position YAML.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Normalized daily bars CSV.")
    parser.add_argument("--auto-fetch", action="store_true", help="Fetch latest daily bars for this position before checking.")
    parser.add_argument("--fetch-datalen", type=int, default=120, help="Daily bars to fetch when --auto-fetch is set.")
    parser.add_argument("--short-window", type=int, default=5, help="Short window for T setup checks.")
    parser.add_argument("--mid-window", type=int, default=20, help="Mid window for trend checks.")
    parser.add_argument("--near-stop-pct", type=float, default=3.0, help="Block T when close is this close above stop loss.")
    parser.add_argument("--pullback-pct", type=float, default=3.0, help="Recent-high drawdown threshold for positive T watch.")
    parser.add_argument("--overextended-pct", type=float, default=6.0, help="Short-term overextension threshold for reverse T watch.")
    parser.add_argument("--min-spread-pct", type=float, default=1.2, help="Minimum average daily range for T watch.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile = load_yaml(Path(args.profile))
        position = load_yaml(Path(args.position))
        code = value_at(position, "stock.code")
        if not code:
            raise ValueError("position is missing stock.code")
        if args.auto_fetch:
            try:
                from tools.fetch_daily_bars_sina import fetch_daily_bars
            except ModuleNotFoundError:
                from fetch_daily_bars_sina import fetch_daily_bars

            fetch_daily_bars([str(code)], Path(args.daily_bars), datalen=args.fetch_datalen, merge_existing=True)
        bars = read_bars(Path(args.daily_bars), code)
        result = check_t_opportunity(
            profile,
            position,
            bars,
            short_window=args.short_window,
            mid_window=args.mid_window,
            near_stop_pct=args.near_stop_pct,
            pullback_pct=args.pullback_pct,
            overextended_pct=args.overextended_pct,
            min_spread_pct=args.min_spread_pct,
        )
    except Exception as exc:
        print(f"check T-trade opportunity failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
