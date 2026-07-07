#!/usr/bin/env python3
"""Run trade plan quality and risk checks as one approval gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from tools.check_trade_plan_quality import check_trade_plan_quality
    from tools.risk_check import load_yaml, validate_plan
except ModuleNotFoundError:
    from check_trade_plan_quality import check_trade_plan_quality
    from risk_check import load_yaml, validate_plan


def gate_conclusion(quality: dict[str, Any], risk: dict[str, Any] | None) -> str:
    if quality["conclusion"] == "blocked":
        return "blocked_by_quality"
    if risk is None:
        return "blocked_by_quality"
    if risk["conclusion"] == "blocked":
        return "blocked_by_risk"
    if quality["conclusion"] == "needs_review" or risk["conclusion"] == "needs_confirmation":
        return "needs_confirmation"
    return "pass"


def run_gate(profile_path: Path, plan_path: Path, *, skip_risk_when_quality_blocked: bool = True) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    plan = load_yaml(plan_path)
    quality = check_trade_plan_quality(plan)
    risk = None
    if not (skip_risk_when_quality_blocked and quality["conclusion"] == "blocked"):
        risk = validate_plan(profile, plan)

    return {
        "trade_plan_id": plan.get("trade_plan", {}).get("id"),
        "profile": str(profile_path),
        "plan": str(plan_path),
        "conclusion": gate_conclusion(quality, risk),
        "quality": quality,
        "risk": risk,
    }


def print_text(result: dict[str, Any]) -> None:
    print(f"trade plan: {result.get('trade_plan_id') or '-'}")
    print(f"gate conclusion: {result['conclusion']}")
    print(f"quality conclusion: {result['quality']['conclusion']}")
    print(f"risk conclusion: {result['risk']['conclusion'] if result['risk'] else 'skipped'}")

    for title, section, key in (
        ("quality blockers", "quality", "blockers"),
        ("quality warnings", "quality", "warnings"),
        ("risk blockers", "risk", "blockers"),
        ("risk warnings", "risk", "warnings"),
    ):
        print(f"\n{title}:")
        data = result.get(section)
        items = data.get(key, []) if data else []
        if not items:
            print("- none")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run trade plan quality and risk checks as one approval gate.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--plan", required=True, help="Path to trade plan YAML.")
    parser.add_argument("--run-risk-even-if-quality-blocked", action="store_true", help="Run risk check even when quality check is blocked.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_gate(
            Path(args.profile),
            Path(args.plan),
            skip_risk_when_quality_blocked=not args.run_risk_even_if_quality_blocked,
        )
    except Exception as exc:
        print(f"trade plan gate failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)

    return 0 if result["conclusion"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
