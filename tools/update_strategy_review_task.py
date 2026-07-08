#!/usr/bin/env python3
"""Update a strategy review task status with an auditable resolution."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {"open", "resolved", "deferred"}
FINAL_STATUSES = {"resolved", "deferred"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_task(tasks_doc: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in tasks_doc.get("tasks", []) or []:
        if task.get("id") == task_id:
            return task
    raise ValueError(f"task not found: {task_id}")


def update_task(
    tasks_doc: dict[str, Any],
    *,
    task_id: str,
    status: str,
    resolution: str,
    updated_by: str,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}")
    if status in FINAL_STATUSES and not resolution.strip():
        raise ValueError(f"{status} task requires a non-empty resolution")

    updated_at = updated_at or datetime.now()
    timestamp = updated_at.isoformat(timespec="seconds")
    task = find_task(tasks_doc, task_id)
    previous_status = task.get("task_status") or "open"

    task["task_status"] = status
    task["resolution"] = resolution
    task["updated_by"] = updated_by
    task["updated_at"] = timestamp
    if status in FINAL_STATUSES:
        task["resolved_at"] = timestamp
    elif status == "open":
        task["resolved_at"] = None

    history = task.setdefault("history", [])
    history.append(
        {
            "updated_at": timestamp,
            "updated_by": updated_by,
            "from_status": previous_status,
            "to_status": status,
            "resolution": resolution,
        }
    )
    tasks_doc["updated_at"] = timestamp
    tasks_doc["open_task_count"] = sum(1 for item in tasks_doc.get("tasks", []) or [] if (item.get("task_status") or "open") == "open")
    tasks_doc["resolved_task_count"] = sum(1 for item in tasks_doc.get("tasks", []) or [] if item.get("task_status") == "resolved")
    tasks_doc["deferred_task_count"] = sum(1 for item in tasks_doc.get("tasks", []) or [] if item.get("task_status") == "deferred")
    return task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update a strategy review task status.")
    parser.add_argument("--tasks", default="data/metadata/strategy-review-tasks.json", help="Strategy review task JSON.")
    parser.add_argument("--task-id", required=True, help="Task id to update.")
    parser.add_argument("--status", required=True, choices=sorted(ALLOWED_STATUSES), help="New task status.")
    parser.add_argument("--resolution", default="", help="Human resolution or deferral reason.")
    parser.add_argument("--updated-by", default="human", help="Reviewer name or identifier.")
    parser.add_argument("--output", help="Output JSON path. Defaults to overwriting --tasks.")
    parser.add_argument("--json", action="store_true", help="Print updated task as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        tasks_doc = load_json(Path(args.tasks))
        task = update_task(
            tasks_doc,
            task_id=args.task_id,
            status=args.status,
            resolution=args.resolution,
            updated_by=args.updated_by,
        )
        write_json(Path(args.output or args.tasks), tasks_doc)
    except Exception as exc:
        print(f"strategy review task update failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    else:
        print(f"updated task: {task['id']}")
        print(f"status: {task['task_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
