#!/usr/bin/env python3
"""Create a trade review YAML from an executed trade plan."""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from new_trade_plan import set_value, write_yaml
    from risk_check import as_float, load_yaml, value_at


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def infer_result_category(trade_return_pct: float, followed_plan: bool) -> str:
    if followed_plan and trade_return_pct >= 0:
        return "strategy_profit"
    if followed_plan and trade_return_pct < 0:
        return "strategy_loss"
    if not followed_plan and trade_return_pct >= 0:
        return "execution_error_profit"
    return "execution_error_loss"


def validate_review_labels(profile: dict[str, Any], result_category: str, error_tags: list[str]) -> None:
    review_config = profile.get("review", {})
    allowed_categories = set(review_config.get("result_categories", []))
    allowed_error_tags = set(review_config.get("error_tags", []))

    if allowed_categories and result_category not in allowed_categories:
        allowed = ", ".join(sorted(allowed_categories))
        raise ValueError(f"unknown result category {result_category}; allowed: {allowed}")

    unknown_tags = sorted(set(error_tags) - allowed_error_tags)
    if allowed_error_tags and unknown_tags:
        allowed = ", ".join(sorted(allowed_error_tags))
        raise ValueError(f"unknown error tags {unknown_tags}; allowed: {allowed}")


def build_output_path(base_dir: Path, review_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{review_id}.yaml"


def create_trade_review(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    profile = load_yaml(Path(args.profile))
    template = load_yaml(Path(args.template))
    plan = load_yaml(Path(args.plan))
    review = deepcopy(template)
    created_at, stamp = now_stamp()
    review_id = args.id or f"TR-{stamp}"
    trade_plan_id = value_at(plan, "trade_plan.id")

    entry_price = as_float(args.entry_price, as_float(value_at(plan, "price_plan.planned_buy_price")))
    exit_price = as_float(args.exit_price)
    position_pct = as_float(args.position_pct, as_float(value_at(plan, "position_plan.planned_position_pct_of_total_assets")))

    if entry_price is None or entry_price <= 0:
        raise ValueError("entry price must be greater than 0")
    if exit_price is None:
        raise ValueError("exit price is required")
    if position_pct is None:
        raise ValueError("position percent is required")

    trade_return_pct = (exit_price - entry_price) / entry_price * 100
    portfolio_return_pct = position_pct * trade_return_pct / 100
    result_category = args.result_category or infer_result_category(trade_return_pct, args.followed_plan)
    error_tags = args.error_tag or []
    validate_review_labels(profile, result_category, error_tags)

    set_value(review, "review.id", review_id)
    set_value(review, "review.status", "draft")
    set_value(review, "review.created_at", created_at)
    set_value(review, "review.source_trade_plan_id", trade_plan_id)

    set_value(review, "stock.code", value_at(plan, "stock.code"))
    set_value(review, "stock.name", value_at(plan, "stock.name"))
    set_value(review, "stock.exchange", value_at(plan, "stock.exchange"))
    set_value(review, "stock.industry", value_at(plan, "stock.industry"))

    set_value(review, "execution.entry_date", args.entry_date)
    set_value(review, "execution.exit_date", args.exit_date)
    set_value(review, "execution.entry_price", entry_price)
    set_value(review, "execution.exit_price", exit_price)
    set_value(review, "execution.position_pct_of_total_assets", position_pct)
    set_value(review, "execution.exit_reason", args.exit_reason)
    set_value(review, "execution.followed_plan", args.followed_plan)

    set_value(review, "result.trade_return_pct", round(trade_return_pct, 4))
    set_value(review, "result.portfolio_return_pct", round(portfolio_return_pct, 4))
    set_value(review, "result.result_category", result_category)
    set_value(review, "result.error_tags", error_tags)

    if args.lesson:
        set_value(review, "review_questions.lesson", args.lesson)
    if args.next_action:
        set_value(review, "review_questions.next_action", args.next_action)
    set_value(review, "trade_plan_snapshot", plan)

    output_path = build_output_path(Path(args.output_dir), review_id, args.output)
    return review, output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a draft trade review YAML from a trade plan.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--template", default="templates/trade-review.example.yaml", help="Path to trade review template YAML.")
    parser.add_argument("--plan", required=True, help="Path to executed trade plan YAML.")
    parser.add_argument("--output-dir", default="reviews", help="Directory for generated trade reviews.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit review id. Defaults to TR-YYYYMMDD-HHMMSS.")

    parser.add_argument("--entry-date", required=True, help="Entry date.")
    parser.add_argument("--exit-date", required=True, help="Exit date.")
    parser.add_argument("--entry-price", type=float, help="Actual entry price. Defaults to planned buy price.")
    parser.add_argument("--exit-price", type=float, required=True, help="Actual exit price.")
    parser.add_argument("--position-pct", type=float, help="Actual position percent. Defaults to planned position percent.")
    parser.add_argument("--exit-reason", required=True, help="Why the trade was exited.")
    parser.add_argument("--followed-plan", action=argparse.BooleanOptionalAction, default=True, help="Whether execution followed the original plan.")
    parser.add_argument("--result-category", help="Review result category. Defaults to inferred category.")
    parser.add_argument("--error-tag", action="append", default=[], help="Execution or strategy error tag. Can be repeated.")
    parser.add_argument("--lesson", help="Main lesson from the trade.")
    parser.add_argument("--next-action", help="Next action after review.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review, output_path = create_trade_review(args)
    write_yaml(output_path, review, overwrite=args.overwrite)
    print(f"created trade review: {output_path}")
    print(f"result category: {review['result']['result_category']}")
    print(f"trade return pct: {review['result']['trade_return_pct']}")
    print(f"portfolio return pct: {review['result']['portfolio_return_pct']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"create trade review failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
