#!/usr/bin/env python3
"""Build a per-holding data quality snapshot for intraday decisions."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from risk_check import as_float, load_yaml, value_at


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def load_intraday_by_code(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not snapshot:
        return {}
    return {str(item.get("code")): item for item in snapshot.get("items", []) if item.get("code")}


def load_daily_stats(path: Path) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return stats
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            code = str(row.get("code") or "")
            trade_date = str(row.get("trade_date") or "")
            if not code:
                continue
            item = stats.setdefault(code, {"row_count": 0, "latest_trade_date": ""})
            item["row_count"] += 1
            if trade_date > item["latest_trade_date"]:
                item["latest_trade_date"] = trade_date
                item["latest_close"] = as_float(row.get("close"))
    return stats


def load_minute_stats(cache_dir: Path) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    if not cache_dir.exists():
        return stats
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        bars = data.get("bars") if isinstance(data, dict) else data
        if not isinstance(bars, list):
            continue
        code = path.stem
        latest = ""
        latest_close = None
        for bar in bars:
            timestamp = str(bar.get("timestamp") or "")
            if timestamp > latest:
                latest = timestamp
                latest_close = as_float(bar.get("close"))
        stats[code] = {"bar_count": len(bars), "latest_timestamp": latest, "latest_close": latest_close, "cache_path": str(path)}
    return stats


def age_days(as_of: datetime, date_text: str | None) -> int | None:
    parsed = parse_datetime(date_text)
    if not parsed:
        return None
    return (as_of.date() - parsed.date()).days


def age_hours(as_of: datetime, timestamp_text: str | None) -> float | None:
    parsed = parse_datetime(timestamp_text)
    if not parsed:
        return None
    if as_of.tzinfo is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=as_of.tzinfo)
    return (as_of - parsed).total_seconds() / 3600


def status_label(status: str) -> str:
    return {
        "usable": "可用于盘中判断",
        "stale": "数据过期",
        "missing": "数据缺失",
        "insufficient": "样本不足",
    }.get(status, status)


def trust_label(level: str) -> str:
    return {
        "high": "高可信",
        "medium": "中可信",
        "low": "低可信",
    }.get(level, level)


def classify_quote(item: dict[str, Any] | None, max_lag_seconds: float) -> dict[str, Any]:
    if not item:
        return {"status": "missing", "latest_price": None, "lag_seconds": None, "message": "缺少准实时行情快照。"}
    latest_price = as_float(value_at(item, "quote.latest_price"))
    if latest_price is None:
        return {"status": "missing", "latest_price": None, "lag_seconds": None, "message": "行情最新价缺失。"}
    lag = as_float(value_at(item, "quote.quote_lag_seconds"))
    if lag is None:
        return {"status": "missing", "latest_price": latest_price, "lag_seconds": None, "message": "行情延迟字段缺失。"}
    if lag > max_lag_seconds:
        return {"status": "stale", "latest_price": latest_price, "lag_seconds": round(lag, 3), "message": f"行情延迟 {lag:.1f} 秒，超过 {max_lag_seconds:.1f} 秒阈值。"}
    return {"status": "usable", "latest_price": latest_price, "lag_seconds": round(lag, 3), "message": "行情延迟在阈值内。"}


def classify_daily(stats: dict[str, Any] | None, as_of: datetime, min_bars: int, max_age_days: int) -> dict[str, Any]:
    if not stats:
        return {"status": "missing", "row_count": 0, "latest_trade_date": None, "latest_close": None, "age_days": None, "message": "缺少日线数据。"}
    row_count = int(stats.get("row_count") or 0)
    latest = stats.get("latest_trade_date")
    latest_close = as_float(stats.get("latest_close"))
    days = age_days(as_of, latest)
    if row_count < min_bars:
        return {"status": "insufficient", "row_count": row_count, "latest_trade_date": latest, "latest_close": latest_close, "age_days": days, "message": f"日线数量 {row_count} 少于 {min_bars}。"}
    if days is None:
        return {"status": "missing", "row_count": row_count, "latest_trade_date": latest, "latest_close": latest_close, "age_days": None, "message": "日线最新日期无法解析。"}
    if days > max_age_days:
        return {"status": "stale", "row_count": row_count, "latest_trade_date": latest, "latest_close": latest_close, "age_days": days, "message": f"日线距当前 {days} 天，超过 {max_age_days} 天阈值。"}
    return {"status": "usable", "row_count": row_count, "latest_trade_date": latest, "latest_close": latest_close, "age_days": days, "message": "日线样本和新鲜度可用。"}


def classify_minute(stats: dict[str, Any] | None, as_of: datetime, min_bars: int, max_age_hours: float) -> dict[str, Any]:
    if not stats:
        return {"status": "missing", "bar_count": 0, "latest_timestamp": None, "latest_close": None, "age_hours": None, "message": "缺少分钟线缓存。"}
    bar_count = int(stats.get("bar_count") or 0)
    latest = stats.get("latest_timestamp")
    latest_close = as_float(stats.get("latest_close"))
    hours = age_hours(as_of, latest)
    if bar_count < min_bars:
        return {"status": "insufficient", "bar_count": bar_count, "latest_timestamp": latest, "latest_close": latest_close, "age_hours": None if hours is None else round(hours, 3), "message": f"分钟线数量 {bar_count} 少于 {min_bars}。"}
    if hours is None:
        return {"status": "missing", "bar_count": bar_count, "latest_timestamp": latest, "latest_close": latest_close, "age_hours": None, "message": "分钟线最新时间无法解析。"}
    if hours > max_age_hours:
        return {"status": "stale", "bar_count": bar_count, "latest_timestamp": latest, "latest_close": latest_close, "age_hours": round(hours, 3), "message": f"分钟线距当前 {hours:.1f} 小时，超过 {max_age_hours:.1f} 小时阈值。"}
    return {"status": "usable", "bar_count": bar_count, "latest_timestamp": latest, "latest_close": latest_close, "age_hours": round(hours, 3), "message": "分钟线样本和新鲜度可用。"}


def overall_status(quote: dict[str, Any], daily: dict[str, Any], minute: dict[str, Any]) -> str:
    statuses = {quote["status"], daily["status"], minute["status"]}
    if "missing" in statuses:
        return "missing"
    if "insufficient" in statuses:
        return "insufficient"
    if "stale" in statuses:
        return "stale"
    return "usable"


def price_diff_pct(base: float | None, other: float | None) -> float | None:
    if base in (None, 0) or other is None:
        return None
    return (other / base - 1) * 100


def date_part(value: str | None) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def source_consistency(quote: dict[str, Any], daily: dict[str, Any], minute: dict[str, Any], max_diff_pct: float) -> dict[str, Any]:
    quote_price = as_float(quote.get("latest_price"))
    checks: list[dict[str, Any]] = []
    issues: list[str] = []

    minute_close = as_float(minute.get("latest_close"))
    minute_diff = price_diff_pct(minute_close, quote_price)
    if quote_price is None or minute_close is None:
        checks.append({"source": "minute", "status": "skipped", "message": "缺少行情现价或分钟线最新收盘价。"})
    else:
        minute_status = "pass" if abs(minute_diff or 0.0) <= max_diff_pct else "conflict"
        message = f"东方财富现价与分钟线最新收盘价差 {minute_diff:.2f}%。"
        checks.append(
            {
                "source": "minute",
                "status": minute_status,
                "quote_price": quote_price,
                "reference_price": minute_close,
                "reference_timestamp": minute.get("latest_timestamp"),
                "diff_pct": round(minute_diff or 0.0, 4),
                "message": message,
            }
        )
        if minute_status == "conflict":
            issues.append(message)

    daily_close = as_float(daily.get("latest_close"))
    daily_date = date_part(daily.get("latest_trade_date"))
    minute_date = date_part(minute.get("latest_timestamp"))
    daily_diff = price_diff_pct(daily_close, quote_price)
    if quote_price is None or daily_close is None:
        checks.append({"source": "daily", "status": "skipped", "message": "缺少行情现价或日线最新收盘价。"})
    elif minute_date and daily_date != minute_date:
        checks.append(
            {
                "source": "daily",
                "status": "reference_only",
                "quote_price": quote_price,
                "reference_price": daily_close,
                "reference_date": daily_date,
                "minute_date": minute_date,
                "diff_pct": None if daily_diff is None else round(daily_diff, 4),
                "message": f"日线日期 {daily_date} 与分钟线日期 {minute_date} 不一致，仅作参考。",
            }
        )
    else:
        daily_status = "pass" if abs(daily_diff or 0.0) <= max_diff_pct else "conflict"
        message = f"东方财富现价与日线最新收盘价差 {daily_diff:.2f}%。"
        checks.append(
            {
                "source": "daily",
                "status": daily_status,
                "quote_price": quote_price,
                "reference_price": daily_close,
                "reference_date": daily_date,
                "diff_pct": round(daily_diff or 0.0, 4),
                "message": message,
            }
        )
        if daily_status == "conflict":
            issues.append(message)
    status = "conflict" if issues else "pass" if any(check["status"] == "pass" for check in checks) else "skipped"
    return {"status": status, "max_diff_pct": max_diff_pct, "issues": issues, "checks": checks}


def data_trust(quote: dict[str, Any], daily: dict[str, Any], minute: dict[str, Any], consistency: dict[str, Any]) -> dict[str, Any]:
    sections = {"行情": quote, "日线": daily, "分钟线": minute}
    blocking = [f"{name}: {section['message']}" for name, section in sections.items() if section["status"] in {"missing", "insufficient"}]
    stale = [f"{name}: {section['message']}" for name, section in sections.items() if section["status"] == "stale"]
    consistency_issues = [f"一致性: {message}" for message in consistency.get("issues", [])]
    if blocking or quote["status"] == "stale" or consistency_issues:
        level = "low"
        reasons = blocking or consistency_issues or [f"行情: {quote['message']}"]
        if stale and not blocking and not consistency_issues:
            reasons = stale
    elif stale:
        level = "medium"
        reasons = stale
    else:
        level = "high"
        reasons = ["行情、日线和分钟线均满足当前阈值。"]
    return {
        "level": level,
        "label": trust_label(level),
        "intraday_decision_allowed": level == "high",
        "reasons": reasons,
    }


def build_item(
    position_path: Path,
    intraday_by_code: dict[str, dict[str, Any]],
    daily_stats: dict[str, dict[str, Any]],
    minute_stats: dict[str, dict[str, Any]],
    *,
    as_of: datetime,
    max_quote_lag_seconds: float,
    min_daily_bars: int,
    max_daily_age_days: int,
    min_minute_bars: int,
    max_minute_age_hours: float,
    max_consistency_diff_pct: float,
) -> dict[str, Any]:
    position = load_yaml(position_path)
    code = str(value_at(position, "stock.code") or "")
    name = value_at(position, "stock.name") or code
    quote = classify_quote(intraday_by_code.get(code), max_quote_lag_seconds)
    daily = classify_daily(daily_stats.get(code), as_of, min_daily_bars, max_daily_age_days)
    minute = classify_minute(minute_stats.get(code), as_of, min_minute_bars, max_minute_age_hours)
    status = overall_status(quote, daily, minute)
    consistency = source_consistency(quote, daily, minute, max_consistency_diff_pct)
    trust = data_trust(quote, daily, minute, consistency)
    blockers = [section["message"] for section in (quote, daily, minute) if section["status"] in {"missing", "insufficient"}]
    warnings = [section["message"] for section in (quote, daily, minute) if section["status"] == "stale"]
    return {
        "code": code,
        "name": name,
        "position_path": str(position_path),
        "overall_status": status,
        "status_label": status_label(status),
        "usable_for_intraday_decision": status == "usable",
        "data_trust": trust,
        "source_consistency": consistency,
        "quote": quote,
        "daily": daily,
        "minute": minute,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_report(
    position_paths: list[Path],
    intraday_snapshot: dict[str, Any] | None,
    daily_bars: Path,
    minute_cache_dir: Path,
    *,
    as_of: datetime | None = None,
    max_quote_lag_seconds: float = 60.0,
    min_daily_bars: int = 20,
    max_daily_age_days: int = 5,
    min_minute_bars: int = 120,
    max_minute_age_hours: float = 30.0,
    max_consistency_diff_pct: float = 1.0,
) -> dict[str, Any]:
    as_of = as_of or datetime.now().astimezone()
    intraday_by_code = load_intraday_by_code(intraday_snapshot)
    daily_stats = load_daily_stats(daily_bars)
    minute_stats = load_minute_stats(minute_cache_dir)
    items = [
        build_item(
            path,
            intraday_by_code,
            daily_stats,
            minute_stats,
            as_of=as_of,
            max_quote_lag_seconds=max_quote_lag_seconds,
            min_daily_bars=min_daily_bars,
            max_daily_age_days=max_daily_age_days,
            min_minute_bars=min_minute_bars,
            max_minute_age_hours=max_minute_age_hours,
            max_consistency_diff_pct=max_consistency_diff_pct,
        )
        for path in position_paths
    ]
    status_counts: dict[str, int] = {}
    trust_counts: dict[str, int] = {}
    for item in items:
        status_counts[item["overall_status"]] = status_counts.get(item["overall_status"], 0) + 1
        trust_level = item["data_trust"]["level"]
        trust_counts[trust_level] = trust_counts.get(trust_level, 0) + 1
    return {
        "generated_at": as_of.isoformat(timespec="seconds"),
        "thresholds": {
            "max_quote_lag_seconds": max_quote_lag_seconds,
            "min_daily_bars": min_daily_bars,
            "max_daily_age_days": max_daily_age_days,
            "min_minute_bars": min_minute_bars,
            "max_minute_age_hours": max_minute_age_hours,
            "max_consistency_diff_pct": max_consistency_diff_pct,
        },
        "position_count": len(items),
        "usable_count": status_counts.get("usable", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "trust_counts": dict(sorted(trust_counts.items())),
        "items": sorted(items, key=lambda item: (item["overall_status"], item["code"])),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 持仓数据质量快照",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        f"持仓数：{report['position_count']}，可用于盘中判断：{report['usable_count']}，状态分布：{report['status_counts']}，可信等级：{report.get('trust_counts', {})}",
        "",
        "| 代码 | 名称 | 总状态 | 可信等级 | 一致性 | 行情延迟 | 日线最新 | 分钟线最新 | 说明 |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for item in report["items"]:
        quote = item["quote"]
        daily = item["daily"]
        minute = item["minute"]
        notes = item["blockers"] or item["warnings"] or ["可用"]
        lag = quote.get("lag_seconds")
        lines.append(
            f"| {item['code']} | {item['name']} | {item['status_label']} | {item['data_trust']['label']} | {item['source_consistency']['status']} | "
            f"{'-' if lag is None else f'{lag:.1f}s'} | "
            f"{daily.get('latest_trade_date') or '-'} ({daily.get('status')}) | "
            f"{minute.get('latest_timestamp') or '-'} ({minute.get('status')}) | "
            f"{'；'.join(notes[:3])} |"
        )
    return "\n".join(lines) + "\n"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build data quality snapshot for intraday holding decisions.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--intraday-snapshot", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--minute-cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--max-quote-lag-seconds", type=float, default=60.0)
    parser.add_argument("--min-daily-bars", type=int, default=20)
    parser.add_argument("--max-daily-age-days", type=int, default=5)
    parser.add_argument("--min-minute-bars", type=int, default=120)
    parser.add_argument("--max-minute-age-hours", type=float, default=30.0)
    parser.add_argument("--max-consistency-diff-pct", type=float, default=1.0)
    parser.add_argument("--output", default="data/metadata/data-quality-snapshot.json")
    parser.add_argument("--markdown-output", default="reports/data-quality-snapshot.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        intraday_snapshot = None
        snapshot_path = Path(args.intraday_snapshot)
        if snapshot_path.exists():
            intraday_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        report = build_report(
            expand_position_paths(args.positions),
            intraday_snapshot,
            Path(args.daily_bars),
            Path(args.minute_cache_dir),
            max_quote_lag_seconds=args.max_quote_lag_seconds,
            min_daily_bars=args.min_daily_bars,
            max_daily_age_days=args.max_daily_age_days,
            min_minute_bars=args.min_minute_bars,
            max_minute_age_hours=args.max_minute_age_hours,
            max_consistency_diff_pct=args.max_consistency_diff_pct,
        )
        write_json(Path(args.output), report)
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(render_markdown(report), encoding="utf-8")
    except Exception as exc:
        print(f"build data quality snapshot failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"positions: {report['position_count']}, usable: {report['usable_count']}, states: {report['status_counts']}")
        print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
