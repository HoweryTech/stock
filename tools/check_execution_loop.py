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
    from tools.risk_check import load_yaml
except ModuleNotFoundError:
    from check_exit_execution import check_exit_execution
    from check_trade_execution import check_execution
    from check_trade_review_quality import check_trade_review_quality
    from risk_check import load_yaml


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


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "pass_count": sum(1 for row in rows if row["conclusion"] == "pass"),
        "needs_review_count": sum(1 for row in rows if row["conclusion"] == "needs_review"),
        "blocked_count": sum(1 for row in rows if row["conclusion"] == "blocked"),
        "rows": rows,
    }


def build_loop_check(args: argparse.Namespace) -> dict[str, Any]:
    trade_execution_rows = check_documents(expand_paths(args.trade_executions), check_execution, "execution_id")
    exit_execution_rows = check_documents(expand_paths(args.exit_executions), check_exit_execution, "exit_execution_id")
    review_rows = check_documents(expand_paths(args.reviews), check_trade_review_quality, "review_id")
    sections = {
        "trade_executions": summarize_rows(trade_execution_rows),
        "exit_executions": summarize_rows(exit_execution_rows),
        "reviews": summarize_rows(review_rows),
    }
    blocked_count = sum(section["blocked_count"] for section in sections.values())
    needs_review_count = sum(section["needs_review_count"] for section in sections.values())
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
        for row in section["rows"]:
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
        "",
    ]
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
