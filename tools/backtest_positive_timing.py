#!/usr/bin/env python3
"""Backtest positive-T intraday timing score thresholds from cached 5-minute bars."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.build_realtime_decision_cards import build_positive_timing, load_minute_bars
    from tools.check_portfolio_positions import expand_position_paths
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from build_realtime_decision_cards import build_positive_timing, load_minute_bars
    from check_portfolio_positions import expand_position_paths
    from risk_check import as_float, load_yaml, value_at


def round4(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def trade_fees(buy_price: float, sell_price: float, shares: int, costs: dict[str, float]) -> dict[str, float]:
    buy_amount = buy_price * shares
    sell_amount = sell_price * shares
    buy_commission = max(buy_amount * costs["commission_rate"], costs["minimum_commission"])
    sell_commission = max(sell_amount * costs["commission_rate"], costs["minimum_commission"])
    stamp_duty = sell_amount * costs["stamp_duty_rate"]
    transfer_fee = (buy_amount + sell_amount) * costs["transfer_fee_rate"]
    total = buy_commission + sell_commission + stamp_duty + transfer_fee
    gross = (sell_price - buy_price) * shares
    return {
        "gross_profit": round(gross, 4),
        "fees": round(total, 4),
        "net_profit": round(gross - total, 4),
    }


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def recent_range_pct(bars: list[dict[str, Any]], window: int = 20) -> float | None:
    ranges = []
    for bar in bars[-window:]:
        high = as_float(bar.get("high"))
        low = as_float(bar.get("low"))
        close = as_float(bar.get("close"))
        if high is not None and low is not None and close not in (None, 0):
            ranges.append((high - low) / close * 100)
    return average(ranges)


def fee_aware_target_pct(
    buy_price: float,
    shares: int,
    costs: dict[str, float],
    *,
    minimum_net_profit: float,
    start_pct: float,
    max_pct: float,
) -> float | None:
    pct = start_pct
    while pct <= max_pct + 1e-9:
        sell_price = buy_price * (1 + pct / 100)
        if trade_fees(buy_price, sell_price, shares, costs)["net_profit"] >= minimum_net_profit:
            return pct
        pct += 0.05
    return None


def trade_bounds(
    prefix: list[dict[str, Any]],
    buy_price: float,
    shares: int,
    costs: dict[str, float],
    *,
    target_pct: float,
    stop_pct: float,
    adaptive: bool,
    min_target_pct: float,
    max_target_pct: float,
    min_stop_pct: float,
    max_stop_pct: float,
    range_target_multiplier: float,
    range_stop_multiplier: float,
    minimum_net_profit: float,
) -> dict[str, Any]:
    if adaptive:
        range_pct = recent_range_pct(prefix) or target_pct
        raw_target = clamp(range_pct * range_target_multiplier, min_target_pct, max_target_pct)
        raw_stop = clamp(range_pct * range_stop_multiplier, min_stop_pct, max_stop_pct)
    else:
        range_pct = recent_range_pct(prefix)
        raw_target = target_pct
        raw_stop = stop_pct
    fee_target = fee_aware_target_pct(
        buy_price,
        shares,
        costs,
        minimum_net_profit=minimum_net_profit,
        start_pct=raw_target,
        max_pct=max_target_pct if adaptive else max(raw_target, max_target_pct),
    )
    return {
        "adaptive": adaptive,
        "range_pct": round4(range_pct),
        "raw_target_pct": round4(raw_target),
        "target_pct": round4(fee_target),
        "stop_pct": round4(raw_stop),
        "fee_blocked": fee_target is None,
    }


def group_by_day(bars: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        timestamp = str(bar.get("timestamp") or "")
        if timestamp:
            grouped[timestamp[:10]].append(bar)
    return {day: sorted(items, key=lambda item: str(item.get("timestamp") or "")) for day, items in sorted(grouped.items())}


def score_prefix(code: str, prefix: list[dict[str, Any]]) -> dict[str, Any]:
    current = as_float(prefix[-1].get("close"))
    intraday = {
        "code": code,
        "quote": {"latest_price": current},
        "capital_flow": {"main_net_inflow_ratio_pct": 0.0},
    }
    return build_positive_timing(intraday, {"conclusion": "positive_t_candidate"}, prefix)


def simulate_day(
    code: str,
    day_bars: list[dict[str, Any]],
    *,
    threshold: float,
    horizon_bars: int,
    target_pct: float,
    stop_pct: float,
    trade_shares: int,
    costs: dict[str, float],
    adaptive_bounds: bool = False,
    min_target_pct: float = 1.2,
    max_target_pct: float = 4.0,
    min_stop_pct: float = 0.8,
    max_stop_pct: float = 2.0,
    range_target_multiplier: float = 1.2,
    range_stop_multiplier: float = 0.8,
    minimum_net_profit: float = 5.0,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    index = 19
    while index < len(day_bars) - 1:
        timing = score_prefix(code, day_bars[: index + 1])
        score = as_float(timing.get("score"))
        if score is None or score < threshold:
            index += 1
            continue
        entry_index = index + 1
        entry_bar = day_bars[entry_index]
        buy_price = as_float(entry_bar.get("open"))
        if buy_price in (None, 0):
            index += 1
            continue
        bounds = trade_bounds(
            day_bars[: index + 1],
            buy_price,
            trade_shares,
            costs,
            target_pct=target_pct,
            stop_pct=stop_pct,
            adaptive=adaptive_bounds,
            min_target_pct=min_target_pct,
            max_target_pct=max_target_pct,
            min_stop_pct=min_stop_pct,
            max_stop_pct=max_stop_pct,
            range_target_multiplier=range_target_multiplier,
            range_stop_multiplier=range_stop_multiplier,
            minimum_net_profit=minimum_net_profit,
        )
        if bounds["fee_blocked"]:
            index += 1
            continue
        effective_target_pct = float(bounds["target_pct"])
        effective_stop_pct = float(bounds["stop_pct"])
        target_price = round(buy_price * (1 + effective_target_pct / 100), 4)
        stop_price = round(buy_price * (1 - effective_stop_pct / 100), 4)
        outcome = "timeout"
        exit_price = as_float(day_bars[min(len(day_bars) - 1, entry_index + horizon_bars)].get("close"), buy_price) or buy_price
        exit_time = day_bars[min(len(day_bars) - 1, entry_index + horizon_bars)].get("timestamp")
        for bar in day_bars[entry_index : min(len(day_bars), entry_index + horizon_bars + 1)]:
            low = as_float(bar.get("low"))
            high = as_float(bar.get("high"))
            if low is not None and low <= stop_price:
                outcome = "stopped"
                exit_price = stop_price
                exit_time = bar.get("timestamp")
                break
            if high is not None and high >= target_price:
                outcome = "completed"
                exit_price = target_price
                exit_time = bar.get("timestamp")
                break
        fees = trade_fees(buy_price, float(exit_price), trade_shares, costs)
        trades.append(
            {
                "trade_date": str(day_bars[0].get("timestamp") or "")[:10],
                "signal_time": day_bars[index].get("timestamp"),
                "entry_time": entry_bar.get("timestamp"),
                "exit_time": exit_time,
                "outcome": outcome,
                "score": round4(score),
                "buy_price": round4(buy_price),
                "target_price": round4(target_price),
                "stop_price": round4(stop_price),
                "target_pct": round4(effective_target_pct),
                "stop_pct": round4(effective_stop_pct),
                "range_pct": bounds["range_pct"],
                "adaptive_bounds": adaptive_bounds,
                "exit_price": round4(float(exit_price)),
                "shares": trade_shares,
                **fees,
            }
        )
        index = entry_index + horizon_bars + 1
    return trades


def summarize_threshold(trades: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    triggered = len(trades)
    completed = sum(1 for trade in trades if trade["outcome"] == "completed")
    stopped = sum(1 for trade in trades if trade["outcome"] == "stopped")
    timeout = sum(1 for trade in trades if trade["outcome"] == "timeout")
    net = sum(float(trade["net_profit"]) for trade in trades)
    success_rate = completed / triggered * 100 if triggered else None
    stop_rate = stopped / triggered * 100 if triggered else None
    average_net = net / triggered if triggered else None
    return {
        "threshold": threshold,
        "triggered_count": triggered,
        "completed_count": completed,
        "stopped_count": stopped,
        "timeout_count": timeout,
        "success_rate_pct": round4(success_rate),
        "stop_rate_pct": round4(stop_rate),
        "total_net_profit": round4(net),
        "average_net_profit": round4(average_net),
        "trades": trades[-20:],
    }


def recommend_threshold(results: list[dict[str, Any]], *, min_triggers: int) -> dict[str, Any]:
    def metric(item: dict[str, Any], key: str, default: float) -> float:
        value = item.get(key)
        return default if value is None else float(value)

    eligible = [
        item
        for item in results
        if item["triggered_count"] >= min_triggers
        and metric(item, "success_rate_pct", 0) >= 55
        and metric(item, "average_net_profit", -999999) >= 0
        and metric(item, "stop_rate_pct", 100) <= 35
    ]
    if not eligible:
        return {"threshold": None, "verdict": "insufficient_or_weak", "reason": "触发次数、成功率、净收益或止损率未同时达标。"}
    best = max(eligible, key=lambda item: (metric(item, "success_rate_pct", 0) - metric(item, "stop_rate_pct", 0) + metric(item, "average_net_profit", 0) / 10, item["threshold"]))
    return {"threshold": best["threshold"], "verdict": "usable_for_watch", "reason": "在样本内同时满足触发次数、成功率、净收益和止损率约束。"}


def summarize_code(
    code: str,
    name: str,
    bars: list[dict[str, Any]],
    *,
    thresholds: list[float],
    horizon_bars: int,
    target_pct: float,
    stop_pct: float,
    trade_shares: int,
    costs: dict[str, float],
    min_triggers: int,
    adaptive_bounds: bool,
    min_target_pct: float,
    max_target_pct: float,
    min_stop_pct: float,
    max_stop_pct: float,
    range_target_multiplier: float,
    range_stop_multiplier: float,
    minimum_net_profit: float,
) -> dict[str, Any]:
    grouped = group_by_day(bars)
    threshold_results = []
    for threshold in thresholds:
        trades: list[dict[str, Any]] = []
        for day_bars in grouped.values():
            trades.extend(
                simulate_day(
                    code,
                    day_bars,
                    threshold=threshold,
                    horizon_bars=horizon_bars,
                    target_pct=target_pct,
                    stop_pct=stop_pct,
                    trade_shares=trade_shares,
                    costs=costs,
                    adaptive_bounds=adaptive_bounds,
                    min_target_pct=min_target_pct,
                    max_target_pct=max_target_pct,
                    min_stop_pct=min_stop_pct,
                    max_stop_pct=max_stop_pct,
                    range_target_multiplier=range_target_multiplier,
                    range_stop_multiplier=range_stop_multiplier,
                    minimum_net_profit=minimum_net_profit,
                )
            )
        threshold_results.append(summarize_threshold(trades, threshold))
    return {
        "code": code,
        "name": name,
        "bar_count": len(bars),
        "trading_days": len(grouped),
        "start": min(grouped) if grouped else None,
        "end": max(grouped) if grouped else None,
        "recommended": recommend_threshold(threshold_results, min_triggers=min_triggers),
        "thresholds": threshold_results,
    }


def build_report(
    position_paths: list[Path],
    *,
    cache_dir: Path,
    thresholds: list[float],
    horizon_bars: int,
    target_pct: float,
    stop_pct: float,
    trade_shares: int,
    costs: dict[str, float],
    min_triggers: int,
    adaptive_bounds: bool = True,
    min_target_pct: float = 1.2,
    max_target_pct: float = 4.0,
    min_stop_pct: float = 0.8,
    max_stop_pct: float = 2.0,
    range_target_multiplier: float = 1.2,
    range_stop_multiplier: float = 0.8,
    minimum_net_profit: float = 5.0,
) -> dict[str, Any]:
    minute_by_code = load_minute_bars(cache_dir)
    items = []
    errors = []
    for path in position_paths:
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        name = str(value_at(position, "stock.name") or code)
        bars = minute_by_code.get(code) or []
        if not bars:
            errors.append({"code": code, "message": f"missing minute cache for {code}"})
            continue
        items.append(
            summarize_code(
                code,
                name,
                bars,
                thresholds=thresholds,
                horizon_bars=horizon_bars,
                target_pct=target_pct,
                stop_pct=stop_pct,
                trade_shares=trade_shares,
                costs=costs,
                min_triggers=min_triggers,
                adaptive_bounds=adaptive_bounds,
                min_target_pct=min_target_pct,
                max_target_pct=max_target_pct,
                min_stop_pct=min_stop_pct,
                max_stop_pct=max_stop_pct,
                range_target_multiplier=range_target_multiplier,
                range_stop_multiplier=range_stop_multiplier,
                minimum_net_profit=minimum_net_profit,
            )
        )
    recommendations = Counter(item["recommended"]["threshold"] for item in items if item["recommended"]["threshold"] is not None)
    portfolio_threshold = recommendations.most_common(1)[0][0] if recommendations else None
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"minute_cache_dir": str(cache_dir), "position_count": len(position_paths)},
        "policy": {
            "thresholds": thresholds,
            "horizon_bars": horizon_bars,
            "horizon_minutes": horizon_bars * 5,
            "target_pct": target_pct,
            "stop_pct": stop_pct,
            "adaptive_bounds": adaptive_bounds,
            "min_target_pct": min_target_pct,
            "max_target_pct": max_target_pct,
            "min_stop_pct": min_stop_pct,
            "max_stop_pct": max_stop_pct,
            "range_target_multiplier": range_target_multiplier,
            "range_stop_multiplier": range_stop_multiplier,
            "minimum_net_profit": minimum_net_profit,
            "trade_shares": trade_shares,
            "min_triggers": min_triggers,
            "fees_included": True,
        },
        "portfolio_recommended_threshold": portfolio_threshold,
        "items": items,
        "errors": errors,
    }


def parse_thresholds(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest positive-T timing score thresholds with cached 5-minute bars.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--thresholds", default="60,65,70")
    parser.add_argument("--horizon-bars", type=int, default=6)
    parser.add_argument("--target-pct", type=float, default=1.2)
    parser.add_argument("--stop-pct", type=float, default=1.0)
    parser.add_argument("--fixed-bounds", action="store_true", help="Use fixed target/stop percentages instead of volatility and fee-aware bounds.")
    parser.add_argument("--min-target-pct", type=float, default=1.2)
    parser.add_argument("--max-target-pct", type=float, default=4.0)
    parser.add_argument("--min-stop-pct", type=float, default=0.8)
    parser.add_argument("--max-stop-pct", type=float, default=2.0)
    parser.add_argument("--range-target-multiplier", type=float, default=1.2)
    parser.add_argument("--range-stop-multiplier", type=float, default=0.8)
    parser.add_argument("--minimum-net-profit", type=float, default=5.0)
    parser.add_argument("--trade-shares", type=int, default=100)
    parser.add_argument("--min-triggers", type=int, default=5)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--minimum-commission", type=float, default=5.0)
    parser.add_argument("--stamp-duty-rate", type=float, default=0.0005)
    parser.add_argument("--transfer-fee-rate", type=float, default=0.00001)
    parser.add_argument("--output", default="data/metadata/positive-timing-backtest.json")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    costs = {
        "commission_rate": args.commission_rate,
        "minimum_commission": args.minimum_commission,
        "stamp_duty_rate": args.stamp_duty_rate,
        "transfer_fee_rate": args.transfer_fee_rate,
    }
    report = build_report(
        expand_position_paths(args.positions),
        cache_dir=Path(args.cache_dir),
        thresholds=parse_thresholds(args.thresholds),
        horizon_bars=args.horizon_bars,
        target_pct=args.target_pct,
        stop_pct=args.stop_pct,
        trade_shares=args.trade_shares,
        costs=costs,
        min_triggers=args.min_triggers,
        adaptive_bounds=not args.fixed_bounds,
        min_target_pct=args.min_target_pct,
        max_target_pct=args.max_target_pct,
        min_stop_pct=args.min_stop_pct,
        max_stop_pct=args.max_stop_pct,
        range_target_multiplier=args.range_target_multiplier,
        range_stop_multiplier=args.range_stop_multiplier,
        minimum_net_profit=args.minimum_net_profit,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"positive timing backtest: {len(report['items'])} items, errors: {len(report['errors'])}, recommended: {report['portfolio_recommended_threshold']}")
        print(f"output: {output}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
