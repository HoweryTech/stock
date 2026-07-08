#!/usr/bin/env python3
"""Generate auditable strategy config change drafts from resolved review tasks."""

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


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")
    return normalized or "UNKNOWN"


def action_codes(task: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for item in task.get("actions", []) or []:
        code = item.get("code")
        if code:
            codes.append(str(code))
    return codes


def infer_change_items(task: dict[str, Any]) -> list[dict[str, str]]:
    if task.get("task_type") == "config_version":
        version_id = task.get("config_version_id") or "UNKNOWN_CONFIG_VERSION"
        return [
            {
                "path": "risk.max_total_position_pct",
                "proposed_change": "复核是否降低该配置版本后的总仓位上限，具体数值需人工填写。",
                "reason": f"配置版本 {version_id} 表现偏弱，需要确认是否降低整体风险暴露。",
            },
            {
                "path": "risk.max_position_pct_per_stock",
                "proposed_change": "复核是否降低该配置版本后的单票仓位上限，具体数值需人工填写。",
                "reason": f"配置版本 {version_id} 表现偏弱，需要控制单笔错误对组合的影响。",
            },
            {
                "path": "strategies",
                "proposed_change": "复核是否拆分适用场景、提高筛选阈值或回滚部分策略规则。",
                "reason": f"配置版本 {version_id} 需要区分配置规则问题、市场阶段问题和具体策略问题。",
            },
        ]
    strategy = task.get("strategy") or "UNKNOWN"
    codes = set(action_codes(task))
    items: list[dict[str, str]] = []
    if "strategy_cooldown_required" in codes:
        items.append(
            {
                "path": f"strategies.{strategy}.enabled",
                "proposed_change": "复核是否临时暂停新开仓；如暂停，应显式记录恢复条件。",
                "reason": "策略触发连续亏损冷静期。",
            }
        )
    if "loss_making_discipline_exception" in codes:
        items.append(
            {
                "path": f"strategies.{strategy}.discipline.exception_position_limit_pct",
                "proposed_change": "降低或取消纪律例外仓位上限。",
                "reason": "纪律例外交易产生亏损。",
            }
        )
    if {"low_win_rate", "low_average_return", "negative_portfolio_contribution"} & codes:
        items.append(
            {
                "path": f"strategies.{strategy}.screening",
                "proposed_change": "提高筛选阈值、缩小适用场景或降低默认仓位。",
                "reason": "策略胜率、平均收益或组合贡献低于阈值。",
            }
        )
    if not items:
        items.append(
            {
                "path": f"strategies.{strategy}",
                "proposed_change": "根据复核结论调整策略规则，具体字段需人工填写。",
                "reason": "策略复核任务已解决，但动作类型未映射到具体字段。",
            }
        )
    return items


def build_change_drafts(tasks_doc: dict[str, Any], generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    drafts: list[dict[str, Any]] = []
    for task in tasks_doc.get("tasks", []) or []:
        if task.get("task_status") != "resolved":
            continue
        resolution = (task.get("resolution") or "").strip()
        if not resolution:
            continue
        task_type = task.get("task_type") or "strategy"
        strategy = task.get("strategy") or ("CONFIG_VERSION" if task_type == "config_version" else "UNKNOWN")
        task_id = task.get("id") or f"UNKNOWN-{len(drafts) + 1}"
        drafts.append(
            {
                "id": f"CONFIG-CHANGE-{slug(task_id)}",
                "source_task_id": task_id,
                "source_task_type": task_type,
                "strategy": strategy,
                "config_version_id": task.get("config_version_id"),
                "profile_hash": task.get("profile_hash"),
                "status": "draft",
                "created_at": generated_at.isoformat(timespec="seconds"),
                "effective_date": None,
                "resolution": resolution,
                "review_evidence": {
                    "task_type": task_type,
                    "config_version_id": task.get("config_version_id"),
                    "profile_hash": task.get("profile_hash"),
                    "task_status": task.get("status"),
                    "task_priority": task.get("priority"),
                    "resolved_at": task.get("resolved_at"),
                    "actions": task.get("actions") or [],
                    "stats": task.get("stats") or {},
                },
                "change_items": infer_change_items(task),
                "approval": {
                    "required": True,
                    "approved_by": "",
                    "approved_at": None,
                    "rejected_by": "",
                    "rejected_at": None,
                    "rejected_reason": "",
                },
                "history": [],
            }
        )
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "source_task_count": len(tasks_doc.get("tasks", []) or []),
        "draft_count": len(drafts),
        "drafts": drafts,
    }


def render_change_drafts(result: dict[str, Any]) -> str:
    lines = [
        "# 策略配置变更草稿",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 来源任务数量：{result['source_task_count']}",
        f"- 草稿数量：{result['draft_count']}",
        "- 决策边界：本清单只生成配置变更草稿，不自动修改投资体系配置。",
        "",
    ]
    if not result["drafts"]:
        lines.append("- 无配置变更草稿。")
        return "\n".join(lines)

    for draft in result["drafts"]:
        lines.extend(
            [
                f"## {draft['id']}",
                "",
                f"- 来源任务：{draft['source_task_id']}",
                f"- 策略：{draft['strategy']}",
                f"- 状态：{draft['status']}",
                f"- 生效日期：{draft['effective_date'] or '待填写'}",
                f"- 人工结论：{draft['resolution']}",
                "",
                "建议变更项：",
            ]
        )
        for item in draft["change_items"]:
            lines.append(f"- `{item['path']}`：{item['proposed_change']} 原因：{item['reason']}")
        lines.extend(["", "审批：", "- required: true", "- approved_by: 待填写", ""])
    return "\n".join(lines).rstrip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate strategy config change drafts from resolved review tasks.")
    parser.add_argument("--tasks", default="data/metadata/strategy-review-tasks.json", help="Strategy review task JSON.")
    parser.add_argument("--output", default="reports/strategy-config-changes.md", help="Output Markdown config change drafts.")
    parser.add_argument("--json-output", default="data/metadata/strategy-config-changes.json", help="Output JSON config change drafts.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_change_drafts(load_json(Path(args.tasks)))
        write_text(Path(args.output), render_change_drafts(result))
        write_json(Path(args.json_output), result)
    except Exception as exc:
        print(f"strategy config change generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"strategy config changes: {args.output}")
        print(f"draft count: {result['draft_count']}")
    return 1 if result["draft_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
