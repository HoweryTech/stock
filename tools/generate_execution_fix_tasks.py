#!/usr/bin/env python3
"""Generate actionable fix tasks from execution loop check results."""

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


def slug(value: Any) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "-", str(value or "UNKNOWN").upper()).strip("-")
    return normalized or "UNKNOWN"


def task_id(group: str, code: str, subject_id: Any) -> str:
    return f"EXEC-FIX-{slug(group)}-{slug(code)}-{slug(subject_id)}"


def build_tasks(loop_check: dict[str, Any], generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    tasks: list[dict[str, Any]] = []
    for group in loop_check.get("fix_actions", []) or []:
        for item in group.get("items", []) or []:
            tasks.append(
                {
                    "id": task_id(group.get("group"), item.get("code"), item.get("subject_id")),
                    "task_status": "open",
                    "group": group.get("group"),
                    "group_title": group.get("title"),
                    "source_code": item.get("code"),
                    "subject_id": item.get("subject_id"),
                    "message": item.get("message") or "",
                    "fix_hint": item.get("fix_hint") or "",
                    "resolution": "",
                    "created_at": generated_at.isoformat(timespec="seconds"),
                    "history": [],
                }
            )
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "source_conclusion": loop_check.get("conclusion") or "unknown",
        "source_blocked_count": loop_check.get("blocked_count", 0),
        "source_needs_review_count": loop_check.get("needs_review_count", 0),
        "task_count": len(tasks),
        "open_task_count": len(tasks),
        "tasks": tasks,
    }


def render_tasks(task_doc: dict[str, Any]) -> str:
    lines = [
        "# 执行闭环修复任务",
        "",
        "- 决策边界：本报告只把执行闭环检查结果转成修复待办，不构成买卖建议。",
        f"- 生成时间：{task_doc['generated_at']}",
        f"- 来源闭环结论：{task_doc['source_conclusion']}",
        f"- 任务数量：{task_doc['task_count']}",
        f"- 未完成任务：{task_doc['open_task_count']}",
        "",
    ]
    if not task_doc["tasks"]:
        lines.append("- 当前没有执行闭环修复任务。")
        return "\n".join(lines)

    current_group = None
    for task in task_doc["tasks"]:
        if task["group_title"] != current_group:
            current_group = task["group_title"]
            lines.extend([f"## {current_group}", ""])
        lines.append(f"- [ ] {task['id']} subject={task['subject_id'] or '-'}")
        lines.append(f"  - issue: {task['message']}")
        if task["fix_hint"]:
            lines.append(f"  - fix: {task['fix_hint']}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate fix tasks from execution loop check JSON.")
    parser.add_argument("--loop-check", default="data/metadata/execution-loop-check.json", help="Execution loop check JSON.")
    parser.add_argument("--output", default="reports/execution-fix-tasks.md", help="Output Markdown task report.")
    parser.add_argument("--json-output", default="data/metadata/execution-fix-tasks.json", help="Output JSON task document.")
    parser.add_argument("--json", action="store_true", help="Print task document as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        tasks = build_tasks(load_json(Path(args.loop_check)))
        write_text(Path(args.output), render_tasks(tasks))
        write_json(Path(args.json_output), tasks)
    except Exception as exc:
        print(f"execution fix task generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    else:
        print(f"execution fix tasks: {args.output}")
        print(f"task count: {tasks['task_count']}")
    return 1 if tasks["open_task_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
