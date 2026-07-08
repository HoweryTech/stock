#!/usr/bin/env python3
"""Create a new trade plan YAML from the project template."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from tools.risk_check import as_float, load_yaml
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml


def set_value(data: dict[str, Any], path: str, value: Any) -> None:
    current: dict[str, Any] = data
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"{path} conflicts with non-object field {part}")
        current = child
    current[parts[-1]] = value


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def build_output_path(base_dir: Path, trade_plan_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{trade_plan_id}.yaml"


def load_strategy_config_snapshot(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"available": False, "path": None, "reason": "not_configured"}
    if not path.exists():
        return {"available": False, "path": str(path), "reason": "missing"}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    data = deepcopy(data)
    data["available"] = True
    data.setdefault("path", str(path))
    return data


def calculate_derived_fields(profile: dict[str, Any], plan: dict[str, Any]) -> None:
    risk = profile.get("risk", {})
    chase_limit_pct = as_float(risk.get("chase_limit_pct_above_plan_price"), 0.0) or 0.0

    planned_buy_price = as_float(plan.get("price_plan", {}).get("planned_buy_price"))
    stop_loss_price = as_float(plan.get("price_plan", {}).get("stop_loss_price"))
    position = plan.get("position_plan", {})
    planned_position_pct = as_float(position.get("planned_position_pct_of_total_assets"))
    current_stock_pct = as_float(position.get("current_stock_position_pct"), 0.0) or 0.0
    current_industry_pct = as_float(position.get("current_industry_position_pct"), 0.0) or 0.0
    current_total_pct = as_float(position.get("current_total_position_pct"), 0.0) or 0.0

    if planned_buy_price is not None:
        plan["price_plan"]["max_acceptable_buy_price"] = round(planned_buy_price * (1 + chase_limit_pct / 100), 4)
        if plan["price_plan"].get("current_price") in (None, ""):
            plan["price_plan"]["current_price"] = planned_buy_price

    if planned_position_pct is not None:
        position["expected_stock_position_pct_after_buy"] = round(current_stock_pct + planned_position_pct, 4)
        position["expected_industry_position_pct_after_buy"] = round(current_industry_pct + planned_position_pct, 4)
        position["expected_total_position_pct_after_buy"] = round(current_total_pct + planned_position_pct, 4)

    if planned_buy_price and stop_loss_price is not None and planned_position_pct is not None:
        max_loss_pct = planned_position_pct * abs(planned_buy_price - stop_loss_price) / planned_buy_price
        plan.setdefault("risk_calculation", {})["max_loss_pct_of_total_assets"] = round(max_loss_pct, 4)


def normalize_draft_fields(plan: dict[str, Any]) -> None:
    set_value(plan, "strategy.buy_reason", "")
    set_value(plan, "strategy.key_evidence", [])
    set_value(plan, "strategy.counter_evidence_and_risks", [])
    set_value(plan, "exit_plan.stop_loss_conditions", [])
    set_value(plan, "exit_plan.take_profit_conditions", [])
    set_value(plan, "exit_plan.invalidation_conditions", [])
    set_value(plan, "exit_plan.observation_items", [])
    set_value(plan, "review_seed.review_focus", [])


def create_trade_plan(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    profile = load_yaml(Path(args.profile))
    template = load_yaml(Path(args.template))
    plan = deepcopy(template)
    created_at, stamp = now_stamp()
    trade_plan_id = args.id or f"TP-{stamp}"
    snapshot_path = getattr(args, "strategy_config_snapshot", None)

    normalize_draft_fields(plan)

    set_value(plan, "trade_plan.id", trade_plan_id)
    set_value(plan, "trade_plan.status", "draft")
    set_value(plan, "trade_plan.created_at", created_at)
    set_value(plan, "stock.code", args.code)
    set_value(plan, "stock.name", args.name)
    set_value(plan, "stock.exchange", args.exchange)
    set_value(plan, "stock.industry", args.industry)
    set_value(plan, "stock.is_st", args.is_st)
    set_value(plan, "stock.is_suspended", args.is_suspended)
    set_value(plan, "stock.has_delisting_risk", args.has_delisting_risk)
    set_value(plan, "stock.abnormal_trading_status", args.abnormal_trading_status)
    set_value(plan, "strategy.source", args.strategy)
    set_value(plan, "strategy.timeframe", args.timeframe)

    if args.buy_reason:
        set_value(plan, "strategy.buy_reason", args.buy_reason)
    if args.key_evidence:
        set_value(plan, "strategy.key_evidence", [{"type": "manual", "description": item} for item in args.key_evidence])
    if args.risk:
        set_value(plan, "strategy.counter_evidence_and_risks", [{"type": "manual", "description": item} for item in args.risk])
    if args.stop_loss_condition:
        set_value(plan, "exit_plan.stop_loss_conditions", args.stop_loss_condition)
    if args.take_profit_condition:
        set_value(plan, "exit_plan.take_profit_conditions", args.take_profit_condition)
    if args.invalidation_condition:
        set_value(plan, "exit_plan.invalidation_conditions", args.invalidation_condition)
    if args.observation_item:
        set_value(plan, "exit_plan.observation_items", args.observation_item)

    set_value(plan, "price_plan.planned_buy_price", args.planned_buy_price)
    set_value(plan, "price_plan.current_price", args.current_price)
    set_value(plan, "price_plan.stop_loss_price", args.stop_loss_price)
    set_value(plan, "position_plan.planned_position_pct_of_total_assets", args.position_pct)
    set_value(plan, "position_plan.current_stock_position_pct", args.current_stock_pct)
    set_value(plan, "position_plan.current_industry_position_pct", args.current_industry_pct)
    set_value(plan, "position_plan.current_total_position_pct", args.current_total_pct)
    set_value(plan, "strategy_config_snapshot", load_strategy_config_snapshot(Path(snapshot_path) if snapshot_path else None))

    calculate_derived_fields(profile, plan)

    output_path = build_output_path(Path(args.output_dir), trade_plan_id, args.output)
    return plan, output_path


def write_yaml(path: Path, data: dict[str, Any], overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a new draft trade plan YAML.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--strategy-config-snapshot", default="data/metadata/strategy-config-snapshot.json", help="Optional strategy config version snapshot JSON.")
    parser.add_argument("--template", default="templates/trade-plan.example.yaml", help="Path to trade plan template YAML.")
    parser.add_argument("--output-dir", default="plans", help="Directory for generated trade plans.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit trade plan id. Defaults to TP-YYYYMMDD-HHMMSS.")

    parser.add_argument("--code", required=True, help="Stock code.")
    parser.add_argument("--name", required=True, help="Stock name.")
    parser.add_argument("--exchange", default="UNKNOWN", help="Exchange, for example SSE or SZSE.")
    parser.add_argument("--industry", default="待确认", help="Industry name.")
    parser.add_argument("--is-st", action="store_true", help="Mark the stock as ST.")
    parser.add_argument("--is-suspended", action="store_true", help="Mark the stock as suspended.")
    parser.add_argument("--has-delisting-risk", action="store_true", help="Mark the stock as having delisting risk.")
    parser.add_argument("--abnormal-trading-status", action="store_true", help="Mark abnormal trading status.")

    parser.add_argument("--strategy", default="trend_strength", help="Strategy source.")
    parser.add_argument("--timeframe", default="swing", help="Strategy timeframe.")
    parser.add_argument("--buy-reason", help="Initial buy reason.")
    parser.add_argument("--key-evidence", action="append", default=[], help="Key evidence. Can be repeated.")
    parser.add_argument("--risk", action="append", default=[], help="Counter evidence or risk. Can be repeated.")
    parser.add_argument("--stop-loss-condition", action="append", default=[], help="Stop loss condition. Can be repeated.")
    parser.add_argument("--take-profit-condition", action="append", default=[], help="Take profit condition. Can be repeated.")
    parser.add_argument("--invalidation-condition", action="append", default=[], help="Invalidation condition. Can be repeated.")
    parser.add_argument("--observation-item", action="append", default=[], help="Holding observation item. Can be repeated.")

    parser.add_argument("--planned-buy-price", type=float, help="Planned buy price.")
    parser.add_argument("--current-price", type=float, help="Current price.")
    parser.add_argument("--stop-loss-price", type=float, help="Stop loss price.")
    parser.add_argument("--position-pct", type=float, help="Planned position as percent of total assets.")
    parser.add_argument("--current-stock-pct", type=float, default=0.0, help="Current stock position percent.")
    parser.add_argument("--current-industry-pct", type=float, default=0.0, help="Current industry position percent.")
    parser.add_argument("--current-total-pct", type=float, default=0.0, help="Current total position percent.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan, output_path = create_trade_plan(args)
    write_yaml(output_path, plan, overwrite=args.overwrite)
    print(f"created trade plan: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"create trade plan failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
