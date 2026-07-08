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
    from tools.risk_check import load_yaml, validate_plan, value_at
except ModuleNotFoundError:
    from check_trade_plan_quality import check_trade_plan_quality
    from risk_check import load_yaml, validate_plan, value_at


def load_strategy_health(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"available": False, "conclusion": "missing", "strategies": []}
    if not path.exists():
        return {"available": False, "conclusion": "missing", "path": str(path), "strategies": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["available"] = True
    return data


def strategy_health_for_plan(plan: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    strategy = value_at(plan, "strategy.source")
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    info: list[dict[str, str]] = []

    if not health.get("available"):
        return {"conclusion": "missing", "strategy": strategy, "blockers": [], "warnings": [], "info": []}

    matched = None
    for item in health.get("strategies", []) or []:
        if item.get("strategy") == strategy:
            matched = item
            break

    if matched is None:
        info.append({"code": "strategy_health_not_found", "message": f"策略 {strategy or '-'} 不在策略健康检查结果中。"})
        conclusion = "pass"
    elif matched.get("status") == "pause_new_entries":
        blockers.append({"code": "strategy_paused", "message": f"策略 {strategy} 已暂停新开仓，不能进入买入执行。"})
        conclusion = "blocked"
    elif matched.get("status") == "needs_review":
        warnings.append({"code": "strategy_needs_review", "message": f"策略 {strategy} 处于需复核状态，进入执行前必须人工确认。"})
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "conclusion": conclusion,
        "strategy": strategy,
        "blockers": blockers,
        "warnings": warnings,
        "info": info,
    }


def gate_conclusion(quality: dict[str, Any], risk: dict[str, Any] | None, strategy_health: dict[str, Any] | None = None) -> str:
    if quality["conclusion"] == "blocked":
        return "blocked_by_quality"
    if risk is None:
        return "blocked_by_quality"
    if risk["conclusion"] == "blocked":
        return "blocked_by_risk"
    if strategy_health and strategy_health["conclusion"] == "blocked":
        return "blocked_by_strategy_health"
    if quality["conclusion"] == "needs_review" or risk["conclusion"] == "needs_confirmation":
        return "needs_confirmation"
    if strategy_health and strategy_health["conclusion"] == "needs_review":
        return "needs_confirmation"
    return "pass"


def run_gate(
    profile_path: Path,
    plan_path: Path,
    *,
    skip_risk_when_quality_blocked: bool = True,
    strategy_health_path: Path | None = None,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    plan = load_yaml(plan_path)
    quality = check_trade_plan_quality(plan)
    risk = None
    strategy_health = None
    if not (skip_risk_when_quality_blocked and quality["conclusion"] == "blocked"):
        risk = validate_plan(profile, plan)
        strategy_health = strategy_health_for_plan(plan, load_strategy_health(strategy_health_path))

    return {
        "trade_plan_id": plan.get("trade_plan", {}).get("id"),
        "profile": str(profile_path),
        "plan": str(plan_path),
        "conclusion": gate_conclusion(quality, risk, strategy_health),
        "quality": quality,
        "risk": risk,
        "strategy_health": strategy_health,
    }


def print_text(result: dict[str, Any]) -> None:
    print(f"trade plan: {result.get('trade_plan_id') or '-'}")
    print(f"gate conclusion: {result['conclusion']}")
    print(f"quality conclusion: {result['quality']['conclusion']}")
    print(f"risk conclusion: {result['risk']['conclusion'] if result['risk'] else 'skipped'}")
    print(f"strategy health conclusion: {result['strategy_health']['conclusion'] if result['strategy_health'] else 'skipped'}")

    for title, section, key in (
        ("quality blockers", "quality", "blockers"),
        ("quality warnings", "quality", "warnings"),
        ("risk blockers", "risk", "blockers"),
        ("risk warnings", "risk", "warnings"),
        ("strategy health blockers", "strategy_health", "blockers"),
        ("strategy health warnings", "strategy_health", "warnings"),
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
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Optional strategy health JSON.")
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
            strategy_health_path=Path(args.strategy_health) if args.strategy_health else None,
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
