#!/usr/bin/env python3
"""Import and normalize A-share stock universe CSV files."""

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


STANDARD_FIELDS = [
    "code",
    "name",
    "exchange",
    "industry",
    "is_st",
    "is_suspended",
    "has_delisting_risk",
    "abnormal_trading_status",
    "listing_date",
    "avg_daily_turnover_cny",
    "data_source",
    "updated_at",
]

REQUIRED_FIELDS = [
    "code",
    "name",
    "exchange",
    "industry",
    "is_st",
    "is_suspended",
    "has_delisting_risk",
    "abnormal_trading_status",
]

BOOLEAN_FIELDS = {
    "is_st",
    "is_suspended",
    "has_delisting_risk",
    "abnormal_trading_status",
}

VALID_EXCHANGES = {"SSE", "SZSE", "BSE", "UNKNOWN"}
CODE_PATTERN = re.compile(r"^\d{6}$")


@dataclass
class ImportIssue:
    row: int
    field: str
    message: str


def parse_bool(value: str, field: str, row_number: int) -> bool:
    normalized = value.strip().lower()
    truthy = {"true", "1", "yes", "y", "是"}
    falsy = {"false", "0", "no", "n", "否"}
    if normalized in truthy:
        return True
    if normalized in falsy:
        return False
    raise ValueError(f"row {row_number} field {field}: invalid boolean value {value!r}")


def normalize_date(value: str, field: str, row_number: int) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"row {row_number} field {field}: expected YYYY-MM-DD, got {value!r}") from exc
    return value


def normalize_turnover(value: str, row_number: int) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"row {row_number} field avg_daily_turnover_cny: invalid number {value!r}") from exc
    if number < 0:
        raise ValueError(f"row {row_number} field avg_daily_turnover_cny: value must be >= 0")
    return str(int(number)) if number.is_integer() else str(number)


def normalize_row(row: dict[str, str], row_number: int) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    code = (row.get("code") or "").strip()
    if not CODE_PATTERN.match(code):
        raise ValueError(f"row {row_number} field code: expected 6 digits, got {code!r}")
    normalized["code"] = code

    name = (row.get("name") or "").strip()
    if not name:
        raise ValueError(f"row {row_number} field name: value is required")
    normalized["name"] = name

    exchange = (row.get("exchange") or "").strip().upper()
    if exchange not in VALID_EXCHANGES:
        allowed = ", ".join(sorted(VALID_EXCHANGES))
        raise ValueError(f"row {row_number} field exchange: expected one of {allowed}, got {exchange!r}")
    normalized["exchange"] = exchange

    industry = (row.get("industry") or "").strip()
    if not industry:
        raise ValueError(f"row {row_number} field industry: value is required")
    normalized["industry"] = industry

    for field in BOOLEAN_FIELDS:
        normalized[field] = parse_bool(row.get(field) or "", field, row_number)

    normalized["listing_date"] = normalize_date(row.get("listing_date") or "", "listing_date", row_number)
    normalized["avg_daily_turnover_cny"] = normalize_turnover(row.get("avg_daily_turnover_cny") or "", row_number)
    normalized["data_source"] = (row.get("data_source") or "").strip()
    normalized["updated_at"] = normalize_date(row.get("updated_at") or "", "updated_at", row_number)
    return normalized


def validate_header(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("input CSV is missing header")
    missing = [field for field in REQUIRED_FIELDS if field not in fieldnames]
    if missing:
        raise ValueError(f"input CSV missing required columns: {', '.join(missing)}")


def read_stock_universe(input_path: Path) -> tuple[list[dict[str, Any]], list[ImportIssue]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        validate_header(reader.fieldnames)
        rows: list[dict[str, Any]] = []
        issues: list[ImportIssue] = []
        seen_codes: set[str] = set()

        for row_number, row in enumerate(reader, start=2):
            try:
                normalized = normalize_row(row, row_number)
                code = normalized["code"]
                if code in seen_codes:
                    raise ValueError(f"row {row_number} field code: duplicate code {code}")
                seen_codes.add(code)
                rows.append(normalized)
            except ValueError as exc:
                issues.append(ImportIssue(row=row_number, field="", message=str(exc)))

    return rows, issues


def write_stock_universe(output_path: Path, rows: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def build_metadata(input_path: Path, output_path: Path, rows: list[dict[str, Any]], issues: list[ImportIssue]) -> dict[str, Any]:
    return {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(input_path),
        "output": str(output_path),
        "row_count": len(rows),
        "issue_count": len(issues),
        "exchanges": sorted({row["exchange"] for row in rows}),
        "industries": sorted({row["industry"] for row in rows}),
        "issues": [issue.__dict__ for issue in issues],
    }


def write_metadata(metadata_path: Path, metadata: dict[str, Any]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def import_stock_universe(input_path: Path, output_path: Path, metadata_path: Path, strict: bool = True) -> dict[str, Any]:
    rows, issues = read_stock_universe(input_path)
    if strict and issues:
        metadata = build_metadata(input_path, output_path, rows, issues)
        write_metadata(metadata_path, metadata)
        raise ValueError(f"import failed with {len(issues)} issue(s); see {metadata_path}")

    write_stock_universe(output_path, rows)
    metadata = build_metadata(input_path, output_path, rows, issues)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"imported rows: {metadata['row_count']}")
    print(f"issues: {metadata['issue_count']}")
    print(f"exchanges: {', '.join(metadata['exchanges']) or '-'}")
    print(f"industries: {', '.join(metadata['industries']) or '-'}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import and normalize an A-share stock universe CSV.")
    parser.add_argument("--input", required=True, help="Input stock universe CSV.")
    parser.add_argument("--output", default="data/processed/stock_universe.csv", help="Normalized output CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/stock_universe.import.json", help="Import metadata JSON.")
    parser.add_argument("--allow-invalid", action="store_true", help="Write valid rows even when some rows are invalid.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = import_stock_universe(
            Path(args.input),
            Path(args.output),
            Path(args.metadata_output),
            strict=not args.allow_invalid,
        )
    except Exception as exc:
        print(f"stock universe import failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 1 if metadata["issue_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
