#!/usr/bin/env python3
"""Check strategy health from review analysis and cooldown results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def is_loss_making_exception(item: dict[str, Any], strategy: str) -> bool:
    if item.get("strategy") != strategy:
        return False
    trade_return = item.get("trade_return_pct")
    portfolio_return = item.get("portfolio_return_pct")
    return (trade_return is not None and trade_return < 0) or (portfolio_return is not None and portfolio_return < 0)


def classify_strategy(
    strategy: str,
    stats: dict[str, Any],
    cooldown: dict[str, Any],
    discipline: dict[str, Any],
    *,
    min_trades: int,
    min_win_rate_pct: float,
    min_avg_return_pct: float,
) -> dict[str, Any]:
    actions: list[dict[str, str]] = []
    review_count = int(stats.get("count") or 0)
    win_rate = stats.get("win_rate_pct")
    avg_return = stats.get("avg_trade_return_pct")
    portfolio_return = stats.get("total_portfolio_return_pct")
    strategy_streaks = cooldown.get("strategy_losing_streaks") or {}
    exception_losses = [
        item
        for item in discipline.get("exceptions", [])
        if is_loss_making_exception(item, strategy)
    ]

    if strategy_streaks.get(strategy, 0) >= int(cooldown.get("threshold") or 0) and cooldown.get("conclusion") == "cooldown_required":
        actions.append(
            {
                "code": "strategy_cooldown_required",
                "message": f"策略 {strategy} 已触发连续亏损冷静期。",
            }
        )
    if review_count < min_trades:
        actions.append(
            {
                "code": "insufficient_review_sample",
                "message": f"策略 {strategy} 复盘样本 {review_count} 笔，低于评估阈值 {min_trades} 笔。",
            }
        )
    if review_count >= min_trades and win_rate is not None and win_rate < min_win_rate_pct:
        actions.append(
            {
                "code": "low_win_rate",
                "message": f"策略 {strategy} 胜率 {win_rate:.2f}% 低于阈值 {min_win_rate_pct:.2f}%。",
            }
        )
    if review_count >= min_trades and avg_return is not None and avg_return < min_avg_return_pct:
        actions.append(
            {
                "code": "low_average_return",
                "message": f"策略 {strategy} 平均单笔收益 {avg_return:.2f}% 低于阈值 {min_avg_return_pct:.2f}%。",
            }
        )
    if portfolio_return is not None and portfolio_return < 0:
        actions.append(
            {
                "code": "negative_portfolio_contribution",
                "message": f"策略 {strategy} 组合收益贡献为 {portfolio_return:.2f}%。",
            }
        )
    if exception_losses:
        actions.append(
            {
                "code": "loss_making_discipline_exception",
                "message": f"策略 {strategy} 存在 {len(exception_losses)} 笔亏损纪律例外交易，需要复查破例规则。",
            }
        )

    if any(item["code"] == "strategy_cooldown_required" for item in actions):
        status = "pause_new_entries"
    elif any(item["code"] in {"low_win_rate", "low_average_return", "negative_portfolio_contribution", "loss_making_discipline_exception"} for item in actions):
        status = "needs_review"
    else:
        status = "healthy"

    return {
        "strategy": strategy,
        "status": status,
        "stats": stats,
        "discipline_exception_loss_count": len(exception_losses),
        "actions": actions,
    }


def classify_config_version(
    version_id: str,
    stats: dict[str, Any],
    *,
    min_trades: int,
    min_win_rate_pct: float,
    min_avg_return_pct: float,
) -> dict[str, Any]:
    actions: list[dict[str, str]] = []
    review_count = int(stats.get("count") or 0)
    win_rate = stats.get("win_rate_pct")
    avg_return = stats.get("avg_trade_return_pct")
    portfolio_return = stats.get("total_portfolio_return_pct")

    if review_count < min_trades:
        actions.append(
            {
                "code": "config_version_insufficient_review_sample",
                "message": f"配置版本 {version_id} 复盘样本 {review_count} 笔，低于评估阈值 {min_trades} 笔。",
            }
        )
    if review_count >= min_trades and win_rate is not None and win_rate < min_win_rate_pct:
        actions.append(
            {
                "code": "config_version_low_win_rate",
                "message": f"配置版本 {version_id} 胜率 {win_rate:.2f}% 低于阈值 {min_win_rate_pct:.2f}%。",
            }
        )
    if review_count >= min_trades and avg_return is not None and avg_return < min_avg_return_pct:
        actions.append(
            {
                "code": "config_version_low_average_return",
                "message": f"配置版本 {version_id} 平均单笔收益 {avg_return:.2f}% 低于阈值 {min_avg_return_pct:.2f}%。",
            }
        )
    if portfolio_return is not None and portfolio_return < 0:
        actions.append(
            {
                "code": "config_version_negative_portfolio_contribution",
                "message": f"配置版本 {version_id} 组合收益贡献为 {portfolio_return:.2f}%。",
            }
        )

    if any(
        item["code"]
        in {
            "config_version_low_win_rate",
            "config_version_low_average_return",
            "config_version_negative_portfolio_contribution",
        }
        for item in actions
    ):
        status = "needs_review"
    else:
        status = "healthy"

    return {
        "version_id": version_id,
        "profile_hash": stats.get("profile_hash"),
        "profile_hash_short": stats.get("profile_hash_short"),
        "status": status,
        "stats": stats,
        "actions": actions,
    }


def check_strategy_health(
    analysis: dict[str, Any],
    cooldown: dict[str, Any],
    *,
    min_trades: int = 3,
    min_win_rate_pct: float = 40.0,
    min_avg_return_pct: float = 0.0,
) -> dict[str, Any]:
    strategies = analysis.get("by_strategy") or {}
    config_versions = analysis.get("by_config_version") or {}
    discipline = analysis.get("discipline") or {}
    rows = [
        classify_strategy(
            strategy,
            stats,
            cooldown,
            discipline,
            min_trades=min_trades,
            min_win_rate_pct=min_win_rate_pct,
            min_avg_return_pct=min_avg_return_pct,
        )
        for strategy, stats in sorted(strategies.items())
    ]
    config_rows = [
        classify_config_version(
            version,
            stats,
            min_trades=min_trades,
            min_win_rate_pct=min_win_rate_pct,
            min_avg_return_pct=min_avg_return_pct,
        )
        for version, stats in sorted(config_versions.items())
    ]
    pause_count = sum(1 for row in rows if row["status"] == "pause_new_entries")
    review_count = sum(1 for row in rows if row["status"] == "needs_review")
    config_review_count = sum(1 for row in config_rows if row["status"] == "needs_review")
    if pause_count:
        conclusion = "pause_required"
    elif review_count or config_review_count:
        conclusion = "needs_review"
    else:
        conclusion = "healthy"
    return {
        "conclusion": conclusion,
        "strategy_count": len(rows),
        "pause_count": pause_count,
        "needs_review_count": review_count,
        "config_version_count": len(config_rows),
        "needs_review_config_version_count": config_review_count,
        "thresholds": {
            "min_trades": min_trades,
            "min_win_rate_pct": min_win_rate_pct,
            "min_avg_return_pct": min_avg_return_pct,
        },
        "strategies": rows,
        "config_versions": config_rows,
    }


def render_health(result: dict[str, Any]) -> str:
    lines = [
        "# 策略健康检查",
        "",
        f"- 结论：{result['conclusion']}",
        f"- 策略数量：{result['strategy_count']}",
        f"- 暂停新开仓策略数：{result['pause_count']}",
        f"- 需复核策略数：{result['needs_review_count']}",
        f"- 配置版本数量：{result.get('config_version_count', 0)}",
        f"- 需复核配置版本数：{result.get('needs_review_config_version_count', 0)}",
        "- 决策边界：本报告只生成策略复核清单，不自动修改策略配置。",
        "",
        "## 策略明细",
        "",
    ]
    if not result["strategies"]:
        lines.append("- 无。")
    for row in result["strategies"]:
        stats = row["stats"]
        lines.append(
            f"- {row['strategy']}: status={row['status']} count={stats.get('count')} win_rate={stats.get('win_rate_pct')}% avg_return={stats.get('avg_trade_return_pct')}% portfolio={stats.get('total_portfolio_return_pct')}%"
        )
        for action in row["actions"]:
            lines.append(f"  - [{action['code']}] {action['message']}")
    lines.extend(["", "## 配置版本明细", ""])
    if not result.get("config_versions"):
        lines.append("- 无。")
    for row in result.get("config_versions", []):
        stats = row["stats"]
        hash_text = row.get("profile_hash_short") or "-"
        lines.append(
            f"- {row['version_id']}: status={row['status']} hash={hash_text} count={stats.get('count')} win_rate={stats.get('win_rate_pct')}% avg_return={stats.get('avg_trade_return_pct')}% portfolio={stats.get('total_portfolio_return_pct')}%"
        )
        for action in row["actions"]:
            lines.append(f"  - [{action['code']}] {action['message']}")
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check strategy health from review analysis and cooldown results.")
    parser.add_argument("--analysis", default="data/metadata/review-analysis.json", help="Review analysis JSON.")
    parser.add_argument("--cooldown", default="data/metadata/review-cooldown.json", help="Review cooldown JSON.")
    parser.add_argument("--output", default="reports/strategy-health.md", help="Output Markdown health report.")
    parser.add_argument("--json-output", default="data/metadata/strategy-health.json", help="Output JSON health report.")
    parser.add_argument("--min-trades", type=int, default=3, help="Minimum review sample size for statistical warnings.")
    parser.add_argument("--min-win-rate-pct", type=float, default=40.0, help="Minimum acceptable win rate.")
    parser.add_argument("--min-avg-return-pct", type=float, default=0.0, help="Minimum acceptable average trade return.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = check_strategy_health(
            load_json(Path(args.analysis)),
            load_json(Path(args.cooldown)),
            min_trades=args.min_trades,
            min_win_rate_pct=args.min_win_rate_pct,
            min_avg_return_pct=args.min_avg_return_pct,
        )
        write_text(Path(args.output), render_health(result))
        write_json(Path(args.json_output), result)
    except Exception as exc:
        print(f"strategy health check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"strategy health: {args.output}")
        print(f"conclusion: {result['conclusion']}")
        print(f"pause count: {result['pause_count']}")
        print(f"needs review count: {result['needs_review_count']}")
    return 1 if result["conclusion"] in {"pause_required", "needs_review"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
