#!/usr/bin/env python3
"""Run the daily watchlist pipeline from normalized market and financial data."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.apply_candidate_portfolio_fit import apply_portfolio_fit
    from tools.calc_industry_strength import run_calculation as run_industry_strength_calculation
    from tools.calc_technical_indicators import build_report as build_technical_indicators_report
    from tools.calc_technical_indicators import render_markdown as render_technical_indicators_markdown
    from tools.calc_technical_indicators import write_json as write_technical_indicators_json
    from tools.calc_trend_factors import parse_windows, run_calculation
    from tools.check_candidate_pool import run_check as run_candidate_pool_check
    from tools.generate_watchlist_report import run_report
    from tools.merge_candidate_pool import run_merge
    from tools.risk_check import load_yaml
    from tools.screen_event_catalyst import run_screen as run_event_catalyst_screen
    from tools.screen_trend_strength import run_screen as run_trend_screen
    from tools.screen_trend_strength import trend_screening_config
    from tools.screen_value_quality import run_screen as run_value_quality_screen
except ModuleNotFoundError:
    from apply_candidate_portfolio_fit import apply_portfolio_fit
    from calc_industry_strength import run_calculation as run_industry_strength_calculation
    from calc_technical_indicators import build_report as build_technical_indicators_report
    from calc_technical_indicators import render_markdown as render_technical_indicators_markdown
    from calc_technical_indicators import write_json as write_technical_indicators_json
    from calc_trend_factors import parse_windows, run_calculation
    from check_candidate_pool import run_check as run_candidate_pool_check
    from generate_watchlist_report import run_report
    from merge_candidate_pool import run_merge
    from risk_check import load_yaml
    from screen_event_catalyst import run_screen as run_event_catalyst_screen
    from screen_trend_strength import run_screen as run_trend_screen
    from screen_trend_strength import trend_screening_config
    from screen_value_quality import run_screen as run_value_quality_screen


def resolve_windows(profile: dict[str, Any], windows_override: str | None) -> list[int]:
    if windows_override:
        return parse_windows(windows_override)
    config = trend_screening_config(profile)
    return [int(config.get("window", 20))]


def write_pipeline_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run_pipeline(
    profile_path: Path,
    daily_bars_path: Path,
    financial_metrics_path: Path,
    valuation_metrics_path: Path,
    event_catalyst_events_path: Path | None,
    universe_path: Path | None,
    windows_override: str | None,
    trend_factors_output: Path,
    trend_factors_metadata: Path,
    trend_candidates_output: Path,
    trend_candidates_metadata: Path,
    value_quality_candidates_output: Path,
    value_quality_candidates_metadata: Path,
    event_catalyst_candidates_output: Path,
    event_catalyst_candidates_metadata: Path,
    industry_strength_output: Path,
    industry_strength_metadata: Path,
    technical_indicators_output: Path,
    technical_indicators_markdown_output: Path,
    candidate_pool_output: Path,
    candidate_pool_metadata: Path,
    candidate_portfolio_fit_metadata: Path,
    report_output: Path,
    pipeline_metadata_output: Path,
    position_patterns: list[str] | None = None,
    planned_position_pct: float = 5.0,
    strategy_health_path: Path | None = None,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    windows = resolve_windows(profile, windows_override)

    trend_factors = run_calculation(daily_bars_path, universe_path, trend_factors_output, trend_factors_metadata, windows)
    trend_candidates = run_trend_screen(profile_path, trend_factors_output, trend_candidates_output, trend_candidates_metadata)
    value_quality_candidates = run_value_quality_screen(
        profile_path,
        financial_metrics_path,
        value_quality_candidates_output,
        value_quality_candidates_metadata,
        valuation_metrics_path,
    )
    event_catalyst_candidates = None
    if event_catalyst_events_path is not None:
        event_catalyst_candidates = run_event_catalyst_screen(
            profile_path,
            event_catalyst_events_path,
            event_catalyst_candidates_output,
            event_catalyst_candidates_metadata,
        )
    industry_strength = None
    if universe_path is not None:
        industry_strength = run_industry_strength_calculation(
            daily_bars_path,
            universe_path,
            industry_strength_output,
            industry_strength_metadata,
            windows,
        )
    technical_indicators = build_technical_indicators_report(daily_bars_path)
    write_technical_indicators_json(technical_indicators_output, technical_indicators)
    technical_indicators_markdown_output.parent.mkdir(parents=True, exist_ok=True)
    technical_indicators_markdown_output.write_text(render_technical_indicators_markdown(technical_indicators), encoding="utf-8")
    candidate_pool = run_merge(
        trend_candidates_output,
        value_quality_candidates_output,
        candidate_pool_output,
        candidate_pool_metadata,
        event_catalyst_candidates_output if event_catalyst_candidates is not None else None,
        universe_path,
        industry_strength_output if industry_strength is not None else None,
        technical_indicators_output,
    )
    candidate_portfolio_fit = None
    if position_patterns:
        candidate_portfolio_fit = apply_portfolio_fit(
            profile_path,
            candidate_pool_output,
            candidate_pool_output,
            candidate_portfolio_fit_metadata,
            position_patterns,
            planned_position_pct,
            strategy_health_path,
        )
    candidate_pool_check = run_candidate_pool_check(candidate_pool_output, universe_path)
    report = run_report(candidate_pool_output, report_output)

    metadata = {
        "pipeline_run_at": datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile_path),
        "inputs": {
            "daily_bars": str(daily_bars_path),
            "financial_metrics": str(financial_metrics_path),
            "valuation_metrics": str(valuation_metrics_path),
            "event_catalyst_events": str(event_catalyst_events_path) if event_catalyst_events_path else None,
            "universe": str(universe_path) if universe_path else None,
            "positions": position_patterns,
            "strategy_health": str(strategy_health_path) if strategy_health_path else None,
        },
        "windows": windows,
        "steps": {
            "trend_factors": trend_factors,
            "trend_candidates": trend_candidates,
            "value_quality_candidates": value_quality_candidates,
            "event_catalyst_candidates": event_catalyst_candidates,
            "industry_strength": industry_strength,
            "technical_indicators": {
                "output": str(technical_indicators_output),
                "markdown_output": str(technical_indicators_markdown_output),
                "code_count": technical_indicators["source"]["code_count"],
                "periods": technical_indicators["indicator_policy"]["periods"],
                "indicators": technical_indicators["indicator_policy"]["indicators"],
            },
            "candidate_pool": candidate_pool,
            "candidate_portfolio_fit": candidate_portfolio_fit,
            "candidate_pool_check": candidate_pool_check,
            "watchlist_report": report,
        },
        "outputs": {
            "trend_factors": str(trend_factors_output),
            "trend_candidates": str(trend_candidates_output),
            "value_quality_candidates": str(value_quality_candidates_output),
            "event_catalyst_candidates": str(event_catalyst_candidates_output) if event_catalyst_candidates is not None else None,
            "industry_strength": str(industry_strength_output) if industry_strength is not None else None,
            "technical_indicators": str(technical_indicators_output),
            "technical_indicators_markdown": str(technical_indicators_markdown_output),
            "candidate_pool": str(candidate_pool_output),
            "candidate_portfolio_fit": str(candidate_portfolio_fit_metadata) if candidate_portfolio_fit else None,
            "watchlist_report": str(report_output),
        },
    }
    write_pipeline_metadata(pipeline_metadata_output, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    steps = metadata["steps"]
    print(f"windows: {', '.join(str(window) for window in metadata['windows'])}")
    print(f"trend factors: {steps['trend_factors']['row_count']}")
    print(f"trend candidates: {steps['trend_candidates']['candidate_count']}")
    print(f"value quality candidates: {steps['value_quality_candidates']['candidate_count']}")
    if steps.get("event_catalyst_candidates"):
        print(f"event catalyst candidates: {steps['event_catalyst_candidates']['candidate_count']}")
    if steps.get("industry_strength"):
        print(f"industry strength factors: {steps['industry_strength']['row_count']}")
    print(f"technical indicators: {steps['technical_indicators']['code_count']}")
    print(f"candidate pool: {steps['candidate_pool']['candidate_count']}")
    if steps.get("candidate_portfolio_fit"):
        print(f"portfolio fit: {steps['candidate_portfolio_fit']['status_counts']}")
    print(f"candidate pool check: {steps['candidate_pool_check']['conclusion']}")
    print(f"watchlist report: {metadata['outputs']['watchlist_report']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily watchlist pipeline.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Input normalized daily bars CSV.")
    parser.add_argument("--financial-metrics", default="data/processed/financial_metrics.csv", help="Input normalized financial metrics CSV.")
    parser.add_argument("--valuation-metrics", default="data/processed/valuation_metrics.csv", help="Input normalized valuation metrics CSV.")
    parser.add_argument("--event-catalyst-events", help="Optional structured event CSV.")
    parser.add_argument("--universe", help="Optional tradable universe CSV.")
    parser.add_argument("--windows", help="Optional comma-separated trend factor windows. Defaults to the trend screening window.")
    parser.add_argument("--trend-factors-output", default="data/processed/trend_factors.csv", help="Output trend factors CSV.")
    parser.add_argument("--trend-factors-metadata", default="data/metadata/trend_factors.json", help="Output trend factor metadata JSON.")
    parser.add_argument("--trend-candidates-output", default="data/processed/trend_candidates.csv", help="Output trend candidates CSV.")
    parser.add_argument("--trend-candidates-metadata", default="data/metadata/trend_candidates.json", help="Output trend candidate metadata JSON.")
    parser.add_argument(
        "--value-quality-candidates-output",
        default="data/processed/value_quality_candidates.csv",
        help="Output value quality candidates CSV.",
    )
    parser.add_argument(
        "--value-quality-candidates-metadata",
        default="data/metadata/value_quality_candidates.json",
        help="Output value quality candidate metadata JSON.",
    )
    parser.add_argument("--event-catalyst-candidates-output", default="data/processed/event_catalyst_candidates.csv", help="Output event catalyst candidate CSV.")
    parser.add_argument("--event-catalyst-candidates-metadata", default="data/metadata/event_catalyst_candidates.json", help="Output event catalyst candidate metadata JSON.")
    parser.add_argument("--industry-strength-output", default="data/processed/industry_strength_factors.csv", help="Output industry strength factor CSV.")
    parser.add_argument("--industry-strength-metadata", default="data/metadata/industry_strength_factors.json", help="Output industry strength metadata JSON.")
    parser.add_argument("--technical-indicators-output", default="data/metadata/technical-indicators.json", help="Output multi-period technical indicators JSON.")
    parser.add_argument("--technical-indicators-markdown-output", default="reports/technical-indicators.md", help="Output multi-period technical indicators Markdown.")
    parser.add_argument("--candidate-pool-output", default="data/processed/candidate_pool.csv", help="Output candidate pool CSV.")
    parser.add_argument("--candidate-pool-metadata", default="data/metadata/candidate_pool.json", help="Output candidate pool metadata JSON.")
    parser.add_argument("--candidate-portfolio-fit-metadata", default="data/metadata/candidate_portfolio_fit.json", help="Output candidate portfolio fit metadata JSON.")
    parser.add_argument("--positions", nargs="+", help="Optional position YAML paths or glob patterns for portfolio fit.")
    parser.add_argument("--planned-position-pct", type=float, default=5.0, help="Assumed new position percent for portfolio fit.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Optional strategy health JSON for portfolio fit.")
    parser.add_argument("--report-output", default="reports/watchlist.md", help="Output watchlist report Markdown.")
    parser.add_argument("--metadata-output", default="data/metadata/watchlist_pipeline.json", help="Output pipeline metadata JSON.")
    parser.add_argument("--json", action="store_true", help="Print pipeline metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_pipeline(
            Path(args.profile),
            Path(args.daily_bars),
            Path(args.financial_metrics),
            Path(args.valuation_metrics),
            Path(args.event_catalyst_events) if args.event_catalyst_events else None,
            Path(args.universe) if args.universe else None,
            args.windows,
            Path(args.trend_factors_output),
            Path(args.trend_factors_metadata),
            Path(args.trend_candidates_output),
            Path(args.trend_candidates_metadata),
            Path(args.value_quality_candidates_output),
            Path(args.value_quality_candidates_metadata),
            Path(args.event_catalyst_candidates_output),
            Path(args.event_catalyst_candidates_metadata),
            Path(args.industry_strength_output),
            Path(args.industry_strength_metadata),
            Path(args.technical_indicators_output),
            Path(args.technical_indicators_markdown_output),
            Path(args.candidate_pool_output),
            Path(args.candidate_pool_metadata),
            Path(args.candidate_portfolio_fit_metadata),
            Path(args.report_output),
            Path(args.metadata_output),
            args.positions,
            args.planned_position_pct,
            Path(args.strategy_health) if args.strategy_health else None,
        )
    except Exception as exc:
        print(f"watchlist pipeline failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
