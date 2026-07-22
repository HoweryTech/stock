#!/usr/bin/env python3
"""Merge strategy candidate CSV files into one auditable candidate pool."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float
except ModuleNotFoundError:
    from risk_check import as_float


OUTPUT_FIELDS = [
    "code",
    "name",
    "industry",
    "exchange",
    "board",
    "strategies",
    "strategy_count",
    "combined_score",
    "primary_strategy",
    "strategy_confluence_score",
    "strategy_confluence_evidence",
    "trend_score",
    "value_quality_score",
    "event_score",
    "event_date",
    "event_type",
    "liquidity_score",
    "liquidity_evidence",
    "industry_strength_score",
    "industry_strength_evidence",
    "data_quality_score",
    "data_quality_status",
    "data_quality_evidence",
    "risk_penalty_score",
    "risk_penalty_evidence",
    "trade_date",
    "report_period",
    "reasons",
    "risks",
]


def read_candidates(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_universe_context(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            (row.get("code") or "").strip(): row
            for row in csv.DictReader(file)
            if (row.get("code") or "").strip()
        }


def read_row_context(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            (row.get("code") or "").strip(): row
            for row in csv.DictReader(file)
            if (row.get("code") or "").strip()
        }


def split_text(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def prefixed_text(strategy: str, value: str) -> list[str]:
    return [f"[{strategy}] {part}" for part in split_text(value)]


def format_amount(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 2)
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def infer_board(code: str, exchange: str = "") -> str:
    code = (code or "").strip()
    exchange = (exchange or "").strip().upper()
    if exchange == "BSE":
        return "bse"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("600", "601", "603", "605")):
        return "sse_main"
    if code.startswith(("000", "001", "002", "003")):
        return "szse_main"
    return "unknown"


def add_strategy_candidate(pool: dict[str, dict[str, Any]], strategy: str, row: dict[str, str]) -> None:
    code = row.get("code", "").strip()
    if not code:
        return

    candidate = pool.setdefault(
        code,
        {
            "code": code,
            "strategies": [],
            "trend_score": "",
            "value_quality_score": "",
            "event_score": "",
            "event_date": "",
            "event_type": "",
            "trend_turnover_avg": "",
            "trade_date": "",
            "report_period": "",
            "reasons": [],
            "risks": [],
        },
    )
    if strategy not in candidate["strategies"]:
        candidate["strategies"].append(strategy)

    score = row.get("score", "")
    if strategy == "trend_strength":
        candidate["trend_score"] = score
        candidate["trade_date"] = row.get("trade_date", candidate["trade_date"])
        candidate["trend_turnover_avg"] = row.get("turnover_avg", candidate.get("trend_turnover_avg", ""))
    elif strategy == "value_quality":
        candidate["value_quality_score"] = score
        candidate["report_period"] = row.get("report_period", candidate["report_period"])
    elif strategy == "event_catalyst":
        candidate["event_score"] = score
        candidate["event_date"] = row.get("event_date", candidate.get("event_date", ""))
        candidate["event_type"] = row.get("event_type", candidate.get("event_type", ""))

    candidate["reasons"].extend(prefixed_text(strategy, row.get("reasons", "")))
    candidate["risks"].extend(prefixed_text(strategy, row.get("risks", "")))


def primary_strategy(candidate: dict[str, Any]) -> str:
    strategies = sorted(candidate["strategies"])
    if len(strategies) > 1:
        return "multi_strategy"
    return strategies[0] if strategies else ""


def liquidity_fields(candidate: dict[str, Any], universe_row: dict[str, str] | None = None) -> tuple[str, str]:
    universe_row = universe_row or {}
    trend_turnover = as_float(candidate.get("trend_turnover_avg"))
    universe_turnover = as_float(universe_row.get("avg_daily_turnover_cny"))
    selected_turnover = trend_turnover if trend_turnover is not None else universe_turnover
    if selected_turnover is None:
        return "", ""

    score = min(max(selected_turnover / 1_000_000_000 * 100.0, 0.0), 100.0)
    evidence_parts: list[str] = []
    if trend_turnover is not None:
        evidence_parts.append(f"趋势窗口平均成交额 {format_amount(trend_turnover)}")
    if universe_turnover is not None:
        evidence_parts.append(f"股票池平均成交额 {format_amount(universe_turnover)}")
    return str(round(score, 6)), "；".join(evidence_parts)


def strategy_confluence_fields(strategies: list[str]) -> tuple[float, str]:
    strategy_count = len(strategies)
    score = strategy_count * 100.0
    if strategy_count >= 2:
        evidence = f"命中 {strategy_count} 个策略：{', '.join(strategies)}"
    elif strategy_count == 1:
        evidence = f"单策略来源：{strategies[0]}"
    else:
        evidence = "缺少策略来源"
    return score, evidence


def risk_penalty_fields(candidate: dict[str, Any]) -> tuple[float, str]:
    risks = candidate.get("risks") or []
    if not risks:
        return 0.0, "未提供显式风险，门禁会要求人工补充。"

    risk_text = " ".join(str(item) for item in risks)
    penalty = min(len(risks) * 3.0, 12.0)
    severe_keywords = ("退市", "ST", "停牌", "减持", "解禁", "问询", "延期", "高估", "追高", "流动性")
    matched = [keyword for keyword in severe_keywords if keyword in risk_text]
    if matched:
        penalty += min(len(matched) * 4.0, 16.0)
    evidence = f"风险提示 {len(risks)} 条"
    if matched:
        evidence += f"，命中高关注关键词：{', '.join(matched)}"
    return round(-penalty, 6), evidence


def data_quality_fields(
    candidate: dict[str, Any],
    strategies: list[str],
    universe_row: dict[str, str],
    industry_row: dict[str, str],
    liquidity_score: str,
) -> tuple[float, str, str]:
    checks: list[tuple[bool, str]] = [
        (bool(candidate.get("reasons")), "入选证据"),
        (bool(candidate.get("risks")), "风险提示"),
        (bool(universe_row.get("name")), "股票名称"),
        (bool(universe_row.get("industry")), "行业"),
        (bool(liquidity_score), "流动性"),
        (bool(industry_row.get("industry_strength_score")), "行业强度"),
    ]
    if "trend_strength" in strategies:
        checks.extend(
            [
                (bool(candidate.get("trend_score")), "趋势分"),
                (bool(candidate.get("trade_date")), "趋势交易日"),
            ]
        )
    if "value_quality" in strategies:
        checks.extend(
            [
                (bool(candidate.get("value_quality_score")), "价值质量分"),
                (bool(candidate.get("report_period")), "报告期"),
            ]
        )
    if "event_catalyst" in strategies:
        checks.extend(
            [
                (bool(candidate.get("event_score")), "事件分"),
                (bool(candidate.get("event_date")), "事件日期"),
            ]
        )

    passed = [label for ok, label in checks if ok]
    missing = [label for ok, label in checks if not ok]
    score = round(len(passed) / len(checks) * 20.0, 6) if checks else 0.0
    if score >= 18:
        status = "complete"
    elif score >= 14:
        status = "partial"
    else:
        status = "weak"
    evidence = f"已具备：{', '.join(passed) if passed else '-'}"
    if missing:
        evidence += f"；缺失：{', '.join(missing)}"
    return score, status, evidence


def score_component(value: Any, weight: float = 1.0) -> float:
    return (as_float(value, 0.0) or 0.0) * weight


def combined_score_from_components(candidate: dict[str, Any]) -> float:
    return round(
        score_component(candidate.get("strategy_confluence_score"))
        + score_component(candidate.get("trend_score"))
        + score_component(candidate.get("value_quality_score"))
        + score_component(candidate.get("event_score"))
        + score_component(candidate.get("industry_strength_score"), 0.2)
        + score_component(candidate.get("liquidity_score"), 0.1)
        + score_component(candidate.get("data_quality_score"))
        + score_component(candidate.get("risk_penalty_score")),
        6,
    )


def finalize_candidate(
    candidate: dict[str, Any],
    universe_row: dict[str, str] | None = None,
    industry_row: dict[str, str] | None = None,
) -> dict[str, Any]:
    strategies = sorted(candidate["strategies"])
    universe_row = universe_row or {}
    industry_row = industry_row or {}
    liquidity_score, liquidity_evidence = liquidity_fields(candidate, universe_row)
    strategy_confluence_score, strategy_confluence_evidence = strategy_confluence_fields(strategies)
    data_quality_score, data_quality_status, data_quality_evidence = data_quality_fields(
        candidate,
        strategies,
        universe_row,
        industry_row,
        liquidity_score,
    )
    risk_penalty_score, risk_penalty_evidence = risk_penalty_fields(candidate)
    enriched_candidate = {
        **candidate,
        "strategy_confluence_score": strategy_confluence_score,
        "liquidity_score": liquidity_score,
        "industry_strength_score": industry_row.get("industry_strength_score", ""),
        "data_quality_score": data_quality_score,
        "risk_penalty_score": risk_penalty_score,
    }
    return {
        "code": candidate["code"],
        "name": universe_row.get("name", ""),
        "industry": universe_row.get("industry", ""),
        "exchange": universe_row.get("exchange", ""),
        "board": infer_board(candidate["code"], universe_row.get("exchange", "")),
        "strategies": "|".join(strategies),
        "strategy_count": len(strategies),
        "combined_score": combined_score_from_components(enriched_candidate),
        "primary_strategy": primary_strategy(candidate),
        "strategy_confluence_score": strategy_confluence_score,
        "strategy_confluence_evidence": strategy_confluence_evidence,
        "trend_score": candidate.get("trend_score", ""),
        "value_quality_score": candidate.get("value_quality_score", ""),
        "event_score": candidate.get("event_score", ""),
        "event_date": candidate.get("event_date", ""),
        "event_type": candidate.get("event_type", ""),
        "liquidity_score": liquidity_score,
        "liquidity_evidence": liquidity_evidence,
        "industry_strength_score": industry_row.get("industry_strength_score", ""),
        "industry_strength_evidence": industry_row.get("industry_strength_evidence", ""),
        "data_quality_score": data_quality_score,
        "data_quality_status": data_quality_status,
        "data_quality_evidence": data_quality_evidence,
        "risk_penalty_score": risk_penalty_score,
        "risk_penalty_evidence": risk_penalty_evidence,
        "trade_date": candidate.get("trade_date", ""),
        "report_period": candidate.get("report_period", ""),
        "reasons": " | ".join(candidate["reasons"]),
        "risks": " | ".join(candidate["risks"]),
    }


def merge_candidates(
    trend_rows: list[dict[str, str]],
    value_quality_rows: list[dict[str, str]],
    event_rows: list[dict[str, str]] | None = None,
    universe_context: dict[str, dict[str, str]] | None = None,
    industry_context: dict[str, dict[str, str]] | None = None,
    max_candidates: int | None = None,
) -> list[dict[str, Any]]:
    universe_context = universe_context or {}
    industry_context = industry_context or {}
    pool: dict[str, dict[str, Any]] = {}
    for row in trend_rows:
        add_strategy_candidate(pool, "trend_strength", row)
    for row in value_quality_rows:
        add_strategy_candidate(pool, "value_quality", row)
    for row in event_rows or []:
        add_strategy_candidate(pool, "event_catalyst", row)

    candidates = [
        finalize_candidate(candidate, universe_context.get(candidate["code"]), industry_context.get(candidate["code"]))
        for candidate in pool.values()
    ]
    candidates.sort(key=lambda item: (-float(item["combined_score"]), item["code"]))
    return candidates[:max_candidates] if max_candidates else candidates


def write_candidates(path: Path, candidates: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({field: candidate.get(field, "") for field in OUTPUT_FIELDS})


def build_metadata(
    trend_path: Path,
    value_quality_path: Path,
    event_path: Path | None,
    universe_path: Path | None,
    industry_strength_path: Path | None,
    output_path: Path,
    trend_rows: list[dict[str, str]],
    value_quality_rows: list[dict[str, str]],
    event_rows: list[dict[str, str]],
    universe_context: dict[str, dict[str, str]],
    industry_context: dict[str, dict[str, str]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "merged_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "trend_strength": str(trend_path),
            "value_quality": str(value_quality_path),
            "event_catalyst": str(event_path) if event_path else None,
            "universe": str(universe_path) if universe_path else None,
            "industry_strength": str(industry_strength_path) if industry_strength_path else None,
        },
        "output": str(output_path),
        "input_counts": {
            "trend_strength": len(trend_rows),
            "value_quality": len(value_quality_rows),
            "event_catalyst": len(event_rows),
            "universe": len(universe_context),
            "industry_strength": len(industry_context),
        },
        "candidate_count": len(candidates),
        "multi_strategy_count": sum(1 for candidate in candidates if candidate["primary_strategy"] == "multi_strategy"),
        "enriched_count": sum(1 for candidate in candidates if candidate.get("name") or candidate.get("industry")),
        "liquidity_scored_count": sum(1 for candidate in candidates if candidate.get("liquidity_score") != ""),
        "industry_strength_scored_count": sum(1 for candidate in candidates if candidate.get("industry_strength_score") != ""),
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run_merge(
    trend_path: Path,
    value_quality_path: Path,
    output_path: Path,
    metadata_path: Path,
    event_path: Path | None = None,
    universe_path: Path | None = None,
    industry_strength_path: Path | None = None,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    trend_rows = read_candidates(trend_path)
    value_quality_rows = read_candidates(value_quality_path)
    event_rows = read_candidates(event_path) if event_path else []
    universe_context = read_universe_context(universe_path)
    industry_context = read_row_context(industry_strength_path)
    candidates = merge_candidates(trend_rows, value_quality_rows, event_rows, universe_context, industry_context, max_candidates)
    write_candidates(output_path, candidates)
    metadata = build_metadata(
        trend_path,
        value_quality_path,
        event_path,
        universe_path,
        industry_strength_path,
        output_path,
        trend_rows,
        value_quality_rows,
        event_rows,
        universe_context,
        industry_context,
        candidates,
    )
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"trend rows: {metadata['input_counts']['trend_strength']}")
    print(f"value quality rows: {metadata['input_counts']['value_quality']}")
    print(f"candidate rows: {metadata['candidate_count']}")
    print(f"multi-strategy rows: {metadata['multi_strategy_count']}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge strategy candidates into a unified candidate pool.")
    parser.add_argument("--trend-candidates", default="data/processed/trend_candidates.csv", help="Input trend candidates CSV.")
    parser.add_argument(
        "--value-quality-candidates",
        default="data/processed/value_quality_candidates.csv",
        help="Input value quality candidates CSV.",
    )
    parser.add_argument("--output", default="data/processed/candidate_pool.csv", help="Output merged candidate pool CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/candidate_pool.json", help="Merge metadata JSON.")
    parser.add_argument("--event-candidates", help="Optional event catalyst candidate CSV.")
    parser.add_argument("--universe", help="Optional stock universe or tradable universe CSV for name, industry, and liquidity enrichment.")
    parser.add_argument("--industry-strength", help="Optional industry strength factor CSV.")
    parser.add_argument("--max-candidates", type=int, help="Limit output candidate count.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_merge(
            Path(args.trend_candidates),
            Path(args.value_quality_candidates),
            Path(args.output),
            Path(args.metadata_output),
            Path(args.event_candidates) if args.event_candidates else None,
            Path(args.universe) if args.universe else None,
            Path(args.industry_strength) if args.industry_strength else None,
            args.max_candidates,
        )
    except Exception as exc:
        print(f"candidate pool merge failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
