#!/usr/bin/env python3
"""Update one position with daily price and run holding checks."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.new_position import calculate_return
    from tools.new_trade_plan import set_value, write_yaml
    from tools.position_check import validate_position
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from new_position import calculate_return
    from new_trade_plan import set_value, write_yaml
    from position_check import validate_position
    from risk_check import as_float, load_yaml, value_at


def update_position_tracking(position: dict[str, Any], current_price: float, days_held: int | None, notes: list[str]) -> dict[str, Any]:
    entry_price = as_float(value_at(position, "entry.entry_price"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"))
    if entry_price is None or entry_price <= 0:
        raise ValueError("entry price must be greater than 0")
    if position_pct is None:
        raise ValueError("position percent is required")
    if current_price <= 0:
        raise ValueError("current price must be greater than 0")

    current_return_pct, current_portfolio_return_pct = calculate_return(entry_price, current_price, position_pct)
    set_value(position, "tracking.current_price", current_price)
    set_value(position, "tracking.current_return_pct", current_return_pct)
    set_value(position, "tracking.current_portfolio_return_pct", current_portfolio_return_pct)
    if days_held is not None:
        set_value(position, "tracking.days_held", days_held)

    existing_notes = value_at(position, "tracking.notes") or []
    existing_notes = existing_notes if isinstance(existing_notes, list) else [str(existing_notes)]
    dated_notes = [f"{datetime.now().date().isoformat()} {note}" for note in notes]
    set_value(position, "tracking.notes", existing_notes + dated_notes)
    return position


def build_daily_check(position_path: Path, position: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "position": str(position_path),
        "position_id": value_at(position, "position.id"),
        "stock_code": value_at(position, "stock.code"),
        "stock_name": value_at(position, "stock.name"),
        "check": check,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_update(
    profile_path: Path,
    position_path: Path,
    current_price: float,
    output_path: Path | None,
    check_output_path: Path | None,
    days_held: int | None = None,
    notes: list[str] | None = None,
    near_stop_pct: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    position = load_yaml(position_path)
    updated = update_position_tracking(position, current_price, days_held, notes or [])
    check = validate_position(profile, updated, near_stop_pct=near_stop_pct)

    final_position_path = output_path or position_path
    write_yaml(final_position_path, updated, overwrite=overwrite or final_position_path == position_path)
    daily_check = build_daily_check(final_position_path, updated, check)
    if check_output_path:
        write_json(check_output_path, daily_check)
    return daily_check


def print_summary(result: dict[str, Any]) -> None:
    check = result["check"]
    print(f"position: {result['position_id'] or '-'}")
    print(f"stock: {result['stock_code'] or '-'} {result['stock_name'] or '-'}")
    print(f"conclusion: {check['conclusion']}")
    calculations = check["calculations"]
    print(f"current price: {calculations.get('current_price')}")
    print(f"current return pct: {calculations.get('current_return_pct')}")
    print(f"distance to stop pct: {calculations.get('distance_to_stop_pct')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update one position with daily price and run holding checks.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--position", required=True, help="Path to position YAML.")
    parser.add_argument("--current-price", type=float, required=True, help="Latest current price.")
    parser.add_argument("--days-held", type=int, help="Update days held.")
    parser.add_argument("--note", action="append", default=[], help="Tracking note. Can be repeated.")
    parser.add_argument("--near-stop-pct", type=float, help="Warn when current price is within this percent above stop loss. Defaults to risk.near_stop_warning_pct.")
    parser.add_argument("--output", help="Output position YAML. Defaults to updating --position in place.")
    parser.add_argument("--check-output", help="Optional daily check JSON output.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists.")
    parser.add_argument("--json", action="store_true", help="Print daily check result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_update(
            Path(args.profile),
            Path(args.position),
            args.current_price,
            Path(args.output) if args.output else None,
            Path(args.check_output) if args.check_output else None,
            days_held=args.days_held,
            notes=args.note,
            near_stop_pct=args.near_stop_pct,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        print(f"position daily update failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)
    return 1 if result["check"]["conclusion"] == "needs_action" else 0


if __name__ == "__main__":
    raise SystemExit(main())
