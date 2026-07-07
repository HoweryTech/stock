#!/usr/bin/env python3
"""Check holding risk for one position."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import CheckItem, as_float, is_missing, load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import CheckItem, as_float, is_missing, load_yaml, value_at


def validate_position(profile: dict[str, Any], position: dict[str, Any], near_stop_pct: float = 3.0) -> dict[str, Any]:
    risk_config = profile.get("risk", {})
    max_stock_pct = as_float(risk_config.get("max_position_pct_per_stock"), 100.0) or 100.0
    max_industry_pct = as_float(risk_config.get("max_position_pct_per_industry"), 100.0) or 100.0
    max_total_pct = as_float(risk_config.get("max_total_position_pct"), 100.0) or 100.0

    actions: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    current_price = as_float(value_at(position, "tracking.current_price"))
    entry_price = as_float(value_at(position, "entry.entry_price"))
    stop_loss_price = as_float(value_at(position, "risk.stop_loss_price"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"))
    industry_pct = as_float(value_at(position, "portfolio_context.industry_position_pct"), position_pct)
    total_pct = as_float(value_at(position, "portfolio_context.total_position_pct"), position_pct)
    status = value_at(position, "position.status")

    if is_missing(value_at(position, "strategy.buy_reason")):
        warnings.append(CheckItem("missing_buy_reason", "持仓缺少买入理由，需要复核。"))
    if is_missing(value_at(position, "risk.invalidation_conditions")):
        warnings.append(CheckItem("missing_invalidation_conditions", "持仓缺少失效条件，需要补充。"))
    if is_missing(value_at(position, "risk.observation_items")):
        info.append(CheckItem("missing_observation_items", "持仓缺少观察项。"))

    distance_to_stop_pct = None
    if current_price is not None and stop_loss_price is not None:
        if current_price <= stop_loss_price:
            actions.append(
                CheckItem(
                    "stop_loss_triggered",
                    f"当前价格 {current_price:.2f} 已触发止损价 {stop_loss_price:.2f}。",
                )
            )
        elif current_price > 0:
            distance_to_stop_pct = (current_price - stop_loss_price) / current_price * 100
            if distance_to_stop_pct <= near_stop_pct:
                warnings.append(
                    CheckItem(
                        "near_stop_loss",
                        f"当前价格距离止损价仅 {distance_to_stop_pct:.2f}%，需要重点观察。",
                    )
                )
    else:
        warnings.append(CheckItem("missing_price_or_stop_loss", "缺少当前价格或止损价，无法检查止损。"))

    if position_pct is not None and position_pct > max_stock_pct:
        actions.append(CheckItem("stock_position_limit_exceeded", f"单票仓位 {position_pct:.2f}% 超过上限 {max_stock_pct:.2f}%。"))
    elif position_pct is not None and position_pct > max_stock_pct * 0.8:
        warnings.append(CheckItem("stock_position_high", "单票仓位接近上限。"))

    if industry_pct is not None and industry_pct > max_industry_pct:
        actions.append(CheckItem("industry_position_limit_exceeded", f"行业仓位 {industry_pct:.2f}% 超过上限 {max_industry_pct:.2f}%。"))
    elif industry_pct is not None and industry_pct > max_industry_pct * 0.8:
        warnings.append(CheckItem("industry_position_high", "行业仓位接近上限。"))

    if total_pct is not None and total_pct > max_total_pct:
        actions.append(CheckItem("total_position_limit_exceeded", f"总仓位 {total_pct:.2f}% 超过上限 {max_total_pct:.2f}%。"))
    elif total_pct is not None and total_pct > max_total_pct * 0.8:
        warnings.append(CheckItem("total_position_high", "总仓位接近上限。"))

    if status and status not in set(profile.get("holding_statuses", [])):
        warnings.append(CheckItem("unknown_position_status", f"持仓状态 {status} 未在投资体系中定义。"))

    if actions:
        conclusion = "needs_action"
    elif warnings:
        conclusion = "warning"
    else:
        conclusion = "normal"

    return {
        "position_id": value_at(position, "position.id"),
        "stock_code": value_at(position, "stock.code"),
        "stock_name": value_at(position, "stock.name"),
        "conclusion": conclusion,
        "actions": [item.__dict__ for item in actions],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
        "calculations": {
            "entry_price": entry_price,
            "current_price": current_price,
            "stop_loss_price": stop_loss_price,
            "distance_to_stop_pct": distance_to_stop_pct,
            "position_pct_of_total_assets": position_pct,
            "industry_position_pct": industry_pct,
            "total_position_pct": total_pct,
            "current_return_pct": as_float(value_at(position, "tracking.current_return_pct")),
            "current_portfolio_return_pct": as_float(value_at(position, "tracking.current_portfolio_return_pct")),
        },
    }


def print_text_report(result: dict[str, Any]) -> None:
    conclusion_labels = {
        "normal": "正常",
        "warning": "强提醒",
        "needs_action": "需处理",
    }
    print(f"持仓编号：{result.get('position_id') or '-'}")
    print(f"标的：{result.get('stock_code') or '-'} {result.get('stock_name') or '-'}")
    print(f"检查结论：{conclusion_labels.get(result['conclusion'], result['conclusion'])}")

    for title, key in (("需处理项", "actions"), ("强提醒项", "warnings"), ("信息提示项", "info")):
        print(f"\n{title}：")
        items = result[key]
        if not items:
            print("- 无")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")

    print("\n计算结果：")
    for key, value in result["calculations"].items():
        print(f"- {key}: {'-' if value is None else round(value, 4)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check holding risk for one position.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--position", default="templates/position.example.yaml", help="Path to position YAML.")
    parser.add_argument("--near-stop-pct", type=float, default=3.0, help="Warn when current price is within this percent above stop loss.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile = load_yaml(Path(args.profile))
    position = load_yaml(Path(args.position))
    result = validate_position(profile, position, near_stop_pct=args.near_stop_pct)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_report(result)

    return 1 if result["conclusion"] == "needs_action" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"position check failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
