#!/usr/bin/env python3
"""Calculate basic trend strength factors from daily bars."""

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
    from tools.import_daily_bars import parse_number
    from tools.import_stock_universe import parse_bool
except ModuleNotFoundError:
    from import_daily_bars import parse_number
    from import_stock_universe import parse_bool


BASE_FIELDS = [
    "code",
    "trade_date",
    "close",
    "bars_count",
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
]


def parse_windows(value: str) -> list[int]:
    windows: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        window = int(part)
        if window <= 0:
            raise ValueError("windows must be positive integers")
        windows.append(window)
    if not windows:
        raise ValueError("at least one window is required")
    return sorted(set(windows))


def factor_fields(windows: list[int]) -> list[str]:
    fields = list(BASE_FIELDS)
    for window in windows:
        fields.extend(
            [
                f"return_{window}d",
                f"ma_{window}",
                f"above_ma_{window}",
                f"turnover_avg_{window}",
            ]
        )
    return fields


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 6)
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def read_universe_codes(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return {(row.get("code") or "").strip() for row in reader if (row.get("code") or "").strip()}


def read_daily_bars(path: Path, universe_codes: set[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("code") or "").strip()
            if not code:
                raise ValueError(f"row {row_number} field code: value is required")
            if universe_codes is not None and code not in universe_codes:
                continue
            trade_date = (row.get("trade_date") or "").strip()
            datetime.strptime(trade_date, "%Y-%m-%d")
            close = parse_number(row.get("close") or "", "close", row_number)
            turnover = parse_number(row.get("turnover") or "", "turnover", row_number)
            grouped[code].append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "close": close,
                    "turnover": turnover,
                    "is_suspended": parse_bool(row.get("is_suspended") or "false", "is_suspended", row_number),
                    "is_limit_up": parse_bool(row.get("is_limit_up") or "false", "is_limit_up", row_number),
                    "is_limit_down": parse_bool(row.get("is_limit_down") or "false", "is_limit_down", row_number),
                }
            )

    for rows in grouped.values():
        rows.sort(key=lambda item: item["trade_date"])
    return dict(sorted(grouped.items()))


def calculate_for_code(rows: list[dict[str, Any]], windows: list[int]) -> dict[str, Any]:
    latest = rows[-1]
    result: dict[str, Any] = {
        "code": latest["code"],
        "trade_date": latest["trade_date"],
        "close": format_number(latest["close"]),
        "bars_count": len(rows),
        "is_suspended": latest["is_suspended"],
        "is_limit_up": latest["is_limit_up"],
        "is_limit_down": latest["is_limit_down"],
    }

    for window in windows:
        window_rows = rows[-window:]
        if len(window_rows) < window:
            result[f"return_{window}d"] = ""
            result[f"ma_{window}"] = ""
            result[f"above_ma_{window}"] = ""
            result[f"turnover_avg_{window}"] = ""
            continue

        first_close = window_rows[0]["close"]
        latest_close = latest["close"]
        close_values = [row["close"] for row in window_rows]
        turnover_values = [row["turnover"] for row in window_rows]
        moving_average = sum(close_values) / window
        turnover_average = sum(turnover_values) / window
        result[f"return_{window}d"] = format_number((latest_close / first_close - 1) * 100 if first_close else None)
        result[f"ma_{window}"] = format_number(moving_average)
        result[f"above_ma_{window}"] = latest_close >= moving_average
        result[f"turnover_avg_{window}"] = format_number(turnover_average)

    return result


def calculate_trend_factors(grouped_bars: dict[str, list[dict[str, Any]]], windows: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in sorted(grouped_bars):
        if grouped_bars[code]:
            rows.append(calculate_for_code(grouped_bars[code], windows))
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
    universe_path: Path | None,
    output_path: Path,
    rows: list[dict[str, Any]],
    windows: list[int],
) -> dict[str, Any]:
    trade_dates = sorted({row["trade_date"] for row in rows})
    return {
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
        "daily_bars": str(daily_bars_path),
        "universe": str(universe_path) if universe_path else None,
        "output": str(output_path),
        "row_count": len(rows),
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
    universe_path: Path | None,
    output_path: Path,
    metadata_path: Path,
    windows: list[int],
) -> dict[str, Any]:
    universe_codes = read_universe_codes(universe_path)
    grouped_bars = read_daily_bars(daily_bars_path, universe_codes)
    rows = calculate_trend_factors(grouped_bars, windows)
    write_factors(output_path, rows, windows)
    metadata = build_metadata(daily_bars_path, universe_path, output_path, rows, windows)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"factor rows: {metadata['row_count']}")
    print(f"windows: {', '.join(str(item) for item in metadata['windows'])}")
    print(f"date range: {metadata['start_date'] or '-'} -> {metadata['end_date'] or '-'}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate basic trend strength factors.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Input normalized daily bars CSV.")
    parser.add_argument("--universe", help="Optional tradable universe CSV.")
    parser.add_argument("--output", default="data/processed/trend_factors.csv", help="Output trend factors CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/trend_factors.json", help="Calculation metadata JSON.")
    parser.add_argument("--windows", default="5,20", help="Comma-separated bar windows, for example 5,20,60.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        windows = parse_windows(args.windows)
        metadata = run_calculation(
            Path(args.daily_bars),
            Path(args.universe) if args.universe else None,
            Path(args.output),
            Path(args.metadata_output),
            windows,
        )
    except Exception as exc:
        print(f"trend factor calculation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

