#!/usr/bin/env python3
"""Screen event catalyst candidates from structured event records."""

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
    "event_date",
    "event_type",
    "score",
    "title",
    "expected_impact",
    "counter_evidence",
    "risk_disclosure",
    "reasons",
    "risks",
]

DEFAULT_SCREENING_CONFIG = {
    "min_impact_score": 3.0,
    "min_confidence": 2.0,
    "max_candidates": 20,
}

RISK_EVENT_TYPES = {
    "regulatory_inquiry",
    "shareholder_reduce",
    "unlock",
    "litigation",
    "financial_correction",
}


def read_events(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def event_screening_config(profile: dict[str, Any]) -> dict[str, Any]:
    config = dict(DEFAULT_SCREENING_CONFIG)
    strategy = profile.get("strategies", {}).get("event_catalyst", {})
    config["supported_event_types"] = strategy.get("supported_event_types", [])
    config.update(strategy.get("screening", {}) or {})
    return config


def number_from_row(row: dict[str, str], field: str) -> float | None:
    return as_float(row.get(field))


def score_event(row: dict[str, str]) -> float:
    impact_score = number_from_row(row, "impact_score") or 0.0
    confidence = number_from_row(row, "confidence") or 0.0
    return round(impact_score * 10.0 + confidence * 2.0, 6)


def candidate_from_row(row: dict[str, str], config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    code = (row.get("code") or "").strip()
    event_type = (row.get("event_type") or "").strip()
    title = (row.get("title") or "").strip()
    expected_impact = (row.get("expected_impact") or "").strip()
    counter_evidence = (row.get("counter_evidence") or "").strip()
    risk_disclosure = (row.get("risk_disclosure") or "").strip()
    impact_score = number_from_row(row, "impact_score")
    confidence = number_from_row(row, "confidence")

    exclusions: list[str] = []
    if not code:
        exclusions.append("缺少股票代码。")
    if not row.get("event_date"):
        exclusions.append("缺少事件日期。")
    if not event_type:
        exclusions.append("缺少事件类型。")
    supported = set(config.get("supported_event_types") or [])
    if supported and event_type not in supported:
        exclusions.append(f"事件类型 {event_type} 不在支持列表中。")
    if event_type in RISK_EVENT_TYPES:
        exclusions.append(f"事件类型 {event_type} 是风险事件，只进入风险复核，不生成事件催化候选。")
    if not title:
        exclusions.append("缺少事件标题。")
    if not expected_impact:
        exclusions.append("缺少预期影响说明。")
    if impact_score is None:
        exclusions.append("缺少影响评分。")
    elif impact_score < float(config.get("min_impact_score", 3.0)):
        exclusions.append(f"影响评分 {impact_score:.2f} 低于阈值 {float(config.get('min_impact_score', 3.0)):.2f}。")
    if confidence is None:
        exclusions.append("缺少可信度评分。")
    elif confidence < float(config.get("min_confidence", 2.0)):
        exclusions.append(f"可信度评分 {confidence:.2f} 低于阈值 {float(config.get('min_confidence', 2.0)):.2f}。")
    if not counter_evidence:
        exclusions.append("缺少反证检查。")
    if not risk_disclosure:
        exclusions.append("缺少风险披露。")
    if exclusions:
        return None, exclusions

    reasons = [
        f"事件类型 {event_type}。",
        f"事件标题：{title}。",
        f"预期影响：{expected_impact}。",
        f"影响评分 {impact_score:.2f}，可信度 {confidence:.2f}。",
    ]
    risks = [
        f"反证：{counter_evidence}。",
        f"风险披露：{risk_disclosure}。",
    ]
    return (
        {
            "code": code,
            "event_date": row.get("event_date", ""),
            "event_type": event_type,
            "score": score_event(row),
            "title": title,
            "expected_impact": expected_impact,
            "counter_evidence": counter_evidence,
            "risk_disclosure": risk_disclosure,
            "reasons": " | ".join(reasons),
            "risks": " | ".join(risks),
        },
        [],
    )


def screen_candidates(rows: list[dict[str, str]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for row in rows:
        candidate, reasons = candidate_from_row(row, config)
        if candidate is None:
            exclusions.append({"code": row.get("code", ""), "event_date": row.get("event_date", ""), "event_type": row.get("event_type", ""), "reasons": reasons})
        else:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-float(item["score"]), item["code"], item["event_date"]))
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
    events_path: Path,
    output_path: Path,
    config: dict[str, Any],
    rows: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "screened_at": datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile_path),
        "events": str(events_path),
        "output": str(output_path),
        "strategy": "event_catalyst",
        "config": config,
        "input_count": len(rows),
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
    events_path: Path,
    output_path: Path,
    metadata_path: Path,
    max_candidates_override: int | None = None,
) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    config = event_screening_config(profile)
    if max_candidates_override is not None:
        config["max_candidates"] = max_candidates_override
    rows = read_events(events_path)
    candidates, exclusions = screen_candidates(rows, config)
    write_candidates(output_path, candidates)
    metadata = build_metadata(profile_path, events_path, output_path, config, rows, candidates, exclusions)
    write_metadata(metadata_path, metadata)
    return metadata


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"strategy: {metadata['strategy']}")
    print(f"input rows: {metadata['input_count']}")
    print(f"candidate rows: {metadata['candidate_count']}")
    print(f"excluded rows: {metadata['excluded_count']}")
    print(f"output: {metadata['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen event catalyst candidates.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Path to investment profile YAML.")
    parser.add_argument("--events", default="data/processed/event_catalyst_events.csv", help="Input structured event CSV.")
    parser.add_argument("--output", default="data/processed/event_catalyst_candidates.csv", help="Output event catalyst candidates CSV.")
    parser.add_argument("--metadata-output", default="data/metadata/event_catalyst_candidates.json", help="Screen metadata JSON.")
    parser.add_argument("--max-candidates", type=int, help="Override max candidates.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_screen(
            Path(args.profile),
            Path(args.events),
            Path(args.output),
            Path(args.metadata_output),
            args.max_candidates,
        )
    except Exception as exc:
        print(f"event catalyst screening failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
