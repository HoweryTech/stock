#!/usr/bin/env python3
"""Fetch A-share valuation metrics from Eastmoney public quote list."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from tools.data_retention import retain_file_snapshot
    from tools.import_valuation_metrics import STANDARD_FIELDS
except ModuleNotFoundError:
    from data_retention import retain_file_snapshot
    from import_valuation_metrics import STANDARD_FIELDS


EASTMONEY_LIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
DEFAULT_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
DEFAULT_FIELDS = "f12,f14,f9,f23,f20,f21,f115,f152"


def as_number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 6)
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def normalize_row(row: dict[str, Any], trade_date: str, updated_at: str) -> dict[str, str]:
    pe_ttm = as_number(row.get("f115"))
    pe_dynamic = as_number(row.get("f9"))
    return {
        "trade_date": trade_date,
        "code": str(row.get("f12") or "").strip(),
        "pe_ttm": format_number(pe_ttm if pe_ttm is not None else pe_dynamic),
        "pb": format_number(as_number(row.get("f23"))),
        "ps_ttm": "",
        "pcf_ttm": "",
        "dividend_yield": "",
        "market_cap": format_number(as_number(row.get("f20"))),
        "float_market_cap": format_number(as_number(row.get("f21"))),
        "pe_percentile": "",
        "pb_percentile": "",
        "industry_pe_percentile": "",
        "industry_pb_percentile": "",
        "data_source": "eastmoney_quote_list",
        "updated_at": updated_at,
    }


def fetch_page(page: int, page_size: int, timeout: float, retries: int = 3) -> dict[str, Any]:
    params = {
        "pn": page,
        "pz": page_size,
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": DEFAULT_FS,
        "fields": DEFAULT_FIELDS,
    }
    request = Request(
        f"{EASTMONEY_LIST_URL}?{urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/center/gridlist.html",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * attempt)
    raise RuntimeError(f"failed to fetch Eastmoney valuation page {page}: {last_error}") from last_error


def fetch_rows(page_size: int = 100, timeout: float = 15.0, trade_date: str | None = None) -> list[dict[str, str]]:
    first = fetch_page(1, page_size, timeout)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    raw_rows = list(data.get("diff") or [])
    effective_page_size = len(raw_rows) or page_size
    pages = (total + effective_page_size - 1) // effective_page_size
    for page in range(2, pages + 1):
        payload = fetch_page(page, page_size, timeout)
        raw_rows.extend((payload.get("data") or {}).get("diff") or [])

    valuation_date = trade_date or date.today().isoformat()
    updated_at = date.today().isoformat()
    rows = [normalize_row(row, valuation_date, updated_at) for row in raw_rows if str(row.get("f12") or "").strip()]
    return sorted(rows, key=lambda item: item["code"])


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def build_metadata(rows: list[dict[str, str]], output: Path, retained_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    missing: dict[str, int] = {}
    for field in ("pe_ttm", "pb", "ps_ttm", "pcf_ttm", "dividend_yield", "pe_percentile", "pb_percentile"):
        missing[field] = sum(1 for row in rows if not row.get(field))
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": "eastmoney_quote_list",
        "output": str(output),
        "row_count": len(rows),
        "trade_date": rows[0]["trade_date"] if rows else None,
        "missing_by_field": missing,
        "field_notes": {
            "pe_ttm": "Eastmoney f115 when available, otherwise f9 dynamic PE fallback.",
            "pb": "Eastmoney f23.",
            "market_cap": "Eastmoney f20.",
            "float_market_cap": "Eastmoney f21.",
            "ps_ttm": "Reserved; not populated by quote list source.",
            "pcf_ttm": "Reserved; not populated by quote list source.",
            "dividend_yield": "Reserved; not populated by quote list source.",
            "industry_percentiles": "Reserved for later industry distribution calculation.",
        },
        "retained_snapshot": retained_snapshot,
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch A-share valuation metrics from Eastmoney.")
    parser.add_argument("--output", default="data/processed/valuation_metrics.csv")
    parser.add_argument("--metadata-output", default="data/metadata/valuation_metrics.fetch.json")
    parser.add_argument("--trade-date", help="Override valuation trade date, defaults to today.")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--archive-root", default="data/raw/snapshots")
    parser.add_argument("--no-archive", action="store_true", help="Do not retain a raw snapshot copy.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows = fetch_rows(args.page_size, args.timeout, args.trade_date)
        output = Path(args.output)
        write_rows(output, rows)
        retained = None if args.no_archive else retain_file_snapshot(output, "valuation_metrics", Path(args.archive_root))
        metadata = build_metadata(rows, output, retained)
        write_metadata(Path(args.metadata_output), metadata)
    except Exception as exc:
        print(f"fetch Eastmoney valuation metrics failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"valuation rows: {metadata['row_count']}")
        print(f"trade date: {metadata['trade_date'] or '-'}")
        print(f"output: {metadata['output']}")
        if metadata.get("retained_snapshot"):
            print(f"retained snapshot: {metadata['retained_snapshot']['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
