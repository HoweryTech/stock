#!/usr/bin/env python3
"""Screen value quality candidates from financial metrics snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, load_yaml
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml


OUTPUT_FIELDS = [
    "code",
    "report_period",
    "score",
    "roe",
    "roa",
    "gross_margin",
    "net_margin",
    "debt_ratio",
    "operating_cash_flow",
    "revenue_growth_yoy",
    "net_profit_growth_yoy",
    "deducted_net_profit_growth_yoy",
    "eps",
    "reasons",
    "risks",
]

DEFAULT_SCREENING_CONFIG = {
    "min_roe": 4.0,
    "min_roa": 1.0,
    "min_operating_cash_flow": 0.0,
    "max_debt_ratio": 75.0,
    "min_revenue_growth_yoy": 0.0,
    "min_deducted_net_profit_growth_yoy": 0.0,
    "min_gross_margin": 0.0,
    "max_candidates": 20,
}


def read_financial_metrics(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def value_quality_screening_config(profile: dict[str, Any]) -> dict[str, Any]:
    config = dict(DEFAULT_SCREENING_CONFIG)
    configured = profile.get("strategies", {}).get("value_quality", {}).get("screening", {})
    config.update(configured or {})
    return config


def latest_rows_by_code(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for row in sorted(rows, key=lambda item: (item.get("code", ""), item.get("report_period", ""))):
        code = row.get("code", "")
        if code:
            latest[code] = row
    return list(latest.values())


def number_from_row(row: dict[str, str], field: str) -> float | None:
    return as_float(row.get(field))


def require_min(row: dict[str, str], field: str, threshold: float, label: str) -> str | None:
    value = number_from_row(row, field)
    if value is None:
        return f"{label}缺失，无法验证。"
    if value < threshold:
        return f"{label} {value:.2f} 低于阈值 {threshold:.2f}。"
    return None


def require_max(row: dict[str, str], field: str, threshold: float, label: str) -> str | None:
    value = number_from_row(row, field)
    if value is None:
        return f"{label}缺失，无法验证。"
    if value > threshold:
        return f"{label} {value:.2f} 高于阈值 {threshold:.2f}。"
    return None


def score_candidate(row: dict[str, str]) -> float:
    roe = number_from_row(row, "roe") or 0.0
    roa = number_from_row(row, "roa") or 0.0
    gross_margin = number_from_row(row, "gross_margin") or 0.0
    debt_ratio = number_from_row(row, "debt_ratio") or 0.0
    revenue_growth = number_from_row(row, "revenue_growth_yoy") or 0.0
    deducted_growth = number_from_row(row, "deducted_net_profit_growth_yoy") or 0.0
    operating_cash_flow = number_from_row(row, "operating_cash_flow") or 0.0

    score = roe + roa
    score += max(gross_margin, 0.0) * 0.05
    score += max(revenue_growth, 0.0) * 0.2
    score += max(deducted_growth, 0.0) * 0.2
    score -= max(debt_ratio - 60.0, 0.0) * 0.1
    if operating_cash_flow > 0:
        score += 2.0
    return round(score, 6)


def candidate_from_row(row: dict[str, str], config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    checks = [
        require_min(row, "roe", float(config["min_roe"]), "ROE"),
        require_min(row, "roa", float(config["min_roa"]), "ROA"),
        require_min(row, "operating_cash_flow", float(config["min_operating_cash_flow"]), "经营现金流净额"),
        require_max(row, "debt_ratio", float(config["max_debt_ratio"]), "资产负债率"),
        require_min(row, "revenue_growth_yoy", float(config["min_revenue_growth_yoy"]), "营收同比"),
        require_min(row, "deducted_net_profit_growth_yoy", float(config["min_deducted_net_profit_growth_yoy"]), "扣非净利润同比"),
        require_min(row, "gross_margin", float(config["min_gross_margin"]), "毛利率"),
    ]
    exclusions = [check for check in checks if check]
    if exclusions:
        return None, exclusions

    roe = number_from_row(row, "roe") or 0.0
    roa = number_from_row(row, "roa") or 0.0
    debt_ratio = number_from_row(row, "debt_ratio") or 0.0
    operating_cash_flow = number_from_row(row, "operating_cash_flow") or 0.0
    revenue_growth = number_from_row(row, "revenue_growth_yoy") or 0.0
    deducted_growth = number_from_row(row, "deducted_net_profit_growth_yoy") or 0.0
    gross_margin = number_from_row(row, "gross_margin") or 0.0

    reasons = [
        f"ROE {roe:.2f}% >= {float(config['min_roe']):.2f}%。",
        f"ROA {roa:.2f}% >= {float(config['min_roa']):.2f}%。",
        f"经营现金流净额 {operating_cash_flow:.0f} >= {float(config['min_operating_cash_flow']):.0f}。",
        f"资产负债率 {debt_ratio:.2f}% <= {float(config['max_debt_ratio']):.2f}%。",
        f"营收同比 {revenue_growth:.2f}% >= {float(config['min_revenue_growth_yoy']):.2f}%。",
        f"扣非净利润同比 {deducted_growth:.2f}% >= {float(config['min_deducted_net_profit_growth_yoy']):.2f}%。",
    ]
    risks: list[str] = []
    if debt_ratio > 70:
        risks.append("资产负债率接近上限，需核查行业属性和偿债压力。")
    if gross_margin == 0:
        risks.append("毛利率为 0，需确认是否为金融行业口径或数据缺失。")

    return (
        {
            "code": row.get("code", ""),
            "report_period": row.get("report_period", ""),
            "score": score_candidate(row),
            "roe": row.get("roe", ""),
            "roa": row.get("roa", ""),
            "gross_margin": row.get("gross_margin", ""),
            "net_margin": row.get("net_margin", ""),
            "debt_ratio": row.get("debt_ratio", ""),
            "operating_cash_flow": row.get("operating_cash_flow", ""),
            "revenue_growth_yoy": row.get("revenue_growth_yoy", ""),
            "net_profit_growth_yoy": row.get("net_profit_growth_yoy", ""),
            "deducted_net_profit_growth_yoy": row.get("deducted_net_profit_growth_yoy", ""),
            "eps": row.get("eps", ""),
            "reasons": " | ".join(reasons),
            "risks": " | ".join(risks),
        },
        [],
    )


def screen_candidates(metric_rows: list[dict[str, str]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []

    for row in latest_rows_by_code(metric_rows):
        candidate, reasons = candidate_from_row(row, config)
        if candidate is None:
            exclusions.append({"code": row.get("code", ""), "report_period": row.get("report_period", ""), "reasons": reasons})
        else:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-float(item["score"]), item["code"]))
    return candidates[: int(config.get("max_candidates", 20))], exclusions


def write_candidates(path: Path, candidates: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({field: candidate.get(field, "") for field in OUTPUT_FIELDS})


def build_metadata(
    profile_path: Path,
    metrics_path: Path,
    output_path: Path,
    config: dict[str, Any],
    metric_rows: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    periods = sorted({row.get("report_period", "") for row in metric_rows if row.get("report_period")})
    return {
        "screened_at": datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile_path),
        "financial_metrics": str(metrics_path),
        "output": str(output_path),
        "strategy": "value_quality",
        "config": config,
        "input_count": len(metric_rows),
        "latest_code_count": len(latest_rows_by_code(metric_rows)),
        "candidate_count": len(candidates),
        "excluded_count": len(exclusions),
        "start_period": periods[0] if periods else None,
        "end_period": periods[-1] if periods else None,
        "exclusions": exclusions,
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run_screen(
    profile_path: Path,
    metrics_path: Path,
    output_path: Path,
    metadata_path: Path,
    max_candidates_override: int | None = None,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    config = value_quality_screening_config(profile)
    if max_candidates_override is not None:
        config["max_candidates"] = max_candidates_override
    metric_rows = read_financial_metrics(metrics_path)
    candidates, exclusions = screen_candidates(metric_rows, config)
    write_candidates(output_path, candidates)
    metadata = build_metadata(profile_path, metrics_path, output_path, config, metric_rows, candidates, exclusions)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"strategy: {metadata['strategy']}")
    print(f"input rows: {metadata['input_count']}")
    print(f"latest codes: {metadata['latest_code_count']}")
    print(f"candidate rows: {metadata['candidate_count']}")
    print(f"excluded rows: {metadata['excluded_count']}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen value quality candidates.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--financial-metrics", default="data/processed/financial_metrics.csv", help="Input financial metrics CSV.")
    parser.add_argument("--output", default="data/processed/value_quality_candidates.csv", help="Output value quality candidates CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/value_quality_candidates.json", help="Screen metadata JSON.")
    parser.add_argument("--max-candidates", type=int, help="Override max candidates.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_screen(
            Path(args.profile),
            Path(args.financial_metrics),
            Path(args.output),
            Path(args.metadata_output),
            args.max_candidates,
        )
    except Exception as exc:
        print(f"value quality screening failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
