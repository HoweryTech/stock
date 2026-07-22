#!/usr/bin/env python3
"""Build per-holding realtime decision cards from existing monitoring artifacts."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml, value_at


STATE_LABELS = {
    "market_wait": "非交易时段，等待行情",
    "data_stale": "行情过期，暂停盘中判断",
    "exit_risk_review": "退出风险优先",
    "risk_downgrade_watch": "风险降级观察",
    "reverse_buyback_review": "反T回补复核",
    "data_insufficient": "数据不足，暂不决策",
    "risk_reduction_review": "仓位风险复核",
    "positive_t_watch": "正T观察候选",
    "reverse_t_watch": "反T观察候选",
    "hold_no_add": "持有观察，禁止补仓",
    "observe": "只观察，不操作",
}

ACTION_LABELS = {
    "wait_for_market_session": "等待交易时段刷新",
    "pause_intraday_decision": "暂停实时决策，等待行情刷新",
    "create_exit_or_risk_review": "止损风险优先：不补仓、不做T",
    "watch_risk_downgrade": "风险已降级：观察复核",
    "review_reverse_t_buyback": "反T回补复核：只处理已卖出腿",
    "complete_data_before_decision": "补齐行情、止损和样本数据",
    "review_position_reduction": "仓位超限：核算减仓",
    "watch_positive_t_only": "只进入正T人工观察",
    "watch_reverse_t_only": "只进入反T人工观察",
    "hold_without_adding": "持有观察，不补仓",
    "do_nothing": "不买、不卖，继续监控",
}

HARD_T_BLOCKERS = {"stop_loss_triggered", "near_stop_loss", "limit_down", "stock_suspended"}
DATA_BLOCKERS = {"insufficient_daily_bars", "missing_price_or_stop_loss"}
QUALITY_BLOCKER_STATUSES = {"missing", "insufficient"}
LIMITED_HISTORY_STATUS = "limited_history"
POSITIVE_T_SCORE_THRESHOLD = 65.0
DEFAULT_SUPPLEMENTAL_CAPITAL_POLICY = {
    "supplemental_capital_allowed": True,
    "account_cash_required": False,
    "base_single_add_pct_total_assets": 3.0,
    "strong_single_add_pct_total_assets": 5.0,
    "max_single_add_pct_total_assets": 5.0,
    "max_intraday_add_pct_total_assets": 8.0,
    "max_stock_position_pct_after_add": 12.0,
    "max_added_risk_pct_total_assets": 0.5,
    "min_stop_buffer_pct": 3.0,
    "min_target_gap_pct": 1.2,
}


def supplemental_capital_policy_from_profile(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = dict(DEFAULT_SUPPLEMENTAL_CAPITAL_POLICY)
    source = value_at(profile or {}, "t_trading.supplemental_capital") or {}
    mapping = {
        "supplemental_capital_allowed": "supplemental_capital_allowed",
        "account_cash_required": "account_cash_required",
        "base_single_add_pct_total_assets": "base_single_add_pct_total_assets",
        "strong_single_add_pct_total_assets": "strong_single_add_pct_total_assets",
        "max_single_add_pct_total_assets": "max_single_add_pct_total_assets",
        "max_intraday_add_pct_total_assets": "max_intraday_add_pct_total_assets",
        "max_stock_position_pct_after_add": "max_stock_position_pct_after_add",
        "max_added_risk_pct_total_assets": "max_added_risk_pct_total_assets",
        "min_stop_buffer_pct": "min_stop_buffer_pct",
        "min_target_gap_pct": "min_target_gap_pct",
    }
    for source_key, target_key in mapping.items():
        if source_key not in source:
            continue
        value = source[source_key]
        if isinstance(policy[target_key], bool):
            policy[target_key] = bool(value)
        else:
            numeric = as_float(value)
            if numeric is not None:
                policy[target_key] = numeric
    policy["base_single_add_pct_total_assets"] = min(policy["base_single_add_pct_total_assets"], policy["max_single_add_pct_total_assets"])
    policy["strong_single_add_pct_total_assets"] = min(policy["strong_single_add_pct_total_assets"], policy["max_single_add_pct_total_assets"])
    return policy


def load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else None


def load_minute_bars(cache_dir: Path | None) -> dict[str, list[dict[str, Any]]]:
    if cache_dir is None or not cache_dir.exists():
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        bars = data.get("bars") if isinstance(data, dict) else data
        if isinstance(bars, list):
            result[path.stem] = sorted((bar for bar in bars if isinstance(bar, dict)), key=lambda item: str(item.get("timestamp") or ""))
    return result


def code_from_path(path: str | None) -> str | None:
    if not path:
        return None
    match = re.search(r"(\d{6})(?=\.yaml$|$)", path)
    return match.group(1) if match else None


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def dynamic_price_zone_width(anchor_price: float | None, *, ratio_pct: float = 0.18, min_ticks: int = 1, max_ticks: int = 6, tick: float = 0.01) -> float:
    if anchor_price is None or anchor_price <= 0:
        return round(min_ticks * tick, 2)
    raw_width = anchor_price * ratio_pct / 100
    ticks = math.ceil(raw_width / tick)
    ticks = max(min_ticks, min(max_ticks, ticks))
    return round(ticks * tick, 2)


def dynamic_stop_loss_reference(
    *,
    base_stop: float | None,
    stop_loss_confirmed: bool,
    current: float | None,
    entry: float | None,
    ma20: float | None,
    recent_low: float | None,
    atr_pct: float | None,
    technical_label: str | None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []

    def add(source: str, price: float | None, reason: str) -> None:
        if price is None or price <= 0:
            return
        if current is not None and price >= current:
            price = current * 0.995
        rounded_price = round(price, 4)
        if rounded_price <= 0 or any(item["price"] == rounded_price for item in candidates):
            return
        candidates.append({"source": source, "price": rounded_price, "reason": reason})

    add("draft_or_confirmed", base_stop, "持仓文件中的止损价；若未确认，只能作为草案参考。")
    if entry is not None:
        add("entry_risk_budget", entry * 0.88, "按成本下浮12%估算单笔风险预算。")
    if ma20 is not None:
        add("ma20_buffer", ma20 * 0.98, "按20日均线下方2%估算趋势失效参考。")
    if recent_low is not None:
        add("recent_low_buffer", recent_low * 0.99, "按近期低点下方1%估算结构破位参考。")
    if current is not None and atr_pct is not None:
        atr_buffer_pct = min(10.0, max(3.0, atr_pct * 1.5))
        add("atr_buffer", current * (1 - atr_buffer_pct / 100), f"按ATR波动缓冲{atr_buffer_pct:.2f}%估算动态风控参考。")

    if not candidates:
        return {"price": None, "source": None, "reason": None, "candidates": []}

    candidates = sorted(candidates, key=lambda item: item["price"])
    if stop_loss_confirmed and base_stop is not None:
        selected = next((item for item in candidates if item["source"] == "draft_or_confirmed"), candidates[0])
    elif technical_label in {"bearish", "slightly_bearish"}:
        selected = candidates[-1]
    elif technical_label == "bullish":
        selected = candidates[len(candidates) // 2]
    else:
        selected = candidates[-2] if len(candidates) >= 2 else candidates[-1]

    return {
        "price": selected["price"],
        "source": selected["source"],
        "reason": selected["reason"],
        "candidates": candidates,
    }


def positive_t_score_threshold(confirmation_count: int, technical_operation: dict[str, Any]) -> float:
    threshold = POSITIVE_T_SCORE_THRESHOLD
    tier = str(technical_operation.get("tier") or "")
    if tier == "watch_candidate" and confirmation_count >= 3:
        threshold -= 3.0
    elif tier in {"not_available", "observe_only"}:
        threshold += 3.0
    if confirmation_count < 2:
        threshold += 3.0
    return round(max(60.0, min(72.0, threshold)), 1)


def item_code(item: dict[str, Any], *paths: str) -> str | None:
    for path in paths:
        value = value_at(item, path)
        if value:
            return str(value)
    return code_from_path(str(item.get("path") or ""))


def index_portfolio_check(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {
        str(value_at(item, "result.stock_code") or code_from_path(item.get("path"))): item["result"]
        for item in doc.get("positions", [])
        if value_at(item, "result.stock_code") or code_from_path(item.get("path"))
    }


def index_t_checks(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {
        str(value_at(item, "result.stock_code") or code_from_path(item.get("path"))): item["result"]
        for item in doc.get("items", [])
        if value_at(item, "result.stock_code") or code_from_path(item.get("path"))
    }


def index_action_backtests(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {
        str(value_at(item, "stock.code") or code_from_path(item.get("path"))): item
        for item in doc.get("items", [])
        if value_at(item, "stock.code") or code_from_path(item.get("path"))
    }


def index_simple_items(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {str(item["code"]): item for item in doc.get("items", []) if item.get("code")}


def index_technical_indicators(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return index_simple_items(doc)


def data_quality_status(data_quality: dict[str, Any] | None) -> str | None:
    if not data_quality:
        return None
    return str(data_quality.get("overall_status") or "")


def data_trust_level(data_quality: dict[str, Any] | None) -> str | None:
    if not data_quality:
        return None
    return str(value_at(data_quality, "data_trust.level") or "")


def source_consistency_status(data_quality: dict[str, Any] | None) -> str | None:
    if not data_quality:
        return None
    return str(value_at(data_quality, "source_consistency.status") or "")


def market_session(data_quality: dict[str, Any] | None) -> dict[str, Any]:
    session = value_at(data_quality or {}, "market_session")
    return session if isinstance(session, dict) else {}


def off_session_quote_wait(data_quality: dict[str, Any] | None) -> bool:
    session = market_session(data_quality)
    return bool(session and not session.get("live_quote_required") and value_at(data_quality or {}, "quote.status") == "stale")


def build_decision_mode(data_quality: dict[str, Any] | None) -> dict[str, Any]:
    if not data_quality:
        return {
            "mode": "observe_only",
            "label": "只观察",
            "reason": "未生成数据质量快照，不能支撑盘中交易判断。",
            "next_step": "先刷新完整日内决策链，再看是否进入人工确认。",
            "tradable": False,
        }
    session = market_session(data_quality)
    trust = data_quality.get("data_trust") or {}
    reasons = list(trust.get("reasons") or []) + list(data_quality.get("blockers") or [])
    consistency = data_quality.get("source_consistency") or {}
    if session and session.get("live_quote_required") is False:
        return {
            "mode": "observe_only",
            "label": "只观察",
            "reason": session.get("message") or "当前不在连续盘中执行窗口，行情只用于观察。",
            "next_step": "等进入交易时段后刷新完整日内决策链。",
            "tradable": False,
        }
    if data_quality_status(data_quality) in QUALITY_BLOCKER_STATUSES or data_trust_level(data_quality) == "low":
        return {
            "mode": "blocked",
            "label": "禁止决策",
            "reason": reasons[0] if reasons else "数据可信度不足，不能验证盘中建议。",
            "next_step": "先修复行情、日线、分钟线或价格一致性问题，再重新生成决策卡。",
            "tradable": False,
        }
    if consistency.get("status") == "conflict":
        issues = consistency.get("issues") or []
        return {
            "mode": "blocked",
            "label": "禁止决策",
            "reason": issues[0] if issues else "行情源价格存在冲突。",
            "next_step": "先刷新分钟线和日线缓存，确认东方财富现价与本地缓存一致。",
            "tradable": False,
        }
    if trust.get("intraday_decision_allowed"):
        return {
            "mode": "tradable",
            "label": "可人工确认",
            "reason": "行情、数据质量和交易时段允许进入盘中人工确认。",
            "next_step": "仍只按可操作步骤表执行，真实成交后再写入系统。",
            "tradable": True,
        }
    return {
        "mode": "observe_only",
        "label": "只观察",
        "reason": reasons[0] if reasons else "当前数据只能支撑观察，不能支撑盘中交易动作。",
        "next_step": "等待数据质量提升或重新刷新后再评估。",
        "tradable": False,
    }


def decision_priority(state: str) -> int:
    return {
        "exit_risk_review": 90,
        "reverse_buyback_review": 92,
        "data_stale": 80,
        "market_wait": 75,
        "data_insufficient": 70,
        "risk_reduction_review": 60,
        "risk_downgrade_watch": 55,
        "positive_t_watch": 45,
        "reverse_t_watch": 40,
        "hold_no_add": 50,
        "observe": 10,
    }.get(state, 0)


def near_stop_path3_recovered(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    minute_confirmation: dict[str, Any] | None = None,
) -> bool:
    calculations = (portfolio or {}).get("calculations", {})
    current = as_float(value_at(intraday, "quote.latest_price"))
    stop_loss = as_float(calculations.get("stop_loss_price"))
    stop_loss_confirmed = bool(calculations.get("stop_loss_confirmed", stop_loss is not None))
    ma5 = as_float(value_at(intraday, "technicals.ma5"))
    minute_status = str((minute_confirmation or {}).get("status") or "")
    if current is None or stop_loss is None or not stop_loss_confirmed or current <= stop_loss or minute_status != "confirm":
        return False
    recover_price = stop_loss * 1.012
    if ma5 is not None:
        recover_price = max(recover_price, ma5)
    return current >= recover_price


def indicator_value(period_data: dict[str, Any], path: str) -> float | None:
    current: Any = period_data
    for part in path.split("."):
        current = current.get(part) if isinstance(current, dict) else None
    return as_float(current)


def score_period_indicators(period: str, period_data: dict[str, Any], weight: float) -> tuple[float, list[str]]:
    score = 0.0
    signals: list[str] = []

    macd_histogram = indicator_value(period_data, "macd.histogram")
    macd_dif = indicator_value(period_data, "macd.dif")
    macd_dea = indicator_value(period_data, "macd.dea")
    if macd_histogram is not None:
        if macd_histogram > 0:
            score += 12 * weight
            signals.append(f"{period}: MACD柱为正，动能偏多。")
        elif macd_histogram < 0:
            score -= 12 * weight
            signals.append(f"{period}: MACD柱为负，动能偏弱。")
    if macd_dif is not None and macd_dea is not None:
        if macd_dif > macd_dea:
            score += 6 * weight
        elif macd_dif < macd_dea:
            score -= 6 * weight

    percent_b = indicator_value(period_data, "boll.percent_b")
    if percent_b is not None:
        if percent_b < 0.15:
            score -= 10 * weight
            signals.append(f"{period}: 价格接近或跌破BOLL下轨，趋势承压。")
        elif percent_b > 0.9:
            score -= 6 * weight
            signals.append(f"{period}: 价格接近BOLL上轨，追高风险上升。")
        elif 0.35 <= percent_b <= 0.75:
            score += 4 * weight

    rsi14 = indicator_value(period_data, "rsi.rsi14")
    if rsi14 is not None:
        if rsi14 < 30:
            score -= 8 * weight
            signals.append(f"{period}: RSI14低于30，弱势或超卖。")
        elif rsi14 < 45:
            score -= 4 * weight
        elif rsi14 <= 65:
            score += 5 * weight
        elif rsi14 > 75:
            score -= 6 * weight
            signals.append(f"{period}: RSI14高于75，短线过热。")

    k_value = indicator_value(period_data, "kdj.k")
    d_value = indicator_value(period_data, "kdj.d")
    j_value = indicator_value(period_data, "kdj.j")
    if k_value is not None and d_value is not None:
        score += (3 if k_value >= d_value else -3) * weight
    if j_value is not None:
        if j_value < 0:
            score -= 5 * weight
            signals.append(f"{period}: KDJ-J低于0，短线偏弱。")
        elif j_value > 100:
            score -= 5 * weight
            signals.append(f"{period}: KDJ-J高于100，短线过热。")

    atr_pct = indicator_value(period_data, "atr.atr_pct")
    if atr_pct is not None and atr_pct >= 8:
        score -= 6 * weight
        signals.append(f"{period}: ATR%为{atr_pct:.2f}，波动风险偏高。")

    volume_ratio = indicator_value(period_data, "volume.volume_ratio_20")
    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            score += (6 if (macd_histogram or 0) >= 0 else -3) * weight
            signals.append(f"{period}: 20根量比{volume_ratio:.2f}，成交明显放大。")
        elif volume_ratio < 0.7:
            score -= 3 * weight

    return score, signals


def score_dimension(value: float, *, limit: float = 100.0) -> float:
    return max(-limit, min(limit, value))


def build_technical_dimension_scores(periods: dict[str, Any], weights: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    dimensions = {
        "trend": 0.0,
        "risk": 0.0,
        "reversal": 0.0,
        "volume_confirmation": 0.0,
        "multi_timeframe": 0.0,
    }
    signals: list[str] = []
    trend_directions: list[int] = []
    for period, weight in weights.items():
        data = periods.get(period) or {}
        macd_histogram = indicator_value(data, "macd.histogram")
        macd_dif = indicator_value(data, "macd.dif")
        macd_dea = indicator_value(data, "macd.dea")
        period_trend = 0.0
        if macd_histogram is not None:
            period_trend += 18 if macd_histogram > 0 else -18 if macd_histogram < 0 else 0
        if macd_dif is not None and macd_dea is not None:
            period_trend += 10 if macd_dif > macd_dea else -10 if macd_dif < macd_dea else 0
        percent_b = indicator_value(data, "boll.percent_b")
        if percent_b is not None:
            if 0.45 <= percent_b <= 0.8:
                period_trend += 8
            elif percent_b < 0.2:
                period_trend -= 10
            elif percent_b > 0.95:
                period_trend -= 4
        dimensions["trend"] += period_trend * weight
        if period_trend > 4:
            trend_directions.append(1)
        elif period_trend < -4:
            trend_directions.append(-1)

        atr_pct = indicator_value(data, "atr.atr_pct")
        rsi14 = indicator_value(data, "rsi.rsi14")
        kdj_j = indicator_value(data, "kdj.j")
        period_risk = 0.0
        if atr_pct is not None:
            if atr_pct >= 8:
                period_risk -= 22
            elif atr_pct >= 5:
                period_risk -= 12
            else:
                period_risk += 5
        if rsi14 is not None and (rsi14 > 75 or rsi14 < 25):
            period_risk -= 10
        if percent_b is not None and (percent_b > 1 or percent_b < 0):
            period_risk -= 8
        if kdj_j is not None and (kdj_j > 100 or kdj_j < 0):
            period_risk -= 6
        dimensions["risk"] += period_risk * weight

        period_reversal = 0.0
        if rsi14 is not None:
            if 30 <= rsi14 <= 45:
                period_reversal += 10
            elif rsi14 < 25:
                period_reversal -= 8
            elif rsi14 > 75:
                period_reversal -= 8
        if percent_b is not None and 0.15 <= percent_b <= 0.35:
            period_reversal += 8
        if kdj_j is not None and 0 <= kdj_j <= 30:
            period_reversal += 6
        dimensions["reversal"] += period_reversal * weight

        volume_ratio = indicator_value(data, "volume.volume_ratio_20")
        period_volume = 0.0
        if volume_ratio is not None:
            if volume_ratio >= 1.5:
                period_volume += 16 if (macd_histogram or 0) >= 0 else -8
            elif volume_ratio >= 1.0:
                period_volume += 6
            elif volume_ratio < 0.7:
                period_volume -= 8
        dimensions["volume_confirmation"] += period_volume * weight

    if trend_directions:
        positive = trend_directions.count(1)
        negative = trend_directions.count(-1)
        if positive >= 2:
            dimensions["multi_timeframe"] = 18.0
            signals.append("日/周/月至少两个周期趋势偏多，多周期一致性支持。")
        elif negative >= 2:
            dimensions["multi_timeframe"] = -18.0
            signals.append("日/周/月至少两个周期趋势偏弱，多周期一致性偏空。")
        else:
            dimensions["multi_timeframe"] = 0.0
            signals.append("多周期方向不一致，降低单一周期信号权重。")
    normalized = {key: rounded(score_dimension(value)) for key, value in dimensions.items()}
    return normalized, signals


def technical_dimension_summary(scores: dict[str, float]) -> str:
    trend = as_float(scores.get("trend"), 0.0) or 0.0
    risk = as_float(scores.get("risk"), 0.0) or 0.0
    reversal = as_float(scores.get("reversal"), 0.0) or 0.0
    volume = as_float(scores.get("volume_confirmation"), 0.0) or 0.0
    multi = as_float(scores.get("multi_timeframe"), 0.0) or 0.0
    if risk <= -18 and reversal > 0 and trend <= 0 and volume <= 0:
        return "有一点反转迹象，但风险分明显拖累，趋势和量能还没确认，所以不支持继续追买或继续做T。"
    if risk <= -18:
        return "风险分明显拖累，当前先控制风险，不支持追买、补仓或做T。"
    if trend > 10 and volume > 5 and multi >= 0:
        return "趋势和量能同时转强，多周期没有明显冲突，可进入人工观察候选。"
    if reversal > 6 and trend <= 0:
        return "反转迹象开始出现，但趋势还没确认，只能观察，不能提前买入。"
    if trend <= -10 and volume <= 0:
        return "趋势和量能都偏弱，当前不支持买入或做T。"
    if multi < 0:
        return "多周期一致性偏空，即使短线反弹也需要降低操作级别。"
    return "技术维度没有形成明确共振，维持观察，不因单一指标触发交易。"


def technical_unlock_condition(
    code: str,
    label: str,
    current: Any,
    target: str,
    passed: bool,
    *,
    operator: str | None = None,
    target_value: float | None = None,
) -> dict[str, Any]:
    current_value = rounded(current) if isinstance(current, int | float) else current
    current_number = as_float(current)
    gap: float | None = None
    if current_number is not None and target_value is not None:
        if operator in {">", ">="}:
            gap = max(0.0, target_value - current_number)
        elif operator in {"<", "<="}:
            gap = max(0.0, current_number - target_value)
    gap_text = None
    if passed:
        gap_text = "已达到目标。"
    elif gap is not None:
        gap_text = f"还差 {gap:.1f} 分。"
    hint = None
    if code in {"risk_recovered", "risk_not_heavy"}:
        hint = "需要 ATR、RSI/KDJ 过弱、BOLL 下沿等风险项继续修复。"
    elif code in {"trend_positive", "trend_recovered", "trend_strong"}:
        hint = "需要 MACD、均线位置和多周期趋势继续改善。"
    elif code in {"volume_confirmed", "volume_strong"}:
        hint = "需要成交量/量比放大并配合价格修复。"
    elif code == "multi_not_negative":
        hint = "需要日线、周线、月线方向不再互相拖累。"
    return {
        "code": code,
        "label": label,
        "current": current_value,
        "target": target,
        "passed": passed,
        "operator": operator,
        "target_value": rounded(target_value),
        "gap": rounded(gap),
        "gap_text": gap_text,
        "hint": hint,
    }


def build_technical_operation(technical_assessment: dict[str, Any] | None) -> dict[str, Any]:
    assessment = technical_assessment or {}
    if not assessment.get("available"):
        return {
            "tier": "not_available",
            "tier_label": "技术未确认",
            "allow_buy_watch": False,
            "allow_t_watch": False,
            "reason": "日线、周线、月线技术指标不可用，不能用技术面放开买入或做T。",
            "next_step": "先补齐技术指标数据；补齐前只按止损、仓位和实时行情做风险控制。",
            "unlock_conditions": [
                {"code": "technical_data_available", "label": "技术指标数据", "current": None, "target": "日线/周线/月线指标可用", "passed": False}
            ],
        }
    scores = assessment.get("dimension_scores") or {}
    trend = as_float(scores.get("trend"), 0.0) or 0.0
    risk = as_float(scores.get("risk"), 0.0) or 0.0
    reversal = as_float(scores.get("reversal"), 0.0) or 0.0
    volume = as_float(scores.get("volume_confirmation"), 0.0) or 0.0
    multi = as_float(scores.get("multi_timeframe"), 0.0) or 0.0
    label = str(assessment.get("label") or "")
    summary = str(assessment.get("summary") or technical_dimension_summary(scores))
    risk_recovery_conditions = [
        technical_unlock_condition("risk_recovered", "风险分", risk, "> -18.0", risk > -18, operator=">", target_value=-18.0),
        technical_unlock_condition("trend_positive", "趋势分", trend, "> 0.0", trend > 0, operator=">", target_value=0.0),
        technical_unlock_condition("volume_confirmed", "量能确认", volume, "> 0.0", volume > 0, operator=">", target_value=0.0),
    ]
    watch_conditions = [
        technical_unlock_condition("trend_strong", "趋势分", trend, "> 10.0", trend > 10, operator=">", target_value=10.0),
        technical_unlock_condition("volume_strong", "量能确认", volume, "> 5.0", volume > 5, operator=">", target_value=5.0),
        technical_unlock_condition("multi_not_negative", "多周期一致", multi, ">= 0.0", multi >= 0, operator=">=", target_value=0.0),
        {"code": "technical_label_positive", "label": "技术标签", "current": label or None, "target": "bullish 或 slightly_bullish", "passed": label in {"bullish", "slightly_bullish"}},
    ]
    if risk <= -18 and reversal > 0 and trend <= 0 and volume <= 0:
        return {
            "tier": "risk_control_first",
            "tier_label": "风险优先",
            "allow_buy_watch": False,
            "allow_t_watch": False,
            "reason": summary,
            "next_step": "不追买、不补仓、不做T；等风险分回到 -18 以上，且趋势分和量能确认至少转正后，再重新开放买入/做T观察。",
            "unlock_conditions": risk_recovery_conditions,
        }
    if risk <= -18 or label == "bearish":
        return {
            "tier": "risk_control_first",
            "tier_label": "风险优先",
            "allow_buy_watch": False,
            "allow_t_watch": False,
            "reason": summary,
            "next_step": "技术风险仍明显，先控制下行风险；只有风险分修复后，才允许讨论低吸或做T。",
            "unlock_conditions": risk_recovery_conditions,
        }
    if trend > 10 and volume > 5 and multi >= 0 and label in {"bullish", "slightly_bullish"}:
        return {
            "tier": "watch_candidate",
            "tier_label": "可进观察",
            "allow_buy_watch": True,
            "allow_t_watch": True,
            "reason": summary,
            "next_step": "允许进入人工观察候选，但仍要同时满足实时价格区间、数据质量、止损距离和成交确认。",
            "unlock_conditions": watch_conditions,
        }
    if reversal > 6 and trend <= 0:
        return {
            "tier": "observe_only",
            "tier_label": "只观察",
            "allow_buy_watch": False,
            "allow_t_watch": False,
            "reason": summary,
            "next_step": "反转还没有被趋势确认，先观察；不要提前买入，也不要把反弹当成可执行做T信号。",
            "unlock_conditions": [
                technical_unlock_condition("trend_positive", "趋势分", trend, "> 0.0", trend > 0, operator=">", target_value=0.0),
                technical_unlock_condition("volume_confirmed", "量能确认", volume, "> 0.0", volume > 0, operator=">", target_value=0.0),
                technical_unlock_condition("risk_not_heavy", "风险分", risk, "> -18.0", risk > -18, operator=">", target_value=-18.0),
            ],
        }
    if trend <= -10 and volume <= 0:
        return {
            "tier": "forbid_chase",
            "tier_label": "禁止追买",
            "allow_buy_watch": False,
            "allow_t_watch": False,
            "reason": summary,
            "next_step": "趋势和量能没有配合，继续持有观察；等趋势或量能至少有一项明显修复后再复核。",
            "unlock_conditions": [
                technical_unlock_condition("trend_recovered", "趋势分", trend, "> -10.0", trend > -10, operator=">", target_value=-10.0),
                technical_unlock_condition("volume_confirmed", "量能确认", volume, "> 0.0", volume > 0, operator=">", target_value=0.0),
            ],
        }
    return {
        "tier": "observe_only",
        "tier_label": "只观察",
        "allow_buy_watch": False,
        "allow_t_watch": False,
        "reason": summary,
        "next_step": "技术面没有形成可执行共振，本轮不因单一指标触发交易。",
        "unlock_conditions": watch_conditions,
    }


TECHNICAL_UNLOCK_NEAR_MARGIN = {
    "risk_recovered": 3.0,
    "risk_not_heavy": 3.0,
    "trend_positive": 1.5,
    "volume_confirmed": 1.5,
    "trend_recovered": 2.0,
    "trend_strong": 2.0,
    "volume_strong": 2.0,
    "multi_not_negative": 2.0,
}


def technical_condition_near_unlock(condition: dict[str, Any]) -> bool:
    if condition.get("passed"):
        return False
    current = as_float(condition.get("current"))
    target = as_float(condition.get("target_value"))
    if current is None or target is None:
        return False
    margin = TECHNICAL_UNLOCK_NEAR_MARGIN.get(str(condition.get("code")), 0.0)
    operator = condition.get("operator")
    if operator in {">", ">="} and current < target:
        return (target - current) <= margin
    if operator in {"<", "<="} and current > target:
        return (current - target) <= margin
    return False


def build_technical_unlock_alert(card: dict[str, Any]) -> dict[str, Any] | None:
    operation = ((card.get("decision") or {}).get("technical_operation") or {})
    if operation.get("allow_buy_watch") or operation.get("allow_t_watch"):
        return None
    conditions = operation.get("unlock_conditions") or []
    all_passed = all(condition.get("passed") for condition in conditions) if conditions else False
    active_conditions = conditions if all_passed else [condition for condition in conditions if technical_condition_near_unlock(condition)]
    if not active_conditions:
        return None
    active_gaps = [as_float(condition.get("gap")) for condition in active_conditions]
    active_gaps = [gap for gap in active_gaps if gap is not None]
    min_gap = min(active_gaps) if active_gaps else 0.0
    alert_type = "technical_unlocked" if all_passed else "technical_unlock_near"
    title = "技术面已满足解锁条件" if all_passed else "技术面接近解锁条件"
    waiting = [condition for condition in conditions if not condition.get("passed")]
    next_condition = waiting[0] if waiting else None
    if all_passed:
        message = "技术门禁条件已全部满足，可重新评估正T/反T观察，但仍需通过实时价格、数据质量和止损距离。"
        action_label = "重新评估，不直接交易"
        checklist = [
            "先刷新实时行情和数据质量，确认不是盘后或行情过期。",
            "再查看正T/反T状态是否进入候选；没有候选时继续观察。",
            "最后检查止损距离、回测门禁、费用模型和人工确认，全部通过后才生成交易计划。",
        ]
    elif next_condition:
        message = f"{next_condition.get('label')} 当前 {next_condition.get('current')}，目标 {next_condition.get('target')}；接近后可重新评估。"
        action_label = "接近解锁，只观察"
        checklist = [
            "不买入、不补仓、不做T；接近解锁不等于允许交易。",
            f"优先盯住 {next_condition.get('label')} 是否真正达到 {next_condition.get('target')}。",
            "达到目标后等待下一轮决策卡刷新，再看是否解除技术门禁。",
        ]
    else:
        message = operation.get("next_step") or "技术条件接近解锁，等待下一轮确认。"
        action_label = "继续观察"
        checklist = ["等待下一轮技术指标刷新后再重新评估。"]
    return {
        "code": card.get("code"),
        "name": card.get("name"),
        "type": alert_type,
        "severity": "action" if all_passed else "watch",
        "title": title,
        "message": message,
        "action_label": action_label,
        "checklist": checklist,
        "technical_tier": operation.get("tier"),
        "technical_tier_label": operation.get("tier_label"),
        "min_gap": rounded(min_gap),
        "conditions": conditions,
        "matched_conditions": active_conditions,
        "post_unlock_review": card.get("post_unlock_review_summary") or {},
    }


def technical_post_unlock_checklist(operation: dict[str, Any]) -> list[str]:
    if operation.get("allow_buy_watch") or operation.get("allow_t_watch"):
        return [
            "技术面只允许进入观察，不代表可以直接买入或做T。",
            "继续确认实时价格区间、数据质量、止损距离和分时成交信号。",
            "只有决策卡给出候选状态并通过人工确认后，才允许生成交易计划。",
        ]
    return [
        "接近解锁时仍然不买入、不补仓、不做T。",
        "等所有解锁条件满足后，先刷新决策卡；只把状态升级为“可重新评估”。",
        "重新评估仍要通过实时价格、数据质量、止损距离、回测和费用模型。",
    ]


def review_check(code: str, label: str, status: str, message: str, next_step: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "message": message, "next_step": next_step}


def build_t_performance_gate(intraday: dict[str, Any]) -> dict[str, Any]:
    performance = intraday.get("t_closure_performance") or {}
    total_count = int(as_float(performance.get("total_count"), 0.0) or 0)
    profitable_count = int(as_float(performance.get("profitable_count"), 0.0) or 0)
    loss_count = int(as_float(performance.get("loss_count"), 0.0) or 0)
    total_net_profit = as_float(performance.get("total_net_profit"), 0.0) or 0.0
    win_rate_pct = as_float(performance.get("win_rate_pct"))
    recent_closures = performance.get("recent_closures") if isinstance(performance.get("recent_closures"), list) else []
    consecutive_loss_count = 0
    for closure in reversed(recent_closures):
        if as_float(closure.get("net_profit"), 0.0) <= 0:
            consecutive_loss_count += 1
        else:
            break

    reasons: list[str] = []
    evidence: list[str] = []
    if total_count == 0:
        status = "caution"
        status_label = "暂无做T实盘闭环样本"
        reasons.append("这只股票没有真实做T闭环样本，不能因为模型候选就放大交易。")
        next_step = "只允许最小100股试做或继续观察；完成闭环后再评估是否适合继续做T。"
    elif total_count >= 2 and total_net_profit <= 0:
        status = "blocked"
        status_label = "做T实盘累计未盈利"
        reasons.append(f"已完成 {total_count} 轮做T闭环，累计净收益 {total_net_profit:.2f} 元，不支持继续执行做T候选。")
        next_step = "暂停做T执行；只保留观察、止损或减仓决策，等待后续闭环绩效改善。"
    elif total_count >= 3 and win_rate_pct is not None and win_rate_pct < 50:
        status = "blocked"
        status_label = "做T实盘胜率偏低"
        reasons.append(f"已完成 {total_count} 轮做T闭环，胜率 {win_rate_pct:.2f}% 低于50%。")
        next_step = "暂停做T执行；需要复盘失败原因后再恢复最小股数试做。"
    elif consecutive_loss_count >= 2:
        status = "blocked"
        status_label = "做T最近连续失败"
        reasons.append(f"最近连续 {consecutive_loss_count} 轮做T闭环扣费后未盈利。")
        next_step = "暂停做T至少到下一次技术/量能重新确认；不要继续用做T摊低成本。"
    elif total_count < 3:
        status = "caution"
        status_label = "做T实盘样本较少"
        reasons.append(f"只有 {total_count} 轮做T闭环样本，累计净收益 {total_net_profit:.2f} 元，不能放大单次股数。")
        next_step = "若其他门禁也通过，只允许最小股数执行；继续积累闭环样本。"
    else:
        status = "pass"
        status_label = "做T实盘绩效允许观察"
        reasons.append(f"已完成 {total_count} 轮做T闭环，胜率 {win_rate_pct:.2f}%，累计净收益 {total_net_profit:.2f} 元。")
        next_step = "可以继续按系统候选小额执行；仍需人工确认价格、费用和失败后果。"

    if total_count:
        evidence.append(
            f"做T实盘闭环 {total_count} 轮，盈利 {profitable_count} 轮，未盈利 {loss_count} 轮，"
            f"胜率 {win_rate_pct if win_rate_pct is not None else '-'}%，累计净收益 {total_net_profit:.2f} 元。"
        )
    else:
        evidence.append("做T实盘闭环 0 轮，当前没有可验证的真实绩效。")
    if consecutive_loss_count:
        evidence.append(f"最近连续未盈利闭环 {consecutive_loss_count} 轮。")
    return {
        "status": status,
        "status_label": status_label,
        "total_count": total_count,
        "profitable_count": profitable_count,
        "loss_count": loss_count,
        "win_rate_pct": win_rate_pct,
        "total_net_profit": rounded(total_net_profit),
        "consecutive_loss_count": consecutive_loss_count,
        "reasons": reasons,
        "evidence": evidence,
        "next_step": next_step,
    }


def build_execution_quality_gate(intraday: dict[str, Any]) -> dict[str, Any]:
    summary = intraday.get("execution_quality_summary") or {}
    review_count = int(as_float(summary.get("review_count"), 0.0) or 0)
    average_score = as_float(summary.get("average_score"))
    failed_count = int(as_float(summary.get("failed_count"), 0.0) or 0)
    needs_review_count = int(as_float(summary.get("needs_review_count"), 0.0) or 0)
    poor_score_count = int(as_float(summary.get("poor_score_count"), 0.0) or 0)
    recent_reviews = summary.get("recent_reviews") if isinstance(summary.get("recent_reviews"), list) else []
    reasons: list[str] = []
    evidence: list[str] = []

    if review_count == 0:
        status = "caution"
        status_label = "暂无执行评分"
        reasons.append("这只股票没有真实成交后的执行评分，不能因为模型候选就放大买入或做T。")
        next_step = "只允许最小股数候选或继续观察；先积累成交后复盘评分。"
    elif failed_count:
        status = "blocked"
        status_label = "执行评分失败"
        reasons.append(f"最近 {len(recent_reviews)} 笔评分中有 {failed_count} 笔失败复盘，不支持新的买入或做T候选。")
        next_step = "暂停新的买入/做T；先复盘失败成交的价格、费用和执行原因。"
    elif poor_score_count >= 2 or (average_score is not None and average_score < 70):
        status = "blocked"
        status_label = "执行质量偏低"
        reasons.append(f"最近执行质量均分 {average_score if average_score is not None else '-'}，低分成交 {poor_score_count} 笔。")
        next_step = "暂停新的买入/做T；只保留止损、减仓或观察。"
    elif average_score is None or needs_review_count or average_score < 85:
        status = "caution"
        status_label = "执行质量需复盘"
        reasons.append(f"最近执行质量均分 {average_score if average_score is not None else '-'}，需复盘成交 {needs_review_count} 笔。")
        next_step = "若其他门禁通过，也只允许最小股数候选；先看复盘检查项。"
    else:
        status = "pass"
        status_label = "执行质量良好"
        reasons.append(f"最近执行质量均分 {average_score:.2f}，没有失败复盘。")
        next_step = "可以继续小额候选；仍需人工确认价格、数量和失败后果。"

    if review_count:
        evidence.append(f"执行评分 {review_count} 笔，最近 {len(recent_reviews)} 笔均分 {average_score if average_score is not None else '-'}。")
        latest = summary.get("latest_review") or (recent_reviews[-1] if recent_reviews else {})
        if isinstance(latest, dict):
            evidence.append(f"最近成交评分 {latest.get('score', '-')}，状态 {latest.get('status_label') or latest.get('status') or '-'}。")
    else:
        evidence.append("执行评分 0 笔，当前没有可验证的真实执行质量。")
    return {
        "status": status,
        "status_label": status_label,
        "review_count": review_count,
        "average_score": rounded(average_score),
        "failed_count": failed_count,
        "needs_review_count": needs_review_count,
        "poor_score_count": poor_score_count,
        "reasons": reasons,
        "evidence": evidence,
        "next_step": next_step,
    }


def build_liquidity_activity_gate(
    intraday: dict[str, Any],
    technical_assessment: dict[str, Any] | None = None,
    data_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quote = intraday.get("quote") or {}
    latest_price = as_float(quote.get("latest_price"))
    turnover = as_float(quote.get("turnover"))
    volume = as_float(quote.get("volume"))
    quote_lag = as_float(quote.get("quote_lag_seconds"))
    daily_volume_ratio = as_float(value_at(technical_assessment or {}, "periods.daily.volume_ratio_20"))
    minute_volume_ratio = as_float(value_at(intraday, "positive_t_plan.timing_metrics.volume_ratio"))
    market_live_required = market_session(data_quality).get("live_quote_required")
    reasons: list[str] = []
    evidence: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []

    if latest_price is None or latest_price <= 0:
        blockers.append("缺少可用现价，不能判断盘口成交活跃度。")
    if quote_lag is not None and quote_lag > 60 and market_live_required is not False:
        blockers.append(f"行情延迟 {quote_lag:.1f} 秒，不能用作盘中成交活跃度确认。")
    elif quote_lag is not None:
        evidence.append(f"行情延迟 {quote_lag:.1f} 秒。")

    if turnover is None:
        warnings.append("缺少实时成交额，只能降低活跃度可信度。")
    else:
        evidence.append(f"实时成交额 {money_text(turnover)}。")
        if turnover < 10_000_000:
            blockers.append(f"实时成交额仅 {money_text(turnover)}，主动买入或反T卖出容易出现成交滑点。")
        elif turnover < 30_000_000:
            warnings.append(f"实时成交额 {money_text(turnover)} 偏低，只允许最小股数观察。")

    if volume is not None:
        evidence.append(f"实时成交量 {int(volume)} 手/股单位按行情源口径。")

    if daily_volume_ratio is None:
        warnings.append("缺少日线20根量比，不能确认当前成交是否放大。")
    else:
        evidence.append(f"日线20根量比 {daily_volume_ratio:.2f}。")
        if daily_volume_ratio < 0.55:
            blockers.append(f"日线20根量比 {daily_volume_ratio:.2f} 明显缩量，主动交易信号不可靠。")
        elif daily_volume_ratio < 0.80:
            warnings.append(f"日线20根量比 {daily_volume_ratio:.2f} 偏低，必须等待分钟确认和更小股数。")

    if minute_volume_ratio is not None:
        evidence.append(f"5分钟量比 {minute_volume_ratio:.2f}。")
        if minute_volume_ratio < 0.80:
            warnings.append(f"5分钟量比 {minute_volume_ratio:.2f} 偏低，挂单前需确认价格仍在计划区间。")

    if blockers:
        status = "blocked"
        status_label = "成交活跃度阻断"
        reasons = blockers
        next_step = "不执行主动买入或反T卖出；只保留风控卖出、已打开做T腿闭环或继续观察。"
    elif warnings:
        status = "caution"
        status_label = "成交活跃度谨慎"
        reasons = warnings
        next_step = "若其他门禁通过，只允许最小100股、限价挂单，并在成交后立即写入系统。"
    else:
        status = "pass"
        status_label = "成交活跃度可用"
        reasons = ["成交额、量比和行情延迟没有触发主动交易阻断。"]
        next_step = "继续确认价格区间、数量、费用和执行后果。"

    return {
        "status": status,
        "status_label": status_label,
        "latest_price": rounded(latest_price),
        "turnover": rounded(turnover),
        "volume": rounded(volume),
        "quote_lag_seconds": rounded(quote_lag),
        "daily_volume_ratio_20": rounded(daily_volume_ratio),
        "minute_volume_ratio": rounded(minute_volume_ratio),
        "reasons": reasons,
        "evidence": evidence,
        "next_step": next_step,
        "scope": "只阻断主动买入和反T卖出；止损、减仓和已打开做T腿闭环仍按风控优先处理。",
    }


def build_post_unlock_review(
    technical_operation: dict[str, Any],
    state: str,
    levels: dict[str, Any],
    data_quality: dict[str, Any] | None,
    positive_timing: dict[str, Any],
    minute_confirmation: dict[str, Any],
    reverse_backtest: dict[str, Any] | None,
    intraday: dict[str, Any],
    t_performance_gate: dict[str, Any],
    execution_quality_gate: dict[str, Any],
    liquidity_activity_gate: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    if not (technical_operation.get("allow_buy_watch") or technical_operation.get("allow_t_watch")):
        checks.append(
            review_check(
                "technical_gate",
                "技术门禁",
                "block",
                technical_operation.get("next_step") or "技术门禁未解除。",
                "继续等待解锁条件全部满足；当前不买入、不补仓、不做T。",
            )
        )
        return {
            "status": "technical_locked",
            "status_label": "技术未解锁，只观察",
            "candidate": None,
            "checks": checks,
            "next_step": "接近解锁只代表需要重点观察；技术门禁解除前不进入交易候选。",
        }

    quality_status = data_quality_status(data_quality)
    trust_level = data_trust_level(data_quality)
    if quality_status in QUALITY_BLOCKER_STATUSES or trust_level == "low":
        checks.append(
            review_check(
                "data_quality",
                "数据质量",
                "block",
                f"数据状态 {quality_status or '-'}，可信等级 {trust_level or '-'}。",
                "先修复行情、日线、分钟线或一致性问题，再重新评估。",
            )
        )
    elif data_quality:
        checks.append(review_check("data_quality", "数据质量", "pass", "数据质量没有硬阻断。", "继续检查止损距离和候选状态。"))
    else:
        checks.append(review_check("data_quality", "数据质量", "warn", "未提供数据质量快照。", "刷新完整日内决策链后再进入候选。"))

    minute_status = str((minute_confirmation or {}).get("status") or "not_available")
    minute_label = str((minute_confirmation or {}).get("status_label") or minute_status)
    minute_summary = str((minute_confirmation or {}).get("summary") or "")
    if minute_status == "confirm":
        checks.append(review_check("minute_confirmation", "分钟二次确认", "pass", minute_summary or "分钟级二次确认已通过。", "允许继续复核正T/反T候选。"))
    elif minute_status == "block":
        checks.append(review_check("minute_confirmation", "分钟二次确认", "block", minute_summary or "分钟级二次确认阻断主动动作。", "分钟信号转为确认前，不执行正T、反T或放宽止损。"))
    else:
        checks.append(review_check("minute_confirmation", "分钟二次确认", "warn", f"{minute_label}：{minute_summary or '分钟信号尚未确认。'}", "只观察；等待分钟级确认后才进入人工候选。"))

    current = as_float(levels.get("current_price"))
    near_block = as_float(levels.get("near_stop_block_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    if current is not None and stop_loss is not None and current <= stop_loss:
        checks.append(review_check("stop_loss", "止损距离", "block", f"现价 {current:.2f} 已不高于止损价 {stop_loss:.2f}。", "止损风险优先，不评估买入或做T。"))
    elif current is not None and near_block is not None and current <= near_block:
        checks.append(review_check("stop_buffer", "止损距离", "block", f"现价 {current:.2f} 仍在做T阻断价 {near_block:.2f} 附近。", "等待价格离开止损阻断区后再评估。"))
    else:
        checks.append(review_check("stop_buffer", "止损距离", "pass", "未触发止损或做T阻断价。", "继续检查正T/反T候选。"))

    positive_status = positive_timing.get("status")
    if positive_status == "confirmed":
        checks.append(review_check("positive_timing", "正T分时", "pass", "正T分时评分已确认。", "可进入正T人工候选复核。"))
    elif positive_timing.get("available"):
        checks.append(review_check("positive_timing", "正T分时", "warn", f"正T分时状态为 {positive_status or '-'}。", positive_timing.get("next_action") or "继续等待分时确认。"))
    else:
        checks.append(review_check("positive_timing", "正T分时", "warn", "当前不是正T分时确认状态。", "没有正T候选时不买入。"))

    reverse_status = value_at(intraday, "reverse_t_plan.status")
    reverse_verdict = (reverse_backtest or {}).get("verdict")
    if reverse_status == "candidate" and reverse_verdict in {"pass", "rule_observation_only"}:
        checks.append(review_check("reverse_t", "反T门禁", "pass", "反T候选和回测门禁同时满足。", "可进入反T人工候选复核。"))
    elif reverse_status == "candidate":
        checks.append(review_check("reverse_t", "反T门禁", "block", f"反T候选存在，但回测门禁为 {reverse_verdict or '-'}。", "回测门禁未通过前不执行反T。"))
    else:
        checks.append(review_check("reverse_t", "反T门禁", "warn", f"反T状态为 {reverse_status or '-'}。", "未进入反T候选时不卖出做T。"))

    if t_performance_gate.get("status") == "blocked":
        checks.append(
            review_check(
                "t_performance",
                "做T实盘绩效",
                "block",
                t_performance_gate.get("reasons", ["做T实盘绩效未通过。"])[0],
                t_performance_gate.get("next_step") or "暂停做T执行。",
            )
        )
    elif t_performance_gate.get("status") == "pass":
        checks.append(review_check("t_performance", "做T实盘绩效", "pass", t_performance_gate.get("reasons", ["实盘绩效允许观察。"])[0], "继续人工确认价格、数量、费用和失败后果。"))
    else:
        checks.append(review_check("t_performance", "做T实盘绩效", "warn", t_performance_gate.get("reasons", ["实盘样本不足。"])[0], t_performance_gate.get("next_step") or "只允许最小股数试做或继续观察。"))

    if execution_quality_gate.get("status") == "blocked":
        checks.append(
            review_check(
                "execution_quality",
                "执行质量评分",
                "block",
                execution_quality_gate.get("reasons", ["近期执行评分未通过。"])[0],
                execution_quality_gate.get("next_step") or "暂停新的买入或做T候选。",
            )
        )
    elif execution_quality_gate.get("status") == "pass":
        checks.append(review_check("execution_quality", "执行质量评分", "pass", execution_quality_gate.get("reasons", ["执行质量允许观察。"])[0], "继续人工确认价格、数量、费用和失败后果。"))
    else:
        checks.append(review_check("execution_quality", "执行质量评分", "warn", execution_quality_gate.get("reasons", ["执行评分样本不足。"])[0], execution_quality_gate.get("next_step") or "只允许最小股数试做或继续观察。"))

    if liquidity_activity_gate.get("status") == "blocked":
        checks.append(
            review_check(
                "liquidity_activity",
                "成交活跃度",
                "block",
                liquidity_activity_gate.get("reasons", ["成交活跃度未通过。"])[0],
                liquidity_activity_gate.get("next_step") or "暂停主动买入或反T卖出。",
            )
        )
    elif liquidity_activity_gate.get("status") == "pass":
        checks.append(review_check("liquidity_activity", "成交活跃度", "pass", liquidity_activity_gate.get("reasons", ["成交活跃度可用。"])[0], "继续人工确认价格、数量和费用。"))
    else:
        checks.append(review_check("liquidity_activity", "成交活跃度", "warn", liquidity_activity_gate.get("reasons", ["成交活跃度需要谨慎复核。"])[0], liquidity_activity_gate.get("next_step") or "只允许最小股数试做或继续观察。"))

    blocking = [item for item in checks if item["status"] == "block"]
    minute_ready = any(item["code"] == "minute_confirmation" and item["status"] == "pass" for item in checks)
    positive_ready = any(item["code"] == "positive_timing" and item["status"] == "pass" for item in checks)
    reverse_ready = any(item["code"] == "reverse_t" and item["status"] == "pass" for item in checks)
    if blocking:
        status = "blocked_after_unlock"
        label = "技术已观察，但复核仍阻断"
        candidate = None
        next_step = blocking[0]["next_step"]
    elif minute_ready and (positive_ready or reverse_ready):
        status = "manual_candidate"
        label = "可进入人工候选复核"
        candidate = "positive_t" if positive_ready else "reverse_t"
        next_step = "只生成候选计划，不自动下单；继续人工确认价格、数量、费用和失败后果。"
    else:
        status = "watch_only"
        label = "技术解锁后仍只观察"
        candidate = None
        next_step = "技术面改善后，还需要等待分钟级确认与正T/反T候选结构同时出现。"
    return {"status": status, "status_label": label, "candidate": candidate, "checks": checks, "next_step": next_step}


def build_post_unlock_review_summary(review: dict[str, Any]) -> dict[str, Any]:
    status = str(review.get("status") or "unknown")
    checks = review.get("checks") or []
    blocking_checks = [item for item in checks if item.get("status") == "block"]
    waiting_checks = [item for item in checks if item.get("status") == "warn"]
    passed_checks = [item for item in checks if item.get("status") == "pass"]
    tone = {
        "manual_candidate": "candidate",
        "blocked_after_unlock": "block",
        "technical_locked": "locked",
        "watch_only": "watch",
    }.get(status, "watch")
    if status == "manual_candidate":
        title = "人工候选"
    elif status == "blocked_after_unlock":
        title = "复核阻断"
    elif status == "technical_locked":
        title = "技术未解锁"
    else:
        title = "复核观察"
    return {
        "status": status,
        "status_label": review.get("status_label") or title,
        "title": title,
        "tone": tone,
        "candidate": review.get("candidate"),
        "next_step": review.get("next_step") or "",
        "blocking_checks": [item.get("label") or item.get("code") for item in blocking_checks],
        "waiting_checks": [item.get("label") or item.get("code") for item in waiting_checks],
        "passed_check_count": len(passed_checks),
        "blocked_check_count": len(blocking_checks),
        "waiting_check_count": len(waiting_checks),
    }


def build_post_unlock_review_alert(card: dict[str, Any]) -> dict[str, Any] | None:
    summary = card.get("post_unlock_review_summary") or {}
    status = summary.get("status")
    if status not in {"manual_candidate", "blocked_after_unlock"}:
        return None
    severity = "action" if status == "manual_candidate" else "watch"
    title = "自动复核进入人工候选" if status == "manual_candidate" else "自动复核仍被阻断"
    candidate_label = {"positive_t": "正T", "reverse_t": "反T"}.get(str(summary.get("candidate") or ""), "交易")
    if status == "manual_candidate":
        message = f"{candidate_label}候选已通过自动复核链；仍需人工确认价格、数量、费用和失败后果，不能自动下单。"
        action_label = f"{candidate_label}人工候选"
    else:
        blockers = "、".join(str(item) for item in summary.get("blocking_checks") or [] if item)
        message = f"技术条件已进入观察，但复核链仍被{blockers or '关键条件'}阻断；本轮不买入、不卖出做T。"
        action_label = "复核阻断，只观察"
    return {
        "code": card.get("code"),
        "name": card.get("name"),
        "type": "post_unlock_review",
        "severity": severity,
        "title": title,
        "action_label": action_label,
        "message": message,
        "review": summary,
    }


def build_intraday_trigger_alert(card: dict[str, Any]) -> dict[str, Any] | None:
    manual_plan = card.get("manual_execution_plan") or {}
    price_table = card.get("price_action_table") or {}
    primary = price_table.get("primary_action") or {}
    levels = card.get("price_levels") or {}
    current = as_float(levels.get("current_price"))

    if manual_plan.get("plan_type") == "near_stop_playbook":
        stop_loss = as_float(manual_plan.get("stop_loss_price") or levels.get("stop_loss_price"))
        rebound_zone = manual_plan.get("price_zone") or []
        recover_price = as_float(value_at(manual_plan, "technical_context.recover_price"))
        minute_status = str(value_at(manual_plan, "technical_context.minute_confirmation_status") or (card.get("minute_confirmation") or {}).get("status") or "")
        triggers = []
        if stop_loss is not None:
            triggers.append({"path": "path1_break", "label": "路径1-下破", "condition": f"现价小于等于 {money_text(stop_loss)}", "price": rounded(stop_loss)})
        if isinstance(rebound_zone, list) and len(rebound_zone) >= 2:
            triggers.append({"path": "path2_rebound", "label": "路径2-反抽", "condition": f"价格反抽到 {format_price_zone(rebound_zone)}", "price_zone": rebound_zone})
        if recover_price is not None:
            triggers.append({"path": "path3_recover", "label": "路径3-站稳", "condition": f"价格站上 {money_text(recover_price)} 且分钟确认", "price": rounded(recover_price)})

        active_path = None
        active_label = "等待路径触发"
        severity = "watch"
        action_label = "盯盘中，不提前交易"
        message = manual_plan.get("reason") or "近硬止损预案已生成，等待下破、反抽或站稳任一路径触发。"
        target = "manual-execution-plan"
        if current is not None and stop_loss is not None and current <= stop_loss:
            active_path = "path1_break"
            active_label = "路径1已触发：下破硬止损"
            severity = "action"
            action_label = "进入止损减仓/硬退出复核"
            message = f"现价 {money_text(current)} 已小于等于硬止损 {money_text(stop_loss)}，立刻刷新执行计划；不再等待反T或正T信号。"
        elif current is not None and isinstance(rebound_zone, list) and len(rebound_zone) >= 2:
            low = as_float(rebound_zone[0])
            high = as_float(rebound_zone[1])
            if low is not None and high is not None and low <= current <= high:
                active_path = "path2_rebound"
                active_label = "路径2已触发：进入反抽区"
                severity = "action"
                action_label = f"复核风控减仓 {manual_plan.get('shares') or '-'} 股"
                message = f"现价 {money_text(current)} 已进入反抽区 {format_price_zone(rebound_zone)}；若技术仍未修复，按风控减仓步骤处理。"
        if active_path is None and current is not None and recover_price is not None and current >= recover_price and minute_status == "confirm":
            active_path = "path3_recover"
            active_label = "路径3已触发：站稳并分钟确认"
            severity = "watch"
            action_label = "风险降级观察"
            message = f"现价 {money_text(current)} 已站上 {money_text(recover_price)} 且分钟确认通过，本轮退出风险可降级为观察复核。"
        return {
            "code": card.get("code"),
            "name": card.get("name"),
            "type": "intraday_trigger",
            "severity": severity,
            "title": active_label,
            "action_label": action_label,
            "message": message,
            "current_price": rounded(current),
            "active_path": active_path,
            "target": target,
            "triggers": triggers,
        }

    if primary.get("status") != "ready" or primary.get("action") == "当前动作":
        return None
    return {
        "code": card.get("code"),
        "name": card.get("name"),
        "type": "intraday_trigger",
        "severity": "action",
        "title": f"{primary.get('action') or '价格动作'}已触发",
        "action_label": primary.get("status_label") or "可人工确认",
        "message": (
            f"{primary.get('trigger') or '触发条件已满足'}；"
            f"操作：{primary.get('operation') or '按页面步骤处理'}。"
        ),
        "current_price": rounded(current),
        "active_path": "price_action_ready",
        "target": "action-step-table",
        "primary_action": primary,
    }


def build_technical_assessment(technical_indicators: dict[str, Any] | None) -> dict[str, Any]:
    if not technical_indicators:
        return {"available": False, "score": None, "label": "missing", "signals": [], "periods": {}, "dimension_scores": {}, "dimension_signals": [], "summary": ""}
    periods = technical_indicators.get("periods") or {}
    weights = {"daily": 0.55, "weekly": 0.35, "monthly": 0.10}
    total = 0.0
    signals: list[str] = []
    period_summary: dict[str, Any] = {}
    for period, weight in weights.items():
        period_data = periods.get(period) or {}
        period_score, period_signals = score_period_indicators(period, period_data, weight)
        total += period_score
        signals.extend(period_signals[:3])
        period_summary[period] = {
            "bar_count": period_data.get("bar_count"),
            "latest_trade_date": period_data.get("latest_trade_date"),
            "score": rounded(period_score),
            "macd_histogram": rounded(indicator_value(period_data, "macd.histogram")),
            "boll_percent_b": rounded(indicator_value(period_data, "boll.percent_b")),
            "rsi14": rounded(indicator_value(period_data, "rsi.rsi14")),
            "kdj_j": rounded(indicator_value(period_data, "kdj.j")),
            "atr_pct": rounded(indicator_value(period_data, "atr.atr_pct")),
            "volume_ratio_20": rounded(indicator_value(period_data, "volume.volume_ratio_20")),
        }
    dimension_scores, dimension_signals = build_technical_dimension_scores(periods, weights)
    if total >= 18:
        label = "bullish"
    elif total <= -18:
        label = "bearish"
    elif total >= 6:
        label = "slightly_bullish"
    elif total <= -6:
        label = "slightly_bearish"
    else:
        label = "neutral"
    return {
        "available": True,
        "score": rounded(total),
        "label": label,
        "signals": (signals + dimension_signals)[:8],
        "periods": period_summary,
        "dimension_scores": dimension_scores,
        "dimension_signals": dimension_signals,
        "summary": technical_dimension_summary(dimension_scores),
    }


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def ema_latest(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    value = values[0]
    for item in values[1:]:
        value = item * multiplier + value * (1 - multiplier)
    return value


def simple_rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    avg_gain = average(gains)
    avg_loss = average(losses)
    if avg_gain is None or avg_loss is None:
        return None
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - 100 / (1 + relative_strength)


def rolling_stddev(values: list[float]) -> float | None:
    if not values:
        return None
    mean = average(values)
    if mean is None:
        return None
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def build_minute_confirmation(
    intraday: dict[str, Any],
    minute_bars: list[dict[str, Any]] | None,
    technical_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = minute_analysis_bars(minute_bars or [], 20)
    day_bars = analysis["bars"]
    current_day_count = int(analysis["current_day_bar_count"] or 0)
    warmup_used = bool(analysis["warmup_used"])
    if len(day_bars) < 20:
        return {
            "available": False,
            "status": "not_available",
            "status_label": "分钟数据不足",
            "score": None,
            "summary": f"最新交易日5分钟线只有 {current_day_count} 根，且缓存不足以补齐20根分析样本，不能做分钟级二次确认。",
            "signals": [],
            "blockers": ["5分钟线不足20根，MA20、RSI、BOLL和量能确认不稳定。"],
            "metrics": {"bar_count": len(day_bars), "current_day_bar_count": current_day_count, "warmup_used": warmup_used},
        }

    closes = [as_float(bar.get("close")) for bar in day_bars]
    closes = [value for value in closes if value is not None]
    volumes = [as_float(bar.get("volume")) for bar in day_bars]
    volumes = [value for value in volumes if value is not None]
    if len(closes) < 20:
        return {
            "available": False,
            "status": "not_available",
            "status_label": "分钟价格不足",
            "score": None,
            "summary": f"5分钟有效收盘价只有 {len(closes)} 个，不能做分钟级二次确认。",
            "signals": [],
            "blockers": ["5分钟线价格字段不完整，短线动能、BOLL和均线确认失效。"],
            "metrics": {"bar_count": len(day_bars), "current_day_bar_count": current_day_count, "warmup_used": warmup_used, "valid_close_count": len(closes)},
        }

    current = as_float(value_at(intraday, "quote.latest_price"), closes[-1]) or closes[-1]
    previous = closes[-2] if len(closes) >= 2 else None
    ma5 = average(closes[-5:])
    ma20 = average(closes[-20:])
    std20 = rolling_stddev(closes[-20:])
    boll_upper = None if ma20 is None or std20 is None else ma20 + std20 * 2
    boll_lower = None if ma20 is None or std20 is None else ma20 - std20 * 2
    boll_percent_b = None
    if boll_upper is not None and boll_lower is not None and boll_upper != boll_lower:
        boll_percent_b = (current - boll_lower) / (boll_upper - boll_lower)
    ema12 = ema_latest(closes, 12)
    ema26 = ema_latest(closes, 26)
    macd_hist = None if ema12 is None or ema26 is None else ema12 - ema26
    rsi14 = simple_rsi(closes, 14)
    avg_volume_20 = average(volumes[-20:]) if len(volumes) >= 20 else None
    volume_ratio = None if avg_volume_20 in (None, 0) or not volumes else volumes[-1] / avg_volume_20
    return_3_pct = None if len(closes) < 4 or closes[-4] == 0 else (current / closes[-4] - 1) * 100
    return_6_pct = None if len(closes) < 7 or closes[-7] == 0 else (current / closes[-7] - 1) * 100
    latest_return_pct = None if previous in (None, 0) else (current / previous - 1) * 100
    technical_label = str((technical_assessment or {}).get("label") or "")

    score = 0.0
    signals: list[str] = []
    blockers: list[str] = []
    if warmup_used:
        signals.append(f"早盘仅有 {current_day_count} 根当日5分钟线，已用上一交易日尾盘补足20根预热样本。")
    if ma5 is not None and current >= ma5:
        score += 8
        signals.append("现价站在5分钟MA5上方，短线没有继续破位。")
    elif ma5 is not None:
        score -= 8
        blockers.append("现价低于5分钟MA5，短线仍偏弱。")
    if ma20 is not None and current >= ma20:
        score += 8
        signals.append("现价站在5分钟MA20上方，分时结构可观察。")
    elif ma20 is not None:
        score -= 8
        blockers.append("现价低于5分钟MA20，分时结构尚未修复。")
    if return_3_pct is not None and return_6_pct is not None:
        if return_3_pct > 0 and return_6_pct > 0:
            score += 14
            signals.append(f"近3/6根5分钟涨幅为 {return_3_pct:.2f}% / {return_6_pct:.2f}%，短线动能改善。")
        elif return_3_pct < 0 and return_6_pct < 0:
            score -= 14
            blockers.append(f"近3/6根5分钟涨幅为 {return_3_pct:.2f}% / {return_6_pct:.2f}%，短线动能转弱。")
    if macd_hist is not None:
        if macd_hist > 0:
            score += 12
            signals.append("5分钟MACD动能为正，支持继续观察执行窗口。")
        else:
            score -= 12
            blockers.append("5分钟MACD动能为负，暂不支持主动动作。")
    if rsi14 is not None:
        if 45 <= rsi14 <= 65:
            score += 8
            signals.append(f"5分钟RSI14为 {rsi14:.1f}，处于健康确认区。")
        elif 35 <= rsi14 < 45:
            score += 2
            signals.append(f"5分钟RSI14为 {rsi14:.1f}，弱修复但还不强。")
        elif rsi14 < 35:
            score -= 8
            blockers.append(f"5分钟RSI14为 {rsi14:.1f}，短线承接偏弱。")
        elif rsi14 > 75:
            score -= 6
            blockers.append(f"5分钟RSI14为 {rsi14:.1f}，短线过热，避免追价。")
    if boll_percent_b is not None:
        if 0.25 <= boll_percent_b <= 0.80:
            score += 6
            signals.append(f"5分钟BOLL位置 {boll_percent_b:.2f}，没有贴下轨破位或上轨过热。")
        elif boll_percent_b < 0.15:
            score -= 8
            blockers.append(f"5分钟BOLL位置 {boll_percent_b:.2f}，接近下轨弱势区。")
        elif boll_percent_b > 0.95:
            score -= 5
            blockers.append(f"5分钟BOLL位置 {boll_percent_b:.2f}，接近上轨，追价性价比不足。")
    if volume_ratio is not None:
        if volume_ratio >= 1.3 and (latest_return_pct or 0) >= 0:
            score += 8
            signals.append(f"5分钟量比 {volume_ratio:.2f} 且价格未下跌，成交确认偏正。")
        elif volume_ratio >= 1.3 and (latest_return_pct or 0) < 0:
            score -= 8
            blockers.append(f"5分钟量比 {volume_ratio:.2f} 但价格下跌，放量偏空。")
        elif volume_ratio < 0.6:
            score -= 4
            blockers.append(f"5分钟量比 {volume_ratio:.2f}，成交不足。")
    if technical_label in {"bearish", "slightly_bearish"} and score > 0:
        score -= 6
        blockers.append("大周期技术面偏弱，分钟确认分需要打折。")

    if score >= 18:
        status = "confirm"
        status_label = "分钟确认"
        summary = "5分钟动能、均线、BOLL/RSI和量能整体支持把价格区间作为可观察执行窗口。"
    elif score <= -18:
        status = "block"
        status_label = "分钟阻断"
        summary = "5分钟动能或结构明显偏弱，暂不支持主动买入、卖出做T或放宽止损。"
    else:
        status = "watch"
        status_label = "分钟观察"
        summary = "5分钟信号不够一致，只能作为观察证据，不能单独触发交易动作。"

    return {
        "available": True,
        "status": status,
        "status_label": status_label,
        "score": rounded(score),
        "summary": summary,
        "signals": signals[:5],
        "blockers": blockers[:5],
        "latest_timestamp": day_bars[-1].get("timestamp"),
        "metrics": {
            "bar_count": len(day_bars),
            "current_day_bar_count": current_day_count,
            "warmup_bar_count": int(analysis["warmup_bar_count"] or 0),
            "warmup_used": warmup_used,
            "last_close": rounded(current),
            "ma5": rounded(ma5),
            "ma20": rounded(ma20),
            "return_3_pct": rounded(return_3_pct),
            "return_6_pct": rounded(return_6_pct),
            "latest_return_pct": rounded(latest_return_pct),
            "macd_hist": rounded(macd_hist),
            "rsi14": rounded(rsi14),
            "boll_percent_b": rounded(boll_percent_b),
            "volume_ratio": rounded(volume_ratio),
            "technical_label": technical_label or None,
        },
    }


def latest_day_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not bars:
        return []
    latest_date = str(bars[-1].get("timestamp") or "")[:10]
    return [bar for bar in bars if str(bar.get("timestamp") or "").startswith(latest_date)]


def minute_analysis_bars(bars: list[dict[str, Any]], min_count: int = 20) -> dict[str, Any]:
    ordered = sorted((bar for bar in bars if isinstance(bar, dict)), key=lambda item: str(item.get("timestamp") or ""))
    day_bars = latest_day_bars(ordered)
    if len(day_bars) >= min_count:
        return {
            "bars": day_bars,
            "current_day_bar_count": len(day_bars),
            "warmup_bar_count": 0,
            "warmup_used": False,
            "latest_date": str(day_bars[-1].get("timestamp") or "")[:10] if day_bars else "",
        }
    warmup_bars = ordered[-min_count:] if len(ordered) >= min_count else ordered
    return {
        "bars": warmup_bars,
        "current_day_bar_count": len(day_bars),
        "warmup_bar_count": max(0, len(warmup_bars) - len(day_bars)),
        "warmup_used": len(warmup_bars) >= min_count and len(day_bars) > 0,
        "latest_date": str(day_bars[-1].get("timestamp") or "")[:10] if day_bars else "",
    }


def positive_t_blocker(code: str, label: str, current: str, reason: str, next_step: str) -> dict[str, str]:
    return {
        "code": code,
        "label": label,
        "current": current,
        "reason": reason,
        "next_step": next_step,
    }


def build_positive_timing(
    intraday: dict[str, Any],
    t_check: dict[str, Any] | None,
    minute_bars: list[dict[str, Any]] | None,
    technical_assessment: dict[str, Any] | None = None,
    technical_operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not t_check or t_check.get("conclusion") != "positive_t_candidate":
        return {"available": False, "status": "not_applicable", "score": None, "signals": [], "blockers": [], "next_action": "当前不是正T候选，不评估正T买入腿。", "buy_zone": None, "target_sell_zone": None}
    analysis = minute_analysis_bars(minute_bars or [], 20)
    day_bars = analysis["bars"]
    current_day_count = int(analysis["current_day_bar_count"] or 0)
    warmup_used = bool(analysis["warmup_used"])
    if len(day_bars) < 20:
        return {
            "available": False,
            "status": "insufficient",
            "score": None,
            "signals": [f"最新交易日5分钟线数量 {current_day_count} 少于20，且缓存不足以补齐20根分析样本，不能确认正T买点。"],
            "blockers": [
                positive_t_blocker(
                    "minute_sample_insufficient",
                    "分钟线样本",
                    f"{len(day_bars)} / 20 根",
                    "5分钟线不足，MA20、RSI、量比和回踩结构还不能稳定计算。",
                    "继续等待分钟线缓存补齐到至少20根，再重新判断正T。",
                )
            ],
            "next_action": "不买入。等待5分钟线样本补齐后系统自动重新评分。",
            "buy_zone": None,
            "target_sell_zone": None,
        }
    closes = [as_float(bar.get("close")) for bar in day_bars]
    closes = [value for value in closes if value is not None]
    highs = [as_float(bar.get("high")) for bar in day_bars[-20:]]
    highs = [value for value in highs if value is not None]
    lows = [as_float(bar.get("low")) for bar in day_bars[-20:]]
    lows = [value for value in lows if value is not None]
    volumes = [as_float(bar.get("volume")) for bar in day_bars]
    volumes = [value for value in volumes if value is not None]
    if len(closes) < 20:
        return {
            "available": False,
            "status": "insufficient",
            "score": None,
            "signals": ["5分钟线价格字段不足，不能确认正T买点。"],
            "blockers": [
                positive_t_blocker(
                    "minute_price_insufficient",
                    "分钟线价格",
                    f"{len(closes)} / 20 个有效收盘价",
                    "分钟线价格字段不完整，无法确认MA、RSI和回踩区间。",
                    "刷新分钟线缓存；价格字段补齐前不执行正T。",
                )
            ],
            "next_action": "不买入。先修复分钟线数据完整性。",
            "buy_zone": None,
            "target_sell_zone": None,
        }
    current = as_float(value_at(intraday, "quote.latest_price"), closes[-1]) or closes[-1]
    ma5 = average(closes[-5:])
    ma20 = average(closes[-20:])
    previous_ma5 = average(closes[-6:-1]) if len(closes) >= 6 else None
    previous_close = closes[-2] if len(closes) >= 2 else None
    latest_open = as_float(day_bars[-1].get("open"))
    latest_close = closes[-1]
    recent_high = max(highs) if highs else None
    recent_low = min(lows) if lows else None
    pullback_pct = None if recent_high in (None, 0) else (current / recent_high - 1) * 100
    rebound_pct = None if previous_close in (None, 0) else (current / previous_close - 1) * 100
    ema12 = ema_latest(closes, 12)
    ema26 = ema_latest(closes, 26)
    macd_hist = None if ema12 is None or ema26 is None else ema12 - ema26
    rsi14 = simple_rsi(closes, 14)
    avg_volume_20 = average(volumes[-20:]) if len(volumes) >= 20 else None
    volume_ratio = None if avg_volume_20 in (None, 0) or not volumes else volumes[-1] / avg_volume_20
    main_flow = as_float(value_at(intraday, "capital_flow.main_net_inflow_ratio_pct"))
    technical_label = str((technical_assessment or {}).get("label") or "")
    technical_operation = technical_operation or build_technical_operation(technical_assessment)
    technical_supported = technical_operation.get("tier") == "not_available" or bool(technical_operation.get("allow_buy_watch"))
    recaptured_ma5 = (
        current is not None
        and ma5 is not None
        and previous_close is not None
        and previous_ma5 is not None
        and previous_close < previous_ma5
        and current >= ma5
    )
    bullish_volume_candle = (
        latest_open is not None
        and latest_close > latest_open
        and volume_ratio is not None
        and volume_ratio >= 1.05
    )
    flow_confirmed = main_flow is not None and main_flow >= 1.0

    score = 0.0
    signals: list[str] = []
    if warmup_used:
        signals.append(f"早盘仅有 {current_day_count} 根当日5分钟线，已用上一交易日尾盘补足20根预热样本。")
    if ma20 is not None and current >= ma20:
        score += 15
        signals.append(f"现价仍在5分钟MA20上方，分时趋势未破。")
    elif ma20 is not None:
        score -= 15
        signals.append("现价跌破5分钟MA20，正T买入腿暂缓。")
    if ma5 is not None and ma20 is not None and ma5 >= ma20:
        score += 10
        signals.append("5分钟MA5不低于MA20，短线结构可观察。")
    if pullback_pct is not None:
        if -2.5 <= pullback_pct <= -0.3:
            score += 20
            signals.append(f"相对近20根5分钟高点回落 {pullback_pct:.2f}%，具备低吸观察空间。")
        elif pullback_pct > -0.3:
            score -= 8
            signals.append("回踩幅度不足，当前不追价做正T。")
        elif pullback_pct < -4.0:
            score -= 12
            signals.append("分时回落过深，可能不是正T低吸而是转弱。")
    if rebound_pct is not None and rebound_pct > 0:
        score += 10
        signals.append(f"最新5分钟价格较上一根回升 {rebound_pct:.2f}%。")
    if recaptured_ma5:
        score += 15
        signals.append("最新5分钟重新站上MA5，回踩后出现修复确认。")
    if bullish_volume_candle:
        score += 10
        signals.append("最新5分钟为放量阳线，低吸买点获得成交确认。")
    if macd_hist is not None:
        if macd_hist > 0:
            score += 12
            signals.append("5分钟MACD短长均线差为正，动能有修复。")
        else:
            score -= 8
            signals.append("5分钟MACD仍偏弱，等待动能修复。")
    if rsi14 is not None:
        if 40 <= rsi14 <= 65:
            score += 12
            signals.append(f"5分钟RSI14为 {rsi14:.1f}，处于可观察区。")
        elif rsi14 < 35:
            score -= 10
            signals.append(f"5分钟RSI14为 {rsi14:.1f}，短线过弱。")
        elif rsi14 > 75:
            score -= 8
            signals.append(f"5分钟RSI14为 {rsi14:.1f}，不适合追买。")
    if volume_ratio is not None:
        if 0.9 <= volume_ratio <= 2.5:
            score += 8
            signals.append(f"5分钟量比 {volume_ratio:.2f}，成交确认适中。")
        elif volume_ratio < 0.7:
            score -= 5
            signals.append(f"5分钟量比 {volume_ratio:.2f}，承接不足。")
        elif volume_ratio > 3.0:
            score -= 5
            signals.append(f"5分钟量比 {volume_ratio:.2f}，波动过热，不追价。")
    if main_flow is not None:
        if main_flow > 0:
            score += 8
            signals.append(f"主力净流入占比 {main_flow:.2f}%，资金流未明显拖累。")
        elif main_flow <= -3:
            score -= 12
            signals.append(f"主力净流入占比 {main_flow:.2f}%，资金流偏弱。")

    score = max(0.0, min(100.0, score))
    confirmation_count = sum(bool(item) for item in (recaptured_ma5, bullish_volume_candle, flow_confirmed))
    if not technical_supported:
        signals.append(f"技术操作档位为 {technical_operation.get('tier_label') or technical_label}，不允许仅凭分时信号做正T。")
    if confirmation_count < 2:
        signals.append("确认信号少于2个，需要等待MA5修复、放量阳线或主力净流入进一步共振。")
    buy_high = min(value for value in [current, ma5] if value is not None) if ma5 is not None else current
    buy_low_candidates = [buy_high * 0.988]
    if ma20 is not None:
        buy_low_candidates.append(ma20)
    if recent_low is not None:
        buy_low_candidates.append(recent_low)
    buy_low = min(buy_high, max(buy_low_candidates))
    target_low = max(current, buy_high * 1.012)
    threshold = positive_t_score_threshold(confirmation_count, technical_operation)
    target_high = target_low + dynamic_price_zone_width(target_low)
    status = "confirmed" if score >= threshold and confirmation_count >= 2 and technical_supported else "watch"
    blockers: list[dict[str, str]] = []
    if score < threshold:
        blockers.append(
            positive_t_blocker(
                "score_below_threshold",
                "分时评分",
                f"{score:.1f} / {threshold}",
                "分时趋势、回踩幅度、动能、量能和资金流的综合分还没有达到买入确认线。",
                f"继续观察，只有评分达到动态确认线 {threshold:.0f} 分及以上才允许进入正T买入区间。",
            )
        )
    if confirmation_count < 2:
        blockers.append(
            positive_t_blocker(
                "confirmation_insufficient",
                "确认信号",
                f"{confirmation_count} / 2",
                "MA5修复、放量阳线、主力净流入三项里至少需要两项共振，当前确认不足。",
                "等待重新站上MA5、最新5分钟放量阳线，或主力净流入转正后再评估。",
            )
        )
    if not technical_supported:
        blockers.append(
            positive_t_blocker(
                "technical_operation_blocked",
                "技术操作档位",
                technical_operation.get("tier_label") or technical_label,
                technical_operation.get("reason") or "技术操作档位不支持正T买入腿。",
                technical_operation.get("next_step") or "等待技术背景恢复后再重新评估正T。",
            )
        )
    next_action = "可进入正T买入观察区；按买入区、目标卖出区和资金上限执行，不长期摊低成本。"
    if blockers:
        first = blockers[0]
        next_action = f"当前不买入。先处理阻断项：{first['label']}，{first['next_step']}"
    return {
        "available": True,
        "status": status,
        "score": rounded(score),
        "threshold": threshold,
        "base_threshold": POSITIVE_T_SCORE_THRESHOLD,
        "latest_timestamp": day_bars[-1].get("timestamp"),
        "buy_zone": [rounded(buy_low), rounded(buy_high)] if status == "confirmed" else None,
        "target_sell_zone": [rounded(target_low), rounded(target_high)] if status == "confirmed" else None,
        "signals": signals[:8],
        "blockers": blockers,
        "next_action": next_action,
        "metrics": {
            "bar_count": len(day_bars),
            "current_day_bar_count": current_day_count,
            "warmup_bar_count": int(analysis["warmup_bar_count"] or 0),
            "warmup_used": warmup_used,
            "ma5": rounded(ma5),
            "ma20": rounded(ma20),
            "pullback_pct": rounded(pullback_pct),
            "rebound_pct": rounded(rebound_pct),
            "macd_hist": rounded(macd_hist),
            "rsi14": rounded(rsi14),
            "volume_ratio": rounded(volume_ratio),
            "main_flow_ratio_pct": rounded(main_flow),
            "recaptured_ma5": recaptured_ma5,
            "bullish_volume_candle": bullish_volume_candle,
            "flow_confirmed": flow_confirmed,
            "confirmation_count": confirmation_count,
            "dynamic_score_threshold": threshold,
            "technical_label": technical_label or None,
            "technical_supported": technical_supported,
            "technical_operation_tier": technical_operation.get("tier"),
            "technical_operation_label": technical_operation.get("tier_label"),
        },
    }


def choose_state(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    technical_assessment: dict[str, Any] | None = None,
    t_performance_gate: dict[str, Any] | None = None,
    execution_quality_gate: dict[str, Any] | None = None,
    minute_confirmation: dict[str, Any] | None = None,
) -> tuple[str, str]:
    signal_codes = {item.get("code") for item in intraday.get("signals", [])}
    portfolio_action_codes = {item.get("code") for item in (portfolio or {}).get("actions", [])}
    t_blockers = {item.get("code") for item in (t_check or {}).get("blockers", [])}
    quality_status = data_quality_status(data_quality)
    trust_level = data_trust_level(data_quality)
    quote_wait = off_session_quote_wait(data_quality)
    states: list[tuple[str, str]] = []
    reverse_status = str(value_at(intraday, "reverse_t_plan.status") or "")
    has_open_reverse_leg = bool(value_at(intraday, "reverse_t_plan.open_reverse_t_leg"))

    if trust_level == "low":
        states.append(("data_insufficient", "数据可信等级为低，不能验证盘中建议。"))
    if "stale_quote" in signal_codes:
        if quote_wait:
            states.append(("market_wait", "当前不在实时交易窗口，等待下一次行情刷新后再判断。"))
        else:
            states.append(("data_stale", "盘中行情过期，不能给实时执行建议。"))
    if quality_status == "stale":
        if quote_wait:
            states.append(("market_wait", "当前不在实时交易窗口，上一撮合时段行情只可观察。"))
        else:
            states.append(("data_stale", "行情、日线或分钟线存在过期数据，不能给实时执行建议。"))
    if quality_status in QUALITY_BLOCKER_STATUSES:
        states.append(("data_insufficient", "行情、日线或分钟线数据缺失或样本不足，不能验证盘中建议。"))
    recovered_from_near_stop = near_stop_path3_recovered(intraday, portfolio, minute_confirmation)
    current = as_float(value_at(intraday, "quote.latest_price"))
    stop_loss = as_float(value_at(portfolio or {}, "calculations.stop_loss_price"))
    confirmed_value = value_at(portfolio or {}, "calculations.stop_loss_confirmed")
    stop_loss_confirmed = bool(stop_loss is not None if confirmed_value is None else confirmed_value)
    live_stop_loss_triggered = current is not None and stop_loss is not None and stop_loss_confirmed and current <= stop_loss
    stale_stop_loss_signal = bool(portfolio_action_codes & {"stop_loss_triggered"} and not live_stop_loss_triggered)
    hard_exit_triggered = bool(live_stop_loss_triggered or t_blockers & {"limit_down", "stock_suspended"})
    near_stop_triggered = bool(stale_stop_loss_signal or t_blockers & {"near_stop_loss"})
    if hard_exit_triggered or (near_stop_triggered and not recovered_from_near_stop):
        states.append(("exit_risk_review", "触发或逼近硬风控，退出风险优先于做T。"))
    if near_stop_triggered and recovered_from_near_stop:
        states.append(("risk_downgrade_watch", "已站上恢复价且分钟确认，退出风险降级为观察复核。"))
    if has_open_reverse_leg or reverse_status in {"buyback_ready", "buyback_wait"}:
        states.append(("reverse_buyback_review", "已有反T卖出腿，当前优先复核是否按回补价买回。"))
    hard_data_blockers = t_blockers & DATA_BLOCKERS
    if quality_status == LIMITED_HISTORY_STATUS:
        hard_data_blockers.discard("insufficient_daily_bars")
    if hard_data_blockers:
        states.append(("data_insufficient", "日线、止损或样本不足，不能验证交易环境。"))
    if portfolio_action_codes & {"stock_position_limit_exceeded", "industry_position_limit_exceeded", "total_position_limit_exceeded"}:
        states.append(("risk_reduction_review", "持仓或组合仓位超限，需要先复核降仓。"))
    if value_at(intraday, "reduction_plan.status") == "actionable":
        states.append(("risk_reduction_review", "实时市值测算显示可复核降仓。"))
    positive_candidate = bool(t_check and t_check.get("conclusion") == "positive_t_candidate")
    reverse_candidate = reverse_status == "candidate"
    if positive_candidate:
        states.append(("positive_t_watch", "日线环境进入正T观察候选。"))
    if reverse_candidate:
        states.append(("reverse_t_watch", "盘中价格进入反T观察候选。"))
    if (t_performance_gate or {}).get("status") == "blocked" and (positive_candidate or reverse_candidate):
        states.append(("hold_no_add", "做T实盘绩效门禁未通过，本轮暂停正T/反T执行。"))
    if (execution_quality_gate or {}).get("status") == "blocked" and (positive_candidate or reverse_candidate):
        states.append(("hold_no_add", "近期执行质量评分未通过，本轮暂停买入/做T执行。"))
    if (technical_assessment or {}).get("label") == "bearish":
        states.append(("hold_no_add", "多周期技术指标偏弱，禁止补仓和做T，先观察风险变化。"))
    if signal_codes & {"below_ma20", "intraday_drop"}:
        states.append(("hold_no_add", "盘中走势偏弱，禁止补仓。"))
    if reverse_backtest and reverse_backtest.get("verdict") in {"insufficient_sample", "weak_result"} and value_at(intraday, "reverse_t_plan.status") == "candidate":
        states.append(("hold_no_add", "反T历史回测未通过，只能观察不能执行。"))

    if not states:
        return "observe", "没有硬风险或明确做T结构，继续观察。"
    return max(states, key=lambda item: decision_priority(item[0]))


def price_levels(
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    intraday: dict[str, Any],
    reverse_forecast: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    technical_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calculations = (portfolio or {}).get("calculations", {})
    t_calculations = (t_check or {}).get("calculations", {})
    current = as_float(value_at(intraday, "quote.latest_price"))
    stop_loss = as_float(calculations.get("stop_loss_price"))
    stop_loss_confirmed = bool(calculations.get("stop_loss_confirmed", stop_loss is not None))
    stop_loss_confirmation_status = calculations.get("stop_loss_confirmation_status")
    stop_loss_confirmation_label = calculations.get("stop_loss_confirmation_label")
    ma20 = as_float(value_at(intraday, "technicals.ma20") or t_calculations.get("ma_mid"))
    recent_low = as_float(t_calculations.get("recent_low"))
    entry_price = as_float(value_at(intraday, "position.entry_price"))
    dynamic_stop = dynamic_stop_loss_reference(
        base_stop=stop_loss,
        stop_loss_confirmed=stop_loss_confirmed,
        current=current,
        entry=entry_price,
        ma20=ma20,
        recent_low=recent_low,
        atr_pct=as_float(value_at(technical_assessment or {}, "periods.daily.atr_pct")),
        technical_label=str((technical_assessment or {}).get("label") or ""),
    )
    warning_pct = as_float(calculations.get("near_stop_warning_pct"), 3.0) or 3.0
    block_pct = as_float(t_calculations.get("near_stop_block_pct"), 1.0) or 1.0
    near_warning_price = None
    near_block_price = None
    if stop_loss is not None and stop_loss_confirmed:
        near_warning_price = stop_loss / (1 - warning_pct / 100)
        near_block_price = stop_loss / (1 - block_pct / 100)
    forecast_sell_zone = value_at(reverse_forecast or {}, "predicted_sell_zone")
    forecast_buyback = as_float(value_at(reverse_forecast or {}, "predicted_buyback_max_price"))
    forecast_as_of = value_at(reverse_forecast or {}, "as_of")
    forecast_date = parse_date(forecast_as_of)
    decision_date = parse_date(generated_at)
    forecast_stale = bool(forecast_date and decision_date and forecast_date != decision_date)
    intraday_sell_zone = value_at(intraday, "reverse_t_plan.sell_zone")
    intraday_buyback = as_float(value_at(intraday, "reverse_t_plan.buyback_max_price"))
    current_reference_zone = value_at(intraday, "reverse_t_plan.current_reference_zone")
    current_reference_buyback = as_float(value_at(intraday, "reverse_t_plan.current_reference_buyback_max_price"))
    has_forecast = reverse_forecast is not None and not forecast_stale
    sell_zone = forecast_sell_zone if has_forecast else None
    buyback = forecast_buyback if has_forecast else None
    if has_forecast and forecast_sell_zone:
        zone_source = "forecast"
    elif reverse_forecast is not None and forecast_stale:
        zone_source = "forecast_stale"
    elif has_forecast:
        zone_source = "forecast_unavailable"
    else:
        zone_source = None
    return {
        "current_price": rounded(current),
        "stop_loss_price": rounded(stop_loss),
        "stop_loss_confirmed": stop_loss_confirmed,
        "stop_loss_confirmation_status": stop_loss_confirmation_status,
        "stop_loss_confirmation_label": stop_loss_confirmation_label,
        "dynamic_stop_loss_price": rounded(as_float(dynamic_stop.get("price"))),
        "dynamic_stop_loss_source": dynamic_stop.get("source"),
        "dynamic_stop_loss_reason": dynamic_stop.get("reason"),
        "dynamic_stop_loss_candidates": dynamic_stop.get("candidates") or [],
        "near_stop_warning_price": rounded(near_warning_price),
        "near_stop_block_price": rounded(near_block_price),
        "ma5": rounded(as_float(value_at(intraday, "technicals.ma5") or t_calculations.get("ma_short"))),
        "ma20": rounded(ma20),
        "recent_high": rounded(as_float(t_calculations.get("recent_high"))),
        "recent_low": rounded(recent_low),
        "reverse_t_sell_zone": sell_zone,
        "reverse_t_buyback_max_price": rounded(buyback),
        "reverse_t_intraday_reference_zone": intraday_sell_zone,
        "reverse_t_intraday_reference_buyback_max_price": rounded(intraday_buyback),
        "reverse_t_current_reference_zone": current_reference_zone,
        "reverse_t_current_reference_buyback_max_price": rounded(current_reference_buyback),
        "reverse_t_current_reference_status": value_at(intraday, "reverse_t_plan.current_reference_status"),
        "reverse_t_current_reference_source": value_at(intraday, "reverse_t_plan.current_reference_source"),
        "reverse_t_current_reference_reason": value_at(intraday, "reverse_t_plan.current_reference_reason"),
        "reverse_t_current_reference_required_gap_pct": rounded(as_float(value_at(intraday, "reverse_t_plan.current_reference_required_gap_pct"))),
        "reverse_t_zone_source": zone_source,
        "reverse_t_forecast_as_of": forecast_as_of,
        "reverse_t_forecast_stale": forecast_stale,
        "reverse_t_reach_probability_pct": rounded(as_float(value_at(reverse_forecast or {}, "reach_probability_pct"))),
        "reverse_t_roundtrip_probability_pct": rounded(as_float(value_at(reverse_forecast or {}, "roundtrip_probability_pct"))),
        "reverse_t_joint_probability_pct": rounded(as_float(value_at(reverse_forecast or {}, "joint_roundtrip_probability_pct"))),
    }


def build_evidence(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    action_backtest: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    reverse_forecast: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    technical_assessment: dict[str, Any] | None = None,
    positive_timing: dict[str, Any] | None = None,
    minute_confirmation: dict[str, Any] | None = None,
    t_performance_gate: dict[str, Any] | None = None,
    execution_quality_gate: dict[str, Any] | None = None,
    liquidity_activity_gate: dict[str, Any] | None = None,
) -> list[str]:
    evidence: list[str] = []
    if liquidity_activity_gate:
        evidence.append(f"[成交活跃度] {liquidity_activity_gate.get('status_label') or liquidity_activity_gate.get('status')}")
        evidence.extend(f"[成交活跃度] {item}" for item in (liquidity_activity_gate.get("evidence") or [])[:3])
        for reason in (liquidity_activity_gate.get("reasons") or [])[:2]:
            evidence.append(f"[成交活跃度复核] {reason}")
    if execution_quality_gate:
        evidence.append(f"[执行质量评分] {execution_quality_gate.get('status_label') or execution_quality_gate.get('status')}")
        evidence.extend(f"[执行质量] {item}" for item in (execution_quality_gate.get("evidence") or [])[:2])
    if t_performance_gate:
        evidence.append(f"[做T实盘绩效] {t_performance_gate.get('status_label') or t_performance_gate.get('status')}")
        evidence.extend(f"[做T实盘] {item}" for item in (t_performance_gate.get("evidence") or [])[:2])
    if positive_timing and positive_timing.get("available"):
        evidence.append(
            f"[正T分时评分] {positive_timing.get('status')} · score={positive_timing.get('score')} / {positive_timing.get('threshold')}"
        )
        for signal in positive_timing.get("signals", [])[:3]:
            evidence.append(f"[正T分时] {signal}")
    if minute_confirmation:
        evidence.append(
            f"[分钟二次确认] {minute_confirmation.get('status_label') or minute_confirmation.get('status')} · "
            f"score={minute_confirmation.get('score') if minute_confirmation.get('score') is not None else '-'}"
        )
        for signal in (minute_confirmation.get("signals") or [])[:2]:
            evidence.append(f"[分钟确认] {signal}")
        for blocker in (minute_confirmation.get("blockers") or [])[:2]:
            evidence.append(f"[分钟阻断] {blocker}")
    if technical_assessment and technical_assessment.get("available"):
        evidence.append(
            f"[技术指标] {technical_assessment.get('label')} · score={technical_assessment.get('score')}"
        )
        for signal in technical_assessment.get("signals", [])[:4]:
            evidence.append(f"[技术指标] {signal}")
    if data_quality:
        trust = data_quality.get("data_trust") or {}
        trust_text = trust.get("label") or trust.get("level") or "-"
        evidence.append(f"[数据质量] {data_quality.get('status_label') or data_quality.get('overall_status')} · {trust_text}")
        session = market_session(data_quality)
        if session:
            evidence.append(f"[交易时段] {session.get('label') or session.get('phase')} · {session.get('message') or '-'}")
        consistency = data_quality.get("source_consistency") or {}
        if consistency:
            evidence.append(f"[数据一致性] {consistency.get('status') or '-'} · 阈值 {consistency.get('max_diff_pct', '-')}%")
            for issue in consistency.get("issues", [])[:2]:
                evidence.append(f"[数据源冲突] {issue}")
        for reason in trust.get("reasons", [])[:2]:
            evidence.append(f"[可信等级] {reason}")
        for message in (data_quality.get("warnings") or [])[:2]:
            evidence.append(f"[数据质量过期] {message}")
    for signal in intraday.get("signals", [])[:3]:
        evidence.append(f"[盘中:{signal.get('code')}] {signal.get('message')}")
    for item in (portfolio or {}).get("warnings", [])[:2]:
        if item.get("code") != "unknown_position_status":
            evidence.append(f"[持仓:{item.get('code')}] {item.get('message')}")
    for item in (portfolio or {}).get("actions", [])[:2]:
        evidence.append(f"[持仓:{item.get('code')}] {item.get('message')}")
    if t_check:
        evidence.append(f"[做T日线] market={t_check.get('market_setup')} execution={t_check.get('conclusion')}")
        for blocker in t_check.get("blockers", [])[:2]:
            evidence.append(f"[做T阻断:{blocker.get('code')}] {blocker.get('message')}")
    if action_backtest:
        evidence.append(
            f"[动作矩阵回测] 弱规则 {action_backtest.get('weak_rule_count', 0)} 条；"
            f"最弱状态 {value_at(action_backtest, 'weakest_state.state') or '-'} "
            f"{value_at(action_backtest, 'weakest_state.average_return_pct') if value_at(action_backtest, 'weakest_state.average_return_pct') is not None else '-'}%"
        )
    if reverse_backtest:
        evidence.append(f"[反T回测] {reverse_backtest.get('verdict_label') or reverse_backtest.get('verdict')}")
    if reverse_forecast:
        evidence.append(f"[反T预测] {reverse_forecast.get('status_label') or reverse_forecast.get('status')}")
        sell_zone = reverse_forecast.get("predicted_sell_zone")
        buyback = reverse_forecast.get("predicted_buyback_max_price")
        if sell_zone and buyback is not None:
            evidence.append(
                f"[反T预测区间] 卖出观察 {sell_zone[0]:.2f}-{sell_zone[1]:.2f}，"
                f"回补上限 {buyback:.2f}；到达概率 {reverse_forecast.get('reach_probability_pct', '-')}%，"
                f"回补概率 {reverse_forecast.get('roundtrip_probability_pct', '-')}%。"
            )
        elif sell_zone:
            evidence.append(
                f"[反T预测区间] 卖出观察 {sell_zone[0]:.2f}-{sell_zone[1]:.2f}；"
                "当前费用模型未给出可执行回补上限。"
            )
    return evidence or ["暂无足够证据，按只观察处理。"]


def build_blockers(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    technical_assessment: dict[str, Any] | None = None,
    t_performance_gate: dict[str, Any] | None = None,
    execution_quality_gate: dict[str, Any] | None = None,
    liquidity_activity_gate: dict[str, Any] | None = None,
    daily_trade_rhythm: dict[str, Any] | None = None,
    minute_confirmation: dict[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if data_quality_status(data_quality) in QUALITY_BLOCKER_STATUSES:
        blockers.extend(data_quality.get("blockers") or [])
    if data_trust_level(data_quality) == "low":
        blockers.extend((data_quality.get("data_trust") or {}).get("reasons") or [])
    if source_consistency_status(data_quality) == "conflict":
        blockers.extend((data_quality or {}).get("source_consistency", {}).get("issues") or [])
    blockers.extend(signal.get("message") for signal in intraday.get("signals", []) if signal.get("severity") in {"block", "risk"})
    recovered_from_near_stop = near_stop_path3_recovered(intraday, portfolio, minute_confirmation)
    for item in (t_check or {}).get("blockers", []):
        if data_quality_status(data_quality) == LIMITED_HISTORY_STATUS and item.get("code") == "insufficient_daily_bars":
            continue
        if recovered_from_near_stop and item.get("code") == "near_stop_loss":
            continue
        blockers.append(item.get("message"))
    if (technical_assessment or {}).get("label") == "bearish":
        blockers.append("多周期技术指标偏弱，本轮禁止补仓和做T。")
    if (t_performance_gate or {}).get("status") == "blocked":
        blockers.extend((t_performance_gate or {}).get("reasons") or ["做T实盘绩效门禁未通过。"])
    if (execution_quality_gate or {}).get("status") == "blocked":
        blockers.extend((execution_quality_gate or {}).get("reasons") or ["近期执行质量评分未通过。"])
    if (liquidity_activity_gate or {}).get("status") == "blocked":
        blockers.extend((liquidity_activity_gate or {}).get("reasons") or ["成交活跃度不足，主动交易暂停。"])
    if (daily_trade_rhythm or {}).get("status") in {"risk_exit_cooldown", "trade_frequency_caution"}:
        blockers.extend((daily_trade_rhythm or {}).get("blockers") or [(daily_trade_rhythm or {}).get("next_action")])
    if reverse_backtest and reverse_backtest.get("verdict") != "pass":
        blockers.append(reverse_backtest.get("verdict_label") or "反T历史回测未通过。")
    return [item for item in blockers if item]


def build_next_step(state: str, action_backtest: dict[str, Any] | None, levels: dict[str, Any] | None = None) -> str:
    levels = levels or {}
    if state == "data_stale":
        return "先刷新准实时监控快照；行情恢复前不做盘中动作。"
    if state == "market_wait":
        return "等待进入连续交易时段后刷新行情；未刷新前只观察，不做盘中动作。"
    if state == "exit_risk_review":
        current = as_float(levels.get("current_price"))
        stop_loss = as_float(levels.get("stop_loss_price"))
        near_block = as_float(levels.get("near_stop_block_price"))
        if current is not None and stop_loss is not None and current <= stop_loss:
            return f"现价 {current:.2f} 已不高于止损价 {stop_loss:.2f}：优先按止损退出处理；禁止补仓、禁止做T摊低成本。"
        if current is not None and near_block is not None and current <= near_block:
            return f"现价 {current:.2f} 已进入做T阻断区 {near_block:.2f} 附近：先做退出/减仓复核，不做T；若跌破止损价立即转止损退出。"
        return "退出风险优先：本轮不买、不做T；先核对止损价、持仓数量和可卖数量，再决定是否生成止损退出或降风险卖出计划。"
    if state == "risk_downgrade_watch":
        return "路径3站稳并分钟确认，退出风险降级为观察复核；本轮不主动卖出，也不补仓、不做T。"
    if state == "reverse_buyback_review":
        buyback = as_float(levels.get("reverse_t_buyback_max_price"))
        if buyback is None:
            buyback = as_float(levels.get("reverse_t_intraday_reference_buyback_max_price"))
        if buyback is not None:
            return f"已有开放反T卖出腿；只在 {buyback:.2f} 元及以下买回同等股数。若决定不回补，则把该卖出腿按减仓/退出后果管理。"
        return "已有开放反T卖出腿；先复核回补上限和卖出数量。若决定不回补，则把该卖出腿按减仓/退出后果管理。"
    if state == "data_insufficient":
        return "本轮不交易；先修复数据阻断，再重新生成实时决策卡。"
    if state == "risk_reduction_review":
        return "仓位风险优先：先计算卖出100股后的仓位和盈亏影响；若仍超限，再按100股整数倍生成降仓计划。"
    if state == "positive_t_watch":
        return "只进入正T人工观察；先定义买入价、卖出价、失败后是否接受加仓和最大新增仓位。"
    if state == "reverse_t_watch":
        return "只进入反T人工观察；必须通过5分钟回测、费用模型、分时转弱和人工确认。"
    if state == "hold_no_add":
        if action_backtest and (action_backtest.get("weak_rule_count") or 0) > 0:
            return "动作矩阵存在弱规则，先复核规则和仓位，不新增交易。"
        return "继续持有观察，不补仓；等待趋势或风险信号改变后复核。"
    return "本轮不买不卖，继续监控关键价位。"


def whole_lot_shares(value: Any) -> int | None:
    shares = as_float(value)
    if shares is None or shares <= 0:
        return None
    if shares < 100:
        return int(shares)
    return int(shares // 100 * 100)


def money_text(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f} 元"


def floor_lot_from_cash(cash: float | None, price: float | None) -> int:
    if cash is None or price in (None, 0):
        return 0
    return max(0, int(cash / price // 100 * 100))


def estimate_trade_fees(side: str, price: float | None, shares: int | float | None) -> dict[str, float | None]:
    if price is None or shares is None or price <= 0 or shares <= 0:
        return {"commission": None, "stamp_duty": None, "transfer_fee": None, "total_fees": None}
    amount = float(price) * float(shares)
    commission = max(amount * 0.0003, 5.0)
    stamp_duty = amount * 0.0005 if side == "sell" else 0.0
    transfer_fee = amount * 0.00001
    return {
        "commission": rounded(commission),
        "stamp_duty": rounded(stamp_duty),
        "transfer_fee": rounded(transfer_fee),
        "total_fees": rounded(commission + stamp_duty + transfer_fee),
    }


def risk_exit_reduce_shares(current_shares: int) -> int:
    if current_shares <= 0:
        return 0
    if current_shares <= 100:
        return current_shares
    half = whole_lot_shares(current_shares * 0.5) or 100
    return min(current_shares, max(100, half))


def build_stop_loss_risk_plan(
    levels: dict[str, Any],
    position: dict[str, Any],
    technical_assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    current = as_float(levels.get("current_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    stop_loss_confirmed = bool(levels.get("stop_loss_confirmed", stop_loss is not None))
    current_shares = int(as_float(position.get("shares"), 0.0) or 0)
    if current is None or stop_loss is None or not stop_loss_confirmed or current > stop_loss or current_shares <= 0:
        return {"applicable": False, "status": "not_applicable", "steps": []}

    scores = (technical_assessment or {}).get("dimension_scores") or {}
    trend = as_float(scores.get("trend"), 0.0) or 0.0
    risk = as_float(scores.get("risk"), 0.0) or 0.0
    reversal = as_float(scores.get("reversal"), 0.0) or 0.0
    volume = as_float(scores.get("volume_confirmation"), 0.0) or 0.0
    label = str((technical_assessment or {}).get("label") or "")
    drawdown_from_stop_pct = (stop_loss - current) / stop_loss * 100 if stop_loss else 0.0
    reduce_shares = risk_exit_reduce_shares(current_shares)

    if current_shares <= 100 or (drawdown_from_stop_pct >= 3.0 and trend <= -15 and volume <= 0):
        plan_type = "hard_exit"
        status = "ready_for_manual_confirm"
        status_label = "硬退出计划"
        action_label = "硬退出"
        shares = current_shares
        min_price = current
        price_zone = [rounded(current), rounded(current)]
        reason = "跌破止损后继续偏弱，且风险已经超出可等待范围。"
    elif drawdown_from_stop_pct <= 2.0 and reversal >= 6 and risk > -30:
        plan_type = "rebound_reduce"
        status = "wait_rebound_reduce"
        status_label = "等反弹减仓计划"
        action_label = "反弹减仓"
        shares = reduce_shares
        rebound_low = max(current * 1.008, min(stop_loss, current * 1.015))
        rebound_high = max(rebound_low, stop_loss * 1.01)
        price_zone = [rounded(rebound_low), rounded(rebound_high)]
        min_price = as_float(price_zone[0])
        reason = "跌破止损但有反转迹象，不追杀；等反弹到压力区先降低风险仓位。"
    else:
        plan_type = "risk_reduce"
        status = "ready_for_manual_confirm"
        status_label = "止损减仓计划"
        action_label = "止损减仓"
        shares = reduce_shares
        min_price = current
        price_zone = [rounded(current), rounded(current)]
        reason = "跌破硬止损后先减掉约半仓风险，不默认一次清仓。"

    entry_price = as_float(position.get("entry_price"))
    fee_price = min_price or current
    fees = estimate_trade_fees("sell", fee_price, shares)
    estimated_cash = fee_price * shares - float(fees["total_fees"] or 0.0) if fee_price is not None else None
    realized_pnl = None
    if fee_price is not None and entry_price is not None:
        realized_pnl = (fee_price - entry_price) * shares - float(fees["total_fees"] or 0.0)
    post_trade_shares = max(0, current_shares - shares)
    loss_text = money_text(realized_pnl)
    stop_text = money_text(stop_loss)
    zone_text = format_price_zone(price_zone)
    failure_conditions = [
        f"价格没有到 {zone_text} 时，不为了补仓或做T扩大风险。",
        "卖出后若马上反弹，不追高买回；等待系统用剩余仓位重新评估。",
        "卖出后若继续跌破止损，不做正T摊低成本，下一轮继续按风险仓位处理。",
    ]
    if plan_type == "rebound_reduce":
        steps = [
            f"先不补仓、不做T；只等待价格反弹到 {zone_text}。",
            f"进入区间后打开券商交易软件，进入“交易/卖出”，卖出数量输入 {shares} 股。",
            f"卖出价格输入 {zone_text} 内的限价；低于 {money_text(min_price)} 不卖这笔反弹减仓单。",
            f"成交前确认这是风控减仓，不是反T卖出腿；成交后在本系统写入卖出成交。",
            f"成交后剩余约 {post_trade_shares} 股，继续观察是否重新站回止损价 {stop_text}。",
        ]
        post_trade_plan = f"若反弹减仓成交，先把风险仓位降到 {post_trade_shares} 股；后续只有重新站回 {stop_text} 且量能确认，才重新评估做T或加仓。"
    else:
        steps = [
            "打开券商交易软件，进入“交易/卖出”，不要选择买入或融资加仓。",
            f"卖出数量输入 {shares} 股；这不是默认清仓，成交后预计剩余 {post_trade_shares} 股。",
            f"卖出价格输入现价附近限价；若价格继续快速下破 {stop_text}，不等待做T信号。",
            f"提交前核对方向为“卖出”、数量为 {shares} 股；预计本次实现盈亏约 {loss_text}，预估费用约 {money_text(as_float(fees.get('total_fees')))}。",
            "成交后立即在本系统写入卖出成交并刷新建议；刷新前不新增买入。",
        ]
        post_trade_plan = f"成交后系统按剩余 {post_trade_shares} 股重算止损和做T资格；若仍低于止损且技术未修复，下一轮继续降风险，而不是补仓摊低成本。"

    return {
        "applicable": True,
        "candidate": "risk_exit",
        "plan_type": plan_type,
        "status": status,
        "status_label": status_label,
        "action_label": action_label,
        "side": "sell",
        "side_label": "卖出",
        "trade_intent": "risk_exit_reduce" if plan_type != "hard_exit" else "risk_exit_full",
        "shares": shares,
        "price_zone": price_zone,
        "target_zone": None,
        "min_price": rounded(min_price),
        "stop_loss_price": rounded(stop_loss),
        "current_price": rounded(current),
        "estimated_amount": rounded(estimated_cash),
        "estimated_fees": fees,
        "estimated_realized_pnl": rounded(realized_pnl),
        "post_trade_shares": post_trade_shares,
        "technical_context": {
            "label": label,
            "trend": rounded(trend),
            "risk": rounded(risk),
            "reversal": rounded(reversal),
            "volume_confirmation": rounded(volume),
            "drawdown_from_stop_pct": rounded(drawdown_from_stop_pct),
        },
        "reason": reason,
        "failure_conditions": failure_conditions,
        "steps": steps,
        "post_trade_plan": post_trade_plan,
    }


def build_near_stop_risk_plan(
    state: str,
    levels: dict[str, Any],
    position: dict[str, Any],
    technical_assessment: dict[str, Any] | None,
    minute_confirmation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = as_float(levels.get("current_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    near_block = as_float(levels.get("near_stop_block_price"))
    stop_loss_confirmed = bool(levels.get("stop_loss_confirmed", stop_loss is not None))
    current_shares = int(as_float(position.get("shares"), 0.0) or 0)
    if (
        state not in {"exit_risk_review", "risk_downgrade_watch"}
        or current is None
        or stop_loss is None
        or not stop_loss_confirmed
        or current <= stop_loss
        or current_shares <= 0
    ):
        return {"applicable": False, "status": "not_applicable", "steps": []}

    scores = (technical_assessment or {}).get("dimension_scores") or {}
    trend = as_float(scores.get("trend"), 0.0) or 0.0
    risk = as_float(scores.get("risk"), 0.0) or 0.0
    reversal = as_float(scores.get("reversal"), 0.0) or 0.0
    volume = as_float(scores.get("volume_confirmation"), 0.0) or 0.0
    label = str((technical_assessment or {}).get("label") or "")
    distance_pct = (current - stop_loss) / stop_loss * 100 if stop_loss else None
    shares = risk_exit_reduce_shares(current_shares)
    ma5 = as_float(levels.get("ma5"))
    ma20 = as_float(levels.get("ma20"))
    rebound_low = stop_loss * 1.01
    resistance_candidates = [value for value in (ma5, ma20) if value is not None and value >= rebound_low]
    rebound_high = min(resistance_candidates) if resistance_candidates else stop_loss * 1.025
    rebound_high = max(rebound_low, rebound_high)
    rebound_zone = [rounded(rebound_low), rounded(rebound_high)]
    recover_price = stop_loss * 1.012
    if ma5 is not None:
        recover_price = max(recover_price, ma5)
    failure_zone = [rounded(stop_loss), rounded(stop_loss)]
    stop_text = money_text(stop_loss)
    near_text = money_text(near_block)
    rebound_text = format_price_zone(rebound_zone)
    minute_status = str((minute_confirmation or {}).get("status") or "not_available")
    minute_label = str((minute_confirmation or {}).get("status_label") or minute_status)
    distance_text = "-" if distance_pct is None else f"{distance_pct:.2f}%"
    post_trade_shares = max(0, current_shares - shares)
    recovered = near_stop_path3_recovered({"quote": {"latest_price": current}, "technicals": {"ma5": ma5}}, {"calculations": {"stop_loss_price": stop_loss, "stop_loss_confirmed": stop_loss_confirmed}}, minute_confirmation)
    reason = (
        f"现价距离硬止损仅 {distance_text}，先停止补仓和做T；"
        "按下破、反抽、站稳三条路径等待盘中确认。"
    )
    if recovered:
        reason = "路径3已触发：价格站上恢复价且分钟确认通过，本轮退出风险降级为观察复核；不主动卖出。"
    steps = [
        f"路径1-下破：现价小于等于硬止损 {stop_text} 时，立刻转入止损减仓/硬退出计划，不再等待反T或正T信号。",
        f"路径2-反抽：若价格反抽到 {rebound_text} 但技术仍未修复，卖出 {shares} 股风控减仓；成交后预计剩余 {post_trade_shares} 股。",
        f"路径3-站稳：若价格重新站上 {money_text(recover_price)}，且分钟二次确认为“分钟确认”，本轮退出风险才降级为观察复核，不主动卖出。",
        f"在以上任一路径触发前，不补仓、不做T；低于做T阻断价 {near_text} 时，所有T操作继续禁止。",
    ]
    failure_conditions = [
        "没有跌破硬止损，也没有反抽到减仓区时，不提前卖出。",
        "反抽减仓成交后不立刻买回；等待系统按剩余仓位刷新下一步。",
        "若盘中数据可信度低或行情延迟超阈值，只保留预案，不按旧价执行。",
    ]
    return {
        "applicable": True,
        "candidate": "risk_exit",
        "plan_type": "near_stop_playbook",
        "status": "path3_recovered" if recovered else "near_stop_review",
        "status_label": "风险降级观察" if recovered else "近硬止损盘中预案",
        "action_label": "风险降级观察" if recovered else "止损风险复核",
        "side": "sell",
        "side_label": "卖出",
        "trade_intent": "risk_exit_reduce",
        "shares": shares,
        "price_zone": rebound_zone,
        "target_zone": failure_zone,
        "min_price": rounded(as_float(rebound_zone[0])),
        "stop_loss_price": rounded(stop_loss),
        "current_price": rounded(current),
        "post_trade_shares": post_trade_shares,
        "technical_context": {
            "label": label,
            "trend": rounded(trend),
            "risk": rounded(risk),
            "reversal": rounded(reversal),
            "volume_confirmation": rounded(volume),
            "distance_to_stop_pct": rounded(distance_pct),
            "recover_price": rounded(recover_price),
            "minute_confirmation_status": minute_status,
            "minute_confirmation_label": minute_label,
        },
        "reason": reason,
        "failure_conditions": failure_conditions,
        "steps": steps,
        "post_trade_plan": f"盘中只按三条路径处理：跌破 {stop_text} 转退出，反抽到 {rebound_text} 降风险，重新站上 {money_text(recover_price)} 且分钟确认通过才降级观察。",
    }


def build_manual_execution_plan(
    review_summary: dict[str, Any],
    levels: dict[str, Any],
    position: dict[str, Any],
    capital_plan: dict[str, Any],
    positive_timing: dict[str, Any],
    intraday: dict[str, Any],
    state: str = "",
    technical_assessment: dict[str, Any] | None = None,
    minute_confirmation: dict[str, Any] | None = None,
    daily_trade_rhythm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state == "reverse_buyback_review":
        reverse_plan = intraday.get("reverse_t_plan") or {}
        open_leg = reverse_plan.get("open_reverse_t_leg") or {}
        buyback_max = as_float(reverse_plan.get("buyback_max_price") or levels.get("reverse_t_buyback_max_price"))
        current = as_float(levels.get("current_price"))
        shares = int(as_float(open_leg.get("shares"), 0.0) or reverse_plan.get("trade_shares") or 0)
        sell_price = as_float(open_leg.get("sell_price"))
        buyback_ready = reverse_plan.get("status") == "buyback_ready" and current is not None and buyback_max is not None and current <= buyback_max
        buy_fees = estimate_trade_fees("buy", min(current, buyback_max) if current is not None and buyback_max is not None else buyback_max, shares)
        sell_fees = estimate_trade_fees("sell", sell_price, shares)
        estimated_net = None
        if sell_price is not None and buyback_max is not None and shares:
            estimated_net = (sell_price - buyback_max) * shares - float(sell_fees.get("total_fees") or 0.0) - float(buy_fees.get("total_fees") or 0.0)
        return {
            "applicable": True,
            "status": "ready_for_manual_confirm" if buyback_ready else "watch",
            "status_label": "反T回补已到价" if buyback_ready else "等待反T回补价",
            "candidate": "reverse_t_buyback",
            "side": "buy",
            "side_label": "买入",
            "trade_intent": "reverse_t_close",
            "shares": shares or None,
            "price_zone": [rounded(buyback_max), rounded(buyback_max)] if buyback_max is not None else None,
            "max_price": rounded(buyback_max),
            "estimated_fees": buy_fees,
            "expected_net_profit_at_buyback_max": rounded(estimated_net),
            "open_reverse_t_leg": open_leg,
            "steps": [
                f"先确认这笔开放反T卖出腿：卖出价 {money_text(sell_price)}，数量 {shares or '-'} 股。",
                f"只在价格不高于 {money_text(buyback_max)} 时买回同等股数；高于该价格不追买。",
                "提交前核对方向是“买入”、数量等于原卖出腿数量；这不是新增补仓。",
                f"如果你决定不买回，则把这笔卖出的 {shares or '-'} 股改按减仓/退出结果管理，不再挂回补单。",
                "成交后立即记录为反T回补成交，关闭这条开放反T腿。",
            ],
            "post_trade_plan": "回补成交后只关闭这条反T腿；剩余持仓继续按独立止损/持仓风险复核，不用回补来扩大仓位。",
        }
    stop_loss_plan = build_stop_loss_risk_plan(levels, position, technical_assessment)
    if stop_loss_plan.get("applicable"):
        return stop_loss_plan
    near_stop_plan = build_near_stop_risk_plan(state, levels, position, technical_assessment, minute_confirmation)
    if near_stop_plan.get("applicable"):
        return near_stop_plan

    if review_summary.get("status") != "manual_candidate":
        return {
            "applicable": False,
            "status": "not_applicable",
            "status_label": "未进入人工候选，不生成交易计划",
            "steps": [],
        }

    candidate = review_summary.get("candidate")
    rhythm_status = str((daily_trade_rhythm or {}).get("status") or "")
    if rhythm_status == "risk_exit_cooldown" and candidate in {"positive_t", "reverse_t"}:
        return {
            "applicable": True,
            "status": "blocked",
            "status_label": "日内节奏冷静期，禁止新增做T",
            "candidate": candidate,
            "steps": [
                (daily_trade_rhythm or {}).get("next_action") or "今日已执行风控卖出；不立刻反向买回、补仓或新增做T。",
                "今天只允许继续观察、记录已成交结果，或在价格继续触发风险时执行止损/减仓计划。",
                "等下一交易日或下一轮完整决策链重新评估后，再考虑新的正T/反T。",
            ],
        }
    if rhythm_status == "trade_frequency_caution" and candidate == "positive_t":
        return {
            "applicable": True,
            "status": "blocked",
            "status_label": "今日操作偏多，禁止新增正T买入",
            "candidate": candidate,
            "steps": [
                (daily_trade_rhythm or {}).get("next_action") or "今日成交次数偏多，不再新增补仓或正T买入。",
                "若已有开放中的做T腿，只处理闭环条件；没有闭环条件时继续观察。",
            ],
        }
    current = as_float(levels.get("current_price"))
    current_shares = int(as_float(position.get("shares"), 0.0) or 0)
    entry_price = as_float(position.get("entry_price"))
    if candidate == "positive_t":
        shares = int(capital_plan.get("suggested_buy_shares") or 0)
        buy_zone = capital_plan.get("buy_zone") or positive_timing.get("buy_zone") or []
        target_zone = capital_plan.get("target_sell_zone") or positive_timing.get("target_sell_zone") or []
        buy_high = as_float(buy_zone[1]) if isinstance(buy_zone, list) and len(buy_zone) >= 2 else current
        target_low = as_float(target_zone[0]) if isinstance(target_zone, list) and len(target_zone) >= 2 else None
        estimated_amount = buy_high * shares if buy_high is not None and shares else None
        fees = estimate_trade_fees("buy", buy_high, shares)
        if not shares or buy_high is None or not isinstance(buy_zone, list) or len(buy_zone) < 2 or not isinstance(target_zone, list) or len(target_zone) < 2:
            return {
                "applicable": True,
                "status": "blocked",
                "status_label": "正T候选缺少可买数量或价格区间",
                "candidate": "positive_t",
                "steps": ["不下单；等待资金计划给出100股整数、买入观察区和目标卖出区后再确认。"],
            }
        added_risk = as_float(capital_plan.get("added_risk_amount"))
        target_profit = None
        if target_low is not None:
            target_profit = (target_low - buy_high) * shares - float(fees["total_fees"] or 0.0) - float(estimate_trade_fees("sell", target_low, shares)["total_fees"] or 0.0)
        return {
            "applicable": True,
            "status": "ready_for_manual_confirm",
            "status_label": "正T人工候选计划",
            "candidate": "positive_t",
            "side": "buy",
            "side_label": "买入",
            "trade_intent": "positive_t_open",
            "shares": shares,
            "price_zone": buy_zone,
            "target_zone": target_zone,
            "max_price": rounded(buy_high),
            "estimated_amount": rounded(estimated_amount),
            "estimated_fees": fees,
            "expected_net_profit_at_target": rounded(target_profit),
            "post_trade_shares": current_shares + shares,
            "risk_amount": rounded(added_risk),
            "failure_conditions": [
                f"价格没有进入 {buy_zone[0]:.2f}-{buy_zone[1]:.2f} 元买入观察区时，不追价。",
                f"买入后跌破止损价 {money_text(as_float(levels.get('stop_loss_price')))}，新增仓位按止损处理。",
                "买入后未到目标卖出区，当天收盘前重新评估是否转为普通加仓持有。",
            ],
            "steps": [
                f"打开券商交易软件，选择“买入”，证券代码按当前详情页股票。",
                f"买入价格只填 {buy_zone[0]:.2f}-{buy_zone[1]:.2f} 元区间内的价格；高于 {buy_high:.2f} 元不买。",
                f"买入数量填 {shares} 股；本次预计占用资金约 {money_text(estimated_amount)}，预估费用约 {money_text(as_float(fees.get('total_fees')))}。",
                "提交前核对方向是“买入”、数量和价格无误；未成交不追高改价。",
                f"成交后立即在本系统写入买入成交；随后只盯 {target_zone[0]:.2f}-{target_zone[1]:.2f} 元卖出新增的 {shares} 股。",
            ],
            "post_trade_plan": f"买入成交后，目标是在 {target_zone[0]:.2f}-{target_zone[1]:.2f} 元卖出新增 {shares} 股完成正T；若不到目标，重新评估而不是继续加仓。",
        }

    if candidate == "reverse_t":
        reverse_plan = intraday.get("reverse_t_plan") or {}
        shares = int(reverse_plan.get("trade_shares") or 100)
        sell_zone = levels.get("reverse_t_sell_zone") or []
        buyback_max = as_float(levels.get("reverse_t_buyback_max_price"))
        sell_low = as_float(sell_zone[0]) if isinstance(sell_zone, list) and len(sell_zone) >= 2 else None
        sell_high = as_float(sell_zone[1]) if isinstance(sell_zone, list) and len(sell_zone) >= 2 else None
        fee_price = sell_low or current
        fees = estimate_trade_fees("sell", fee_price, shares)
        estimated_cash = fee_price * shares - float(fees["total_fees"] or 0.0) if fee_price is not None else None
        realized_pnl = None
        if fee_price is not None and entry_price is not None:
            realized_pnl = (fee_price - entry_price) * shares - float(fees["total_fees"] or 0.0)
        if not shares or sell_low is None or sell_high is None:
            return {
                "applicable": True,
                "status": "blocked",
                "status_label": "反T候选缺少卖出区间或数量",
                "candidate": "reverse_t",
                "steps": ["不下单；等待卖出观察区、回补上限和100股整数数量全部生成后再确认。"],
            }
        return {
            "applicable": True,
            "status": "ready_for_manual_confirm",
            "status_label": "反T人工候选计划",
            "candidate": "reverse_t",
            "side": "sell",
            "side_label": "卖出",
            "trade_intent": "reverse_t_open",
            "shares": shares,
            "price_zone": sell_zone,
            "target_zone": [rounded(buyback_max), rounded(buyback_max)] if buyback_max is not None else None,
            "min_price": rounded(sell_low),
            "estimated_amount": rounded(estimated_cash),
            "estimated_fees": fees,
            "estimated_realized_pnl": rounded(realized_pnl),
            "post_trade_shares": current_shares - shares,
            "failure_conditions": [
                f"价格没有进入 {sell_low:.2f}-{sell_high:.2f} 元卖出观察区时，不卖。",
                "卖出后没有跌到回补上限，不追买；这笔卖出先按计划外减仓风险管理。",
                "卖出后若继续上涨，不加倍卖出；等待系统下一轮重新评估。",
            ],
            "steps": [
                "打开券商交易软件，选择“卖出”，证券代码按当前详情页股票。",
                f"卖出价格只填 {sell_low:.2f}-{sell_high:.2f} 元区间内的价格；低于 {sell_low:.2f} 元不卖。",
                f"卖出数量填 {shares} 股；预计卖出后剩余 {current_shares - shares} 股，预估费用约 {money_text(as_float(fees.get('total_fees')))}。",
                "提交前核对方向是“卖出”、数量和价格无误；成交后不要立刻追买。",
                f"成交后在本系统写入反T卖出腿；只有价格不高于 {money_text(buyback_max)} 才考虑回补同等 {shares} 股。",
            ],
            "post_trade_plan": f"卖出成交后等待回补价不高于 {money_text(buyback_max)}；未到回补价不买回，防止反T变追高。",
        }

    return {
        "applicable": True,
        "status": "blocked",
        "status_label": "未知候选类型，不能生成计划",
        "candidate": candidate,
        "steps": ["不下单；等待系统识别为正T或反T候选后再生成计划。"],
    }


def build_capital_plan(
    state: str,
    levels: dict[str, Any],
    position: dict[str, Any],
    *,
    total_assets: float | None = None,
    technical_assessment: dict[str, Any] | None = None,
    positive_timing: dict[str, Any] | None = None,
    minute_confirmation: dict[str, Any] | None = None,
    supplemental_capital_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(supplemental_capital_policy or supplemental_capital_policy_from_profile())
    technical_label = str((technical_assessment or {}).get("label") or "")
    technical_score = as_float((technical_assessment or {}).get("score"))
    strong_setup = technical_label == "bullish" and technical_score is not None and technical_score >= 18
    effective_single_add_pct = (
        policy["strong_single_add_pct_total_assets"] if strong_setup else policy["base_single_add_pct_total_assets"]
    )
    current = as_float(levels.get("current_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    ma5 = as_float(levels.get("ma5"))
    recent_low = as_float(levels.get("recent_low"))
    shares = as_float(position.get("shares"), 0.0) or 0.0
    market_value = as_float(position.get("market_value"))
    live_pct = as_float(position.get("live_position_pct"))
    if live_pct is None and total_assets not in (None, 0):
        value = market_value if market_value is not None else (current * shares if current is not None else None)
        live_pct = None if value is None else value / float(total_assets) * 100

    plan: dict[str, Any] = {
        **policy,
        "applicable": state == "positive_t_watch",
        "status": "not_applicable",
        "status_label": "仅正T观察候选才评估追加资金",
        "single_add_tier": "strong" if strong_setup else "base",
        "effective_single_add_pct_total_assets": effective_single_add_pct,
        "max_additional_capital": None,
        "suggested_buy_shares": 0,
        "estimated_buy_amount": None,
        "post_add_position_pct": None,
        "added_risk_amount": None,
        "added_risk_pct_total_assets": None,
        "buy_zone": None,
        "target_sell_zone": None,
        "failure_plan": "未触发正T条件时不追加资金；触发后若跌破止损，新增仓位按止损处理。",
        "steps": [],
        "reasons": [],
    }
    if state != "positive_t_watch":
        return plan
    minute_status = str((minute_confirmation or {}).get("status") or "not_available")
    if minute_status != "confirm":
        plan.update(
            {
                "status": "waiting_minute_confirmation",
                "status_label": "分钟二次确认未通过，不计算正T追加资金",
                "reasons": [
                    str((minute_confirmation or {}).get("summary") or "等待5分钟MACD、RSI、BOLL、量能和均线共同确认。")
                ],
            }
        )
        return plan
    if positive_timing and positive_timing.get("status") != "confirmed":
        plan.update(
            {
                "status": "waiting_intraday_confirmation",
                "status_label": "日线正T候选已出现，但分时评分未确认买点",
                "reasons": positive_timing.get("signals", [])[:4] or ["等待5分钟线、量能和资金流进一步确认。"],
            }
        )
        return plan
    if total_assets in (None, 0) or current in (None, 0):
        plan.update(
            {
                "status": "blocked",
                "status_label": "缺少总资产或现价，不能计算追加资金上限",
                "reasons": ["缺少总资产或现价，不能把追加资金转换为100股整数和风险金额。"],
            }
        )
        return plan
    max_single_cash = float(total_assets) * effective_single_add_pct / 100
    current_position_pct = live_pct or 0.0
    capacity_pct = policy["max_stock_position_pct_after_add"] - current_position_pct
    position_capacity_cash = max(0.0, float(total_assets) * capacity_pct / 100)
    max_cash = max(0.0, min(max_single_cash, position_capacity_cash))
    buy_zone_high = min(value for value in [current, ma5] if value is not None) if ma5 is not None else current
    stop_buffer_price = stop_loss * (1 + policy["min_stop_buffer_pct"] / 100) if stop_loss is not None else None
    timing_buy_zone = positive_timing.get("buy_zone") if isinstance(positive_timing, dict) else None
    timing_target_zone = positive_timing.get("target_sell_zone") if isinstance(positive_timing, dict) else None
    buy_zone_low = buy_zone_high * (1 - policy["min_target_gap_pct"] / 100)
    if recent_low is not None:
        buy_zone_low = max(buy_zone_low, recent_low)
    if stop_buffer_price is not None:
        buy_zone_low = max(buy_zone_low, stop_buffer_price)
    buy_zone_low = min(buy_zone_low, buy_zone_high)
    if isinstance(timing_buy_zone, list) and len(timing_buy_zone) == 2:
        buy_zone_low = as_float(timing_buy_zone[0], buy_zone_low) or buy_zone_low
        buy_zone_high = as_float(timing_buy_zone[1], buy_zone_high) or buy_zone_high
    suggested_shares = floor_lot_from_cash(max_cash, buy_zone_high)
    estimated_buy_amount = suggested_shares * buy_zone_high if suggested_shares else None
    added_risk = None
    added_risk_pct = None
    if suggested_shares and stop_loss is not None:
        added_risk = max(0.0, (buy_zone_high - stop_loss) * suggested_shares)
        added_risk_pct = added_risk / float(total_assets) * 100
    max_added_risk = float(total_assets) * policy["max_added_risk_pct_total_assets"] / 100
    if suggested_shares and added_risk is not None and added_risk > max_added_risk:
        risk_limited_shares = floor_lot_from_cash(max_added_risk / max((buy_zone_high - stop_loss), 0.01), 1.0) if stop_loss is not None else 0
        suggested_shares = min(suggested_shares, risk_limited_shares)
        estimated_buy_amount = suggested_shares * buy_zone_high if suggested_shares else None
        added_risk = max(0.0, (buy_zone_high - stop_loss) * suggested_shares) if suggested_shares and stop_loss is not None else None
        added_risk_pct = added_risk / float(total_assets) * 100 if added_risk is not None else None
    post_add_pct = current_position_pct + ((estimated_buy_amount or 0.0) / float(total_assets) * 100)
    target_low = max(current, buy_zone_high * (1 + policy["min_target_gap_pct"] / 100))
    target_high = target_low + dynamic_price_zone_width(target_low)
    if isinstance(timing_target_zone, list) and len(timing_target_zone) == 2:
        target_low = as_float(timing_target_zone[0], target_low) or target_low
        target_high = as_float(timing_target_zone[1], target_high) or target_high
    plan.update(
        {
            "max_additional_capital": rounded(max_cash),
            "suggested_buy_shares": int(suggested_shares),
            "estimated_buy_amount": rounded(estimated_buy_amount),
            "post_add_position_pct": rounded(post_add_pct),
            "added_risk_amount": rounded(added_risk),
            "added_risk_pct_total_assets": rounded(added_risk_pct),
            "buy_zone": [rounded(buy_zone_low), rounded(buy_zone_high)],
            "target_sell_zone": [rounded(target_low), rounded(target_high)],
        }
    )
    if max_cash < buy_zone_high * 100:
        plan.update(
            {
                "status": "blocked",
                "status_label": "追加资金上限不足买入100股",
                "reasons": [f"本次追加资金上限约 {money_text(max_cash)}，低于买入100股所需金额。"],
            }
        )
        return plan
    if stop_loss is not None and buy_zone_low <= stop_buffer_price:
        plan["reasons"].append(f"买入观察下沿必须高于止损价至少 {policy['min_stop_buffer_pct']:.1f}%。")
    plan.update(
        {
            "status": "watch",
            "status_label": "可用追加资金进入正T观察，不要求账户当前现金已足额在场",
            "steps": [
                f"最多只准备追加 {money_text(max_cash)}，不是无限补仓；本轮单次追加上限为总资产 {effective_single_add_pct:.1f}%。",
                f"只在价格回落到 {buy_zone_low:.2f}-{buy_zone_high:.2f} 区间且数据质量仍为高/中可信时，才考虑买入 {int(suggested_shares)} 股。",
                f"买入后目标不是长期摊低成本，而是在 {target_low:.2f}-{target_high:.2f} 区间优先卖出新增的 {int(suggested_shares)} 股完成正T。",
                f"若买入后跌破止损价 {money_text(stop_loss)}，新增仓位按止损处理；预计新增风险约 {money_text(added_risk)}。",
                "如果买入区间没有触发，不追价；如果买入后未到卖出目标，当天收盘前重新评估是否转为普通加仓持有。",
            ],
        }
    )
    if strong_setup:
        plan["reasons"].append("多周期技术指标达到 bullish，正T追加资金上限从基础3%放宽到5%。")
    else:
        plan["reasons"].append("技术面未达到强趋势，只使用基础3%追加资金上限。")
    return plan


def build_action_steps(
    state: str,
    levels: dict[str, Any],
    blockers: list[str],
    action_backtest: dict[str, Any] | None,
    *,
    code: str | None = None,
    name: str | None = None,
    position: dict[str, Any] | None = None,
    capital_plan: dict[str, Any] | None = None,
    technical_operation: dict[str, Any] | None = None,
    t_performance_gate: dict[str, Any] | None = None,
    execution_quality_gate: dict[str, Any] | None = None,
    liquidity_activity_gate: dict[str, Any] | None = None,
    manual_execution_plan: dict[str, Any] | None = None,
    daily_trade_rhythm: dict[str, Any] | None = None,
) -> list[str]:
    current = as_float(levels.get("current_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    stop_loss_confirmed = bool(levels.get("stop_loss_confirmed", stop_loss is not None))
    near_block = as_float(levels.get("near_stop_block_price"))
    shares = whole_lot_shares((position or {}).get("shares"))
    entry_price = as_float((position or {}).get("entry_price"))
    unrealized_pnl = as_float((position or {}).get("unrealized_pnl"))
    estimated_cash = current * shares if current is not None and shares is not None else None
    estimated_pnl = (current - entry_price) * shares if current is not None and entry_price is not None and shares is not None else unrealized_pnl
    loss_word = "亏损" if estimated_pnl is not None and estimated_pnl < 0 else "盈亏"
    rhythm_status = str((daily_trade_rhythm or {}).get("status") or "")
    if rhythm_status == "risk_exit_cooldown" and state not in {"exit_risk_review", "risk_reduction_review", "data_insufficient"}:
        return [
            (daily_trade_rhythm or {}).get("next_action") or "今日已执行风控卖出，进入日内冷静期。",
            "现在不补仓、不做正T买入、不做新的反T；避免刚减仓又反向追买。",
            "只继续监控硬止损、风控减仓触发价和已打开做T腿的闭环条件。",
            "下一步：等下一轮完整决策卡或下一交易日重新评估趋势、量能、分钟确认后，再决定是否恢复交易动作。",
        ]
    if rhythm_status == "trade_frequency_caution" and state in {"positive_t_watch", "reverse_t_watch", "hold_no_add", "observe"}:
        return [
            (daily_trade_rhythm or {}).get("next_action") or "今日成交次数偏多，先降频观察。",
            "不新增补仓，不放大做T股数；只允许完成已打开的做T闭环或继续处理退出风险。",
            "下一步：等待下一轮触发价或下一交易日重新评估。",
        ]
    if state == "reverse_buyback_review":
        reverse_plan_steps = list((manual_execution_plan or {}).get("steps") or [])
        steps = [
            "当前不是新增买入，也不是补仓摊低成本；只处理已经卖出的反T腿是否闭环。",
        ]
        if reverse_plan_steps:
            steps.extend(str(step) for step in reverse_plan_steps)
        else:
            buyback = as_float(levels.get("reverse_t_buyback_max_price"))
            steps.append(f"只在价格不高于 {money_text(buyback)} 时买回原卖出股数；高于回补上限不追买。")
            steps.append("如果不回补，则把这笔卖出按减仓结果管理。")
        if stop_loss is not None:
            steps.append(f"止损风险仍保留：剩余持仓继续参考止损价 {stop_loss:.2f}，但不覆盖这条已打开反T腿的回补判断。")
        if blockers:
            steps.append(f"本轮主要提示：{blockers[0]}")
        return steps
    if state == "risk_downgrade_watch":
        steps = [
            "路径3已触发：价格站上恢复价且分钟确认通过，本轮退出风险降级为观察复核。",
            "这不是交易执行信号；当前不主动卖出，不补仓，不做正T或反T。",
            "继续刷新决策卡；只有后续重新跌回下破/反抽路径，才再处理风控卖出预案。",
        ]
        risk_plan = manual_execution_plan if (manual_execution_plan or {}).get("candidate") == "risk_exit" else {}
        if risk_plan.get("post_trade_plan"):
            steps.append(f"后续监控口径：{risk_plan['post_trade_plan']}")
        return steps
    if state == "exit_risk_review":
        steps = ["本轮禁止买入、补仓、做T；只允许处理卖出风险。"]
        if rhythm_status != "clear":
            steps.append(f"日内节奏：{(daily_trade_rhythm or {}).get('next_action') or (daily_trade_rhythm or {}).get('status_label')}")
        risk_plan = manual_execution_plan if (manual_execution_plan or {}).get("candidate") == "risk_exit" else {}
        if risk_plan:
            realized = as_float(risk_plan.get("estimated_realized_pnl"))
            remaining = risk_plan.get("post_trade_shares")
            if risk_plan.get("plan_type") == "near_stop_playbook":
                steps.append(
                    f"当前不是立即卖出；这是三路径预案。只有路径2反抽触发时，才考虑风控减仓 {risk_plan.get('shares') or '-'} 股；"
                    f"成交后预计剩余 {remaining if remaining is not None else '-'} 股。"
                )
            else:
                steps.append(
                    f"当前计划：{risk_plan.get('action_label') or '风控卖出'} {risk_plan.get('shares') or '-'} 股；"
                    f"执行后预计剩余 {remaining if remaining is not None else '-'} 股，预计确认盈亏约 {money_text(realized)}。"
                )
            steps.extend(str(step) for step in risk_plan.get("steps", []))
            if risk_plan.get("post_trade_plan"):
                steps.append(f"成交后的下一步计划：{risk_plan['post_trade_plan']}")
            if near_block is not None:
                steps.append(f"做T阻断价：{near_block:.2f}；低于或接近该价时，正T/反T/补仓全部保持禁止。")
            return steps
        if current is not None and stop_loss is not None:
            if current <= stop_loss:
                steps.extend(
                    [
                        f"操作后果：按现价附近卖出 {shares or '全部可卖'} 股，预计回收现金约 {money_text(estimated_cash)}，预计确认{loss_word}约 {money_text(estimated_pnl)}。",
                        "仓位后果：全仓卖出后该股持仓变为0股；如果后面反弹，这部分仓位不再参与反弹。",
                        "打开券商交易软件，进入“交易/卖出”。",
                        f"卖出数量输入：{shares} 股；如果券商显示可卖数量少于该数，输入券商显示的全部可卖数量。" if shares else "卖出数量输入：券商显示的全部可卖数量；数量不足100股时按券商允许的零股/全部卖出规则处理。",
                        f"卖出价格输入：先参考现价 {current:.2f}；如果盘口买一价低于现价，用买一价或可成交价，不要高挂等反弹。",
                        "点击卖出前最后核对：卖出数量、卖出价格、方向是“卖出”。",
                        "提交后只检查是否成交；未成交时不要改成买入或补仓，只允许按更接近可成交的卖出价重新挂单。",
                        "成交后的下一步计划：记录卖出成交价和数量，更新持仓为已退出，再做复盘：这次亏损是策略问题、执行问题还是止损设置问题。",
                    ]
                )
            else:
                steps.extend(
                    [
                        f"当前还没有跌破止损价：现价 {current:.2f}，止损价 {stop_loss:.2f}。",
                        "现在不要下卖单，也不要补仓或做T。",
                        f"设置价格提醒：低于/等于 {stop_loss:.2f} 立即提醒。",
                        "如果提醒触发，按“交易/卖出 -> 输入代码 -> 输入全部可卖数量 -> 用可成交卖出价提交”的止损流程处理。",
                        "未触发前的计划：只观察，不做T；下一次刷新决策卡后再判断是否继续持有或转为止损卖出。",
                    ]
                )
        elif stop_loss is None:
            steps.append("缺少止损价时不能判断卖出触发位；先在持仓文件补齐止损价，不下单。")
        if near_block is not None:
            steps.append(f"做T阻断价是 {near_block:.2f}；价格在该价附近或更低时，不允许买入做T。")
        steps.append("成交后记录卖出价格、卖出数量和原因：止损/退出风险；再进入卖出执行记录和复盘。")
        if blockers:
            steps.append(f"本轮主要风险提示：{blockers[0]}")
        return steps
    if state == "risk_reduction_review":
        return [
            "不新增买入，不做T扩大风险敞口。",
            "先按100股整数倍计算降仓后仓位是否回到上限内。",
            "若卖出100股会过度降仓，则只记录复核结论，不强行交易。",
            "确认正式仓位上限、可卖数量和预估费用后，再生成降仓卖出计划。",
        ]
    if state == "data_insufficient":
        steps = ["本轮不买、不卖、不做T，因为系统不能验证行情、样本、止损或数据一致性。"]
        blocker_text = "\n".join(blockers)
        if "日线数量" in blocker_text:
            steps.append(f"补齐日线历史数据：刷新 {code or '该股票'} 的日线，至少达到20根日线后再判断趋势和做T环境。")
        if "分钟线" in blocker_text or "一致性" in blocker_text or "现价与分钟线" in blocker_text:
            steps.append("刷新5分钟线缓存并复核东方财富现价与分钟线最新收盘价；偏差回到阈值内后再决策。")
        if "止损价" in blocker_text:
            steps.append("补齐或确认止损价；没有止损价时不允许生成做T或退出执行建议。")
        if "样本不足" in blocker_text or "回测" in blocker_text:
            steps.append("样本不足时只允许观察；不要把正T/反T候选当成可执行交易。")
        if len(steps) == 1:
            steps.append("先逐条处理阻断原因，再重新生成实时决策卡。")
        steps.append("修复后重新运行完整日内决策链，只有状态离开数据不足后才继续判断交易动作。")
        return steps
    if state == "positive_t_watch":
        steps = [
            "只允许加入人工观察，不直接买入。",
            "可追加资金不等于可以无上限补仓；必须先满足买入区间、止损距离、数据质量和技术面条件。",
        ]
        if t_performance_gate and t_performance_gate.get("status") == "caution":
            steps.append(f"做T实盘绩效提示：{(t_performance_gate.get('reasons') or ['样本不足，不放大单次股数。'])[0]}")
        if execution_quality_gate and execution_quality_gate.get("status") == "caution":
            steps.append(f"执行质量提示：{(execution_quality_gate.get('reasons') or ['执行评分样本不足，不放大单次股数。'])[0]}")
        if liquidity_activity_gate and liquidity_activity_gate.get("status") != "pass":
            steps.append(f"成交活跃度提示：{(liquidity_activity_gate.get('reasons') or ['成交活跃度未完全确认。'])[0]}")
        if technical_operation and not technical_operation.get("allow_buy_watch"):
            steps.append(f"技术操作档位为“{technical_operation.get('tier_label')}”：{technical_operation.get('next_step')}")
        if capital_plan and capital_plan.get("status") == "watch":
            steps.extend(capital_plan.get("steps", []))
        else:
            steps.append((capital_plan or {}).get("status_label") or "先写清买入价、卖出价、失败后是否接受加仓，以及新增仓位上限。")
        steps.append("价格、数据质量和止损距离同时满足后，才生成做T计划并人工确认。")
        return steps
    if state == "reverse_t_watch":
        steps = [
            "只允许加入人工观察，不直接卖出。",
            "必须确认5分钟回测、费用模型、分时转弱和回补上限。",
            "未到回补价不追买；可能形成实际减仓，必须提前接受这个结果。",
        ]
        if t_performance_gate and t_performance_gate.get("status") == "caution":
            steps.append(f"做T实盘绩效提示：{(t_performance_gate.get('reasons') or ['样本不足，不放大单次股数。'])[0]}")
        if execution_quality_gate and execution_quality_gate.get("status") == "caution":
            steps.append(f"执行质量提示：{(execution_quality_gate.get('reasons') or ['执行评分样本不足，不放大单次股数。'])[0]}")
        if liquidity_activity_gate and liquidity_activity_gate.get("status") != "pass":
            steps.append(f"成交活跃度提示：{(liquidity_activity_gate.get('reasons') or ['成交活跃度未完全确认。'])[0]}")
        if technical_operation and not technical_operation.get("allow_t_watch"):
            steps.append(f"技术操作档位为“{technical_operation.get('tier_label')}”：{technical_operation.get('next_step')}")
        return steps
    if state == "hold_no_add":
        steps = ["持有观察，不补仓，不做T。", "等待技术面、数据质量或风险信号改善后再重新评估。"]
        if t_performance_gate and t_performance_gate.get("status") == "blocked":
            steps.append(f"做T实盘绩效阻断：{(t_performance_gate.get('reasons') or ['实盘绩效未通过。'])[0]}")
            steps.append(t_performance_gate.get("next_step") or "暂停做T执行，只保留观察、止损或减仓。")
        if execution_quality_gate and execution_quality_gate.get("status") == "blocked":
            steps.append(f"执行质量阻断：{(execution_quality_gate.get('reasons') or ['近期执行评分未通过。'])[0]}")
            steps.append(execution_quality_gate.get("next_step") or "暂停新的买入/做T，只保留观察、止损或减仓。")
        if liquidity_activity_gate and liquidity_activity_gate.get("status") == "blocked":
            steps.append(f"成交活跃度阻断：{(liquidity_activity_gate.get('reasons') or ['成交活跃度不足。'])[0]}")
            steps.append(liquidity_activity_gate.get("next_step") or "暂停主动买入和反T卖出。")
        if technical_operation and technical_operation.get("tier") in {"risk_control_first", "forbid_chase", "observe_only"}:
            steps.append(f"技术操作档位为“{technical_operation.get('tier_label')}”：{technical_operation.get('next_step')}")
        if action_backtest and (action_backtest.get("weak_rule_count") or 0) > 0:
            steps.append("动作矩阵存在弱规则，先复核规则表现，不新增交易。")
        return steps
    if state in {"data_stale", "market_wait"}:
        return ["等待行情刷新到可用状态；刷新前不做盘中交易动作。"]
    return ["本轮不买不卖，继续监控关键价格、数据质量和技术指标变化。"]


def format_price_zone(zone: Any) -> str:
    if isinstance(zone, list) and len(zone) >= 2:
        low = as_float(zone[0])
        high = as_float(zone[1])
        if low is not None and high is not None:
            return f"{low:.2f}-{high:.2f} 元"
    value = as_float(zone)
    return "-" if value is None else f"{value:.2f} 元"


def action_table_row(
    action: str,
    trigger: str,
    price: str,
    operation: str,
    *,
    status: str,
    status_label: str,
    shares: int | None = None,
    note: str = "",
    priority: int = 0,
) -> dict[str, Any]:
    return {
        "action": action,
        "trigger": trigger,
        "price": price,
        "operation": operation,
        "shares": shares,
        "status": status,
        "status_label": status_label,
        "note": note,
        "priority": priority,
    }


def price_action_priority(action: str, status: str) -> int:
    if status == "ready":
        return {
            "止损/退出": 100,
            "硬退出": 100,
            "止损减仓": 98,
            "反T回补": 105,
            "正T目标卖出": 90,
            "正T买入": 82,
            "反T卖出": 80,
        }.get(action, 70)
    if status == "blocked":
        return {
            "止损风险复核": 88,
            "做T阻断": 78,
            "反T卖出": 72,
            "正T买入": 70,
            "当前动作": 68,
            "禁止追买": 40,
        }.get(action, 60)
    if status == "watch":
        return {
            "反弹减仓": 79,
            "反T回补": 81,
            "正T目标卖出": 55,
            "正T买入": 50,
            "反T卖出": 48,
            "止损/退出": 45,
            "做T阻断": 42,
            "当前动作": 30,
        }.get(action, 20)
    return 0


def build_price_action_table(
    state: str,
    levels: dict[str, Any],
    intraday: dict[str, Any],
    capital_plan: dict[str, Any],
    positive_timing: dict[str, Any],
    manual_execution_plan: dict[str, Any],
    t_performance_gate: dict[str, Any],
    execution_quality_gate: dict[str, Any],
    data_quality: dict[str, Any] | None,
    minute_confirmation: dict[str, Any] | None = None,
    liquidity_activity_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = as_float(levels.get("current_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    dynamic_stop = as_float(levels.get("dynamic_stop_loss_price"), stop_loss)
    stop_loss_confirmed = bool(levels.get("stop_loss_confirmed", stop_loss is not None))
    near_block = as_float(levels.get("near_stop_block_price"))
    position = intraday.get("position") or {}
    shares = whole_lot_shares(position.get("shares"))
    reverse_plan = intraday.get("reverse_t_plan") or {}
    positive_plan = intraday.get("positive_t_plan") or {}
    data_allowed = data_quality is None or bool(value_at(data_quality or {}, "data_trust.intraday_decision_allowed"))
    minute_status = str((minute_confirmation or {}).get("status") or "not_available")
    minute_confirmed = minute_status == "confirm"
    minute_blocked = minute_status == "block"
    minute_status_label = str((minute_confirmation or {}).get("status_label") or "分钟未确认")
    liquidity_status = str((liquidity_activity_gate or {}).get("status") or "")
    liquidity_blocked = liquidity_status == "blocked"
    liquidity_caution = liquidity_status == "caution"
    rows: list[dict[str, Any]] = []

    rows.append(
        action_table_row(
            "当前动作",
            "现在",
            money_text(current),
            "不直接下单",
            status="blocked" if state in {"data_stale", "market_wait", "data_insufficient"} else "watch",
            status_label="等待数据" if state in {"data_stale", "market_wait", "data_insufficient"} else "观察",
            shares=None,
            note="先看本表价格触发条件；没有进入触发价前不操作。",
            priority=price_action_priority("当前动作", "blocked" if state in {"data_stale", "market_wait", "data_insufficient"} else "watch"),
        )
    )

    if stop_loss is not None and stop_loss_confirmed:
        stop_ready = current is not None and current <= stop_loss
        stop_plan = manual_execution_plan if manual_execution_plan.get("candidate") == "risk_exit" else {}
        stop_action = str(
            "止损风险复核"
            if state == "reverse_buyback_review" else
            stop_plan.get("action_label") or ("止损风险复核" if state == "exit_risk_review" and not stop_ready else "止损/退出")
        )
        plan_status = str(stop_plan.get("status") or "")
        row_status = "ready" if plan_status == "ready_for_manual_confirm" else "watch"
        if plan_status == "near_stop_review":
            row_status = "blocked"
        if plan_status == "path3_recovered":
            row_status = "watch"
        if state == "reverse_buyback_review":
            row_status = "watch"
        if not stop_plan:
            row_status = "watch" if state == "reverse_buyback_review" else "ready" if stop_ready else "blocked" if state == "exit_risk_review" else "watch"
        row_status_label = (
            "可执行" if row_status == "ready" and stop_plan else
            "等反弹" if plan_status == "wait_rebound_reduce" else
            "风险降级观察" if plan_status == "path3_recovered" else
            "近硬止损" if plan_status == "near_stop_review" else
            "近硬止损" if row_status == "blocked" and state == "exit_risk_review" else
            "风险复核" if state == "reverse_buyback_review" else
            "已触发" if stop_ready else "未触发"
        )
        row_price = (
            f"≤ {stop_loss:.2f} / {format_price_zone(stop_plan.get('price_zone'))}"
            if stop_plan.get("plan_type") == "near_stop_playbook" and stop_plan.get("price_zone") else
            format_price_zone(stop_plan.get("price_zone")) if stop_plan.get("price_zone") else
            f"≤ {stop_loss:.2f} 元"
        )
        row_trigger = (
            f"三路径：跌破 {stop_loss:.2f} 或反抽到 {format_price_zone(stop_plan.get('price_zone'))}" if stop_plan.get("plan_type") == "near_stop_playbook" and stop_plan.get("price_zone") else
            f"价格进入 {row_price}" if stop_plan.get("plan_type") == "rebound_reduce" else
            f"价格小于等于 {stop_loss:.2f} 元"
        )
        row_shares = int(stop_plan.get("shares") or 0) or shares
        rows.append(
            action_table_row(
                stop_action,
                row_trigger,
                row_price,
                "卖出风险仓位" if row_status == "ready" else "不买入/不做T",
                status=row_status,
                status_label=row_status_label,
                shares=row_shares,
                note=str(stop_plan.get("reason") or ("剩余持仓止损风险仍需复核；但已打开反T卖出腿先按回补或转减仓处理。" if state == "reverse_buyback_review" else "触发后优先处理退出风险；禁止补仓、禁止做T摊低成本。")),
                priority=price_action_priority(stop_action, row_status),
            )
        )
    elif stop_loss is not None:
        review_price = dynamic_stop if dynamic_stop is not None else stop_loss
        review_warning_price = review_price * 1.03 if review_price is not None else None
        review_near = current is not None and review_warning_price is not None and current <= review_warning_price
        dynamic_source = levels.get("dynamic_stop_loss_source") or "draft_or_confirmed"
        if review_near:
            rows.append(
                action_table_row(
                    "止损复核",
                    f"动态复核价 {review_price:.2f} 未确认" if review_price is not None else "止损参考价未确认",
                    f"{review_price:.2f} 元" if review_price is not None else "-",
                    "人工复核，不直接卖出",
                    status="watch",
                    status_label="接近复核",
                    shares=None,
                    note=f"该价格按{dynamic_source}动态估算；未人工确认前不能作为硬退出触发。",
                    priority=46,
                )
            )

    if near_block is not None:
        blocked_now = current is not None and current <= near_block
        rows.append(
            action_table_row(
                "做T阻断",
                f"价格小于等于 {near_block:.2f} 元",
                f"≤ {near_block:.2f} 元",
                "禁止买入/补仓/做T",
                status="blocked" if blocked_now else "watch",
                status_label="阻断中" if blocked_now else "未进入",
                note="离止损太近时，不允许用正T或反T扩大风险。",
                priority=price_action_priority("做T阻断", "blocked" if blocked_now else "watch"),
            )
        )

    buy_zone = capital_plan.get("buy_zone") or positive_timing.get("buy_zone")
    target_zone = capital_plan.get("target_sell_zone") or positive_timing.get("target_sell_zone") or positive_plan.get("target_sell_zone")
    suggested_buy_shares = int(capital_plan.get("suggested_buy_shares") or 0)
    if buy_zone:
        buy_high = as_float(buy_zone[1]) if isinstance(buy_zone, list) and len(buy_zone) >= 2 else None
        buy_ready = state == "positive_t_watch" and data_allowed and minute_confirmed and not liquidity_blocked and current is not None and buy_high is not None and current <= buy_high
        buy_gate_blocked = t_performance_gate.get("status") == "blocked" or execution_quality_gate.get("status") == "blocked"
        buy_blocked = state not in {"positive_t_watch"} or buy_gate_blocked or liquidity_blocked or not data_allowed or not minute_confirmed
        buy_status_label = (
            "执行评分阻断" if execution_quality_gate.get("status") == "blocked" else
            "绩效阻断" if t_performance_gate.get("status") == "blocked" else
            "活跃度阻断" if liquidity_blocked else
            "活跃度谨慎" if liquidity_caution and not buy_blocked else
            "分钟阻断" if minute_blocked else
            "等待分钟确认" if not minute_confirmed else
            "禁止" if buy_blocked else
            "可确认" if buy_ready else
            "等待"
        )
        rows.append(
            action_table_row(
                "正T买入",
                f"价格进入 {format_price_zone(buy_zone)} 且分钟二次确认通过",
                format_price_zone(buy_zone),
                "买入新增仓位",
                status="blocked" if buy_blocked else "ready" if buy_ready else "watch",
                status_label=buy_status_label,
                shares=suggested_buy_shares or None,
                note=f"{minute_status_label}；{(liquidity_activity_gate or {}).get('status_label') or '成交活跃度未单独确认'}；高于买入上限不追，买入后目标是卖出新增仓位，不是长期补仓。",
                priority=price_action_priority("正T买入", "blocked" if buy_blocked else "ready" if buy_ready else "watch"),
            )
        )

    if target_zone:
        close_shares = int(positive_plan.get("trade_shares") or suggested_buy_shares or 0)
        target_low = as_float(target_zone[0]) if isinstance(target_zone, list) and len(target_zone) >= 2 else None
        target_ready = current is not None and target_low is not None and current >= target_low and positive_plan.get("status") == "target_sell_ready"
        has_open_positive_leg = bool(positive_plan.get("open_positive_t_leg"))
        rows.append(
            action_table_row(
                "正T目标卖出",
                f"已买入正T腿后，价格进入 {format_price_zone(target_zone)}",
                format_price_zone(target_zone),
                "卖出新增仓位",
                status="ready" if target_ready else "watch",
                status_label="已触发" if target_ready else "等待",
                shares=close_shares or None,
                note="只卖新增股数完成闭环；未到目标不急卖。",
                priority=price_action_priority("正T目标卖出", "ready" if target_ready else "watch") if has_open_positive_leg or target_ready else 36,
            )
        )

    reverse_zone = levels.get("reverse_t_sell_zone")
    reverse_shares = int(reverse_plan.get("trade_shares") or 0)
    if reverse_zone:
        reverse_gate_blocked = t_performance_gate.get("status") == "blocked" or execution_quality_gate.get("status") == "blocked"
        reverse_candidate = state == "reverse_t_watch" and reverse_plan.get("status") == "candidate" and minute_confirmed and not reverse_gate_blocked and not liquidity_blocked
        reverse_status_label = (
            "可确认" if reverse_candidate else
            "执行评分阻断" if execution_quality_gate.get("status") == "blocked" else
            "绩效阻断" if t_performance_gate.get("status") == "blocked" else
            "活跃度阻断" if liquidity_blocked else
            "活跃度谨慎" if liquidity_caution else
            "分钟阻断" if minute_blocked else
            "等待分钟确认" if not minute_confirmed else
            "仅观察"
        )
        rows.append(
            action_table_row(
                "反T卖出",
                f"价格进入 {format_price_zone(reverse_zone)} 且分钟二次确认通过",
                format_price_zone(reverse_zone),
                "卖出计划股数",
                status="ready" if reverse_candidate else "blocked" if reverse_gate_blocked or liquidity_blocked or not minute_confirmed else "watch",
                status_label=reverse_status_label,
                shares=reverse_shares or None,
                note=f"{minute_status_label}；{(liquidity_activity_gate or {}).get('status_label') or '成交活跃度未单独确认'}；没有回补上限或未接受卖出后果时，不执行反T卖出。",
                priority=price_action_priority("反T卖出", "ready" if reverse_candidate else "blocked" if reverse_gate_blocked or liquidity_blocked or not minute_confirmed else "watch"),
            )
        )

    open_leg = reverse_plan.get("open_reverse_t_leg") or {}
    has_open_reverse_leg = bool(open_leg)
    buyback = as_float(reverse_plan.get("buyback_max_price") if has_open_reverse_leg else levels.get("reverse_t_buyback_max_price"))
    if buyback is not None and (has_open_reverse_leg or reverse_zone):
        buyback_ready = reverse_plan.get("status") == "buyback_ready" and has_open_reverse_leg
        buyback_shares = int(as_float(open_leg.get("shares"), 0.0) or reverse_shares or 0)
        rows.append(
            action_table_row(
                "反T回补",
                f"已有反T卖出腿成交后，价格小于等于 {buyback:.2f} 元",
                f"≤ {buyback:.2f} 元",
                "买回同等股数",
                status="ready" if buyback_ready else "watch",
                status_label="已触发" if buyback_ready else "等待",
                shares=buyback_shares or None,
                note="高于回补上限不追买；未回补要按减仓后果管理。",
                priority=price_action_priority("反T回补", "ready" if buyback_ready else "watch") if has_open_reverse_leg else 35,
            )
        )

    if buy_zone:
        buy_high = as_float(buy_zone[1]) if isinstance(buy_zone, list) and len(buy_zone) >= 2 else None
        rows.append(
            action_table_row(
                "禁止追买",
                "价格高于买入观察上限，或数据/止损/绩效门禁未通过",
                f"> {buy_high:.2f} 元" if buy_high is not None else "-",
                "不买入",
                status="blocked",
                status_label="硬限制",
                note="宁可错过，不在计划外提高买入价。",
                priority=price_action_priority("禁止追买", "blocked"),
            )
        )

    ordered_rows = sorted(enumerate(rows), key=lambda item: (-int(item[1].get("priority") or 0), item[0]))
    rows = [row for _, row in ordered_rows]
    primary_action = rows[0] if rows else None
    return {
        "status": state,
        "status_label": STATE_LABELS.get(state, state),
        "rows": rows,
        "primary_action": primary_action,
        "summary": "按触发价执行；未进入对应价格区间时只观察，不提前操作。",
    }


def build_action_arbitration(
    state: str,
    price_action_table: dict[str, Any],
    decision_mode: dict[str, Any],
    minute_confirmation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary = price_action_table.get("primary_action") or {}
    rows = price_action_table.get("rows") or []
    mode = str(decision_mode.get("mode") or "")
    minute_status = str((minute_confirmation or {}).get("status") or "not_available")
    minute_label = str((minute_confirmation or {}).get("status_label") or minute_status)
    suppressed: list[dict[str, Any]] = []
    primary_action = str(primary.get("action") or "当前动作")
    if state == "reverse_buyback_review":
        summary = f"{primary_action}优先；这是已打开的反T卖出腿闭环，不等同于新增补仓。"
    elif state == "exit_risk_review":
        summary = f"{primary_action}优先；正T、反T和补仓全部让位于硬止损风险。"
    elif state == "risk_downgrade_watch":
        summary = "风险已降级为观察复核；路径3不等同于交易执行信号。"
    elif mode != "tradable":
        summary = f"{decision_mode.get('label') or '只观察'}优先；数据或交易时段未支持盘中人工确认。"
    elif minute_status != "confirm":
        summary = f"{minute_label}优先；分钟二次确认通过前，正T、反T和放宽止损都只观察。"
    elif primary.get("status") == "ready":
        summary = f"{primary_action}已满足触发条件；其他动作等待本动作处理后重新评估。"
    else:
        summary = f"{primary_action}作为当前主观察动作；未触发前不提前切换到其他动作。"
    for row in rows:
        if row is primary:
            continue
        action = str(row.get("action") or "")
        if not action or action == primary_action:
            continue
        if state == "reverse_buyback_review" and action != "反T回补" and any(keyword in action for keyword in ("T", "买入", "追买")):
            reason = "先处理已打开的反T腿；新增做T或追买不能和回补闭环混在一起。"
        elif state == "exit_risk_review" and any(keyword in action for keyword in ("T", "买入", "追买")):
            reason = "硬止损或近硬止损优先，做T/买入不能扩大风险。"
        elif mode != "tradable":
            reason = f"盘中可信度为{decision_mode.get('label') or mode}，该动作只能保留观察。"
        elif minute_status != "confirm" and any(keyword in action for keyword in ("T", "买入", "止损复核")):
            reason = f"{minute_label}，分钟二次确认通过前不执行该动作。"
        elif row.get("status") == "blocked":
            reason = row.get("note") or row.get("status_label") or "该动作当前被规则阻断。"
        elif row.get("status") == "watch":
            reason = row.get("trigger") or "该动作尚未到触发条件。"
        else:
            reason = row.get("note") or "优先级低于当前主动作。"
        suppressed.append(
            {
                "action": action,
                "status": row.get("status"),
                "status_label": row.get("status_label"),
                "reason": reason,
            }
        )
    return {
        "primary_action": primary_action,
        "primary_status": primary.get("status"),
        "summary": summary,
        "suppressed_actions": suppressed[:6],
    }


def build_structured_conclusion(
    state: str,
    price_action_table: dict[str, Any],
    action_arbitration: dict[str, Any],
    decision_mode: dict[str, Any],
    minute_confirmation: dict[str, Any] | None,
    blockers: list[str],
    next_step: str,
    action_steps: list[str],
    levels: dict[str, Any],
) -> dict[str, Any]:
    primary = price_action_table.get("primary_action") or {}
    primary_action = str(primary.get("action") or action_arbitration.get("primary_action") or "当前动作")
    primary_status = str(primary.get("status") or "watch")
    primary_status_label = str(primary.get("status_label") or primary_status)
    primary_price = str(primary.get("price") or "-")
    trigger = str(primary.get("trigger") or primary.get("price") or next_step or "等待下一轮实时数据确认")
    if primary_action == "当前动作" and state in {"market_wait", "data_stale", "data_insufficient"}:
        trigger = next_step or trigger
    minute_label = str((minute_confirmation or {}).get("status_label") or "分钟未确认")
    mode_label = str(decision_mode.get("label") or "只观察")
    current_action = "只观察，不下单"
    if primary_status == "ready":
        current_action = f"人工确认后执行{primary_action}"
    elif state == "reverse_buyback_review":
        current_action = f"先复核{primary_action}，不新增卖出腿"
    elif state == "exit_risk_review":
        current_action = f"先处理{primary_action}，不做其他交易"
    elif state == "risk_downgrade_watch":
        current_action = "风险降级为观察复核，不主动交易"
    elif state in {"market_wait", "data_stale", "data_insufficient"}:
        current_action = "先刷新/修复数据，不交易"
    elif primary_status == "blocked":
        current_action = f"不交易，先处理{primary_action}阻断"
    elif state in {"positive_t_watch", "reverse_t_watch"}:
        current_action = f"只观察{primary_action}，等待触发和门禁确认"

    forbidden_actions: list[str] = []
    if state == "reverse_buyback_review":
        forbidden_actions.append("禁止新增反T卖出、补仓和追买；只允许处理已卖出腿的回补，或确认转为减仓。")
    if state == "exit_risk_review":
        forbidden_actions.append("禁止补仓、禁止正T、禁止反T，卖出风险优先。")
    if state == "risk_downgrade_watch":
        forbidden_actions.append("路径3只是解除退出风险优先，不代表可以买入、补仓或做T。")
    if decision_mode.get("mode") != "tradable":
        forbidden_actions.append(f"盘中可信度为“{mode_label}”时，禁止人工确认交易。")
    if (minute_confirmation or {}).get("status") != "confirm":
        forbidden_actions.append(f"{minute_label}时，禁止正T、反T和放宽止损。")
    if primary_status == "blocked" and primary.get("note"):
        forbidden_actions.append(str(primary.get("note")))
    forbidden_actions.extend(str(item) for item in blockers[:2])
    if not forbidden_actions:
        forbidden_actions.append("未到触发条件前不提前下单，不追价，不临时改方向。")

    summary = f"{current_action}；触发条件：{trigger}；禁止动作：{forbidden_actions[0]}"
    return {
        "current_action": current_action,
        "trigger_condition": trigger,
        "forbidden_actions": dedupe_keep_order(forbidden_actions)[:5],
        "summary": summary,
        "primary_action": primary_action,
        "primary_status": primary_status,
        "primary_status_label": primary_status_label,
        "primary_price": primary_price,
        "primary_shares": primary.get("shares"),
        "next_step": next_step,
        "first_step": action_steps[0] if action_steps else "",
        "minute_confirmation_label": minute_label,
        "decision_mode_label": mode_label,
        "dynamic_stop_loss_price": levels.get("dynamic_stop_loss_price"),
        "stop_loss_confirmed": levels.get("stop_loss_confirmed"),
    }


def confidence_for(state: str, evidence: list[str], blockers: list[str]) -> str:
    if state in {"exit_risk_review", "reverse_buyback_review", "risk_downgrade_watch", "data_stale", "market_wait", "data_insufficient"}:
        return "high"
    if blockers:
        return "medium"
    if len(evidence) >= 4:
        return "medium"
    return "low"


def queue_category(card: dict[str, Any]) -> tuple[str, str, int]:
    state = str(card.get("state") or "")
    primary = value_at(card, "price_action_table.primary_action") or {}
    primary_action = str(primary.get("action") or "")
    primary_status = str(primary.get("status") or "")
    review_status = str(value_at(card, "post_unlock_review_summary.status") or "")
    if primary_action == "反T回补":
        return "manual_candidate", "反T回补复核", 97 if primary_status == "ready" else 74
    if state == "reverse_buyback_review":
        return "manual_candidate", "反T回补复核", 74
    if primary_action in {"止损/退出", "止损减仓", "硬退出"} and primary_status == "ready":
        return "risk_exit", "优先处理卖出风险", 100
    if primary_action == "止损风险复核":
        return "risk_exit", "先复核硬止损风险", 99
    if primary_action == "反弹减仓":
        return "risk_exit", "等待反弹减仓", 98
    if state == "risk_downgrade_watch":
        return "observe", "风险降级观察", 58
    if state == "exit_risk_review":
        return "risk_exit", "优先处理卖出风险", 95
    if state in {"data_stale", "market_wait", "data_insufficient"}:
        return "data_fix", "先修复数据", 86 if state == "data_insufficient" else 82
    if state == "risk_reduction_review":
        return "risk_reduction", "复核降仓", 78
    if review_status == "manual_candidate":
        return "manual_candidate", "人工候选复核", 70
    if state in {"positive_t_watch", "reverse_t_watch"}:
        return "watch_candidate", "候选观察", 62
    if state == "hold_no_add":
        return "blocked_watch", "禁止操作只观察", 45
    return "observe", "继续观察", 20


def build_portfolio_priority_queue(cards: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for card in cards:
        category, category_label, base_rank = queue_category(card)
        primary = value_at(card, "price_action_table.primary_action") or {}
        blockers = card.get("blockers") or []
        review = card.get("post_unlock_review_summary") or {}
        t_gate = card.get("t_performance_gate") or {}
        q_gate = card.get("execution_quality_gate") or {}
        rank = base_rank
        if primary.get("status") == "ready":
            rank += 8
        if blockers:
            rank += 2 if category in {"risk_exit", "data_fix", "risk_reduction"} else -4
        if q_gate.get("status") == "blocked" or t_gate.get("status") == "blocked":
            rank -= 3 if category not in {"risk_exit", "data_fix"} else 0
        action_label = card.get("decision", {}).get("action_label") or category_label
        if primary.get("action") and primary.get("status") == "ready":
            action_label = f"{primary.get('action')}：{primary.get('operation') or action_label}"
        elif category == "data_fix":
            action_label = "补齐数据后再决策"
        elif category == "blocked_watch":
            action_label = "禁止买入、补仓、做T"
        next_step = card.get("decision", {}).get("next_step") or review.get("next_step") or ""
        items.append(
            {
                "rank": rank,
                "urgency": "high" if rank >= 85 else "medium" if rank >= 60 else "low",
                "category": category,
                "category_label": category_label,
                "code": card.get("code"),
                "name": card.get("name"),
                "state": card.get("state"),
                "state_label": card.get("state_label"),
                "action_label": action_label,
                "next_step": next_step,
                "reason": card.get("reason"),
                "primary_action": primary,
                "blocking_checks": review.get("blocking_checks") or [],
                "t_performance_status": t_gate.get("status_label") or t_gate.get("status"),
                "execution_quality_status": q_gate.get("status_label") or q_gate.get("status"),
            }
        )
    ordered = sorted(items, key=lambda item: (-int(item.get("rank") or 0), str(item.get("code") or "")))
    counts: dict[str, int] = {}
    for item in ordered:
        category = str(item.get("category") or "unknown")
        counts[category] = counts.get(category, 0) + 1
    return {
        "items": ordered,
        "top_items": ordered[:8],
        "counts": dict(sorted(counts.items())),
        "summary": "按风险优先、数据可用、人工候选、禁止操作、观察的顺序处理；队列只排序，不自动下单。",
    }


def technical_decision_note(technical_assessment: dict[str, Any]) -> str | None:
    label = technical_assessment.get("label")
    if label == "bearish":
        return "技术指标偏弱时，不放宽做T限制；等日线或周线动能修复后再评估。"
    if label == "bullish":
        return "技术指标偏多，但仍只能作为观察证据，不能替代止损和人工确认。"
    if label in {"slightly_bearish", "slightly_bullish"}:
        return "技术指标只有轻微信号，继续结合实时价、止损距离和成交量验证。"
    return None


def build_card(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    action_backtest: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    reverse_forecast: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    technical_indicators: dict[str, Any] | None = None,
    total_assets: float | None = None,
    minute_bars: list[dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
    supplemental_capital_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    technical_assessment = build_technical_assessment(technical_indicators)
    technical_operation = build_technical_operation(technical_assessment)
    technical_operation["post_unlock_checklist"] = technical_post_unlock_checklist(technical_operation)
    decision_mode = build_decision_mode(data_quality)
    minute_confirmation = build_minute_confirmation(intraday, minute_bars, technical_assessment)
    positive_timing = build_positive_timing(intraday, t_check, minute_bars, technical_assessment, technical_operation)
    t_performance_gate = build_t_performance_gate(intraday)
    execution_quality_gate = build_execution_quality_gate(intraday)
    liquidity_activity_gate = build_liquidity_activity_gate(intraday, technical_assessment, data_quality)
    state, reason = choose_state(
        intraday,
        portfolio,
        t_check,
        reverse_backtest,
        data_quality,
        technical_assessment,
        t_performance_gate,
        execution_quality_gate,
        minute_confirmation,
    )
    evidence = build_evidence(
        intraday,
        portfolio,
        t_check,
        action_backtest,
        reverse_backtest,
        reverse_forecast,
        data_quality,
        technical_assessment,
        positive_timing,
        minute_confirmation,
        t_performance_gate,
        execution_quality_gate,
        liquidity_activity_gate,
    )
    daily_trade_rhythm = intraday.get("daily_trade_rhythm") or {}
    blockers = build_blockers(
        intraday,
        portfolio,
        t_check,
        reverse_backtest,
        data_quality,
        technical_assessment,
        t_performance_gate,
        execution_quality_gate,
        liquidity_activity_gate,
        daily_trade_rhythm,
        minute_confirmation,
    )
    levels = price_levels(portfolio, t_check, intraday, reverse_forecast, generated_at, technical_assessment)
    technical_operation["post_unlock_review"] = build_post_unlock_review(
        technical_operation,
        state,
        levels,
        data_quality,
        positive_timing,
        minute_confirmation,
        reverse_backtest,
        intraday,
        t_performance_gate,
        execution_quality_gate,
        liquidity_activity_gate,
    )
    post_unlock_review_summary = build_post_unlock_review_summary(technical_operation["post_unlock_review"])
    capital_plan = build_capital_plan(
        state,
        levels,
        intraday.get("position", {}),
        total_assets=total_assets,
        technical_assessment=technical_assessment,
        positive_timing=positive_timing,
        minute_confirmation=minute_confirmation,
        supplemental_capital_policy=supplemental_capital_policy,
    )
    manual_execution_plan = build_manual_execution_plan(
        post_unlock_review_summary,
        levels,
        intraday.get("position", {}),
        capital_plan,
        positive_timing,
        intraday,
        state,
        technical_assessment,
        minute_confirmation,
        daily_trade_rhythm,
    )
    price_action_table = build_price_action_table(
        state,
        levels,
        intraday,
        capital_plan,
        positive_timing,
        manual_execution_plan,
        t_performance_gate,
        execution_quality_gate,
        data_quality,
        minute_confirmation,
        liquidity_activity_gate,
    )
    action_arbitration = build_action_arbitration(state, price_action_table, decision_mode, minute_confirmation)
    next_step = build_next_step(state, action_backtest, levels)
    action_steps = build_action_steps(
        state,
        levels,
        blockers,
        action_backtest,
        code=str(intraday.get("code") or ""),
        name=str(intraday.get("name") or ""),
        position=intraday.get("position", {}),
        capital_plan=capital_plan,
        technical_operation=technical_operation,
        t_performance_gate=t_performance_gate,
        execution_quality_gate=execution_quality_gate,
        liquidity_activity_gate=liquidity_activity_gate,
        manual_execution_plan=manual_execution_plan,
        daily_trade_rhythm=daily_trade_rhythm,
    )
    structured_conclusion = build_structured_conclusion(
        state,
        price_action_table,
        action_arbitration,
        decision_mode,
        minute_confirmation,
        blockers,
        next_step,
        action_steps,
        levels,
    )
    action_code = {
        "market_wait": "wait_for_market_session",
        "data_stale": "pause_intraday_decision",
        "exit_risk_review": "create_exit_or_risk_review",
        "risk_downgrade_watch": "watch_risk_downgrade",
        "reverse_buyback_review": "review_reverse_t_buyback",
        "data_insufficient": "complete_data_before_decision",
        "risk_reduction_review": "review_position_reduction",
        "positive_t_watch": "watch_positive_t_only",
        "reverse_t_watch": "watch_reverse_t_only",
        "hold_no_add": "hold_without_adding",
        "observe": "do_nothing",
    }[state]
    execution_allowed = False
    if state in {"positive_t_watch", "reverse_t_watch"} and not blockers:
        execution_allowed = False
    return {
        "code": intraday.get("code"),
        "name": intraday.get("name"),
        "state": state,
        "state_label": STATE_LABELS[state],
        "reason": reason,
        "decision": {
            "action": action_code,
            "action_label": ACTION_LABELS[action_code],
            "execution_allowed": execution_allowed,
            "confidence": confidence_for(state, evidence, blockers),
            "next_step": next_step,
            "action_steps": action_steps,
            "structured_conclusion": structured_conclusion,
            "technical_operation": technical_operation,
            "action_arbitration": action_arbitration,
        },
        "price_levels": levels,
        "price_action_table": price_action_table,
        "capital_plan": capital_plan,
        "t_performance_gate": t_performance_gate,
        "execution_quality_gate": execution_quality_gate,
        "liquidity_activity_gate": liquidity_activity_gate,
        "execution_quality_summary": intraday.get("execution_quality_summary") or {},
        "daily_trade_rhythm": daily_trade_rhythm,
        "t_closure_performance": intraday.get("t_closure_performance") or {},
        "positive_timing": positive_timing,
        "minute_confirmation": minute_confirmation,
        "post_unlock_review_summary": post_unlock_review_summary,
        "manual_execution_plan": manual_execution_plan,
        "decision_mode": decision_mode,
        "position": intraday.get("position", {}),
        "market_context": {
            "quote_lag_seconds": value_at(intraday, "quote.quote_lag_seconds"),
            "change_pct": value_at(intraday, "quote.change_pct"),
            "main_net_inflow_ratio_pct": value_at(intraday, "capital_flow.main_net_inflow_ratio_pct"),
            "t_market_setup": (t_check or {}).get("market_setup"),
            "t_conclusion": (t_check or {}).get("conclusion"),
            "reverse_t_status": value_at(intraday, "reverse_t_plan.status"),
            "reverse_t_backtest_verdict": (reverse_backtest or {}).get("verdict"),
            "reverse_t_forecast_status": (reverse_forecast or {}).get("status"),
            "action_backtest_weak_rule_count": (action_backtest or {}).get("weak_rule_count"),
            "data_quality_status": data_quality_status(data_quality),
            "data_trust_level": data_trust_level(data_quality),
            "source_consistency_status": source_consistency_status(data_quality),
            "market_session_phase": market_session(data_quality).get("phase"),
            "market_session_label": market_session(data_quality).get("label"),
            "live_quote_required": market_session(data_quality).get("live_quote_required"),
            "technical_score": technical_assessment.get("score"),
            "technical_label": technical_assessment.get("label"),
            "positive_timing_score": positive_timing.get("score"),
            "positive_timing_status": positive_timing.get("status"),
            "minute_confirmation_status": minute_confirmation.get("status"),
            "minute_confirmation_score": minute_confirmation.get("score"),
            "t_performance_status": t_performance_gate.get("status"),
            "t_performance_total_count": t_performance_gate.get("total_count"),
            "t_performance_total_net_profit": t_performance_gate.get("total_net_profit"),
            "execution_quality_status": execution_quality_gate.get("status"),
            "execution_quality_average_score": execution_quality_gate.get("average_score"),
            "execution_quality_review_count": execution_quality_gate.get("review_count"),
            "liquidity_activity_status": liquidity_activity_gate.get("status"),
            "liquidity_activity_turnover": liquidity_activity_gate.get("turnover"),
            "liquidity_activity_daily_volume_ratio_20": liquidity_activity_gate.get("daily_volume_ratio_20"),
            "decision_mode": decision_mode.get("mode"),
            "decision_mode_label": decision_mode.get("label"),
        },
        "technical_assessment": technical_assessment,
        "data_quality": data_quality or {},
        "blockers": blockers,
        "evidence": evidence,
        "guardrails": [
            "本卡片只做决策辅助，不自动下单。",
            "任何买入、卖出、做T或降仓动作都必须先生成计划并人工确认。",
            "行情过期、触发止损、接近硬阻断止损、跌停或数据不足时禁止做T。",
        ],
    }


def manual_buy_amount_today(card: dict[str, Any]) -> float:
    total = 0.0
    for trade in (card.get("daily_trade_rhythm") or {}).get("recent_trades") or []:
        if str(trade.get("side") or "").lower() != "buy":
            continue
        price = as_float(trade.get("price"))
        shares = as_float(trade.get("shares"))
        if price is not None and shares is not None:
            total += price * shares
    return total


def link_intraday_capital_usage(
    cards: list[dict[str, Any]],
    total_assets: float | None,
    supplemental_capital_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = supplemental_capital_policy or supplemental_capital_policy_from_profile()
    if total_assets in (None, 0):
        return {
            "available": False,
            "status": "missing_total_assets",
            "status_label": "缺少总资产，不能联动盘中资金预算",
            "total_assets": total_assets,
            "max_intraday_add_amount": None,
            "used_buy_amount": None,
            "reserved_candidate_amount": None,
            "remaining_add_amount": None,
            "candidate_count": 0,
        }

    budget = float(total_assets) * float(policy["max_intraday_add_pct_total_assets"]) / 100
    used_buy_amount = sum(manual_buy_amount_today(card) for card in cards)
    remaining = max(0.0, budget - used_buy_amount)
    candidates = [
        card
        for card in cards
        if (card.get("capital_plan") or {}).get("applicable")
        and (card.get("capital_plan") or {}).get("suggested_buy_shares")
        and (card.get("capital_plan") or {}).get("estimated_buy_amount")
    ]
    candidates.sort(
        key=lambda card: (
            -decision_priority(str(card.get("state") or "")),
            0 if (card.get("post_unlock_review_summary") or {}).get("status") == "manual_candidate" else 1,
            str(card.get("code") or ""),
        )
    )

    reserved = 0.0
    allocations: list[dict[str, Any]] = []
    for card in candidates:
        plan = card.get("capital_plan") or {}
        estimated = as_float(plan.get("estimated_buy_amount"), 0.0) or 0.0
        enough = estimated <= remaining
        allocated = estimated if enough else 0.0
        if enough:
            remaining -= estimated
            reserved += estimated
        link = {
            "status": "allocated" if enough else "portfolio_budget_blocked",
            "status_label": "组合预算已预留" if enough else "组合日内新增预算不足",
            "allocated_amount": rounded(allocated),
            "requested_amount": rounded(estimated),
            "remaining_after_allocation": rounded(remaining),
            "max_intraday_add_amount": rounded(budget),
            "used_buy_amount": rounded(used_buy_amount),
            "max_intraday_add_pct_total_assets": policy["max_intraday_add_pct_total_assets"],
            "next_step": "可继续做个股执行前检查。" if enough else "今天不再为该候选追加资金；除非先释放预算或下一轮重新排序。",
        }
        plan["portfolio_capital_link"] = link
        if not enough and plan.get("status") in {"watch", "ready_for_manual_confirm"}:
            plan["status"] = "portfolio_budget_blocked"
            plan["status_label"] = "组合日内新增资金预算不足"
            plan.setdefault("reasons", []).append("多个候选共享日内新增资金预算，本候选未获得预算预留。")
            plan.setdefault("steps", []).insert(0, "组合日内新增资金预算不足，本轮不买入；只观察价格和技术确认。")
            manual_plan = card.get("manual_execution_plan") or {}
            if manual_plan.get("candidate") == "positive_t":
                manual_plan["status"] = "blocked"
                manual_plan["status_label"] = "组合预算不足，禁止正T买入"
                manual_plan["steps"] = [
                    "组合日内新增资金预算不足，本轮不填写买入单。",
                    "继续观察该股价格和技术确认；若下一轮预算释放，系统会重新计算。",
                ]
            for row in (card.get("price_action_table") or {}).get("rows") or []:
                if row.get("action") == "正T买入":
                    row["status"] = "blocked"
                    row["status_label"] = "组合预算阻断"
                    row["operation"] = "不买入"
                    row["note"] = "组合日内新增资金预算不足，本轮不执行正T买入。"
                    row["priority"] = price_action_priority("正T买入", "blocked")
            rows = (card.get("price_action_table") or {}).get("rows") or []
            if rows:
                ordered_rows = sorted(enumerate(rows), key=lambda item: (-int(item[1].get("priority") or 0), item[0]))
                card["price_action_table"]["rows"] = [row for _, row in ordered_rows]
                card["price_action_table"]["primary_action"] = card["price_action_table"]["rows"][0]
        allocations.append(
            {
                "code": card.get("code"),
                "name": card.get("name"),
                "status": link["status"],
                "requested_amount": link["requested_amount"],
                "allocated_amount": link["allocated_amount"],
            }
        )

    return {
        "available": True,
        "status": "budget_available" if remaining > 0 else "budget_exhausted",
        "status_label": "日内新增资金预算可用" if remaining > 0 else "日内新增资金预算已用尽",
        "total_assets": rounded(float(total_assets)),
        "max_intraday_add_pct_total_assets": policy["max_intraday_add_pct_total_assets"],
        "max_intraday_add_amount": rounded(budget),
        "used_buy_amount": rounded(used_buy_amount),
        "reserved_candidate_amount": rounded(reserved),
        "remaining_add_amount": rounded(remaining),
        "candidate_count": len(candidates),
        "allocations": allocations,
        "scope": "只约束新增买入和正T买入；反T回补、风控卖出、减仓不占用新增资金预算。",
    }


def build_report(
    intraday_snapshot: dict[str, Any],
    portfolio_check: dict[str, Any] | None,
    t_opportunities: dict[str, Any] | None,
    action_backtests: dict[str, Any] | None,
    reverse_t_backtest: dict[str, Any] | None,
    reverse_t_forecast: dict[str, Any] | None,
    data_quality: dict[str, Any] | None = None,
    technical_indicators: dict[str, Any] | None = None,
    minute_bars: dict[str, list[dict[str, Any]]] | None = None,
    *,
    generated_at: datetime | None = None,
    investment_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at_value = generated_at or datetime.now().astimezone()
    supplemental_capital_policy = supplemental_capital_policy_from_profile(investment_profile)
    portfolio_by_code = index_portfolio_check(portfolio_check)
    t_by_code = index_t_checks(t_opportunities)
    action_backtest_by_code = index_action_backtests(action_backtests)
    reverse_backtest_by_code = index_simple_items(reverse_t_backtest)
    reverse_forecast_by_code = index_simple_items(reverse_t_forecast)
    data_quality_by_code = index_simple_items(data_quality)
    technical_by_code = index_technical_indicators(technical_indicators)
    minute_by_code = minute_bars or {}
    total_assets = as_float(intraday_snapshot.get("total_assets"))
    cards = [
        build_card(
            item,
            portfolio_by_code.get(str(item.get("code"))),
            t_by_code.get(str(item.get("code"))),
            action_backtest_by_code.get(str(item.get("code"))),
            reverse_backtest_by_code.get(str(item.get("code"))),
            reverse_forecast_by_code.get(str(item.get("code"))),
            data_quality_by_code.get(str(item.get("code"))),
            technical_by_code.get(str(item.get("code"))),
            total_assets,
            minute_by_code.get(str(item.get("code"))),
            generated_at_value,
            supplemental_capital_policy,
        )
        for item in intraday_snapshot.get("items", [])
    ]
    intraday_capital_usage = link_intraday_capital_usage(cards, total_assets, supplemental_capital_policy)
    state_counts: dict[str, int] = {}
    for card in cards:
        state_counts[card["state"]] = state_counts.get(card["state"], 0) + 1
    technical_unlock_alerts = sorted(
        (alert for card in cards if (alert := build_technical_unlock_alert(card))),
        key=lambda alert: (0 if alert.get("type") == "technical_unlocked" else 1, as_float(alert.get("min_gap"), 9999.0) or 9999.0, str(alert.get("code") or "")),
    )
    post_unlock_review_alerts = sorted(
        (alert for card in cards if (alert := build_post_unlock_review_alert(card))),
        key=lambda alert: (0 if alert.get("severity") == "action" else 1, str(alert.get("code") or "")),
    )
    intraday_trigger_alerts = sorted(
        (alert for card in cards if (alert := build_intraday_trigger_alert(card))),
        key=lambda alert: (0 if alert.get("severity") == "action" else 1, str(alert.get("code") or "")),
    )
    priority_queue = build_portfolio_priority_queue(cards)
    return {
        "generated_at": generated_at_value.isoformat(timespec="seconds"),
        "source": {
            "intraday_generated_at": intraday_snapshot.get("generated_at"),
            "portfolio_check_available": portfolio_check is not None,
            "t_opportunities_available": t_opportunities is not None,
            "action_backtests_available": action_backtests is not None,
            "reverse_t_backtest_available": reverse_t_backtest is not None,
            "reverse_t_forecast_available": reverse_t_forecast is not None,
            "data_quality_available": data_quality is not None,
            "technical_indicators_available": technical_indicators is not None,
            "minute_bars_available": bool(minute_by_code),
            "investment_profile_available": investment_profile is not None,
        },
        "card_count": len(cards),
        "state_counts": dict(sorted(state_counts.items())),
        "intraday_capital_usage": intraday_capital_usage,
        "intraday_trigger_alerts": intraday_trigger_alerts,
        "technical_unlock_alerts": technical_unlock_alerts,
        "post_unlock_review_alerts": post_unlock_review_alerts,
        "priority_queue": priority_queue,
        "cards": sorted(cards, key=lambda card: (-decision_priority(card["state"]), str(card["code"]))),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 实时持仓决策卡",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "本报告只做人工决策辅助，不自动下单；所有动作必须先生成计划并人工确认。",
        "",
        "## 汇总",
        "",
        f"- 卡片数：{report['card_count']}",
        f"- 状态分布：{report['state_counts']}",
        "",
        "## 盘中盯盘提醒",
        "",
    ]
    trigger_alerts = report.get("intraday_trigger_alerts") or []
    if trigger_alerts:
        for alert in trigger_alerts[:10]:
            lines.append(
                f"- {alert.get('code')} {alert.get('name')}：{alert.get('title')}；"
                f"{alert.get('action_label')}。{alert.get('message')}"
            )
    else:
        lines.append("- 暂无触发提醒。")
    lines.extend([
        "",
        "## 今日处理顺序",
        "",
        str(value_at(report, "priority_queue.summary") or "按队列顺序处理；队列只排序，不自动下单。"),
        "",
    ])
    queue_items = value_at(report, "priority_queue.top_items") or []
    if queue_items:
        lines.extend(["| 顺序 | 代码 | 名称 | 分类 | 动作 | 下一步 |", "| ---: | --- | --- | --- | --- | --- |"])
        for index, item in enumerate(queue_items, start=1):
            lines.append(
                f"| {index} | {item.get('code')} | {item.get('name')} | {item.get('category_label')} | "
                f"{item.get('action_label')} | {item.get('next_step') or '-'} |"
            )
        lines.append("")
    lines.extend([
        "| 代码 | 名称 | 状态 | 当前价 | 止损 | 阻断价 | 技术分 | 动作 | 置信度 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ])
    for card in report["cards"]:
        levels = card["price_levels"]
        decision = card["decision"]
        technical = card.get("technical_assessment") or {}
        lines.append(
            f"| {card['code']} | {card['name']} | {card['state_label']} | "
            f"{levels.get('current_price') if levels.get('current_price') is not None else '-'} | "
            f"{levels.get('stop_loss_price') if levels.get('stop_loss_price') is not None else '-'} | "
            f"{levels.get('near_stop_block_price') if levels.get('near_stop_block_price') is not None else '-'} | "
            f"{technical.get('score') if technical.get('score') is not None else '-'} | "
            f"{decision['action_label']} | {decision['confidence']} |"
        )
    lines.extend(["", "## 明细", ""])
    for card in report["cards"]:
        decision = card["decision"]
        levels = card["price_levels"]
        lines.extend(
            [
                f"### {card['code']} {card['name']}",
                "",
                f"- 状态：{card['state_label']}",
                f"- 建议：{decision['action_label']}；执行许可：{decision['execution_allowed']}",
                f"- 下一步：{decision['next_step']}",
                f"- 关键价格：当前 {levels.get('current_price') or '-'}，止损 {levels.get('stop_loss_price') or '-'}，做T阻断价 {levels.get('near_stop_block_price') or '-'}，MA20 {levels.get('ma20') or '-'}",
            ]
        )
        capital_plan = card.get("capital_plan") or {}
        if capital_plan.get("applicable"):
            lines.append(
                f"- 追加资金计划：{capital_plan.get('status_label')}；"
                f"上限 {capital_plan.get('max_additional_capital') or '-'}，"
                f"建议 {capital_plan.get('suggested_buy_shares') or 0} 股"
            )
        if decision.get("action_steps"):
            lines.append("- 操作步骤：")
            lines.extend(f"  - {item}" for item in decision["action_steps"][:6])
        action_table = card.get("price_action_table") or {}
        action_rows = action_table.get("rows") or []
        if action_rows:
            lines.append("- 价格动作表：")
            for row in action_rows[:6]:
                shares_text = f"，{row.get('shares')}股" if row.get("shares") else ""
                lines.append(
                    f"  - {row.get('action')}：{row.get('trigger')} -> {row.get('operation')}，"
                    f"{row.get('status_label')}{shares_text}。{row.get('note') or ''}"
                )
        positive_timing = card.get("positive_timing") or {}
        if positive_timing.get("available"):
            lines.append(
                f"- 正T分时评分：{positive_timing.get('score')} / {positive_timing.get('threshold')}，状态 {positive_timing.get('status')}"
            )
        note = technical_decision_note(card.get("technical_assessment") or {})
        if note:
            lines.append(f"- 技术判断：{note}")
        review_summary = card.get("post_unlock_review_summary") or {}
        if review_summary:
            lines.append(f"- 解锁后复核：{review_summary.get('status_label')}；下一步：{review_summary.get('next_step') or '-'}")
        manual_plan = card.get("manual_execution_plan") or {}
        if manual_plan.get("applicable"):
            if manual_plan.get("plan_type") == "near_stop_playbook":
                lines.append(f"- 人工候选计划：{manual_plan.get('status_label')}；等待三路径触发")
            else:
                lines.append(
                    f"- 人工候选计划：{manual_plan.get('status_label')}；"
                    f"{manual_plan.get('side_label') or '-'} {manual_plan.get('shares') or 0} 股"
                )
        if card["blockers"]:
            lines.append("- 阻断：")
            lines.extend(f"  - {item}" for item in card["blockers"][:4])
        lines.append("- 证据：")
        lines.extend(f"  - {item}" for item in card["evidence"][:6])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build realtime decision cards for current holdings.")
    parser.add_argument("--intraday-snapshot", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--portfolio-check", default="data/metadata/eastmoney-portfolio-check.after-threshold.json")
    parser.add_argument("--t-opportunities", default="data/metadata/eastmoney-portfolio-t-opportunities.near-config.json")
    parser.add_argument("--action-backtests", default="data/metadata/portfolio-action-matrix-backtests.after-plan.json")
    parser.add_argument("--reverse-t-backtest", default="data/metadata/reverse-t-backtest.json")
    parser.add_argument("--reverse-t-forecast", default="data/metadata/reverse-t-forecast.json")
    parser.add_argument("--data-quality", default="data/metadata/data-quality-snapshot.json")
    parser.add_argument("--technical-indicators", default="data/metadata/technical-indicators.json")
    parser.add_argument("--minute-cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--profile", default="config/investment-profile.yaml")
    parser.add_argument("--output", default="data/metadata/realtime-decision-cards.json")
    parser.add_argument("--markdown-output", default="reports/realtime-decision-cards.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        intraday_snapshot = load_json_if_exists(Path(args.intraday_snapshot))
        if intraday_snapshot is None:
            raise ValueError(f"missing intraday snapshot: {args.intraday_snapshot}")
        report = build_report(
            intraday_snapshot,
            load_json_if_exists(Path(args.portfolio_check)),
            load_json_if_exists(Path(args.t_opportunities)),
            load_json_if_exists(Path(args.action_backtests)),
            load_json_if_exists(Path(args.reverse_t_backtest)),
            load_json_if_exists(Path(args.reverse_t_forecast)),
            load_json_if_exists(Path(args.data_quality)),
            load_json_if_exists(Path(args.technical_indicators)),
            load_minute_bars(Path(args.minute_cache_dir)),
            investment_profile=load_yaml(Path(args.profile)) if Path(args.profile).exists() else None,
        )
        write_json(Path(args.output), report)
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(render_markdown(report), encoding="utf-8")
    except Exception as exc:
        print(f"build realtime decision cards failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"cards: {report['card_count']}, states: {report['state_counts']}")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
