#!/usr/bin/env python3
"""Refresh intraday holding artifacts and decision cards in one command."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.build_data_quality_snapshot import build_report as build_data_quality_report
    from tools.build_data_quality_snapshot import render_markdown as render_data_quality_markdown
    from tools.build_realtime_decision_cards import build_report as build_decision_cards_report
    from tools.build_realtime_decision_cards import render_markdown as render_decision_cards_markdown
    from tools.check_portfolio_positions import expand_position_paths, summarize_positions
    from tools.check_portfolio_t_opportunities import check_portfolio_t_opportunities
    from tools.fetch_daily_bars_sina import fetch_daily_bars
    from tools.monitor_intraday_positions import build_snapshot, render_markdown as render_intraday_markdown
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from build_data_quality_snapshot import build_report as build_data_quality_report
    from build_data_quality_snapshot import render_markdown as render_data_quality_markdown
    from build_realtime_decision_cards import build_report as build_decision_cards_report
    from build_realtime_decision_cards import render_markdown as render_decision_cards_markdown
    from check_portfolio_positions import expand_position_paths, summarize_positions
    from check_portfolio_t_opportunities import check_portfolio_t_opportunities
    from fetch_daily_bars_sina import fetch_daily_bars
    from monitor_intraday_positions import build_snapshot, render_markdown as render_intraday_markdown
    from risk_check import as_float, load_yaml, value_at


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def cost_model(args: argparse.Namespace, profile: dict[str, Any]) -> dict[str, Any]:
    minimum_net_profit = as_float(value_at(profile, "t_trading.minimum_net_profit_cny"), args.minimum_net_profit) or args.minimum_net_profit
    return {
        "commission_rate": args.commission_rate,
        "minimum_commission": args.minimum_commission,
        "stamp_duty_rate": args.stamp_duty_rate,
        "transfer_fee_rate": args.transfer_fee_rate,
        "minimum_net_profit": minimum_net_profit,
        "verified": bool(args.cost_model_verified),
    }


def pipeline_settings(args: argparse.Namespace, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "max_position_pct": as_float(value_at(profile, "risk.max_position_pct_per_stock"), args.max_position_pct) or args.max_position_pct,
        "warning_position_pct": as_float(value_at(profile, "risk.warning_position_pct_per_stock"), args.warning_position_pct),
        "position_limit_verified": bool(value_at(profile, "risk.position_limits_confirmed")) or bool(args.position_limit_verified),
        "max_reverse_t_position_ratio_pct": as_float(value_at(profile, "t_trading.max_position_ratio_pct_per_trade"), args.max_reverse_t_position_ratio) or args.max_reverse_t_position_ratio,
        "costs": cost_model(args, profile),
    }


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    position_paths = expand_position_paths(args.positions)
    profile = load_yaml(Path(args.profile))
    settings = pipeline_settings(args, profile)
    codes = [str(value_at(load_yaml(path), "stock.code") or "") for path in position_paths]
    codes = [code for code in dict.fromkeys(codes) if code]

    daily_refresh: dict[str, Any] | None = None
    if not args.skip_daily_refresh:
        daily_refresh = fetch_daily_bars(codes, Path(args.daily_bars), datalen=args.daily_fetch_datalen, merge_existing=True)
        write_json(Path(args.daily_fetch_metadata_output), daily_refresh)

    intraday_snapshot = build_snapshot(
        position_paths,
        Path(args.daily_bars),
        total_assets=args.total_assets,
        max_stale_seconds=args.max_stale_seconds,
        costs=settings["costs"],
        max_reverse_t_position_ratio_pct=settings["max_reverse_t_position_ratio_pct"],
        max_position_pct=settings["max_position_pct"],
        warning_position_pct=settings["warning_position_pct"],
        position_limit_verified=settings["position_limit_verified"],
    )
    write_json(Path(args.intraday_output), intraday_snapshot)
    write_text(Path(args.intraday_markdown_output), render_intraday_markdown(intraday_snapshot))

    portfolio_check = summarize_positions(profile, position_paths, near_stop_pct=args.position_near_stop_pct)
    write_json(Path(args.portfolio_check_output), portfolio_check)

    t_opportunities = check_portfolio_t_opportunities(
        profile,
        position_paths,
        Path(args.daily_bars),
        short_window=args.short_window,
        mid_window=args.mid_window,
        near_stop_pct=args.t_near_stop_pct,
        pullback_pct=args.pullback_pct,
        overextended_pct=args.overextended_pct,
        min_spread_pct=args.min_spread_pct,
    )
    write_json(Path(args.t_opportunities_output), t_opportunities)

    data_quality = build_data_quality_report(
        position_paths,
        intraday_snapshot,
        Path(args.daily_bars),
        Path(args.minute_cache_dir),
        max_quote_lag_seconds=args.max_quote_lag_seconds,
        min_daily_bars=args.min_daily_bars,
        max_daily_age_days=args.max_daily_age_days,
        min_minute_bars=args.min_minute_bars,
        max_minute_age_hours=args.max_minute_age_hours,
        max_consistency_diff_pct=args.max_consistency_diff_pct,
    )
    write_json(Path(args.data_quality_output), data_quality)
    write_text(Path(args.data_quality_markdown_output), render_data_quality_markdown(data_quality))

    action_backtests = None
    action_backtests_path = Path(args.action_backtests)
    if action_backtests_path.exists():
        action_backtests = json.loads(action_backtests_path.read_text(encoding="utf-8"))
    reverse_t_backtest = None
    reverse_t_backtest_path = Path(args.reverse_t_backtest)
    if reverse_t_backtest_path.exists():
        reverse_t_backtest = json.loads(reverse_t_backtest_path.read_text(encoding="utf-8"))
    reverse_t_forecast = None
    reverse_t_forecast_path = Path(args.reverse_t_forecast)
    if reverse_t_forecast_path.exists():
        reverse_t_forecast = json.loads(reverse_t_forecast_path.read_text(encoding="utf-8"))
    technical_indicators = None
    technical_indicators_path = Path(args.technical_indicators)
    if technical_indicators_path.exists():
        technical_indicators = json.loads(technical_indicators_path.read_text(encoding="utf-8"))

    decision_cards = build_decision_cards_report(
        intraday_snapshot,
        portfolio_check,
        t_opportunities,
        action_backtests,
        reverse_t_backtest,
        reverse_t_forecast,
        data_quality,
        technical_indicators,
    )
    write_json(Path(args.decision_cards_output), decision_cards)
    write_text(Path(args.decision_cards_markdown_output), render_decision_cards_markdown(decision_cards))

    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "position_count": len(position_paths),
        "steps": {
            "intraday_snapshot": {
                "output": args.intraday_output,
                "markdown_output": args.intraday_markdown_output,
                "success_count": intraday_snapshot.get("success_count", 0),
                "error_count": len(intraday_snapshot.get("errors", []) or []),
            },
            "daily_refresh": {
                "enabled": not args.skip_daily_refresh,
                "output": args.daily_fetch_metadata_output,
                "requested_code_count": 0 if daily_refresh is None else daily_refresh.get("requested_code_count", 0),
                "fetched_row_count": 0 if daily_refresh is None else daily_refresh.get("fetched_row_count", 0),
                "start_date": None if daily_refresh is None else daily_refresh.get("start_date"),
                "end_date": None if daily_refresh is None else daily_refresh.get("end_date"),
                "error_count": 0 if daily_refresh is None else len(daily_refresh.get("errors", []) or []),
            },
            "portfolio_check": {
                "output": args.portfolio_check_output,
                "conclusion": portfolio_check.get("conclusion"),
                "warning_count": portfolio_check.get("warning_count", 0),
                "needs_action_count": portfolio_check.get("needs_action_count", 0),
            },
            "t_opportunities": {
                "output": args.t_opportunities_output,
                "execution_conclusion_counts": t_opportunities.get("execution_conclusion_counts", {}),
            },
            "data_quality": {
                "output": args.data_quality_output,
                "markdown_output": args.data_quality_markdown_output,
                "usable_count": data_quality.get("usable_count", 0),
                "status_counts": data_quality.get("status_counts", {}),
            },
            "decision_cards": {
                "output": args.decision_cards_output,
                "markdown_output": args.decision_cards_markdown_output,
                "card_count": decision_cards.get("card_count", 0),
                "state_counts": decision_cards.get("state_counts", {}),
            },
        },
        "source": {
            "positions": args.positions,
            "profile": args.profile,
            "daily_bars": args.daily_bars,
            "minute_cache_dir": args.minute_cache_dir,
            "action_backtests": args.action_backtests,
            "reverse_t_backtest": args.reverse_t_backtest,
            "reverse_t_forecast": args.reverse_t_forecast,
            "technical_indicators": args.technical_indicators,
        },
    }
    write_json(Path(args.metadata_output), metadata)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh intraday monitor, risk checks, T checks and realtime decision cards once.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--profile", default="config/investment-profile.yaml")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--skip-daily-refresh", action="store_true", help="Use existing daily bar cache without refreshing from Sina.")
    parser.add_argument("--daily-fetch-datalen", type=int, default=120)
    parser.add_argument("--daily-fetch-metadata-output", default="data/metadata/daily_bars.fetch.json")
    parser.add_argument("--total-assets", type=float, required=True)
    parser.add_argument("--max-stale-seconds", type=int, default=60)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--minimum-commission", type=float, default=5.0)
    parser.add_argument("--stamp-duty-rate", type=float, default=0.0005)
    parser.add_argument("--transfer-fee-rate", type=float, default=0.00001)
    parser.add_argument("--minimum-net-profit", type=float, default=5.0)
    parser.add_argument("--cost-model-verified", action="store_true")
    parser.add_argument("--max-reverse-t-position-ratio", type=float, default=50.0)
    parser.add_argument("--max-position-pct", type=float, default=10.0)
    parser.add_argument("--warning-position-pct", type=float)
    parser.add_argument("--position-limit-verified", action="store_true")
    parser.add_argument("--position-near-stop-pct", type=float, help="Override risk.near_stop_warning_pct for portfolio checks.")
    parser.add_argument("--t-near-stop-pct", type=float, help="Override t_trading.near_stop_block_pct for T checks.")
    parser.add_argument("--short-window", type=int, default=5)
    parser.add_argument("--mid-window", type=int, default=20)
    parser.add_argument("--pullback-pct", type=float, default=3.0)
    parser.add_argument("--overextended-pct", type=float, default=6.0)
    parser.add_argument("--min-spread-pct", type=float, default=1.2)
    parser.add_argument("--minute-cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--max-quote-lag-seconds", type=float, default=60.0)
    parser.add_argument("--min-daily-bars", type=int, default=20)
    parser.add_argument("--max-daily-age-days", type=int, default=5)
    parser.add_argument("--min-minute-bars", type=int, default=120)
    parser.add_argument("--max-minute-age-hours", type=float, default=30.0)
    parser.add_argument("--max-consistency-diff-pct", type=float, default=1.0)
    parser.add_argument("--action-backtests", default="data/metadata/portfolio-action-matrix-backtests.after-plan.json")
    parser.add_argument("--reverse-t-backtest", default="data/metadata/reverse-t-backtest.json")
    parser.add_argument("--reverse-t-forecast", default="data/metadata/reverse-t-forecast.json")
    parser.add_argument("--technical-indicators", default="data/metadata/technical-indicators.json")
    parser.add_argument("--intraday-output", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--intraday-markdown-output", default="reports/intraday-monitor.latest.md")
    parser.add_argument("--portfolio-check-output", default="data/metadata/eastmoney-portfolio-check.after-threshold.json")
    parser.add_argument("--t-opportunities-output", default="data/metadata/eastmoney-portfolio-t-opportunities.near-config.json")
    parser.add_argument("--data-quality-output", default="data/metadata/data-quality-snapshot.json")
    parser.add_argument("--data-quality-markdown-output", default="reports/data-quality-snapshot.md")
    parser.add_argument("--decision-cards-output", default="data/metadata/realtime-decision-cards.json")
    parser.add_argument("--decision-cards-markdown-output", default="reports/realtime-decision-cards.md")
    parser.add_argument("--metadata-output", default="data/metadata/intraday-decision-pipeline.json")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_pipeline(args)
    except Exception as exc:
        print(f"intraday decision pipeline failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        cards = metadata["steps"]["decision_cards"]
        intraday = metadata["steps"]["intraday_snapshot"]
        print(f"intraday: {intraday['success_count']}/{metadata['position_count']} success")
        print(f"decision cards: {cards['card_count']}, states: {cards['state_counts']}")
        print(f"metadata: {args.metadata_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
