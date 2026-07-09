#!/usr/bin/env python3
"""Check sell execution records before downstream review generation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.manual_confirmation import confirmation_snapshot_confirmed
    from tools.risk_check import as_float, is_missing, load_yaml, value_at
except ModuleNotFoundError:
    from manual_confirmation import confirmation_snapshot_confirmed
    from risk_check import as_float, is_missing, load_yaml, value_at


@dataclass
class CheckItem:
    code: str
    message: str


def check_required_fields(execution: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    required = [
        ("execution.id", "卖出执行记录编号"),
        ("execution.source_exit_plan_id", "来源退出计划编号"),
        ("execution.source_position_id", "来源持仓编号"),
        ("execution.source_trade_plan_id", "来源交易计划编号"),
        ("execution.exit_check_conclusion", "退出计划检查结论"),
        ("order.execution_date", "卖出日期"),
        ("order.execution_price", "卖出价格"),
        ("order.exited_position_pct_of_total_assets", "卖出仓位"),
    ]
    for path, label in required:
        if is_missing(value_at(execution, path)):
            blockers.append(CheckItem(f"missing_{path.replace('.', '_')}", f"缺少{label}。"))
    return blockers


def exit_execution_requires_confirmation(execution: dict[str, Any]) -> bool:
    exit_check = value_at(execution, "execution.exit_check_conclusion")
    mode = value_at(execution, "execution.mode")
    return exit_check == "needs_review" or mode == "real"


def check_exit_gate_and_confirmation(execution: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    exit_check = value_at(execution, "execution.exit_check_conclusion")
    user_confirmed = bool(value_at(execution, "execution.user_confirmed"))
    mode = value_at(execution, "execution.mode")

    if exit_check not in {"pass", "needs_review"}:
        blockers.append(CheckItem("exit_check_not_executable", f"退出计划检查结论 {exit_check!r} 不允许卖出执行。"))
    if exit_check == "needs_review" and not user_confirmed:
        blockers.append(CheckItem("missing_user_confirmation", "退出计划检查需要人工确认，但卖出执行记录未确认。"))
    if mode == "real" and not user_confirmed:
        blockers.append(CheckItem("real_exit_execution_without_confirmation", "真实卖出执行必须人工确认。"))
    if exit_execution_requires_confirmation(execution) and not confirmation_snapshot_confirmed(execution):
        confirmation_id = value_at(execution, "execution.confirmation_id") or "missing"
        blockers.append(CheckItem("missing_confirmed_manual_confirmation_record", f"缺少已确认的人工确认记录：{confirmation_id}。"))
    return blockers


def check_price_and_position(execution: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    execution_price = as_float(value_at(execution, "order.execution_price"))
    planned_exit_price = as_float(value_at(execution, "exit_snapshot.planned_exit_price"))
    min_acceptable_exit_price = as_float(value_at(execution, "exit_snapshot.min_acceptable_exit_price"))
    exited_position_pct = as_float(value_at(execution, "order.exited_position_pct_of_total_assets"))
    current_position_pct = as_float(value_at(execution, "exit_plan_snapshot.position_snapshot.position_pct_of_total_assets"))
    slippage_pct = as_float(value_at(execution, "order.slippage_pct_vs_plan"))

    if execution_price is not None and execution_price <= 0:
        blockers.append(CheckItem("invalid_execution_price", "卖出价格必须大于 0。"))
    if execution_price is not None and min_acceptable_exit_price is not None and execution_price < min_acceptable_exit_price:
        blockers.append(
            CheckItem(
                "execution_price_below_min_acceptable",
                f"卖出价格 {execution_price:.2f} 低于最低可接受退出价 {min_acceptable_exit_price:.2f}。",
            )
        )
    if value_at(execution, "order.price_above_min_acceptable") is False:
        blockers.append(CheckItem("execution_marked_below_min_price", "卖出执行记录标记为低于最低可接受退出价。"))
    if exited_position_pct is not None and exited_position_pct <= 0:
        blockers.append(CheckItem("invalid_exited_position_pct", "卖出仓位必须大于 0。"))
    if exited_position_pct is not None and current_position_pct is not None and exited_position_pct > current_position_pct + 0.01:
        blockers.append(CheckItem("exit_position_above_current_position", f"卖出仓位 {exited_position_pct:.2f}% 高于当前持仓 {current_position_pct:.2f}%。"))

    if slippage_pct is not None and slippage_pct < 0:
        warnings.append(CheckItem("negative_exit_slippage", f"卖出价较计划退出价低 {abs(slippage_pct):.2f}%。"))
    if execution_price is not None and planned_exit_price is not None and execution_price > planned_exit_price:
        info.append(CheckItem("execution_above_plan_exit_price", "卖出价高于计划退出价。"))

    return blockers, warnings, info


def check_exit_execution(execution: dict[str, Any]) -> dict[str, Any]:
    blockers = check_required_fields(execution)
    blockers.extend(check_exit_gate_and_confirmation(execution))
    price_blockers, warnings, info = check_price_and_position(execution)
    blockers.extend(price_blockers)

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "exit_execution_id": value_at(execution, "execution.id"),
        "source_exit_plan_id": value_at(execution, "execution.source_exit_plan_id"),
        "source_position_id": value_at(execution, "execution.source_position_id"),
        "source_trade_plan_id": value_at(execution, "execution.source_trade_plan_id"),
        "conclusion": conclusion,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
    }


def run_check(execution_path: Path) -> dict[str, Any]:
    return check_exit_execution(load_yaml(execution_path))


def print_text(result: dict[str, Any]) -> None:
    print(f"exit execution: {result.get('exit_execution_id') or '-'}")
    print(f"source exit plan: {result.get('source_exit_plan_id') or '-'}")
    print(f"source position: {result.get('source_position_id') or '-'}")
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
    parser = argparse.ArgumentParser(description="Check sell execution record before downstream review generation.")
    parser.add_argument("--exit-execution", required=True, help="Path to sell execution YAML.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_check(Path(args.exit_execution))
    except Exception as exc:
        print(f"sell execution check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
