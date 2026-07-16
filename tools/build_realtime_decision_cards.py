#!/usr/bin/env python3
"""Build per-holding realtime decision cards from existing monitoring artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, value_at
except ModuleNotFoundError:
    from risk_check import as_float, value_at


STATE_LABELS = {
    "market_wait": "非交易时段，等待行情",
    "data_stale": "行情过期，暂停盘中判断",
    "exit_risk_review": "退出风险优先",
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
POSITIVE_T_SCORE_THRESHOLD = 65.0
SUPPLEMENTAL_CAPITAL_POLICY = {
    "supplemental_capital_allowed": True,
    "account_cash_required": False,
    "base_single_add_pct_total_assets": 3.0,
    "strong_single_add_pct_total_assets": 5.0,
    "max_single_add_pct_total_assets": 5.0,
    "max_stock_position_pct_after_add": 12.0,
    "max_added_risk_pct_total_assets": 0.5,
    "min_stop_buffer_pct": 3.0,
    "min_target_gap_pct": 1.2,
}


def load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def decision_priority(state: str) -> int:
    return {
        "exit_risk_review": 90,
        "data_stale": 80,
        "market_wait": 75,
        "data_insufficient": 70,
        "risk_reduction_review": 60,
        "positive_t_watch": 45,
        "reverse_t_watch": 40,
        "hold_no_add": 50,
        "observe": 10,
    }.get(state, 0)


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


def build_technical_assessment(technical_indicators: dict[str, Any] | None) -> dict[str, Any]:
    if not technical_indicators:
        return {"available": False, "score": None, "label": "missing", "signals": [], "periods": {}}
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
        "signals": signals[:8],
        "periods": period_summary,
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


def latest_day_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not bars:
        return []
    latest_date = str(bars[-1].get("timestamp") or "")[:10]
    return [bar for bar in bars if str(bar.get("timestamp") or "").startswith(latest_date)]


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
) -> dict[str, Any]:
    if not t_check or t_check.get("conclusion") != "positive_t_candidate":
        return {"available": False, "status": "not_applicable", "score": None, "signals": [], "blockers": [], "next_action": "当前不是正T候选，不评估正T买入腿。", "buy_zone": None, "target_sell_zone": None}
    day_bars = latest_day_bars(minute_bars or [])
    if len(day_bars) < 20:
        return {
            "available": False,
            "status": "insufficient",
            "score": None,
            "signals": [f"最新交易日5分钟线数量 {len(day_bars)} 少于20，不能确认正T买点。"],
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
    technical_supported = technical_label not in {"bearish", "slightly_bearish"}
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
        signals.append(f"日/周/月技术背景为 {technical_label}，不允许仅凭分时信号做正T。")
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
    status = "confirmed" if score >= POSITIVE_T_SCORE_THRESHOLD and confirmation_count >= 2 and technical_supported else "watch"
    blockers: list[dict[str, str]] = []
    if score < POSITIVE_T_SCORE_THRESHOLD:
        blockers.append(
            positive_t_blocker(
                "score_below_threshold",
                "分时评分",
                f"{score:.1f} / {POSITIVE_T_SCORE_THRESHOLD}",
                "分时趋势、回踩幅度、动能、量能和资金流的综合分还没有达到买入确认线。",
                f"继续观察，只有评分达到 {POSITIVE_T_SCORE_THRESHOLD:.0f} 分及以上才允许进入正T买入区间。",
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
                "higher_timeframe_weak",
                "日/周/月背景",
                technical_label,
                "大周期技术背景偏弱，不能只凭盘中反弹去追加资金做正T。",
                "等待技术背景恢复到 neutral 或更强；恢复前只允许持有观察、减仓或风险复核，不做正T买入腿。",
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
        "threshold": POSITIVE_T_SCORE_THRESHOLD,
        "latest_timestamp": day_bars[-1].get("timestamp"),
        "buy_zone": [rounded(buy_low), rounded(buy_high)] if status == "confirmed" else None,
        "target_sell_zone": [rounded(target_low), rounded(target_low + 0.02)] if status == "confirmed" else None,
        "signals": signals[:8],
        "blockers": blockers,
        "next_action": next_action,
        "metrics": {
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
            "technical_label": technical_label or None,
            "technical_supported": technical_supported,
        },
    }


def choose_state(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    technical_assessment: dict[str, Any] | None = None,
) -> tuple[str, str]:
    signal_codes = {item.get("code") for item in intraday.get("signals", [])}
    portfolio_action_codes = {item.get("code") for item in (portfolio or {}).get("actions", [])}
    t_blockers = {item.get("code") for item in (t_check or {}).get("blockers", [])}
    quality_status = data_quality_status(data_quality)
    trust_level = data_trust_level(data_quality)
    quote_wait = off_session_quote_wait(data_quality)
    states: list[tuple[str, str]] = []

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
    if portfolio_action_codes & {"stop_loss_triggered"} or t_blockers & HARD_T_BLOCKERS:
        states.append(("exit_risk_review", "触发或逼近硬风控，退出风险优先于做T。"))
    if t_blockers & DATA_BLOCKERS:
        states.append(("data_insufficient", "日线、止损或样本不足，不能验证交易环境。"))
    if portfolio_action_codes & {"stock_position_limit_exceeded", "industry_position_limit_exceeded", "total_position_limit_exceeded"}:
        states.append(("risk_reduction_review", "持仓或组合仓位超限，需要先复核降仓。"))
    if value_at(intraday, "reduction_plan.status") == "actionable":
        states.append(("risk_reduction_review", "实时市值测算显示可复核降仓。"))
    if t_check and t_check.get("conclusion") == "positive_t_candidate":
        states.append(("positive_t_watch", "日线环境进入正T观察候选。"))
    if value_at(intraday, "reverse_t_plan.status") == "candidate":
        states.append(("reverse_t_watch", "盘中价格进入反T观察候选。"))
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
) -> dict[str, Any]:
    calculations = (portfolio or {}).get("calculations", {})
    t_calculations = (t_check or {}).get("calculations", {})
    stop_loss = as_float(calculations.get("stop_loss_price"))
    warning_pct = as_float(calculations.get("near_stop_warning_pct"), 3.0) or 3.0
    block_pct = as_float(t_calculations.get("near_stop_block_pct"), 1.0) or 1.0
    near_warning_price = None
    near_block_price = None
    if stop_loss is not None:
        near_warning_price = stop_loss / (1 - warning_pct / 100)
        near_block_price = stop_loss / (1 - block_pct / 100)
    forecast_sell_zone = value_at(reverse_forecast or {}, "predicted_sell_zone")
    forecast_buyback = as_float(value_at(reverse_forecast or {}, "predicted_buyback_max_price"))
    intraday_sell_zone = value_at(intraday, "reverse_t_plan.sell_zone")
    intraday_buyback = as_float(value_at(intraday, "reverse_t_plan.buyback_max_price"))
    has_forecast = reverse_forecast is not None
    sell_zone = forecast_sell_zone if has_forecast else intraday_sell_zone
    buyback = forecast_buyback if has_forecast else intraday_buyback
    if forecast_sell_zone:
        zone_source = "forecast"
    elif has_forecast:
        zone_source = "forecast_unavailable"
    elif intraday_sell_zone:
        zone_source = "intraday_high_fallback"
    else:
        zone_source = None
    return {
        "current_price": rounded(as_float(value_at(intraday, "quote.latest_price"))),
        "stop_loss_price": rounded(stop_loss),
        "near_stop_warning_price": rounded(near_warning_price),
        "near_stop_block_price": rounded(near_block_price),
        "ma5": rounded(as_float(value_at(intraday, "technicals.ma5") or t_calculations.get("ma_short"))),
        "ma20": rounded(as_float(value_at(intraday, "technicals.ma20") or t_calculations.get("ma_mid"))),
        "recent_high": rounded(as_float(t_calculations.get("recent_high"))),
        "recent_low": rounded(as_float(t_calculations.get("recent_low"))),
        "reverse_t_sell_zone": sell_zone,
        "reverse_t_buyback_max_price": rounded(buyback),
        "reverse_t_zone_source": zone_source,
        "reverse_t_forecast_as_of": value_at(reverse_forecast or {}, "as_of"),
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
) -> list[str]:
    evidence: list[str] = []
    if positive_timing and positive_timing.get("available"):
        evidence.append(
            f"[正T分时评分] {positive_timing.get('status')} · score={positive_timing.get('score')} / {positive_timing.get('threshold')}"
        )
        for signal in positive_timing.get("signals", [])[:3]:
            evidence.append(f"[正T分时] {signal}")
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
    t_check: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
    technical_assessment: dict[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if data_quality_status(data_quality) in QUALITY_BLOCKER_STATUSES:
        blockers.extend(data_quality.get("blockers") or [])
    if data_trust_level(data_quality) == "low":
        blockers.extend((data_quality.get("data_trust") or {}).get("reasons") or [])
    if source_consistency_status(data_quality) == "conflict":
        blockers.extend((data_quality or {}).get("source_consistency", {}).get("issues") or [])
    blockers.extend(signal.get("message") for signal in intraday.get("signals", []) if signal.get("severity") in {"block", "risk"})
    blockers.extend(item.get("message") for item in (t_check or {}).get("blockers", []))
    if (technical_assessment or {}).get("label") == "bearish":
        blockers.append("多周期技术指标偏弱，本轮禁止补仓和做T。")
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


def build_capital_plan(
    state: str,
    levels: dict[str, Any],
    position: dict[str, Any],
    *,
    total_assets: float | None = None,
    technical_assessment: dict[str, Any] | None = None,
    positive_timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(SUPPLEMENTAL_CAPITAL_POLICY)
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
    target_high = target_low + 0.02
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
) -> list[str]:
    current = as_float(levels.get("current_price"))
    stop_loss = as_float(levels.get("stop_loss_price"))
    near_block = as_float(levels.get("near_stop_block_price"))
    shares = whole_lot_shares((position or {}).get("shares"))
    entry_price = as_float((position or {}).get("entry_price"))
    unrealized_pnl = as_float((position or {}).get("unrealized_pnl"))
    estimated_cash = current * shares if current is not None and shares is not None else None
    estimated_pnl = (current - entry_price) * shares if current is not None and entry_price is not None and shares is not None else unrealized_pnl
    loss_word = "亏损" if estimated_pnl is not None and estimated_pnl < 0 else "盈亏"
    if state == "exit_risk_review":
        steps = ["本轮禁止买入、补仓、做T；只允许处理卖出风险。"]
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
        if capital_plan and capital_plan.get("status") == "watch":
            steps.extend(capital_plan.get("steps", []))
        else:
            steps.append((capital_plan or {}).get("status_label") or "先写清买入价、卖出价、失败后是否接受加仓，以及新增仓位上限。")
        steps.append("价格、数据质量和止损距离同时满足后，才生成做T计划并人工确认。")
        return steps
    if state == "reverse_t_watch":
        return [
            "只允许加入人工观察，不直接卖出。",
            "必须确认5分钟回测、费用模型、分时转弱和回补上限。",
            "未到回补价不追买；可能形成实际减仓，必须提前接受这个结果。",
        ]
    if state == "hold_no_add":
        steps = ["持有观察，不补仓，不做T。", "等待技术面、数据质量或风险信号改善后再重新评估。"]
        if action_backtest and (action_backtest.get("weak_rule_count") or 0) > 0:
            steps.append("动作矩阵存在弱规则，先复核规则表现，不新增交易。")
        return steps
    if state in {"data_stale", "market_wait"}:
        return ["等待行情刷新到可用状态；刷新前不做盘中交易动作。"]
    return ["本轮不买不卖，继续监控关键价格、数据质量和技术指标变化。"]


def confidence_for(state: str, evidence: list[str], blockers: list[str]) -> str:
    if state in {"exit_risk_review", "data_stale", "market_wait", "data_insufficient"}:
        return "high"
    if blockers:
        return "medium"
    if len(evidence) >= 4:
        return "medium"
    return "low"


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
) -> dict[str, Any]:
    technical_assessment = build_technical_assessment(technical_indicators)
    positive_timing = build_positive_timing(intraday, t_check, minute_bars, technical_assessment)
    state, reason = choose_state(intraday, portfolio, t_check, reverse_backtest, data_quality, technical_assessment)
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
    )
    blockers = build_blockers(intraday, t_check, reverse_backtest, data_quality, technical_assessment)
    levels = price_levels(portfolio, t_check, intraday, reverse_forecast)
    capital_plan = build_capital_plan(
        state,
        levels,
        intraday.get("position", {}),
        total_assets=total_assets,
        technical_assessment=technical_assessment,
        positive_timing=positive_timing,
    )
    action_code = {
        "market_wait": "wait_for_market_session",
        "data_stale": "pause_intraday_decision",
        "exit_risk_review": "create_exit_or_risk_review",
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
            "next_step": build_next_step(state, action_backtest, levels),
            "action_steps": build_action_steps(
                state,
                levels,
                blockers,
                action_backtest,
                code=str(intraday.get("code") or ""),
                name=str(intraday.get("name") or ""),
                position=intraday.get("position", {}),
                capital_plan=capital_plan,
            ),
        },
        "price_levels": levels,
        "capital_plan": capital_plan,
        "positive_timing": positive_timing,
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
) -> dict[str, Any]:
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
        )
        for item in intraday_snapshot.get("items", [])
    ]
    state_counts: dict[str, int] = {}
    for card in cards:
        state_counts[card["state"]] = state_counts.get(card["state"], 0) + 1
    return {
        "generated_at": (generated_at or datetime.now().astimezone()).isoformat(timespec="seconds"),
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
        },
        "card_count": len(cards),
        "state_counts": dict(sorted(state_counts.items())),
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
        "| 代码 | 名称 | 状态 | 当前价 | 止损 | 阻断价 | 技术分 | 动作 | 置信度 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
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
        positive_timing = card.get("positive_timing") or {}
        if positive_timing.get("available"):
            lines.append(
                f"- 正T分时评分：{positive_timing.get('score')} / {positive_timing.get('threshold')}，状态 {positive_timing.get('status')}"
            )
        note = technical_decision_note(card.get("technical_assessment") or {})
        if note:
            lines.append(f"- 技术判断：{note}")
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
