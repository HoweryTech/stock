#!/usr/bin/env python3
"""Calculate daily, weekly and monthly technical indicators from OHLCV bars."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from risk_check import load_yaml, value_at


def as_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def read_position_codes(paths: list[str] | None) -> set[str] | None:
    if not paths:
        return None
    codes: set[str] = set()
    for path in expand_position_paths(paths):
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        if code:
            codes.add(code)
    return codes


def read_daily_bars(path: Path, codes: set[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row_number, row in enumerate(csv.DictReader(file), start=2):
            code = str(row.get("code") or "").strip()
            if not code or (codes is not None and code not in codes):
                continue
            trade_date = str(row.get("trade_date") or "").strip()
            datetime.strptime(trade_date, "%Y-%m-%d")
            bar = {
                "trade_date": trade_date,
                "code": code,
                "open": as_number(row.get("open")),
                "high": as_number(row.get("high")),
                "low": as_number(row.get("low")),
                "close": as_number(row.get("close")),
                "volume": as_number(row.get("volume")),
                "turnover": as_number(row.get("turnover")),
            }
            missing = [field for field in ("open", "high", "low", "close", "volume") if bar[field] is None]
            if missing:
                raise ValueError(f"row {row_number} missing numeric fields: {', '.join(missing)}")
            grouped[code].append(bar)
    for rows in grouped.values():
        rows.sort(key=lambda item: item["trade_date"])
    return dict(sorted(grouped.items()))


def aggregate_period(rows: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    if period == "daily":
        return list(rows)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        date = datetime.strptime(row["trade_date"], "%Y-%m-%d")
        key = f"{date.isocalendar().year}-W{date.isocalendar().week:02d}" if period == "weekly" else date.strftime("%Y-%m")
        buckets[key].append(row)
    aggregated = []
    for key in sorted(buckets):
        bucket = sorted(buckets[key], key=lambda item: item["trade_date"])
        aggregated.append(
            {
                "trade_date": bucket[-1]["trade_date"],
                "period_key": key,
                "code": bucket[-1]["code"],
                "open": bucket[0]["open"],
                "high": max(item["high"] for item in bucket),
                "low": min(item["low"] for item in bucket),
                "close": bucket[-1]["close"],
                "volume": sum(item["volume"] or 0.0 for item in bucket),
                "turnover": sum(item["turnover"] or 0.0 for item in bucket),
            }
        )
    return aggregated


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stddev(values: list[float]) -> float | None:
    mean = average(values)
    if mean is None:
        return None
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def ema_series(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    result: list[float | None] = []
    multiplier = 2 / (period + 1)
    ema = values[0]
    for index, value in enumerate(values):
        ema = value if index == 0 else value * multiplier + ema * (1 - multiplier)
        result.append(ema if index >= period - 1 else None)
    return result


def macd(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 35:
        return {"status": "insufficient", "dif": None, "dea": None, "histogram": None}
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    dif_values = [None if fast is None or slow is None else fast - slow for fast, slow in zip(ema12, ema26)]
    valid_dif = [value for value in dif_values if value is not None]
    dea_valid = ema_series(valid_dif, 9)
    dea: list[float | None] = [None] * (len(dif_values) - len(dea_valid)) + dea_valid
    latest_dif = dif_values[-1]
    latest_dea = dea[-1]
    histogram = None if latest_dif is None or latest_dea is None else 2 * (latest_dif - latest_dea)
    return {"status": "ok", "dif": rounded(latest_dif), "dea": rounded(latest_dea), "histogram": rounded(histogram)}


def bollinger(closes: list[float], period: int = 20) -> dict[str, Any]:
    if len(closes) < period:
        return {"status": "insufficient", "middle": None, "upper": None, "lower": None, "percent_b": None, "width_pct": None}
    window = closes[-period:]
    middle = average(window)
    deviation = stddev(window)
    assert middle is not None and deviation is not None
    upper = middle + 2 * deviation
    lower = middle - 2 * deviation
    close = closes[-1]
    percent_b = None if upper == lower else (close - lower) / (upper - lower)
    width_pct = None if middle == 0 else (upper - lower) / middle * 100
    return {
        "status": "ok",
        "middle": rounded(middle),
        "upper": rounded(upper),
        "lower": rounded(lower),
        "percent_b": rounded(percent_b),
        "width_pct": rounded(width_pct),
    }


def rsi(closes: list[float], period: int) -> float | None:
    if len(closes) <= period:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = average(gains[:period])
    avg_loss = average(losses[:period])
    if avg_gain is None or avg_loss is None:
        return None
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - 100 / (1 + relative_strength)


def rsi_bundle(closes: list[float]) -> dict[str, Any]:
    rsi6 = rsi(closes, 6)
    rsi14 = rsi(closes, 14)
    return {"status": "ok" if rsi14 is not None else "insufficient", "rsi6": rounded(rsi6), "rsi14": rounded(rsi14)}


def true_ranges(rows: list[dict[str, Any]]) -> list[float]:
    ranges = []
    previous_close = None
    for row in rows:
        high = float(row["high"])
        low = float(row["low"])
        if previous_close is None:
            ranges.append(high - low)
        else:
            ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(row["close"])
    return ranges


def atr(rows: list[dict[str, Any]], period: int = 14) -> dict[str, Any]:
    if len(rows) < period:
        return {"status": "insufficient", "atr": None, "atr_pct": None}
    value = average(true_ranges(rows)[-period:])
    close = float(rows[-1]["close"])
    return {"status": "ok", "atr": rounded(value), "atr_pct": rounded(None if close == 0 or value is None else value / close * 100)}


def kdj(rows: list[dict[str, Any]], period: int = 9) -> dict[str, Any]:
    if len(rows) < period:
        return {"status": "insufficient", "k": None, "d": None, "j": None}
    k_value = 50.0
    d_value = 50.0
    for index in range(period - 1, len(rows)):
        window = rows[index - period + 1:index + 1]
        high = max(float(row["high"]) for row in window)
        low = min(float(row["low"]) for row in window)
        close = float(rows[index]["close"])
        rsv = 50.0 if high == low else (close - low) / (high - low) * 100
        k_value = k_value * 2 / 3 + rsv / 3
        d_value = d_value * 2 / 3 + k_value / 3
    return {"status": "ok", "k": rounded(k_value), "d": rounded(d_value), "j": rounded(3 * k_value - 2 * d_value)}


def volume_metrics(rows: list[dict[str, Any]], short_window: int = 5, long_window: int = 20) -> dict[str, Any]:
    latest = float(rows[-1]["volume"]) if rows else None
    avg_short = average([float(row["volume"]) for row in rows[-short_window:]]) if len(rows) >= short_window else None
    avg_long = average([float(row["volume"]) for row in rows[-long_window:]]) if len(rows) >= long_window else None
    return {
        "status": "ok" if avg_long is not None else "insufficient",
        "latest_volume": rounded(latest, 2),
        "avg_volume_5": rounded(avg_short, 2),
        "avg_volume_20": rounded(avg_long, 2),
        "volume_ratio_20": rounded(None if latest is None or avg_long in (None, 0) else latest / avg_long),
    }


def calculate_period_indicators(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(row["close"]) for row in rows]
    return {
        "bar_count": len(rows),
        "latest_trade_date": rows[-1]["trade_date"] if rows else None,
        "close": rounded(closes[-1] if closes else None),
        "macd": macd(closes),
        "boll": bollinger(closes),
        "rsi": rsi_bundle(closes),
        "kdj": kdj(rows),
        "atr": atr(rows),
        "volume": volume_metrics(rows),
    }


def calculate_indicators(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    items = []
    for code, rows in grouped.items():
        periods = {
            "daily": calculate_period_indicators(aggregate_period(rows, "daily")),
            "weekly": calculate_period_indicators(aggregate_period(rows, "weekly")),
            "monthly": calculate_period_indicators(aggregate_period(rows, "monthly")),
        }
        items.append({"code": code, "periods": periods})
    return items


def build_report(daily_bars: Path, position_paths: list[str] | None = None) -> dict[str, Any]:
    codes = read_position_codes(position_paths)
    grouped = read_daily_bars(daily_bars, codes)
    items = calculate_indicators(grouped)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"daily_bars": str(daily_bars), "positions": position_paths or [], "code_count": len(grouped)},
        "indicator_policy": {
            "computed_from": "local_ohlcv_bars",
            "periods": ["daily", "weekly", "monthly"],
            "indicators": ["MACD(12,26,9)", "BOLL(20,2)", "RSI(6,14)", "KDJ(9,3,3)", "ATR(14)", "volume_ratio_20"],
        },
        "items": items,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 多周期技术指标",
        "",
        f"生成时间：{report['generated_at']}",
        f"计算口径：{report['indicator_policy']['computed_from']}，指标：{', '.join(report['indicator_policy']['indicators'])}",
        "",
        "| 代码 | 周期 | 日期 | 收盘 | MACD柱 | BOLL%b | RSI14 | KDJ-J | ATR% | 量比20 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["items"]:
        for period in ("daily", "weekly", "monthly"):
            data = item["periods"][period]

            def value(path: str) -> Any:
                current: Any = data
                for part in path.split("."):
                    current = current.get(part) if isinstance(current, dict) else None
                return current

            lines.append(
                f"| {item['code']} | {period} | {data.get('latest_trade_date') or '-'} | {data.get('close') or '-'} | "
                f"{value('macd.histogram') if value('macd.histogram') is not None else '-'} | "
                f"{value('boll.percent_b') if value('boll.percent_b') is not None else '-'} | "
                f"{value('rsi.rsi14') if value('rsi.rsi14') is not None else '-'} | "
                f"{value('kdj.j') if value('kdj.j') is not None else '-'} | "
                f"{value('atr.atr_pct') if value('atr.atr_pct') is not None else '-'} | "
                f"{value('volume.volume_ratio_20') if value('volume.volume_ratio_20') is not None else '-'} |"
            )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate daily, weekly and monthly technical indicators from OHLCV bars.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--positions", nargs="+", help="Optional position files; when omitted all codes in daily bars are calculated.")
    parser.add_argument("--output", default="data/metadata/technical-indicators.json")
    parser.add_argument("--markdown-output", default="reports/technical-indicators.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(Path(args.daily_bars), args.positions)
    except Exception as exc:
        print(f"technical indicator calculation failed: {exc}", file=sys.stderr)
        return 2
    write_json(Path(args.output), report)
    Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.markdown_output).write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"technical indicators: {len(report['items'])} codes")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
