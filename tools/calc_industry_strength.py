#!/usr/bin/env python3
"""Calculate industry strength factors from daily bars and stock universe."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.calc_trend_factors import parse_windows
    from tools.import_daily_bars import parse_number
    from tools.import_stock_universe import parse_bool
except ModuleNotFoundError:
    from calc_trend_factors import parse_windows
    from import_daily_bars import parse_number
    from import_stock_universe import parse_bool


BASE_FIELDS = [
    "code",
    "name",
    "industry",
    "trade_date",
    "industry_member_count",
    "industry_strength_score",
    "industry_strength_evidence",
]


def factor_fields(windows: list[int]) -> list[str]:
    fields = list(BASE_FIELDS)
    for window in windows:
        fields.extend(
            [
                f"industry_return_{window}d",
                f"industry_turnover_avg_{window}",
                f"industry_up_ratio_{window}",
                f"relative_return_vs_industry_{window}d",
            ]
        )
    return fields


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 6)
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def read_universe(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            (row.get("code") or "").strip(): row
            for row in csv.DictReader(file)
            if (row.get("code") or "").strip() and (row.get("industry") or "").strip()
        }


def read_daily_bars(path: Path, universe: dict[str, dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("code") or "").strip()
            if code not in universe:
                continue
            trade_date = (row.get("trade_date") or "").strip()
            datetime.strptime(trade_date, "%Y-%m-%d")
            grouped[code].append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "close": parse_number(row.get("close") or "", "close", row_number),
                    "turnover": parse_number(row.get("turnover") or "", "turnover", row_number),
                    "is_suspended": parse_bool(row.get("is_suspended") or "false", "is_suspended", row_number),
                }
            )

    for rows in grouped.values():
        rows.sort(key=lambda item: item["trade_date"])
    return dict(sorted(grouped.items()))


def window_stats(rows: list[dict[str, Any]], window: int) -> dict[str, float] | None:
    window_rows = rows[-window:]
    if len(window_rows) < window:
        return None
    first_close = window_rows[0]["close"]
    latest_close = window_rows[-1]["close"]
    if not first_close:
        return None
    turnover_avg = sum(row["turnover"] for row in window_rows) / window
    return {
        "return_pct": (latest_close / first_close - 1) * 100,
        "turnover_avg": turnover_avg,
    }


def build_industry_groups(universe: dict[str, dict[str, str]], grouped_bars: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for code in grouped_bars:
        industry = universe.get(code, {}).get("industry", "")
        if industry:
            groups[industry].append(code)
    return {industry: sorted(codes) for industry, codes in sorted(groups.items())}


def industry_window_stats(
    industry_codes: list[str],
    grouped_bars: dict[str, list[dict[str, Any]]],
    window: int,
) -> dict[str, float] | None:
    member_stats = [window_stats(grouped_bars[code], window) for code in industry_codes]
    valid_stats = [item for item in member_stats if item is not None]
    if not valid_stats:
        return None
    returns = [item["return_pct"] for item in valid_stats]
    return {
        "member_count": len(valid_stats),
        "return_pct": sum(returns) / len(returns),
        "turnover_avg": sum(item["turnover_avg"] for item in valid_stats),
        "up_ratio": sum(1 for value in returns if value > 0) / len(returns) * 100,
    }


def score_industry_candidate(industry_stats: dict[str, float], relative_return: float | None) -> float:
    score = industry_stats["return_pct"]
    score += industry_stats["up_ratio"] * 0.1
    score += min(industry_stats["turnover_avg"] / 10_000_000_000 * 10.0, 10.0)
    if relative_return is not None and relative_return > 0:
        score += relative_return * 0.5
    return round(score, 6)


def evidence_text(window: int, industry_stats: dict[str, float], relative_return: float | None) -> str:
    parts = [
        f"行业近 {window} 日收益率 {industry_stats['return_pct']:.2f}%",
        f"行业上涨占比 {industry_stats['up_ratio']:.2f}%",
        f"行业窗口成交额 {industry_stats['turnover_avg']:.0f}",
    ]
    if relative_return is not None:
        parts.append(f"个股相对行业 {relative_return:.2f}%")
    return "；".join(parts)


def calculate_industry_strength(
    universe: dict[str, dict[str, str]],
    grouped_bars: dict[str, list[dict[str, Any]]],
    windows: list[int],
) -> list[dict[str, Any]]:
    industry_groups = build_industry_groups(universe, grouped_bars)
    industry_stats_by_key: dict[tuple[str, int], dict[str, float]] = {}
    for industry, codes in industry_groups.items():
        for window in windows:
            stats = industry_window_stats(codes, grouped_bars, window)
            if stats is not None:
                industry_stats_by_key[(industry, window)] = stats

    primary_window = windows[0]
    rows: list[dict[str, Any]] = []
    for code, bars in grouped_bars.items():
        latest = bars[-1]
        stock = universe.get(code, {})
        industry = stock.get("industry", "")
        row: dict[str, Any] = {
            "code": code,
            "name": stock.get("name", ""),
            "industry": industry,
            "trade_date": latest["trade_date"],
            "industry_member_count": len(industry_groups.get(industry, [])),
            "industry_strength_score": "",
            "industry_strength_evidence": "",
        }

        primary_score: float | None = None
        primary_evidence = ""
        for window in windows:
            stock_stats = window_stats(bars, window)
            industry_stats = industry_stats_by_key.get((industry, window))
            relative_return = None
            if stock_stats is not None and industry_stats is not None:
                relative_return = stock_stats["return_pct"] - industry_stats["return_pct"]
            row[f"industry_return_{window}d"] = format_number(industry_stats["return_pct"] if industry_stats else None)
            row[f"industry_turnover_avg_{window}"] = format_number(industry_stats["turnover_avg"] if industry_stats else None)
            row[f"industry_up_ratio_{window}"] = format_number(industry_stats["up_ratio"] if industry_stats else None)
            row[f"relative_return_vs_industry_{window}d"] = format_number(relative_return)
            if window == primary_window and industry_stats is not None:
                primary_score = score_industry_candidate(industry_stats, relative_return)
                primary_evidence = evidence_text(window, industry_stats, relative_return)

        if primary_score is not None:
            row["industry_strength_score"] = format_number(primary_score)
            row["industry_strength_evidence"] = primary_evidence
        rows.append(row)
    return rows


def write_factors(path: Path, rows: list[dict[str, Any]], windows: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = factor_fields(windows)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def build_metadata(
    daily_bars_path: Path,
    universe_path: Path,
    output_path: Path,
    rows: list[dict[str, Any]],
    windows: list[int],
) -> dict[str, Any]:
    trade_dates = sorted({row["trade_date"] for row in rows if row.get("trade_date")})
    industries = sorted({row["industry"] for row in rows if row.get("industry")})
    return {
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
        "daily_bars": str(daily_bars_path),
        "universe": str(universe_path),
        "output": str(output_path),
        "row_count": len(rows),
        "industry_count": len(industries),
        "industries": industries,
        "windows": windows,
        "start_date": trade_dates[0] if trade_dates else None,
        "end_date": trade_dates[-1] if trade_dates else None,
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run_calculation(
    daily_bars_path: Path,
    universe_path: Path,
    output_path: Path,
    metadata_path: Path,
    windows: list[int],
) -> dict[str, Any]:
    universe = read_universe(universe_path)
    grouped_bars = read_daily_bars(daily_bars_path, universe)
    rows = calculate_industry_strength(universe, grouped_bars, windows)
    write_factors(output_path, rows, windows)
    metadata = build_metadata(daily_bars_path, universe_path, output_path, rows, windows)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"factor rows: {metadata['row_count']}")
    print(f"industries: {metadata['industry_count']}")
    print(f"windows: {', '.join(str(item) for item in metadata['windows'])}")
    print(f"date range: {metadata['start_date'] or '-'} -> {metadata['end_date'] or '-'}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate industry strength factors.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Input normalized daily bars CSV.")
    parser.add_argument("--universe", default="data/processed/tradable_universe.csv", help="Input stock universe or tradable universe CSV.")
    parser.add_argument("--output", default="data/processed/industry_strength_factors.csv", help="Output industry strength factor CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/industry_strength_factors.json", help="Calculation metadata JSON.")
    parser.add_argument("--windows", default="5,20", help="Comma-separated bar windows, for example 5,20,60.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_calculation(
            Path(args.daily_bars),
            Path(args.universe),
            Path(args.output),
            Path(args.metadata_output),
            parse_windows(args.windows),
        )
    except Exception as exc:
        print(f"industry strength calculation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
