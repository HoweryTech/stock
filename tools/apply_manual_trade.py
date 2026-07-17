#!/usr/bin/env python3
"""Apply a user-entered executed trade to a local position file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.monitor_intraday_positions import trade_costs
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from monitor_intraday_positions import trade_costs
    from new_trade_plan import set_value, write_yaml
    from risk_check import as_float, load_yaml, value_at


def locate_position(position_paths: list[Path], code: str) -> tuple[dict[str, Any], Path]:
    for path in position_paths:
        position = load_yaml(path)
        if str(value_at(position, "stock.code") or "") == code:
            return position, path
    raise ValueError(f"position not found for code {code}")


def cost_model(args: argparse.Namespace) -> dict[str, float]:
    return {
        "commission_rate": args.commission_rate,
        "minimum_commission": args.minimum_commission,
        "stamp_duty_rate": args.stamp_duty_rate,
        "transfer_fee_rate": args.transfer_fee_rate,
        "minimum_net_profit": 0.0,
    }


def one_side_fees(side: str, price: float, shares: float, costs: dict[str, float]) -> dict[str, float]:
    amount = price * shares
    commission = max(amount * costs["commission_rate"], costs["minimum_commission"])
    stamp_duty = amount * costs["stamp_duty_rate"] if side == "sell" else 0.0
    transfer_fee = amount * costs["transfer_fee_rate"]
    total = commission + stamp_duty + transfer_fee
    return {
        "commission": round(commission, 4),
        "stamp_duty": round(stamp_duty, 4),
        "transfer_fee": round(transfer_fee, 4),
        "total_fees": round(total, 4),
    }


def recalculated_position_pct(shares: float, price: float, total_assets: float | None) -> float | None:
    if total_assets is None or total_assets <= 0:
        return None
    return round(shares * price / total_assets * 100, 4)


def append_manual_trade(position: dict[str, Any], record: dict[str, Any]) -> None:
    history = position.setdefault("manual_trade_history", [])
    if not isinstance(history, list):
        raise ValueError("manual_trade_history must be a list")
    history.append(record)


def find_manual_trade(position: dict[str, Any], trade_id: str) -> dict[str, Any] | None:
    history = position.get("manual_trade_history") or []
    if not isinstance(history, list):
        return None
    for record in history:
        if str(record.get("id") or "") == trade_id:
            return record
    return None


def reverse_t_closure_summary(open_record: dict[str, Any], close_record: dict[str, Any], shares_after: float) -> dict[str, Any]:
    sell_price = as_float(open_record.get("price"), 0.0) or 0.0
    buy_price = as_float(close_record.get("price"), 0.0) or 0.0
    open_shares = as_float(open_record.get("shares"), 0.0) or 0.0
    close_shares = as_float(close_record.get("shares"), 0.0) or 0.0
    shares = min(open_shares, close_shares)
    sell_fees = as_float(value_at(open_record, "fees.total_fees"), 0.0) or 0.0
    buy_fees = as_float(value_at(close_record, "fees.total_fees"), 0.0) or 0.0
    gross_profit = (sell_price - buy_price) * shares
    total_fees = sell_fees + buy_fees
    net_profit = gross_profit - total_fees
    cost_reduction = net_profit / shares_after if shares_after > 0 else None
    status = "closed_profitable" if net_profit >= 0 else "closed_loss"
    if status == "closed_profitable":
        next_plan = "反T闭环完成；今天不再围绕同一卖出腿重复操作，刷新实时建议后只按新的区间观察。"
    else:
        next_plan = "反T闭环已记录但扣费后未盈利；暂停同类操作，先复核卖出价、回补价和费用参数。"
    return {
        "status": status,
        "sell_trade_id": open_record.get("id"),
        "buy_trade_id": close_record.get("id"),
        "sell_occurred_at": open_record.get("occurred_at"),
        "buy_occurred_at": close_record.get("occurred_at"),
        "sell_price": round(sell_price, 4),
        "buy_price": round(buy_price, 4),
        "shares": float(shares),
        "gross_profit": round(gross_profit, 4),
        "fees": {
            "sell_fees": round(sell_fees, 4),
            "buy_fees": round(buy_fees, 4),
            "total_fees": round(total_fees, 4),
        },
        "net_profit": round(net_profit, 4),
        "cost_reduction_per_remaining_share": None if cost_reduction is None else round(cost_reduction, 4),
        "next_plan": next_plan,
    }


def positive_t_closure_summary(open_record: dict[str, Any], close_record: dict[str, Any]) -> dict[str, Any]:
    buy_price = as_float(open_record.get("price"), 0.0) or 0.0
    sell_price = as_float(close_record.get("price"), 0.0) or 0.0
    open_shares = as_float(open_record.get("shares"), 0.0) or 0.0
    close_shares = as_float(close_record.get("shares"), 0.0) or 0.0
    shares = min(open_shares, close_shares)
    buy_fees = as_float(value_at(open_record, "fees.total_fees"), 0.0) or 0.0
    sell_fees = as_float(value_at(close_record, "fees.total_fees"), 0.0) or 0.0
    gross_profit = (sell_price - buy_price) * shares
    total_fees = buy_fees + sell_fees
    net_profit = gross_profit - total_fees
    status = "closed_profitable" if net_profit >= 0 else "closed_loss"
    if status == "closed_profitable":
        next_plan = "正T闭环完成；今天不再围绕同一买入腿重复操作，刷新实时建议后只按新的候选计划观察。"
    else:
        next_plan = "正T闭环已记录但扣费后未盈利；暂停同类操作，先复核买入价、卖出价和费用参数。"
    return {
        "status": status,
        "buy_trade_id": open_record.get("id"),
        "sell_trade_id": close_record.get("id"),
        "buy_occurred_at": open_record.get("occurred_at"),
        "sell_occurred_at": close_record.get("occurred_at"),
        "buy_price": round(buy_price, 4),
        "sell_price": round(sell_price, 4),
        "shares": float(shares),
        "gross_profit": round(gross_profit, 4),
        "fees": {
            "buy_fees": round(buy_fees, 4),
            "sell_fees": round(sell_fees, 4),
            "total_fees": round(total_fees, 4),
        },
        "net_profit": round(net_profit, 4),
        "profit_per_share": None if shares <= 0 else round(net_profit / shares, 4),
        "next_plan": next_plan,
    }


def execution_quality_review(record: dict[str, Any]) -> dict[str, Any]:
    score = 70
    checks: list[dict[str, Any]] = []

    def add(code: str, label: str, status: str, message: str, delta: int = 0) -> None:
        nonlocal score
        score += delta
        checks.append({"code": code, "label": label, "status": status, "message": message, "score_delta": delta})

    shares = as_float(record.get("shares"), 0.0) or 0.0
    side = str(record.get("side") or "")
    intent = str(record.get("trade_intent") or "")
    realized_pnl = as_float(record.get("realized_pnl"))
    reverse_closure = record.get("reverse_t_closure") if isinstance(record.get("reverse_t_closure"), dict) else None
    positive_closure = record.get("positive_t_closure") if isinstance(record.get("positive_t_closure"), dict) else None
    closure = reverse_closure or positive_closure

    if shares > 0 and shares % 100 == 0:
        add("lot_size", "交易单位", "pass", "成交数量为100股整数手。", 5)
    else:
        add("lot_size", "交易单位", "warn", "成交数量不是100股整数手，后续需要人工复核。", -15)

    if intent:
        add("intent_recorded", "成交意图", "pass", f"已记录成交意图：{intent}。", 5)
    else:
        add("intent_missing", "成交意图", "warn", "普通手工成交未绑定系统候选计划，复盘时要确认是否属于临时决策。", -5)

    if closure:
        net_profit = as_float(closure.get("net_profit"), 0.0) or 0.0
        if net_profit >= 10:
            add("closure_profit_target", "闭环收益", "pass", f"扣费后净收益 {net_profit:.2f} 元，达到10元复盘参考线。", 15)
        elif net_profit >= 0:
            add("closure_profitable", "闭环收益", "warn", f"扣费后净收益 {net_profit:.2f} 元，盈利但低于10元参考线。", 5)
        else:
            add("closure_loss", "闭环收益", "block", f"扣费后净收益 {net_profit:.2f} 元，闭环未盈利。", -30)
    elif intent in {"reverse_t_open", "positive_t_open"}:
        add("open_leg_pending", "开放腿跟踪", "warn", "已打开T交易腿，必须等回补/目标卖出完成后才能判断最终收益。", 0)
    elif side == "sell" and realized_pnl is not None:
        if realized_pnl < 0:
            add("risk_exit_loss", "卖出结果", "warn", f"本次卖出确认亏损 {realized_pnl:.2f} 元；若是止损退出，复盘重点是是否避免继续扩大亏损。", -5)
        else:
            add("sell_profit", "卖出结果", "pass", f"本次卖出实现收益 {realized_pnl:.2f} 元。", 10)
    elif side == "buy":
        add("buy_follow_up", "买入跟踪", "warn", "买入成交后还不能判断成败，需要跟踪目标卖出区、失败价和仓位风险。", 0)

    total_fees = as_float(value_at(record, "fees.total_fees"))
    if total_fees is not None:
        add("fees_recorded", "费用记录", "pass", f"已记录本次费用 {total_fees:.2f} 元。", 5)
    else:
        add("fees_missing", "费用记录", "warn", "未记录费用，收益复盘可能失真。", -10)

    score = max(0, min(100, score))
    blocking = any(check["status"] == "block" for check in checks)
    warnings = [check for check in checks if check["status"] == "warn"]
    if blocking:
        status = "failed"
        label = "执行失败复盘"
        next_action = "暂停同类操作，先复盘价格、费用和执行原因。"
    elif score >= 85:
        status = "good"
        label = "执行质量良好"
        next_action = "按刷新后的实时建议继续观察，不因单次成功放大仓位。"
    elif warnings:
        status = "needs_review"
        label = "需要复盘"
        next_action = "先按检查项复盘，不把这笔成交直接当作可复制样本。"
    else:
        status = "acceptable"
        label = "执行可接受"
        next_action = "继续跟踪后续价格表现，等待系统刷新下一动作。"
    return {"score": round(score, 1), "status": status, "status_label": label, "checks": checks, "next_action": next_action}


def apply_manual_trade(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    code = str(args.code)
    side = str(args.side).lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    shares = as_float(args.shares)
    price = as_float(args.price)
    if shares is None or shares <= 0:
        raise ValueError("shares must be greater than 0")
    if price is None or price <= 0:
        raise ValueError("price must be greater than 0")

    position, path = locate_position(expand_position_paths(args.positions), code)
    current_shares = as_float(value_at(position, "entry.shares"), 0.0) or 0.0
    entry_price = as_float(value_at(position, "entry.entry_price"), 0.0) or 0.0
    costs = cost_model(args)
    fees = one_side_fees(side, price, shares, costs)
    occurred_at = args.occurred_at or datetime.now().astimezone().isoformat(timespec="seconds")
    trade_amount = round(price * shares, 2)
    realized_pnl = None

    if side == "sell":
        if shares > current_shares:
            raise ValueError(f"sell shares {shares:g} exceed current shares {current_shares:g}")
        new_shares = current_shares - shares
        realized_pnl = round((price - entry_price) * shares - fees["total_fees"], 4)
        set_value(position, "entry.shares", float(new_shares))
        set_value(position, "position.status", "closed" if new_shares <= 0 else "normal")
        available = as_float(value_at(position, "broker_import_snapshot.available_shares"), current_shares)
        if available is not None:
            set_value(position, "broker_import_snapshot.available_shares", float(max(0.0, available - shares)))
    else:
        new_shares = current_shares + shares
        new_entry_price = ((entry_price * current_shares) + trade_amount + fees["total_fees"]) / new_shares
        set_value(position, "entry.shares", float(new_shares))
        set_value(position, "entry.entry_price", round(new_entry_price, 4))
        set_value(position, "entry.planned_buy_price", round(new_entry_price, 4))
        set_value(position, "position.status", "normal")
        available = as_float(value_at(position, "broker_import_snapshot.available_shares"), current_shares)
        if available is not None:
            set_value(position, "broker_import_snapshot.available_shares", float(available + shares))

    set_value(position, "tracking.current_price", price)
    basis_price = as_float(value_at(position, "entry.entry_price"), entry_price)
    if basis_price:
        set_value(position, "tracking.current_return_pct", round((price / basis_price - 1) * 100, 4))
    position_pct = recalculated_position_pct(float(new_shares), price, args.total_assets)
    if position_pct is not None:
        set_value(position, "entry.position_pct_of_total_assets", position_pct)
        set_value(position, "tracking.current_portfolio_return_pct", round(position_pct * ((price / (basis_price or price) - 1) * 100) / 100, 4))
    set_value(position, "broker_import_snapshot.market_value", round(float(new_shares) * price, 2))
    set_value(position, "broker_import_snapshot.profit_loss", round((price - (basis_price or price)) * float(new_shares), 2))
    if basis_price:
        set_value(position, "broker_import_snapshot.return_pct", round((price / basis_price - 1) * 100, 4))

    record = {
        "id": f"MANUAL-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{code}-{side}",
        "source": "dashboard_manual_trade" if args.source == "dashboard" else "manual_cli",
        "occurred_at": occurred_at,
        "code": code,
        "side": side,
        "trade_intent": getattr(args, "trade_intent", None) or "",
        "linked_trade_id": getattr(args, "linked_trade_id", None) or "",
        "price": price,
        "shares": shares,
        "amount": trade_amount,
        "fees": fees,
        "realized_pnl": realized_pnl,
        "shares_before": current_shares,
        "shares_after": float(new_shares),
        "note": args.note or "",
    }
    linked_trade_id = str(record.get("linked_trade_id") or "")
    if side == "buy" and record["trade_intent"] == "reverse_t_close" and linked_trade_id:
        open_record = find_manual_trade(position, linked_trade_id)
        if open_record is None:
            raise ValueError(f"linked reverse T open trade not found: {linked_trade_id}")
        if open_record.get("side") != "sell" or open_record.get("trade_intent") != "reverse_t_open":
            raise ValueError(f"linked trade is not a reverse T open sell: {linked_trade_id}")
        closure = reverse_t_closure_summary(open_record, record, float(new_shares))
        record["reverse_t_closure"] = closure
        set_value(position, "tracking.latest_reverse_t_closure", closure)
    if side == "sell" and record["trade_intent"] == "positive_t_close" and linked_trade_id:
        open_record = find_manual_trade(position, linked_trade_id)
        if open_record is None:
            raise ValueError(f"linked positive T open trade not found: {linked_trade_id}")
        if open_record.get("side") != "buy" or open_record.get("trade_intent") != "positive_t_open":
            raise ValueError(f"linked trade is not a positive T open buy: {linked_trade_id}")
        closure = positive_t_closure_summary(open_record, record)
        record["positive_t_closure"] = closure
        set_value(position, "tracking.latest_positive_t_closure", closure)
    review = execution_quality_review(record)
    record["execution_quality_review"] = review
    set_value(position, "tracking.latest_execution_quality_review", review)
    append_manual_trade(position, record)
    write_yaml(path, position, overwrite=True)
    return {"position_path": str(path), "trade": record, "position": {"shares": float(new_shares), "entry_price": value_at(position, "entry.entry_price")}}, path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a manually entered executed buy/sell to a position YAML.")
    parser.add_argument("--positions", nargs="+", default=["positions/POS-EASTMONEY-*.yaml"])
    parser.add_argument("--code", required=True)
    parser.add_argument("--side", choices=["buy", "sell"], required=True)
    parser.add_argument("--shares", type=float, required=True)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--total-assets", type=float, default=25480.0)
    parser.add_argument("--occurred-at")
    parser.add_argument("--note")
    parser.add_argument("--trade-intent", choices=["", "positive_t_open", "positive_t_close", "reverse_t_open", "reverse_t_close"], default="")
    parser.add_argument("--linked-trade-id", default="")
    parser.add_argument("--source", choices=["cli", "dashboard"], default="cli")
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--minimum-commission", type=float, default=5.0)
    parser.add_argument("--stamp-duty-rate", type=float, default=0.0005)
    parser.add_argument("--transfer-fee-rate", type=float, default=0.00001)
    parser.add_argument("--metadata-output", default="data/metadata/manual-trade-update.latest.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result, _ = apply_manual_trade(args)
    output = Path(args.metadata_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
