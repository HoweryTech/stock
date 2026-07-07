#!/usr/bin/env python3
"""Create a draft trade plan from a candidate pool row."""

from __future__ import annotations

import argparse
import csv
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

try:
    from tools.new_trade_plan import create_trade_plan, write_yaml
    from tools.generate_watchlist_report import split_text
except ModuleNotFoundError:
    from new_trade_plan import create_trade_plan, write_yaml
    from generate_watchlist_report import split_text


def read_candidates(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def find_candidate(candidates: list[dict[str, str]], code: str) -> dict[str, str]:
    for candidate in candidates:
        if candidate.get("code") == code:
            return candidate
    raise ValueError(f"candidate {code} not found")


def infer_strategy(candidate: dict[str, str], explicit_strategy: str | None = None) -> str:
    if explicit_strategy:
        return explicit_strategy
    primary_strategy = candidate.get("primary_strategy", "")
    if primary_strategy and primary_strategy != "multi_strategy":
        return primary_strategy
    strategies = split_text(candidate.get("strategies", ""))
    return strategies[0] if strategies else "trend_strength"


def infer_timeframe(strategy: str) -> str:
    return "mid_term" if strategy == "value_quality" else "swing"


def candidate_evidence(candidate: dict[str, str]) -> list[str]:
    return split_text(candidate.get("reasons", ""))


def candidate_risks(candidate: dict[str, str]) -> list[str]:
    risks = split_text(candidate.get("risks", ""))
    if risks:
        return risks
    return ["观察池未输出显式风险，但仍需人工补充估值、公告、市场环境、止损和仓位风险。"]


def observation_items(candidate: dict[str, str]) -> list[str]:
    items = [
        "确认候选池证据是否仍然有效。",
        "补充估值、公告、行业景气和市场环境检查。",
        "明确买入价、止损价、仓位和失效条件后再提交风控校验。",
    ]
    if candidate.get("primary_strategy") == "multi_strategy":
        items.append("确认多策略证据是否互相支持，而不是互相冲突。")
    return items


def args_from_candidate(args: argparse.Namespace, candidate: dict[str, str]) -> Namespace:
    strategy = infer_strategy(candidate, args.strategy)
    return Namespace(
        profile=args.profile,
        template=args.template,
        output_dir=args.output_dir,
        output=args.output,
        overwrite=args.overwrite,
        id=args.id,
        code=args.code,
        name=args.name,
        exchange=args.exchange,
        industry=args.industry,
        is_st=False,
        is_suspended=False,
        has_delisting_risk=False,
        abnormal_trading_status=False,
        strategy=strategy,
        timeframe=args.timeframe or infer_timeframe(strategy),
        buy_reason=args.buy_reason or f"来自观察池候选，主策略：{candidate.get('primary_strategy') or strategy}。",
        key_evidence=candidate_evidence(candidate),
        risk=candidate_risks(candidate),
        stop_loss_condition=args.stop_loss_condition,
        take_profit_condition=args.take_profit_condition,
        invalidation_condition=args.invalidation_condition,
        observation_item=observation_items(candidate) + args.observation_item,
        planned_buy_price=args.planned_buy_price,
        current_price=args.current_price,
        stop_loss_price=args.stop_loss_price,
        position_pct=args.position_pct,
        current_stock_pct=args.current_stock_pct,
        current_industry_pct=args.current_industry_pct,
        current_total_pct=args.current_total_pct,
    )


def create_plan_from_candidate(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    candidate = find_candidate(read_candidates(Path(args.candidates)), args.code)
    return create_trade_plan(args_from_candidate(args, candidate))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a draft trade plan from a candidate pool row.")
    parser.add_argument("--candidates", default="data/processed/candidate_pool.csv", help="Input candidate pool CSV.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--template", default="templates/trade-plan.example.yaml", help="Path to trade plan template YAML.")
    parser.add_argument("--output-dir", default="plans", help="Directory for generated trade plans.")
    parser.add_argument("--output", help="Explicit output file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists.")
    parser.add_argument("--id", help="Explicit trade plan id. Defaults to TP-YYYYMMDD-HHMMSS.")

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
    parser.add_argument("--observation-item", action="append", default=[], help="Extra observation item. Can be repeated.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan, output_path = create_plan_from_candidate(args)
        write_yaml(output_path, plan, overwrite=args.overwrite)
    except Exception as exc:
        print(f"create trade plan from candidate failed: {exc}", file=sys.stderr)
        return 2

    print(f"created trade plan: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
