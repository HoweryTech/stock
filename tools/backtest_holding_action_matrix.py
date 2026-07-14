#!/usr/bin/env python3
"""Backtest holding trend states and action-matrix triggers on daily bars."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.build_holding_action_draft import classify_holding
    from tools.check_t_trade_opportunity import check_t_opportunity, read_bars
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from build_holding_action_draft import classify_holding
    from check_t_trade_opportunity import check_t_opportunity, read_bars
    from risk_check import as_float, load_yaml, value_at


DEFAULT_HORIZONS = [1, 3, 5, 10, 20]


def clone_for_day(position: dict[str, Any], close: float, trade_date: str) -> dict[str, Any]:
    snapshot = json.loads(json.dumps(position, ensure_ascii=False))
    entry_price = as_float(value_at(snapshot, "entry.entry_price"))
    position_pct = as_float(value_at(snapshot, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    snapshot.setdefault("tracking", {})
    snapshot["tracking"]["current_price"] = close
    snapshot["tracking"]["current_return_pct"] = None if entry_price in (None, 0) else round((close / entry_price - 1) * 100, 4)
    snapshot["tracking"]["current_portfolio_return_pct"] = (
        None if entry_price in (None, 0) else round((close / entry_price - 1) * position_pct, 4)
    )
    snapshot["tracking"]["notes"] = [f"backtest snapshot as of {trade_date}"]
    return snapshot


def pct_change(current: float, base: float) -> float | None:
    if base == 0:
        return None
    return (current / base - 1) * 100


def future_window_metrics(rows: list[dict[str, Any]], index: int, horizons: list[int]) -> dict[str, Any]:
    close = as_float(rows[index].get("close"))
    assert close is not None
    metrics: dict[str, Any] = {}
    for horizon in horizons:
        end_index = min(index + horizon, len(rows) - 1)
        future = rows[index + 1:end_index + 1]
        if not future:
            metrics[str(horizon)] = {"available": False}
            continue
        future_close = as_float(rows[end_index].get("close"))
        lows = [as_float(row.get("low")) for row in future]
        highs = [as_float(row.get("high")) for row in future]
        valid_lows = [value for value in lows if value is not None]
        valid_highs = [value for value in highs if value is not None]
        metrics[str(horizon)] = {
            "available": True,
            "end_trade_date": rows[end_index]["trade_date"],
            "return_pct": None if future_close is None else round(pct_change(future_close, close) or 0.0, 4),
            "max_drawdown_pct": None if not valid_lows else round(pct_change(min(valid_lows), close) or 0.0, 4),
            "max_up_pct": None if not valid_highs else round(pct_change(max(valid_highs), close) or 0.0, 4),
        }
    return metrics


def summarize_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "average": None, "positive_rate_pct": None}
    positives = sum(1 for value in values if value > 0)
    return {
        "count": len(values),
        "average": round(sum(values) / len(values), 4),
        "positive_rate_pct": round(positives / len(values) * 100, 2),
    }


def rule_is_active(rule: dict[str, Any], event: dict[str, Any], trend_metrics: dict[str, Any]) -> bool:
    trigger = rule.get("trigger")
    close = as_float(event.get("close"))
    price = as_float(rule.get("price"))
    if trigger == "price_lte_stop_loss":
        return close is not None and price is not None and close <= price
    if trigger == "price_within_3pct_above_stop_loss":
        stop = as_float(event.get("stop_loss_price"))
        return close is not None and price is not None and stop is not None and stop < close <= price
    if trigger == "missing_stop_loss":
        return event.get("stop_loss_price") is None
    if trigger == "close_lt_ma20":
        return close is not None and price is not None and close < price
    if trigger == "close_gte_ma20_and_return_mid_positive":
        return_mid = as_float(trend_metrics.get("return_mid_pct"))
        return close is not None and price is not None and close >= price and return_mid is not None and return_mid > 0
    if trigger == "pullback_to_ma5":
        return close is not None and price is not None and price > 0 and abs(close / price - 1) <= 0.02
    if trigger == "price_gte_recent_high":
        return close is not None and price is not None and close >= price
    if trigger == "price_lte_recent_low":
        return close is not None and price is not None and close <= price
    if trigger == "position_above_limit_or_review_line":
        return True
    if trigger in {"reverse_t_candidate", "positive_t_candidate", "positive_and_reverse_t_conflict"}:
        return True
    if str(trigger or "").startswith("trend_state_"):
        return True
    return False


def summarize_group(events: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": len(events), "horizons": {}}
    for horizon in horizons:
        key = str(horizon)
        returns = [
            event["future"][key]["return_pct"]
            for event in events
            if event["future"].get(key, {}).get("available") and event["future"][key]["return_pct"] is not None
        ]
        drawdowns = [
            event["future"][key]["max_drawdown_pct"]
            for event in events
            if event["future"].get(key, {}).get("available") and event["future"][key]["max_drawdown_pct"] is not None
        ]
        summary["horizons"][key] = {
            "return": summarize_values(returns),
            "max_drawdown": summarize_values(drawdowns),
        }
    return summary


def build_report(
    *,
    position: dict[str, Any],
    bars: list[dict[str, Any]],
    profile: dict[str, Any],
    horizons: list[int],
    min_history: int,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for index in range(max(min_history, 1) - 1, len(bars) - 1):
        close = as_float(bars[index].get("close"))
        if close is None:
            continue
        history = bars[:index + 1]
        snapshot = clone_for_day(position, close, bars[index]["trade_date"])
        t_result = check_t_opportunity(profile, snapshot, history)
        item = classify_holding(snapshot, t_result)
        candidate_rules = [
            rule for rule in item["action_matrix"]
            if rule.get("severity") in {"critical", "high", "medium"}
        ]
        event = {
            "trade_date": bars[index]["trade_date"],
            "close": close,
            "stop_loss_price": as_float(value_at(snapshot, "risk.stop_loss_price")),
            "trend_state": item["trend_state"]["state"],
            "trend_label": item["trend_state"]["label"],
            "action": item["action"],
            "action_label": item["action_label"],
            "market_setup": item["market_setup"],
            "rules": [],
            "future": future_window_metrics(bars, index, horizons),
        }
        event["rules"] = [
            rule for rule in candidate_rules
            if rule_is_active(rule, event, item["trend_state"]["metrics"])
        ]
        events.append(event)

    by_trend: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_trend[event["trend_state"]].append(event)
        for rule in event["rules"]:
            by_rule[rule["trigger"]].append(event)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stock": {
            "code": value_at(position, "stock.code"),
            "name": value_at(position, "stock.name"),
        },
        "source": {
            "bar_count": len(bars),
            "start": bars[0]["trade_date"] if bars else None,
            "end": bars[-1]["trade_date"] if bars else None,
            "min_history": min_history,
            "horizons": horizons,
        },
        "method": "逐日仅使用当日及以前日线生成趋势状态和动作矩阵，再统计后续收益与回撤。",
        "limitations": [
            "该回测不模拟真实成交、滑点、盘口排队或停复牌无法成交场景。",
            "动作矩阵是风险与观察规则，不等同于自动买卖策略。",
            "历史结果不代表未来表现。",
        ],
        "event_count": len(events),
        "summary_by_trend_state": {key: summarize_group(value, horizons) for key, value in sorted(by_trend.items())},
        "summary_by_rule_trigger": {key: summarize_group(value, horizons) for key, value in sorted(by_rule.items())},
        "recent_events": events[-50:],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 持仓动作矩阵日线回测",
        "",
        f"生成时间：{report['generated_at']}",
        f"标的：{report['stock']['code']} {report['stock']['name']}",
        f"样本：{report['source']['start']} -> {report['source']['end']}，{report['source']['bar_count']} 条日线",
        "",
        "本报告只验证规则触发后的历史表现，不构成买卖建议。",
        "",
        "## 趋势状态汇总",
        "",
        "| 趋势状态 | 样本数 | 5日平均收益 | 5日正收益率 | 20日平均收益 | 20日正收益率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for state, summary in report["summary_by_trend_state"].items():
        h5 = summary["horizons"].get("5", {}).get("return", {})
        h20 = summary["horizons"].get("20", {}).get("return", {})
        lines.append(
            f"| {state} | {summary['count']} | {h5.get('average') if h5.get('average') is not None else '-'} | "
            f"{h5.get('positive_rate_pct') if h5.get('positive_rate_pct') is not None else '-'} | "
            f"{h20.get('average') if h20.get('average') is not None else '-'} | "
            f"{h20.get('positive_rate_pct') if h20.get('positive_rate_pct') is not None else '-'} |"
        )
    lines.extend(["", "## 关键规则汇总", "", "| 规则 | 触发数 | 5日平均收益 | 20日平均收益 |", "| --- | ---: | ---: | ---: |"])
    for trigger, summary in report["summary_by_rule_trigger"].items():
        h5 = summary["horizons"].get("5", {}).get("return", {})
        h20 = summary["horizons"].get("20", {}).get("return", {})
        lines.append(
            f"| {trigger} | {summary['count']} | {h5.get('average') if h5.get('average') is not None else '-'} | "
            f"{h20.get('average') if h20.get('average') is not None else '-'} |"
        )
    lines.extend(["", "## 最近事件", ""])
    for event in report["recent_events"][-10:]:
        h5 = event["future"].get("5", {})
        h5_text = "-" if not h5.get("available") else h5.get("return_pct")
        lines.append(
            f"- {event['trade_date']} close={event['close']} trend={event['trend_state']} action={event['action']} next5d={h5_text}%"
        )
    return "\n".join(lines)


def parse_horizons(value: str) -> list[int]:
    horizons = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not horizons or any(item <= 0 for item in horizons):
        raise argparse.ArgumentTypeError("horizons must be positive integers")
    return sorted(set(horizons))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest holding action-matrix states with daily bars.")
    parser.add_argument("--position", required=True, help="Position YAML path.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Normalized daily bars CSV.")
    parser.add_argument("--profile", default="config/investment-profile.yaml", help="Investment profile YAML.")
    parser.add_argument("--horizons", type=parse_horizons, default=DEFAULT_HORIZONS, help="Forward horizons, comma-separated.")
    parser.add_argument("--min-history", type=int, default=20, help="Minimum daily bars before replay starts.")
    parser.add_argument("--output", default="data/metadata/holding-action-matrix-backtest.json")
    parser.add_argument("--markdown-output", default="reports/holding-action-matrix-backtest.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        position = load_yaml(Path(args.position))
        profile_path = Path(args.profile)
        profile = load_yaml(profile_path) if profile_path.exists() else load_yaml(Path("config/investment-profile.example.yaml"))
        code = str(value_at(position, "stock.code") or "")
        bars = read_bars(Path(args.daily_bars), code)
        if len(bars) < args.min_history + 1:
            raise ValueError(f"not enough bars for {code}: {len(bars)} < {args.min_history + 1}")
        report = build_report(position=position, bars=bars, profile=profile, horizons=args.horizons, min_history=args.min_history)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown_output = Path(args.markdown_output)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(report) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"holding action matrix backtest failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"events: {report['event_count']}")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
