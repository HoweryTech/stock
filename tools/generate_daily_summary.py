#!/usr/bin/env python3
"""Generate a daily operating summary from workflow artifacts."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_trade_review_quality import check_trade_review_quality
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_trade_review_quality import check_trade_review_quality
    from risk_check import load_yaml, value_at


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        paths.extend(Path(match) for match in matches)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def load_yaml_files(patterns: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in expand_paths(patterns):
        items.append({"path": str(path), "data": load_yaml(path)})
    return items


def collect_codes(items: list[dict[str, Any]], key: str) -> list[str]:
    codes: list[str] = []
    for item in items:
        code = item.get("code")
        message = item.get("message")
        if code and message:
            codes.append(f"[{code}] {message}")
        elif code:
            codes.append(str(code))
        elif message:
            codes.append(str(message))
    return codes


def summarize_watchlist(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {"available": False, "conclusion": "missing", "candidate_count": None, "warnings": []}
    candidate_check = value_at(metadata, "steps.candidate_pool_check") or value_at(metadata, "candidate_pool_check") or {}
    return {
        "available": True,
        "conclusion": candidate_check.get("conclusion") or metadata.get("conclusion") or "unknown",
        "candidate_count": value_at(metadata, "steps.merge_candidate_pool.rows") or metadata.get("candidate_count"),
        "warnings": collect_codes(candidate_check.get("warnings", []) or [], "warnings"),
    }


def summarize_portfolio(portfolio: dict[str, Any] | None) -> dict[str, Any]:
    if not portfolio:
        return {"available": False, "conclusion": "missing", "position_count": 0, "needs_action_count": 0, "warning_count": 0, "items": []}
    items = collect_codes(portfolio.get("portfolio_actions", []) or [], "portfolio_actions")
    items.extend(collect_codes(portfolio.get("portfolio_warnings", []) or [], "portfolio_warnings"))
    for position in portfolio.get("positions", []) or []:
        result = position.get("result", {})
        for action in result.get("actions", []) or []:
            code = action.get("code")
            message = action.get("message")
            items.append(f"{position.get('path')}: [{code}] {message}")
        for warning in result.get("warnings", []) or []:
            code = warning.get("code")
            message = warning.get("message")
            items.append(f"{position.get('path')}: [{code}] {message}")
    return {
        "available": True,
        "conclusion": portfolio.get("conclusion") or "unknown",
        "position_count": portfolio.get("position_count", 0),
        "total_position_pct": portfolio.get("total_position_pct"),
        "needs_action_count": portfolio.get("needs_action_count", 0),
        "warning_count": portfolio.get("warning_count", 0),
        "items": items,
    }


def summarize_exit_plans(exit_plans: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in exit_plans:
        data = item["data"]
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "exit_plan.id"),
                "stock": value_at(data, "stock.code"),
                "type": value_at(data, "exit_plan.exit_type"),
                "urgency": value_at(data, "exit_plan.urgency"),
                "must_exit": value_at(data, "decision.must_exit"),
                "daily_conclusion": value_at(data, "checks.daily_check_conclusion"),
            }
        )
    return {"count": len(rows), "rows": rows}


def summarize_exit_executions(executions: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in executions:
        data = item["data"]
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "execution.id"),
                "stock": value_at(data, "stock.code"),
                "exit_check": value_at(data, "execution.exit_check_conclusion"),
                "trade_return_pct": value_at(data, "result_estimate.trade_return_pct"),
                "portfolio_return_pct": value_at(data, "result_estimate.portfolio_return_pct"),
            }
        )
    return {"count": len(rows), "rows": rows}


def summarize_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    drafts = 0
    quality_needs_review = 0
    quality_blocked = 0
    for item in reviews:
        data = item["data"]
        status = value_at(data, "review.status")
        quality = check_trade_review_quality(data)
        if status == "draft":
            drafts += 1
        if quality["conclusion"] == "blocked":
            quality_blocked += 1
        elif quality["conclusion"] == "needs_review":
            quality_needs_review += 1
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "review.id"),
                "stock": value_at(data, "stock.code"),
                "status": status,
                "category": value_at(data, "result.result_category"),
                "trade_return_pct": value_at(data, "result.trade_return_pct"),
                "lesson": value_at(data, "review_questions.lesson"),
                "quality_conclusion": quality["conclusion"],
            }
        )
    return {
        "count": len(rows),
        "draft_count": drafts,
        "quality_blocked_count": quality_blocked,
        "quality_needs_review_count": quality_needs_review,
        "rows": rows,
    }


def derive_operating_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    watchlist = summary["watchlist"]
    portfolio = summary["portfolio"]
    exits = summary["exit_plans"]
    reviews = summary["reviews"]

    if not watchlist["available"]:
        actions.append("生成或刷新观察池流水线。")
    elif watchlist["conclusion"] not in {"pass", "normal"}:
        actions.append(f"检查候选池质量结论：{watchlist['conclusion']}。")

    if not portfolio["available"]:
        actions.append("执行组合持仓日检。")
    elif portfolio["conclusion"] == "needs_action":
        actions.append("优先处理组合或持仓日检中的 needs_action。")
    elif portfolio["conclusion"] == "warning":
        actions.append("复核组合或持仓提醒项。")

    urgent_exits = [row for row in exits["rows"] if row["must_exit"] or row["urgency"] == "immediate"]
    if urgent_exits:
        actions.append(f"处理 {len(urgent_exits)} 个紧急退出计划。")
    if reviews["draft_count"]:
        actions.append(f"补全 {reviews['draft_count']} 份复盘草稿。")
    if reviews["quality_blocked_count"]:
        actions.append(f"修正 {reviews['quality_blocked_count']} 份阻断级复盘。")
    if reviews["quality_needs_review_count"]:
        actions.append(f"完善 {reviews['quality_needs_review_count']} 份需复核复盘。")
    if not actions:
        actions.append("当前没有阻断项；保持观察，不因空闲而交易。")
    return actions


def build_summary(args: argparse.Namespace, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    summary = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "watchlist": summarize_watchlist(load_json_if_exists(Path(args.watchlist_metadata))),
        "portfolio": summarize_portfolio(load_json_if_exists(Path(args.portfolio_check))),
        "exit_plans": summarize_exit_plans(load_yaml_files(args.exit_plans)),
        "exit_executions": summarize_exit_executions(load_yaml_files(args.exit_executions)),
        "reviews": summarize_reviews(load_yaml_files(args.reviews)),
    }
    summary["operating_actions"] = derive_operating_actions(summary)
    return summary


def render_section_list(items: list[str]) -> list[str]:
    if not items:
        return ["- 无。"]
    return [f"- {item}" for item in items]


def render_summary(summary: dict[str, Any]) -> str:
    watchlist = summary["watchlist"]
    portfolio = summary["portfolio"]
    exits = summary["exit_plans"]
    executions = summary["exit_executions"]
    reviews = summary["reviews"]

    lines = [
        "# 每日操作摘要",
        "",
        f"- 生成时间：{summary['generated_at']}",
        "- 决策边界：本报告只汇总事实和规则检查，不构成买卖建议。",
        "",
        "## 今日优先动作",
        "",
        *render_section_list(summary["operating_actions"]),
        "",
        "## 观察池",
        "",
        f"- 元数据状态：{'已读取' if watchlist['available'] else '缺失'}",
        f"- 候选池结论：{watchlist['conclusion']}",
        f"- 候选数量：{watchlist['candidate_count'] if watchlist['candidate_count'] is not None else '-'}",
        "",
        "## 组合持仓",
        "",
        f"- 检查状态：{'已读取' if portfolio['available'] else '缺失'}",
        f"- 组合结论：{portfolio['conclusion']}",
        f"- 持仓数量：{portfolio['position_count']}",
        f"- 总仓位：{portfolio.get('total_position_pct') if portfolio.get('total_position_pct') is not None else '-'}",
        f"- 需处理持仓数：{portfolio['needs_action_count']}",
        f"- 提醒持仓数：{portfolio['warning_count']}",
        "",
        "组合/持仓提示：",
        *render_section_list(portfolio["items"]),
        "",
        "## 退出与卖出",
        "",
        f"- 退出计划数量：{exits['count']}",
        f"- 卖出执行数量：{executions['count']}",
        "",
    ]

    if exits["rows"]:
        lines.append("退出计划：")
        for row in exits["rows"]:
            lines.append(f"- {row['id']} {row['stock']} {row['type']} urgency={row['urgency']} must_exit={row['must_exit']}")
        lines.append("")
    if executions["rows"]:
        lines.append("卖出执行：")
        for row in executions["rows"]:
            lines.append(
                f"- {row['id']} {row['stock']} check={row['exit_check']} trade_return={row['trade_return_pct']}% portfolio={row['portfolio_return_pct']}%"
            )
        lines.append("")

    lines.extend(
        [
            "## 复盘",
            "",
            f"- 复盘记录数量：{reviews['count']}",
            f"- 草稿数量：{reviews['draft_count']}",
            f"- 阻断级复盘：{reviews['quality_blocked_count']}",
            f"- 需复核复盘：{reviews['quality_needs_review_count']}",
            "",
        ]
    )
    if reviews["rows"]:
        lines.append("复盘草稿：")
        for row in reviews["rows"]:
            lesson = row["lesson"] or "待补充"
            lines.append(
                f"- {row['id']} {row['stock']} {row['category']} quality={row['quality_conclusion']} return={row['trade_return_pct']}% lesson={lesson}"
            )
        lines.append("")

    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a daily operating summary from workflow artifacts.")
    parser.add_argument("--watchlist-metadata", default="data/metadata/watchlist_pipeline.json", help="Watchlist pipeline metadata JSON.")
    parser.add_argument("--portfolio-check", default="data/metadata/portfolio_positions.check.json", help="Portfolio position check JSON.")
    parser.add_argument("--exit-plans", nargs="+", default=["exit-plans/*.yaml"], help="Exit plan YAML paths or glob patterns.")
    parser.add_argument("--exit-executions", nargs="+", default=["exit-executions/*.yaml"], help="Sell execution YAML paths or glob patterns.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--output", default="reports/daily-summary.md", help="Output Markdown report.")
    parser.add_argument("--json-output", help="Optional output JSON summary.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary instead of text status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build_summary(args)
        write_text(Path(args.output), render_summary(summary))
        if args.json_output:
            write_json(Path(args.json_output), summary)
    except Exception as exc:
        print(f"daily summary generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"daily summary: {args.output}")
        print("actions:")
        for item in summary["operating_actions"]:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
