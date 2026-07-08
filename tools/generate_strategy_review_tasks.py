#!/usr/bin/env python3
"""Generate strategy review tasks from strategy health results."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def task_id(strategy: str, status: str) -> str:
    raw = f"{strategy}-{status}".upper()
    normalized = re.sub(r"[^A-Z0-9]+", "-", raw).strip("-")
    return f"STRATEGY-REVIEW-{normalized or 'UNKNOWN'}"


def task_priority(status: str) -> str:
    if status == "pause_new_entries":
        return "high"
    if status == "needs_review":
        return "medium"
    return "low"


def required_review_items(actions: list[dict[str, Any]]) -> list[str]:
    codes = {str(item.get("code")) for item in actions}
    items: list[str] = []
    if "strategy_cooldown_required" in codes:
        items.append("复查最近连续亏损交易，确认是否暂停该策略新开仓。")
    if "loss_making_discipline_exception" in codes:
        items.append("复查纪律例外的触发条件、仓位上限和审批理由。")
    if {"low_win_rate", "low_average_return", "negative_portfolio_contribution"} & codes:
        items.append("复查策略样本、胜率、平均收益和组合贡献。")
    if "insufficient_review_sample" in codes:
        items.append("补充样本，不因样本不足直接扩大仓位。")
    if not items:
        items.append("复查策略健康动作，形成继续、暂停或调整规则的结论。")
    return items


def build_tasks(health: dict[str, Any], generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    tasks: list[dict[str, Any]] = []
    for row in health.get("strategies", []) or []:
        status = row.get("status")
        if status not in {"pause_new_entries", "needs_review"}:
            continue
        strategy = row.get("strategy") or "UNKNOWN"
        actions = row.get("actions") or []
        tasks.append(
            {
                "id": task_id(strategy, status),
                "strategy": strategy,
                "status": status,
                "priority": task_priority(status),
                "discipline_exception_loss_count": row.get("discipline_exception_loss_count", 0),
                "stats": row.get("stats") or {},
                "actions": actions,
                "required_review_items": required_review_items(actions),
                "decision_required": "暂停新开仓、继续观察、调整规则或降低仓位上限。",
            }
        )
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "source_conclusion": health.get("conclusion") or "unknown",
        "task_count": len(tasks),
        "tasks": tasks,
    }


def render_tasks(result: dict[str, Any]) -> str:
    lines = [
        "# 策略复核任务",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 来源策略健康结论：{result['source_conclusion']}",
        f"- 任务数量：{result['task_count']}",
        "- 决策边界：本清单只生成复核任务，不自动修改策略配置。",
        "",
    ]
    if not result["tasks"]:
        lines.append("- 无待复核策略。")
        return "\n".join(lines)

    for task in result["tasks"]:
        lines.extend(
            [
                f"## {task['id']}",
                "",
                f"- 策略：{task['strategy']}",
                f"- 状态：{task['status']}",
                f"- 优先级：{task['priority']}",
                f"- 亏损纪律例外数：{task['discipline_exception_loss_count']}",
                f"- 要求决策：{task['decision_required']}",
                "",
                "触发原因：",
            ]
        )
        if task["actions"]:
            for action in task["actions"]:
                lines.append(f"- [{action.get('code')}] {action.get('message')}")
        else:
            lines.append("- 未记录具体动作。")
        lines.extend(["", "复核事项："])
        for item in task["required_review_items"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate strategy review tasks from strategy health results.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Strategy health JSON.")
    parser.add_argument("--output", default="reports/strategy-review-tasks.md", help="Output Markdown task list.")
    parser.add_argument("--json-output", default="data/metadata/strategy-review-tasks.json", help="Output JSON task list.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_tasks(load_json(Path(args.strategy_health)))
        write_text(Path(args.output), render_tasks(result))
        write_json(Path(args.json_output), result)
    except Exception as exc:
        print(f"strategy review task generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"strategy review tasks: {args.output}")
        print(f"task count: {result['task_count']}")
    return 1 if result["task_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
