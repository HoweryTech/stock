#!/usr/bin/env python3
"""Import and normalize valuation metrics CSV files."""

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
    "trade_date",
    "code",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "pcf_ttm",
    "dividend_yield",
    "market_cap",
    "float_market_cap",
    "pe_percentile",
    "pb_percentile",
    "industry_pe_percentile",
    "industry_pb_percentile",
    "data_source",
    "updated_at",
]

REQUIRED_FIELDS = ["trade_date", "code"]
NUMERIC_FIELDS = [
    "pe_ttm",
    "pb",
    "ps_ttm",
    "pcf_ttm",
    "dividend_yield",
    "market_cap",
    "float_market_cap",
    "pe_percentile",
    "pb_percentile",
    "industry_pe_percentile",
    "industry_pb_percentile",
]
CODE_PATTERN = re.compile(r"^\d{6}$")


@dataclass
class ImportIssue:
    row: int
    field: str
    message: str


def normalize_date(value: str, field: str, row_number: int, *, required: bool = True) -> str:
    value = (value or "").strip()
    if not value:
        if required:
            raise ValueError(f"row {row_number} field {field}: value is required")
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"row {row_number} field {field}: expected YYYY-MM-DD, got {value!r}") from exc
    return value


def parse_number(value: str, field: str, row_number: int) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"row {row_number} field {field}: invalid number {value!r}") from exc


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def normalize_percentile(value: float | None, field: str, row_number: int) -> str:
    if value is None:
        return ""
    if value < 0 or value > 100:
        raise ValueError(f"row {row_number} field {field}: percentile must be between 0 and 100")
    return format_number(value)


def normalize_row(row: dict[str, str], row_number: int) -> dict[str, Any]:
    trade_date = normalize_date(row.get("trade_date") or "", "trade_date", row_number)
    code = (row.get("code") or "").strip()
    if not CODE_PATTERN.match(code):
        raise ValueError(f"row {row_number} field code: expected 6 digits, got {code!r}")

    normalized: dict[str, Any] = {
        "trade_date": trade_date,
        "code": code,
    }
    for field in NUMERIC_FIELDS:
        value = parse_number(row.get(field) or "", field, row_number)
        if field.endswith("_percentile"):
            normalized[field] = normalize_percentile(value, field, row_number)
        else:
            normalized[field] = format_number(value)
    normalized["data_source"] = (row.get("data_source") or "").strip()
    normalized["updated_at"] = normalize_date(row.get("updated_at") or "", "updated_at", row_number, required=False)
    return normalized


def validate_header(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("input CSV is missing header")
    missing = [field for field in REQUIRED_FIELDS if field not in fieldnames]
    if missing:
        raise ValueError(f"input CSV missing required columns: {', '.join(missing)}")


def read_valuation_metrics(input_path: Path) -> tuple[list[dict[str, Any]], list[ImportIssue]]:
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
                    raise ValueError(f"row {row_number} field trade_date/code: duplicate metrics {key[0]} {key[1]}")
                seen_keys.add(key)
                rows.append(normalized)
            except ValueError as exc:
                issues.append(ImportIssue(row=row_number, field="", message=str(exc)))

    rows.sort(key=lambda item: (item["code"], item["trade_date"]))
    return rows, issues


def write_valuation_metrics(output_path: Path, rows: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def build_metadata(input_path: Path, output_path: Path, rows: list[dict[str, Any]], issues: list[ImportIssue]) -> dict[str, Any]:
    codes = sorted({row["code"] for row in rows})
    dates = sorted({row["trade_date"] for row in rows})
    return {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(input_path),
        "output": str(output_path),
        "row_count": len(rows),
        "issue_count": len(issues),
        "code_count": len(codes),
        "codes": codes,
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "issues": [issue.__dict__ for issue in issues],
    }


def write_metadata(metadata_path: Path, metadata: dict[str, Any]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def import_valuation_metrics(input_path: Path, output_path: Path, metadata_path: Path, strict: bool = True) -> dict[str, Any]:
    rows, issues = read_valuation_metrics(input_path)
    if strict and issues:
        metadata = build_metadata(input_path, output_path, rows, issues)
        write_metadata(metadata_path, metadata)
        raise ValueError(f"import failed with {len(issues)} issue(s); see {metadata_path}")

    write_valuation_metrics(output_path, rows)
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
    parser = argparse.ArgumentParser(description="Import and normalize valuation metrics CSV.")
    parser.add_argument("--input", required=True, help="Input valuation metrics CSV.")
    parser.add_argument("--output", default="data/processed/valuation_metrics.csv", help="Normalized output CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/valuation_metrics.import.json", help="Import metadata JSON.")
    parser.add_argument("--allow-invalid", action="store_true", help="Write valid rows even when some rows are invalid.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = import_valuation_metrics(
            Path(args.input),
            Path(args.output),
            Path(args.metadata_output),
            strict=not args.allow_invalid,
        )
    except Exception as exc:
        print(f"valuation metrics import failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 1 if metadata["issue_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
