#!/usr/bin/env python3
"""Build a conservative portfolio action draft from T-check results."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import as_float, load_yaml, value_at


ACTION_LABELS = {
    "exit_risk_review": "优先核验退出风险",
    "risk_reduction_review": "优先评估降仓",
    "fundamental_review": "优先复核基本面",
    "hold_no_add": "持有观察，禁止补仓",
    "t_watch_only": "做T观察，不执行",
    "data_insufficient": "数据不足，暂不决策",
}


def classify_holding(position: dict[str, Any], result: dict[str, Any], research: dict[str, Any] | None = None) -> dict[str, Any]:
    blockers = {item.get("code") for item in result.get("blockers", [])}
    metrics = result.get("calculations", {})
    return_mid = as_float(metrics.get("return_mid_pct"))
    close = as_float(metrics.get("latest_close"))
    ma_mid = as_float(metrics.get("ma_mid"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    financial_flags = (research or {}).get("financial_review", {}).get("flags", [])
    announcement_review = (research or {}).get("risk_review", {})

    if "limit_down" in blockers:
        action, priority = "exit_risk_review", 1
        reasons = ["最新日线被识别为跌停，先核验流动性、除权和行情数据。"]
    elif "stock_position_limit_exceeded" in blockers:
        action, priority = "risk_reduction_review", 2
        reasons = [f"单票仓位 {position_pct:.2f}% 超过配置上限，新增买入和正T买入腿均应禁止。"]
    elif financial_flags:
        action, priority = "fundamental_review", 3
        reasons = [item["message"] for item in financial_flags]
    elif "insufficient_daily_bars" in blockers:
        action, priority = "data_insufficient", 3
        reasons = ["有效日线不足 20 条，无法验证中期趋势。"]
    elif return_mid is not None and close is not None and ma_mid is not None and return_mid < 0 and close < ma_mid:
        action, priority = "hold_no_add", 4
        reasons = [f"20日收益 {return_mid:.2f}% 且收盘价低于20日均线，趋势尚未恢复。"]
    elif result.get("market_setup") in {"positive_t_candidate", "reverse_t_candidate", "both_setups_watch_only"}:
        action, priority = "t_watch_only", 5
        reasons = ["行情存在做T观察形态，但账户风控条件尚未满足。"]
    else:
        action, priority = "hold_no_add", 6
        reasons = ["当前没有清晰做T形态，也没有已验证的补仓依据。"]

    if financial_flags and action != "fundamental_review":
        reasons.extend(item["message"] for item in financial_flags)
    if announcement_review.get("requires_manual_review"):
        reasons.append(f"近期公告标题命中 {announcement_review.get('matched_announcement_count', 0)} 项风险关键词，需阅读原文。")

    unlock_conditions = [
        "补齐原始买入逻辑、基本面反证条件和可执行止损规则。",
        "任何补仓前重新校验单票、行业和总仓位上限。",
    ]
    if return_mid is not None and return_mid < 0:
        unlock_conditions.append("至少等待收盘价重新站上20日均线且20日收益转正，再评估新增仓位。")
    if action == "risk_reduction_review":
        unlock_conditions.append("单票仓位降至配置上限以内前，不新增该股票仓位。")
    if action == "data_insufficient":
        unlock_conditions.append("积累至少20个有效交易日后重新运行趋势检查。")

    return {
        "stock_code": result.get("stock_code"),
        "stock_name": result.get("stock_name"),
        "position_pct": round(position_pct, 4),
        "priority": priority,
        "action": action,
        "action_label": ACTION_LABELS[action],
        "add_allowed": False,
        "t_trade_allowed": result.get("conclusion") != "blocked",
        "market_setup": result.get("market_setup"),
        "reasons": reasons,
        "unlock_conditions": unlock_conditions,
        "metrics": {
            "trade_date": metrics.get("trade_date"),
            "latest_close": close,
            "ma_mid": ma_mid,
            "return_mid_pct": return_mid,
            "avg_range_pct": as_float(metrics.get("avg_range_pct")),
        },
    }


def build_action_draft(t_report: dict[str, Any], research_report: dict[str, Any] | None = None) -> dict[str, Any]:
    research_by_code = {item["code"]: item for item in (research_report or {}).get("items", [])}
    items: list[dict[str, Any]] = []
    for source in t_report.get("items", []):
        position = load_yaml(Path(source["path"]))
        code = str(source["result"].get("stock_code") or "")
        items.append(classify_holding(position, source["result"], research_by_code.get(code)))
    items.sort(key=lambda item: (item["priority"], -item["position_pct"], item["stock_code"] or ""))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_checked_at": t_report.get("checked_at"),
        "conclusion": "manual_rules_required",
        "policy": {
            "auto_order": False,
            "default_add_allowed": False,
            "default_t_trade_allowed": False,
            "note": "本草案只做风险排序，不构成自动交易指令。",
        },
        "items": items,
    }


def render_markdown(draft: dict[str, Any]) -> str:
    lines = [
        "# 持仓处置草案",
        "",
        f"生成时间：{draft['generated_at']}",
        "",
        "本草案只做风险排序，不自动下单，也不使用统一亏损比例倒推止损价。",
        "",
        "| 优先级 | 代码 | 名称 | 仓位 | 当前分类 | 20日收益 |",
        "| --- | --- | --- | ---: | --- | ---: |",
    ]
    for item in draft["items"]:
        mid_return = item["metrics"]["return_mid_pct"]
        return_text = "-" if mid_return is None else f"{mid_return:.2f}%"
        lines.append(
            f"| {item['priority']} | {item['stock_code']} | {item['stock_name']} | "
            f"{item['position_pct']:.2f}% | {item['action_label']} | {return_text} |"
        )
    lines.extend(["", "## 解锁原则", "", "- 没有止损、失效条件和最大新增仓位时，禁止补仓。", "- 仓位超限时优先评估降仓，不做正T买入腿。", "- 日线做T候选只进入观察名单，执行前仍需分时确认。", ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a conservative holding action draft.")
    parser.add_argument("--t-report", default="data/metadata/portfolio-t-opportunities.check.json")
    parser.add_argument("--research-report", help="Optional holding fundamentals and announcements report.")
    parser.add_argument("--output", default="data/metadata/holding-action-draft.json")
    parser.add_argument("--markdown-output", default="reports/holding-action-draft.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = json.loads(Path(args.t_report).read_text(encoding="utf-8"))
        research_report = json.loads(Path(args.research_report).read_text(encoding="utf-8")) if args.research_report else None
        draft = build_action_draft(report, research_report)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown_output = Path(args.markdown_output)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(draft), encoding="utf-8")
    except Exception as exc:
        print(f"build holding action draft failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(draft, ensure_ascii=False, indent=2))
    else:
        print(f"positions: {len(draft['items'])}")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
