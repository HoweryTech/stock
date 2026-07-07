#!/usr/bin/env python3
"""Check exit plans before sell execution."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, is_missing, load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import as_float, is_missing, load_yaml, value_at


ALLOWED_EXIT_TYPES = {"stop_loss", "take_profit", "thesis_invalidated", "risk_reduction"}
ALLOWED_URGENCY = {"normal", "soon", "immediate"}


@dataclass
class CheckItem:
    code: str
    message: str


def append_missing(blockers: list[CheckItem], exit_plan: dict[str, Any], path: str, label: str) -> None:
    if is_missing(value_at(exit_plan, path)):
        blockers.append(CheckItem(f"missing_{path.replace('.', '_')}", f"缺少{label}。"))


def check_required_fields(exit_plan: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    required = [
        ("exit_plan.id", "退出计划编号"),
        ("exit_plan.source_position_id", "来源持仓编号"),
        ("exit_plan.exit_type", "退出类型"),
        ("exit_plan.urgency", "退出紧急度"),
        ("stock.code", "股票代码"),
        ("stock.name", "股票名称"),
        ("position_snapshot.current_price", "当前价格"),
        ("position_snapshot.position_pct_of_total_assets", "当前仓位"),
        ("decision.exit_reason", "退出理由"),
        ("decision.evidence", "退出证据"),
        ("decision.risks_if_hold", "继续持有风险"),
        ("decision.planned_exit_price", "计划退出价格"),
        ("decision.exit_position_pct", "计划退出仓位"),
    ]
    for path, label in required:
        append_missing(blockers, exit_plan, path, label)
    return blockers


def check_type_and_source(exit_plan: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []

    exit_type = value_at(exit_plan, "exit_plan.exit_type")
    urgency = value_at(exit_plan, "exit_plan.urgency")
    action_codes = value_at(exit_plan, "checks.source_action_codes") or []
    daily_conclusion = value_at(exit_plan, "checks.daily_check_conclusion")
    triggered_by_daily_check = bool(value_at(exit_plan, "checks.triggered_by_daily_check"))

    if exit_type and exit_type not in ALLOWED_EXIT_TYPES:
        blockers.append(CheckItem("unknown_exit_type", f"退出类型 {exit_type!r} 不在允许范围内。"))
    if urgency and urgency not in ALLOWED_URGENCY:
        blockers.append(CheckItem("unknown_urgency", f"退出紧急度 {urgency!r} 不在允许范围内。"))
    if "stop_loss_triggered" in action_codes and exit_type != "stop_loss":
        blockers.append(CheckItem("stop_loss_action_mismatch", "日检触发止损，但退出类型不是 stop_loss。"))
    if exit_type == "stop_loss" and not bool(value_at(exit_plan, "decision.must_exit")):
        blockers.append(CheckItem("stop_loss_not_mandatory", "止损退出必须标记为 must_exit=true。"))
    if exit_type == "stop_loss" and urgency != "immediate":
        warnings.append(CheckItem("stop_loss_not_immediate", "止损退出建议标记为 immediate。"))
    if triggered_by_daily_check and is_missing(daily_conclusion):
        warnings.append(CheckItem("missing_daily_check_conclusion", "标记为由日检触发，但缺少日检结论。"))
    if value_at(exit_plan, "checks.matched_original_plan") is False:
        warnings.append(CheckItem("exit_not_matched_original_plan", "退出计划标记为不符合原交易计划，需要人工确认。"))

    return blockers, warnings


def check_price_and_position(exit_plan: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    current_price = as_float(value_at(exit_plan, "position_snapshot.current_price"))
    entry_price = as_float(value_at(exit_plan, "position_snapshot.entry_price"))
    current_return_pct = as_float(value_at(exit_plan, "position_snapshot.current_return_pct"))
    current_position_pct = as_float(value_at(exit_plan, "position_snapshot.position_pct_of_total_assets"))
    planned_exit_price = as_float(value_at(exit_plan, "decision.planned_exit_price"))
    min_acceptable_exit_price = as_float(value_at(exit_plan, "decision.min_acceptable_exit_price"))
    exit_position_pct = as_float(value_at(exit_plan, "decision.exit_position_pct"))
    exit_type = value_at(exit_plan, "exit_plan.exit_type")

    if planned_exit_price is not None and planned_exit_price <= 0:
        blockers.append(CheckItem("invalid_planned_exit_price", "计划退出价格必须大于 0。"))
    if min_acceptable_exit_price is not None and min_acceptable_exit_price <= 0:
        blockers.append(CheckItem("invalid_min_acceptable_exit_price", "最低可接受退出价必须大于 0。"))
    if min_acceptable_exit_price is not None and planned_exit_price is not None and min_acceptable_exit_price > planned_exit_price:
        blockers.append(CheckItem("min_exit_price_above_plan", "最低可接受退出价不能高于计划退出价。"))
    if exit_position_pct is not None and exit_position_pct <= 0:
        blockers.append(CheckItem("invalid_exit_position_pct", "计划退出仓位必须大于 0。"))
    if current_position_pct is not None and exit_position_pct is not None and exit_position_pct > current_position_pct + 0.01:
        blockers.append(
            CheckItem("exit_position_above_current", f"计划退出仓位 {exit_position_pct:.2f}% 高于当前持仓 {current_position_pct:.2f}%。")
        )
    if current_price is not None and planned_exit_price is not None and abs(planned_exit_price - current_price) / current_price > 0.05:
        warnings.append(CheckItem("exit_price_far_from_current", "计划退出价偏离当前价超过 5%，需要确认可成交性。"))
    if exit_type == "take_profit" and current_return_pct is not None and current_return_pct < 0:
        warnings.append(CheckItem("take_profit_with_negative_return", "退出类型为止盈，但当前收益率为负。"))
    if entry_price is not None and planned_exit_price is not None:
        expected_return = round((planned_exit_price - entry_price) / entry_price * 100, 4) if entry_price else None
        info.append(CheckItem("planned_exit_return", f"按计划退出价估算收益率为 {expected_return:.2f}%。"))

    return blockers, warnings, info


def check_exit_plan(exit_plan: dict[str, Any]) -> dict[str, Any]:
    blockers = check_required_fields(exit_plan)
    type_blockers, type_warnings = check_type_and_source(exit_plan)
    price_blockers, price_warnings, info = check_price_and_position(exit_plan)
    blockers.extend(type_blockers)
    blockers.extend(price_blockers)
    warnings = type_warnings + price_warnings

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "exit_plan_id": value_at(exit_plan, "exit_plan.id"),
        "source_position_id": value_at(exit_plan, "exit_plan.source_position_id"),
        "conclusion": conclusion,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
    }


def run_check(exit_plan_path: Path) -> dict[str, Any]:
    return check_exit_plan(load_yaml(exit_plan_path))


def print_text(result: dict[str, Any]) -> None:
    print(f"exit plan: {result.get('exit_plan_id') or '-'}")
    print(f"source position: {result.get('source_position_id') or '-'}")
    print(f"conclusion: {result['conclusion']}")
    for title, key in (("blockers", "blockers"), ("warnings", "warnings"), ("info", "info")):
        print(f"\n{title}:")
        items = result[key]
        if not items:
            print("- none")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check exit plan before sell execution.")
    parser.add_argument("--exit-plan", required=True, help="Path to exit plan YAML.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_check(Path(args.exit_plan))
    except Exception as exc:
        print(f"exit plan check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
