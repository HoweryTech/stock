#!/usr/bin/env python3
"""Check and summarize multiple position YAML files."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.position_check import validate_position
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from position_check import validate_position
    from risk_check import as_float, load_yaml, value_at


def expand_position_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            paths.extend(Path(match) for match in matches)
        else:
            paths.append(Path(pattern))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def summarize_positions(profile: dict[str, Any], position_paths: list[Path], near_stop_pct: float = 3.0) -> dict[str, Any]:
    position_results: list[dict[str, Any]] = []
    total_position_pct = 0.0
    industry_position_pct: dict[str, float] = {}

    for path in position_paths:
        position = load_yaml(path)
        result = validate_position(profile, position, near_stop_pct=near_stop_pct)
        position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
        industry = value_at(position, "stock.industry") or "UNKNOWN"
        total_position_pct += position_pct
        industry_position_pct[industry] = industry_position_pct.get(industry, 0.0) + position_pct
        position_results.append({"path": str(path), "result": result})

    needs_action = [item for item in position_results if item["result"]["conclusion"] == "needs_action"]
    warnings = [item for item in position_results if item["result"]["conclusion"] == "warning"]
    if needs_action:
        conclusion = "needs_action"
    elif warnings:
        conclusion = "warning"
    else:
        conclusion = "normal"

    risk_config = profile.get("risk", {})
    max_total_pct = as_float(risk_config.get("max_total_position_pct"), 100.0) or 100.0
    max_industry_pct = as_float(risk_config.get("max_position_pct_per_industry"), 100.0) or 100.0
    portfolio_warnings: list[dict[str, str]] = []
    portfolio_actions: list[dict[str, str]] = []

    if total_position_pct > max_total_pct:
        portfolio_actions.append({"code": "portfolio_total_position_exceeded", "message": f"组合总仓位 {total_position_pct:.2f}% 超过上限 {max_total_pct:.2f}%。"})
    elif total_position_pct > max_total_pct * 0.8:
        portfolio_warnings.append({"code": "portfolio_total_position_high", "message": "组合总仓位接近上限。"})

    for industry, pct in sorted(industry_position_pct.items()):
        if pct > max_industry_pct:
            portfolio_actions.append({"code": "portfolio_industry_position_exceeded", "message": f"{industry} 行业仓位 {pct:.2f}% 超过上限 {max_industry_pct:.2f}%。"})
        elif pct > max_industry_pct * 0.8:
            portfolio_warnings.append({"code": "portfolio_industry_position_high", "message": f"{industry} 行业仓位接近上限。"})

    if portfolio_actions:
        conclusion = "needs_action"
    elif portfolio_warnings and conclusion == "normal":
        conclusion = "warning"

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "conclusion": conclusion,
        "position_count": len(position_results),
        "total_position_pct": round(total_position_pct, 4),
        "industry_position_pct": {industry: round(pct, 4) for industry, pct in sorted(industry_position_pct.items())},
        "portfolio_actions": portfolio_actions,
        "portfolio_warnings": portfolio_warnings,
        "needs_action_count": len(needs_action),
        "warning_count": len(warnings),
        "positions": position_results,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(result: dict[str, Any]) -> None:
    print(f"conclusion: {result['conclusion']}")
    print(f"position count: {result['position_count']}")
    print(f"total position pct: {result['total_position_pct']}")
    print(f"needs action: {result['needs_action_count']}")
    print(f"warnings: {result['warning_count']}")
    if result["portfolio_actions"]:
        print("portfolio actions:")
        for item in result["portfolio_actions"]:
            print(f"- [{item['code']}] {item['message']}")
    if result["portfolio_warnings"]:
        print("portfolio warnings:")
        for item in result["portfolio_warnings"]:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check and summarize multiple positions.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--positions", nargs="+", default=["positions/*.yaml"], help="Position YAML paths or glob patterns.")
    parser.add_argument("--near-stop-pct", type=float, default=3.0, help="Warn when current price is within this percent above stop loss.")
    parser.add_argument("--output", default="data/metadata/portfolio_positions.check.json", help="Output portfolio check JSON.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile = load_yaml(Path(args.profile))
        position_paths = expand_position_paths(args.positions)
        result = summarize_positions(profile, position_paths, near_stop_pct=args.near_stop_pct)
        write_json(Path(args.output), result)
    except Exception as exc:
        print(f"portfolio position check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)
    return 1 if result["conclusion"] == "needs_action" else 0


if __name__ == "__main__":
    raise SystemExit(main())
