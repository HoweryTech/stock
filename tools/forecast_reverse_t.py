#!/usr/bin/env python3
"""Forecast the next reverse-T opportunity from analogous 5-minute patterns."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from tools.backtest_reverse_t import fetch_minute_bars, fetch_sina_minute_bars
    from tools.check_portfolio_positions import expand_position_paths
    from tools.monitor_intraday_positions import fee_viable_trade
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from backtest_reverse_t import fetch_minute_bars, fetch_sina_minute_bars
    from check_portfolio_positions import expand_position_paths
    from monitor_intraday_positions import fee_viable_trade
    from risk_check import as_float, load_yaml, value_at


MAX_PREDICTED_BUYBACK_GAP_PCT = 5.0
BASE_FORECAST_PROBABILITY_THRESHOLD = 60.0


def average(values: list[float]) -> float:
    return sum(values) / len(values)


def ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def quantile(values: list[float], level: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires values")
    index = (len(ordered) - 1) * level
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def probability_thresholds(sample_count: int, neighbor_count: int) -> dict[str, Any]:
    adjustment = 0.0
    reasons: list[str] = []
    if sample_count < 180:
        adjustment += 6.0
        reasons.append("样本量不足180，预测门槛上调。")
    elif sample_count < 260:
        adjustment += 3.0
        reasons.append("样本量低于260，预测门槛小幅上调。")
    elif sample_count >= 500:
        adjustment -= 3.0
        reasons.append("样本量超过500，允许小幅降低预警门槛。")
    if neighbor_count < 30:
        adjustment += 3.0
        reasons.append("相似样本少于30，预测门槛上调。")
    elif neighbor_count >= 80:
        adjustment -= 1.0
        reasons.append("相似样本不少于80，门槛小幅放宽。")
    threshold = round(max(55.0, min(68.0, BASE_FORECAST_PROBABILITY_THRESHOLD + adjustment)), 2)
    if not reasons:
        reasons.append("样本量和相似样本数量处于常规区间，使用基础门槛。")
    return {
        "minimum_reach_probability_pct": threshold,
        "minimum_roundtrip_probability_pct": threshold,
        "base_probability_threshold_pct": BASE_FORECAST_PROBABILITY_THRESHOLD,
        "adjustment_pct": round(adjustment, 2),
        "reasons": reasons,
    }


def feature_vector(bars: list[dict[str, Any]], index: int) -> list[float] | None:
    if index < 26:
        return None
    window = bars[index - 26:index + 1]
    closes = [bar["close"] for bar in window]
    current = bars[index]
    same_day = [bar for bar in bars[:index + 1] if bar["timestamp"][:10] == current["timestamp"][:10]]
    day_high = max(bar["high"] for bar in same_day)
    day_low = min(bar["low"] for bar in same_day)
    range_position = (current["close"] - day_low) / (day_high - day_low) if day_high > day_low else 0.5
    returns = [(closes[pos] / closes[pos - 1] - 1) * 100 for pos in range(1, len(closes))]
    gains = [max(value, 0) for value in returns[-14:]]
    losses = [max(-value, 0) for value in returns[-14:]]
    avg_loss = average(losses)
    rsi = 100 if avg_loss == 0 else 100 - 100 / (1 + average(gains) / avg_loss)
    middle = average(closes[-20:])
    variance = average([(value - middle) ** 2 for value in closes[-20:]])
    boll_z = (current["close"] - middle) / math.sqrt(variance) if variance > 0 else 0
    macd_pct = (ema(closes, 12) - ema(closes, 26)) / current["close"] * 100
    true_ranges = [bar["high"] - bar["low"] for bar in window[-14:]]
    atr_pct = average(true_ranges) / current["close"] * 100
    volumes = [bar["volume"] for bar in window]
    volume_ratio = current["volume"] / average(volumes[-20:]) if average(volumes[-20:]) else 1
    hour, minute = map(int, current["timestamp"][11:16].split(":"))
    session_minute = (hour - 9) * 60 + minute - 30
    return [
        returns[-1] / 2, sum(returns[-3:]) / 4, range_position,
        macd_pct / 2, boll_z / 3, rsi / 100,
        min(volume_ratio, 5) / 5, atr_pct / 3, session_minute / 240,
    ]


def build_samples(bars: list[dict[str, Any]], horizon_bars: int = 6) -> list[dict[str, Any]]:
    samples = []
    for index in range(26, len(bars) - horizon_bars):
        future = bars[index + 1:index + 1 + horizon_bars]
        if not future or future[-1]["timestamp"][:10] != bars[index]["timestamp"][:10]:
            continue
        features = feature_vector(bars, index)
        if features is None:
            continue
        peak_index = max(range(len(future)), key=lambda pos: future[pos]["high"])
        future_high = future[peak_index]["high"]
        future_low = min(bar["low"] for bar in future)
        later_low = min(bar["low"] for bar in future[peak_index:])
        close = bars[index]["close"]
        samples.append(
            {
                "timestamp": bars[index]["timestamp"], "features": features,
                "max_up_pct": (future_high / close - 1) * 100,
                "max_down_pct": (close / future_low - 1) * 100 if future_low else 0,
                "pullback_pct": (future_high / later_low - 1) * 100 if later_low else 0,
            }
        )
    return samples


def distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def forecast(code: str, name: str, bars: list[dict[str, Any]], shares: int, costs: dict[str, float], neighbors: int = 60) -> dict[str, Any]:
    samples = build_samples(bars)
    current_features = feature_vector(bars, len(bars) - 1) if bars else None
    max_shares = int(shares * 0.5 // 100 * 100)
    if max_shares < 100:
        return {"code": code, "name": name, "status": "position_not_supported", "status_label": "持仓不足以保留底仓做反T", "sample_count": len(samples)}
    if current_features is None or len(samples) < 120:
        return {"code": code, "name": name, "status": "insufficient", "status_label": "预测样本不足", "sample_count": len(samples)}
    nearest = sorted(samples, key=lambda sample: distance(current_features, sample["features"]))[:neighbors]
    up_moves = [sample["max_up_pct"] for sample in nearest]
    current_price = bars[-1]["close"]
    low_up_pct = max(0.0, quantile(up_moves, 0.5))
    high_up_pct = max(low_up_pct, quantile(up_moves, 0.75))
    zone_low = round(current_price * (1 + low_up_pct / 100), 2)
    zone_high = round(current_price * (1 + high_up_pct / 100), 2)
    viable = fee_viable_trade(zone_low, max_shares, costs, min_gap_pct=1.2, max_gap_pct=MAX_PREDICTED_BUYBACK_GAP_PCT)
    if not viable:
        return {
            "code": code, "name": name, "status": "fee_blocked", "status_label": "预测价差不足以覆盖费用",
            "as_of": bars[-1]["timestamp"], "horizon_minutes": 30,
            "current_price": current_price, "predicted_sell_zone": [zone_low, zone_high],
            "predicted_buyback_max_price": None,
            "max_buyback_gap_pct": MAX_PREDICTED_BUYBACK_GAP_PCT,
            "sample_count": len(samples), "neighbor_count": len(nearest),
            "indicators": {"model": "nearest_5minute_patterns", "features": ["returns", "range_position", "MACD", "BOLL", "RSI", "volume_ratio", "ATR", "time_of_day"]},
            "execution_allowed": False,
            "note": "预测区间仅用于观察；当前费用模型下没有可接受的回补上限。",
        }
    required_up = (zone_low / current_price - 1) * 100
    required_pullback = viable["required_gap_pct"]
    reached = [sample for sample in nearest if sample["max_up_pct"] >= required_up]
    if required_up <= 0:
        roundtrips = [sample for sample in nearest if sample.get("max_down_pct", 0) >= required_pullback]
    else:
        roundtrips = [sample for sample in reached if sample["pullback_pct"] >= required_pullback]
    reach_probability = len(reached) / len(nearest) * 100
    roundtrip_probability = len(roundtrips) / len(reached) * 100 if reached else 0
    joint_probability = len(roundtrips) / len(nearest) * 100
    thresholds = probability_thresholds(len(samples), len(nearest))
    reach_threshold = thresholds["minimum_reach_probability_pct"]
    roundtrip_threshold = thresholds["minimum_roundtrip_probability_pct"]
    if reach_probability >= reach_threshold and roundtrip_probability >= roundtrip_threshold:
        status, label = "early_warning", "指标预测反T区间预警"
    elif reach_probability >= max(50.0, reach_threshold - 10.0):
        status, label = "watch", "指标预测区间仍需观察"
    else:
        status, label = "low_probability", "未来30分钟到达概率偏低"
    return {
        "code": code, "name": name, "status": status, "status_label": label,
        "as_of": bars[-1]["timestamp"], "horizon_minutes": 30,
        "current_price": current_price, "predicted_sell_zone": [zone_low, zone_high],
        "reach_probability_pct": round(reach_probability, 2),
        "roundtrip_probability_pct": round(roundtrip_probability, 2),
        "joint_roundtrip_probability_pct": round(joint_probability, 2),
        "predicted_buyback_max_price": viable["buyback_max_price"],
        "trade_shares": viable["trade_shares"], "required_gap_pct": required_pullback,
        "estimated_net_profit_at_limit": viable["fees"]["net_profit"],
        "max_buyback_gap_pct": MAX_PREDICTED_BUYBACK_GAP_PCT,
        "sample_count": len(samples), "neighbor_count": len(nearest),
        "probability_policy": thresholds,
        "indicators": {"model": "nearest_5minute_patterns", "features": ["returns", "range_position", "MACD", "BOLL", "RSI", "volume_ratio", "ATR", "time_of_day"]},
        "execution_allowed": False,
        "note": "概率预测仅用于提前预警；必须同时通过历史回测和实时确认才可进入人工候选。",
    }


def build_report(position_paths: list[Path], begin: str, end: str, costs: dict[str, float], cache_dir: Path = Path("data/processed/minute-bars")) -> dict[str, Any]:
    items, errors = [], []
    cache_dir.mkdir(parents=True, exist_ok=True)
    for path in position_paths:
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        position_name = str(value_at(position, "stock.name") or code)
        shares = int(as_float(value_at(position, "entry.shares"), 0) or 0)
        last_error = None
        cache_path = cache_dir / f"{code}.json"
        for attempt in range(3):
            try:
                try:
                    bars = fetch_sina_minute_bars(code)
                    name = position_name
                    bar_source = "sina_5minute"
                except Exception:
                    name, bars = fetch_minute_bars(code, begin, end)
                    bar_source = "eastmoney_5minute"
                cache_path.write_text(json.dumps({"name": name, "bars": bars}, ensure_ascii=False), encoding="utf-8")
                item = forecast(code, name, bars, shares, costs)
                item["bar_source"] = bar_source
                items.append(item)
                time.sleep(1.0)
                break
            except Exception as exc:
                last_error = exc
                time.sleep(1.0 * (attempt + 1))
        else:
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                item = forecast(code, cached["name"], cached["bars"], shares, costs)
                item["bar_source"] = "cache_after_fetch_failure"
                items.append(item)
            else:
                errors.append({"code": code, "message": str(last_error)})
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "eastmoney_5minute_kline", "items": items, "errors": errors,
        "policy": {
            "execution_allowed": False,
            "horizon_minutes": 30,
            "base_probability_threshold_pct": BASE_FORECAST_PROBABILITY_THRESHOLD,
            "threshold_mode": "dynamic_by_sample_count_and_neighbor_count",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forecast next reverse-T sell interval.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--begin", default=(date.today() - timedelta(days=180)).strftime("%Y%m%d"))
    parser.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--output", default="data/metadata/reverse-t-forecast.json")
    parser.add_argument("--cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--interval", type=float, default=0, help="Refresh interval in seconds; zero runs once.")
    parser.add_argument("--profile", default="config/investment-profile.yaml")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile_path = Path(args.profile)
    profile = load_yaml(profile_path) if profile_path.exists() else {}
    minimum_net_profit = as_float(value_at(profile, "t_trading.minimum_net_profit_cny"), 5.0) or 5.0
    costs = {"commission_rate": 0.0003, "minimum_commission": 5.0, "stamp_duty_rate": 0.0005, "transfer_fee_rate": 0.00001, "minimum_net_profit": minimum_net_profit, "verified": False}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = expand_position_paths(args.positions)
    while True:
        report = build_report(paths, args.begin, args.end, costs, Path(args.cache_dir))
        target = output if not report["errors"] else output.with_suffix(".failed.json")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        print(f"forecasted: {len(report['items'])}, errors: {len(report['errors'])}, output: {target}", flush=True)
        if args.interval <= 0:
            return 0 if not report["errors"] else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
