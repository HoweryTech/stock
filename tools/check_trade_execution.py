#!/usr/bin/env python3
"""Check trade execution records against the gated trade plan snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.new_trade_execution import ALLOWED_GATE_CONCLUSIONS
    from tools.risk_check import as_float, is_missing, load_yaml, value_at
except ModuleNotFoundError:
    from new_trade_execution import ALLOWED_GATE_CONCLUSIONS
    from risk_check import as_float, is_missing, load_yaml, value_at


@dataclass
class CheckItem:
    code: str
    message: str


def check_required_fields(execution: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    required = [
        ("execution.id", "执行记录编号"),
        ("execution.source_trade_plan_id", "来源交易计划编号"),
        ("execution.gate_conclusion", "门禁结论"),
        ("order.side", "交易方向"),
        ("order.execution_date", "执行日期"),
        ("order.execution_price", "执行价格"),
        ("order.position_pct_of_total_assets", "实际仓位"),
    ]
    for path, label in required:
        if is_missing(value_at(execution, path)):
            blockers.append(CheckItem(f"missing_{path.replace('.', '_')}", f"缺少{label}。"))
    return blockers


def check_gate_and_confirmation(execution: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    gate_conclusion = value_at(execution, "execution.gate_conclusion")
    user_confirmed = bool(value_at(execution, "execution.user_confirmed"))
    mode = value_at(execution, "execution.mode")

    if gate_conclusion not in ALLOWED_GATE_CONCLUSIONS:
        blockers.append(CheckItem("gate_not_executable", f"门禁结论 {gate_conclusion!r} 不允许执行。"))
    if gate_conclusion == "needs_confirmation" and not user_confirmed:
        blockers.append(CheckItem("missing_user_confirmation", "门禁需要人工确认，但执行记录未确认。"))
    if mode == "real" and not user_confirmed:
        blockers.append(CheckItem("real_execution_without_confirmation", "真实执行必须人工确认。"))
    return blockers


def check_cooldown_exception(execution: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    side = value_at(execution, "order.side")
    cooldown_conclusion = value_at(execution, "execution.cooldown_conclusion") or value_at(execution, "cooldown_snapshot.conclusion")
    exception_reason = value_at(execution, "execution.cooldown_exception_reason")
    user_confirmed = bool(value_at(execution, "execution.user_confirmed"))

    if side == "buy" and cooldown_conclusion == "cooldown_required":
        if not user_confirmed:
            blockers.append(CheckItem("cooldown_exception_without_confirmation", "冷静期内买入例外必须人工确认。"))
        if is_missing(exception_reason):
            blockers.append(CheckItem("missing_cooldown_exception_reason", "冷静期内买入例外必须记录原因。"))
    return blockers


def current_strategy_status(execution: dict[str, Any]) -> str | None:
    strategy = value_at(execution, "trade_plan_snapshot.strategy.source")
    for item in value_at(execution, "strategy_health_snapshot.strategies") or []:
        if item.get("strategy") == strategy:
            return item.get("status")
    return None


def check_strategy_health_exception(execution: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    side = value_at(execution, "order.side")
    exception_reason = value_at(execution, "execution.cooldown_exception_reason")
    user_confirmed = bool(value_at(execution, "execution.user_confirmed"))

    if side == "buy" and current_strategy_status(execution) == "pause_new_entries":
        if not user_confirmed:
            blockers.append(CheckItem("strategy_health_exception_without_confirmation", "策略暂停期买入例外必须人工确认。"))
        if is_missing(exception_reason):
            blockers.append(CheckItem("missing_strategy_health_exception_reason", "策略暂停期买入例外必须记录原因。"))
    return blockers


def check_price_and_position(execution: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    execution_price = as_float(value_at(execution, "order.execution_price"))
    planned_buy_price = as_float(value_at(execution, "risk_snapshot.planned_buy_price"))
    max_acceptable_buy_price = as_float(value_at(execution, "risk_snapshot.max_acceptable_buy_price"))
    actual_position_pct = as_float(value_at(execution, "order.position_pct_of_total_assets"))
    planned_position_pct = as_float(value_at(execution, "trade_plan_snapshot.position_plan.planned_position_pct_of_total_assets"))
    slippage_pct = as_float(value_at(execution, "order.slippage_pct_vs_plan"))

    if execution_price is not None and execution_price <= 0:
        blockers.append(CheckItem("invalid_execution_price", "执行价格必须大于 0。"))
    if execution_price is not None and max_acceptable_buy_price is not None and execution_price > max_acceptable_buy_price:
        blockers.append(
            CheckItem(
                "execution_price_above_max_acceptable",
                f"执行价格 {execution_price:.2f} 高于最大可接受买入价 {max_acceptable_buy_price:.2f}。",
            )
        )
    if value_at(execution, "order.price_within_max_acceptable") is False:
        blockers.append(CheckItem("execution_marked_outside_max_price", "执行记录标记为超过最大可接受买入价。"))
    if actual_position_pct is not None and planned_position_pct is not None and actual_position_pct > planned_position_pct + 0.01:
        blockers.append(
            CheckItem("execution_position_above_plan", f"实际仓位 {actual_position_pct:.2f}% 高于计划仓位 {planned_position_pct:.2f}%。")
        )

    if slippage_pct is not None and slippage_pct > 0:
        warnings.append(CheckItem("positive_slippage", f"执行价较计划买入价高 {slippage_pct:.2f}%。"))
    if execution_price is not None and planned_buy_price is not None and execution_price < planned_buy_price:
        info.append(CheckItem("execution_below_plan_price", "执行价低于计划买入价。"))
    if actual_position_pct is not None and planned_position_pct is not None and actual_position_pct < planned_position_pct:
        info.append(CheckItem("execution_position_below_plan", "实际仓位低于计划仓位。"))

    return blockers, warnings, info


def check_execution(execution: dict[str, Any]) -> dict[str, Any]:
    blockers = check_required_fields(execution)
    blockers.extend(check_gate_and_confirmation(execution))
    blockers.extend(check_cooldown_exception(execution))
    blockers.extend(check_strategy_health_exception(execution))
    price_blockers, warnings, info = check_price_and_position(execution)
    blockers.extend(price_blockers)

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "execution_id": value_at(execution, "execution.id"),
        "source_trade_plan_id": value_at(execution, "execution.source_trade_plan_id"),
        "conclusion": conclusion,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
    }


def run_check(execution_path: Path) -> dict[str, Any]:
    return check_execution(load_yaml(execution_path))


def print_text(result: dict[str, Any]) -> None:
    print(f"execution: {result.get('execution_id') or '-'}")
    print(f"source trade plan: {result.get('source_trade_plan_id') or '-'}")
    print(f"conclusion: {result['conclusion']}")
    for title, key in (("blockers", "blockers"), ("warnings", "warnings"), ("info", "info")):
        print(f"\n{title}:")
        items = result[key]
        if not items:
            print("- none")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check trade execution record against the gated trade plan snapshot.")
    parser.add_argument("--execution", required=True, help="Path to trade execution YAML.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_check(Path(args.execution))
    except Exception as exc:
        print(f"trade execution check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
