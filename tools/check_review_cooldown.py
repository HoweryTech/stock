#!/usr/bin/env python3
"""Check review-derived losing streak cooldown status."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml, value_at


def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(match) for match in sorted(glob.glob(pattern)))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def review_row(path: Path) -> dict[str, Any]:
    review = load_yaml(path)
    return {
        "path": str(path),
        "review_id": value_at(review, "review.id"),
        "exit_date": value_at(review, "execution.exit_date") or "",
        "strategy": value_at(review, "trade_plan_snapshot.strategy.source") or "UNKNOWN",
        "trade_return_pct": as_float(value_at(review, "result.trade_return_pct")),
        "result_category": value_at(review, "result.result_category"),
    }


def sorted_reviews(review_paths: list[Path]) -> list[dict[str, Any]]:
    rows = [review_row(path) for path in review_paths]
    return sorted(rows, key=lambda row: (row["exit_date"], row["review_id"] or "", row["path"]))


def losing_streak(rows: list[dict[str, Any]]) -> int:
    streak = 0
    for row in reversed(rows):
        trade_return = row["trade_return_pct"]
        if trade_return is None:
            continue
        if trade_return < 0:
            streak += 1
        else:
            break
    return streak


def streaks_by_strategy(rows: list[dict[str, Any]]) -> dict[str, int]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["strategy"], []).append(row)
    return {strategy: losing_streak(items) for strategy, items in sorted(grouped.items())}


def check_cooldown(profile: dict[str, Any], review_paths: list[Path]) -> dict[str, Any]:
    config = value_at(profile, "risk.losing_streak_cooldown") or {}
    enabled = bool(config.get("enabled", False))
    threshold = int(config.get("consecutive_losing_trades") or 0)
    cooldown_days = int(config.get("cooldown_trading_days") or 0)
    rows = sorted_reviews(review_paths)
    overall_streak = losing_streak(rows)
    strategy_streaks = streaks_by_strategy(rows)
    actions: list[dict[str, str]] = []

    if enabled and threshold > 0 and overall_streak >= threshold:
        actions.append(
            {
                "code": "overall_losing_streak_cooldown",
                "message": f"整体连续亏损 {overall_streak} 笔，达到冷静期阈值 {threshold} 笔；暂停新开仓 {cooldown_days} 个交易日。",
            }
        )
    for strategy, streak in strategy_streaks.items():
        if enabled and threshold > 0 and streak >= threshold:
            actions.append(
                {
                    "code": "strategy_losing_streak_cooldown",
                    "message": f"策略 {strategy} 连续亏损 {streak} 笔，达到冷静期阈值 {threshold} 笔；暂停该策略新开仓 {cooldown_days} 个交易日。",
                }
            )

    return {
        "enabled": enabled,
        "conclusion": "cooldown_required" if actions else "normal",
        "review_count": len(rows),
        "threshold": threshold,
        "cooldown_trading_days": cooldown_days,
        "overall_losing_streak": overall_streak,
        "strategy_losing_streaks": strategy_streaks,
        "actions": actions,
        "reviews": rows,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_text(result: dict[str, Any]) -> None:
    print(f"conclusion: {result['conclusion']}")
    print(f"review count: {result['review_count']}")
    print(f"overall losing streak: {result['overall_losing_streak']}")
    print("strategy losing streaks:")
    if not result["strategy_losing_streaks"]:
        print("- none")
    for strategy, streak in result["strategy_losing_streaks"].items():
        print(f"- {strategy}: {streak}")
    print("actions:")
    if not result["actions"]:
        print("- none")
    for item in result["actions"]:
        print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check review-derived losing streak cooldown status.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--output", default="data/metadata/review-cooldown.json", help="Output cooldown JSON.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile = load_yaml(Path(args.profile))
        result = check_cooldown(profile, expand_paths(args.reviews))
        write_json(Path(args.output), result)
    except Exception as exc:
        print(f"review cooldown check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "cooldown_required" else 0


if __name__ == "__main__":
    raise SystemExit(main())
