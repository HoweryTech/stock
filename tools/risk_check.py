#!/usr/bin/env python3
"""Validate a trade plan against the personal investment profile."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CheckItem:
    code: str
    message: str


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def value_at(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def required_field_paths() -> dict[str, tuple[str, str]]:
    return {
        "stock_code": ("stock.code", "股票代码"),
        "stock_name": ("stock.name", "股票名称"),
        "strategy_source": ("strategy.source", "策略来源"),
        "buy_reason": ("strategy.buy_reason", "买入理由"),
        "key_evidence": ("strategy.key_evidence", "关键证据"),
        "counter_evidence_and_risks": ("strategy.counter_evidence_and_risks", "反证和风险"),
        "planned_buy_price": ("price_plan.planned_buy_price", "计划买入价"),
        "max_acceptable_buy_price": ("price_plan.max_acceptable_buy_price", "最大可接受买入价"),
        "stop_loss_price": ("price_plan.stop_loss_price", "止损价"),
        "planned_position_pct": ("position_plan.planned_position_pct_of_total_assets", "计划仓位"),
        "max_loss_pct_of_total_assets": ("risk_calculation.max_loss_pct_of_total_assets", "单笔最大亏损"),
        "exit_conditions": ("exit_plan", "卖出条件"),
        "invalidation_conditions": ("exit_plan.invalidation_conditions", "失效条件"),
    }


def validate_required_fields(profile: dict[str, Any], plan: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    paths = required_field_paths()
    required_fields = profile.get("trade_plan_required_fields") or paths.keys()

    for field in required_fields:
        if field not in paths:
            continue
        path, label = paths[field]
        if is_missing(value_at(plan, path)):
            blockers.append(CheckItem(f"missing_{field}", f"缺少必填字段：{label}。"))

    return blockers


def validate_plan(profile: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    risk = profile.get("risk", {})
    universe = profile.get("universe_filters", {})

    blockers = validate_required_fields(profile, plan)
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    planned_buy_price = as_float(value_at(plan, "price_plan.planned_buy_price"))
    current_price = as_float(value_at(plan, "price_plan.current_price"), planned_buy_price)
    configured_max_buy_price = as_float(value_at(plan, "price_plan.max_acceptable_buy_price"))
    stop_loss_price = as_float(value_at(plan, "price_plan.stop_loss_price"))
    planned_position_pct = as_float(value_at(plan, "position_plan.planned_position_pct_of_total_assets"))
    current_industry_pct = as_float(value_at(plan, "position_plan.current_industry_position_pct"), 0.0) or 0.0
    expected_stock_pct = as_float(value_at(plan, "position_plan.expected_stock_position_pct_after_buy"))
    expected_industry_pct = as_float(value_at(plan, "position_plan.expected_industry_position_pct_after_buy"))
    expected_total_pct = as_float(value_at(plan, "position_plan.expected_total_position_pct_after_buy"))

    chase_limit_pct = as_float(risk.get("chase_limit_pct_above_plan_price"), 0.0) or 0.0
    max_loss_limit = as_float(risk.get("max_loss_per_trade_pct_of_total_assets"), 0.0) or 0.0
    max_stock_pct = as_float(risk.get("max_position_pct_per_stock"), 100.0) or 100.0
    max_industry_pct = as_float(risk.get("max_position_pct_per_industry"), 100.0) or 100.0
    max_total_pct = as_float(risk.get("max_total_position_pct"), 100.0) or 100.0

    calculated_max_buy_price = None
    if planned_buy_price is not None:
        calculated_max_buy_price = planned_buy_price * (1 + chase_limit_pct / 100)
    max_acceptable_buy_price = configured_max_buy_price or calculated_max_buy_price

    calculated_max_loss_pct = None
    if planned_buy_price and stop_loss_price is not None and planned_position_pct is not None:
        calculated_max_loss_pct = planned_position_pct * abs(planned_buy_price - stop_loss_price) / planned_buy_price

    declared_max_loss_pct = as_float(value_at(plan, "risk_calculation.max_loss_pct_of_total_assets"))
    max_loss_pct = calculated_max_loss_pct if calculated_max_loss_pct is not None else declared_max_loss_pct

    if value_at(plan, "stock.is_st") and universe.get("exclude_st", True):
        blockers.append(CheckItem("stock_is_st", "标的是 ST 股票，禁止买入。"))
    if value_at(plan, "stock.has_delisting_risk") and universe.get("exclude_delisting_risk", True):
        blockers.append(CheckItem("delisting_risk", "标的存在退市风险，禁止买入。"))
    if value_at(plan, "stock.is_suspended") and universe.get("exclude_suspended", True):
        blockers.append(CheckItem("stock_suspended", "标的当前停牌，禁止买入。"))
    if value_at(plan, "stock.abnormal_trading_status") and universe.get("exclude_abnormal_trading_status", True):
        blockers.append(CheckItem("abnormal_trading_status", "标的交易状态异常，禁止买入。"))

    if current_price is not None and max_acceptable_buy_price is not None:
        if current_price > max_acceptable_buy_price:
            blockers.append(
                CheckItem(
                    "price_too_far_above_plan",
                    f"当前价格 {current_price:.2f} 高于最大可接受买入价 {max_acceptable_buy_price:.2f}。",
                )
            )
        elif planned_buy_price is not None and current_price > planned_buy_price:
            info.append(CheckItem("price_above_plan", "当前价格高于计划买入价，但未超过追高阈值。"))

    if max_loss_pct is not None and max_loss_limit and max_loss_pct > max_loss_limit:
        blockers.append(
            CheckItem(
                "risk_per_trade_exceeded",
                f"单笔最大亏损 {max_loss_pct:.2f}% 超过上限 {max_loss_limit:.2f}%。",
            )
        )

    if expected_stock_pct is not None and expected_stock_pct > max_stock_pct:
        blockers.append(
            CheckItem("stock_position_limit_exceeded", f"买入后单票仓位 {expected_stock_pct:.2f}% 超过上限 {max_stock_pct:.2f}%。")
        )
    if expected_industry_pct is not None and expected_industry_pct > max_industry_pct:
        blockers.append(
            CheckItem(
                "industry_position_limit_exceeded",
                f"买入后行业仓位 {expected_industry_pct:.2f}% 超过上限 {max_industry_pct:.2f}%。",
            )
        )
    if expected_total_pct is not None and expected_total_pct > max_total_pct:
        blockers.append(CheckItem("total_position_limit_exceeded", f"买入后总仓位 {expected_total_pct:.2f}% 超过上限 {max_total_pct:.2f}%。"))

    if expected_industry_pct is not None and expected_industry_pct > max_industry_pct * 0.8:
        warnings.append(CheckItem("industry_position_high", "买入后行业仓位接近上限。"))
    if expected_total_pct is not None and expected_total_pct > max_total_pct * 0.8:
        warnings.append(CheckItem("total_position_high", "买入后总仓位接近上限。"))
    if current_industry_pct > 0:
        info.append(CheckItem("same_industry_position_exists", "当前组合中已有同行业持仓。"))

    strategy_source = value_at(plan, "strategy.source")
    enabled_strategies = profile.get("strategies", {})
    if strategy_source and strategy_source not in enabled_strategies:
        blockers.append(CheckItem("unknown_strategy_source", f"策略来源 {strategy_source} 未在投资体系中定义。"))
    elif strategy_source and not enabled_strategies.get(strategy_source, {}).get("enabled", False):
        blockers.append(CheckItem("strategy_disabled", f"策略 {strategy_source} 当前未启用。"))
    elif strategy_source == "event_catalyst":
        warnings.append(CheckItem("event_catalyst_uncertainty", "事件催化策略不确定性较高，需要确认反证和风险。"))

    confirmation_required = bool(value_at(plan, "risk_check_expectation.user_confirmation_required"))
    if blockers:
        conclusion = "blocked"
    elif warnings or confirmation_required:
        conclusion = "needs_confirmation"
    else:
        conclusion = "pass"

    return {
        "trade_plan_id": value_at(plan, "trade_plan.id"),
        "conclusion": conclusion,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in info],
        "calculations": {
            "planned_buy_price": planned_buy_price,
            "current_price": current_price,
            "max_acceptable_buy_price": max_acceptable_buy_price,
            "stop_loss_price": stop_loss_price,
            "planned_position_pct_of_total_assets": planned_position_pct,
            "calculated_max_loss_pct_of_total_assets": calculated_max_loss_pct,
            "declared_max_loss_pct_of_total_assets": declared_max_loss_pct,
            "expected_stock_position_pct_after_buy": expected_stock_pct,
            "expected_industry_position_pct_after_buy": expected_industry_pct,
            "expected_total_position_pct_after_buy": expected_total_pct,
        },
        "confirmation_text": value_at(plan, "risk_check_expectation.confirmation_text") if conclusion != "blocked" else None,
    }


def print_text_report(result: dict[str, Any]) -> None:
    conclusion_labels = {
        "pass": "通过",
        "blocked": "阻断",
        "needs_confirmation": "需确认",
    }
    print(f"交易计划编号：{result.get('trade_plan_id') or '-'}")
    print(f"校验结论：{conclusion_labels.get(result['conclusion'], result['conclusion'])}")

    for title, key in (("阻断项", "blockers"), ("强提醒项", "warnings"), ("信息提示项", "info")):
        print(f"\n{title}：")
        items = result[key]
        if not items:
            print("- 无")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")

    print("\n计算结果：")
    calculations = result["calculations"]
    ordered_keys = [
        "planned_buy_price",
        "current_price",
        "max_acceptable_buy_price",
        "stop_loss_price",
        "planned_position_pct_of_total_assets",
        "calculated_max_loss_pct_of_total_assets",
        "declared_max_loss_pct_of_total_assets",
        "expected_stock_position_pct_after_buy",
        "expected_industry_position_pct_after_buy",
        "expected_total_position_pct_after_buy",
    ]
    for key in ordered_keys:
        value = calculations.get(key)
        print(f"- {key}: {'-' if value is None else round(value, 4)}")

    if result.get("confirmation_text"):
        print("\n人工确认文案：")
        print(result["confirmation_text"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a trade plan against an investment profile.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--plan", default="templates/trade-plan.example.yaml", help="Path to trade plan YAML.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile = load_yaml(Path(args.profile))
    plan = load_yaml(Path(args.plan))
    result = validate_plan(profile, plan)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_report(result)

    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"risk check failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

