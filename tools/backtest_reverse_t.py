#!/usr/bin/env python3
"""Fetch Eastmoney 5-minute bars and backtest the reverse-T price rules."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.fetch_holding_research import get_json, security_id
    from tools.monitor_intraday_positions import fee_viable_trade, trade_costs
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from fetch_holding_research import get_json, security_id
    from monitor_intraday_positions import fee_viable_trade, trade_costs
    from risk_check import as_float, load_yaml, value_at


KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def parse_kline(code: str, row: str) -> dict[str, Any]:
    values = row.split(",")
    if len(values) < 11:
        raise ValueError(f"invalid kline for {code}: {row!r}")
    return {
        "timestamp": values[0],
        "code": code,
        "open": float(values[1]),
        "close": float(values[2]),
        "high": float(values[3]),
        "low": float(values[4]),
        "volume": float(values[5]),
        "turnover": float(values[6]),
        "amplitude_pct": float(values[7]),
        "change_pct": float(values[8]),
        "change_amount": float(values[9]),
        "turnover_rate_pct": float(values[10]),
    }


def fetch_minute_bars(code: str, begin: str, end: str, interval_minutes: int = 5) -> tuple[str, list[dict[str, Any]]]:
    payload = get_json(
        KLINE_URL,
        {
            "secid": security_id(code), "klt": interval_minutes, "fqt": 1,
            "beg": begin, "end": end, "lmt": 100000,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        },
    )
    data = payload.get("data") or {}
    return str(data.get("name") or code), [parse_kline(code, row) for row in data.get("klines") or []]


def simulate_day(
    bars: list[dict[str, Any]],
    *,
    max_shares: int,
    costs: dict[str, float],
    min_range_pct: float = 1.5,
    min_gap_pct: float = 1.2,
) -> dict[str, Any] | None:
    if len(bars) < 3 or max_shares < 100:
        return None
    day_open = bars[0]["open"]
    running_high = bars[0]["high"]
    running_low = bars[0]["low"]
    signal_index = None
    for index, bar in enumerate(bars[:-1]):
        running_high = max(running_high, bar["high"])
        running_low = min(running_low, bar["low"])
        range_pct = (running_high - running_low) / running_low * 100 if running_low else 0
        range_position = (bar["close"] - running_low) / (running_high - running_low) if running_high > running_low else 0
        turned_down = bar["close"] <= running_high - 0.01
        if range_pct >= min_range_pct and bar["close"] >= day_open and range_position >= 0.7 and turned_down:
            signal_index = index
            break
    if signal_index is None:
        return None

    entry_bar = bars[signal_index + 1]
    sell_price = entry_bar["open"]
    viable = fee_viable_trade(sell_price, max_shares, costs, min_gap_pct=min_gap_pct)
    if not viable:
        return {"status": "fee_blocked", "signal_time": bars[signal_index]["timestamp"], "sell_price": sell_price}

    shares = viable["trade_shares"]
    buyback_price = viable["buyback_max_price"]
    for bar in bars[signal_index + 1:]:
        if bar["low"] <= buyback_price:
            result = trade_costs(sell_price, buyback_price, shares, costs)
            return {
                "status": "completed", "signal_time": bars[signal_index]["timestamp"],
                "sell_time": entry_bar["timestamp"], "sell_price": sell_price,
                "buy_time": bar["timestamp"], "buy_price": buyback_price,
                "shares": shares, **result,
            }
    return {
        "status": "not_bought_back", "signal_time": bars[signal_index]["timestamp"],
        "sell_time": entry_bar["timestamp"], "sell_price": sell_price,
        "buy_price_limit": buyback_price, "shares": shares,
        "close_price": bars[-1]["close"],
    }


def summarize(
    code: str,
    name: str,
    bars: list[dict[str, Any]],
    shares: int,
    costs: dict[str, float],
    max_trade_ratio_pct: float,
    *,
    exclude_validation_date: str | None = None,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        grouped[bar["timestamp"][:10]].append(bar)
    max_shares = int(shares * max_trade_ratio_pct / 100 // 100 * 100)
    trades = []
    intraday_observation = None
    for trade_date, day_bars in sorted(grouped.items()):
        result = simulate_day(day_bars, max_shares=max_shares, costs=costs)
        if trade_date == exclude_validation_date:
            intraday_observation = None if result is None else {"trade_date": trade_date, **result}
            continue
        if result:
            trades.append({"trade_date": trade_date, **result})
    completed = [item for item in trades if item["status"] == "completed"]
    unrecovered = [item for item in trades if item["status"] == "not_bought_back"]
    triggered = len(completed) + len(unrecovered)
    success_rate = len(completed) / triggered * 100 if triggered else None
    total_net = sum(item["net_profit"] for item in completed)
    if len(grouped) < 20 or triggered < 10:
        verdict = "insufficient_sample"
        verdict_label = "样本不足，禁止按回测执行"
    elif success_rate is not None and success_rate >= 60 and total_net > 0:
        verdict = "rule_observation_only"
        verdict_label = "规则可继续观察，仍需模拟盘验证"
    else:
        verdict = "rule_rejected"
        verdict_label = "历史结果未通过，禁止执行"
    return {
        "code": code, "name": name, "bar_count": len(bars), "trading_days": len(grouped),
        "start": min(grouped) if grouped else None, "end": max(grouped) if grouped else None,
        "triggered_count": triggered, "completed_count": len(completed),
        "not_bought_back_count": len(unrecovered),
        "success_rate_pct": None if success_rate is None else round(success_rate, 2),
        "total_completed_net_profit": round(total_net, 2),
        "average_completed_net_profit": round(total_net / len(completed), 2) if completed else None,
        "verdict": verdict, "verdict_label": verdict_label,
        "validation_excluded_date": exclude_validation_date,
        "intraday_observation": intraday_observation,
        "coverage": {"price_rule": True, "fees": True, "capital_flow_history": False, "slippage": False},
        "trades": trades[-30:],
    }


def build_report(position_paths: list[Path], begin: str, end: str, costs: dict[str, float], max_trade_ratio_pct: float) -> dict[str, Any]:
    items = []
    errors = []
    now = datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    exclude_validation_date = today if end == now.strftime("%Y%m%d") and (now.hour, now.minute) < (15, 5) else None
    for path in position_paths:
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        shares = int(as_float(value_at(position, "entry.shares"), 0) or 0)
        try:
            last_error = None
            for attempt in range(3):
                try:
                    name, bars = fetch_minute_bars(code, begin, end)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
            else:
                raise RuntimeError(f"failed after 3 attempts: {last_error}")
            items.append(
                summarize(
                    code, name, bars, shares, costs, max_trade_ratio_pct,
                    exclude_validation_date=exclude_validation_date,
                )
            )
        except Exception as exc:
            errors.append({"code": code, "message": str(exc)})
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": "eastmoney_5minute_kline", "begin": begin, "end": end,
        "method": "signal confirmed at 5-minute close; sell no earlier than next bar open; buyback only after later low touches fee-aware limit",
        "limitations": ["历史资金流未纳入回测。", "未模拟滑点和盘口排队。", "回测结果不代表未来表现。"],
        "items": items, "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest reverse-T rules with Eastmoney 5-minute bars.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--begin", default=(date.today() - timedelta(days=180)).strftime("%Y%m%d"))
    parser.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--output", default="data/metadata/reverse-t-backtest.json")
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--minimum-commission", type=float, default=5.0)
    parser.add_argument("--stamp-duty-rate", type=float, default=0.0005)
    parser.add_argument("--transfer-fee-rate", type=float, default=0.00001)
    parser.add_argument("--minimum-net-profit", type=float, default=5.0)
    parser.add_argument("--max-trade-ratio", type=float, default=50.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    costs = {
        "commission_rate": args.commission_rate, "minimum_commission": args.minimum_commission,
        "stamp_duty_rate": args.stamp_duty_rate, "transfer_fee_rate": args.transfer_fee_rate,
        "minimum_net_profit": args.minimum_net_profit, "verified": False,
    }
    report = build_report(expand_position_paths(args.positions), args.begin, args.end, costs, args.max_trade_ratio)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    target = output if not report["errors"] else output.with_suffix(".failed.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"backtested: {len(report['items'])}, errors: {len(report['errors'])}, output: {target}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
