#!/usr/bin/env python3
"""Screen trend strength candidates from trend factor snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.import_stock_universe import parse_bool
    from tools.risk_check import as_float, load_yaml
except ModuleNotFoundError:
    from import_stock_universe import parse_bool
    from risk_check import as_float, load_yaml


OUTPUT_FIELDS = [
    "code",
    "trade_date",
    "score",
    "close",
    "return",
    "ma",
    "above_ma",
    "turnover_avg",
    "reasons",
    "risks",
]


def read_factors(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def trend_screening_config(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("strategies", {}).get("trend_strength", {}).get("screening", {})


def bool_from_row(row: dict[str, str], field: str) -> bool:
    return parse_bool(str(row.get(field, "false")), field, 0)


def candidate_from_row(row: dict[str, str], config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    window = int(config.get("window", 20))
    return_field = f"return_{window}d"
    ma_field = f"ma_{window}"
    above_ma_field = f"above_ma_{window}"
    turnover_field = f"turnover_avg_{window}"

    risks: list[str] = []
    missing_fields = [field for field in (return_field, ma_field, above_ma_field, turnover_field) if field not in row or row.get(field) == ""]
    if missing_fields:
        return None, [f"数据不足：缺少 {', '.join(missing_fields)}。"]

    is_suspended = bool_from_row(row, "is_suspended")
    is_limit_up = bool_from_row(row, "is_limit_up")
    is_limit_down = bool_from_row(row, "is_limit_down")
    if config.get("exclude_suspended", True) and is_suspended:
        return None, ["停牌，排除。"]
    if config.get("exclude_limit_up", False) and is_limit_up:
        return None, ["涨停，排除。"]
    if config.get("exclude_limit_down", True) and is_limit_down:
        return None, ["跌停，排除。"]

    return_pct = as_float(row.get(return_field))
    moving_average = as_float(row.get(ma_field))
    turnover_avg = as_float(row.get(turnover_field))
    close = as_float(row.get("close"))
    above_ma = bool_from_row(row, above_ma_field)
    min_return_pct = as_float(config.get("min_return_pct"), 0.0) or 0.0

    if return_pct is None or return_pct < min_return_pct:
        return None, [f"区间收益率 {return_pct if return_pct is not None else '-'}% 低于阈值 {min_return_pct}%。"]
    if config.get("require_above_ma", True) and not above_ma:
        return None, [f"最新收盘价未站上 MA{window}。"]

    reasons = [
        f"近 {window} 日收益率 {return_pct:.2f}% >= {min_return_pct:.2f}%。",
        f"最新收盘价 {'站上' if above_ma else '未站上'} MA{window}。",
    ]
    if turnover_avg is not None:
        reasons.append(f"近 {window} 日平均成交额 {turnover_avg:.0f}。")

    if is_limit_up:
        risks.append("最新交易日涨停，追高风险需要额外确认。")
    if is_limit_down:
        risks.append("最新交易日跌停，流动性和止损执行风险较高。")
    if is_suspended:
        risks.append("最新交易日停牌，无法正常交易。")

    score = return_pct + (5.0 if above_ma else 0.0)
    if turnover_avg is not None:
        score += min(turnover_avg / 1_000_000_000, 5.0)

    return (
        {
            "code": row.get("code", ""),
            "trade_date": row.get("trade_date", ""),
            "score": round(score, 6),
            "close": row.get("close", ""),
            "return": round(return_pct, 6),
            "ma": "" if moving_average is None else round(moving_average, 6),
            "above_ma": above_ma,
            "turnover_avg": "" if turnover_avg is None else round(turnover_avg, 6),
            "reasons": " | ".join(reasons),
            "risks": " | ".join(risks),
        },
        [],
    )


def screen_candidates(factor_rows: list[dict[str, str]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for row in factor_rows:
        candidate, reasons = candidate_from_row(row, config)
        if candidate is None:
            exclusions.append({"code": row.get("code", ""), "trade_date": row.get("trade_date", ""), "reasons": reasons})
        else:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-float(item["score"]), item["code"]))
    max_candidates = int(config.get("max_candidates", 20))
    return candidates[:max_candidates], exclusions


def write_candidates(path: Path, candidates: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({field: candidate.get(field, "") for field in OUTPUT_FIELDS})


def build_metadata(
    profile_path: Path,
    factors_path: Path,
    output_path: Path,
    config: dict[str, Any],
    factor_rows: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "screened_at": datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile_path),
        "factors": str(factors_path),
        "output": str(output_path),
        "strategy": "trend_strength",
        "config": config,
        "input_count": len(factor_rows),
        "candidate_count": len(candidates),
        "excluded_count": len(exclusions),
        "exclusions": exclusions,
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run_screen(
    profile_path: Path,
    factors_path: Path,
    output_path: Path,
    metadata_path: Path,
    window_override: int | None = None,
    max_candidates_override: int | None = None,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    config = dict(trend_screening_config(profile))
    if window_override is not None:
        config["window"] = window_override
    if max_candidates_override is not None:
        config["max_candidates"] = max_candidates_override
    factor_rows = read_factors(factors_path)
    candidates, exclusions = screen_candidates(factor_rows, config)
    write_candidates(output_path, candidates)
    metadata = build_metadata(profile_path, factors_path, output_path, config, factor_rows, candidates, exclusions)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"strategy: {metadata['strategy']}")
    print(f"input rows: {metadata['input_count']}")
    print(f"candidate rows: {metadata['candidate_count']}")
    print(f"excluded rows: {metadata['excluded_count']}")
    print(f"window: {metadata['config'].get('window')}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen trend strength candidates.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--factors", default="data/processed/trend_factors.csv", help="Input trend factors CSV.")
    parser.add_argument("--output", default="data/processed/trend_candidates.csv", help="Output trend candidates CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/trend_candidates.json", help="Screen metadata JSON.")
    parser.add_argument("--window", type=int, help="Override screening window.")
    parser.add_argument("--max-candidates", type=int, help="Override max candidates.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_screen(
            Path(args.profile),
            Path(args.factors),
            Path(args.output),
            Path(args.metadata_output),
            window_override=args.window,
            max_candidates_override=args.max_candidates,
        )
    except Exception as exc:
        print(f"trend strength screening failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

