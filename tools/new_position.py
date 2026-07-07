#!/usr/bin/env python3
"""Create a position YAML from an executed trade plan."""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from new_trade_plan import set_value, write_yaml
    from risk_check import as_float, load_yaml, value_at


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def build_output_path(base_dir: Path, position_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{position_id}.yaml"


def calculate_return(entry_price: float, current_price: float, position_pct: float) -> tuple[float, float]:
    current_return_pct = (current_price - entry_price) / entry_price * 100
    current_portfolio_return_pct = position_pct * current_return_pct / 100
    return round(current_return_pct, 4), round(current_portfolio_return_pct, 4)


def create_position(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    template = load_yaml(Path(args.template))
    plan = load_yaml(Path(args.plan))
    position = deepcopy(template)
    created_at, stamp = now_stamp()
    position_id = args.id or f"POS-{stamp}"
    source_trade_plan_id = value_at(plan, "trade_plan.id")

    entry_price = as_float(args.entry_price, as_float(value_at(plan, "price_plan.planned_buy_price")))
    current_price = as_float(args.current_price, entry_price)
    position_pct = as_float(args.position_pct, as_float(value_at(plan, "position_plan.planned_position_pct_of_total_assets")))
    stop_loss_price = as_float(args.stop_loss_price, as_float(value_at(plan, "price_plan.stop_loss_price")))
    max_loss_pct = as_float(value_at(plan, "risk_calculation.max_loss_pct_of_total_assets"))

    if entry_price is None or entry_price <= 0:
        raise ValueError("entry price must be greater than 0")
    if current_price is None:
        raise ValueError("current price is required")
    if position_pct is None:
        raise ValueError("position percent is required")

    current_return_pct, current_portfolio_return_pct = calculate_return(entry_price, current_price, position_pct)

    set_value(position, "position.id", position_id)
    set_value(position, "position.status", args.status)
    set_value(position, "position.created_at", created_at)
    set_value(position, "position.source_trade_plan_id", source_trade_plan_id)

    set_value(position, "stock.code", value_at(plan, "stock.code"))
    set_value(position, "stock.name", value_at(plan, "stock.name"))
    set_value(position, "stock.exchange", value_at(plan, "stock.exchange"))
    set_value(position, "stock.industry", value_at(plan, "stock.industry"))

    set_value(position, "entry.entry_date", args.entry_date)
    set_value(position, "entry.entry_price", entry_price)
    set_value(position, "entry.shares", args.shares)
    set_value(position, "entry.position_pct_of_total_assets", position_pct)
    set_value(position, "entry.planned_buy_price", value_at(plan, "price_plan.planned_buy_price"))
    set_value(position, "entry.max_acceptable_buy_price", value_at(plan, "price_plan.max_acceptable_buy_price"))

    set_value(position, "risk.stop_loss_price", stop_loss_price)
    set_value(position, "risk.max_loss_pct_of_total_assets", max_loss_pct)
    set_value(position, "risk.take_profit_conditions", value_at(plan, "exit_plan.take_profit_conditions") or [])
    set_value(position, "risk.invalidation_conditions", value_at(plan, "exit_plan.invalidation_conditions") or [])
    set_value(position, "risk.observation_items", value_at(plan, "exit_plan.observation_items") or [])

    set_value(position, "strategy.source", value_at(plan, "strategy.source"))
    set_value(position, "strategy.timeframe", value_at(plan, "strategy.timeframe"))
    set_value(position, "strategy.buy_reason", value_at(plan, "strategy.buy_reason"))
    set_value(position, "strategy.key_evidence", value_at(plan, "strategy.key_evidence") or [])
    set_value(position, "strategy.counter_evidence_and_risks", value_at(plan, "strategy.counter_evidence_and_risks") or [])

    set_value(position, "tracking.current_price", current_price)
    set_value(position, "tracking.current_return_pct", current_return_pct)
    set_value(position, "tracking.current_portfolio_return_pct", current_portfolio_return_pct)
    set_value(position, "tracking.days_held", args.days_held)
    set_value(position, "tracking.notes", args.note or [])
    set_value(position, "trade_plan_snapshot", plan)

    output_path = build_output_path(Path(args.output_dir), position_id, args.output)
    return position, output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a position YAML from an executed trade plan.")
    parser.add_argument("--template", default="templates/position.example.yaml", help="Path to position template YAML.")
    parser.add_argument("--plan", required=True, help="Path to executed trade plan YAML.")
    parser.add_argument("--output-dir", default="positions", help="Directory for generated positions.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit position id. Defaults to POS-YYYYMMDD-HHMMSS.")
    parser.add_argument("--status", default="normal", help="Initial position status.")

    parser.add_argument("--entry-date", required=True, help="Entry date.")
    parser.add_argument("--entry-price", type=float, help="Actual entry price. Defaults to planned buy price.")
    parser.add_argument("--current-price", type=float, help="Current price. Defaults to entry price.")
    parser.add_argument("--position-pct", type=float, help="Actual position percent. Defaults to planned position percent.")
    parser.add_argument("--shares", type=float, help="Actual shares if known.")
    parser.add_argument("--stop-loss-price", type=float, help="Actual stop loss price. Defaults to plan stop loss price.")
    parser.add_argument("--days-held", type=int, default=0, help="Days held when creating the position.")
    parser.add_argument("--note", action="append", default=[], help="Initial tracking note. Can be repeated.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    position, output_path = create_position(args)
    write_yaml(output_path, position, overwrite=args.overwrite)
    print(f"created position: {output_path}")
    print(f"status: {position['position']['status']}")
    print(f"current return pct: {position['tracking']['current_return_pct']}")
    print(f"portfolio return pct: {position['tracking']['current_portfolio_return_pct']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"create position failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
