#!/usr/bin/env python3
"""Generate a human-readable watchlist report from candidate CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def read_candidates(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def split_text(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def is_unified_candidate(candidate: dict[str, str]) -> bool:
    return "strategies" in candidate or "combined_score" in candidate or "primary_strategy" in candidate


def strategy_label(strategy: str) -> str:
    labels = {
        "multi_strategy": "多策略共振",
        "trend_strength": "趋势强度",
        "value_quality": "价值质量",
    }
    return labels.get(strategy, strategy or "-")


def format_unified_candidate(candidate: dict[str, str], index: int) -> list[str]:
    code = candidate.get("code", "")
    strategy_codes = split_text(candidate.get("strategies", ""))
    strategies = [strategy_label(strategy) for strategy in strategy_codes]
    primary_strategy = candidate.get("primary_strategy", "")
    plan_strategy = primary_strategy if primary_strategy != "multi_strategy" else (strategy_codes[0] if strategy_codes else "")

    lines = [
        f"## {index}. {code}",
        "",
        f"- 主策略：{strategy_label(primary_strategy)}",
        f"- 策略来源：{', '.join(strategies) if strategies else '-'}",
        f"- 策略数量：{candidate.get('strategy_count') or '-'}",
        f"- 综合排序分：{candidate.get('combined_score') or '-'}",
        f"- 趋势分：{candidate.get('trend_score') or '-'}",
        f"- 价值质量分：{candidate.get('value_quality_score') or '-'}",
        f"- 交易日：{candidate.get('trade_date') or '-'}",
        f"- 报告期：{candidate.get('report_period') or '-'}",
        "",
        "入选原因：",
    ]

    reasons = split_text(candidate.get("reasons", ""))
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- 暂无。")

    risks = split_text(candidate.get("risks", ""))
    lines.extend(["", "风险提示："])
    if risks:
        lines.extend(f"- {risk}" for risk in risks)
    else:
        lines.append("- 暂无显式风险提示，但仍需按交易计划检查止损、仓位、估值和公告风险。")

    lines.extend(
        [
            "",
            "下一步观察：",
            "- 多策略候选需要确认不同策略证据是否互相支持，而不是互相冲突。",
            "- 单策略候选需要补齐缺失的基本面、趋势、估值或风险证据。",
            "- 是否能形成明确买入价、止损价、仓位和失效条件。",
            "",
            "交易计划入口：",
            "```bash",
            f"python3 tools/new_trade_plan.py --code {code} --name 待补充 --strategy {plan_strategy or '待补充'} --planned-buy-price 待补充 --stop-loss-price 待补充 --position-pct 待补充",
            "```",
            "",
        ]
    )
    return lines


def format_candidate(candidate: dict[str, str], index: int) -> list[str]:
    if is_unified_candidate(candidate):
        return format_unified_candidate(candidate, index)

    code = candidate.get("code", "")
    trade_date = candidate.get("trade_date", "")
    lines = [
        f"## {index}. {code}",
        "",
        f"- 交易日：{trade_date or '-'}",
        f"- 策略：趋势强度",
        f"- 分数：{candidate.get('score') or '-'}",
        f"- 收盘价：{candidate.get('close') or '-'}",
        f"- 区间收益：{candidate.get('return') or '-'}%",
        f"- 均线：{candidate.get('ma') or '-'}",
        f"- 是否站上均线：{candidate.get('above_ma') or '-'}",
        f"- 平均成交额：{candidate.get('turnover_avg') or '-'}",
        "",
        "入选原因：",
    ]

    reasons = split_text(candidate.get("reasons", ""))
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- 暂无。")

    risks = split_text(candidate.get("risks", ""))
    lines.extend(["", "风险提示："])
    if risks:
        lines.extend(f"- {risk}" for risk in risks)
    else:
        lines.append("- 暂无显式风险提示，但仍需按交易计划检查止损、仓位和公告风险。")

    lines.extend(
        [
            "",
            "下一步观察：",
            "- 是否继续维持趋势强度。",
            "- 是否出现追高、涨跌停、停牌或流动性风险。",
            "- 是否能形成明确买入价、止损价和失效条件。",
            "",
            "交易计划入口：",
            "```bash",
            f"python3 tools/new_trade_plan.py --code {code} --name 待补充 --strategy trend_strength --planned-buy-price 待补充 --stop-loss-price 待补充 --position-pct 待补充",
            "```",
            "",
        ]
    )
    return lines


def generate_report(candidates: list[dict[str, str]], generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    lines = [
        "# 候选股观察池报告",
        "",
        f"- 生成时间：{generated_at.isoformat(timespec='seconds')}",
        f"- 候选数量：{len(candidates)}",
        "- 决策边界：本报告只用于观察，不构成买入建议。真实交易前必须生成交易计划并通过风控校验。",
        "",
    ]

    if not candidates:
        lines.extend(["当前没有候选股。", ""])
        return "\n".join(lines)

    for index, candidate in enumerate(candidates, start=1):
        lines.extend(format_candidate(candidate, index))
    return "\n".join(lines)


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_report(candidates_path: Path, output_path: Path) -> dict[str, Any]:
    candidates = read_candidates(candidates_path)
    content = generate_report(candidates)
    write_report(output_path, content)
    return {
        "candidates": str(candidates_path),
        "output": str(output_path),
        "candidate_count": len(candidates),
    }


def print_summary(result: dict[str, Any]) -> None:
    print(f"candidate rows: {result['candidate_count']}")
    print(f"output: {result['output']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a watchlist report from candidate CSV.")
    parser.add_argument("--candidates", default="data/processed/candidate_pool.csv", help="Input candidate CSV.")
    parser.add_argument("--output", default="reports/watchlist.md", help="Output Markdown report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_report(Path(args.candidates), Path(args.output))
    except Exception as exc:
        print(f"watchlist report generation failed: {exc}", file=sys.stderr)
        return 2

    print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
