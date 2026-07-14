#!/usr/bin/env python3
"""Fetch A-share daily bars from Sina and write the project standard CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from tools.import_daily_bars import STANDARD_FIELDS
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from import_daily_bars import STANDARD_FIELDS
    from risk_check import load_yaml, value_at


SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"


def market_prefix_for_code(code: str) -> str:
    code = code.strip()
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("0", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    raise ValueError(f"unsupported A-share code: {code}")


def symbol_for_code(code: str) -> str:
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"stock code must be 6 digits, got {code!r}")
    return f"{market_prefix_for_code(code)}{code}"


def infer_limit_flags(close: float, pre_close: float | None) -> tuple[bool, bool]:
    if pre_close in (None, 0):
        return False, False
    pct = (close / pre_close - 1) * 100
    return pct >= 9.8, pct <= -9.8


def parse_number(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 6)
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def normalize_sina_rows(code: str, rows: list[dict[str, Any]], *, updated_at: str | None = None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    previous_close: float | None = None
    updated = updated_at or date.today().isoformat()
    for source in rows:
        trade_date = str(source.get("day") or "").strip()
        datetime.strptime(trade_date, "%Y-%m-%d")
        open_price = parse_number(source.get("open"), "open")
        high = parse_number(source.get("high"), "high")
        low = parse_number(source.get("low"), "low")
        close = parse_number(source.get("close"), "close")
        volume = parse_number(source.get("volume"), "volume")
        if high < low:
            raise ValueError(f"{code} {trade_date}: high must be >= low")
        if open_price < low or open_price > high or close < low or close > high:
            raise ValueError(f"{code} {trade_date}: open/close must be between low and high")

        is_limit_up, is_limit_down = infer_limit_flags(close, previous_close)
        turnover = close * volume
        normalized.append(
            {
                "trade_date": trade_date,
                "code": code,
                "open": format_number(open_price),
                "high": format_number(high),
                "low": format_number(low),
                "close": format_number(close),
                "pre_close": format_number(previous_close),
                "volume": format_number(volume),
                "turnover": format_number(turnover),
                "turnover_rate": "",
                "is_limit_up": is_limit_up,
                "is_limit_down": is_limit_down,
                "is_suspended": False,
                "adjust_type": "none",
                "data_source": "sina_kline",
                "updated_at": updated,
            }
        )
        previous_close = close
    return normalized


def fetch_sina_kline(code: str, datalen: int, timeout: float = 15.0) -> list[dict[str, Any]]:
    params = urlencode({"symbol": symbol_for_code(code), "scale": "240", "ma": "no", "datalen": str(datalen)})
    request = Request(
        f"{SINA_KLINE_URL}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", "replace")
    except URLError as exc:
        raise RuntimeError(f"failed to fetch {code} daily bars from Sina: {exc}") from exc

    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError(f"unexpected Sina response for {code}")
    return normalize_sina_rows(code, data)


def read_existing_daily_bars(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def merge_rows(existing: list[dict[str, Any]], fetched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing + fetched:
        key = ((row.get("trade_date") or "").strip(), (row.get("code") or "").strip())
        if key[0] and key[1]:
            merged[key] = row
    return [merged[key] for key in sorted(merged)]


def write_daily_bars(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def extract_codes_from_positions(paths: list[str]) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        position = load_yaml(Path(raw_path))
        code = value_at(position, "stock.code")
        if code and code not in seen:
            codes.append(str(code))
            seen.add(str(code))
    return codes


def fetch_daily_bars(codes: list[str], output: Path, datalen: int, merge_existing: bool = True) -> dict[str, Any]:
    fetched: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for code in codes:
        try:
            fetched.extend(fetch_sina_kline(code, datalen=datalen))
        except Exception as exc:
            errors.append({"code": code, "message": str(exc)})

    existing = read_existing_daily_bars(output) if merge_existing else []
    rows = merge_rows(existing, fetched)
    write_daily_bars(output, rows)
    dates = sorted({row["trade_date"] for row in fetched})
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": "sina_kline",
        "codes": codes,
        "requested_code_count": len(codes),
        "fetched_row_count": len(fetched),
        "output_row_count": len(rows),
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "output": str(output),
        "errors": errors,
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"source: {metadata['source']}")
    print(f"codes: {', '.join(metadata['codes']) or '-'}")
    print(f"fetched rows: {metadata['fetched_row_count']}")
    print(f"date range: {metadata['start_date'] or '-'} -> {metadata['end_date'] or '-'}")
    print(f"output: {metadata['output']}")
    if metadata["errors"]:
        print("errors:")
        for item in metadata["errors"]:
            print(f"- {item['code']}: {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch A-share daily bars from Sina.")
    parser.add_argument("--codes", nargs="*", default=[], help="6-digit stock codes.")
    parser.add_argument("--positions", nargs="*", default=[], help="Position YAML files; stock.code will be fetched.")
    parser.add_argument("--datalen", type=int, default=120, help="Number of daily bars per code.")
    parser.add_argument("--output", default="data/processed/daily_bars.csv", help="Standard daily bars CSV output.")
    parser.add_argument("--metadata-output", default="data/metadata/daily_bars.fetch.json", help="Fetch metadata JSON.")
    parser.add_argument("--replace", action="store_true", help="Replace output instead of merging with existing rows.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        codes = list(dict.fromkeys(args.codes + extract_codes_from_positions(args.positions)))
        if not codes:
            raise ValueError("provide --codes or --positions")
        metadata = fetch_daily_bars(codes, Path(args.output), datalen=args.datalen, merge_existing=not args.replace)
        write_metadata(Path(args.metadata_output), metadata)
    except Exception as exc:
        print(f"fetch daily bars failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 1 if metadata["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
