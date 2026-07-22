#!/usr/bin/env python3
"""Refresh 5-minute bar cache for current holding positions."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.repair_data_quality import fetch_minute_cache_for_code
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from repair_data_quality import fetch_minute_cache_for_code
    from risk_check import load_yaml, value_at


def position_codes(position_patterns: list[str]) -> list[str]:
    codes: list[str] = []
    for path in expand_position_paths(position_patterns):
        code = str(value_at(load_yaml(path), "stock.code") or "")
        if code and code not in codes:
            codes.append(code)
    return codes


def refresh_codes(codes: list[str], args: argparse.Namespace) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    cache_dir = Path(args.minute_cache_dir)
    for code in codes:
        try:
            items.append(fetch_minute_cache_for_code(code, cache_dir, args.minute_begin, args.minute_end))
            if args.request_interval_seconds > 0:
                time.sleep(args.request_interval_seconds)
        except Exception as exc:
            errors.append({"code": code, "message": str(exc)})
    return {
        "generated_at": date.today().isoformat(),
        "cache_dir": str(cache_dir),
        "item_count": len(items),
        "error_count": len(errors),
        "items": items,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh current holding 5-minute bar cache.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--minute-cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--minute-begin", default=(date.today() - timedelta(days=180)).strftime("%Y%m%d"))
    parser.add_argument("--minute-end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--request-interval-seconds", type=float, default=0.05)
    parser.add_argument("--output", default="data/metadata/minute-cache-refresh.latest.json")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codes = position_codes(args.positions)
    result = refresh_codes(codes, args)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        latest = max((str(item.get("latest_timestamp") or "") for item in result["items"]), default="-")
        print(f"minute cache: {result['item_count']}/{len(codes)} refreshed, errors={result['error_count']}, latest={latest}")
    return 0 if not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
