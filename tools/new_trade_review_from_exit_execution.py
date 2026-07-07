#!/usr/bin/env python3
"""Create a trade review draft from a sell execution record."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.new_trade_plan import set_value, write_yaml
    from tools.new_trade_review import infer_result_category, validate_review_labels
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from new_trade_plan import set_value, write_yaml
    from new_trade_review import infer_result_category, validate_review_labels
    from risk_check import as_float, load_yaml, value_at


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def build_output_path(base_dir: Path, review_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{review_id}.yaml"


def detach_yaml_aliases(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False))


def first_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def create_trade_review_from_exit_execution(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    profile = load_yaml(Path(args.profile))
    template = load_yaml(Path(args.template))
    exit_execution = load_yaml(Path(args.exit_execution))
    review = deepcopy(template)
    created_at, stamp = now_stamp()
    review_id = args.id or f"TR-{stamp}"

    entry_price = as_float(first_value(value_at(exit_execution, "result_estimate.entry_price"), value_at(exit_execution, "exit_plan_snapshot.position_snapshot.entry_price")))
    exit_price = as_float(value_at(exit_execution, "order.execution_price"))
    position_pct = as_float(value_at(exit_execution, "order.exited_position_pct_of_total_assets"))
    trade_return_pct = as_float(value_at(exit_execution, "result_estimate.trade_return_pct"))
    portfolio_return_pct = as_float(value_at(exit_execution, "result_estimate.portfolio_return_pct"))
    followed_plan = bool(value_at(exit_execution, "exit_snapshot.matched_original_plan"))

    if entry_price is None or entry_price <= 0:
        raise ValueError("entry price must be greater than 0")
    if exit_price is None or exit_price <= 0:
        raise ValueError("exit price must be greater than 0")
    if position_pct is None or position_pct <= 0:
        raise ValueError("exited position percent must be greater than 0")
    if trade_return_pct is None:
        trade_return_pct = round((exit_price - entry_price) / entry_price * 100, 4)
    if portfolio_return_pct is None:
        portfolio_return_pct = round(position_pct * trade_return_pct / 100, 4)

    result_category = args.result_category or infer_result_category(trade_return_pct, followed_plan)
    error_tags = args.error_tag or []
    validate_review_labels(profile, result_category, error_tags)

    set_value(review, "review.id", review_id)
    set_value(review, "review.status", "draft")
    set_value(review, "review.created_at", created_at)
    set_value(review, "review.source_trade_plan_id", value_at(exit_execution, "execution.source_trade_plan_id"))
    set_value(review, "review.source_position_id", value_at(exit_execution, "execution.source_position_id"))
    set_value(review, "review.source_exit_plan_id", value_at(exit_execution, "execution.source_exit_plan_id"))
    set_value(review, "review.source_exit_execution_id", value_at(exit_execution, "execution.id"))

    set_value(review, "stock.code", value_at(exit_execution, "stock.code"))
    set_value(review, "stock.name", value_at(exit_execution, "stock.name"))
    set_value(review, "stock.exchange", value_at(exit_execution, "stock.exchange"))
    set_value(review, "stock.industry", value_at(exit_execution, "stock.industry"))

    set_value(review, "execution.entry_date", value_at(exit_execution, "exit_plan_snapshot.position_full_snapshot.entry.entry_date"))
    set_value(review, "execution.exit_date", value_at(exit_execution, "order.execution_date"))
    set_value(review, "execution.entry_price", entry_price)
    set_value(review, "execution.exit_price", exit_price)
    set_value(review, "execution.position_pct_of_total_assets", position_pct)
    set_value(review, "execution.exit_reason", value_at(exit_execution, "exit_snapshot.exit_reason"))
    set_value(review, "execution.followed_plan", followed_plan)

    set_value(review, "result.trade_return_pct", round(trade_return_pct, 4))
    set_value(review, "result.portfolio_return_pct", round(portfolio_return_pct, 4))
    set_value(review, "result.result_category", result_category)
    set_value(review, "result.error_tags", error_tags)

    if args.lesson:
        set_value(review, "review_questions.lesson", args.lesson)
    if args.next_action:
        set_value(review, "review_questions.next_action", args.next_action)

    trade_plan_snapshot = first_value(
        value_at(exit_execution, "exit_plan_snapshot.position_full_snapshot.trade_plan_snapshot"),
        value_at(exit_execution, "trade_plan_snapshot"),
        {},
    )
    set_value(review, "trade_plan_snapshot", trade_plan_snapshot)
    set_value(review, "exit_plan_snapshot", value_at(exit_execution, "exit_plan_snapshot") or {})
    set_value(review, "exit_execution_snapshot", exit_execution)

    output_path = build_output_path(Path(args.output_dir), review_id, args.output)
    return detach_yaml_aliases(review), output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a trade review draft from a sell execution record.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--template", default="templates/trade-review.example.yaml", help="Path to trade review template YAML.")
    parser.add_argument("--exit-execution", required=True, help="Path to sell execution YAML.")
    parser.add_argument("--output-dir", default="reviews", help="Directory for generated trade reviews.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit review id. Defaults to TR-YYYYMMDD-HHMMSS.")
    parser.add_argument("--result-category", help="Review result category. Defaults to inferred category.")
    parser.add_argument("--error-tag", action="append", default=[], help="Execution or strategy error tag. Can be repeated.")
    parser.add_argument("--lesson", help="Main lesson from the trade.")
    parser.add_argument("--next-action", help="Next action after review.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        review, output_path = create_trade_review_from_exit_execution(args)
        write_yaml(output_path, review, overwrite=args.overwrite)
    except Exception as exc:
        print(f"create trade review from exit execution failed: {exc}", file=sys.stderr)
        return 2

    print(f"created trade review: {output_path}")
    print(f"result category: {review['result']['result_category']}")
    print(f"trade return pct: {review['result']['trade_return_pct']}")
    print(f"portfolio return pct: {review['result']['portfolio_return_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
