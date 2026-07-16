#!/usr/bin/env python3
"""Plan or execute data repairs for intraday decision quality."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from tools.backtest_reverse_t import fetch_minute_bars, fetch_sina_minute_bars
    from tools.build_data_quality_snapshot import build_report as build_quality_report
    from tools.build_data_quality_snapshot import render_markdown as render_quality_markdown
    from tools.check_portfolio_positions import expand_position_paths
    from tools.fetch_daily_bars_sina import fetch_daily_bars
    from tools.monitor_intraday_positions import build_snapshot, render_markdown as render_intraday_markdown
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from backtest_reverse_t import fetch_minute_bars, fetch_sina_minute_bars
    from build_data_quality_snapshot import build_report as build_quality_report
    from build_data_quality_snapshot import render_markdown as render_quality_markdown
    from check_portfolio_positions import expand_position_paths
    from fetch_daily_bars_sina import fetch_daily_bars
    from monitor_intraday_positions import build_snapshot, render_markdown as render_intraday_markdown
    from risk_check import as_float, load_yaml, value_at


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def position_codes(position_paths: list[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in position_paths:
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        if code:
            result[code] = path
    return result


def needs_repair(section: dict[str, Any]) -> bool:
    return section.get("status") in {"missing", "insufficient", "stale"}


def build_plan(quality_report: dict[str, Any], position_paths: list[Path]) -> dict[str, Any]:
    paths_by_code = position_codes(position_paths)
    daily_codes: list[str] = []
    minute_codes: list[str] = []
    quote_codes: list[str] = []
    actions: list[dict[str, Any]] = []
    for item in quality_report.get("items", []):
        code = str(item.get("code") or "")
        if not code:
            continue
        if needs_repair(item.get("quote") or {}):
            quote_codes.append(code)
        if needs_repair(item.get("daily") or {}):
            daily_codes.append(code)
        if needs_repair(item.get("minute") or {}):
            minute_codes.append(code)
    if quote_codes:
        actions.append(
            {
                "type": "refresh_intraday_snapshot",
                "codes": sorted(set(quote_codes)),
                "reason": "准实时行情缺失或过期，需要刷新完整持仓行情快照。",
                "requires_total_assets": True,
            }
        )
    if daily_codes:
        actions.append(
            {
                "type": "fetch_daily_bars",
                "codes": sorted(set(daily_codes)),
                "reason": "日线缺失、样本不足或过期，需要拉取新浪日线并合并到标准日线文件。",
            }
        )
    if minute_codes:
        actions.append(
            {
                "type": "refresh_minute_cache",
                "codes": sorted(set(minute_codes)),
                "reason": "分钟线缓存缺失、样本不足或过期，需要刷新5分钟线缓存。",
                "position_paths": [str(paths_by_code[code]) for code in sorted(set(minute_codes)) if code in paths_by_code],
            }
        )
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_quality_generated_at": quality_report.get("generated_at"),
        "position_count": quality_report.get("position_count", 0),
        "quality_status_counts": quality_report.get("status_counts", {}),
        "action_count": len(actions),
        "actions": actions,
    }


def refresh_intraday_snapshot(args: argparse.Namespace, position_paths: list[Path]) -> dict[str, Any]:
    if args.total_assets is None:
        raise ValueError("--total-assets is required to refresh intraday snapshot")
    profile = load_yaml(Path(args.profile)) if Path(args.profile).exists() else {}
    minimum_net_profit = as_float(value_at(profile, "t_trading.minimum_net_profit_cny"), args.minimum_net_profit) or args.minimum_net_profit
    costs = {
        "commission_rate": args.commission_rate,
        "minimum_commission": args.minimum_commission,
        "stamp_duty_rate": args.stamp_duty_rate,
        "transfer_fee_rate": args.transfer_fee_rate,
        "minimum_net_profit": minimum_net_profit,
        "verified": bool(args.cost_model_verified),
    }
    snapshot = build_snapshot(
        position_paths,
        Path(args.daily_bars),
        total_assets=args.total_assets,
        max_stale_seconds=args.max_stale_seconds,
        costs=costs,
        max_reverse_t_position_ratio_pct=args.max_reverse_t_position_ratio,
        max_position_pct=args.max_position_pct,
        warning_position_pct=args.warning_position_pct,
        position_limit_verified=args.position_limit_verified,
    )
    write_json(Path(args.intraday_output), snapshot)
    write_text(Path(args.intraday_markdown_output), render_intraday_markdown(snapshot))
    return {
        "output": args.intraday_output,
        "markdown_output": args.intraday_markdown_output,
        "success_count": snapshot.get("success_count", 0),
        "error_count": len(snapshot.get("errors", []) or []),
    }


def refresh_daily_bars(codes: list[str], args: argparse.Namespace) -> dict[str, Any]:
    result = fetch_daily_bars(codes, Path(args.daily_bars), datalen=args.fetch_datalen, merge_existing=True)
    write_json(Path(args.daily_metadata_output), result)
    return result


def fetch_minute_cache_for_code(code: str, cache_dir: Path, begin: str, end: str) -> dict[str, Any]:
    try:
        bars = fetch_sina_minute_bars(code)
        source = "sina_5minute"
        name = code
    except Exception:
        name, bars = fetch_minute_bars(code, begin, end)
        source = "eastmoney_5minute"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{code}.json"
    cache_path.write_text(json.dumps({"name": name, "bars": bars}, ensure_ascii=False), encoding="utf-8")
    latest = max((str(bar.get("timestamp") or "") for bar in bars), default="")
    return {"code": code, "source": source, "bar_count": len(bars), "latest_timestamp": latest, "cache_path": str(cache_path)}


def refresh_minute_cache(codes: list[str], args: argparse.Namespace) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    cache_dir = Path(args.minute_cache_dir)
    for code in codes:
        try:
            items.append(fetch_minute_cache_for_code(code, cache_dir, args.minute_begin, args.minute_end))
            if args.request_interval_seconds > 0:
                time.sleep(args.request_interval_seconds)
        except Exception as exc:
            errors.append({"code": code, "message": str(exc)})
    return {"items": items, "errors": errors, "cache_dir": str(cache_dir)}


def rebuild_quality(args: argparse.Namespace, position_paths: list[Path]) -> dict[str, Any]:
    intraday_snapshot = load_json_if_exists(Path(args.intraday_output))
    report = build_quality_report(
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
    write_json(Path(args.quality_output), report)
    write_text(Path(args.quality_markdown_output), render_quality_markdown(report))
    return report


def execute_plan(plan: dict[str, Any], args: argparse.Namespace, position_paths: list[Path]) -> dict[str, Any]:
    executed: list[dict[str, Any]] = []
    for action in plan["actions"]:
        action_type = action["type"]
        codes = action.get("codes") or []
        if action_type == "refresh_intraday_snapshot":
            result = refresh_intraday_snapshot(args, position_paths)
        elif action_type == "fetch_daily_bars":
            result = refresh_daily_bars(codes, args)
        elif action_type == "refresh_minute_cache":
            result = refresh_minute_cache(codes, args)
        else:
            result = {"error": f"unknown action type: {action_type}"}
        executed.append({"type": action_type, "codes": codes, "result": result})
    refreshed_quality = rebuild_quality(args, position_paths)
    return {
        "executed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "executed_actions": executed,
        "refreshed_quality": {
            "output": args.quality_output,
            "markdown_output": args.quality_markdown_output,
            "usable_count": refreshed_quality.get("usable_count", 0),
            "status_counts": refreshed_quality.get("status_counts", {}),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 数据质量修复计划",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        f"持仓数：{report['plan']['position_count']}，待执行动作：{report['plan']['action_count']}，质量分布：{report['plan']['quality_status_counts']}",
        "",
    ]
    if not report["plan"]["actions"]:
        lines.append("当前没有需要自动补数据的项目。")
    else:
        lines.extend(["| 动作 | 股票 | 原因 |", "| --- | --- | --- |"])
        for action in report["plan"]["actions"]:
            lines.append(f"| {action['type']} | {', '.join(action.get('codes') or [])} | {action['reason']} |")
    if report.get("execution"):
        lines.extend(["", "## 执行结果", ""])
        quality = report["execution"]["refreshed_quality"]
        lines.append(f"- 修复后可用：{quality['usable_count']}，状态分布：{quality['status_counts']}")
        for action in report["execution"]["executed_actions"]:
            lines.append(f"- {action['type']}：{len(action.get('codes') or [])} 只")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or execute data repairs for intraday decision quality.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--quality-snapshot", default="data/metadata/data-quality-snapshot.json")
    parser.add_argument("--quality-output", default="data/metadata/data-quality-snapshot.json")
    parser.add_argument("--quality-markdown-output", default="reports/data-quality-snapshot.md")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--daily-metadata-output", default="data/metadata/daily_bars.fetch.json")
    parser.add_argument("--minute-cache-dir", default="data/processed/minute-bars")
    parser.add_argument("--minute-begin", default=(date.today() - timedelta(days=180)).strftime("%Y%m%d"))
    parser.add_argument("--minute-end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--intraday-output", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--intraday-markdown-output", default="reports/intraday-monitor.latest.md")
    parser.add_argument("--profile", default="config/investment-profile.yaml")
    parser.add_argument("--total-assets", type=float)
    parser.add_argument("--max-stale-seconds", type=int, default=60)
    parser.add_argument("--max-quote-lag-seconds", type=float, default=60.0)
    parser.add_argument("--min-daily-bars", type=int, default=20)
    parser.add_argument("--max-daily-age-days", type=int, default=5)
    parser.add_argument("--min-minute-bars", type=int, default=120)
    parser.add_argument("--max-minute-age-hours", type=float, default=30.0)
    parser.add_argument("--max-consistency-diff-pct", type=float, default=1.0)
    parser.add_argument("--fetch-datalen", type=int, default=320)
    parser.add_argument("--request-interval-seconds", type=float, default=0.2)
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
    parser.add_argument("--output", default="data/metadata/data-quality-repair-plan.json")
    parser.add_argument("--markdown-output", default="reports/data-quality-repair-plan.md")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        position_paths = expand_position_paths(args.positions)
        quality = load_json_if_exists(Path(args.quality_snapshot))
        if quality is None:
            quality = rebuild_quality(args, position_paths)
        plan = build_plan(quality, position_paths)
        execution = execute_plan(plan, args, position_paths) if args.execute and plan["actions"] else None
        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "execute" if args.execute else "plan",
            "plan": plan,
            "execution": execution,
        }
        write_json(Path(args.output), report)
        write_text(Path(args.markdown_output), render_markdown(report))
    except Exception as exc:
        print(f"data quality repair failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"mode: {report['mode']}, actions: {report['plan']['action_count']}")
        if report.get("execution"):
            print(f"refreshed quality: {report['execution']['refreshed_quality']['status_counts']}")
        print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
