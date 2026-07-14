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

TREND_STATE_LABELS = {
    "data_insufficient": "数据不足",
    "stop_loss_risk": "止损风险",
    "trend_weakened": "趋势转弱",
    "pullback_watch": "回踩观察",
    "overheated": "高位过热",
    "trend_intact": "趋势正常",
    "neutral": "震荡观察",
}


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def build_trend_state(position: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    blockers = {item.get("code") for item in result.get("blockers", [])}
    warnings = {item.get("code") for item in result.get("warnings", [])}
    metrics = result.get("calculations", {})
    close = as_float(metrics.get("latest_close"))
    ma_short = as_float(metrics.get("ma_short"))
    ma_mid = as_float(metrics.get("ma_mid"))
    return_short = as_float(metrics.get("return_short_pct"))
    return_mid = as_float(metrics.get("return_mid_pct"))
    distance_to_ma_short = as_float(metrics.get("distance_to_ma_short_pct"))
    distance_to_ma_mid = as_float(metrics.get("distance_to_ma_mid_pct"))
    drawdown = as_float(metrics.get("drawdown_from_recent_high_pct"))
    stop_loss_price = as_float(value_at(position, "risk.stop_loss_price"))
    distance_to_stop = as_float(metrics.get("distance_to_stop_pct"))
    evidence: list[str] = []

    if "insufficient_daily_bars" in blockers or close is None or ma_mid is None:
        state = "data_insufficient"
        evidence.append("缺少足够日线或中期均线，不能判断趋势。")
    elif blockers & {"stop_loss_triggered", "near_stop_loss", "limit_down", "missing_price_or_stop_loss"}:
        state = "stop_loss_risk"
        if stop_loss_price is not None:
            evidence.append(f"止损价 {stop_loss_price:.2f} 是第一优先触发位。")
        if distance_to_stop is not None:
            evidence.append(f"距离止损约 {distance_to_stop:.2f}%。")
    elif return_mid is not None and return_mid < 0 and close < ma_mid:
        state = "trend_weakened"
        evidence.append(f"20日收益 {return_mid:.2f}%，且收盘价低于20日均线。")
    elif result.get("market_setup") in {"reverse_t_candidate", "both_setups_watch_only"} or (
        return_short is not None and return_short >= 6.0
    ) or (distance_to_ma_short is not None and distance_to_ma_short >= 6.0):
        state = "overheated"
        evidence.append("短线涨幅或短期均线偏离较高，优先观察冲高风险。")
    elif return_mid is not None and return_mid > 0 and drawdown is not None and drawdown <= -3.0:
        state = "pullback_watch"
        evidence.append(f"中期收益为正，但较近期高点回撤 {drawdown:.2f}%。")
    elif return_mid is not None and return_mid > 0 and distance_to_ma_mid is not None and distance_to_ma_mid >= 0:
        state = "trend_intact"
        evidence.append(f"20日收益 {return_mid:.2f}%，且价格位于20日均线上方。")
    else:
        state = "neutral"
        evidence.append("趋势证据不够强，按震荡观察处理。")

    if "position_price_stale" in warnings:
        evidence.append("持仓价格和最新日线收盘价偏离较大，先刷新持仓价格。")

    return {
        "state": state,
        "label": TREND_STATE_LABELS[state],
        "evidence": evidence,
        "metrics": {
            "trade_date": metrics.get("trade_date"),
            "latest_close": rounded(close),
            "ma_short": rounded(ma_short),
            "ma_mid": rounded(ma_mid),
            "return_short_pct": rounded(return_short),
            "return_mid_pct": rounded(return_mid),
            "distance_to_ma_short_pct": rounded(distance_to_ma_short),
            "distance_to_ma_mid_pct": rounded(distance_to_ma_mid),
            "drawdown_from_recent_high_pct": rounded(drawdown),
            "distance_to_stop_pct": rounded(distance_to_stop),
        },
    }


def add_matrix_rule(
    rules: list[dict[str, Any]],
    *,
    trigger: str,
    action: str,
    label: str,
    next_step: str,
    severity: str,
    price: float | None = None,
    execution_allowed: bool = False,
) -> None:
    rules.append(
        {
            "trigger": trigger,
            "price": rounded(price),
            "action": action,
            "action_label": label,
            "next_step": next_step,
            "severity": severity,
            "execution_allowed": execution_allowed,
        }
    )


def build_action_matrix(position: dict[str, Any], result: dict[str, Any], trend_state: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = result.get("calculations", {})
    market_setup = result.get("market_setup")
    stop_loss_price = as_float(value_at(position, "risk.stop_loss_price"))
    ma_short = as_float(metrics.get("ma_short"))
    ma_mid = as_float(metrics.get("ma_mid"))
    recent_high = as_float(metrics.get("recent_high"))
    recent_low = as_float(metrics.get("recent_low"))
    position_pct = as_float(value_at(position, "entry.position_pct_of_total_assets"), 0.0) or 0.0
    blockers = {item.get("code") for item in result.get("blockers", [])}
    rules: list[dict[str, Any]] = []

    if stop_loss_price is not None:
        add_matrix_rule(
            rules,
            trigger="price_lte_stop_loss",
            price=stop_loss_price,
            action="create_exit_plan",
            label="生成退出计划，禁止补仓和做T",
            next_step="运行 tools/new_exit_plan.py，并完成人工确认后再进入卖出执行。",
            severity="critical",
        )
        add_matrix_rule(
            rules,
            trigger="price_within_3pct_above_stop_loss",
            price=round(stop_loss_price * 1.03, 2),
            action="exit_risk_review",
            label="止损风险复核，不做T",
            next_step="先核验止损、流动性和原买入假设，不新增仓位。",
            severity="high",
        )
    else:
        add_matrix_rule(
            rules,
            trigger="missing_stop_loss",
            action="complete_risk_plan",
            label="补齐止损价后再判断动作",
            next_step="补齐持仓 risk.stop_loss_price 和失效条件。",
            severity="high",
        )

    if ma_mid is not None:
        add_matrix_rule(
            rules,
            trigger="close_lt_ma20",
            price=ma_mid,
            action="hold_no_add",
            label="趋势转弱观察，禁止补仓",
            next_step="等待收盘重新站上20日均线并重新运行持仓趋势检查。",
            severity="medium",
        )
        add_matrix_rule(
            rules,
            trigger="close_gte_ma20_and_return_mid_positive",
            price=ma_mid,
            action="restore_watch",
            label="恢复趋势观察，不自动买入",
            next_step="重新校验仓位、止损、失效条件和交易计划门禁。",
            severity="info",
        )
    if ma_short is not None:
        add_matrix_rule(
            rules,
            trigger="pullback_to_ma5",
            price=ma_short,
            action="positive_t_watch",
            label="正T观察位，仅进入人工看盘",
            next_step="正T必须先定义买入价、卖出价、失败后是否转为加仓和最大新增仓位。",
            severity="info",
        )
    if recent_high is not None:
        add_matrix_rule(
            rules,
            trigger="price_gte_recent_high",
            price=recent_high,
            action="take_profit_or_reverse_t_review",
            label="冲高复核止盈或反T",
            next_step="核对原止盈条件；若做反T，必须通过分时确认、费用模型和人工确认。",
            severity="medium",
        )
    if recent_low is not None:
        add_matrix_rule(
            rules,
            trigger="price_lte_recent_low",
            price=recent_low,
            action="thesis_review",
            label="跌破近期低点，复核买入假设",
            next_step="检查趋势证据、基本面反证和是否需要生成退出计划。",
            severity="medium",
        )
    if "stock_position_limit_exceeded" in blockers or position_pct > 10.0:
        add_matrix_rule(
            rules,
            trigger="position_above_limit_or_review_line",
            action="risk_reduction_review",
            label="优先评估降仓",
            next_step="计算降至配置上限以内所需减仓股数，未确认前不做正T买入腿。",
            severity="high",
        )
    if market_setup == "reverse_t_candidate":
        add_matrix_rule(
            rules,
            trigger="reverse_t_candidate",
            action="reverse_t_watch",
            label="反T观察，不是交易许可",
            next_step="必须通过5分钟回测、实时价格提醒、费用模型和人工确认。",
            severity="medium",
        )
    elif market_setup == "positive_t_candidate":
        add_matrix_rule(
            rules,
            trigger="positive_t_candidate",
            action="positive_t_watch",
            label="正T观察，不是补仓许可",
            next_step="先定义失败后是否接受加仓，并重新校验组合仓位。",
            severity="medium",
        )
    elif market_setup == "both_setups_watch_only":
        add_matrix_rule(
            rules,
            trigger="positive_and_reverse_t_conflict",
            action="manual_direction_review",
            label="正T和反T证据冲突，人工拆解方向",
            next_step="不自动选择方向，先确认趋势周期、仓位和日内走势。",
            severity="medium",
        )

    add_matrix_rule(
        rules,
        trigger=f"trend_state_{trend_state['state']}",
        action="keep_manual_gate",
        label="所有动作保持人工门禁",
        next_step="任何真实交易前必须生成计划/执行记录并保留 confirmed 人工确认。",
        severity="info",
    )
    return rules


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

    trend_state = build_trend_state(position, result)
    action_matrix = build_action_matrix(position, result, trend_state)

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
        "trend_state": trend_state,
        "action_matrix": action_matrix,
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
        "本草案只做风险排序、趋势状态和价格动作矩阵，不自动下单，也不使用统一亏损比例倒推止损价。",
        "",
        "| 优先级 | 代码 | 名称 | 仓位 | 趋势状态 | 当前分类 | 20日收益 |",
        "| --- | --- | --- | ---: | --- | --- | ---: |",
    ]
    for item in draft["items"]:
        mid_return = item["metrics"]["return_mid_pct"]
        return_text = "-" if mid_return is None else f"{mid_return:.2f}%"
        lines.append(
            f"| {item['priority']} | {item['stock_code']} | {item['stock_name']} | "
            f"{item['position_pct']:.2f}% | {item['trend_state']['label']} | {item['action_label']} | {return_text} |"
        )
    lines.extend(["", "## 价格动作矩阵", ""])
    for item in draft["items"]:
        lines.append(f"### {item['stock_code']} {item['stock_name']}")
        lines.append("")
        for rule in item["action_matrix"]:
            price_text = "" if rule["price"] is None else f" price={rule['price']}"
            lines.append(f"- [{rule['severity']}] {rule['trigger']}{price_text} -> {rule['action_label']}；{rule['next_step']}")
        lines.append("")
    lines.extend(["## 解锁原则", "", "- 没有止损、失效条件和最大新增仓位时，禁止补仓。", "- 仓位超限时优先评估降仓，不做正T买入腿。", "- 日线做T候选只进入观察名单，执行前仍需分时确认。", ""])
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
