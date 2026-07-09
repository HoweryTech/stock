#!/usr/bin/env python3
"""Run the daily integrity checks and operating summary as one pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_execution_loop import build_loop_check, render_loop_check
    from tools.generate_daily_summary import build_summary, render_summary
except ModuleNotFoundError:
    from check_execution_loop import build_loop_check, render_loop_check
    from generate_daily_summary import build_summary, render_summary


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_loop_args(args: argparse.Namespace) -> Namespace:
    return Namespace(
        trade_executions=args.trade_executions,
        exit_executions=args.exit_executions,
        positions=args.positions,
        reviews=args.reviews,
        output=args.execution_loop_output,
        json_output=args.execution_loop_json_output,
        json=False,
    )


def build_summary_args(args: argparse.Namespace) -> Namespace:
    return Namespace(
        watchlist_metadata=args.watchlist_metadata,
        portfolio_check=args.portfolio_check,
        exit_plans=args.exit_plans,
        trade_executions=args.trade_executions,
        exit_executions=args.exit_executions,
        reviews=args.reviews,
        review_analysis=args.review_analysis,
        execution_loop_check=args.execution_loop_json_output,
        cooldown_check=args.cooldown_check,
        strategy_health=args.strategy_health,
        strategy_review_tasks=args.strategy_review_tasks,
        strategy_config_changes=args.strategy_config_changes,
        strategy_config_patch=args.strategy_config_patch,
        strategy_config_patch_audit=args.strategy_config_patch_audit,
        strategy_config_regression=args.strategy_config_regression,
        strategy_config_pipeline=args.strategy_config_pipeline,
        strategy_config_snapshot=args.strategy_config_snapshot,
        manual_confirmations=args.manual_confirmations,
        output=args.daily_summary_output,
        json_output=args.daily_summary_json_output,
        json=False,
    )


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    loop_result = build_loop_check(build_loop_args(args))
    write_text(Path(args.execution_loop_output), render_loop_check(loop_result))
    write_json(Path(args.execution_loop_json_output), loop_result)

    daily_summary = build_summary(build_summary_args(args))
    write_text(Path(args.daily_summary_output), render_summary(daily_summary))
    write_json(Path(args.daily_summary_json_output), daily_summary)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": {
            "execution_loop_check": {
                "output": args.execution_loop_output,
                "json_output": args.execution_loop_json_output,
                "conclusion": loop_result["conclusion"],
                "blocked_count": loop_result["blocked_count"],
                "needs_review_count": loop_result["needs_review_count"],
                "downstream_gap_count": loop_result["downstream_gap_count"],
            },
            "daily_summary": {
                "output": args.daily_summary_output,
                "json_output": args.daily_summary_json_output,
                "action_count": len(daily_summary["operating_actions"]),
                "manual_confirmation_count": len(daily_summary["manual_confirmation_items"]),
            },
        },
    }
    write_json(Path(args.metadata_output), metadata)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily integrity checks and operating summary.")
    parser.add_argument("--watchlist-metadata", default="data/metadata/watchlist_pipeline.json", help="Watchlist pipeline metadata JSON.")
    parser.add_argument("--portfolio-check", default="data/metadata/portfolio_positions.check.json", help="Portfolio position check JSON.")
    parser.add_argument("--exit-plans", nargs="+", default=["exit-plans/*.yaml"], help="Exit plan YAML paths or glob patterns.")
    parser.add_argument("--trade-executions", nargs="+", default=["executions/*.yaml"], help="Trade execution YAML paths or glob patterns.")
    parser.add_argument("--exit-executions", nargs="+", default=["exit-executions/*.yaml"], help="Sell execution YAML paths or glob patterns.")
    parser.add_argument("--positions", nargs="+", default=["positions/*.yaml"], help="Position YAML paths or glob patterns.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--review-analysis", default="data/metadata/review-analysis.json", help="Review analysis JSON.")
    parser.add_argument("--cooldown-check", default="data/metadata/review-cooldown.json", help="Review cooldown JSON.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Strategy health JSON.")
    parser.add_argument("--strategy-review-tasks", default="data/metadata/strategy-review-tasks.json", help="Strategy review task JSON.")
    parser.add_argument("--strategy-config-changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change draft JSON.")
    parser.add_argument("--strategy-config-patch", default="data/metadata/strategy-config-patch.json", help="Strategy config patch JSON.")
    parser.add_argument("--strategy-config-patch-audit", default="data/metadata/strategy-config-patch.apply.json", help="Strategy config patch apply audit JSON.")
    parser.add_argument("--strategy-config-regression", default="data/metadata/strategy-config-regression.json", help="Strategy config regression JSON.")
    parser.add_argument("--strategy-config-pipeline", default="data/metadata/strategy-config-change-pipeline.json", help="Strategy config change pipeline metadata JSON.")
    parser.add_argument("--strategy-config-snapshot", default="data/metadata/strategy-config-snapshot.json", help="Strategy config version snapshot JSON.")
    parser.add_argument("--manual-confirmations", default="data/metadata/manual-confirmations.json", help="Manual confirmation record JSON.")
    parser.add_argument("--execution-loop-output", default="reports/execution-loop-check.md", help="Execution loop Markdown output.")
    parser.add_argument("--execution-loop-json-output", default="data/metadata/execution-loop-check.json", help="Execution loop JSON output.")
    parser.add_argument("--daily-summary-output", default="reports/daily-summary.md", help="Daily summary Markdown output.")
    parser.add_argument("--daily-summary-json-output", default="data/metadata/daily-summary.json", help="Daily summary JSON output.")
    parser.add_argument("--metadata-output", default="data/metadata/daily-check-pipeline.json", help="Daily check pipeline metadata JSON.")
    parser.add_argument("--json", action="store_true", help="Print pipeline metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_pipeline(args)
    except Exception as exc:
        print(f"daily check pipeline failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        loop = metadata["steps"]["execution_loop_check"]
        summary = metadata["steps"]["daily_summary"]
        print(f"execution loop conclusion: {loop['conclusion']}")
        print(f"execution loop output: {loop['output']}")
        print(f"daily summary output: {summary['output']}")
        print(f"metadata: {args.metadata_output}")
    return 1 if metadata["steps"]["execution_loop_check"]["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
