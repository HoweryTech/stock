#!/usr/bin/env python3
"""Import and normalize A-share daily bar CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.import_stock_universe import parse_bool
except ModuleNotFoundError:
    from import_stock_universe import parse_bool


STANDARD_FIELDS = [
    "trade_date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "turnover",
    "turnover_rate",
    "is_limit_up",
    "is_limit_down",
    "is_suspended",
    "adjust_type",
    "data_source",
    "updated_at",
]

REQUIRED_FIELDS = [
    "trade_date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "is_limit_up",
    "is_limit_down",
    "is_suspended",
]

BOOLEAN_FIELDS = {"is_limit_up", "is_limit_down", "is_suspended"}
VALID_ADJUST_TYPES = {"", "none", "qfq", "hfq"}
CODE_PATTERN = re.compile(r"^\d{6}$")


@dataclass
class ImportIssue:
    row: int
    field: str
    message: str


def normalize_date(value: str, field: str, row_number: int) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"row {row_number} field {field}: value is required")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"row {row_number} field {field}: expected YYYY-MM-DD, got {value!r}") from exc
    return value


def normalize_optional_date(value: str, field: str, row_number: int) -> str:
    value = value.strip()
    if not value:
        return ""
    return normalize_date(value, field, row_number)


def parse_number(value: str, field: str, row_number: int, *, required: bool = True, min_value: float | None = 0.0) -> float | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise ValueError(f"row {row_number} field {field}: value is required")
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"row {row_number} field {field}: invalid number {value!r}") from exc
    if min_value is not None and number < min_value:
        raise ValueError(f"row {row_number} field {field}: value must be >= {min_value:g}")
    return number


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def normalize_row(row: dict[str, str], row_number: int) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    trade_date = normalize_date(row.get("trade_date") or "", "trade_date", row_number)
    code = (row.get("code") or "").strip()
    if not CODE_PATTERN.match(code):
        raise ValueError(f"row {row_number} field code: expected 6 digits, got {code!r}")

    open_price = parse_number(row.get("open") or "", "open", row_number)
    high = parse_number(row.get("high") or "", "high", row_number)
    low = parse_number(row.get("low") or "", "low", row_number)
    close = parse_number(row.get("close") or "", "close", row_number)
    pre_close = parse_number(row.get("pre_close") or "", "pre_close", row_number, required=False)
    volume = parse_number(row.get("volume") or "", "volume", row_number)
    turnover = parse_number(row.get("turnover") or "", "turnover", row_number)
    turnover_rate = parse_number(row.get("turnover_rate") or "", "turnover_rate", row_number, required=False)

    assert open_price is not None and high is not None and low is not None and close is not None
    assert volume is not None and turnover is not None
    if high < low:
        raise ValueError(f"row {row_number} field high/low: high must be >= low")
    for field, value in (("open", open_price), ("close", close)):
        if value < low or value > high:
            raise ValueError(f"row {row_number} field {field}: value must be between low and high")

    for field in BOOLEAN_FIELDS:
        normalized[field] = parse_bool(row.get(field) or "", field, row_number)

    adjust_type = (row.get("adjust_type") or "").strip().lower()
    if adjust_type not in VALID_ADJUST_TYPES:
        allowed = ", ".join(sorted(VALID_ADJUST_TYPES - {""}))
        raise ValueError(f"row {row_number} field adjust_type: expected one of {allowed}, got {adjust_type!r}")

    normalized.update(
        {
            "trade_date": trade_date,
            "code": code,
            "open": format_number(open_price),
            "high": format_number(high),
            "low": format_number(low),
            "close": format_number(close),
            "pre_close": format_number(pre_close),
            "volume": format_number(volume),
            "turnover": format_number(turnover),
            "turnover_rate": format_number(turnover_rate),
            "adjust_type": adjust_type,
            "data_source": (row.get("data_source") or "").strip(),
            "updated_at": normalize_optional_date(row.get("updated_at") or "", "updated_at", row_number),
        }
    )
    return normalized


def validate_header(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("input CSV is missing header")
    missing = [field for field in REQUIRED_FIELDS if field not in fieldnames]
    if missing:
        raise ValueError(f"input CSV missing required columns: {', '.join(missing)}")


def read_daily_bars(input_path: Path) -> tuple[list[dict[str, Any]], list[ImportIssue]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        validate_header(reader.fieldnames)
        rows: list[dict[str, Any]] = []
        issues: list[ImportIssue] = []
        seen_keys: set[tuple[str, str]] = set()

        for row_number, row in enumerate(reader, start=2):
            try:
                normalized = normalize_row(row, row_number)
                key = (normalized["trade_date"], normalized["code"])
                if key in seen_keys:
                    raise ValueError(f"row {row_number} field trade_date/code: duplicate bar {key[0]} {key[1]}")
                seen_keys.add(key)
                rows.append(normalized)
            except ValueError as exc:
                issues.append(ImportIssue(row=row_number, field="", message=str(exc)))

    rows.sort(key=lambda item: (item["code"], item["trade_date"]))
    return rows, issues


def write_daily_bars(output_path: Path, rows: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def build_metadata(input_path: Path, output_path: Path, rows: list[dict[str, Any]], issues: list[ImportIssue]) -> dict[str, Any]:
    codes = sorted({row["code"] for row in rows})
    trade_dates = sorted({row["trade_date"] for row in rows})
    return {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(input_path),
        "output": str(output_path),
        "row_count": len(rows),
        "issue_count": len(issues),
        "code_count": len(codes),
        "codes": codes,
        "start_date": trade_dates[0] if trade_dates else None,
        "end_date": trade_dates[-1] if trade_dates else None,
        "issues": [issue.__dict__ for issue in issues],
    }


def write_metadata(metadata_path: Path, metadata: dict[str, Any]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def import_daily_bars(input_path: Path, output_path: Path, metadata_path: Path, strict: bool = True) -> dict[str, Any]:
    rows, issues = read_daily_bars(input_path)
    if strict and issues:
        metadata = build_metadata(input_path, output_path, rows, issues)
        write_metadata(metadata_path, metadata)
        raise ValueError(f"import failed with {len(issues)} issue(s); see {metadata_path}")

    write_daily_bars(output_path, rows)
    metadata = build_metadata(input_path, output_path, rows, issues)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"imported rows: {metadata['row_count']}")
    print(f"issues: {metadata['issue_count']}")
    print(f"codes: {metadata['code_count']}")
    print(f"date range: {metadata['start_date'] or '-'} -> {metadata['end_date'] or '-'}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import and normalize A-share daily bar CSV.")
    parser.add_argument("--input", required=True, help="Input daily bar CSV.")
    parser.add_argument("--output", default="data/processed/daily_bars.csv", help="Normalized output CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/daily_bars.import.json", help="Import metadata JSON.")
    parser.add_argument("--allow-invalid", action="store_true", help="Write valid rows even when some rows are invalid.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = import_daily_bars(
            Path(args.input),
            Path(args.output),
            Path(args.metadata_output),
            strict=not args.allow_invalid,
        )
    except Exception as exc:
        print(f"daily bars import failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 1 if metadata["issue_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

