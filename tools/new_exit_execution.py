#!/usr/bin/env python3
"""Create a sell execution record from a checked exit plan."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_exit_plan import run_check
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_exit_plan import run_check
    from new_trade_plan import set_value, write_yaml
    from risk_check import as_float, load_yaml, value_at


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def build_output_path(base_dir: Path, execution_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{execution_id}.yaml"


def load_exit_check(exit_plan_path: Path, check_path: Path | None) -> dict[str, Any]:
    if check_path:
        data = json.loads(check_path.read_text(encoding="utf-8"))
        return data.get("exit_check", data)
    return run_check(exit_plan_path)


def detach_yaml_aliases(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False))


def validate_execution_allowed(check: dict[str, Any], user_confirmed: bool, mode: str) -> None:
    conclusion = check.get("conclusion")
    if conclusion == "blocked":
        raise ValueError("exit plan check conclusion 'blocked' does not allow sell execution")
    if conclusion == "needs_review" and not user_confirmed:
        raise ValueError("exit plan check needs review; pass --user-confirmed after manual confirmation")
    if conclusion not in {"pass", "needs_review"}:
        raise ValueError(f"unknown exit plan check conclusion {conclusion!r}")
    if mode == "real" and not user_confirmed:
        raise ValueError("real sell execution requires --user-confirmed")


def calculate_pct_change(exit_price: float, base_price: float | None) -> float | None:
    if not base_price:
        return None
    return round((exit_price - base_price) / base_price * 100, 4)


def create_exit_execution(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    template = load_yaml(Path(args.template))
    exit_plan_path = Path(args.exit_plan)
    exit_plan = load_yaml(exit_plan_path)
    exit_check = load_exit_check(exit_plan_path, Path(args.check) if args.check else None)
    validate_execution_allowed(exit_check, args.user_confirmed, args.mode)

    execution = deepcopy(template)
    created_at, stamp = now_stamp()
    execution_id = args.id or f"EXITEXEC-{stamp}"
    execution_price = as_float(args.execution_price)
    if execution_price is None or execution_price <= 0:
        raise ValueError("execution price must be greater than 0")

    planned_exit_price = as_float(value_at(exit_plan, "decision.planned_exit_price"))
    min_acceptable_exit_price = as_float(value_at(exit_plan, "decision.min_acceptable_exit_price"))
    entry_price = as_float(value_at(exit_plan, "position_snapshot.entry_price"))
    exited_position_pct = as_float(args.position_pct, as_float(value_at(exit_plan, "decision.exit_position_pct")))
    if exited_position_pct is None or exited_position_pct <= 0:
        raise ValueError("exited position percent must be greater than 0")

    price_above_min = min_acceptable_exit_price is None or execution_price >= min_acceptable_exit_price
    if not price_above_min and not args.allow_below_min_price:
        raise ValueError("execution price is below min acceptable exit price; pass --allow-below-min-price to record the deviation")

    trade_return_pct = calculate_pct_change(execution_price, entry_price)
    portfolio_return_pct = round(exited_position_pct * trade_return_pct / 100, 4) if trade_return_pct is not None else None

    set_value(execution, "execution.id", execution_id)
    set_value(execution, "execution.status", args.status)
    set_value(execution, "execution.created_at", created_at)
    set_value(execution, "execution.mode", args.mode)
    set_value(execution, "execution.side", "sell")
    set_value(execution, "execution.source_exit_plan_id", value_at(exit_plan, "exit_plan.id"))
    set_value(execution, "execution.source_position_id", value_at(exit_plan, "exit_plan.source_position_id"))
    set_value(execution, "execution.source_trade_plan_id", value_at(exit_plan, "exit_plan.source_trade_plan_id"))
    set_value(execution, "execution.exit_check_conclusion", exit_check.get("conclusion"))
    set_value(execution, "execution.user_confirmed", args.user_confirmed)
    set_value(execution, "execution.confirmation_text", args.confirmation_text or value_at(template, "execution.confirmation_text"))

    set_value(execution, "stock.code", value_at(exit_plan, "stock.code"))
    set_value(execution, "stock.name", value_at(exit_plan, "stock.name"))
    set_value(execution, "stock.exchange", value_at(exit_plan, "stock.exchange"))
    set_value(execution, "stock.industry", value_at(exit_plan, "stock.industry"))

    set_value(execution, "order.execution_date", args.execution_date)
    set_value(execution, "order.execution_price", execution_price)
    set_value(execution, "order.shares", args.shares)
    set_value(execution, "order.exited_position_pct_of_total_assets", exited_position_pct)
    set_value(execution, "order.fees", args.fees)
    set_value(execution, "order.slippage_pct_vs_plan", calculate_pct_change(execution_price, planned_exit_price))
    set_value(execution, "order.price_above_min_acceptable", price_above_min)

    set_value(execution, "exit_snapshot.exit_type", value_at(exit_plan, "exit_plan.exit_type"))
    set_value(execution, "exit_snapshot.urgency", value_at(exit_plan, "exit_plan.urgency"))
    set_value(execution, "exit_snapshot.planned_exit_price", planned_exit_price)
    set_value(execution, "exit_snapshot.min_acceptable_exit_price", min_acceptable_exit_price)
    set_value(execution, "exit_snapshot.must_exit", value_at(exit_plan, "decision.must_exit"))
    set_value(execution, "exit_snapshot.exit_reason", value_at(exit_plan, "decision.exit_reason"))
    set_value(execution, "exit_snapshot.matched_original_plan", value_at(exit_plan, "checks.matched_original_plan"))
    set_value(execution, "exit_snapshot.daily_check_conclusion", value_at(exit_plan, "checks.daily_check_conclusion"))
    set_value(execution, "exit_snapshot.source_action_codes", value_at(exit_plan, "checks.source_action_codes") or [])

    set_value(execution, "result_estimate.entry_price", entry_price)
    set_value(execution, "result_estimate.trade_return_pct", trade_return_pct)
    set_value(execution, "result_estimate.portfolio_return_pct", portfolio_return_pct)
    set_value(execution, "notes", args.note or [])
    set_value(
        execution,
        "strategy_config_snapshot",
        value_at(exit_plan, "strategy_config_snapshot")
        or value_at(exit_plan, "position_full_snapshot.strategy_config_snapshot")
        or value_at(exit_plan, "position_full_snapshot.trade_plan_snapshot.strategy_config_snapshot")
        or {},
    )
    set_value(execution, "exit_check_snapshot", exit_check)
    set_value(execution, "exit_plan_snapshot", exit_plan)

    output_path = build_output_path(Path(args.output_dir), execution_id, args.output)
    return detach_yaml_aliases(execution), output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a sell execution record from a checked exit plan.")
    parser.add_argument("--template", default="templates/exit-execution.example.yaml", help="Path to sell execution template YAML.")
    parser.add_argument("--exit-plan", required=True, help="Path to exit plan YAML.")
    parser.add_argument("--check", help="Optional exit check JSON from check_exit_plan.py.")
    parser.add_argument("--output-dir", default="exit-executions", help="Directory for generated sell execution records.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit sell execution id. Defaults to EXITEXEC-YYYYMMDD-HHMMSS.")
    parser.add_argument("--status", default="recorded", help="Execution status.")
    parser.add_argument("--mode", choices=["paper", "real"], default="paper", help="Execution mode.")
    parser.add_argument("--execution-date", required=True, help="Execution date.")
    parser.add_argument("--execution-price", type=float, required=True, help="Actual sell price.")
    parser.add_argument("--shares", type=float, help="Sold shares if known.")
    parser.add_argument("--position-pct", type=float, help="Sold position percent. Defaults to exit plan percent.")
    parser.add_argument("--fees", type=float, default=0.0, help="Execution fees.")
    parser.add_argument("--user-confirmed", action="store_true", help="User has manually confirmed the exit plan/check.")
    parser.add_argument("--allow-below-min-price", action="store_true", help="Record sell execution below min acceptable price.")
    parser.add_argument("--confirmation-text", help="Manual confirmation text.")
    parser.add_argument("--note", action="append", default=[], help="Execution note. Can be repeated.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        execution, output_path = create_exit_execution(args)
        write_yaml(output_path, execution, overwrite=args.overwrite)
    except Exception as exc:
        print(f"create exit execution failed: {exc}", file=sys.stderr)
        return 2

    print(f"created exit execution: {output_path}")
    print(f"exit check conclusion: {execution['execution']['exit_check_conclusion']}")
    print(f"trade return pct: {execution['result_estimate']['trade_return_pct']}")
    print(f"portfolio return pct: {execution['result_estimate']['portfolio_return_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
