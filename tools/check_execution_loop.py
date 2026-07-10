#!/usr/bin/env python3
"""Check execution-to-review workflow integrity across generated records."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

try:
    from tools.check_exit_execution import check_exit_execution
    from tools.check_trade_execution import check_execution
    from tools.check_trade_review_quality import check_trade_review_quality
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_exit_execution import check_exit_execution
    from check_trade_execution import check_execution
    from check_trade_review_quality import check_trade_review_quality
    from risk_check import load_yaml, value_at


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


def check_documents(paths: list[Path], checker: Any, id_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        result = checker(load_yaml(path))
        rows.append(
            {
                "path": str(path),
                "id": result.get(id_key),
                "conclusion": result["conclusion"],
                "blocker_count": len(result.get("blockers", [])),
                "warning_count": len(result.get("warnings", [])),
                "blockers": result.get("blockers", []),
                "warnings": result.get("warnings", []),
            }
        )
    return rows


def load_documents(paths: list[Path]) -> list[dict[str, Any]]:
    return [{"path": str(path), "data": load_yaml(path)} for path in paths]


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "pass_count": sum(1 for row in rows if row["conclusion"] == "pass"),
        "needs_review_count": sum(1 for row in rows if row["conclusion"] == "needs_review"),
        "blocked_count": sum(1 for row in rows if row["conclusion"] == "blocked"),
        "rows": rows,
    }


def conclusion_rank(conclusion: str) -> int:
    return {"blocked": 0, "needs_review": 1, "pass": 2}.get(conclusion, 3)


def sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (conclusion_rank(row["conclusion"]), row.get("id") or "", row.get("path") or ""))


def source_ids(documents: list[dict[str, Any]], path: str) -> set[str]:
    ids: set[str] = set()
    for item in documents:
        source_id = value_at(item["data"], path)
        if source_id:
            ids.add(str(source_id))
    return ids


def row_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["id"]) for row in rows if row.get("id")}


def downstream_gaps(
    trade_execution_rows: list[dict[str, Any]],
    exit_execution_rows: list[dict[str, Any]],
    position_documents: list[dict[str, Any]],
    review_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    positioned_execution_ids = source_ids(position_documents, "execution_snapshot.execution.id")
    reviewed_exit_execution_ids = source_ids(review_documents, "review.source_exit_execution_id")

    for row in trade_execution_rows:
        if row["conclusion"] != "pass" or not row["id"]:
            continue
        if str(row["id"]) not in positioned_execution_ids:
            gaps.append(
                {
                    "subject_type": "trade_execution",
                    "subject_id": row["id"],
                    "expected_record": "position",
                    "code": "missing_position_from_trade_execution",
                    "message": f"买入执行 {row['id']} 已通过检查，但未找到来源执行对应的持仓记录。",
                    "fix_hint": f"运行 tools/new_position_from_execution.py --execution <{row['path']}> 生成持仓记录。",
                }
            )
    for row in exit_execution_rows:
        if row["conclusion"] != "pass" or not row["id"]:
            continue
        if str(row["id"]) not in reviewed_exit_execution_ids:
            gaps.append(
                {
                    "subject_type": "exit_execution",
                    "subject_id": row["id"],
                    "expected_record": "trade_review",
                    "code": "missing_review_from_exit_execution",
                    "message": f"卖出执行 {row['id']} 已通过检查，但未找到来源卖出执行对应的复盘记录。",
                    "fix_hint": f"运行 tools/new_trade_review_from_exit_execution.py --exit-execution <{row['path']}> 生成复盘草稿。",
                }
            )
    return gaps


def orphan_records(
    trade_execution_rows: list[dict[str, Any]],
    exit_execution_rows: list[dict[str, Any]],
    position_documents: list[dict[str, Any]],
    review_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    orphans: list[dict[str, Any]] = []
    trade_execution_ids = row_ids(trade_execution_rows)
    exit_execution_ids = row_ids(exit_execution_rows)

    for item in position_documents:
        position_id = value_at(item["data"], "position.id") or item["path"]
        source_execution_id = value_at(item["data"], "execution_snapshot.execution.id")
        if not source_execution_id:
            orphans.append(
                {
                    "subject_type": "position",
                    "subject_id": position_id,
                    "source_id": None,
                    "code": "position_missing_source_execution",
                    "message": f"持仓 {position_id} 缺少来源买入执行快照。",
                    "fix_hint": "补齐持仓的 execution_snapshot，或用 tools/new_position_from_execution.py 从执行记录重新生成持仓。",
                }
            )
        elif str(source_execution_id) not in trade_execution_ids:
            orphans.append(
                {
                    "subject_type": "position",
                    "subject_id": position_id,
                    "source_id": source_execution_id,
                    "code": "position_source_execution_not_found",
                    "message": f"持仓 {position_id} 的来源买入执行 {source_execution_id} 不在执行记录中。",
                    "fix_hint": "补回来源买入执行记录，或修正持仓的 execution_snapshot.execution.id。",
                }
            )
    for item in review_documents:
        review_id = value_at(item["data"], "review.id") or item["path"]
        source_exit_execution_id = value_at(item["data"], "review.source_exit_execution_id")
        if not source_exit_execution_id:
            orphans.append(
                {
                    "subject_type": "trade_review",
                    "subject_id": review_id,
                    "source_id": None,
                    "code": "review_missing_source_exit_execution",
                    "message": f"复盘 {review_id} 缺少来源卖出执行编号。",
                    "fix_hint": "补齐 review.source_exit_execution_id，或用 tools/new_trade_review_from_exit_execution.py 从卖出执行重新生成复盘。",
                }
            )
        elif str(source_exit_execution_id) not in exit_execution_ids:
            orphans.append(
                {
                    "subject_type": "trade_review",
                    "subject_id": review_id,
                    "source_id": source_exit_execution_id,
                    "code": "review_source_exit_execution_not_found",
                    "message": f"复盘 {review_id} 的来源卖出执行 {source_exit_execution_id} 不在卖出执行记录中。",
                    "fix_hint": "补回来源卖出执行记录，或修正 review.source_exit_execution_id。",
                }
            )
    return orphans


def fix_group_for_code(code: str) -> tuple[str, str]:
    if code == "missing_confirmed_manual_confirmation_record":
        return "manual_confirmation", "补齐人工确认"
    if code == "missing_position_from_trade_execution":
        return "position", "生成持仓记录"
    if code == "missing_review_from_exit_execution":
        return "trade_review", "生成复盘记录"
    if code.startswith("position_") or code.startswith("review_"):
        return "source_link", "修正来源引用"
    return "record_fix", "修正阻断或复核记录"


def add_fix_action(groups: dict[str, dict[str, Any]], *, code: str, subject_id: Any, message: str, fix_hint: str = "") -> None:
    group_code, title = fix_group_for_code(code)
    group = groups.setdefault(group_code, {"group": group_code, "title": title, "count": 0, "items": []})
    group["count"] += 1
    group["items"].append(
        {
            "code": code,
            "subject_id": subject_id,
            "message": message,
            "fix_hint": fix_hint,
        }
    )


def summarize_fix_actions(
    sections: dict[str, dict[str, Any]],
    downstream_gap_rows: list[dict[str, Any]],
    orphan_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for section in sections.values():
        for row in section["rows"]:
            for item in row["blockers"]:
                add_fix_action(groups, code=item["code"], subject_id=row["id"], message=item["message"])
            for item in row["warnings"]:
                add_fix_action(groups, code=item["code"], subject_id=row["id"], message=item["message"])
    for gap in downstream_gap_rows:
        add_fix_action(groups, code=gap["code"], subject_id=gap["subject_id"], message=gap["message"], fix_hint=gap["fix_hint"])
    for orphan in orphan_rows:
        add_fix_action(groups, code=orphan["code"], subject_id=orphan["subject_id"], message=orphan["message"], fix_hint=orphan["fix_hint"])
    return sorted(groups.values(), key=lambda item: item["group"])


def build_loop_check(args: argparse.Namespace) -> dict[str, Any]:
    trade_execution_rows = check_documents(expand_paths(args.trade_executions), check_execution, "execution_id")
    exit_execution_rows = check_documents(expand_paths(args.exit_executions), check_exit_execution, "exit_execution_id")
    review_paths = expand_paths(args.reviews)
    review_rows = check_documents(review_paths, check_trade_review_quality, "review_id")
    position_documents = load_documents(expand_paths(args.positions))
    review_documents = load_documents(review_paths)
    gaps = downstream_gaps(
        trade_execution_rows,
        exit_execution_rows,
        position_documents,
        review_documents,
    )
    orphans = orphan_records(
        trade_execution_rows,
        exit_execution_rows,
        position_documents,
        review_documents,
    )
    sections = {
        "trade_executions": summarize_rows(trade_execution_rows),
        "exit_executions": summarize_rows(exit_execution_rows),
        "reviews": summarize_rows(review_rows),
    }
    fix_actions = summarize_fix_actions(sections, gaps, orphans)
    blocked_count = sum(section["blocked_count"] for section in sections.values())
    downstream_gap_count = len(gaps)
    orphan_record_count = len(orphans)
    needs_review_count = sum(section["needs_review_count"] for section in sections.values()) + downstream_gap_count + orphan_record_count
    if blocked_count:
        conclusion = "blocked"
    elif needs_review_count:
        conclusion = "needs_review"
    else:
        conclusion = "pass"
    return {
        "conclusion": conclusion,
        "blocked_count": blocked_count,
        "needs_review_count": needs_review_count,
        "downstream_gap_count": downstream_gap_count,
        "downstream_gaps": gaps,
        "orphan_record_count": orphan_record_count,
        "orphan_records": orphans,
        "fix_actions": fix_actions,
        **sections,
    }


def render_section(title: str, section: dict[str, Any]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"- 数量：{section['count']}",
        f"- 通过：{section['pass_count']}",
        f"- 需复核：{section['needs_review_count']}",
        f"- 阻断：{section['blocked_count']}",
        "",
    ]
    if section["rows"]:
        for row in sorted_rows(section["rows"]):
            lines.append(
                f"- {row['id'] or '-'} conclusion={row['conclusion']} blockers={row['blocker_count']} warnings={row['warning_count']} path={row['path']}"
            )
            for item in row["blockers"]:
                lines.append(f"  - blocker[{item['code']}] {item['message']}")
            for item in row["warnings"]:
                lines.append(f"  - warning[{item['code']}] {item['message']}")
    else:
        lines.append("- 无记录。")
    lines.append("")
    return lines


def render_loop_check(result: dict[str, Any]) -> str:
    lines = [
        "# 执行闭环总检查",
        "",
        "- 决策边界：本报告只汇总执行、卖出和复盘记录的规则检查结果，不构成买卖建议。",
        f"- 总结论：{result['conclusion']}",
        f"- 阻断项记录数：{result['blocked_count']}",
        f"- 需复核记录数：{result['needs_review_count']}",
        f"- 缺失下游记录数：{result['downstream_gap_count']}",
        f"- 孤儿记录数：{result['orphan_record_count']}",
        "",
    ]
    if result["downstream_gaps"]:
        lines.extend(["## 缺失下游记录", ""])
        for gap in result["downstream_gaps"]:
            lines.append(f"- [{gap['code']}] {gap['message']}")
            lines.append(f"  - fix: {gap['fix_hint']}")
        lines.append("")
    if result["orphan_records"]:
        lines.extend(["## 孤儿记录", ""])
        for item in result["orphan_records"]:
            lines.append(f"- [{item['code']}] {item['message']}")
            lines.append(f"  - fix: {item['fix_hint']}")
        lines.append("")
    if result["fix_actions"]:
        lines.extend(["## 修复动作分组", ""])
        for group in result["fix_actions"]:
            lines.append(f"- {group['title']}：{group['count']} 项")
            for item in group["items"]:
                lines.append(f"  - [{item['code']}] {item['subject_id'] or '-'}：{item['message']}")
                if item["fix_hint"]:
                    lines.append(f"    - fix: {item['fix_hint']}")
        lines.append("")
    lines.extend(render_section("买入执行记录", result["trade_executions"]))
    lines.extend(render_section("卖出执行记录", result["exit_executions"]))
    lines.extend(render_section("交易复盘记录", result["reviews"]))
    return "\n".join(lines).rstrip()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check execution-to-review workflow integrity.")
    parser.add_argument("--trade-executions", nargs="+", default=["executions/*.yaml"], help="Trade execution YAML paths or glob patterns.")
    parser.add_argument("--exit-executions", nargs="+", default=["exit-executions/*.yaml"], help="Sell execution YAML paths or glob patterns.")
    parser.add_argument("--positions", nargs="+", default=["positions/*.yaml"], help="Position YAML paths or glob patterns.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Trade review YAML paths or glob patterns.")
    parser.add_argument("--output", default="reports/execution-loop-check.md", help="Output Markdown report.")
    parser.add_argument("--json-output", help="Optional output JSON summary.")
    parser.add_argument("--json", action="store_true", help="Print JSON result instead of text status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_loop_check(args)
        write_text(Path(args.output), render_loop_check(result))
        if args.json_output:
            write_json(Path(args.json_output), result)
    except Exception as exc:
        print(f"execution loop check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"execution loop check: {args.output}")
        print(f"conclusion: {result['conclusion']}")
        print(f"blocked records: {result['blocked_count']}")
        print(f"needs review records: {result['needs_review_count']}")
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
