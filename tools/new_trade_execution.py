#!/usr/bin/env python3
"""Create a trade execution record from a gated trade plan."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_trade_plan_gate import run_gate
    from tools.new_trade_plan import load_strategy_config_snapshot, set_value, write_yaml
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_trade_plan_gate import run_gate
    from new_trade_plan import load_strategy_config_snapshot, set_value, write_yaml
    from risk_check import as_float, load_yaml, value_at


ALLOWED_GATE_CONCLUSIONS = {"pass", "needs_confirmation"}


def now_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d-%H%M%S")


def build_output_path(base_dir: Path, execution_id: str, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    return base_dir / f"{execution_id}.yaml"


def load_gate_result(profile_path: Path, plan_path: Path, gate_path: Path | None) -> dict[str, Any]:
    if gate_path:
        data = json.loads(gate_path.read_text(encoding="utf-8"))
        return data.get("gate", data)
    return run_gate(profile_path, plan_path)


def load_cooldown_result(cooldown_path: Path | None) -> dict[str, Any]:
    if cooldown_path is None:
        return {"available": False, "conclusion": "missing"}
    if not cooldown_path.exists():
        return {"available": False, "conclusion": "missing", "path": str(cooldown_path)}
    data = json.loads(cooldown_path.read_text(encoding="utf-8"))
    data["available"] = True
    return data


def load_strategy_health(health_path: Path | None) -> dict[str, Any]:
    if health_path is None:
        return {"available": False, "conclusion": "missing", "strategies": []}
    if not health_path.exists():
        return {"available": False, "conclusion": "missing", "path": str(health_path), "strategies": []}
    data = json.loads(health_path.read_text(encoding="utf-8"))
    data["available"] = True
    return data


def strategy_status(health: dict[str, Any], strategy: str | None) -> str | None:
    if not strategy:
        return None
    for item in health.get("strategies", []) or []:
        if item.get("strategy") == strategy:
            return item.get("status")
    return None


def validate_execution_allowed(gate: dict[str, Any], user_confirmed: bool, mode: str) -> None:
    conclusion = gate.get("conclusion")
    if conclusion not in ALLOWED_GATE_CONCLUSIONS:
        raise ValueError(f"trade plan gate conclusion {conclusion!r} does not allow execution")
    if conclusion == "needs_confirmation" and not user_confirmed:
        raise ValueError("gate needs confirmation; pass --user-confirmed after manual confirmation")
    if mode == "real" and not user_confirmed:
        raise ValueError("real execution requires --user-confirmed")


def validate_cooldown_allowed(
    cooldown: dict[str, Any],
    side: str,
    allow_exception: bool,
    exception_reason: str | None,
    user_confirmed: bool,
) -> None:
    if side != "buy" or cooldown.get("conclusion") != "cooldown_required":
        return
    if not allow_exception:
        raise ValueError("review cooldown is required; new buy execution is blocked")
    if not user_confirmed:
        raise ValueError("cooldown exception requires --user-confirmed")
    if not exception_reason or not exception_reason.strip():
        raise ValueError("cooldown exception requires --cooldown-exception-reason")


def validate_strategy_health_allowed(
    health: dict[str, Any],
    strategy: str | None,
    side: str,
    allow_exception: bool,
    exception_reason: str | None,
    user_confirmed: bool,
) -> None:
    if side != "buy" or strategy_status(health, strategy) != "pause_new_entries":
        return
    if not allow_exception:
        raise ValueError(f"strategy {strategy} is paused by strategy health check; new buy execution is blocked")
    if not user_confirmed:
        raise ValueError("strategy health exception requires --user-confirmed")
    if not exception_reason or not exception_reason.strip():
        raise ValueError("strategy health exception requires --cooldown-exception-reason")


def calculate_slippage_pct(execution_price: float, planned_buy_price: float | None) -> float | None:
    if not planned_buy_price:
        return None
    return round((execution_price - planned_buy_price) / planned_buy_price * 100, 4)


def ensure_strategy_config_snapshot(plan: dict[str, Any], snapshot_path: str | None) -> None:
    existing = plan.get("strategy_config_snapshot")
    if isinstance(existing, dict) and existing.get("available"):
        return
    if not snapshot_path:
        return
    set_value(plan, "strategy_config_snapshot", load_strategy_config_snapshot(Path(snapshot_path)))


def create_execution(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    template = load_yaml(Path(args.template))
    plan_path = Path(args.plan)
    profile_path = Path(args.profile)
    plan = load_yaml(plan_path)
    ensure_strategy_config_snapshot(plan, getattr(args, "strategy_config_snapshot", None))
    gate = load_gate_result(profile_path, plan_path, Path(args.gate) if args.gate else None)
    cooldown_check = getattr(args, "cooldown_check", None)
    cooldown = load_cooldown_result(Path(cooldown_check) if cooldown_check else None)
    strategy_health_path = getattr(args, "strategy_health", None)
    strategy_health = load_strategy_health(Path(strategy_health_path) if strategy_health_path else None)
    plan_strategy = value_at(plan, "strategy.source")
    validate_execution_allowed(gate, args.user_confirmed, args.mode)
    validate_cooldown_allowed(
        cooldown,
        args.side,
        bool(getattr(args, "allow_cooldown_exception", False)),
        getattr(args, "cooldown_exception_reason", None),
        args.user_confirmed,
    )
    validate_strategy_health_allowed(
        strategy_health,
        plan_strategy,
        args.side,
        bool(getattr(args, "allow_cooldown_exception", False)),
        getattr(args, "cooldown_exception_reason", None),
        args.user_confirmed,
    )

    execution = deepcopy(template)
    created_at, stamp = now_stamp()
    execution_id = args.id or f"EXEC-{stamp}"
    execution_price = as_float(args.execution_price)
    if execution_price is None or execution_price <= 0:
        raise ValueError("execution price must be greater than 0")

    position_pct = as_float(args.position_pct, as_float(value_at(plan, "position_plan.planned_position_pct_of_total_assets")))
    planned_buy_price = as_float(value_at(plan, "price_plan.planned_buy_price"))
    max_acceptable_buy_price = as_float(value_at(plan, "price_plan.max_acceptable_buy_price"))
    slippage_pct = calculate_slippage_pct(execution_price, planned_buy_price)
    price_within_max = max_acceptable_buy_price is None or execution_price <= max_acceptable_buy_price

    set_value(execution, "execution.id", execution_id)
    set_value(execution, "execution.status", args.status)
    set_value(execution, "execution.created_at", created_at)
    set_value(execution, "execution.mode", args.mode)
    set_value(execution, "execution.source_trade_plan_id", value_at(plan, "trade_plan.id"))
    set_value(execution, "execution.gate_conclusion", gate.get("conclusion"))
    set_value(execution, "execution.cooldown_conclusion", cooldown.get("conclusion"))
    set_value(execution, "execution.strategy_health_conclusion", strategy_health.get("conclusion"))
    set_value(execution, "execution.user_confirmed", args.user_confirmed)
    set_value(execution, "execution.confirmation_text", value_at(plan, "risk_check_expectation.confirmation_text"))
    set_value(execution, "execution.cooldown_exception_reason", getattr(args, "cooldown_exception_reason", None) or "")

    set_value(execution, "stock.code", value_at(plan, "stock.code"))
    set_value(execution, "stock.name", value_at(plan, "stock.name"))
    set_value(execution, "stock.exchange", value_at(plan, "stock.exchange"))
    set_value(execution, "stock.industry", value_at(plan, "stock.industry"))

    set_value(execution, "order.side", args.side)
    set_value(execution, "order.execution_date", args.execution_date)
    set_value(execution, "order.execution_price", execution_price)
    set_value(execution, "order.shares", args.shares)
    set_value(execution, "order.position_pct_of_total_assets", position_pct)
    set_value(execution, "order.fees", args.fees)
    set_value(execution, "order.slippage_pct_vs_plan", slippage_pct)
    set_value(execution, "order.price_within_max_acceptable", price_within_max)

    set_value(execution, "risk_snapshot.planned_buy_price", planned_buy_price)
    set_value(execution, "risk_snapshot.max_acceptable_buy_price", max_acceptable_buy_price)
    set_value(execution, "risk_snapshot.stop_loss_price", value_at(plan, "price_plan.stop_loss_price"))
    set_value(execution, "risk_snapshot.max_loss_pct_of_total_assets", value_at(plan, "risk_calculation.max_loss_pct_of_total_assets"))

    set_value(execution, "notes", args.note or [])
    set_value(execution, "gate_snapshot", gate)
    set_value(execution, "cooldown_snapshot", cooldown)
    set_value(execution, "strategy_health_snapshot", strategy_health)
    set_value(execution, "trade_plan_snapshot", plan)

    output_path = build_output_path(Path(args.output_dir), execution_id, args.output)
    return execution, output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a trade execution record from a gated trade plan.")
    parser.add_argument("--template", default="templates/trade-execution.example.yaml", help="Path to trade execution template YAML.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--strategy-config-snapshot", default="data/metadata/strategy-config-snapshot.json", help="Optional strategy config version snapshot JSON used when the plan has no snapshot.")
    parser.add_argument("--plan", required=True, help="Path to trade plan YAML.")
    parser.add_argument("--gate", help="Optional gate JSON from check_trade_plan_gate.py or prepare_trade_plan_from_candidate.py.")
    parser.add_argument("--cooldown-check", default="data/metadata/review-cooldown.json", help="Optional cooldown JSON from check_review_cooldown.py.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Optional strategy health JSON from check_strategy_health.py.")
    parser.add_argument("--output-dir", default="executions", help="Directory for generated execution records.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit execution id. Defaults to EXEC-YYYYMMDD-HHMMSS.")
    parser.add_argument("--status", default="recorded", help="Execution status.")

    parser.add_argument("--mode", choices=["paper", "real"], default="paper", help="Execution mode.")
    parser.add_argument("--side", choices=["buy", "sell"], default="buy", help="Order side.")
    parser.add_argument("--execution-date", required=True, help="Execution date.")
    parser.add_argument("--execution-price", type=float, required=True, help="Actual execution price.")
    parser.add_argument("--shares", type=float, help="Executed shares if known.")
    parser.add_argument("--position-pct", type=float, help="Actual position percent. Defaults to planned position percent.")
    parser.add_argument("--fees", type=float, default=0.0, help="Execution fees.")
    parser.add_argument("--user-confirmed", action="store_true", help="User has manually confirmed the gated trade plan.")
    parser.add_argument("--allow-cooldown-exception", action="store_true", help="Allow buy execution during review cooldown.")
    parser.add_argument("--cooldown-exception-reason", help="Required reason when allowing a cooldown exception.")
    parser.add_argument("--note", action="append", default=[], help="Execution note. Can be repeated.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        execution, output_path = create_execution(args)
        write_yaml(output_path, execution, overwrite=args.overwrite)
    except Exception as exc:
        print(f"create trade execution failed: {exc}", file=sys.stderr)
        return 2

    print(f"created trade execution: {output_path}")
    print(f"gate conclusion: {execution['execution']['gate_conclusion']}")
    print(f"cooldown conclusion: {execution['execution']['cooldown_conclusion']}")
    print(f"strategy health conclusion: {execution['execution']['strategy_health_conclusion']}")
    print(f"slippage pct vs plan: {execution['order']['slippage_pct_vs_plan']}")
    print(f"price within max acceptable: {execution['order']['price_within_max_acceptable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
