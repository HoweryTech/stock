#!/usr/bin/env python3
"""Check trade review completeness and attribution quality."""

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


@dataclass
class CheckItem:
    code: str
    message: str


def append_missing(blockers: list[CheckItem], review: dict[str, Any], path: str, label: str) -> None:
    if is_missing(value_at(review, path)):
        blockers.append(CheckItem(f"missing_{path.replace('.', '_')}", f"缺少{label}。"))


def check_required_fields(review: dict[str, Any]) -> list[CheckItem]:
    blockers: list[CheckItem] = []
    required = [
        ("review.id", "复盘编号"),
        ("review.source_trade_plan_id", "来源交易计划编号"),
        ("stock.code", "股票代码"),
        ("stock.name", "股票名称"),
        ("execution.entry_date", "入场日期"),
        ("execution.exit_date", "退出日期"),
        ("execution.entry_price", "入场价格"),
        ("execution.exit_price", "退出价格"),
        ("execution.position_pct_of_total_assets", "交易仓位"),
        ("execution.exit_reason", "退出原因"),
        ("result.trade_return_pct", "单笔收益率"),
        ("result.portfolio_return_pct", "组合收益贡献"),
        ("result.result_category", "结果分类"),
    ]
    for path, label in required:
        append_missing(blockers, review, path, label)
    return blockers


def check_numbers(review: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []

    entry_price = as_float(value_at(review, "execution.entry_price"))
    exit_price = as_float(value_at(review, "execution.exit_price"))
    position_pct = as_float(value_at(review, "execution.position_pct_of_total_assets"))
    trade_return_pct = as_float(value_at(review, "result.trade_return_pct"))
    portfolio_return_pct = as_float(value_at(review, "result.portfolio_return_pct"))

    if entry_price is not None and entry_price <= 0:
        blockers.append(CheckItem("invalid_entry_price", "入场价格必须大于 0。"))
    if exit_price is not None and exit_price <= 0:
        blockers.append(CheckItem("invalid_exit_price", "退出价格必须大于 0。"))
    if position_pct is not None and position_pct <= 0:
        blockers.append(CheckItem("invalid_position_pct", "交易仓位必须大于 0。"))
    if entry_price and exit_price is not None and trade_return_pct is not None:
        expected_trade_return = round((exit_price - entry_price) / entry_price * 100, 4)
        if abs(trade_return_pct - expected_trade_return) > 0.01:
            warnings.append(
                CheckItem("trade_return_mismatch", f"单笔收益率 {trade_return_pct:.4f}% 与入场/退出价计算值 {expected_trade_return:.4f}% 不一致。")
            )
    if position_pct is not None and trade_return_pct is not None and portfolio_return_pct is not None:
        expected_portfolio_return = round(position_pct * trade_return_pct / 100, 4)
        if abs(portfolio_return_pct - expected_portfolio_return) > 0.01:
            warnings.append(
                CheckItem(
                    "portfolio_return_mismatch",
                    f"组合收益贡献 {portfolio_return_pct:.4f}% 与仓位/收益率计算值 {expected_portfolio_return:.4f}% 不一致。",
                )
            )

    return blockers, warnings


def check_attribution(review: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []

    result_category = value_at(review, "result.result_category")
    followed_plan = value_at(review, "execution.followed_plan")
    lesson = value_at(review, "review_questions.lesson")
    next_action = value_at(review, "review_questions.next_action")
    error_tags = value_at(review, "result.error_tags") or []

    allowed_categories = {"strategy_profit", "strategy_loss", "execution_error_profit", "execution_error_loss"}
    if result_category and result_category not in allowed_categories:
        blockers.append(CheckItem("unknown_result_category", f"结果分类 {result_category!r} 不在允许范围内。"))
    if result_category and result_category.startswith("execution_error") and not error_tags:
        warnings.append(CheckItem("missing_error_tags", "执行错误类复盘应至少标记一个错误标签。"))
    if followed_plan is None:
        warnings.append(CheckItem("missing_followed_plan_answer", "未回答是否符合原计划。"))
    for path, label in (
        ("review_questions.buy_reason_still_valid", "买入理由是否仍成立"),
        ("review_questions.exit_reason_matches_plan", "退出原因是否符合计划"),
        ("review_questions.risk_control_followed", "风控是否执行"),
        ("review_questions.position_sizing_followed", "仓位是否执行"),
    ):
        if value_at(review, path) is None:
            warnings.append(CheckItem(f"unanswered_{path.replace('.', '_')}", f"复盘问题未回答：{label}。"))
    if is_missing(lesson):
        warnings.append(CheckItem("missing_lesson", "复盘教训为空。"))
    if is_missing(next_action):
        warnings.append(CheckItem("missing_next_action", "下一步动作为空。"))

    return blockers, warnings


def check_trade_review_quality(review: dict[str, Any]) -> dict[str, Any]:
    blockers = check_required_fields(review)
    number_blockers, number_warnings = check_numbers(review)
    attribution_blockers, attribution_warnings = check_attribution(review)
    blockers.extend(number_blockers)
    blockers.extend(attribution_blockers)
    warnings = number_warnings + attribution_warnings

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "review_id": value_at(review, "review.id"),
        "source_trade_plan_id": value_at(review, "review.source_trade_plan_id"),
        "conclusion": conclusion,
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
    }


def run_check(review_path: Path) -> dict[str, Any]:
    return check_trade_review_quality(load_yaml(review_path))


def print_text(result: dict[str, Any]) -> None:
    print(f"review: {result.get('review_id') or '-'}")
    print(f"source trade plan: {result.get('source_trade_plan_id') or '-'}")
    print(f"conclusion: {result['conclusion']}")
    for title, key in (("blockers", "blockers"), ("warnings", "warnings")):
        print(f"\n{title}:")
        items = result[key]
        if not items:
            print("- none")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check trade review completeness and attribution quality.")
    parser.add_argument("--review", required=True, help="Path to trade review YAML.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_check(Path(args.review))
    except Exception as exc:
        print(f"trade review quality check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
