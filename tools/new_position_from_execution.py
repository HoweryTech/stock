#!/usr/bin/env python3
"""Create a position YAML from a trade execution record."""

from __future__ import annotations

import argparse
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

try:
    from tools.new_position import create_position
    from tools.new_trade_plan import write_yaml
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from new_position import create_position
    from new_trade_plan import write_yaml
    from risk_check import load_yaml, value_at


def extract_plan_from_execution(execution: dict[str, Any]) -> dict[str, Any]:
    plan = execution.get("trade_plan_snapshot")
    if not isinstance(plan, dict) or not plan:
        raise ValueError("execution record is missing trade_plan_snapshot")
    return plan


def create_position_from_execution(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    execution = load_yaml(Path(args.execution))
    plan = extract_plan_from_execution(execution)
    execution_id = value_at(execution, "execution.id")

    temp_plan_path = Path(args.temp_plan_path) if args.temp_plan_path else None
    if temp_plan_path is None:
        temp_plan_path = Path(args.output or Path(args.output_dir) / f"{args.id or 'POS-FROM-EXECUTION'}.yaml").with_suffix(".plan.tmp.yaml")
    write_yaml(temp_plan_path, plan, overwrite=True)

    position_args = Namespace(
        template=args.template,
        plan=str(temp_plan_path),
        output_dir=args.output_dir,
        output=args.output,
        overwrite=args.overwrite,
        id=args.id,
        status=args.status,
        entry_date=args.entry_date or value_at(execution, "order.execution_date"),
        entry_price=args.entry_price if args.entry_price is not None else value_at(execution, "order.execution_price"),
        current_price=args.current_price if args.current_price is not None else value_at(execution, "order.execution_price"),
        position_pct=args.position_pct if args.position_pct is not None else value_at(execution, "order.position_pct_of_total_assets"),
        shares=args.shares if args.shares is not None else value_at(execution, "order.shares"),
        stop_loss_price=args.stop_loss_price if args.stop_loss_price is not None else value_at(execution, "risk_snapshot.stop_loss_price"),
        days_held=args.days_held,
        note=(args.note or []) + [f"来源执行记录：{execution_id}。"],
    )
    position, output_path = create_position(position_args)
    position["execution_snapshot"] = execution
    if not args.keep_temp_plan and temp_plan_path.exists():
        temp_plan_path.unlink()
    return position, output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a position YAML from a trade execution record.")
    parser.add_argument("--execution", required=True, help="Path to trade execution YAML.")
    parser.add_argument("--template", default="templates/position.example.yaml", help="Path to position template YAML.")
    parser.add_argument("--output-dir", default="positions", help="Directory for generated positions.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit position id. Defaults to POS-YYYYMMDD-HHMMSS.")
    parser.add_argument("--status", default="normal", help="Initial position status.")
    parser.add_argument("--temp-plan-path", help="Optional temp path for extracted plan snapshot.")
    parser.add_argument("--keep-temp-plan", action="store_true", help="Keep extracted temp plan YAML for debugging.")

    parser.add_argument("--entry-date", help="Override entry date. Defaults to execution date.")
    parser.add_argument("--entry-price", type=float, help="Override entry price. Defaults to execution price.")
    parser.add_argument("--current-price", type=float, help="Current price. Defaults to execution price.")
    parser.add_argument("--position-pct", type=float, help="Override position percent. Defaults to execution position percent.")
    parser.add_argument("--shares", type=float, help="Override shares. Defaults to execution shares.")
    parser.add_argument("--stop-loss-price", type=float, help="Override stop loss price. Defaults to execution risk snapshot.")
    parser.add_argument("--days-held", type=int, default=0, help="Days held when creating the position.")
    parser.add_argument("--note", action="append", default=[], help="Initial tracking note. Can be repeated.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        position, output_path = create_position_from_execution(args)
        write_yaml(output_path, position, overwrite=args.overwrite)
    except Exception as exc:
        print(f"create position from execution failed: {exc}", file=sys.stderr)
        return 2

    print(f"created position: {output_path}")
    print(f"source execution: {position['execution_snapshot']['execution']['id']}")
    print(f"current return pct: {position['tracking']['current_return_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
