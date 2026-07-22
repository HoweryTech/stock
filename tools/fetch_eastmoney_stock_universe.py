#!/usr/bin/env python3
"""Fetch A-share stock universe from Eastmoney public quote list."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from tools.import_stock_universe import STANDARD_FIELDS
except ModuleNotFoundError:
    from import_stock_universe import STANDARD_FIELDS


EASTMONEY_LIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
DEFAULT_FIELDS = "f12,f14,f13,f100,f2,f3,f5,f6,f20,f21,f15,f16,f17,f18,f26,f107,f115,f152"
DEFAULT_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"


def as_number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def exchange_from_market(code: str, market: Any) -> str:
    market_code = str(market)
    if code.startswith(("4", "8", "92")):
        return "BSE"
    if market_code == "1" or code.startswith(("6", "9")):
        return "SSE"
    if market_code == "0" or code.startswith(("0", "2", "3")):
        return "SZSE"
    return "UNKNOWN"


def normalize_listing_date(value: Any) -> str:
    raw = str(value or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def normalize_row(row: dict[str, Any], updated_at: str) -> dict[str, Any]:
    code = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip()
    current_turnover = as_number(row.get("f6")) or 0.0
    listing_date = normalize_listing_date(row.get("f26"))
    return {
        "code": code,
        "name": name,
        "exchange": exchange_from_market(code, row.get("f13")),
        "industry": str(row.get("f100") or "").strip() or "UNKNOWN",
        "is_st": "ST" in name.upper() or "退" in name,
        "is_suspended": row.get("f2") in (None, "-", ""),
        "has_delisting_risk": "退" in name,
        "abnormal_trading_status": False,
        "listing_date": listing_date,
        "avg_daily_turnover_cny": round(current_turnover, 2),
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
    raise RuntimeError(f"failed to fetch Eastmoney stock universe page {page}: {last_error}") from last_error


def fetch_universe(page_size: int = 500, timeout: float = 15.0) -> list[dict[str, Any]]:
    first = fetch_page(1, page_size, timeout)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    rows = list(data.get("diff") or [])
    effective_page_size = len(rows) or page_size
    pages = (total + effective_page_size - 1) // effective_page_size
    for page in range(2, pages + 1):
        payload = fetch_page(page, page_size, timeout)
        rows.extend((payload.get("data") or {}).get("diff") or [])
    updated_at = date.today().isoformat()
    normalized = [normalize_row(row, updated_at) for row in rows if str(row.get("f12") or "").strip()]
    return sorted(normalized, key=lambda item: item["code"])


def write_universe(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def build_metadata(rows: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": "eastmoney_quote_list",
        "output": str(output),
        "row_count": len(rows),
        "exchange_counts": {
            exchange: sum(1 for row in rows if row.get("exchange") == exchange)
            for exchange in sorted({str(row.get("exchange") or "UNKNOWN") for row in rows})
        },
        "st_count": sum(1 for row in rows if row.get("is_st")),
        "suspended_count": sum(1 for row in rows if row.get("is_suspended")),
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch A-share stock universe from Eastmoney.")
    parser.add_argument("--output", default="data/processed/stock_universe.csv")
    parser.add_argument("--metadata-output", default="data/metadata/stock_universe.fetch.json")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows = fetch_universe(args.page_size, args.timeout)
        output = Path(args.output)
        write_universe(output, rows)
        metadata = build_metadata(rows, output)
        write_metadata(Path(args.metadata_output), metadata)
    except Exception as exc:
        print(f"fetch Eastmoney stock universe failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"stock universe rows: {metadata['row_count']}")
        print(f"exchange counts: {metadata['exchange_counts']}")
        print(f"output: {metadata['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
