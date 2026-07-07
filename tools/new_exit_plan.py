#!/usr/bin/env python3
"""Create an exit plan draft from a position and optional daily check."""

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
    from tools.position_check import validate_position
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from new_trade_plan import set_value, write_yaml
    from position_check import validate_position
    from risk_check import as_float, load_yaml, value_at


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def build_output_path(base_dir: Path, exit_plan_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{exit_plan_id}.yaml"


def load_daily_check(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def detach_yaml_aliases(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False))


def infer_exit_type(position: dict[str, Any], daily_check: dict[str, Any] | None, explicit_exit_type: str | None) -> str:
    if explicit_exit_type:
        return explicit_exit_type
    action_codes = set(source_action_codes(daily_check))
    if "stop_loss_triggered" in action_codes:
        return "stop_loss"
    current_price = as_float(value_at(position, "tracking.current_price"))
    entry_price = as_float(value_at(position, "entry.entry_price"))
    if current_price is not None and entry_price is not None and current_price > entry_price:
        return "take_profit"
    return "thesis_invalidated"


def infer_urgency(exit_type: str, daily_check: dict[str, Any] | None) -> str:
    conclusion = value_at(daily_check or {}, "check.conclusion")
    if exit_type == "stop_loss" or conclusion == "needs_action":
        return "immediate"
    if conclusion == "warning":
        return "soon"
    return "normal"


def source_action_codes(daily_check: dict[str, Any] | None) -> list[str]:
    if not daily_check:
        return []
    check = daily_check.get("check", {})
    codes: list[str] = []
    for key in ("actions", "warnings"):
        for item in check.get(key, []) or []:
            code = item.get("code")
            if code:
                codes.append(code)
    return codes


def default_evidence(position: dict[str, Any], daily_check: dict[str, Any] | None, exit_type: str) -> list[str]:
    evidence: list[str] = []
    if daily_check:
        check = daily_check.get("check", {})
        for key in ("actions", "warnings", "info"):
            for item in check.get(key, []) or []:
                evidence.append(f"[daily_check:{item.get('code')}] {item.get('message')}")
    if not evidence:
        evidence.append(f"退出类型：{exit_type}。")
    current_return = value_at(position, "tracking.current_return_pct")
    if current_return is not None:
        evidence.append(f"当前收益率：{current_return}%。")
    return evidence


def create_exit_plan(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    template = load_yaml(Path(args.template))
    position = load_yaml(Path(args.position))
    daily_check = load_daily_check(Path(args.daily_check) if args.daily_check else None)
    if daily_check is None and args.profile:
        profile = load_yaml(Path(args.profile))
        daily_check = {"check": validate_position(profile, position, near_stop_pct=args.near_stop_pct)}

    exit_plan = deepcopy(template)
    created_at, stamp = now_stamp()
    exit_plan_id = args.id or f"EXIT-{stamp}"
    exit_type = infer_exit_type(position, daily_check, args.exit_type)
    urgency = args.urgency or infer_urgency(exit_type, daily_check)
    current_price = as_float(value_at(position, "tracking.current_price"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"))
    exit_position_pct = as_float(args.exit_position_pct, position_pct)

    set_value(exit_plan, "exit_plan.id", exit_plan_id)
    set_value(exit_plan, "exit_plan.status", "draft")
    set_value(exit_plan, "exit_plan.created_at", created_at)
    set_value(exit_plan, "exit_plan.source_position_id", value_at(position, "position.id"))
    set_value(exit_plan, "exit_plan.source_trade_plan_id", value_at(position, "position.source_trade_plan_id"))
    set_value(exit_plan, "exit_plan.exit_type", exit_type)
    set_value(exit_plan, "exit_plan.urgency", urgency)

    set_value(exit_plan, "stock.code", value_at(position, "stock.code"))
    set_value(exit_plan, "stock.name", value_at(position, "stock.name"))
    set_value(exit_plan, "stock.exchange", value_at(position, "stock.exchange"))
    set_value(exit_plan, "stock.industry", value_at(position, "stock.industry"))

    set_value(exit_plan, "position_snapshot.entry_price", value_at(position, "entry.entry_price"))
    set_value(exit_plan, "position_snapshot.current_price", current_price)
    set_value(exit_plan, "position_snapshot.position_pct_of_total_assets", position_pct)
    set_value(exit_plan, "position_snapshot.current_return_pct", value_at(position, "tracking.current_return_pct"))
    set_value(exit_plan, "position_snapshot.current_portfolio_return_pct", value_at(position, "tracking.current_portfolio_return_pct"))
    set_value(exit_plan, "position_snapshot.stop_loss_price", value_at(position, "risk.stop_loss_price"))

    set_value(exit_plan, "decision.exit_reason", args.exit_reason or f"根据 {exit_type} 规则生成退出计划。")
    set_value(exit_plan, "decision.evidence", args.evidence or default_evidence(position, daily_check, exit_type))
    set_value(exit_plan, "decision.risks_if_hold", args.risk_if_hold or ["继续持有可能扩大回撤或偏离原交易计划。"])
    set_value(exit_plan, "decision.planned_exit_price", as_float(args.planned_exit_price, current_price))
    set_value(exit_plan, "decision.exit_position_pct", exit_position_pct)
    set_value(exit_plan, "decision.min_acceptable_exit_price", args.min_acceptable_exit_price)
    set_value(exit_plan, "decision.must_exit", bool(args.must_exit or exit_type == "stop_loss"))

    set_value(exit_plan, "checks.matched_original_plan", args.matched_original_plan)
    set_value(exit_plan, "checks.triggered_by_daily_check", daily_check is not None)
    set_value(exit_plan, "checks.daily_check_conclusion", value_at(daily_check or {}, "check.conclusion"))
    set_value(exit_plan, "checks.source_action_codes", source_action_codes(daily_check))

    set_value(exit_plan, "next_steps.execution_note", args.execution_note or "")
    set_value(exit_plan, "next_steps.review_required_after_exit", True)
    set_value(exit_plan, "position_full_snapshot", position)
    set_value(exit_plan, "daily_check_snapshot", daily_check or {})

    output_path = build_output_path(Path(args.output_dir), exit_plan_id, args.output)
    return detach_yaml_aliases(exit_plan), output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an exit plan draft from a position and optional daily check.")
    parser.add_argument("--template", default="templates/exit-plan.example.yaml", help="Path to exit plan template YAML.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--position", required=True, help="Path to position YAML.")
    parser.add_argument("--daily-check", help="Optional daily check JSON from update_position_daily.py.")
    parser.add_argument("--output-dir", default="exit-plans", help="Directory for generated exit plans.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit exit plan id. Defaults to EXIT-YYYYMMDD-HHMMSS.")
    parser.add_argument("--near-stop-pct", type=float, default=3.0, help="Near-stop threshold when no daily check JSON is provided.")

    parser.add_argument("--exit-type", choices=["stop_loss", "take_profit", "thesis_invalidated", "risk_reduction"], help="Exit type.")
    parser.add_argument("--urgency", choices=["normal", "soon", "immediate"], help="Exit urgency.")
    parser.add_argument("--exit-reason", help="Exit reason.")
    parser.add_argument("--evidence", action="append", default=[], help="Exit evidence. Can be repeated.")
    parser.add_argument("--risk-if-hold", action="append", default=[], help="Risk if continuing to hold. Can be repeated.")
    parser.add_argument("--planned-exit-price", type=float, help="Planned exit price. Defaults to current price.")
    parser.add_argument("--exit-position-pct", type=float, help="Position percent to exit. Defaults to current position percent.")
    parser.add_argument("--min-acceptable-exit-price", type=float, help="Minimum acceptable exit price.")
    parser.add_argument("--must-exit", action="store_true", help="Mark exit as mandatory.")
    parser.add_argument("--matched-original-plan", action=argparse.BooleanOptionalAction, default=True, help="Whether exit matches original plan.")
    parser.add_argument("--execution-note", help="Execution note for the eventual sell order.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        exit_plan, output_path = create_exit_plan(args)
        write_yaml(output_path, exit_plan, overwrite=args.overwrite)
    except Exception as exc:
        print(f"create exit plan failed: {exc}", file=sys.stderr)
        return 2

    print(f"created exit plan: {output_path}")
    print(f"exit type: {exit_plan['exit_plan']['exit_type']}")
    print(f"urgency: {exit_plan['exit_plan']['urgency']}")
    print(f"must exit: {exit_plan['decision']['must_exit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
