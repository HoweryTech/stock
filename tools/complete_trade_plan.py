#!/usr/bin/env python3
"""Complete editable fields in an existing draft trade plan."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from tools.new_trade_plan import calculate_derived_fields, set_value, write_yaml
    from tools.risk_check import is_missing, load_yaml, value_at
except ModuleNotFoundError:
    from new_trade_plan import calculate_derived_fields, set_value, write_yaml
    from risk_check import is_missing, load_yaml, value_at


def append_items(existing: Any, items: list[str], *, item_type: str | None = None) -> list[Any]:
    result = list(existing) if isinstance(existing, list) else []
    for item in items:
        result.append({"type": item_type or "manual", "description": item} if item_type is not None else item)
    return result


def replace_or_append(existing: Any, items: list[str], replace: bool, *, item_type: str | None = None) -> list[Any]:
    if replace:
        return append_items([], items, item_type=item_type)
    return append_items(existing, items, item_type=item_type)


def apply_completion(plan: dict[str, Any], profile: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.status:
        set_value(plan, "trade_plan.status", args.status)
    if args.buy_reason:
        set_value(plan, "strategy.buy_reason", args.buy_reason)

    if args.key_evidence:
        set_value(
            plan,
            "strategy.key_evidence",
            replace_or_append(value_at(plan, "strategy.key_evidence"), args.key_evidence, args.replace_evidence, item_type="manual"),
        )
    if args.risk:
        set_value(
            plan,
            "strategy.counter_evidence_and_risks",
            replace_or_append(value_at(plan, "strategy.counter_evidence_and_risks"), args.risk, args.replace_risks, item_type="manual"),
        )
    if args.stop_loss_condition:
        set_value(
            plan,
            "exit_plan.stop_loss_conditions",
            replace_or_append(value_at(plan, "exit_plan.stop_loss_conditions"), args.stop_loss_condition, args.replace_exit_rules),
        )
    if args.take_profit_condition:
        set_value(
            plan,
            "exit_plan.take_profit_conditions",
            replace_or_append(value_at(plan, "exit_plan.take_profit_conditions"), args.take_profit_condition, args.replace_exit_rules),
        )
    if args.invalidation_condition:
        set_value(
            plan,
            "exit_plan.invalidation_conditions",
            replace_or_append(value_at(plan, "exit_plan.invalidation_conditions"), args.invalidation_condition, args.replace_exit_rules),
        )
    if args.observation_item:
        set_value(
            plan,
            "exit_plan.observation_items",
            replace_or_append(value_at(plan, "exit_plan.observation_items"), args.observation_item, args.replace_observation_items),
        )
    if args.review_focus:
        set_value(
            plan,
            "review_seed.review_focus",
            replace_or_append(value_at(plan, "review_seed.review_focus"), args.review_focus, args.replace_review_focus),
        )

    numeric_updates = [
        ("price_plan.planned_buy_price", args.planned_buy_price),
        ("price_plan.current_price", args.current_price),
        ("price_plan.stop_loss_price", args.stop_loss_price),
        ("position_plan.planned_position_pct_of_total_assets", args.position_pct),
        ("position_plan.current_stock_position_pct", args.current_stock_pct),
        ("position_plan.current_industry_position_pct", args.current_industry_pct),
        ("position_plan.current_total_position_pct", args.current_total_pct),
    ]
    for path, value in numeric_updates:
        if value is not None:
            set_value(plan, path, value)

    calculate_derived_fields(profile, plan)
    if args.mark_ready:
        missing_fields = [
            value_at(plan, "exit_plan.stop_loss_conditions"),
            value_at(plan, "exit_plan.take_profit_conditions"),
            value_at(plan, "exit_plan.invalidation_conditions"),
        ]
        if any(is_missing(value) for value in missing_fields):
            raise ValueError("cannot mark ready before stop loss, take profit, and invalidation conditions are completed")
        set_value(plan, "trade_plan.status", "ready_for_gate")
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Complete editable fields in an existing draft trade plan.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--plan", required=True, help="Input trade plan YAML.")
    parser.add_argument("--output", help="Output path. Defaults to updating --plan in place.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists.")
    parser.add_argument("--status", help="Set trade plan status.")
    parser.add_argument("--mark-ready", action="store_true", help="Set status to ready_for_gate after required exit rules are completed.")

    parser.add_argument("--buy-reason", help="Replace buy reason.")
    parser.add_argument("--key-evidence", action="append", default=[], help="Append key evidence. Can be repeated.")
    parser.add_argument("--risk", action="append", default=[], help="Append counter evidence or risk. Can be repeated.")
    parser.add_argument("--stop-loss-condition", action="append", default=[], help="Append stop loss condition. Can be repeated.")
    parser.add_argument("--take-profit-condition", action="append", default=[], help="Append take profit condition. Can be repeated.")
    parser.add_argument("--invalidation-condition", action="append", default=[], help="Append invalidation condition. Can be repeated.")
    parser.add_argument("--observation-item", action="append", default=[], help="Append observation item. Can be repeated.")
    parser.add_argument("--review-focus", action="append", default=[], help="Append review focus. Can be repeated.")

    parser.add_argument("--replace-evidence", action="store_true", help="Replace existing key evidence instead of appending.")
    parser.add_argument("--replace-risks", action="store_true", help="Replace existing risks instead of appending.")
    parser.add_argument("--replace-exit-rules", action="store_true", help="Replace existing exit rules instead of appending.")
    parser.add_argument("--replace-observation-items", action="store_true", help="Replace existing observation items instead of appending.")
    parser.add_argument("--replace-review-focus", action="store_true", help="Replace existing review focus instead of appending.")

    parser.add_argument("--planned-buy-price", type=float, help="Update planned buy price.")
    parser.add_argument("--current-price", type=float, help="Update current price.")
    parser.add_argument("--stop-loss-price", type=float, help="Update stop loss price.")
    parser.add_argument("--position-pct", type=float, help="Update planned position percent.")
    parser.add_argument("--current-stock-pct", type=float, help="Update current stock position percent.")
    parser.add_argument("--current-industry-pct", type=float, help="Update current industry position percent.")
    parser.add_argument("--current-total-pct", type=float, help="Update current total position percent.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile = load_yaml(Path(args.profile))
        plan_path = Path(args.plan)
        plan = load_yaml(plan_path)
        output_path = Path(args.output) if args.output else plan_path
        updated = apply_completion(plan, profile, args)
        write_yaml(output_path, updated, overwrite=args.overwrite or output_path == plan_path)
    except Exception as exc:
        print(f"complete trade plan failed: {exc}", file=sys.stderr)
        return 2

    print(f"completed trade plan: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
