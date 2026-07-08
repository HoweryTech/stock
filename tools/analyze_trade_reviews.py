#!/usr/bin/env python3
"""Analyze trade reviews for strategy iteration."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from tools.check_trade_review_quality import check_trade_review_quality
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from check_trade_review_quality import check_trade_review_quality
    from risk_check import as_float, load_yaml, value_at


def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(match) for match in sorted(glob.glob(pattern)))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def strategy_of(review: dict[str, Any]) -> str:
    return value_at(review, "trade_plan_snapshot.strategy.source") or "UNKNOWN"


def add_numeric(bucket: dict[str, Any], trade_return_pct: float | None, portfolio_return_pct: float | None) -> None:
    bucket["count"] += 1
    if trade_return_pct is not None:
        bucket["trade_return_sum"] += trade_return_pct
        if trade_return_pct >= 0:
            bucket["win_count"] += 1
        else:
            bucket["loss_count"] += 1
    if portfolio_return_pct is not None:
        bucket["portfolio_return_sum"] += portfolio_return_pct


def finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    count = bucket["count"]
    measured = bucket["win_count"] + bucket["loss_count"]
    return {
        "count": count,
        "win_count": bucket["win_count"],
        "loss_count": bucket["loss_count"],
        "win_rate_pct": round(bucket["win_count"] / measured * 100, 4) if measured else None,
        "avg_trade_return_pct": round(bucket["trade_return_sum"] / measured, 4) if measured else None,
        "total_portfolio_return_pct": round(bucket["portfolio_return_sum"], 4),
    }


def empty_bucket() -> dict[str, Any]:
    return {
        "count": 0,
        "win_count": 0,
        "loss_count": 0,
        "trade_return_sum": 0.0,
        "portfolio_return_sum": 0.0,
    }


def analyze_reviews(review_paths: list[Path]) -> dict[str, Any]:
    overall = empty_bucket()
    by_strategy: dict[str, dict[str, Any]] = defaultdict(empty_bucket)
    result_categories: Counter[str] = Counter()
    error_tags: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    discipline: dict[str, Any] = {
        "cooldown_exception_count": 0,
        "strategy_health_exception_count": 0,
        "exception_trade_return_sum": 0.0,
        "exception_portfolio_return_sum": 0.0,
        "exceptions": [],
    }
    rows: list[dict[str, Any]] = []

    for path in review_paths:
        review = load_yaml(path)
        review_quality = check_trade_review_quality(review)
        strategy = strategy_of(review)
        trade_return_pct = as_float(value_at(review, "result.trade_return_pct"))
        portfolio_return_pct = as_float(value_at(review, "result.portfolio_return_pct"))
        category = value_at(review, "result.result_category") or "UNKNOWN"
        tags = value_at(review, "result.error_tags") or []
        was_cooldown_exception = bool(value_at(review, "discipline.was_cooldown_exception"))
        was_strategy_health_exception = bool(value_at(review, "discipline.was_strategy_health_exception"))
        is_exception = was_cooldown_exception or was_strategy_health_exception

        add_numeric(overall, trade_return_pct, portfolio_return_pct)
        add_numeric(by_strategy[strategy], trade_return_pct, portfolio_return_pct)
        result_categories[category] += 1
        quality[review_quality["conclusion"]] += 1
        for tag in tags:
            error_tags[str(tag)] += 1
        if was_cooldown_exception:
            discipline["cooldown_exception_count"] += 1
        if was_strategy_health_exception:
            discipline["strategy_health_exception_count"] += 1
        if is_exception:
            discipline["exception_trade_return_sum"] += trade_return_pct or 0.0
            discipline["exception_portfolio_return_sum"] += portfolio_return_pct or 0.0
            discipline["exceptions"].append(
                {
                    "review_id": value_at(review, "review.id"),
                    "strategy": strategy,
                    "trade_return_pct": trade_return_pct,
                    "portfolio_return_pct": portfolio_return_pct,
                    "exception_reason": value_at(review, "discipline.exception_reason"),
                }
            )

        rows.append(
            {
                "path": str(path),
                "review_id": value_at(review, "review.id"),
                "stock": value_at(review, "stock.code"),
                "strategy": strategy,
                "result_category": category,
                "trade_return_pct": trade_return_pct,
                "portfolio_return_pct": portfolio_return_pct,
                "quality_conclusion": review_quality["conclusion"],
                "lesson": value_at(review, "review_questions.lesson"),
                "was_cooldown_exception": was_cooldown_exception,
                "was_strategy_health_exception": was_strategy_health_exception,
            }
        )

    exception_count = len(discipline["exceptions"])
    discipline["exception_avg_trade_return_pct"] = round(discipline["exception_trade_return_sum"] / exception_count, 4) if exception_count else None
    discipline["exception_total_portfolio_return_pct"] = round(discipline["exception_portfolio_return_sum"], 4)
    discipline["exception_trade_return_sum"] = round(discipline["exception_trade_return_sum"], 4)
    discipline["exception_portfolio_return_sum"] = round(discipline["exception_portfolio_return_sum"], 4)

    return {
        "review_count": len(review_paths),
        "overall": finalize_bucket(overall),
        "by_strategy": {strategy: finalize_bucket(bucket) for strategy, bucket in sorted(by_strategy.items())},
        "result_categories": dict(sorted(result_categories.items())),
        "error_tags": dict(sorted(error_tags.items())),
        "quality": dict(sorted(quality.items())),
        "discipline": discipline,
        "reviews": rows,
    }


def render_counter(counter: dict[str, int]) -> list[str]:
    if not counter:
        return ["- 无。"]
    return [f"- {key}: {value}" for key, value in counter.items()]


def render_analysis(analysis: dict[str, Any]) -> str:
    overall = analysis["overall"]
    lines = [
        "# 交易复盘分析",
        "",
        f"- 复盘数量：{analysis['review_count']}",
        f"- 胜率：{overall['win_rate_pct'] if overall['win_rate_pct'] is not None else '-'}%",
        f"- 平均单笔收益率：{overall['avg_trade_return_pct'] if overall['avg_trade_return_pct'] is not None else '-'}%",
        f"- 组合收益贡献合计：{overall['total_portfolio_return_pct']}%",
        "- 决策边界：本报告只做复盘归因统计，不构成买卖建议。",
        "",
        "## 按策略汇总",
        "",
    ]

    if not analysis["by_strategy"]:
        lines.append("- 无。")
    else:
        for strategy, stats in analysis["by_strategy"].items():
            lines.append(
                f"- {strategy}: count={stats['count']} win_rate={stats['win_rate_pct']}% avg_return={stats['avg_trade_return_pct']}% portfolio={stats['total_portfolio_return_pct']}%"
            )

    lines.extend(["", "## 结果分类", "", *render_counter(analysis["result_categories"]), "", "## 错误标签", "", *render_counter(analysis["error_tags"]), ""])
    lines.extend(["## 复盘质量", "", *render_counter(analysis["quality"]), ""])
    discipline = analysis.get("discipline", {})
    lines.extend(
        [
            "## 纪律例外",
            "",
            f"- 冷静期例外交易数：{discipline.get('cooldown_exception_count', 0)}",
            f"- 策略健康例外交易数：{discipline.get('strategy_health_exception_count', 0)}",
            f"- 例外交易平均收益率：{discipline.get('exception_avg_trade_return_pct') if discipline.get('exception_avg_trade_return_pct') is not None else '-'}%",
            f"- 例外交易组合贡献合计：{discipline.get('exception_total_portfolio_return_pct', 0.0)}%",
            "",
        ]
    )
    if discipline.get("exceptions"):
        lines.append("例外交易明细：")
        for item in discipline["exceptions"]:
            lines.append(
                f"- {item['review_id']} strategy={item['strategy']} return={item['trade_return_pct']}% portfolio={item['portfolio_return_pct']}% reason={item.get('exception_reason') or '未记录'}"
            )
        lines.append("")
    lines.extend(["## 明细", ""])
    if not analysis["reviews"]:
        lines.append("- 无。")
    else:
        for row in analysis["reviews"]:
            lesson = row["lesson"] or "待补充"
            lines.append(
                f"- {row['review_id']} {row['stock']} strategy={row['strategy']} category={row['result_category']} quality={row['quality_conclusion']} return={row['trade_return_pct']}% lesson={lesson}"
            )
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze trade reviews for strategy iteration.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--output", default="reports/review-analysis.md", help="Output Markdown analysis.")
    parser.add_argument("--json-output", help="Optional output JSON analysis.")
    parser.add_argument("--json", action="store_true", help="Print JSON analysis.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        review_paths = expand_paths(args.reviews)
        analysis = analyze_reviews(review_paths)
        write_text(Path(args.output), render_analysis(analysis))
        if args.json_output:
            write_json(Path(args.json_output), analysis)
    except Exception as exc:
        print(f"trade review analysis failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(f"review analysis: {args.output}")
        print(f"review count: {analysis['review_count']}")
        print(f"total portfolio return pct: {analysis['overall']['total_portfolio_return_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
