#!/usr/bin/env python3
"""Build per-holding realtime decision cards from existing monitoring artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import as_float, value_at
except ModuleNotFoundError:
    from risk_check import as_float, value_at


STATE_LABELS = {
    "data_stale": "行情过期，暂停盘中判断",
    "exit_risk_review": "退出风险优先",
    "data_insufficient": "数据不足，暂不决策",
    "risk_reduction_review": "仓位风险复核",
    "positive_t_watch": "正T观察候选",
    "reverse_t_watch": "反T观察候选",
    "hold_no_add": "持有观察，禁止补仓",
    "observe": "只观察，不操作",
}

ACTION_LABELS = {
    "pause_intraday_decision": "暂停实时决策，等待行情刷新",
    "create_exit_or_risk_review": "生成退出或风险复核计划",
    "complete_data_before_decision": "补齐行情、止损和样本数据",
    "review_position_reduction": "复核是否降仓",
    "watch_positive_t_only": "只进入正T人工观察",
    "watch_reverse_t_only": "只进入反T人工观察",
    "hold_without_adding": "持有观察，不补仓",
    "do_nothing": "不买、不卖，继续监控",
}

HARD_T_BLOCKERS = {"stop_loss_triggered", "near_stop_loss", "limit_down", "stock_suspended"}
DATA_BLOCKERS = {"insufficient_daily_bars", "missing_price_or_stop_loss"}
QUALITY_BLOCKER_STATUSES = {"missing", "insufficient"}


def load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def code_from_path(path: str | None) -> str | None:
    if not path:
        return None
    match = re.search(r"(\d{6})(?=\.yaml$|$)", path)
    return match.group(1) if match else None


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def item_code(item: dict[str, Any], *paths: str) -> str | None:
    for path in paths:
        value = value_at(item, path)
        if value:
            return str(value)
    return code_from_path(str(item.get("path") or ""))


def index_portfolio_check(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {
        str(value_at(item, "result.stock_code") or code_from_path(item.get("path"))): item["result"]
        for item in doc.get("positions", [])
        if value_at(item, "result.stock_code") or code_from_path(item.get("path"))
    }


def index_t_checks(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {
        str(value_at(item, "result.stock_code") or code_from_path(item.get("path"))): item["result"]
        for item in doc.get("items", [])
        if value_at(item, "result.stock_code") or code_from_path(item.get("path"))
    }


def index_action_backtests(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {
        str(value_at(item, "stock.code") or code_from_path(item.get("path"))): item
        for item in doc.get("items", [])
        if value_at(item, "stock.code") or code_from_path(item.get("path"))
    }


def index_simple_items(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not doc:
        return {}
    return {str(item["code"]): item for item in doc.get("items", []) if item.get("code")}


def data_quality_status(data_quality: dict[str, Any] | None) -> str | None:
    if not data_quality:
        return None
    return str(data_quality.get("overall_status") or "")


def decision_priority(state: str) -> int:
    return {
        "exit_risk_review": 90,
        "data_stale": 80,
        "data_insufficient": 70,
        "risk_reduction_review": 60,
        "positive_t_watch": 45,
        "reverse_t_watch": 40,
        "hold_no_add": 30,
        "observe": 10,
    }.get(state, 0)


def choose_state(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
) -> tuple[str, str]:
    signal_codes = {item.get("code") for item in intraday.get("signals", [])}
    portfolio_action_codes = {item.get("code") for item in (portfolio or {}).get("actions", [])}
    t_blockers = {item.get("code") for item in (t_check or {}).get("blockers", [])}
    quality_status = data_quality_status(data_quality)
    states: list[tuple[str, str]] = []

    if "stale_quote" in signal_codes:
        states.append(("data_stale", "盘中行情过期，不能给实时执行建议。"))
    if quality_status == "stale":
        states.append(("data_stale", "行情、日线或分钟线存在过期数据，不能给实时执行建议。"))
    if quality_status in QUALITY_BLOCKER_STATUSES:
        states.append(("data_insufficient", "行情、日线或分钟线数据缺失或样本不足，不能验证盘中建议。"))
    if portfolio_action_codes & {"stop_loss_triggered"} or t_blockers & HARD_T_BLOCKERS:
        states.append(("exit_risk_review", "触发或逼近硬风控，退出风险优先于做T。"))
    if t_blockers & DATA_BLOCKERS:
        states.append(("data_insufficient", "日线、止损或样本不足，不能验证交易环境。"))
    if portfolio_action_codes & {"stock_position_limit_exceeded", "industry_position_limit_exceeded", "total_position_limit_exceeded"}:
        states.append(("risk_reduction_review", "持仓或组合仓位超限，需要先复核降仓。"))
    if value_at(intraday, "reduction_plan.status") == "actionable":
        states.append(("risk_reduction_review", "实时市值测算显示可复核降仓。"))
    if t_check and t_check.get("conclusion") == "positive_t_candidate":
        states.append(("positive_t_watch", "日线环境进入正T观察候选。"))
    if value_at(intraday, "reverse_t_plan.status") == "candidate":
        states.append(("reverse_t_watch", "盘中价格进入反T观察候选。"))
    if signal_codes & {"below_ma20", "intraday_drop"}:
        states.append(("hold_no_add", "盘中走势偏弱，禁止补仓。"))
    if reverse_backtest and reverse_backtest.get("verdict") in {"insufficient_sample", "weak_result"} and value_at(intraday, "reverse_t_plan.status") == "candidate":
        states.append(("hold_no_add", "反T历史回测未通过，只能观察不能执行。"))

    if not states:
        return "observe", "没有硬风险或明确做T结构，继续观察。"
    return max(states, key=lambda item: decision_priority(item[0]))


def price_levels(
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    intraday: dict[str, Any],
) -> dict[str, Any]:
    calculations = (portfolio or {}).get("calculations", {})
    t_calculations = (t_check or {}).get("calculations", {})
    stop_loss = as_float(calculations.get("stop_loss_price"))
    warning_pct = as_float(calculations.get("near_stop_warning_pct"), 3.0) or 3.0
    block_pct = as_float(t_calculations.get("near_stop_block_pct"), 1.0) or 1.0
    near_warning_price = None
    near_block_price = None
    if stop_loss is not None:
        near_warning_price = stop_loss / (1 - warning_pct / 100)
        near_block_price = stop_loss / (1 - block_pct / 100)
    return {
        "current_price": rounded(as_float(value_at(intraday, "quote.latest_price"))),
        "stop_loss_price": rounded(stop_loss),
        "near_stop_warning_price": rounded(near_warning_price),
        "near_stop_block_price": rounded(near_block_price),
        "ma5": rounded(as_float(value_at(intraday, "technicals.ma5") or t_calculations.get("ma_short"))),
        "ma20": rounded(as_float(value_at(intraday, "technicals.ma20") or t_calculations.get("ma_mid"))),
        "recent_high": rounded(as_float(t_calculations.get("recent_high"))),
        "recent_low": rounded(as_float(t_calculations.get("recent_low"))),
        "reverse_t_sell_zone": value_at(intraday, "reverse_t_plan.sell_zone"),
        "reverse_t_buyback_max_price": rounded(as_float(value_at(intraday, "reverse_t_plan.buyback_max_price"))),
    }


def build_evidence(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    action_backtest: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    reverse_forecast: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
) -> list[str]:
    evidence: list[str] = []
    if data_quality:
        evidence.append(f"[数据质量] {data_quality.get('status_label') or data_quality.get('overall_status')}")
        for message in (data_quality.get("warnings") or [])[:2]:
            evidence.append(f"[数据质量过期] {message}")
    for signal in intraday.get("signals", [])[:3]:
        evidence.append(f"[盘中:{signal.get('code')}] {signal.get('message')}")
    for item in (portfolio or {}).get("warnings", [])[:2]:
        if item.get("code") != "unknown_position_status":
            evidence.append(f"[持仓:{item.get('code')}] {item.get('message')}")
    for item in (portfolio or {}).get("actions", [])[:2]:
        evidence.append(f"[持仓:{item.get('code')}] {item.get('message')}")
    if t_check:
        evidence.append(f"[做T日线] market={t_check.get('market_setup')} execution={t_check.get('conclusion')}")
        for blocker in t_check.get("blockers", [])[:2]:
            evidence.append(f"[做T阻断:{blocker.get('code')}] {blocker.get('message')}")
    if action_backtest:
        evidence.append(
            f"[动作矩阵回测] 弱规则 {action_backtest.get('weak_rule_count', 0)} 条；"
            f"最弱状态 {value_at(action_backtest, 'weakest_state.state') or '-'} "
            f"{value_at(action_backtest, 'weakest_state.average_return_pct') if value_at(action_backtest, 'weakest_state.average_return_pct') is not None else '-'}%"
        )
    if reverse_backtest:
        evidence.append(f"[反T回测] {reverse_backtest.get('verdict_label') or reverse_backtest.get('verdict')}")
    if reverse_forecast:
        evidence.append(f"[反T预测] {reverse_forecast.get('status_label') or reverse_forecast.get('status')}")
    return evidence or ["暂无足够证据，按只观察处理。"]


def build_blockers(
    intraday: dict[str, Any],
    t_check: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
) -> list[str]:
    blockers: list[str] = []
    if data_quality_status(data_quality) in QUALITY_BLOCKER_STATUSES:
        blockers.extend(data_quality.get("blockers") or [])
    blockers.extend(signal.get("message") for signal in intraday.get("signals", []) if signal.get("severity") in {"block", "risk"})
    blockers.extend(item.get("message") for item in (t_check or {}).get("blockers", []))
    if reverse_backtest and reverse_backtest.get("verdict") != "pass":
        blockers.append(reverse_backtest.get("verdict_label") or "反T历史回测未通过。")
    return [item for item in blockers if item]


def build_next_step(state: str, action_backtest: dict[str, Any] | None) -> str:
    if state == "data_stale":
        return "先刷新准实时监控快照；行情恢复前不做盘中动作。"
    if state == "exit_risk_review":
        return "优先生成退出或风险复核计划；未确认前禁止补仓和做T。"
    if state == "data_insufficient":
        return "补齐日线、实时价、止损价或样本后重新生成决策卡。"
    if state == "risk_reduction_review":
        return "计算降仓目标和最小100股影响，确认后再生成退出/降仓计划。"
    if state == "positive_t_watch":
        return "只进入正T人工观察；先定义买入价、卖出价、失败后是否接受加仓和最大新增仓位。"
    if state == "reverse_t_watch":
        return "只进入反T人工观察；必须通过5分钟回测、费用模型、分时转弱和人工确认。"
    if state == "hold_no_add":
        if action_backtest and (action_backtest.get("weak_rule_count") or 0) > 0:
            return "动作矩阵存在弱规则，先复核规则和仓位，不新增交易。"
        return "继续持有观察，不补仓；等待趋势或风险信号改变后复核。"
    return "本轮不买不卖，继续监控关键价位。"


def confidence_for(state: str, evidence: list[str], blockers: list[str]) -> str:
    if state in {"exit_risk_review", "data_stale", "data_insufficient"}:
        return "high"
    if blockers:
        return "medium"
    if len(evidence) >= 4:
        return "medium"
    return "low"


def build_card(
    intraday: dict[str, Any],
    portfolio: dict[str, Any] | None,
    t_check: dict[str, Any] | None,
    action_backtest: dict[str, Any] | None,
    reverse_backtest: dict[str, Any] | None,
    reverse_forecast: dict[str, Any] | None,
    data_quality: dict[str, Any] | None,
) -> dict[str, Any]:
    state, reason = choose_state(intraday, portfolio, t_check, reverse_backtest, data_quality)
    evidence = build_evidence(intraday, portfolio, t_check, action_backtest, reverse_backtest, reverse_forecast, data_quality)
    blockers = build_blockers(intraday, t_check, reverse_backtest, data_quality)
    action_code = {
        "data_stale": "pause_intraday_decision",
        "exit_risk_review": "create_exit_or_risk_review",
        "data_insufficient": "complete_data_before_decision",
        "risk_reduction_review": "review_position_reduction",
        "positive_t_watch": "watch_positive_t_only",
        "reverse_t_watch": "watch_reverse_t_only",
        "hold_no_add": "hold_without_adding",
        "observe": "do_nothing",
    }[state]
    execution_allowed = False
    if state in {"positive_t_watch", "reverse_t_watch"} and not blockers:
        execution_allowed = False
    return {
        "code": intraday.get("code"),
        "name": intraday.get("name"),
        "state": state,
        "state_label": STATE_LABELS[state],
        "reason": reason,
        "decision": {
            "action": action_code,
            "action_label": ACTION_LABELS[action_code],
            "execution_allowed": execution_allowed,
            "confidence": confidence_for(state, evidence, blockers),
            "next_step": build_next_step(state, action_backtest),
        },
        "price_levels": price_levels(portfolio, t_check, intraday),
        "position": intraday.get("position", {}),
        "market_context": {
            "quote_lag_seconds": value_at(intraday, "quote.quote_lag_seconds"),
            "change_pct": value_at(intraday, "quote.change_pct"),
            "main_net_inflow_ratio_pct": value_at(intraday, "capital_flow.main_net_inflow_ratio_pct"),
            "t_market_setup": (t_check or {}).get("market_setup"),
            "t_conclusion": (t_check or {}).get("conclusion"),
            "reverse_t_status": value_at(intraday, "reverse_t_plan.status"),
            "reverse_t_backtest_verdict": (reverse_backtest or {}).get("verdict"),
            "reverse_t_forecast_status": (reverse_forecast or {}).get("status"),
            "action_backtest_weak_rule_count": (action_backtest or {}).get("weak_rule_count"),
            "data_quality_status": data_quality_status(data_quality),
        },
        "data_quality": data_quality or {},
        "blockers": blockers,
        "evidence": evidence,
        "guardrails": [
            "本卡片只做决策辅助，不自动下单。",
            "任何买入、卖出、做T或降仓动作都必须先生成计划并人工确认。",
            "行情过期、触发止损、接近硬阻断止损、跌停或数据不足时禁止做T。",
        ],
    }


def build_report(
    intraday_snapshot: dict[str, Any],
    portfolio_check: dict[str, Any] | None,
    t_opportunities: dict[str, Any] | None,
    action_backtests: dict[str, Any] | None,
    reverse_t_backtest: dict[str, Any] | None,
    reverse_t_forecast: dict[str, Any] | None,
    data_quality: dict[str, Any] | None = None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    portfolio_by_code = index_portfolio_check(portfolio_check)
    t_by_code = index_t_checks(t_opportunities)
    action_backtest_by_code = index_action_backtests(action_backtests)
    reverse_backtest_by_code = index_simple_items(reverse_t_backtest)
    reverse_forecast_by_code = index_simple_items(reverse_t_forecast)
    data_quality_by_code = index_simple_items(data_quality)
    cards = [
        build_card(
            item,
            portfolio_by_code.get(str(item.get("code"))),
            t_by_code.get(str(item.get("code"))),
            action_backtest_by_code.get(str(item.get("code"))),
            reverse_backtest_by_code.get(str(item.get("code"))),
            reverse_forecast_by_code.get(str(item.get("code"))),
            data_quality_by_code.get(str(item.get("code"))),
        )
        for item in intraday_snapshot.get("items", [])
    ]
    state_counts: dict[str, int] = {}
    for card in cards:
        state_counts[card["state"]] = state_counts.get(card["state"], 0) + 1
    return {
        "generated_at": (generated_at or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "source": {
            "intraday_generated_at": intraday_snapshot.get("generated_at"),
            "portfolio_check_available": portfolio_check is not None,
            "t_opportunities_available": t_opportunities is not None,
            "action_backtests_available": action_backtests is not None,
            "reverse_t_backtest_available": reverse_t_backtest is not None,
            "reverse_t_forecast_available": reverse_t_forecast is not None,
            "data_quality_available": data_quality is not None,
        },
        "card_count": len(cards),
        "state_counts": dict(sorted(state_counts.items())),
        "cards": sorted(cards, key=lambda card: (-decision_priority(card["state"]), str(card["code"]))),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 实时持仓决策卡",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "本报告只做人工决策辅助，不自动下单；所有动作必须先生成计划并人工确认。",
        "",
        "## 汇总",
        "",
        f"- 卡片数：{report['card_count']}",
        f"- 状态分布：{report['state_counts']}",
        "",
        "| 代码 | 名称 | 状态 | 当前价 | 止损 | 阻断价 | 动作 | 置信度 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for card in report["cards"]:
        levels = card["price_levels"]
        decision = card["decision"]
        lines.append(
            f"| {card['code']} | {card['name']} | {card['state_label']} | "
            f"{levels.get('current_price') if levels.get('current_price') is not None else '-'} | "
            f"{levels.get('stop_loss_price') if levels.get('stop_loss_price') is not None else '-'} | "
            f"{levels.get('near_stop_block_price') if levels.get('near_stop_block_price') is not None else '-'} | "
            f"{decision['action_label']} | {decision['confidence']} |"
        )
    lines.extend(["", "## 明细", ""])
    for card in report["cards"]:
        decision = card["decision"]
        levels = card["price_levels"]
        lines.extend(
            [
                f"### {card['code']} {card['name']}",
                "",
                f"- 状态：{card['state_label']}",
                f"- 建议：{decision['action_label']}；执行许可：{decision['execution_allowed']}",
                f"- 下一步：{decision['next_step']}",
                f"- 关键价格：当前 {levels.get('current_price') or '-'}，止损 {levels.get('stop_loss_price') or '-'}，做T阻断价 {levels.get('near_stop_block_price') or '-'}，MA20 {levels.get('ma20') or '-'}",
            ]
        )
        if card["blockers"]:
            lines.append("- 阻断：")
            lines.extend(f"  - {item}" for item in card["blockers"][:4])
        lines.append("- 证据：")
        lines.extend(f"  - {item}" for item in card["evidence"][:6])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build realtime decision cards for current holdings.")
    parser.add_argument("--intraday-snapshot", default="data/metadata/intraday-monitor.latest.json")
    parser.add_argument("--portfolio-check", default="data/metadata/eastmoney-portfolio-check.after-threshold.json")
    parser.add_argument("--t-opportunities", default="data/metadata/eastmoney-portfolio-t-opportunities.near-config.json")
    parser.add_argument("--action-backtests", default="data/metadata/portfolio-action-matrix-backtests.after-plan.json")
    parser.add_argument("--reverse-t-backtest", default="data/metadata/reverse-t-backtest.json")
    parser.add_argument("--reverse-t-forecast", default="data/metadata/reverse-t-forecast.json")
    parser.add_argument("--data-quality", default="data/metadata/data-quality-snapshot.json")
    parser.add_argument("--output", default="data/metadata/realtime-decision-cards.json")
    parser.add_argument("--markdown-output", default="reports/realtime-decision-cards.md")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        intraday_snapshot = load_json_if_exists(Path(args.intraday_snapshot))
        if intraday_snapshot is None:
            raise ValueError(f"missing intraday snapshot: {args.intraday_snapshot}")
        report = build_report(
            intraday_snapshot,
            load_json_if_exists(Path(args.portfolio_check)),
            load_json_if_exists(Path(args.t_opportunities)),
            load_json_if_exists(Path(args.action_backtests)),
            load_json_if_exists(Path(args.reverse_t_backtest)),
            load_json_if_exists(Path(args.reverse_t_forecast)),
            load_json_if_exists(Path(args.data_quality)),
        )
        write_json(Path(args.output), report)
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(render_markdown(report), encoding="utf-8")
    except Exception as exc:
        print(f"build realtime decision cards failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"cards: {report['card_count']}, states: {report['state_counts']}")
        print(f"output: {args.output}")
        print(f"markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
