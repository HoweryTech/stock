#!/usr/bin/env python3
"""Create, complete, and gate-check a trade plan from a candidate row."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from tools.check_trade_plan_gate import run_gate
    from tools.complete_trade_plan import apply_completion
    from tools.new_trade_plan import create_trade_plan, write_yaml
    from tools.new_trade_plan_from_candidate import args_from_candidate, find_candidate, read_candidates
    from tools.risk_check import load_yaml
except ModuleNotFoundError:
    from check_trade_plan_gate import run_gate
    from complete_trade_plan import apply_completion
    from new_trade_plan import create_trade_plan, write_yaml
    from new_trade_plan_from_candidate import args_from_candidate, find_candidate, read_candidates
    from risk_check import load_yaml


def candidate_creation_args(args: argparse.Namespace, output_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        candidates=args.candidates,
        profile=args.profile,
        template=args.template,
        output_dir=args.output_dir,
        output=str(output_path),
        overwrite=True,
        id=args.id,
        code=args.code,
        name=args.name,
        exchange=args.exchange,
        industry=args.industry,
        strategy=args.strategy,
        timeframe=args.timeframe,
        buy_reason=args.buy_reason,
        planned_buy_price=args.planned_buy_price,
        current_price=args.current_price,
        stop_loss_price=args.stop_loss_price,
        position_pct=args.position_pct,
        current_stock_pct=args.current_stock_pct,
        current_industry_pct=args.current_industry_pct,
        current_total_pct=args.current_total_pct,
        stop_loss_condition=[],
        take_profit_condition=[],
        invalidation_condition=[],
        observation_item=[],
    )


def completion_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        status=None,
        mark_ready=args.mark_ready,
        buy_reason=None,
        key_evidence=[],
        risk=[],
        stop_loss_condition=args.stop_loss_condition,
        take_profit_condition=args.take_profit_condition,
        invalidation_condition=args.invalidation_condition,
        observation_item=args.observation_item,
        review_focus=args.review_focus,
        replace_evidence=False,
        replace_risks=False,
        replace_exit_rules=False,
        replace_observation_items=False,
        replace_review_focus=False,
        planned_buy_price=None,
        current_price=None,
        stop_loss_price=None,
        position_pct=None,
        current_stock_pct=None,
        current_industry_pct=None,
        current_total_pct=None,
    )


def build_output_path(args: argparse.Namespace) -> Path:
    if args.output:
        return Path(args.output)
    plan_id = args.id or f"TP-{args.code}"
    return Path(args.output_dir) / f"{plan_id}.yaml"


def run_prepare(args: argparse.Namespace) -> dict[str, Any]:
    output_path = build_output_path(args)
    profile = load_yaml(Path(args.profile))
    candidate = find_candidate(read_candidates(Path(args.candidates)), args.code)

    creation_args = args_from_candidate(candidate_creation_args(args, output_path), candidate)
    plan, _ = create_trade_plan(creation_args)
    plan = apply_completion(plan, profile, completion_args(args))
    write_yaml(output_path, plan, overwrite=args.overwrite)

    gate = run_gate(Path(args.profile), output_path)
    result = {
        "plan": str(output_path),
        "candidate": args.code,
        "gate": gate,
    }
    if args.gate_output:
        gate_output = Path(args.gate_output)
        gate_output.parent.mkdir(parents=True, exist_ok=True)
        gate_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def print_summary(result: dict[str, Any]) -> None:
    print(f"plan: {result['plan']}")
    print(f"candidate: {result['candidate']}")
    print(f"gate conclusion: {result['gate']['conclusion']}")
    print(f"quality conclusion: {result['gate']['quality']['conclusion']}")
    print(f"risk conclusion: {result['gate']['risk']['conclusion'] if result['gate']['risk'] else 'skipped'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create, complete, and gate-check a trade plan from a candidate row.")
    parser.add_argument("--candidates", default="data/processed/candidate_pool.csv", help="Input candidate pool CSV.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--template", default="templates/trade-plan.example.yaml", help="Path to trade plan template YAML.")
    parser.add_argument("--output-dir", default="plans", help="Directory for generated trade plans.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--gate-output", help="Optional JSON output for gate result.")
    parser.add_argument("--id", help="Explicit trade plan id.")

    parser.add_argument("--code", required=True, help="Candidate stock code.")
    parser.add_argument("--name", default="待补充", help="Stock name.")
    parser.add_argument("--exchange", default="UNKNOWN", help="Exchange, for example SSE or SZSE.")
    parser.add_argument("--industry", default="待确认", help="Industry name.")
    parser.add_argument("--strategy", help="Override strategy source.")
    parser.add_argument("--timeframe", help="Override strategy timeframe.")
    parser.add_argument("--buy-reason", help="Override initial buy reason.")

    parser.add_argument("--planned-buy-price", type=float, help="Planned buy price.")
    parser.add_argument("--current-price", type=float, help="Current price.")
    parser.add_argument("--stop-loss-price", type=float, help="Stop loss price.")
    parser.add_argument("--position-pct", type=float, help="Planned position as percent of total assets.")
    parser.add_argument("--current-stock-pct", type=float, default=0.0, help="Current stock position percent.")
    parser.add_argument("--current-industry-pct", type=float, default=0.0, help="Current industry position percent.")
    parser.add_argument("--current-total-pct", type=float, default=0.0, help="Current total position percent.")

    parser.add_argument("--stop-loss-condition", action="append", default=[], help="Stop loss condition. Can be repeated.")
    parser.add_argument("--take-profit-condition", action="append", default=[], help="Take profit condition. Can be repeated.")
    parser.add_argument("--invalidation-condition", action="append", default=[], help="Invalidation condition. Can be repeated.")
    parser.add_argument("--observation-item", action="append", default=[], help="Observation item. Can be repeated.")
    parser.add_argument("--review-focus", action="append", default=[], help="Review focus. Can be repeated.")
    parser.add_argument("--mark-ready", action="store_true", help="Set status to ready_for_gate after required exit rules are completed.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_prepare(args)
    except Exception as exc:
        print(f"prepare trade plan failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result)
    return 0 if result["gate"]["conclusion"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
