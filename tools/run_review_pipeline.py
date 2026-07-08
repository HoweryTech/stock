#!/usr/bin/env python3
"""Run review analysis and cooldown checks as one maintenance pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.analyze_trade_reviews import analyze_reviews, expand_paths, render_analysis, write_text
    from tools.check_review_cooldown import check_cooldown
    from tools.risk_check import load_yaml
except ModuleNotFoundError:
    from analyze_trade_reviews import analyze_reviews, expand_paths, render_analysis, write_text
    from check_review_cooldown import check_cooldown
    from risk_check import load_yaml


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    review_paths = expand_paths(args.reviews)
    analysis = analyze_reviews(review_paths)
    cooldown = check_cooldown(load_yaml(Path(args.profile)), review_paths)

    write_text(Path(args.analysis_output), render_analysis(analysis))
    write_json(Path(args.analysis_json_output), analysis)
    write_json(Path(args.cooldown_output), cooldown)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reviews": [str(path) for path in review_paths],
        "review_count": len(review_paths),
        "steps": {
            "review_analysis": {
                "output": args.analysis_output,
                "json_output": args.analysis_json_output,
                "review_count": analysis["review_count"],
                "total_portfolio_return_pct": analysis["overall"]["total_portfolio_return_pct"],
            },
            "cooldown_check": {
                "output": args.cooldown_output,
                "conclusion": cooldown["conclusion"],
                "overall_losing_streak": cooldown["overall_losing_streak"],
            },
        },
    }
    write_json(Path(args.metadata_output), metadata)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run review analysis and cooldown checks.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--analysis-output", default="reports/review-analysis.md", help="Output Markdown review analysis.")
    parser.add_argument("--analysis-json-output", default="data/metadata/review-analysis.json", help="Output JSON review analysis.")
    parser.add_argument("--cooldown-output", default="data/metadata/review-cooldown.json", help="Output cooldown JSON.")
    parser.add_argument("--metadata-output", default="data/metadata/review-pipeline.json", help="Output pipeline metadata JSON.")
    parser.add_argument("--json", action="store_true", help="Print pipeline metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_pipeline(args)
    except Exception as exc:
        print(f"review pipeline failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"review count: {metadata['review_count']}")
        print(f"review analysis: {metadata['steps']['review_analysis']['output']}")
        print(f"cooldown conclusion: {metadata['steps']['cooldown_check']['conclusion']}")
        print(f"metadata: {args.metadata_output}")
    return 1 if metadata["steps"]["cooldown_check"]["conclusion"] == "cooldown_required" else 0


if __name__ == "__main__":
    raise SystemExit(main())
