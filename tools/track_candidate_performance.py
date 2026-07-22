#!/usr/bin/env python3
"""Track forward returns after candidates enter the watchlist."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.calc_trend_factors import parse_windows
    from tools.import_daily_bars import parse_number
except ModuleNotFoundError:
    from calc_trend_factors import parse_windows
    from import_daily_bars import parse_number


def read_candidates(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_daily_bars(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("code") or "").strip()
            trade_date = (row.get("trade_date") or "").strip()
            if not code or not trade_date:
                continue
            datetime.strptime(trade_date, "%Y-%m-%d")
            grouped[code].append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "close": parse_number(row.get("close") or "", "close", row_number),
                }
            )
    for rows in grouped.values():
        rows.sort(key=lambda item: item["trade_date"])
    return dict(grouped)


def candidate_start_date(candidate: dict[str, str]) -> str:
    return candidate.get("trade_date") or candidate.get("event_date") or candidate.get("valuation_trade_date") or ""


def find_bar_index(rows: list[dict[str, Any]], start_date: str) -> int | None:
    for index, row in enumerate(rows):
        if row["trade_date"] >= start_date:
            return index
    return None


def forward_return(entry_close: float, target_close: float) -> float:
    return round((target_close / entry_close - 1) * 100, 6) if entry_close else 0.0


def track_candidate(candidate: dict[str, str], bars_by_code: dict[str, list[dict[str, Any]]], horizons: list[int]) -> dict[str, Any]:
    code = candidate.get("code", "")
    rows = bars_by_code.get(code, [])
    start_date = candidate_start_date(candidate)
    result: dict[str, Any] = {
        "code": code,
        "name": candidate.get("name", ""),
        "industry": candidate.get("industry", ""),
        "strategies": candidate.get("strategies", ""),
        "primary_strategy": candidate.get("primary_strategy", ""),
        "start_date": start_date,
        "entry_trade_date": None,
        "entry_close": None,
        "horizons": {},
        "status": "missing_bars",
    }
    if not rows or not start_date:
        result["status"] = "missing_start_date" if not start_date else "missing_bars"
        return result

    entry_index = find_bar_index(rows, start_date)
    if entry_index is None:
        result["status"] = "missing_entry_bar"
        return result

    entry = rows[entry_index]
    entry_close = float(entry["close"])
    result["entry_trade_date"] = entry["trade_date"]
    result["entry_close"] = entry_close
    complete_count = 0
    for horizon in horizons:
        target_index = entry_index + horizon
        if target_index >= len(rows):
            result["horizons"][str(horizon)] = {
                "status": "insufficient_future_bars",
                "target_trade_date": None,
                "target_close": None,
                "return_pct": None,
            }
            continue
        target = rows[target_index]
        complete_count += 1
        result["horizons"][str(horizon)] = {
            "status": "complete",
            "target_trade_date": target["trade_date"],
            "target_close": target["close"],
            "return_pct": forward_return(entry_close, float(target["close"])),
        }

    result["status"] = "complete" if complete_count == len(horizons) else "partial" if complete_count else "pending"
    return result


def summarize_items(items: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {"candidate_count": len(items), "horizons": {}}
    for horizon in horizons:
        key = str(horizon)
        returns = [
            item["horizons"][key]["return_pct"]
            for item in items
            if key in item["horizons"] and item["horizons"][key].get("status") == "complete"
        ]
        if returns:
            summary["horizons"][key] = {
                "completed_count": len(returns),
                "average_return_pct": round(sum(returns) / len(returns), 6),
                "win_rate_pct": round(sum(1 for value in returns if value > 0) / len(returns) * 100, 6),
            }
        else:
            summary["horizons"][key] = {"completed_count": 0, "average_return_pct": None, "win_rate_pct": None}
    return summary


def build_report(candidates_path: Path, daily_bars_path: Path, horizons: list[int]) -> dict[str, Any]:
    candidates = read_candidates(candidates_path)
    bars_by_code = read_daily_bars(daily_bars_path)
    items = [track_candidate(candidate, bars_by_code, horizons) for candidate in candidates]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidates": str(candidates_path),
        "daily_bars": str(daily_bars_path),
        "horizons": horizons,
        "summary": summarize_items(items, horizons),
        "items": items,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 候选池入池后表现跟踪",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 候选数量：{report['summary']['candidate_count']}",
        f"- 观察窗口：{', '.join(str(item) for item in report['horizons'])} 个交易日",
        "",
        "## 汇总",
        "",
        "| 窗口 | 完成数 | 平均收益 | 胜率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for horizon in report["horizons"]:
        data = report["summary"]["horizons"][str(horizon)]
        average = "-" if data["average_return_pct"] is None else f"{data['average_return_pct']:.2f}%"
        win_rate = "-" if data["win_rate_pct"] is None else f"{data['win_rate_pct']:.2f}%"
        lines.append(f"| {horizon} | {data['completed_count']} | {average} | {win_rate} |")

    lines.extend(["", "## 明细", "", "| 代码 | 名称 | 策略 | 入池日 | 入池价 | 状态 |", "| --- | --- | --- | --- | ---: | --- |"])
    for item in report["items"]:
        strategies = (item.get("strategies") or "-").replace("|", ", ")
        lines.append(
            f"| {item['code']} | {item.get('name') or '-'} | {strategies} | "
            f"{item.get('entry_trade_date') or item.get('start_date') or '-'} | "
            f"{item.get('entry_close') if item.get('entry_close') is not None else '-'} | {item['status']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track watchlist candidate forward returns.")
    parser.add_argument("--candidates", default="data/processed/candidate_pool.csv", help="Input candidate pool CSV.")
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv", help="Input normalized daily bars CSV.")
    parser.add_argument("--horizons", default="5,10,20", help="Comma-separated forward trading-day horizons.")
    parser.add_argument("--output", default="data/metadata/candidate-performance.json", help="Output JSON report.")
    parser.add_argument("--markdown-output", default="reports/candidate-performance.md", help="Output Markdown report.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(Path(args.candidates), Path(args.daily_bars), parse_windows(args.horizons))
        write_json(Path(args.output), report)
        write_markdown(Path(args.markdown_output), render_markdown(report))
    except Exception as exc:
        print(f"candidate performance tracking failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"candidates: {report['summary']['candidate_count']}")
        print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
