#!/usr/bin/env python3
"""Add portfolio fit status to candidate pool rows."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, load_yaml
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml


PORTFOLIO_FIT_FIELDS = [
    "portfolio_fit_status",
    "portfolio_fit_action",
    "portfolio_fit_evidence",
    "current_stock_position_pct",
    "current_industry_position_pct",
    "current_total_position_pct",
    "expected_stock_position_pct_after_buy",
    "expected_industry_position_pct_after_buy",
    "expected_total_position_pct_after_buy",
]


def read_candidates(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), list(reader.fieldnames or [])


def write_candidates(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output_fields = list(fieldnames)
    for field in PORTFOLIO_FIT_FIELDS:
        if field not in output_fields:
            output_fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=output_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in output_fields})


def split_text(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def expand_existing_position_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            paths.extend(Path(match) for match in matches)
        elif not glob.has_magic(pattern):
            paths.append(Path(pattern))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def active_position(position: dict[str, Any]) -> bool:
    status = str(position.get("position", {}).get("status") or "").strip()
    return status not in {"closed", "exited", "archived"}


def position_context(position_paths: list[Path]) -> dict[str, Any]:
    total_pct = 0.0
    by_code: dict[str, float] = {}
    by_industry: dict[str, float] = {}
    loaded_paths: list[str] = []
    for path in position_paths:
        position = load_yaml(path)
        if not active_position(position):
            continue
        code = str(position.get("stock", {}).get("code") or "").strip()
        industry = str(position.get("stock", {}).get("industry") or "UNKNOWN").strip() or "UNKNOWN"
        position_pct = as_float(position.get("entry", {}).get("position_pct_of_total_assets"), 0.0) or 0.0
        total_pct += position_pct
        if code:
            by_code[code] = by_code.get(code, 0.0) + position_pct
        by_industry[industry] = by_industry.get(industry, 0.0) + position_pct
        loaded_paths.append(str(path))
    return {
        "position_count": len(loaded_paths),
        "position_paths": loaded_paths,
        "total_position_pct": round(total_pct, 4),
        "by_code": {code: round(value, 4) for code, value in sorted(by_code.items())},
        "by_industry": {industry: round(value, 4) for industry, value in sorted(by_industry.items())},
    }


def load_strategy_health(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return {
        str(item.get("strategy")): str(item.get("status"))
        for item in data.get("strategies", [])
        if isinstance(item, dict) and item.get("strategy")
    }


def candidate_strategies(candidate: dict[str, str]) -> list[str]:
    primary = (candidate.get("primary_strategy") or "").strip()
    strategies = split_text(candidate.get("strategies", ""))
    if primary and primary != "multi_strategy" and primary not in strategies:
        strategies.append(primary)
    return strategies


def strategy_fit_messages(strategy_health: dict[str, str], strategies: list[str]) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    for strategy in strategies:
        status = strategy_health.get(strategy)
        if status == "pause_new_entries":
            blockers.append(f"策略 {strategy} 已被策略健康检查暂停新开仓")
        elif status == "needs_review":
            warnings.append(f"策略 {strategy} 需要复核")
    return blockers, warnings


def format_pct(value: float) -> str:
    return f"{value:.2f}%"


def fit_candidate(
    candidate: dict[str, str],
    profile: dict[str, Any],
    context: dict[str, Any],
    strategy_health: dict[str, str],
    planned_position_pct: float,
) -> dict[str, Any]:
    risk = profile.get("risk", {})
    max_stock_pct = as_float(risk.get("max_position_pct_per_stock"), 100.0) or 100.0
    max_industry_pct = as_float(risk.get("max_position_pct_per_industry"), 100.0) or 100.0
    max_total_pct = as_float(risk.get("max_total_position_pct"), 100.0) or 100.0

    code = (candidate.get("code") or "").strip()
    industry = (candidate.get("industry") or "UNKNOWN").strip() or "UNKNOWN"
    current_stock_pct = float(context["by_code"].get(code, 0.0))
    current_industry_pct = float(context["by_industry"].get(industry, 0.0))
    current_total_pct = float(context["total_position_pct"])
    expected_stock_pct = round(current_stock_pct + planned_position_pct, 4)
    expected_industry_pct = round(current_industry_pct + planned_position_pct, 4)
    expected_total_pct = round(current_total_pct + planned_position_pct, 4)

    blockers: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    if current_stock_pct > 0:
        blockers.append(f"当前已持有 {code}，现有仓位 {format_pct(current_stock_pct)}")
    if expected_stock_pct > max_stock_pct:
        blockers.append(f"买入后单票仓位 {format_pct(expected_stock_pct)} 超过上限 {format_pct(max_stock_pct)}")
    if expected_industry_pct > max_industry_pct:
        blockers.append(f"买入后行业仓位 {format_pct(expected_industry_pct)} 超过上限 {format_pct(max_industry_pct)}")
    if expected_total_pct > max_total_pct:
        blockers.append(f"买入后总仓位 {format_pct(expected_total_pct)} 超过上限 {format_pct(max_total_pct)}")

    strategy_blockers, strategy_warnings = strategy_fit_messages(strategy_health, candidate_strategies(candidate))
    blockers.extend(strategy_blockers)
    warnings.extend(strategy_warnings)

    if current_industry_pct > 0 and expected_industry_pct <= max_industry_pct:
        warnings.append(f"当前已有 {industry} 行业仓位 {format_pct(current_industry_pct)}")
    if expected_industry_pct > max_industry_pct * 0.8 and expected_industry_pct <= max_industry_pct:
        warnings.append(f"买入后行业仓位 {format_pct(expected_industry_pct)} 接近上限 {format_pct(max_industry_pct)}")
    if expected_total_pct > max_total_pct * 0.8 and expected_total_pct <= max_total_pct:
        warnings.append(f"买入后总仓位 {format_pct(expected_total_pct)} 接近上限 {format_pct(max_total_pct)}")
    if context["position_count"] == 0:
        info.append("未读取到当前持仓，组合适配只按空仓测算")

    if blockers:
        status = "deferred_by_portfolio"
        action = "defer"
    elif warnings:
        status = "watch"
        action = "manual_review"
    else:
        status = "ready_for_plan"
        action = "prepare_trade_plan"

    evidence_parts = [f"[阻断] {item}" for item in blockers]
    evidence_parts.extend(f"[提醒] {item}" for item in warnings)
    evidence_parts.extend(f"[信息] {item}" for item in info)
    if not evidence_parts:
        evidence_parts.append("组合仓位、行业暴露和策略健康检查未发现阻断。")

    enriched = dict(candidate)
    enriched.update(
        {
            "portfolio_fit_status": status,
            "portfolio_fit_action": action,
            "portfolio_fit_evidence": " | ".join(evidence_parts),
            "current_stock_position_pct": round(current_stock_pct, 4),
            "current_industry_position_pct": round(current_industry_pct, 4),
            "current_total_position_pct": round(current_total_pct, 4),
            "expected_stock_position_pct_after_buy": expected_stock_pct,
            "expected_industry_position_pct_after_buy": expected_industry_pct,
            "expected_total_position_pct_after_buy": expected_total_pct,
        }
    )
    return enriched


def apply_portfolio_fit(
    profile_path: Path,
    candidates_path: Path,
    output_path: Path,
    metadata_output: Path,
    position_patterns: list[str],
    planned_position_pct: float = 5.0,
    strategy_health_path: Path | None = None,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    candidates, fieldnames = read_candidates(candidates_path)
    position_paths = expand_existing_position_paths(position_patterns)
    context = position_context(position_paths)
    strategy_health = load_strategy_health(strategy_health_path)
    fitted = [
        fit_candidate(candidate, profile, context, strategy_health, planned_position_pct)
        for candidate in candidates
    ]
    write_candidates(output_path, fitted, fieldnames)

    status_counts: dict[str, int] = {}
    for candidate in fitted:
        status = candidate["portfolio_fit_status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile_path),
        "candidates": str(candidates_path),
        "output": str(output_path),
        "positions": context["position_paths"],
        "strategy_health": str(strategy_health_path) if strategy_health_path else None,
        "planned_position_pct": planned_position_pct,
        "candidate_count": len(fitted),
        "status_counts": dict(sorted(status_counts.items())),
        "portfolio_context": {
            "position_count": context["position_count"],
            "total_position_pct": context["total_position_pct"],
            "industry_position_pct": context["by_industry"],
        },
    }
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add portfolio fit status to candidate pool rows.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--candidates", default="data/processed/candidate_pool.csv", help="Input candidate pool CSV.")
    parser.add_argument("--output", default="data/processed/candidate_pool.portfolio_fit.csv", help="Output candidate pool CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/candidate_portfolio_fit.json", help="Output metadata JSON.")
    parser.add_argument("--positions", nargs="+", default=["positions/*.yaml"], help="Position YAML paths or glob patterns.")
    parser.add_argument("--planned-position-pct", type=float, default=5.0, help="Assumed new position percent for fit checks.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Optional strategy health JSON.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = apply_portfolio_fit(
            Path(args.profile),
            Path(args.candidates),
            Path(args.output),
            Path(args.metadata_output),
            args.positions,
            args.planned_position_pct,
            Path(args.strategy_health) if args.strategy_health else None,
        )
    except Exception as exc:
        print(f"candidate portfolio fit failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"candidate rows: {metadata['candidate_count']}")
        print(f"status counts: {metadata['status_counts']}")
        print(f"output: {metadata['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
