#!/usr/bin/env python3
"""Generate a pending config patch from checked strategy config changes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import load_yaml, value_at


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


def build_patch(profile: dict[str, Any], changes_doc: dict[str, Any], check_result: dict[str, Any], generated_at: datetime | None = None) -> dict[str, Any]:
    if check_result.get("conclusion") != "pass":
        raise ValueError("strategy config change check must pass before generating patch")

    generated_at = generated_at or datetime.now()
    operations: list[dict[str, Any]] = []
    for draft in changes_doc.get("drafts", []) or []:
        if draft.get("status") != "approved":
            continue
        for item in draft.get("change_items", []) or []:
            if "proposed_value" not in item:
                continue
            path = item.get("path")
            operations.append(
                {
                    "op": "replace",
                    "path": path,
                    "old_value": value_at(profile, path),
                    "new_value": item.get("proposed_value"),
                    "source_change_id": draft.get("id"),
                    "source_task_id": draft.get("source_task_id"),
                    "reason": item.get("reason") or draft.get("resolution"),
                }
            )
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "check_conclusion": check_result.get("conclusion"),
        "operation_count": len(operations),
        "operations": operations,
        "apply_mode": "manual_review_required",
    }


def render_patch(patch: dict[str, Any]) -> str:
    lines = [
        "# 待应用策略配置补丁",
        "",
        f"- 生成时间：{patch['generated_at']}",
        f"- 校验结论：{patch['check_conclusion']}",
        f"- 操作数量：{patch['operation_count']}",
        "- 应用模式：manual_review_required",
        "- 决策边界：本补丁只列出待应用变更，不自动修改配置文件。",
        "",
    ]
    if not patch["operations"]:
        lines.append("- 无可应用操作。")
        return "\n".join(lines)

    for item in patch["operations"]:
        lines.extend(
            [
                f"## {item['source_change_id']}",
                "",
                f"- op: {item['op']}",
                f"- path: `{item['path']}`",
                f"- old_value: {item['old_value']}",
                f"- new_value: {item['new_value']}",
                f"- source_task_id: {item['source_task_id']}",
                f"- reason: {item['reason'] or '未记录'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a pending config patch from checked strategy config changes.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Investment profile YAML.")
    parser.add_argument("--changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change JSON.")
    parser.add_argument("--check", default="data/metadata/strategy-config-changes.check.json", help="Strategy config change check JSON.")
    parser.add_argument("--output", default="reports/strategy-config-patch.md", help="Output Markdown patch.")
    parser.add_argument("--json-output", default="data/metadata/strategy-config-patch.json", help="Output JSON patch.")
    parser.add_argument("--json", action="store_true", help="Print JSON patch.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        patch = build_patch(load_yaml(Path(args.profile)), load_json(Path(args.changes)), load_json(Path(args.check)))
        write_text(Path(args.output), render_patch(patch))
        write_json(Path(args.json_output), patch)
    except Exception as exc:
        print(f"strategy config patch generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(patch, ensure_ascii=False, indent=2))
    else:
        print(f"strategy config patch: {args.output}")
        print(f"operation count: {patch['operation_count']}")
    return 1 if patch["operation_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
