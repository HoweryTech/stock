#!/usr/bin/env python3
"""Run holding action-matrix daily backtests for a portfolio."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.backtest_holding_action_matrix import DEFAULT_HORIZONS, build_report, parse_horizons, render_markdown
    from tools.check_portfolio_positions import expand_position_paths
    from tools.check_t_trade_opportunity import read_bars
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from backtest_holding_action_matrix import DEFAULT_HORIZONS, build_report, parse_horizons, render_markdown
    from check_portfolio_positions import expand_position_paths
    from check_t_trade_opportunity import read_bars
    from risk_check import as_float, load_yaml, value_at


def apply_stop_loss_assumption(position: dict[str, Any], pct_from_entry: float | None) -> dict[str, Any]:
    snapshot = json.loads(json.dumps(position, ensure_ascii=False))
    if pct_from_entry is None or value_at(snapshot, "risk.stop_loss_price") is not None:
        return snapshot
    entry_price = as_float(value_at(snapshot, "entry.entry_price"))
    if entry_price is None:
        return snapshot
    snapshot.setdefault("risk", {})
    snapshot["risk"]["stop_loss_price"] = round(entry_price * (1 - pct_from_entry / 100), 2)
    return snapshot


def first_horizon_summary(report: dict[str, Any], group: str, key: str, horizon: int) -> dict[str, Any]:
    source = report.get(group, {}).get(key, {})
    return source.get("horizons", {}).get(str(horizon), {}).get("return", {})


def summarize_item(report: dict[str, Any], path: Path, primary_horizon: int = 20) -> dict[str, Any]:
    trend_states = report.get("summary_by_trend_state", {})
    rule_triggers = report.get("summary_by_rule_trigger", {})
    best_state = None
    weakest_state = None
    for state, summary in trend_states.items():
        returns = summary.get("horizons", {}).get(str(primary_horizon), {}).get("return", {})
        average = returns.get("average")
        if average is None:
            continue
        candidate = {"state": state, "count": summary.get("count", 0), "average_return_pct": average}
        if best_state is None or average > best_state["average_return_pct"]:
            best_state = candidate
        if weakest_state is None or average < weakest_state["average_return_pct"]:
            weakest_state = candidate

    weak_rule_count = 0
    for summary in rule_triggers.values():
        returns = summary.get("horizons", {}).get(str(primary_horizon), {}).get("return", {})
        average = returns.get("average")
        if average is not None and average < 0:
            weak_rule_count += 1

    return {
        "path": str(path),
        "stock": report["stock"],
        "event_count": report["event_count"],
        "stop_loss_price": value_at(report, "source.risk.stop_loss_price"),
        "trend_state_count": len(trend_states),
        "rule_trigger_count": len(rule_triggers),
        "weak_rule_count": weak_rule_count,
        "best_state": best_state,
        "weakest_state": weakest_state,
    }


def build_portfolio_report(
    *,
    position_paths: list[Path],
    daily_bars: Path,
    profile: dict[str, Any],
    horizons: list[int],
    min_history: int,
    stop_loss_pct_from_entry: float | None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    detail_reports: list[dict[str, Any]] = []
    primary_horizon = 20 if 20 in horizons else max(horizons)
    for path in position_paths:
        try:
            raw_position = load_yaml(path)
            position = apply_stop_loss_assumption(raw_position, stop_loss_pct_from_entry)
            code = str(value_at(position, "stock.code") or "")
            bars = read_bars(daily_bars, code)
            if len(bars) < min_history + 1:
                raise ValueError(f"not enough bars for {code}: {len(bars)} < {min_history + 1}")
            report = build_report(position=position, bars=bars, profile=profile, horizons=horizons, min_history=min_history)
            detail_reports.append(report)
            items.append(summarize_item(report, path, primary_horizon=primary_horizon))
        except Exception as exc:
            errors.append({"path": str(path), "message": str(exc)})
    items.sort(key=lambda item: (-(item["weak_rule_count"] or 0), item["stock"].get("code") or ""))
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "positions": [str(path) for path in position_paths],
            "daily_bars": str(daily_bars),
            "horizons": horizons,
            "min_history": min_history,
            "stop_loss_pct_from_entry": stop_loss_pct_from_entry,
            "primary_horizon": primary_horizon,
        },
        "position_count": len(position_paths),
        "backtested_count": len(items),
        "error_count": len(errors),
        "items": items,
        "errors": errors,
        "details": detail_reports,
    }


def render_portfolio_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 组合持仓动作矩阵回测",
        "",
        f"生成时间：{report['generated_at']}",
        f"持仓数：{report['position_count']}，完成回测：{report['backtested_count']}，错误：{report['error_count']}",
        f"止损价假设：按买入价下浮 {report['source']['stop_loss_pct_from_entry']}%" if report["source"]["stop_loss_pct_from_entry"] is not None else "止损价假设：仅使用持仓文件现有止损价",
        "",
        "## 汇总",
        "",
        "| 代码 | 名称 | 事件数 | 止损价 | 弱规则数 | 最强状态 | 最弱状态 |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report["items"]:
        best = item["best_state"]
        weakest = item["weakest_state"]
        best_text = "-" if not best else f"{best['state']} {best['average_return_pct']}%"
        weakest_text = "-" if not weakest else f"{weakest['state']} {weakest['average_return_pct']}%"
        lines.append(
            f"| {item['stock']['code']} | {item['stock']['name']} | {item['event_count']} | "
            f"{item['stop_loss_price'] if item['stop_loss_price'] is not None else '-'} | {item['weak_rule_count']} | {best_text} | {weakest_text} |"
        )
    if report["errors"]:
        lines.extend(["", "## 错误", ""])
        for item in report["errors"]:
            lines.append(f"- {item['path']}: {item['message']}")
    lines.extend(["", "## 单股明细", ""])
    for detail in report["details"]:
        lines.append(render_markdown(detail))
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run portfolio action-matrix backtests.")
    parser.add_argument("--positions", nargs="+", default=["positions/*.yaml"], help="Position YAML paths or glob patterns.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Normalized daily bars CSV.")
    parser.add_argument("--profile", default="config/investment-profile.yaml", help="Investment profile YAML.")
    parser.add_argument("--horizons", type=parse_horizons, default=DEFAULT_HORIZONS, help="Forward horizons, comma-separated.")
    parser.add_argument("--min-history", type=int, default=20, help="Minimum daily bars before replay starts.")
    parser.add_argument("--stop-loss-pct-from-entry", type=float, help="What-if stop loss for positions missing stop loss.")
    parser.add_argument("--output", default="data/metadata/portfolio-action-matrix-backtests.json")
    parser.add_argument("--markdown-output", default="reports/portfolio-action-matrix-backtests.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile_path = Path(args.profile)
        profile = load_yaml(profile_path) if profile_path.exists() else load_yaml(Path("config/investment-profile.example.yaml"))
        report = build_portfolio_report(
            position_paths=expand_position_paths(args.positions),
            daily_bars=Path(args.daily_bars),
            profile=profile,
            horizons=args.horizons,
            min_history=args.min_history,
            stop_loss_pct_from_entry=args.stop_loss_pct_from_entry,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown_output = Path(args.markdown_output)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_portfolio_markdown(report) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"portfolio action matrix backtests failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"backtested: {report['backtested_count']}, errors: {report['error_count']}")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
