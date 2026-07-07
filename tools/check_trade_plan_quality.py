#!/usr/bin/env python3
"""Check trade plan draft completeness before risk validation."""

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


PLACEHOLDER_MARKERS = ("待补充", "示例", "YYYY", "TODO", "todo")


@dataclass
class CheckItem:
    code: str
    message: str


def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def contains_placeholder(value: Any) -> bool:
    text = text_of(value)
    return any(marker in text for marker in PLACEHOLDER_MARKERS)


def descriptions(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            result.append(text_of(item.get("description")))
        else:
            result.append(text_of(item))
    return [item.strip() for item in result if item and item.strip()]


def has_any_text(items: list[str], keywords: tuple[str, ...]) -> bool:
    return any(any(keyword in item for keyword in keywords) for item in items)


def append_missing_or_placeholder(blockers: list[CheckItem], plan: dict[str, Any], path: str, label: str) -> None:
    value = value_at(plan, path)
    if is_missing(value):
        blockers.append(CheckItem(f"missing_{path.replace('.', '_')}", f"缺少字段：{label}。"))
    elif contains_placeholder(value):
        blockers.append(CheckItem(f"placeholder_{path.replace('.', '_')}", f"{label}仍包含占位内容。"))


def validate_trade_terms(plan: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []

    planned_buy_price = as_float(value_at(plan, "price_plan.planned_buy_price"))
    stop_loss_price = as_float(value_at(plan, "price_plan.stop_loss_price"))
    max_loss_pct = as_float(value_at(plan, "risk_calculation.max_loss_pct_of_total_assets"))
    position_pct = as_float(value_at(plan, "position_plan.planned_position_pct_of_total_assets"))

    if planned_buy_price is not None and stop_loss_price is not None and stop_loss_price >= planned_buy_price:
        blockers.append(CheckItem("invalid_stop_loss_price", "止损价必须低于计划买入价。"))

    if planned_buy_price and stop_loss_price is not None and position_pct is not None:
        expected = round(position_pct * abs(planned_buy_price - stop_loss_price) / planned_buy_price, 4)
        if max_loss_pct is None:
            blockers.append(CheckItem("missing_max_loss_calculation", "缺少按买入价、止损价和仓位计算的单笔最大亏损。"))
        elif abs(max_loss_pct - expected) > 0.01:
            warnings.append(CheckItem("max_loss_calculation_mismatch", f"单笔最大亏损 {max_loss_pct:.4f}% 与计算值 {expected:.4f}% 不一致。"))

    return blockers, warnings


def validate_evidence(plan: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    key_evidence = descriptions(value_at(plan, "strategy.key_evidence"))
    risks = descriptions(value_at(plan, "strategy.counter_evidence_and_risks"))
    observation_items = descriptions(value_at(plan, "exit_plan.observation_items"))
    review_focus = descriptions(value_at(plan, "review_seed.review_focus"))

    if len(key_evidence) < 2:
        blockers.append(CheckItem("insufficient_key_evidence", "关键证据至少需要 2 条。"))
    if len(risks) < 1:
        blockers.append(CheckItem("missing_counter_evidence_and_risks", "至少需要 1 条反证或风险。"))
    if any(contains_placeholder(item) for item in key_evidence):
        blockers.append(CheckItem("placeholder_key_evidence", "关键证据仍包含占位内容。"))
    if any(contains_placeholder(item) for item in risks):
        blockers.append(CheckItem("placeholder_risk", "反证和风险仍包含占位内容。"))

    all_evidence_text = key_evidence + risks + observation_items + [text_of(value_at(plan, "strategy.buy_reason"))]
    if not has_any_text(all_evidence_text, ("观察池", "[trend_strength]", "[value_quality]", "[event_catalyst]")):
        warnings.append(CheckItem("missing_candidate_pool_trace", "未看到候选池来源或策略证据前缀，需确认该计划不是临时拍脑袋生成。"))

    strategy = value_at(plan, "strategy.source")
    if strategy == "value_quality" and not has_any_text(all_evidence_text, ("估值", "PE", "PB", "分位")):
        warnings.append(CheckItem("missing_valuation_evidence", "价值质量计划未看到估值或估值分位证据。"))
    if not review_focus:
        info.append(CheckItem("missing_review_focus", "复盘关注点为空，建议补充便于退出后归因。"))

    return blockers, warnings, info


def validate_exit_plan(plan: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    fields = [
        ("exit_plan.stop_loss_conditions", "止损条件"),
        ("exit_plan.take_profit_conditions", "止盈条件"),
        ("exit_plan.invalidation_conditions", "失效条件"),
    ]
    for path, label in fields:
        value = value_at(plan, path)
        if is_missing(value):
            blockers.append(CheckItem(f"missing_{path.replace('.', '_')}", f"缺少{label}。"))
        elif contains_placeholder(value):
            blockers.append(CheckItem(f"placeholder_{path.replace('.', '_')}", f"{label}仍包含占位内容。"))
    return blockers


def check_trade_plan_quality(plan: dict[str, Any]) -> dict[str, Any]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    required_text_fields = [
        ("stock.code", "股票代码"),
        ("stock.name", "股票名称"),
        ("stock.exchange", "交易所"),
        ("stock.industry", "行业"),
        ("strategy.source", "策略来源"),
        ("strategy.buy_reason", "买入理由"),
        ("price_plan.planned_buy_price", "计划买入价"),
        ("price_plan.stop_loss_price", "止损价"),
        ("position_plan.planned_position_pct_of_total_assets", "计划仓位"),
    ]
    for path, label in required_text_fields:
        append_missing_or_placeholder(blockers, plan, path, label)

    blockers.extend(validate_exit_plan(plan))
    trade_blockers, trade_warnings = validate_trade_terms(plan)
    evidence_blockers, evidence_warnings, evidence_info = validate_evidence(plan)
    blockers.extend(trade_blockers)
    blockers.extend(evidence_blockers)
    warnings.extend(trade_warnings)
    warnings.extend(evidence_warnings)
    info.extend(evidence_info)

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "trade_plan_id": value_at(plan, "trade_plan.id"),
        "conclusion": conclusion,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
    }


def run_check(plan_path: Path) -> dict[str, Any]:
    return check_trade_plan_quality(load_yaml(plan_path))


def print_text(result: dict[str, Any]) -> None:
    print(f"trade plan: {result.get('trade_plan_id') or '-'}")
    print(f"conclusion: {result['conclusion']}")
    for title, key in (("blockers", "blockers"), ("warnings", "warnings"), ("info", "info")):
        print(f"\n{title}:")
        items = result[key]
        if not items:
            print("- none")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check trade plan draft quality before risk validation.")
    parser.add_argument("--plan", required=True, help="Path to trade plan YAML.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_check(Path(args.plan))
    except Exception as exc:
        print(f"trade plan quality check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
