#!/usr/bin/env python3
"""Continuously monitor holding quotes and write quasi-real-time reports."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.fetch_holding_research import fetch_realtime_quotes
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from fetch_holding_research import fetch_realtime_quotes
    from risk_check import as_float, load_yaml, value_at


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_close_history(path: Path) -> dict[str, list[dict[str, Any]]]:
    histories: dict[str, list[tuple[str, float]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            close = as_float(row.get("close"))
            code = str(row.get("code") or "")
            if code and close is not None:
                histories.setdefault(code, []).append((str(row.get("trade_date") or ""), close))
    return {code: [{"trade_date": trade_date, "close": close} for trade_date, close in sorted(rows)] for code, rows in histories.items()}


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def moving_averages(closes: list[float]) -> tuple[float | None, float | None]:
    ma5 = average(closes[-5:]) if len(closes) >= 5 else None
    ma20 = average(closes[-20:]) if len(closes) >= 20 else None
    return ma5, ma20


def multi_timeframe_metrics(rows: list[dict[str, Any]], current_price: float | None) -> dict[str, Any]:
    weekly: dict[str, float] = {}
    monthly: dict[str, float] = {}
    for row in rows:
        trade_date = datetime.strptime(str(row["trade_date"]), "%Y-%m-%d")
        weekly[f"{trade_date.isocalendar().year}-{trade_date.isocalendar().week:02d}"] = float(row["close"])
        monthly[trade_date.strftime("%Y-%m")] = float(row["close"])
    weekly_closes = list(weekly.values())
    monthly_closes = list(monthly.values())
    weekly_ma4 = average(weekly_closes[-4:]) if len(weekly_closes) >= 4 else None
    weekly_ma12 = average(weekly_closes[-12:]) if len(weekly_closes) >= 12 else None
    monthly_ma3 = average(monthly_closes[-3:]) if len(monthly_closes) >= 3 else None
    monthly_ma6 = average(monthly_closes[-6:]) if len(monthly_closes) >= 6 else None

    def period_return(values: list[float], periods: int) -> float | None:
        if current_price is None or len(values) < periods or values[-periods] == 0:
            return None
        return (current_price / values[-periods] - 1) * 100

    weekly_return_4 = period_return(weekly_closes, 4)
    monthly_return_3 = period_return(monthly_closes, 3)
    if current_price is None or weekly_ma4 is None or monthly_ma3 is None:
        alignment = "insufficient"
    elif current_price >= weekly_ma4 and current_price >= monthly_ma3 and (weekly_return_4 or 0) >= 0 and (monthly_return_3 or 0) >= 0:
        alignment = "bullish"
    elif current_price < weekly_ma4 and current_price < monthly_ma3:
        alignment = "bearish"
    else:
        alignment = "mixed"
    return {
        "weekly_closes_count": len(weekly_closes),
        "monthly_closes_count": len(monthly_closes),
        "weekly_ma4": weekly_ma4,
        "weekly_ma12": weekly_ma12,
        "weekly_return_4_pct": weekly_return_4,
        "monthly_ma3": monthly_ma3,
        "monthly_ma6": monthly_ma6,
        "monthly_return_3_pct": monthly_return_3,
        "alignment": alignment,
    }


def floor_to_tick(price: float, tick: float = 0.01) -> float:
    return round(math.floor((price + 1e-9) / tick) * tick, 2)


def dynamic_price_zone_width(anchor_price: float | None, *, ratio_pct: float = 0.18, min_ticks: int = 1, max_ticks: int = 6, tick: float = 0.01) -> float:
    if anchor_price is None or anchor_price <= 0:
        return round(min_ticks * tick, 2)
    raw_width = anchor_price * ratio_pct / 100
    ticks = math.ceil(raw_width / tick)
    ticks = max(min_ticks, min(max_ticks, ticks))
    return round(ticks * tick, 2)


def trade_costs(
    sell_price: float,
    buy_price: float,
    shares: int,
    costs: dict[str, float],
) -> dict[str, float]:
    sell_amount = sell_price * shares
    buy_amount = buy_price * shares
    commission_rate = costs["commission_rate"]
    minimum_commission = costs["minimum_commission"]
    sell_commission = max(sell_amount * commission_rate, minimum_commission)
    buy_commission = max(buy_amount * commission_rate, minimum_commission)
    stamp_duty = sell_amount * costs["stamp_duty_rate"]
    transfer_fee = (sell_amount + buy_amount) * costs["transfer_fee_rate"]
    total_fees = sell_commission + buy_commission + stamp_duty + transfer_fee
    gross_profit = (sell_price - buy_price) * shares
    return {
        "sell_commission": round(sell_commission, 4),
        "buy_commission": round(buy_commission, 4),
        "stamp_duty": round(stamp_duty, 4),
        "transfer_fee": round(transfer_fee, 4),
        "total_fees": round(total_fees, 4),
        "gross_profit": round(gross_profit, 4),
        "net_profit": round(gross_profit - total_fees, 4),
    }


def latest_open_reverse_t_leg(position: dict[str, Any]) -> dict[str, Any] | None:
    history = position.get("manual_trade_history") or []
    if not isinstance(history, list):
        return None
    closed_ids = {
        str(record.get("linked_trade_id"))
        for record in history
        if record.get("side") == "buy" and record.get("trade_intent") == "reverse_t_close" and record.get("linked_trade_id")
    }
    candidates = [
        record for record in history
        if record.get("side") == "sell"
        and record.get("trade_intent") == "reverse_t_open"
        and str(record.get("id") or "") not in closed_ids
    ]
    return candidates[-1] if candidates else None


def latest_open_positive_t_leg(position: dict[str, Any]) -> dict[str, Any] | None:
    history = position.get("manual_trade_history") or []
    if not isinstance(history, list):
        return None
    closed_ids = {
        str(record.get("linked_trade_id"))
        for record in history
        if record.get("side") == "sell" and record.get("trade_intent") == "positive_t_close" and record.get("linked_trade_id")
    }
    candidates = [
        record for record in history
        if record.get("side") == "buy"
        and record.get("trade_intent") == "positive_t_open"
        and str(record.get("id") or "") not in closed_ids
    ]
    return candidates[-1] if candidates else None


def build_t_closure_performance(position: dict[str, Any]) -> dict[str, Any]:
    history = position.get("manual_trade_history") or []
    if not isinstance(history, list):
        history = []

    closures: list[dict[str, Any]] = []
    for record in history:
        intent = record.get("trade_intent")
        closure_key = "reverse_t_closure" if intent == "reverse_t_close" else "positive_t_closure" if intent == "positive_t_close" else ""
        if not closure_key:
            continue
        closure = record.get(closure_key)
        if not isinstance(closure, dict):
            continue
        net_profit = as_float(closure.get("net_profit"), 0.0) or 0.0
        gross_profit = as_float(closure.get("gross_profit"), 0.0) or 0.0
        total_fees = as_float(value_at(closure, "fees.total_fees"), 0.0) or 0.0
        close_price_key = "buy_price" if intent == "reverse_t_close" else "sell_price"
        open_price_key = "sell_price" if intent == "reverse_t_close" else "buy_price"
        closures.append({
            "type": "reverse_t" if intent == "reverse_t_close" else "positive_t",
            "type_label": "反T" if intent == "reverse_t_close" else "正T",
            "status": closure.get("status"),
            "profitable": net_profit > 0,
            "net_profit": round(net_profit, 4),
            "gross_profit": round(gross_profit, 4),
            "total_fees": round(total_fees, 4),
            "shares": as_float(closure.get("shares"), 0.0) or 0.0,
            "open_price": as_float(closure.get(open_price_key)),
            "close_price": as_float(closure.get(close_price_key)),
            "open_trade_id": closure.get("sell_trade_id") if intent == "reverse_t_close" else closure.get("buy_trade_id"),
            "close_trade_id": closure.get("buy_trade_id") if intent == "reverse_t_close" else closure.get("sell_trade_id"),
            "closed_at": record.get("occurred_at"),
        })

    total_count = len(closures)
    profitable_count = sum(1 for item in closures if item["net_profit"] > 0)
    loss_count = sum(1 for item in closures if item["net_profit"] <= 0)
    total_net_profit = round(sum(float(item["net_profit"]) for item in closures), 4)
    total_gross_profit = round(sum(float(item["gross_profit"]) for item in closures), 4)
    total_fees = round(sum(float(item["total_fees"]) for item in closures), 4)
    reverse_closures = [item for item in closures if item["type"] == "reverse_t"]
    positive_closures = [item for item in closures if item["type"] == "positive_t"]
    average_net_profit = round(total_net_profit / total_count, 4) if total_count else None
    win_rate_pct = round(profitable_count / total_count * 100, 2) if total_count else None

    if total_count == 0:
        status = "no_history"
        status_label = "暂无实盘闭环样本"
        next_action = "先只按系统候选小额试做；完成正T/反T闭环后再评估这只股票是否适合继续做T。"
    elif total_net_profit > 0 and profitable_count >= loss_count:
        status = "profitable"
        status_label = "实盘闭环暂时有效"
        next_action = "可以继续小额执行系统给出的候选区间；不要因为单次盈利放大单次股数。"
    else:
        status = "needs_review"
        status_label = "实盘闭环需要降频"
        next_action = "暂停放大做T，只保留最小100股试做或等待更强的技术确认。"

    return {
        "status": status,
        "status_label": status_label,
        "total_count": total_count,
        "profitable_count": profitable_count,
        "loss_count": loss_count,
        "win_rate_pct": win_rate_pct,
        "total_net_profit": total_net_profit,
        "average_net_profit": average_net_profit,
        "total_gross_profit": total_gross_profit,
        "total_fees": total_fees,
        "reverse_t_count": len(reverse_closures),
        "reverse_t_net_profit": round(sum(float(item["net_profit"]) for item in reverse_closures), 4),
        "positive_t_count": len(positive_closures),
        "positive_t_net_profit": round(sum(float(item["net_profit"]) for item in positive_closures), 4),
        "recent_closures": closures[-5:],
        "next_action": next_action,
    }


def one_side_trade_fees(side: str, price: float, shares: int, costs: dict[str, float]) -> dict[str, float]:
    amount = price * shares
    commission = max(amount * costs["commission_rate"], costs["minimum_commission"])
    stamp_duty = amount * costs["stamp_duty_rate"] if side == "sell" else 0.0
    transfer_fee = amount * costs["transfer_fee_rate"]
    return {
        "commission": round(commission, 4),
        "stamp_duty": round(stamp_duty, 4),
        "transfer_fee": round(transfer_fee, 4),
        "total_fees": round(commission + stamp_duty + transfer_fee, 4),
    }


def build_positive_t_plan(
    position: dict[str, Any],
    quote: dict[str, Any],
    *,
    stale: bool,
    costs: dict[str, float],
    target_gain_pct: float = 1.2,
    fallback_stop_pct: float = 3.0,
) -> dict[str, Any]:
    open_leg = latest_open_positive_t_leg(position)
    if not open_leg:
        return {"status": "not_applicable", "status_label": "没有开放中的正T买入腿", "execution_steps": []}

    shares = int(as_float(open_leg.get("shares"), 0.0) or 0.0)
    buy_price = as_float(open_leg.get("price"))
    current = as_float(quote.get("latest_price"))
    stop_loss = as_float(value_at(position, "risk.stop_loss_price"))
    fallback_stop = buy_price * (1 - fallback_stop_pct / 100) if buy_price is not None else None
    failure_price = max(value for value in [stop_loss, fallback_stop] if value is not None) if any(value is not None for value in [stop_loss, fallback_stop]) else None
    if buy_price is None or shares <= 0:
        return {
            "status": "invalid_open_leg",
            "status_label": "正T买入腿记录不完整",
            "open_positive_t_leg": {"id": open_leg.get("id"), "buy_price": buy_price, "shares": shares, "occurred_at": open_leg.get("occurred_at")},
            "execution_steps": ["不生成目标卖出计划；先人工复核正T买入成交记录。"],
        }

    target_low = round(buy_price * (1 + target_gain_pct / 100), 2)
    target_high = round(target_low + dynamic_price_zone_width(target_low), 2)
    buy_fees = as_float(value_at(open_leg, "fees.total_fees"), 0.0) or 0.0
    sell_fees = one_side_trade_fees("sell", target_low, shares, costs)
    estimated_net_profit = (target_low - buy_price) * shares - buy_fees - sell_fees["total_fees"]
    blockers: list[str] = []
    if stale:
        blockers.append("行情过期，不能判断正T目标卖出或失败条件。")
    if current is None:
        blockers.append("缺少现价，不能判断正T目标卖出或失败条件。")

    if blockers:
        status = "data_wait"
        status_label = "等待行情刷新"
        next_action = "刷新实时行情后再判断正T买入腿是否到达目标卖出或失败条件。"
    elif failure_price is not None and current <= failure_price:
        status = "failure_review"
        status_label = "正T失败复核"
        next_action = f"现价 {current:.2f} 已不高于失败价 {failure_price:.2f}；不要继续补仓，先复核是否止损新增仓位或转普通持仓。"
    elif current >= target_low:
        status = "target_sell_ready"
        status_label = "正T目标卖出触发"
        next_action = f"现价 {current:.2f} 已进入目标卖出区；可卖出新增的 {shares} 股完成正T闭环。"
    else:
        status = "target_sell_wait"
        status_label = "等待正T目标卖出"
        next_action = f"继续等待 {target_low:.2f}-{target_high:.2f} 元目标卖出区；未到目标不急于卖出。"

    execution_steps = [
        f"第1步：确认这笔正T买入腿已真实成交：{buy_price:.2f} 元买入 {shares} 股。",
        f"第2步：只在价格进入 {target_low:.2f}-{target_high:.2f} 元目标区时，卖出新增的 {shares} 股；未到目标不卖。",
        f"第3步：若按 {target_low:.2f} 元卖出，扣除买入和卖出费用后预计净收益约 {estimated_net_profit:.2f} 元。",
        f"第4步：如果价格跌到 {failure_price:.2f} 元或更低，不继续补仓；先做失败复核。" if failure_price is not None else "第4步：缺少止损价时，不扩大正T仓位；收盘前必须人工复核。",
        "第5步：当天收盘前仍未到目标卖出区时，刷新系统并决定是否转为普通持仓，不继续追加同方向买入。",
    ]
    if status == "target_sell_ready":
        execution_steps.insert(2, "当前已经到达目标区；先卖出新增股数，再在系统里记录为正T卖出闭环。")
    elif status == "failure_review":
        execution_steps.insert(2, "当前已经触发失败复核；不要用继续买入来摊低这笔正T买入腿。")

    return {
        "status": status,
        "status_label": status_label,
        "trade_shares": shares,
        "buy_price": round(buy_price, 4),
        "target_sell_zone": [target_low, target_high],
        "failure_price": None if failure_price is None else round(failure_price, 4),
        "estimated_net_profit_at_target": round(estimated_net_profit, 4),
        "buy_fees": round(buy_fees, 4),
        "estimated_sell_fees": sell_fees,
        "open_positive_t_leg": {
            "id": open_leg.get("id"),
            "buy_price": buy_price,
            "shares": shares,
            "occurred_at": open_leg.get("occurred_at"),
            "fees": open_leg.get("fees"),
        },
        "blockers": blockers,
        "next_action": next_action,
        "execution_steps": execution_steps,
        "instructions": execution_steps,
    }


def fee_aware_buyback_price(sell_price: float, shares: int, costs: dict[str, float], *, max_gap_pct: float = 8.0) -> dict[str, Any] | None:
    gap_pct = 0.1
    while gap_pct <= max_gap_pct + 1e-9:
        buy_price = floor_to_tick(sell_price * (1 - gap_pct / 100))
        fees = trade_costs(sell_price, buy_price, shares, costs)
        if fees["net_profit"] >= costs["minimum_net_profit"]:
            return {"buyback_max_price": buy_price, "required_gap_pct": round((sell_price / buy_price - 1) * 100, 4), "fees": fees}
        gap_pct += 0.1
    return None


def fee_viable_trade(
    sell_price: float,
    max_shares: int,
    costs: dict[str, float],
    *,
    min_gap_pct: float,
    max_gap_pct: float = 3.0,
) -> dict[str, Any] | None:
    minimum_net_profit = costs["minimum_net_profit"]
    for shares in range(100, max_shares + 1, 100):
        gap_pct = min_gap_pct
        while gap_pct <= max_gap_pct + 1e-9:
            buy_price = floor_to_tick(sell_price * (1 - gap_pct / 100))
            fees = trade_costs(sell_price, buy_price, shares, costs)
            if fees["net_profit"] >= minimum_net_profit:
                return {"trade_shares": shares, "buyback_max_price": buy_price, "required_gap_pct": round((sell_price / buy_price - 1) * 100, 4), "fees": fees}
            gap_pct += 0.1
    return None


def reverse_t_blocker(code: str, label: str, current: str, reason: str, next_step: str) -> dict[str, str]:
    return {
        "code": code,
        "label": label,
        "current": current,
        "reason": reason,
        "next_step": next_step,
    }


def build_reverse_t_execution_steps(
    *,
    status: str,
    trade_shares: int,
    sell_zone_low: float | None,
    sell_zone_high: float | None,
    buyback_max: float | None,
    required_gap_pct: float | None,
    cost_estimate: dict[str, Any] | None,
    failure_as_reduction_acceptable: bool,
) -> list[str]:
    if status != "candidate" or sell_zone_low is None or sell_zone_high is None or buyback_max is None:
        return [
            "当前不要卖出，也不要提前挂反T回补单。",
            "只保留卖出观察区和回补上限作为参考；等状态变为反T候选后再按步骤执行。",
            "如果已手动卖出但未到回补上限，不追价买回；先按实际成交重新生成决策卡。",
        ]
    net_profit = as_float((cost_estimate or {}).get("net_profit"))
    net_text = f"，扣费后最低净收益约{net_profit:.2f}元" if net_profit is not None else ""
    failure_text = "未回补时把这笔卖出计入计划降仓，不追买。" if failure_as_reduction_acceptable else "未回补会形成计划外减仓；如果不能接受这个后果，不执行第1步。"
    return [
        f"第1步：只在价格进入 {sell_zone_low:.2f}-{sell_zone_high:.2f} 元且从区间高位转弱时，限价卖出 {trade_shares} 股；不要在快速拉升中抢跑。",
        f"第2步：卖出成交后，只在价格回落到 {buyback_max:.2f} 元及以下时，买回同等 {trade_shares} 股；高于该价不回补。",
        f"第3步：本轮所需价差约 {required_gap_pct:.2f}%{net_text}；如果卖出后没有触发回补价，按计划停止，不追价买回。",
        f"第4步：{failure_text}",
        "第5步：同一股票当天最多执行一轮；成交后记录实际卖出价、买回价和费用，再刷新系统建议。",
    ]


def build_reverse_t_plan(
    position: dict[str, Any],
    quote: dict[str, Any],
    *,
    stale: bool,
    costs: dict[str, float],
    timeframe: dict[str, Any],
    preferred_reduction_shares: int | None = None,
    max_trade_ratio_pct: float = 50.0,
    min_gap_pct: float = 1.2,
) -> dict[str, Any]:
    shares = int(as_float(value_at(position, "entry.shares"), 0.0) or 0.0)
    available = int(as_float(position.get("broker_import_snapshot", {}).get("available_shares"), shares) or 0.0)
    price = as_float(quote.get("latest_price"))
    high = as_float(quote.get("high"))
    low = as_float(quote.get("low"))
    open_price = as_float(quote.get("open"))
    change_pct = as_float(quote.get("change_pct"))
    max_trade_shares = math.floor(shares * max_trade_ratio_pct / 100 / 100) * 100
    if preferred_reduction_shares:
        max_trade_shares = min(max_trade_shares, preferred_reduction_shares)
    trade_shares = 100
    trade_ratio_pct = trade_shares / shares * 100 if shares else None
    original_position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    failure_as_reduction_acceptable = original_position_pct > 10.0
    range_pct = (high - low) / low * 100 if high is not None and low not in (None, 0) else None
    range_position = (price - low) / (high - low) if price is not None and high is not None and low is not None and high > low else None
    blockers: list[str] = []
    blocker_details: list[dict[str, str]] = []
    def add_blocker(code: str, label: str, current: str, reason: str, next_step: str) -> None:
        blockers.append(reason)
        blocker_details.append(reverse_t_blocker(code, label, current, reason, next_step))

    open_leg = latest_open_reverse_t_leg(position)
    if open_leg:
        leg_shares = int(as_float(open_leg.get("shares"), 0.0) or 0.0)
        sell_price = as_float(open_leg.get("price"))
        viable = fee_aware_buyback_price(sell_price, leg_shares, costs) if sell_price is not None and leg_shares > 0 else None
        buyback_max = viable["buyback_max_price"] if viable else None
        cost_estimate = viable["fees"] if viable else None
        required_gap_pct = viable["required_gap_pct"] if viable else None
        if stale:
            add_blocker("stale_quote", "行情时效", "已过期", "行情过期。", "刷新实时行情；行情恢复前不卖出、不回补。")
        if buyback_max is None:
            add_blocker("open_leg_fee_not_viable", "回补费用", "--", "这笔已卖出的反T腿无法在费用模型下给出回补上限。", "不要追价回补；先人工复核成交记录和费用参数。")
        elif price is not None and price > buyback_max:
            add_blocker("buyback_price_not_reached", "回补价格", f"{price:.2f} > {buyback_max:.2f}", "现价尚未降至反T回补上限。", "继续等待；高于回补上限不买回。")
        status = "buyback_ready" if not blockers and price is not None and buyback_max is not None and price <= buyback_max else "buyback_wait"
        next_action = (
            f"反T回补触发：上午已按 {sell_price:.2f} 卖出 {leg_shares} 股，现价已不高于回补上限 {buyback_max:.2f}，可买回同等股数。"
            if status == "buyback_ready" and sell_price is not None and buyback_max is not None
            else "已有开放中的反T卖出腿；当前只跟踪回补，不再新增反T卖出。"
        )
        execution_steps = [
            f"第1步：确认券商里上午卖出的 {leg_shares} 股已经成交；这是反T卖出腿，不是普通减仓。",
            f"第2步：只在价格 {buyback_max:.2f} 元及以下买回 {leg_shares} 股；高于该价不追买。" if buyback_max is not None else "第2步：费用模型未给出回补价，暂停回补。",
            f"第3步：若按 {buyback_max:.2f} 元回补，预计扣费后净收益约 {cost_estimate['net_profit']:.2f} 元；成交后记录为反T回补。" if buyback_max is not None and cost_estimate else "第3步：先人工复核费用和成交记录。",
            "第4步：回补成交后刷新系统，持仓会恢复，系统重新计算成本和下一步建议。",
        ]
        return {
            "status": status,
            "trade_shares": leg_shares,
            "trade_ratio_pct": None if shares in (None, 0) else round(leg_shares / shares * 100, 2),
            "intraday_range_pct": None if range_pct is None else round(range_pct, 4),
            "range_position_pct": None if range_position is None else round(range_position * 100, 2),
            "sell_zone": [sell_price, sell_price] if sell_price is not None else None,
            "buyback_max_price": buyback_max,
            "min_gap_pct": min_gap_pct,
            "required_gap_pct": required_gap_pct,
            "estimated_cost_reduction_per_share": round(cost_estimate["net_profit"] / (shares + leg_shares), 4) if cost_estimate and shares + leg_shares > 0 else None,
            "cost_estimate": cost_estimate,
            "cost_model_verified": bool(costs.get("verified")),
            "timeframe_alignment": timeframe.get("alignment"),
            "failure_as_reduction_acceptable": True,
            "failure_result": "这是已卖出的反T腿；未回补则持仓继续少100股，不能再把它当作新反T卖出。",
            "high_position_ratio_warning": False,
            "main_flow_confirmation": "not_required_for_open_buyback",
            "price_in_sell_zone": False,
            "open_reverse_t_leg": {
                "id": open_leg.get("id"),
                "sell_price": sell_price,
                "shares": leg_shares,
                "occurred_at": open_leg.get("occurred_at"),
                "fees": open_leg.get("fees"),
            },
            "blockers": blockers,
            "blocker_details": blocker_details,
            "next_action": next_action,
            "execution_steps": execution_steps,
            "instructions": execution_steps,
        }

    if stale:
        add_blocker("stale_quote", "行情时效", "已过期", "行情过期。", "刷新实时行情；行情恢复前不卖出、不回补。")
    if available < 100:
        add_blocker("available_shares_insufficient", "可用股份", f"{available}股", "可用股份不足100股。", "等可卖股份恢复到至少100股，或不要执行反T。")
    if shares < 200:
        add_blocker("base_position_insufficient", "持仓底仓", f"{shares}股", "持仓少于200股，卖出100股后无法保留底仓。", "反T至少需要卖出后仍保留底仓；当前只允许持有观察或按减仓计划卖出。")
    if change_pct is not None and change_pct <= -9.8:
        add_blocker("limit_down_or_near", "跌停风险", f"{change_pct:.2f}%", "接近或达到跌停，不做反T。", "等待流动性恢复；跌停附近不做卖出后回补的价差交易。")
    if range_pct is None or range_pct < 1.5:
        current_range = "--" if range_pct is None else f"{range_pct:.2f}%"
        add_blocker("range_too_small", "当日振幅", current_range, "当日振幅不足1.5%，价差空间不够。", "等待振幅扩大并接近卖出观察区；价差不足时扣费后很难降低成本。")
    if timeframe.get("alignment") == "insufficient":
        add_blocker("timeframe_insufficient", "多周期验证", "历史不足", "周线或月线历史不足，无法完成多周期验证。", "补齐日线历史并重新计算周/月线；未通过前只观察。")
    if timeframe.get("alignment") == "bearish" and not failure_as_reduction_acceptable:
        add_blocker("timeframe_bearish_no_reduction_plan", "多周期趋势", "bearish", "周线和月线均偏弱，且该股票没有计划降仓目标，不宜卖出后再回补。", "若趋势偏弱，应先评估减仓；不把反T作为摊低成本工具。")

    main_flow_ratio = as_float(quote.get("main_net_inflow_ratio_pct"))
    strong_main_inflow = main_flow_ratio is not None and main_flow_ratio >= 3.0

    setup_ready = (
        not blockers
        and price is not None
        and open_price is not None
        and price >= open_price
        and change_pct is not None
        and change_pct > 0
        and range_position is not None
        and range_position >= 0.7
    )
    if blockers:
        status = "not_suitable"
    elif setup_ready:
        status = "candidate"
    else:
        status = "watch"

    sell_zone_low = None
    sell_zone_high = None
    buyback_max = None
    estimated_cost_reduction = None
    cost_estimate = None
    required_gap_pct = None
    if high is not None:
        sell_zone_high = round(high, 2)
        sell_zone_width = dynamic_price_zone_width(high)
        sell_zone_low = max(round(high - sell_zone_width, 2), round(open_price or high, 2))
        viable = fee_viable_trade(sell_zone_low, max_trade_shares, costs, min_gap_pct=min_gap_pct) if max_trade_shares >= 100 else None
        if viable:
            trade_shares = viable["trade_shares"]
            trade_ratio_pct = trade_shares / shares * 100 if shares else None
            buyback_max = viable["buyback_max_price"]
            required_gap_pct = viable["required_gap_pct"]
            cost_estimate = viable["fees"]
            estimated_cost_reduction = round(cost_estimate["net_profit"] / shares, 4) if shares else None
        elif not blockers:
            add_blocker(
                "fee_not_viable",
                "费用模型",
                f"最大价差3%，最低净收益{costs['minimum_net_profit']:.2f}元",
                f"在允许动用的持仓比例和最大3%价差下，扣除估算费用后净收益不足{costs['minimum_net_profit']:.2f}元。",
                "不执行反T；除非扩大可交易股数、降低最低净收益门槛，或等待更高卖出区间后重新计算回补上限。",
            )
            status = "fee_blocked"

    if status == "candidate":
        next_action = f"可以进入反T人工候选：只在价格进入卖出观察区后转弱时卖出{trade_shares}股，随后只在回补上限及以下买回。"
    elif status == "watch":
        next_action = "当前不卖出。等待价格进入卖出观察区、分时转弱且主力净流入不再偏强后，再重新判断。"
    elif status == "fee_blocked":
        next_action = "当前不执行反T。费用模型下没有满足最低净收益的回补方案。"
    else:
        first = blocker_details[0] if blocker_details else None
        next_action = f"当前不执行反T。先处理阻断项：{first['label']}，{first['next_step']}" if first else "当前不执行反T，只观察。"
    execution_steps = build_reverse_t_execution_steps(
        status=status,
        trade_shares=trade_shares,
        sell_zone_low=sell_zone_low,
        sell_zone_high=sell_zone_high,
        buyback_max=buyback_max,
        required_gap_pct=required_gap_pct,
        cost_estimate=cost_estimate,
        failure_as_reduction_acceptable=failure_as_reduction_acceptable,
    )

    return {
        "status": status,
        "trade_shares": trade_shares,
        "trade_ratio_pct": None if trade_ratio_pct is None else round(trade_ratio_pct, 2),
        "intraday_range_pct": None if range_pct is None else round(range_pct, 4),
        "range_position_pct": None if range_position is None else round(range_position * 100, 2),
        "sell_zone": [sell_zone_low, sell_zone_high] if sell_zone_low is not None else None,
        "buyback_max_price": buyback_max,
        "min_gap_pct": min_gap_pct,
        "required_gap_pct": required_gap_pct,
        "estimated_cost_reduction_per_share": estimated_cost_reduction,
        "cost_estimate": cost_estimate,
        "cost_model_verified": bool(costs.get("verified")),
        "timeframe_alignment": timeframe.get("alignment"),
        "failure_as_reduction_acceptable": failure_as_reduction_acceptable,
        "failure_result": "未回补可计入计划降仓。" if failure_as_reduction_acceptable else "未回补会形成计划外减仓，执行前必须明确接受。",
        "high_position_ratio_warning": bool(trade_ratio_pct is not None and trade_ratio_pct >= 50),
        "main_flow_confirmation": "wait_for_weakening" if strong_main_inflow else "not_strong_inflow",
        "price_in_sell_zone": bool(
            price is not None and sell_zone_low is not None and sell_zone_high is not None
            and sell_zone_low <= price <= sell_zone_high
        ),
        "blockers": blockers,
        "blocker_details": blocker_details,
        "next_action": next_action,
        "execution_steps": execution_steps,
        "instructions": [
            f"只在价格进入卖出观察区后转弱时卖出{trade_shares}股，不在快速拉升中抢跑。",
            "卖出后仅在价格降至费用模型给出的回补上限且行情未失效时买回同等股数。",
            "若未到回补价，不追价买回；只有事先接受减仓结果时才允许执行反T。",
            "同一股票当日最多执行一轮，成交后记录实际卖价、买价和费用。",
        ],
    }


def build_reduction_plan(
    position: dict[str, Any],
    quote: dict[str, Any],
    *,
    total_assets: float,
    costs: dict[str, float] | None = None,
    max_position_pct: float = 10.0,
    position_limit_verified: bool = False,
) -> dict[str, Any]:
    shares = int(as_float(value_at(position, "entry.shares"), 0.0) or 0.0)
    price = as_float(quote.get("latest_price"))
    if price is None or shares <= 0 or total_assets <= 0:
        return {"status": "unavailable", "reason": "缺少价格、持股数或账户总资产。"}
    market_value = price * shares
    current_pct = market_value / total_assets * 100
    target_value = total_assets * max_position_pct / 100
    if current_pct <= max_position_pct:
        return {"status": "within_limit", "current_position_pct": round(current_pct, 4), "target_position_pct": max_position_pct, "position_limit_verified": position_limit_verified}

    excess_value = market_value - target_value
    reduction_shares = min(shares, math.ceil(excess_value / price / 100) * 100)
    remaining_shares = shares - reduction_shares
    post_pct = remaining_shares * price / total_assets * 100
    reduction_ratio_pct = reduction_shares / shares * 100
    status = "granularity_review" if reduction_ratio_pct >= 40 else "actionable"
    entry_price = as_float(value_at(position, "entry.entry_price"))
    gross_proceeds = price * reduction_shares
    sell_fees = None
    net_proceeds = None
    realized_pnl_after_fees = None
    if costs:
        sell_fees = max(gross_proceeds * costs["commission_rate"], costs["minimum_commission"])
        sell_fees += gross_proceeds * costs["stamp_duty_rate"]
        sell_fees += gross_proceeds * costs["transfer_fee_rate"]
        net_proceeds = gross_proceeds - sell_fees
        if entry_price is not None:
            realized_pnl_after_fees = net_proceeds - entry_price * reduction_shares
    steps = [
        f"目标是把单票仓位从{current_pct:.2f}%降至10%以内；按当前价最少需减少{reduction_shares}股。",
        f"优先分批每次100股，预计剩余{remaining_shares}股、仓位约{post_pct:.2f}%。",
        "计划减仓卖出后不回补；反T属于另一套交易计划，不与本减仓步骤混用。",
        "若价格快速下跌或接近跌停，不把市价单作为默认执行方式，先确认流动性。",
    ]
    if status == "granularity_review":
        steps.insert(1, "最小100股会造成较大比例减仓，不应只为轻微超限机械执行。")
    return {
        "status": status,
        "current_position_pct": round(current_pct, 4),
        "target_position_pct": max_position_pct,
        "position_limit_verified": position_limit_verified,
        "minimum_reduction_shares": reduction_shares,
        "remaining_shares": remaining_shares,
        "post_reduction_position_pct": round(post_pct, 4),
        "reduction_ratio_pct": round(reduction_ratio_pct, 2),
        "estimated_gross_proceeds": round(gross_proceeds, 2),
        "estimated_sell_fees": None if sell_fees is None else round(sell_fees, 2),
        "estimated_net_proceeds": None if net_proceeds is None else round(net_proceeds, 2),
        "estimated_realized_pnl_after_fees": None if realized_pnl_after_fees is None else round(realized_pnl_after_fees, 2),
        "objective": "降低单票风险并释放现金，不以卖出动作本身创造收益。",
        "steps": steps,
    }


def build_action_decision(reverse_t_plan: dict[str, Any], reduction_plan: dict[str, Any]) -> dict[str, Any]:
    if reduction_plan.get("status") == "granularity_review":
        reduction_now = "不因轻微超限机械卖出；100股会使持仓直接减半。"
    elif reduction_plan.get("status") == "actionable":
        reduction_now = f"按降仓计划优先减少{reduction_plan.get('minimum_reduction_shares')}股。"
    else:
        reduction_now = "当前仓位无需按上限规则减仓。"

    status = reverse_t_plan.get("status")
    shares = reverse_t_plan.get("trade_shares") or 100
    if status == "buyback_ready":
        tier = "reverse_buyback_first"
        tier_label = "反T回补优先"
        headline = f"反T回补触发：买回{shares}股"
        now = f"只在回补上限{reverse_t_plan.get('buyback_max_price'):.2f}元及以下买回{shares}股；高于该价不追。"
    elif status == "buyback_wait":
        tier = "place_wait_order"
        tier_label = "可挂单等待"
        headline = f"等待反T回补{shares}股"
        now = "已有开放中的反T卖出腿，当前只等待回补价，不再新增卖出。"
    elif status == "candidate":
        tier = "place_wait_order"
        tier_label = "可挂单等待"
        headline = f"可进入{shares}股反T人工执行候选"
        now = f"只在卖出观察区出现转弱且主力净流入不再偏强时，卖出{shares}股。"
    else:
        tier = "observe_only"
        tier_label = "只观察"
        headline = "现在不做反T"
        now = "保持现有持仓，不卖出、不回补。"
    if reduction_plan.get("status") == "actionable":
        tier = "risk_reduction_first"
        tier_label = "减仓优先"
    elif reduction_plan.get("status") == "granularity_review" and tier == "observe_only":
        tier = "observe_only"
        tier_label = "只观察"

    conditions: list[str] = []
    blockers = reverse_t_plan.get("blockers") or []
    if any("历史不足" in blocker for blocker in blockers):
        conditions.append("等待周线和月线样本达到系统要求后重新评估；新股阶段不预测稳定区间。")
    conditions.extend(
        [
            "实时价格进入系统更新后的卖出观察区，并位于当日振幅上部。",
            "主力净流入占比降到3%以下，且价格不再快速上涨。",
            "扣除双边佣金、印花税和过户费后，预计净收益不少于5元。",
        ]
    )

    effects = [reduction_now]
    cost_estimate = reverse_t_plan.get("cost_estimate") or {}
    if reverse_t_plan.get("sell_zone") and reverse_t_plan.get("buyback_max_price") is not None:
        low, high = reverse_t_plan["sell_zone"]
        effects.append(
            f"参考情景：{low:.2f}-{high:.2f}元卖出{shares}股、{reverse_t_plan['buyback_max_price']:.2f}元及以下回补，"
            f"预计净收益{cost_estimate.get('net_profit', 0):.2f}元。"
        )
    effects.append(reverse_t_plan.get("failure_result") or "未按计划回补会改变原持仓规模。")
    verdict = "buyback_ready" if status == "buyback_ready" else "manual_candidate" if status == "candidate" else "do_not_execute_now"
    return {
        "verdict": verdict,
        "action_tier": tier,
        "action_tier_label": tier_label,
        "headline": headline,
        "what_to_do_now": now,
        "reduction_decision": reduction_now,
        "execute_when": conditions,
        "expected_effects": effects,
        "prediction_note": "这是条件决策，不是对未来价格的保证性预测。",
    }


def apply_state_action_tier(decision: dict[str, Any], state: str, reverse_t_plan: dict[str, Any], reduction_plan: dict[str, Any]) -> dict[str, Any]:
    tier = decision.get("action_tier") or "observe_only"
    label = decision.get("action_tier_label") or "只观察"
    if state == "data_stale":
        tier, label = "data_blocked", "数据不足禁止决策"
    elif reverse_t_plan.get("status") == "buyback_ready":
        tier, label = "reverse_buyback_first", "反T回补优先"
    elif state == "risk_review":
        tier, label = "stop_loss_first", "止损优先"
    elif reduction_plan.get("status") == "actionable":
        tier, label = "risk_reduction_first", "减仓优先"
    elif state in {"no_add_watch", "hold_no_add"}:
        tier, label = "forbid_chase", "禁止追买"
    elif tier == "place_wait_order":
        label = "可挂单等待"
    elif tier == "observe_only":
        label = "只观察"
    return {**decision, "action_tier": tier, "action_tier_label": label}


def analyze_quote(
    position: dict[str, Any],
    quote: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    total_assets: float,
    max_stale_seconds: int,
    costs: dict[str, float],
    max_reverse_t_position_ratio_pct: float,
    now_timestamp: float,
    max_position_pct: float = 10.0,
    warning_position_pct: float | None = None,
    position_limit_verified: bool = False,
) -> dict[str, Any]:
    code = str(value_at(position, "stock.code") or "")
    shares = as_float(value_at(position, "entry.shares"), 0.0) or 0.0
    entry_price = as_float(value_at(position, "entry.entry_price"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    price = as_float(quote.get("latest_price"))
    change_pct = as_float(quote.get("change_pct"))
    quote_timestamp = as_float(quote.get("quote_timestamp"))
    quote_lag_seconds = None if quote_timestamp is None else max(0.0, now_timestamp - quote_timestamp)
    closes = [float(row["close"]) for row in history]
    ma5, ma20 = moving_averages(closes)
    timeframe = multi_timeframe_metrics(history, price)

    market_value = price * shares if price is not None else None
    unrealized_pnl = (price - entry_price) * shares if price is not None and entry_price is not None else None
    return_pct = (price / entry_price - 1) * 100 if price is not None and entry_price else None
    live_position_pct = market_value / total_assets * 100 if market_value is not None and total_assets > 0 else None
    signals: list[dict[str, str]] = []

    if quote_lag_seconds is None or quote_lag_seconds > max_stale_seconds:
        signals.append({"code": "stale_quote", "severity": "block", "message": "行情时间戳缺失或超过允许延迟，暂停盘中判断。"})
    if change_pct is not None and change_pct <= -9.8:
        signals.append({"code": "limit_down_or_near", "severity": "risk", "message": f"当日涨跌幅 {change_pct:.2f}%，接近或达到跌停。"})
    elif change_pct is not None and change_pct <= -5:
        signals.append({"code": "intraday_drop", "severity": "risk", "message": f"当日跌幅 {change_pct:.2f}%，波动风险较高。"})
    if change_pct is not None and change_pct >= 9.8:
        signals.append({"code": "limit_up_or_near", "severity": "warning", "message": f"当日涨跌幅 {change_pct:.2f}%，正T不追价。"})
    if price is not None and ma20 is not None and price < ma20:
        signals.append({"code": "below_ma20", "severity": "warning", "message": f"现价低于20日均线 {ma20:.2f} 元。"})
    if price is not None and ma5 is not None and price < ma5:
        signals.append({"code": "below_ma5", "severity": "info", "message": f"现价低于5日均线 {ma5:.2f} 元。"})
    if position_pct > max_position_pct:
        suffix = "" if position_limit_verified else "；该上限尚未由用户确认。"
        signals.append({"code": "position_limit_exceeded", "severity": "risk", "message": f"原始单票仓位 {position_pct:.2f}% 超过{max_position_pct:.2f}%上限{suffix}"})
    elif warning_position_pct is not None and position_pct > warning_position_pct:
        signals.append({"code": "position_near_limit", "severity": "warning", "message": f"原始单票仓位 {position_pct:.2f}% 超过{warning_position_pct:.2f}%预警线，但未超过{max_position_pct:.2f}%硬上限。"})

    main_flow_ratio = as_float(quote.get("main_net_inflow_ratio_pct"))
    main_flow_amount = as_float(quote.get("main_net_inflow"))
    if main_flow_ratio is not None and change_pct is not None:
        if change_pct > 0 and main_flow_ratio <= -3:
            signals.append({"code": "price_up_main_outflow", "severity": "warning", "message": f"股价上涨但主力净流出占比 {main_flow_ratio:.2f}%，关注冲高分歧。"})
        elif change_pct < 0 and main_flow_ratio >= 3:
            signals.append({"code": "price_down_main_inflow", "severity": "info", "message": f"股价下跌但主力净流入占比 {main_flow_ratio:.2f}%，观察承接是否持续。"})

    signal_codes = {item["code"] for item in signals}
    if "stale_quote" in signal_codes:
        state = "data_stale"
    elif signal_codes & {"limit_down_or_near", "position_limit_exceeded"}:
        state = "risk_review"
    elif "intraday_drop" in signal_codes or "below_ma20" in signal_codes:
        state = "no_add_watch"
    else:
        state = "observe"

    reduction_plan = build_reduction_plan(
        position, quote, total_assets=total_assets, costs=costs,
        max_position_pct=max_position_pct, position_limit_verified=position_limit_verified,
    )
    reverse_t_plan = build_reverse_t_plan(
        position,
        quote,
        stale="stale_quote" in signal_codes,
        costs=costs,
        timeframe=timeframe,
        preferred_reduction_shares=reduction_plan.get("minimum_reduction_shares") if reduction_plan.get("status") == "actionable" else None,
        max_trade_ratio_pct=max_reverse_t_position_ratio_pct,
    )
    positive_t_plan = build_positive_t_plan(
        position,
        quote,
        stale="stale_quote" in signal_codes,
        costs=costs,
    )
    t_closure_performance = build_t_closure_performance(position)
    action_decision = apply_state_action_tier(build_action_decision(reverse_t_plan, reduction_plan), state, reverse_t_plan, reduction_plan)

    return {
        "code": code,
        "name": quote.get("name") or value_at(position, "stock.name"),
        "state": state,
        "quote": {**quote, "quote_lag_seconds": None if quote_lag_seconds is None else round(quote_lag_seconds, 3)},
        "position": {
            "shares": shares,
            "entry_price": entry_price,
            "market_value": None if market_value is None else round(market_value, 2),
            "unrealized_pnl": None if unrealized_pnl is None else round(unrealized_pnl, 2),
            "return_pct": None if return_pct is None else round(return_pct, 4),
            "original_position_pct": position_pct,
            "live_position_pct": None if live_position_pct is None else round(live_position_pct, 4),
        },
        "technicals": {"ma5": ma5, "ma20": ma20, "multi_timeframe": timeframe},
        "capital_flow": {
            "main_net_inflow": main_flow_amount,
            "main_net_inflow_ratio_pct": main_flow_ratio,
            "super_large_net_inflow": as_float(quote.get("super_large_net_inflow")),
            "large_net_inflow": as_float(quote.get("large_net_inflow")),
            "medium_net_inflow": as_float(quote.get("medium_net_inflow")),
            "small_net_inflow": as_float(quote.get("small_net_inflow")),
            "interpretation": "按成交单大小统计的资金流，不代表识别具体机构身份。",
        },
        "signals": signals,
        "latest_reverse_t_closure": value_at(position, "tracking.latest_reverse_t_closure"),
        "latest_positive_t_closure": value_at(position, "tracking.latest_positive_t_closure"),
        "t_closure_performance": t_closure_performance,
        "reverse_t_plan": reverse_t_plan,
        "positive_t_plan": positive_t_plan,
        "reduction_plan": reduction_plan,
        "action_decision": action_decision,
        "guardrails": {"add_allowed": False, "t_trade_allowed": False, "auto_order": False},
    }


def build_snapshot(
    position_paths: list[Path],
    daily_bars: Path,
    *,
    total_assets: float,
    max_stale_seconds: int,
    costs: dict[str, float],
    max_reverse_t_position_ratio_pct: float,
    max_position_pct: float = 10.0,
    warning_position_pct: float | None = None,
    position_limit_verified: bool = False,
) -> dict[str, Any]:
    positions = [load_yaml(path) for path in position_paths]
    codes = [str(value_at(position, "stock.code") or "") for position in positions]
    quotes = {quote["code"]: quote for quote in fetch_realtime_quotes(codes)}
    histories = read_close_history(daily_bars)
    now = datetime.now().astimezone()
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path, position, code in zip(position_paths, positions, codes):
        quote = quotes.get(code)
        if not quote:
            errors.append({"code": code, "message": "本轮未返回行情。"})
            continue
        item = analyze_quote(
            position,
            quote,
            histories.get(code, []),
            total_assets=total_assets,
            max_stale_seconds=max_stale_seconds,
            costs=costs,
            max_reverse_t_position_ratio_pct=max_reverse_t_position_ratio_pct,
            now_timestamp=now.timestamp(),
            max_position_pct=max_position_pct,
            warning_position_pct=warning_position_pct,
            position_limit_verified=position_limit_verified,
        )
        item["position_path"] = str(path)
        items.append(item)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": "eastmoney_public_quote_snapshot",
        "mode": "quasi_realtime_non_guaranteed",
        "interval_note": "公开网页接口无时效和可用性保证，不用于自动下单。",
        "total_assets": total_assets,
        "cost_model": costs,
        "position_limit": {"warning_position_pct": warning_position_pct, "max_position_pct": max_position_pct, "verified": position_limit_verified},
        "position_count": len(position_paths),
        "success_count": len(items),
        "errors": errors,
        "items": items,
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def render_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        "# 持仓准实时监控",
        "",
        f"更新时间：{snapshot['generated_at']}",
        "",
        "公开行情接口无时效保证；本报告只用于监控，不自动下单。",
        "",
        "| 代码 | 名称 | 现价 | 涨跌幅 | 行情延迟 | 持仓收益 | 状态 | 信号 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in snapshot["items"]:
        quote = item["quote"]
        position = item["position"]
        lag = quote.get("quote_lag_seconds")
        signal_text = "、".join(signal["code"] for signal in item["signals"]) or "无"
        return_text = "-" if position["return_pct"] is None else f"{position['return_pct']:.2f}%"
        lines.append(
            f"| {item['code']} | {item['name']} | {quote.get('latest_price', '-')} | "
            f"{quote.get('change_pct', '-')}% | {'-' if lag is None else f'{lag:.1f}s'} | "
            f"{return_text} | "
            f"{item['state']} | {signal_text} |"
        )
    if snapshot["errors"]:
        lines.extend(["", "## 本轮错误", ""])
        lines.extend(f"- {item['code']}: {item['message']}" for item in snapshot["errors"])
    lines.append("")
    return "\n".join(lines)


def state_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        item["code"]: {
            "state": item["state"],
            "signals": sorted(signal["code"] for signal in item["signals"]),
            "reverse_t_status": item.get("reverse_t_plan", {}).get("status"),
            "reverse_t_price_alert": bool(item.get("reverse_t_plan", {}).get("price_in_sell_zone")),
            "positive_t_status": item.get("positive_t_plan", {}).get("status"),
            "positive_t_target_ready": item.get("positive_t_plan", {}).get("status") == "target_sell_ready",
            "latest_reverse_t_closure": (item.get("latest_reverse_t_closure") or {}).get("buy_trade_id"),
            "latest_positive_t_closure": (item.get("latest_positive_t_closure") or {}).get("sell_trade_id"),
            "t_closure_count": item.get("t_closure_performance", {}).get("total_count"),
            "t_closure_total_net_profit": item.get("t_closure_performance", {}).get("total_net_profit"),
            "reduction_status": item.get("reduction_plan", {}).get("status"),
        }
        for item in snapshot["items"]
    }


def append_event(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "generated_at": snapshot["generated_at"],
        "signature": state_signature(snapshot),
        "prices": {item["code"]: item["quote"].get("latest_price") for item in snapshot["items"]},
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def append_flow_history(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "generated_at": snapshot["generated_at"],
        "samples": [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "latest_price": value_at(item, "quote.latest_price"),
                "high": value_at(item, "quote.high"),
                "main_net_inflow": value_at(item, "capital_flow.main_net_inflow"),
                "main_net_inflow_ratio_pct": value_at(item, "capital_flow.main_net_inflow_ratio_pct"),
            }
            for item in snapshot.get("items", [])
        ],
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def ensure_single_instance(pid_path: Path) -> None:
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(existing_pid, 0)
        except (ValueError, ProcessLookupError):
            pid_path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"monitor already running with pid {existing_pid}")
    atomic_write(pid_path, f"{os.getpid()}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor holdings with quasi-real-time public quotes.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--total-assets", type=float, required=True)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--archive-interval", type=float, default=300.0)
    parser.add_argument("--max-stale-seconds", type=int, default=60)
    parser.add_argument("--commission-rate", type=float, default=0.0003, help="Broker commission rate per side; conservative default 0.03%.")
    parser.add_argument("--minimum-commission", type=float, default=5.0, help="Minimum broker commission per order.")
    parser.add_argument("--stamp-duty-rate", type=float, default=0.0005, help="Sell-side stamp duty rate.")
    parser.add_argument("--transfer-fee-rate", type=float, default=0.00001, help="Transfer fee rate per side.")
    parser.add_argument("--minimum-net-profit", type=float, default=5.0, help="Minimum estimated net profit required for a T trade.")
    parser.add_argument("--cost-model-verified", action="store_true", help="Mark cost inputs as verified against the broker statement.")
    parser.add_argument("--max-reverse-t-position-ratio", type=float, default=50.0, help="Maximum percent of a holding used in one reverse T trade.")
    parser.add_argument("--max-position-pct", type=float, default=10.0, help="Maximum single-stock position percent.")
    parser.add_argument("--warning-position-pct", type=float, help="Single-stock position warning percent.")
    parser.add_argument("--position-limit-verified", action="store_true", help="Mark the single-stock position limit as user-confirmed.")
    parser.add_argument("--profile", default="config/investment-profile.yaml", help="Confirmed investment profile YAML.")
    parser.add_argument("--latest-json", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--latest-markdown", default="reports/intraday-monitor.latest.md")
    parser.add_argument("--event-log", default="data/metadata/intraday-monitor.events.jsonl")
    parser.add_argument("--flow-history-log", default="data/metadata/intraday-flow-history.jsonl")
    parser.add_argument("--archive-dir", default="data/metadata/intraday-archive")
    parser.add_argument("--pid-file", default="data/metadata/intraday-monitor.pid")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--iterations", type=int, help="Stop after N iterations; intended for verification.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pid_path = project_path(args.pid_file)
    ensure_single_instance(pid_path)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    position_patterns = [str(project_path(pattern)) for pattern in args.positions]
    paths = expand_position_paths(position_patterns)
    profile_path = project_path(args.profile)
    profile = load_yaml(profile_path) if profile_path.exists() else {}
    max_position_pct = as_float(value_at(profile, "risk.max_position_pct_per_stock"), args.max_position_pct) or args.max_position_pct
    warning_position_pct = as_float(value_at(profile, "risk.warning_position_pct_per_stock"), args.warning_position_pct)
    position_limit_verified = bool(value_at(profile, "risk.position_limits_confirmed")) or args.position_limit_verified
    minimum_net_profit = as_float(value_at(profile, "t_trading.minimum_net_profit_cny"), args.minimum_net_profit) or args.minimum_net_profit
    max_reverse_t_position_ratio = as_float(value_at(profile, "t_trading.max_position_ratio_pct_per_trade"), args.max_reverse_t_position_ratio) or args.max_reverse_t_position_ratio
    costs = {
        "commission_rate": args.commission_rate,
        "minimum_commission": args.minimum_commission,
        "stamp_duty_rate": args.stamp_duty_rate,
        "transfer_fee_rate": args.transfer_fee_rate,
        "minimum_net_profit": minimum_net_profit,
        "verified": args.cost_model_verified,
    }
    latest_json = project_path(args.latest_json)
    latest_markdown = project_path(args.latest_markdown)
    event_log = project_path(args.event_log)
    flow_history_log = project_path(args.flow_history_log)
    archive_dir = project_path(args.archive_dir)
    previous_signature: dict[str, Any] | None = None
    last_archive = 0.0
    iteration = 0
    try:
        while not stop_event.is_set():
            started = time.time()
            try:
                snapshot = build_snapshot(
                    paths,
                    project_path(args.daily_bars),
                    total_assets=args.total_assets,
                    max_stale_seconds=args.max_stale_seconds,
                    costs=costs,
                    max_reverse_t_position_ratio_pct=max_reverse_t_position_ratio,
                    max_position_pct=max_position_pct,
                    warning_position_pct=warning_position_pct,
                    position_limit_verified=position_limit_verified,
                )
                write_json(latest_json, snapshot)
                atomic_write(latest_markdown, render_markdown(snapshot))
                append_flow_history(flow_history_log, snapshot)
                signature = state_signature(snapshot)
                if signature != previous_signature:
                    append_event(event_log, snapshot)
                    previous_signature = signature
                if started - last_archive >= args.archive_interval:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    write_json(archive_dir / f"snapshot-{stamp}.json", snapshot)
                    last_archive = started
                print(f"[{snapshot['generated_at']}] updated {snapshot['success_count']}/{snapshot['position_count']}", flush=True)
            except Exception as exc:
                print(f"[{datetime.now().isoformat(timespec='seconds')}] monitor iteration failed: {exc}", file=sys.stderr, flush=True)
            iteration += 1
            if args.once or (args.iterations is not None and iteration >= args.iterations):
                break
            remaining = max(0.0, args.interval - (time.time() - started))
            if remaining:
                stop_event.wait(remaining)
    finally:
        pid_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
