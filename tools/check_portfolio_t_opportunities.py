#!/usr/bin/env python3
"""Check T-trade market setups and execution gates for multiple holdings."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.check_t_trade_opportunity import check_t_opportunity, read_bars
    from tools.fetch_daily_bars_sina import fetch_daily_bars
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from check_t_trade_opportunity import check_t_opportunity, read_bars
    from fetch_daily_bars_sina import fetch_daily_bars
    from risk_check import load_yaml, value_at


def check_portfolio_t_opportunities(
    profile: dict[str, Any],
    position_paths: list[Path],
    daily_bars_path: Path,
    **check_options: Any,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for path in position_paths:
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        if not code:
            raise ValueError(f"{path}: missing stock.code")
        result = check_t_opportunity(profile, position, read_bars(daily_bars_path, code), **check_options)
        items.append({"path": str(path), "result": result})

    setup_counts = Counter(item["result"]["market_setup"] for item in items)
    conclusion_counts = Counter(item["result"]["conclusion"] for item in items)
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "position_count": len(items),
        "market_setup_counts": dict(sorted(setup_counts.items())),
        "execution_conclusion_counts": dict(sorted(conclusion_counts.items())),
        "items": items,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(result: dict[str, Any]) -> None:
    print(f"positions: {result['position_count']}")
    print(f"market setups: {result['market_setup_counts']}")
    print(f"execution conclusions: {result['execution_conclusion_counts']}")
    for item in result["items"]:
        check = item["result"]
        print(
            f"- {check['stock_code']} {check['stock_name']}: "
            f"market={check['market_setup']}, execution={check['conclusion']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check T-trade opportunities for multiple holdings.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--output", default="data/metadata/portfolio-t-opportunities.check.json")
    parser.add_argument("--auto-fetch", action="store_true")
    parser.add_argument("--fetch-datalen", type=int, default=120)
    parser.add_argument("--short-window", type=int, default=5)
    parser.add_argument("--mid-window", type=int, default=20)
    parser.add_argument("--near-stop-pct", type=float, help="Defaults to t_trading.near_stop_block_pct in profile.")
    parser.add_argument("--pullback-pct", type=float, default=3.0)
    parser.add_argument("--overextended-pct", type=float, default=6.0)
    parser.add_argument("--min-spread-pct", type=float, default=1.2)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        paths = expand_position_paths(args.positions)
        profile = load_yaml(Path(args.profile))
        daily_bars_path = Path(args.daily_bars)
        if args.auto_fetch:
            codes = [str(value_at(load_yaml(path), "stock.code")) for path in paths]
            fetch_result = fetch_daily_bars(codes, daily_bars_path, args.fetch_datalen, merge_existing=True)
            if fetch_result["errors"]:
                raise RuntimeError(f"daily bar fetch errors: {fetch_result['errors']}")
        result = check_portfolio_t_opportunities(
            profile,
            paths,
            daily_bars_path,
            short_window=args.short_window,
            mid_window=args.mid_window,
            near_stop_pct=args.near_stop_pct,
            pullback_pct=args.pullback_pct,
            overextended_pct=args.overextended_pct,
            min_spread_pct=args.min_spread_pct,
        )
        write_json(Path(args.output), result)
    except Exception as exc:
        print(f"check portfolio T-trade opportunities failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)
    return 1 if result["execution_conclusion_counts"].get("blocked") else 0


if __name__ == "__main__":
    raise SystemExit(main())
