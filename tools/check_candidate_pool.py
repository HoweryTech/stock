#!/usr/bin/env python3
"""Check whether candidate pool rows are sufficiently evidenced before trade planning."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CheckItem:
    code: str
    level: str
    message: str


@dataclass
class CheckContext:
    tradable_codes: set[str] | None = None
    as_of: datetime | None = None
    max_trend_age_days: int = 5


def read_candidate_pool(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_code_set(path: Path) -> set[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {(row.get("code") or "").strip() for row in csv.DictReader(file) if (row.get("code") or "").strip()}


def split_text(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def has_strategy(candidate: dict[str, str], strategy: str) -> bool:
    return strategy in split_text(candidate.get("strategies", ""))


def parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d")


def text_contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def parse_float(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def evidence_dimensions(candidate: dict[str, str], strategies: list[str], reasons: list[str], risks: list[str]) -> set[str]:
    text = " ".join(reasons + risks + strategies)
    dimensions: set[str] = set()

    if "trend_strength" in strategies or text_contains(text, ("趋势", "均线", "MA", "相对强度", "突破", "回踩")):
        dimensions.add("trend")
    if "value_quality" in strategies or text_contains(text, ("ROE", "ROA", "现金流", "负债", "毛利", "净利", "营收", "扣非")):
        dimensions.add("fundamental")
    if text_contains(text, ("估值", "PE", "PB", "分位", "股息", "安全边际")):
        dimensions.add("valuation")
    if text_contains(text, ("成交额", "成交量", "换手", "流动性")) or candidate.get("turnover_avg") or candidate.get("liquidity_score") or candidate.get("liquidity_evidence"):
        dimensions.add("liquidity")
    if text_contains(text, ("行业", "板块")) or candidate.get("industry_strength_score") or candidate.get("industry_strength_evidence"):
        dimensions.add("industry")
    if "event_catalyst" in strategies or text_contains(text, ("公告", "回购", "增持", "减持", "解禁", "合同", "政策", "问询", "重组")):
        dimensions.add("event")
    if risks:
        dimensions.add("risk")

    return dimensions


def check_candidate(candidate: dict[str, str], context: CheckContext | None = None) -> list[CheckItem]:
    context = context or CheckContext()
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
    if context.tradable_codes is not None and candidate.get("code") and candidate["code"] not in context.tradable_codes:
        items.append(CheckItem(code, "blocker", "候选股不在可交易股票池中，需先通过股票池风险过滤。"))

    if has_strategy(candidate, "trend_strength") and not candidate.get("trade_date"):
        items.append(CheckItem(code, "blocker", "趋势候选缺少交易日。"))
    if has_strategy(candidate, "value_quality") and not candidate.get("report_period"):
        items.append(CheckItem(code, "blocker", "价值质量候选缺少财报报告期。"))
    if has_strategy(candidate, "value_quality") and not candidate.get("value_quality_score"):
        items.append(CheckItem(code, "blocker", "价值质量候选缺少价值质量分。"))
    if has_strategy(candidate, "trend_strength") and not candidate.get("trend_score"):
        items.append(CheckItem(code, "blocker", "趋势候选缺少趋势分。"))
    if has_strategy(candidate, "event_catalyst") and not candidate.get("event_date"):
        items.append(CheckItem(code, "blocker", "事件催化候选缺少事件日期。"))
    if has_strategy(candidate, "event_catalyst") and not candidate.get("event_score"):
        items.append(CheckItem(code, "blocker", "事件催化候选缺少事件分。"))

    score_fields = {
        "combined_score": "综合排序分",
        "strategy_confluence_score": "策略共振分",
        "strategy_confluence_evidence": "策略共振证据",
        "data_quality_score": "数据质量分",
        "data_quality_status": "数据质量状态",
        "data_quality_evidence": "数据质量证据",
        "risk_penalty_score": "风险扣分",
        "risk_penalty_evidence": "风险扣分证据",
    }
    missing_score_fields = [label for field, label in score_fields.items() if not candidate.get(field)]
    if missing_score_fields:
        items.append(CheckItem(code, "warning", f"缺少评分解释字段：{', '.join(missing_score_fields)}。"))

    data_quality_status = candidate.get("data_quality_status")
    if data_quality_status == "weak":
        items.append(CheckItem(code, "warning", "数据质量状态为 weak，需补齐缺失证据后再进入交易计划。"))

    risk_penalty = parse_float(candidate.get("risk_penalty_score", ""))
    if risk_penalty is not None and risk_penalty <= -20:
        items.append(CheckItem(code, "warning", f"风险扣分 {risk_penalty:.2f} 较高，需优先复核反证和风险。"))

    liquidity_score = parse_float(candidate.get("liquidity_score", ""))
    if liquidity_score is not None and liquidity_score < 20:
        items.append(CheckItem(code, "warning", f"流动性评分 {liquidity_score:.2f} 偏低，需确认买卖可执行性。"))

    if context.as_of is not None and has_strategy(candidate, "trend_strength") and candidate.get("trade_date"):
        trade_date = parse_date(candidate.get("trade_date", ""))
        if trade_date is not None and (context.as_of.date() - trade_date.date()).days > context.max_trend_age_days:
            items.append(CheckItem(code, "warning", f"趋势交易日早于检查日超过 {context.max_trend_age_days} 天，需刷新行情后再进入交易计划。"))

    dimensions = evidence_dimensions(candidate, strategies, reasons, risks)
    if not risks:
        items.append(CheckItem(code, "warning", "缺少显式风险提示，生成交易计划前必须人工补充反证和风险。"))
    if len(strategies) == 1:
        items.append(CheckItem(code, "warning", "单策略候选，需要补齐其他维度证据后再进入交易计划。"))
    if len(dimensions - {"risk"}) < 2:
        items.append(CheckItem(code, "warning", "候选证据少于 2 个非风险维度，生成交易计划前需补齐基本面、趋势、估值、流动性、行业或事件证据。"))
    if "liquidity" not in dimensions:
        items.append(CheckItem(code, "warning", "缺少流动性证据，需确认成交额和买卖可执行性。"))
    if has_strategy(candidate, "value_quality") and "估值" not in candidate.get("reasons", "") and "分位" not in candidate.get("reasons", ""):
        items.append(CheckItem(code, "warning", "价值质量候选未看到估值分位证据。"))

    if not items:
        items.append(CheckItem(code, "info", "候选池证据门禁检查通过。"))
    return items


def check_candidates(candidates: list[dict[str, str]], context: CheckContext | None = None) -> dict[str, Any]:
    context = context or CheckContext()
    items: list[CheckItem] = []
    for candidate in candidates:
        items.extend(check_candidate(candidate, context))

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
        "context": {
            "tradable_universe_checked": context.tradable_codes is not None,
            "as_of": context.as_of.strftime("%Y-%m-%d") if context.as_of else None,
            "max_trend_age_days": context.max_trend_age_days,
        },
    }


def run_check(
    candidates_path: Path,
    tradable_universe_path: Path | None = None,
    as_of: datetime | None = None,
    max_trend_age_days: int = 5,
) -> dict[str, Any]:
    tradable_codes = read_code_set(tradable_universe_path) if tradable_universe_path else None
    context = CheckContext(tradable_codes=tradable_codes, as_of=as_of, max_trend_age_days=max_trend_age_days)
    return check_candidates(read_candidate_pool(candidates_path), context)


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
    parser.add_argument("--tradable-universe", help="Optional tradable universe CSV for hard membership checks.")
    parser.add_argument("--as-of", help="Optional YYYY-MM-DD check date for trend data freshness checks.")
    parser.add_argument("--max-trend-age-days", type=int, default=5, help="Warn when trend candidate trade_date is older than this many days.")
    parser.add_argument("--json", action="store_true", help="Print result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        as_of = datetime.strptime(args.as_of, "%Y-%m-%d") if args.as_of else None
        result = run_check(
            Path(args.candidates),
            Path(args.tradable_universe) if args.tradable_universe else None,
            as_of,
            args.max_trend_age_days,
        )
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
