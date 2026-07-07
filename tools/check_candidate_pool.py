#!/usr/bin/env python3
"""Check whether candidate pool rows are sufficiently evidenced before trade planning."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CheckItem:
    code: str
    level: str
    message: str


def read_candidate_pool(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def split_text(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def has_strategy(candidate: dict[str, str], strategy: str) -> bool:
    return strategy in split_text(candidate.get("strategies", ""))


def check_candidate(candidate: dict[str, str]) -> list[CheckItem]:
    code = candidate.get("code", "") or "-"
    items: list[CheckItem] = []
    strategies = split_text(candidate.get("strategies", ""))
    reasons = split_text(candidate.get("reasons", ""))
    risks = split_text(candidate.get("risks", ""))

    if not candidate.get("code"):
        items.append(CheckItem(code, "blocker", "缺少股票代码。"))
    if not strategies:
        items.append(CheckItem(code, "blocker", "缺少策略来源。"))
    if not reasons:
        items.append(CheckItem(code, "blocker", "缺少入选证据。"))
    if not candidate.get("primary_strategy"):
        items.append(CheckItem(code, "blocker", "缺少主策略。"))
    if candidate.get("primary_strategy") == "multi_strategy" and len(strategies) < 2:
        items.append(CheckItem(code, "blocker", "主策略为多策略共振，但策略来源少于 2 个。"))

    if has_strategy(candidate, "trend_strength") and not candidate.get("trade_date"):
        items.append(CheckItem(code, "blocker", "趋势候选缺少交易日。"))
    if has_strategy(candidate, "value_quality") and not candidate.get("report_period"):
        items.append(CheckItem(code, "blocker", "价值质量候选缺少财报报告期。"))
    if has_strategy(candidate, "value_quality") and not candidate.get("value_quality_score"):
        items.append(CheckItem(code, "blocker", "价值质量候选缺少价值质量分。"))
    if has_strategy(candidate, "trend_strength") and not candidate.get("trend_score"):
        items.append(CheckItem(code, "blocker", "趋势候选缺少趋势分。"))

    if not risks:
        items.append(CheckItem(code, "warning", "缺少显式风险提示，生成交易计划前必须人工补充反证和风险。"))
    if len(strategies) == 1:
        items.append(CheckItem(code, "warning", "单策略候选，需要补齐其他维度证据后再进入交易计划。"))
    if has_strategy(candidate, "value_quality") and "估值" not in candidate.get("reasons", "") and "分位" not in candidate.get("reasons", ""):
        items.append(CheckItem(code, "warning", "价值质量候选未看到估值分位证据。"))

    if not items:
        items.append(CheckItem(code, "info", "候选池字段检查通过。"))
    return items


def check_candidates(candidates: list[dict[str, str]]) -> dict[str, Any]:
    items: list[CheckItem] = []
    for candidate in candidates:
        items.extend(check_candidate(candidate))

    blockers = [item for item in items if item.level == "blocker"]
    warnings = [item for item in items if item.level == "warning"]
    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"

    return {
        "conclusion": conclusion,
        "candidate_count": len(candidates),
        "blockers": [item.__dict__ for item in blockers],
        "warnings": [item.__dict__ for item in warnings],
        "info": [item.__dict__ for item in items if item.level == "info"],
    }


def run_check(candidates_path: Path) -> dict[str, Any]:
    return check_candidates(read_candidate_pool(candidates_path))


def print_text(result: dict[str, Any]) -> None:
    print(f"candidate rows: {result['candidate_count']}")
    print(f"conclusion: {result['conclusion']}")
    for title, key in (("blockers", "blockers"), ("warnings", "warnings"), ("info", "info")):
        print(f"\n{title}:")
        items = result[key]
        if not items:
            print("- none")
        for item in items:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check candidate pool evidence quality.")
    parser.add_argument("--candidates", default="data/processed/candidate_pool.csv", help="Input candidate pool CSV.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_check(Path(args.candidates))
    except Exception as exc:
        print(f"candidate pool check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
