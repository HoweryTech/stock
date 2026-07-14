#!/usr/bin/env python3
"""Draft risk-plan completions for imported holding positions."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.check_t_trade_opportunity import latest_metrics, read_bars
    from tools.risk_check import as_float, is_missing, load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from check_t_trade_opportunity import latest_metrics, read_bars
    from risk_check import as_float, is_missing, load_yaml, value_at


def round_price(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def candidate(label: str, price: float | None, basis: str) -> dict[str, Any]:
    return {"label": label, "price": round_price(price), "basis": basis}


def safe_latest_metrics(daily_bars: Path, code: str, short_window: int = 5, mid_window: int = 20) -> dict[str, Any]:
    try:
        bars = read_bars(daily_bars, code)
        if len(bars) < mid_window:
            return {"available": False, "bars_count": len(bars)}
        metrics = latest_metrics(bars, short_window, mid_window)
        metrics["available"] = True
        metrics["bars_count"] = len(bars)
        return metrics
    except Exception as exc:
        return {"available": False, "bars_count": 0, "error": str(exc)}


def missing_fields(position: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    if is_missing(value_at(position, "risk.stop_loss_price")):
        fields.append("risk.stop_loss_price")
    if is_missing(value_at(position, "risk.invalidation_conditions")):
        fields.append("risk.invalidation_conditions")
    if is_missing(value_at(position, "risk.take_profit_conditions")):
        fields.append("risk.take_profit_conditions")
    buy_reason = value_at(position, "strategy.buy_reason")
    if is_missing(buy_reason) or "待补充" in str(buy_reason):
        fields.append("strategy.buy_reason")
    if is_missing(value_at(position, "strategy.key_evidence")):
        fields.append("strategy.key_evidence")
    return fields


def stop_loss_candidates(position: dict[str, Any], metrics: dict[str, Any], stop_loss_pct_from_entry: float) -> list[dict[str, Any]]:
    entry_price = as_float(value_at(position, "entry.entry_price"))
    current_price = as_float(value_at(position, "tracking.current_price"))
    recent_low = as_float(metrics.get("recent_low"))
    ma_mid = as_float(metrics.get("ma_mid"))
    candidates: list[dict[str, Any]] = []
    if entry_price is not None:
        candidates.append(
            candidate(
                f"买入价下浮 {stop_loss_pct_from_entry:g}%",
                entry_price * (1 - stop_loss_pct_from_entry / 100),
                "用于导入仓的保守风险预算假设，需人工确认是否符合原始买入逻辑。",
            )
        )
    if recent_low is not None:
        candidates.append(candidate("近20日低点下方1%", recent_low * 0.99, "用于趋势破位观察，不代表必须卖出价。"))
    if ma_mid is not None:
        candidates.append(candidate("20日均线下方2%", ma_mid * 0.98, "用于波段趋势失效观察。"))
    if current_price is not None:
        candidates.append(candidate("当前价下方5%", current_price * 0.95, "用于已有浮亏仓的短期风险控制参考。"))
    unique: list[dict[str, Any]] = []
    seen: set[float] = set()
    for item in candidates:
        price = item["price"]
        if price is None or price <= 0 or price in seen:
            continue
        unique.append(item)
        seen.add(price)
    return unique


def draft_for_position(
    position_path: Path,
    position: dict[str, Any],
    profile: dict[str, Any],
    daily_bars: Path,
    *,
    stop_loss_pct_from_entry: float,
) -> dict[str, Any]:
    code = str(value_at(position, "stock.code") or "")
    metrics = safe_latest_metrics(daily_bars, code)
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    max_stock_pct = as_float(value_at(profile, "risk.max_position_pct_per_stock"), 100.0) or 100.0
    fields = missing_fields(position)
    candidates = stop_loss_candidates(position, metrics, stop_loss_pct_from_entry)
    requires_risk_reduction = position_pct > max_stock_pct
    imported = value_at(position, "strategy.source") == "imported_holding" or value_at(position, "position.source_trade_plan_id") == "IMPORT-EASTMONEY"

    if requires_risk_reduction:
        status = "risk_reduction_first"
        priority = 1
    elif fields:
        status = "needs_manual_completion"
        priority = 2 if imported else 3
    else:
        status = "complete_enough_for_monitoring"
        priority = 5

    proposed_stop = candidates[0]["price"] if candidates else None
    return {
        "path": str(position_path),
        "stock": {
            "code": code,
            "name": value_at(position, "stock.name"),
            "industry": value_at(position, "stock.industry"),
        },
        "status": status,
        "priority": priority,
        "imported_holding": imported,
        "missing_fields": fields,
        "risk_context": {
            "position_pct": round(position_pct, 4),
            "max_stock_pct": max_stock_pct,
            "requires_risk_reduction": requires_risk_reduction,
            "current_price": as_float(value_at(position, "tracking.current_price")),
            "entry_price": as_float(value_at(position, "entry.entry_price")),
            "current_return_pct": as_float(value_at(position, "tracking.current_return_pct")),
        },
        "market_metrics": {
            "available": metrics.get("available", False),
            "trade_date": metrics.get("trade_date"),
            "bars_count": metrics.get("bars_count", 0),
            "latest_close": as_float(metrics.get("latest_close")),
            "ma_mid": as_float(metrics.get("ma_mid")),
            "recent_low": as_float(metrics.get("recent_low")),
            "recent_high": as_float(metrics.get("recent_high")),
            "return_mid_pct": as_float(metrics.get("return_mid_pct")),
        },
        "stop_loss_candidates": candidates,
        "draft_plan": {
            "preferred_stop_loss_price": proposed_stop,
            "stop_loss_condition": "若收盘价跌破人工确认的止损价，先生成退出计划，不补仓、不做T。",
            "take_profit_conditions": [
                "反弹至成本区或近期高点附近时，优先复核是否降仓，而不是默认继续持有。",
                "若短线进入高位过热状态，只进入止盈或反T观察，不自动执行。",
            ],
            "invalidation_conditions": [
                "无法补回原始买入理由或关键证据时，视为买入假设不完整。",
                "收盘价持续低于20日均线且20日收益为负时，复核趋势失效。",
            ],
            "observation_items": [
                "补齐原始买入理由、策略来源和关键证据。",
                "确认止损价是否符合个人单笔最大亏损约束。",
                "复核单票、行业和总仓位是否允许继续持有。",
            ],
            "add_allowed": False,
            "t_trade_allowed": False,
            "auto_order": False,
        },
        "next_steps": [
            "人工选择或调整 stop_loss_candidates 中的止损参考位。",
            "把确认后的止损价、失效条件、止盈条件和买入理由回填到持仓文件。",
            "重新运行持仓日检、动作矩阵回测和每日摘要。",
        ],
    }


def build_report(
    position_paths: list[Path],
    profile: dict[str, Any],
    daily_bars: Path,
    *,
    stop_loss_pct_from_entry: float,
) -> dict[str, Any]:
    items = [
        draft_for_position(path, load_yaml(path), profile, daily_bars, stop_loss_pct_from_entry=stop_loss_pct_from_entry)
        for path in position_paths
    ]
    items.sort(key=lambda item: (item["priority"], -(item["risk_context"]["position_pct"] or 0), item["stock"]["code"]))
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": {
            "auto_update_positions": False,
            "auto_order": False,
            "stop_loss_pct_from_entry": stop_loss_pct_from_entry,
        },
        "position_count": len(items),
        "needs_completion_count": sum(1 for item in items if item["missing_fields"]),
        "risk_reduction_first_count": sum(1 for item in items if item["status"] == "risk_reduction_first"),
        "items": items,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 导入持仓风险计划补全草案",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "本草案不修改持仓文件，不构成买卖建议；所有止损、止盈和失效条件必须人工确认。",
        "",
        "| 优先级 | 代码 | 名称 | 状态 | 仓位 | 缺失字段 | 首选止损参考 |",
        "| ---: | --- | --- | --- | ---: | --- | ---: |",
    ]
    for item in report["items"]:
        missing = ", ".join(item["missing_fields"]) if item["missing_fields"] else "-"
        stop = item["draft_plan"]["preferred_stop_loss_price"]
        lines.append(
            f"| {item['priority']} | {item['stock']['code']} | {item['stock']['name']} | {item['status']} | "
            f"{item['risk_context']['position_pct']:.2f}% | {missing} | {stop if stop is not None else '-'} |"
        )
    lines.extend(["", "## 明细", ""])
    for item in report["items"]:
        lines.append(f"### {item['stock']['code']} {item['stock']['name']}")
        lines.append("")
        lines.append(f"- 状态：{item['status']}")
        lines.append(f"- 缺失字段：{', '.join(item['missing_fields']) if item['missing_fields'] else '无'}")
        lines.append("- 止损参考：")
        if item["stop_loss_candidates"]:
            for candidate_item in item["stop_loss_candidates"]:
                lines.append(f"  - {candidate_item['label']}：{candidate_item['price']}（{candidate_item['basis']}）")
        else:
            lines.append("  - 无可用参考。")
        lines.append("- 下一步：")
        for step in item["next_steps"]:
            lines.append(f"  - {step}")
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft risk-plan completions for imported positions.")
    parser.add_argument("--positions", nargs="+", default=["positions/*.yaml"], help="Position YAML paths or glob patterns.")
    parser.add_argument("--profile", default="config/investment-profile.yaml", help="Investment profile YAML.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Normalized daily bars CSV.")
    parser.add_argument("--stop-loss-pct-from-entry", type=float, default=12.0, help="Reference stop loss below entry price.")
    parser.add_argument("--output", default="data/metadata/imported-position-plan-draft.json")
    parser.add_argument("--markdown-output", default="reports/imported-position-plan-draft.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile_path = Path(args.profile)
        profile = load_yaml(profile_path) if profile_path.exists() else load_yaml(Path("config/investment-profile.example.yaml"))
        report = build_report(
            expand_position_paths(args.positions),
            profile,
            Path(args.daily_bars),
            stop_loss_pct_from_entry=args.stop_loss_pct_from_entry,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown_output = Path(args.markdown_output)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(report) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"complete imported position plan failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"positions: {report['position_count']}")
        print(f"needs completion: {report['needs_completion_count']}")
        print(f"risk reduction first: {report['risk_reduction_first_count']}")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
