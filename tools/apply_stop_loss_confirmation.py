#!/usr/bin/env python3
"""Apply a user stop-loss confirmation choice to a local position file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.apply_manual_trade import locate_position
    from tools.check_portfolio_positions import expand_position_paths
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import as_float, value_at
except ModuleNotFoundError:
    from apply_manual_trade import locate_position
    from check_portfolio_positions import expand_position_paths
    from new_trade_plan import set_value, write_yaml
    from risk_check import as_float, value_at


VALID_ACTIONS = {"confirm_hard_stop", "keep_reference"}


def append_audit_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def action_label(action: str) -> str:
    return {
        "confirm_hard_stop": "确认为硬止损",
        "keep_reference": "仅保留参考",
    }.get(action, action)


def next_plan_for(action: str, stop_loss_price: float, current_price: float | None) -> str:
    if action == "confirm_hard_stop":
        if current_price is not None and current_price <= stop_loss_price:
            return "现价已不高于硬止损价；刷新后会进入退出风险优先流程，下一步按页面唯一结论执行。"
        return "硬止损已生效；后续现价触及或跌破该价时，系统会升级为退出风险优先。"
    return "该价格仍只作为风控参考；未再次确认前，不会升级为硬止损卖出触发线。"


def apply_stop_loss_confirmation(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    code = str(args.code or "")
    action = str(args.action or "")
    if action not in VALID_ACTIONS:
        raise ValueError("action must be confirm_hard_stop or keep_reference")
    stop_loss_price = as_float(args.stop_loss_price)
    if stop_loss_price is None or stop_loss_price <= 0:
        raise ValueError("stop_loss_price must be greater than 0")
    current_price = as_float(getattr(args, "current_price", None))
    position, path = locate_position(expand_position_paths(args.positions), code)
    now = getattr(args, "confirmed_at", None) or datetime.now().astimezone().isoformat(timespec="seconds")
    source = str(getattr(args, "source", "") or "dashboard")
    dynamic_source = str(getattr(args, "dynamic_source", "") or "")
    reason = str(getattr(args, "reason", "") or "")
    note = str(getattr(args, "note", "") or "")
    existing_price = as_float(value_at(position, "risk.stop_loss_price"))

    set_value(position, "risk.stop_loss_price", round(stop_loss_price, 4))
    set_value(position, "risk.stop_loss_confirmed", action == "confirm_hard_stop")
    set_value(position, "risk.stop_loss_confirmation_status", "hard_stop" if action == "confirm_hard_stop" else "reference_only")
    set_value(position, "risk.stop_loss_confirmation_action", action)
    set_value(position, "risk.stop_loss_confirmation_label", action_label(action))
    set_value(position, "risk.stop_loss_confirmation_at", now)
    set_value(position, "risk.stop_loss_confirmation_by", source)
    set_value(position, "risk.stop_loss_confirmation_source", dynamic_source)
    set_value(position, "risk.stop_loss_confirmation_reason", reason)
    set_value(position, "risk.stop_loss_confirmation_note", note)
    if current_price is not None:
        set_value(position, "risk.stop_loss_confirmation_current_price", round(current_price, 4))

    history = position.setdefault("stop_loss_confirmation_history", [])
    if not isinstance(history, list):
        raise ValueError("stop_loss_confirmation_history must be a list")
    record = {
        "id": f"STOPLOSS-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{code}",
        "code": code,
        "name": value_at(position, "stock.name"),
        "action": action,
        "action_label": action_label(action),
        "confirmed": action == "confirm_hard_stop",
        "stop_loss_price": round(stop_loss_price, 4),
        "previous_stop_loss_price": existing_price,
        "current_price": None if current_price is None else round(current_price, 4),
        "dynamic_source": dynamic_source,
        "reason": reason,
        "note": note,
        "source": source,
        "occurred_at": now,
        "next_plan": next_plan_for(action, stop_loss_price, current_price),
    }
    history.append(record)
    write_yaml(path, position, overwrite=True)
    audit_output = getattr(args, "audit_output", None)
    if audit_output:
        append_audit_record(Path(audit_output), {**record, "position_path": str(path)})
    return {"position_path": str(path), "confirmation": record}, path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Confirm or keep a dynamic stop-loss reference for a local position YAML.")
    parser.add_argument("--positions", nargs="+", default=["positions/POS-EASTMONEY-*.yaml"])
    parser.add_argument("--code", required=True)
    parser.add_argument("--action", choices=sorted(VALID_ACTIONS), required=True)
    parser.add_argument("--stop-loss-price", type=float, required=True)
    parser.add_argument("--current-price", type=float)
    parser.add_argument("--dynamic-source", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--source", default="cli")
    parser.add_argument("--confirmed-at")
    parser.add_argument("--audit-output", default="data/metadata/stop-loss-confirmations.jsonl")
    parser.add_argument("--metadata-output", default="data/metadata/stop-loss-confirmation.latest.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result, _ = apply_stop_loss_confirmation(args)
    output = Path(args.metadata_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
