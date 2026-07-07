#!/usr/bin/env python3
"""Filter stock universe into a tradable universe using investment profile rules."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.import_stock_universe import STANDARD_FIELDS, parse_bool
    from tools.risk_check import as_float, load_yaml
except ModuleNotFoundError:
    from import_stock_universe import STANDARD_FIELDS, parse_bool
    from risk_check import as_float, load_yaml


@dataclass
class Exclusion:
    code: str
    name: str
    reason: str
    message: str


def parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d")


def listing_days(listing_date: str, as_of: datetime) -> int | None:
    listed_at = parse_date(listing_date)
    if listed_at is None:
        return None
    return (as_of.date() - listed_at.date()).days


def read_universe(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=2):
            normalized = dict(row)
            for field in ("is_st", "is_suspended", "has_delisting_risk", "abnormal_trading_status"):
                normalized[field] = parse_bool(row.get(field, ""), field, row_number)
            rows.append(normalized)
    return rows


def exclusion_for_row(row: dict[str, Any], filters: dict[str, Any], as_of: datetime) -> Exclusion | None:
    code = row.get("code", "")
    name = row.get("name", "")

    if filters.get("exclude_st", True) and row.get("is_st"):
        return Exclusion(code, name, "is_st", "ST 股票被排除。")
    if filters.get("exclude_delisting_risk", True) and row.get("has_delisting_risk"):
        return Exclusion(code, name, "delisting_risk", "存在退市风险。")
    if filters.get("exclude_suspended", True) and row.get("is_suspended"):
        return Exclusion(code, name, "suspended", "当前停牌。")
    if filters.get("exclude_abnormal_trading_status", True) and row.get("abnormal_trading_status"):
        return Exclusion(code, name, "abnormal_trading_status", "交易状态异常。")

    min_listing_days = as_float(filters.get("min_listing_days"))
    days = listing_days(row.get("listing_date", ""), as_of)
    if min_listing_days is not None and days is not None and days < min_listing_days:
        return Exclusion(code, name, "listing_days_too_short", f"上市天数 {days} 小于要求 {int(min_listing_days)}。")

    min_turnover = as_float(filters.get("min_average_daily_turnover_cny"))
    turnover = as_float(row.get("avg_daily_turnover_cny"))
    if min_turnover is not None and turnover is not None and turnover < min_turnover:
        return Exclusion(code, name, "turnover_too_low", f"平均成交额 {turnover:.0f} 低于要求 {min_turnover:.0f}。")

    return None


def filter_universe(profile: dict[str, Any], rows: list[dict[str, Any]], as_of: datetime) -> tuple[list[dict[str, Any]], list[Exclusion]]:
    filters = profile.get("universe_filters", {})
    eligible: list[dict[str, Any]] = []
    exclusions: list[Exclusion] = []

    for row in rows:
        exclusion = exclusion_for_row(row, filters, as_of)
        if exclusion is None:
            eligible.append(row)
        else:
            exclusions.append(exclusion)

    return eligible, exclusions


def write_universe(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def build_report(
    input_path: Path,
    output_path: Path,
    rows: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    exclusions: list[Exclusion],
    as_of: datetime,
) -> dict[str, Any]:
    excluded_by_reason: dict[str, int] = {}
    for exclusion in exclusions:
        excluded_by_reason[exclusion.reason] = excluded_by_reason.get(exclusion.reason, 0) + 1

    return {
        "filtered_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.strftime("%Y-%m-%d"),
        "input": str(input_path),
        "output": str(output_path),
        "input_count": len(rows),
        "eligible_count": len(eligible),
        "excluded_count": len(exclusions),
        "excluded_by_reason": excluded_by_reason,
        "exclusions": [exclusion.__dict__ for exclusion in exclusions],
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run_filter(profile_path: Path, input_path: Path, output_path: Path, report_path: Path, as_of: datetime) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    rows = read_universe(input_path)
    eligible, exclusions = filter_universe(profile, rows, as_of)
    write_universe(output_path, eligible)
    report = build_report(input_path, output_path, rows, eligible, exclusions, as_of)
    write_report(report_path, report)
    return report


def print_summary(report: dict[str, Any]) -> None:
    print(f"input rows: {report['input_count']}")
    print(f"eligible rows: {report['eligible_count']}")
    print(f"excluded rows: {report['excluded_count']}")
    print(f"as of: {report['as_of']}")
    print(f"output: {report['output']}")
    if report["excluded_by_reason"]:
        print("excluded by reason:")
        for reason, count in sorted(report["excluded_by_reason"].items()):
            print(f"- {reason}: {count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter stock universe into a tradable universe.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--input", default="data/processed/stock_universe.csv", help="Input normalized stock universe CSV.")
    parser.add_argument("--output", default="data/processed/tradable_universe.csv", help="Output tradable universe CSV.")
    parser.add_argument("--report-output", default="data/metadata/tradable_universe.filter.json", help="Filter report JSON.")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"), help="Reference date for listing days.")
    parser.add_argument("--json", action="store_true", help="Print report as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        as_of = datetime.strptime(args.as_of, "%Y-%m-%d")
        report = run_filter(Path(args.profile), Path(args.input), Path(args.output), Path(args.report_output), as_of)
    except Exception as exc:
        print(f"universe filter failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
