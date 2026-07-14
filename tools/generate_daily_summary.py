#!/usr/bin/env python3
"""Generate a daily operating summary from workflow artifacts."""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_exit_execution import check_exit_execution
    from tools.check_trade_review_quality import check_trade_review_quality
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_exit_execution import check_exit_execution
    from check_trade_review_quality import check_trade_review_quality
    from risk_check import load_yaml, value_at


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        paths.extend(Path(match) for match in matches)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def load_yaml_files(patterns: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in expand_paths(patterns):
        items.append({"path": str(path), "data": load_yaml(path)})
    return items


def collect_codes(items: list[dict[str, Any]], key: str) -> list[str]:
    codes: list[str] = []
    for item in items:
        code = item.get("code")
        message = item.get("message")
        if code and message:
            codes.append(f"[{code}] {message}")
        elif code:
            codes.append(str(code))
        elif message:
            codes.append(str(message))
    return codes


def slug(value: Any) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "-", str(value or "UNKNOWN").upper()).strip("-")
    return normalized or "UNKNOWN"


def summarize_watchlist(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {"available": False, "conclusion": "missing", "candidate_count": None, "warnings": []}
    candidate_check = value_at(metadata, "steps.candidate_pool_check") or value_at(metadata, "candidate_pool_check") or {}
    return {
        "available": True,
        "conclusion": candidate_check.get("conclusion") or metadata.get("conclusion") or "unknown",
        "candidate_count": value_at(metadata, "steps.merge_candidate_pool.rows") or metadata.get("candidate_count"),
        "warnings": collect_codes(candidate_check.get("warnings", []) or [], "warnings"),
    }


def summarize_portfolio(portfolio: dict[str, Any] | None) -> dict[str, Any]:
    if not portfolio:
        return {"available": False, "conclusion": "missing", "position_count": 0, "needs_action_count": 0, "warning_count": 0, "items": []}
    items = collect_codes(portfolio.get("portfolio_actions", []) or [], "portfolio_actions")
    items.extend(collect_codes(portfolio.get("portfolio_warnings", []) or [], "portfolio_warnings"))
    for position in portfolio.get("positions", []) or []:
        result = position.get("result", {})
        for action in result.get("actions", []) or []:
            code = action.get("code")
            message = action.get("message")
            items.append(f"{position.get('path')}: [{code}] {message}")
        for warning in result.get("warnings", []) or []:
            code = warning.get("code")
            message = warning.get("message")
            items.append(f"{position.get('path')}: [{code}] {message}")
    return {
        "available": True,
        "conclusion": portfolio.get("conclusion") or "unknown",
        "position_count": portfolio.get("position_count", 0),
        "total_position_pct": portfolio.get("total_position_pct"),
        "needs_action_count": portfolio.get("needs_action_count", 0),
        "warning_count": portfolio.get("warning_count", 0),
        "items": items,
    }


def summarize_exit_plans(exit_plans: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in exit_plans:
        data = item["data"]
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "exit_plan.id"),
                "stock": value_at(data, "stock.code"),
                "type": value_at(data, "exit_plan.exit_type"),
                "urgency": value_at(data, "exit_plan.urgency"),
                "must_exit": value_at(data, "decision.must_exit"),
                "daily_conclusion": value_at(data, "checks.daily_check_conclusion"),
            }
        )
    return {"count": len(rows), "rows": rows}


def summarize_exit_executions(executions: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing_confirmation_count = 0
    for item in executions:
        data = item["data"]
        execution_check = check_exit_execution(data)
        confirmation_status = value_at(data, "confirmation_snapshot.status") or "missing"
        exit_check = value_at(data, "execution.exit_check_conclusion")
        mode = value_at(data, "execution.mode")
        requires_confirmation = exit_check == "needs_review" or mode == "real"
        missing_confirmation = requires_confirmation and confirmation_status != "confirmed"
        if missing_confirmation:
            missing_confirmation_count += 1
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "execution.id"),
                "stock": value_at(data, "stock.code"),
                "mode": mode,
                "exit_check": exit_check,
                "confirmation_id": value_at(data, "execution.confirmation_id"),
                "confirmation_status": confirmation_status,
                "requires_confirmation": requires_confirmation,
                "missing_confirmation": missing_confirmation,
                "execution_check_conclusion": execution_check["conclusion"],
                "execution_check_blocker_count": len(execution_check["blockers"]),
                "execution_check_warning_count": len(execution_check["warnings"]),
                "trade_return_pct": value_at(data, "result_estimate.trade_return_pct"),
                "portfolio_return_pct": value_at(data, "result_estimate.portfolio_return_pct"),
            }
        )
    return {
        "count": len(rows),
        "requires_confirmation_count": sum(1 for row in rows if row["requires_confirmation"]),
        "missing_confirmation_count": missing_confirmation_count,
        "blocked_count": sum(1 for row in rows if row["execution_check_conclusion"] == "blocked"),
        "needs_review_count": sum(1 for row in rows if row["execution_check_conclusion"] == "needs_review"),
        "rows": rows,
    }


def trade_execution_requires_confirmation(data: dict[str, Any]) -> bool:
    gate_conclusion = value_at(data, "execution.gate_conclusion")
    mode = value_at(data, "execution.mode")
    side = value_at(data, "order.side")
    cooldown_conclusion = value_at(data, "execution.cooldown_conclusion") or value_at(data, "cooldown_snapshot.conclusion")
    strategy_status = None
    strategy = value_at(data, "trade_plan_snapshot.strategy.source")
    for item in value_at(data, "strategy_health_snapshot.strategies") or []:
        if item.get("strategy") == strategy:
            strategy_status = item.get("status")
            break
    return (
        gate_conclusion == "needs_confirmation"
        or mode == "real"
        or (side == "buy" and cooldown_conclusion == "cooldown_required")
        or (side == "buy" and strategy_status == "pause_new_entries")
    )


def summarize_trade_executions(executions: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing_confirmation_count = 0
    for item in executions:
        data = item["data"]
        confirmation_status = value_at(data, "confirmation_snapshot.status") or "missing"
        requires_confirmation = trade_execution_requires_confirmation(data)
        missing_confirmation = requires_confirmation and confirmation_status != "confirmed"
        if missing_confirmation:
            missing_confirmation_count += 1
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "execution.id"),
                "stock": value_at(data, "stock.code"),
                "mode": value_at(data, "execution.mode"),
                "side": value_at(data, "order.side"),
                "gate_conclusion": value_at(data, "execution.gate_conclusion"),
                "confirmation_id": value_at(data, "execution.confirmation_id"),
                "confirmation_status": confirmation_status,
                "requires_confirmation": requires_confirmation,
                "missing_confirmation": missing_confirmation,
            }
        )
    return {
        "count": len(rows),
        "requires_confirmation_count": sum(1 for row in rows if row["requires_confirmation"]),
        "missing_confirmation_count": missing_confirmation_count,
        "rows": rows,
    }


def summarize_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    drafts = 0
    quality_needs_review = 0
    quality_blocked = 0
    for item in reviews:
        data = item["data"]
        status = value_at(data, "review.status")
        quality = check_trade_review_quality(data)
        if status == "draft":
            drafts += 1
        if quality["conclusion"] == "blocked":
            quality_blocked += 1
        elif quality["conclusion"] == "needs_review":
            quality_needs_review += 1
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "review.id"),
                "stock": value_at(data, "stock.code"),
                "status": status,
                "category": value_at(data, "result.result_category"),
                "trade_return_pct": value_at(data, "result.trade_return_pct"),
                "lesson": value_at(data, "review_questions.lesson"),
                "quality_conclusion": quality["conclusion"],
            }
        )
    return {
        "count": len(rows),
        "draft_count": drafts,
        "quality_blocked_count": quality_blocked,
        "quality_needs_review_count": quality_needs_review,
        "rows": rows,
    }


def summarize_cooldown(cooldown: dict[str, Any] | None) -> dict[str, Any]:
    if not cooldown:
        return {"available": False, "conclusion": "missing", "actions": []}
    return {
        "available": True,
        "conclusion": cooldown.get("conclusion") or "unknown",
        "overall_losing_streak": cooldown.get("overall_losing_streak"),
        "strategy_losing_streaks": cooldown.get("strategy_losing_streaks") or {},
        "actions": cooldown.get("actions") or [],
    }


def summarize_execution_loop(loop_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not loop_doc:
        return {
            "available": False,
            "conclusion": "missing",
            "blocked_count": 0,
            "needs_review_count": 0,
            "downstream_gap_count": 0,
            "orphan_record_count": 0,
            "fix_actions": [],
        }
    return {
        "available": True,
        "conclusion": loop_doc.get("conclusion") or "unknown",
        "blocked_count": loop_doc.get("blocked_count", 0),
        "needs_review_count": loop_doc.get("needs_review_count", 0),
        "downstream_gap_count": loop_doc.get("downstream_gap_count", 0),
        "orphan_record_count": loop_doc.get("orphan_record_count", 0),
        "fix_actions": loop_doc.get("fix_actions", []) or [],
    }


def summarize_strategy_health(health: dict[str, Any] | None) -> dict[str, Any]:
    if not health:
        return {
            "available": False,
            "conclusion": "missing",
            "pause_count": 0,
            "needs_review_count": 0,
            "config_version_count": 0,
            "needs_review_config_version_count": 0,
            "actions": [],
            "config_version_actions": [],
        }
    actions: list[str] = []
    for row in health.get("strategies", []) or []:
        if row.get("status") in {"pause_new_entries", "needs_review"}:
            strategy = row.get("strategy")
            status = row.get("status")
            row_actions = row.get("actions") or []
            if row_actions:
                for item in row_actions:
                    actions.append(f"{strategy}: {status} [{item.get('code')}] {item.get('message')}")
            else:
                actions.append(f"{strategy}: {status}")
    config_version_actions: list[str] = []
    for row in health.get("config_versions", []) or []:
        if row.get("status") == "needs_review":
            version_id = row.get("version_id")
            status = row.get("status")
            row_actions = row.get("actions") or []
            if row_actions:
                for item in row_actions:
                    config_version_actions.append(f"{version_id}: {status} [{item.get('code')}] {item.get('message')}")
            else:
                config_version_actions.append(f"{version_id}: {status}")
    return {
        "available": True,
        "conclusion": health.get("conclusion") or "unknown",
        "pause_count": health.get("pause_count", 0),
        "needs_review_count": health.get("needs_review_count", 0),
        "config_version_count": health.get("config_version_count", 0),
        "needs_review_config_version_count": health.get("needs_review_config_version_count", 0),
        "actions": actions,
        "config_version_actions": config_version_actions,
    }


def summarize_strategy_review_tasks(tasks_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not tasks_doc:
        return {
            "available": False,
            "task_count": 0,
            "open_task_count": 0,
            "open_strategy_task_count": 0,
            "open_config_version_task_count": 0,
            "resolved_task_count": 0,
            "deferred_task_count": 0,
            "open_tasks": [],
        }
    tasks = tasks_doc.get("tasks", []) or []
    open_tasks = [task for task in tasks if (task.get("task_status") or "open") == "open"]
    open_config_version_tasks = [task for task in open_tasks if task.get("task_type") == "config_version"]
    open_strategy_tasks = [task for task in open_tasks if task.get("task_type") != "config_version"]
    resolved_count = sum(1 for task in tasks if task.get("task_status") == "resolved")
    deferred_count = sum(1 for task in tasks if task.get("task_status") == "deferred")
    return {
        "available": True,
        "task_count": len(tasks),
        "open_task_count": len(open_tasks),
        "open_strategy_task_count": len(open_strategy_tasks),
        "open_config_version_task_count": len(open_config_version_tasks),
        "resolved_task_count": resolved_count,
        "deferred_task_count": deferred_count,
        "open_tasks": [
            {
                "id": task.get("id"),
                "task_type": task.get("task_type") or "strategy",
                "strategy": task.get("strategy"),
                "config_version_id": task.get("config_version_id"),
                "status": task.get("status"),
                "priority": task.get("priority"),
            }
            for task in open_tasks
        ],
    }


def summarize_strategy_config_changes(changes_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not changes_doc:
        return {
            "available": False,
            "draft_count": 0,
            "pending_approval_count": 0,
            "pending_strategy_change_count": 0,
            "pending_config_version_change_count": 0,
            "approved_count": 0,
            "rejected_count": 0,
            "drafts": [],
        }
    drafts = changes_doc.get("drafts", []) or []
    pending = [
        draft
        for draft in drafts
        if draft.get("status") == "draft"
        and (draft.get("approval") or {}).get("required")
        and not (draft.get("approval") or {}).get("approved_by")
    ]
    approved_count = sum(1 for draft in drafts if draft.get("status") == "approved")
    rejected_count = sum(1 for draft in drafts if draft.get("status") == "rejected")
    pending_config_version_count = sum(1 for draft in pending if draft.get("source_task_type") == "config_version")
    pending_strategy_count = sum(1 for draft in pending if draft.get("source_task_type") != "config_version")
    return {
        "available": True,
        "draft_count": len(drafts),
        "pending_approval_count": len(pending),
        "pending_strategy_change_count": pending_strategy_count,
        "pending_config_version_change_count": pending_config_version_count,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "drafts": [
            {
                "id": draft.get("id"),
                "source_task_type": draft.get("source_task_type") or "strategy",
                "strategy": draft.get("strategy"),
                "config_version_id": draft.get("config_version_id"),
                "source_task_id": draft.get("source_task_id"),
                "change_count": len(draft.get("change_items", []) or []),
            }
            for draft in pending
        ],
    }


def summarize_strategy_config_patch(patch_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not patch_doc:
        return {"available": False, "operation_count": 0, "operations": []}
    operations = patch_doc.get("operations", []) or []
    return {
        "available": True,
        "operation_count": len(operations),
        "operations": [
            {
                "path": item.get("path"),
                "old_value": item.get("old_value"),
                "new_value": item.get("new_value"),
                "source_change_id": item.get("source_change_id"),
            }
            for item in operations
        ],
    }


def summarize_manual_confirmation_records(records_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not records_doc:
        return {"available": False, "confirmation_count": 0, "open_count": 0, "confirmed_count": 0, "rejected_count": 0, "by_id": {}}
    confirmations = records_doc.get("confirmations", []) or []
    return {
        "available": True,
        "confirmation_count": len(confirmations),
        "open_count": sum(1 for item in confirmations if item.get("status") == "open"),
        "confirmed_count": sum(1 for item in confirmations if item.get("status") == "confirmed"),
        "rejected_count": sum(1 for item in confirmations if item.get("status") == "rejected"),
        "by_id": {item.get("id"): item for item in confirmations if item.get("id")},
    }


def summarize_strategy_config_patch_audit(audit_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not audit_doc:
        return {"available": False, "operation_count": 0, "applied_by": None, "applied_at": None, "backup": None, "operations": []}
    operations = audit_doc.get("operations", []) or []
    return {
        "available": True,
        "operation_count": len(operations),
        "applied_by": audit_doc.get("applied_by"),
        "applied_at": audit_doc.get("applied_at"),
        "backup": audit_doc.get("backup"),
        "operations": [
            {
                "path": item.get("path"),
                "old_value": item.get("old_value"),
                "new_value": item.get("new_value"),
                "source_change_id": item.get("source_change_id"),
            }
            for item in operations
        ],
    }


def summarize_strategy_config_regression(regression_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not regression_doc:
        return {"available": False, "conclusion": "missing", "blocker_count": 0, "warning_count": 0, "items": []}
    items = collect_codes(regression_doc.get("blockers", []) or [], "blockers")
    items.extend(collect_codes(regression_doc.get("warnings", []) or [], "warnings"))
    return {
        "available": True,
        "conclusion": regression_doc.get("conclusion") or "unknown",
        "blocker_count": len(regression_doc.get("blockers", []) or []),
        "warning_count": len(regression_doc.get("warnings", []) or []),
        "items": items,
    }


def summarize_strategy_config_pipeline(pipeline_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not pipeline_doc:
        return {
            "available": False,
            "apply_requested": False,
            "change_check_conclusion": "missing",
            "patch_operation_count": 0,
            "apply_skipped": True,
            "regression_conclusion": "missing",
        }
    steps = pipeline_doc.get("steps", {}) or {}
    change_check = steps.get("change_check", {}) or {}
    patch = steps.get("patch", {}) or {}
    apply_step = steps.get("apply", {}) or {}
    regression = steps.get("regression", {}) or {}
    return {
        "available": True,
        "apply_requested": bool(pipeline_doc.get("apply_requested")),
        "change_check_conclusion": change_check.get("conclusion") or "unknown",
        "change_check_blocker_count": change_check.get("blocker_count", 0),
        "patch_operation_count": patch.get("operation_count", 0),
        "apply_skipped": bool(apply_step.get("skipped", True)),
        "applied_operation_count": apply_step.get("operation_count", 0),
        "regression_conclusion": regression.get("conclusion") or "unknown",
        "regression_blocker_count": regression.get("blocker_count", 0),
        "regression_skipped": bool(regression.get("skipped", True)),
    }


def summarize_strategy_config_snapshot(snapshot_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot_doc:
        return {
            "available": False,
            "version_id": None,
            "profile_hash": None,
            "profile_hash_short": None,
            "generated_at": None,
            "profile_name": None,
            "source_regression_conclusion": "missing",
        }
    profile_hash = snapshot_doc.get("profile_hash")
    return {
        "available": True,
        "version_id": snapshot_doc.get("version_id"),
        "profile_hash": profile_hash,
        "profile_hash_short": profile_hash[:12] if isinstance(profile_hash, str) else None,
        "generated_at": snapshot_doc.get("generated_at"),
        "profile_name": value_at(snapshot_doc, "profile.name"),
        "source_regression_conclusion": value_at(snapshot_doc, "source.regression.conclusion") or "missing",
    }


def summarize_holding_action_draft(draft_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not draft_doc:
        return {
            "available": False,
            "conclusion": "missing",
            "item_count": 0,
            "critical_rule_count": 0,
            "high_rule_count": 0,
            "trend_states": {},
            "items": [],
        }
    rows: list[dict[str, Any]] = []
    trend_states: dict[str, int] = {}
    critical_rule_count = 0
    high_rule_count = 0
    for item in draft_doc.get("items", []) or []:
        trend = item.get("trend_state") or {}
        state = trend.get("state") or "unknown"
        trend_states[state] = trend_states.get(state, 0) + 1
        matrix = item.get("action_matrix") or []
        critical_rules = [rule for rule in matrix if rule.get("severity") == "critical"]
        high_rules = [rule for rule in matrix if rule.get("severity") == "high"]
        critical_rule_count += len(critical_rules)
        high_rule_count += len(high_rules)
        rows.append(
            {
                "stock_code": item.get("stock_code"),
                "stock_name": item.get("stock_name"),
                "priority": item.get("priority"),
                "action": item.get("action"),
                "action_label": item.get("action_label"),
                "trend_state": state,
                "trend_label": trend.get("label") or state,
                "matrix_count": len(matrix),
                "critical_rules": critical_rules,
                "high_rules": high_rules,
            }
        )
    rows.sort(key=lambda row: (row["priority"] or 99, row["stock_code"] or ""))
    return {
        "available": True,
        "conclusion": draft_doc.get("conclusion") or "unknown",
        "item_count": len(rows),
        "critical_rule_count": critical_rule_count,
        "high_rule_count": high_rule_count,
        "trend_states": trend_states,
        "items": rows,
    }


def derive_operating_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    watchlist = summary["watchlist"]
    portfolio = summary["portfolio"]
    holding_action_draft = summary["holding_action_draft"]
    exits = summary["exit_plans"]
    trade_executions = summary["trade_executions"]
    exit_executions = summary["exit_executions"]
    reviews = summary["reviews"]
    review_analysis_available = summary["review_analysis_available"]
    execution_loop = summary["execution_loop"]
    cooldown = summary["cooldown"]
    strategy_health = summary["strategy_health"]
    strategy_review_tasks = summary["strategy_review_tasks"]
    strategy_config_changes = summary["strategy_config_changes"]
    strategy_config_patch = summary["strategy_config_patch"]
    strategy_config_regression = summary["strategy_config_regression"]
    strategy_config_pipeline = summary["strategy_config_pipeline"]

    if not watchlist["available"]:
        actions.append("生成或刷新观察池流水线。")
    elif watchlist["conclusion"] not in {"pass", "normal"}:
        actions.append(f"检查候选池质量结论：{watchlist['conclusion']}。")

    if not portfolio["available"]:
        actions.append("执行组合持仓日检。")
    elif portfolio["conclusion"] == "needs_action":
        actions.append("优先处理组合或持仓日检中的 needs_action。")
    elif portfolio["conclusion"] == "warning":
        actions.append("复核组合或持仓提醒项。")
    if portfolio["available"] and not holding_action_draft["available"]:
        actions.append("生成或刷新持仓处置草案，补齐趋势状态和价格动作矩阵。")
    elif holding_action_draft["critical_rule_count"]:
        actions.append(f"复核 {holding_action_draft['critical_rule_count']} 条持仓关键价格触发动作。")
    elif holding_action_draft["high_rule_count"]:
        actions.append(f"复核 {holding_action_draft['high_rule_count']} 条持仓高优先级动作条件。")

    urgent_exits = [row for row in exits["rows"] if row["must_exit"] or row["urgency"] == "immediate"]
    if urgent_exits:
        actions.append(f"处理 {len(urgent_exits)} 个紧急退出计划。")
    if trade_executions["missing_confirmation_count"]:
        actions.append(f"修正 {trade_executions['missing_confirmation_count']} 笔缺少确认快照的交易执行记录。")
    if exit_executions["blocked_count"]:
        actions.append(f"修正 {exit_executions['blocked_count']} 笔阻断级卖出执行记录。")
    if exit_executions["missing_confirmation_count"]:
        actions.append(f"修正 {exit_executions['missing_confirmation_count']} 笔缺少确认快照的卖出执行记录。")
    if reviews["draft_count"]:
        actions.append(f"补全 {reviews['draft_count']} 份复盘草稿。")
    if reviews["quality_blocked_count"]:
        actions.append(f"修正 {reviews['quality_blocked_count']} 份阻断级复盘。")
    if reviews["quality_needs_review_count"]:
        actions.append(f"完善 {reviews['quality_needs_review_count']} 份需复核复盘。")
    if (trade_executions["count"] or exit_executions["count"] or reviews["count"]) and not execution_loop["available"]:
        actions.append("执行闭环总检查。")
    elif execution_loop["conclusion"] == "blocked":
        actions.append(f"执行闭环存在 {execution_loop['blocked_count']} 条阻断记录，先修正再推进下一环节。")
    elif execution_loop["conclusion"] == "needs_review":
        actions.append(f"执行闭环存在 {execution_loop['needs_review_count']} 条需复核记录。")
    if reviews["count"] and not review_analysis_available:
        actions.append("生成或刷新交易复盘分析。")
    if reviews["count"] and not cooldown["available"]:
        actions.append("执行复盘冷静期检查。")
    elif cooldown["conclusion"] == "cooldown_required":
        actions.append("冷静期已触发，暂停新开仓并复盘最近亏损。")
    if reviews["count"] and not strategy_health["available"]:
        actions.append("执行策略健康检查。")
    elif strategy_health["conclusion"] == "pause_required":
        actions.append("存在需暂停新开仓的策略。")
    elif strategy_health["conclusion"] == "needs_review" and (
        strategy_health["needs_review_count"] or not strategy_health["needs_review_config_version_count"]
    ):
        actions.append("存在需复核的策略。")
    if strategy_health["needs_review_config_version_count"]:
        actions.append(f"复核 {strategy_health['needs_review_config_version_count']} 个表现异常的策略配置版本。")
    if strategy_review_tasks["open_strategy_task_count"]:
        actions.append(f"处理 {strategy_review_tasks['open_strategy_task_count']} 个未完成策略复核任务。")
    if strategy_review_tasks["open_config_version_task_count"]:
        actions.append(f"处理 {strategy_review_tasks['open_config_version_task_count']} 个未完成配置版本复核任务。")
    if strategy_review_tasks["deferred_task_count"]:
        actions.append(f"复查 {strategy_review_tasks['deferred_task_count']} 个暂缓策略复核任务。")
    if strategy_config_changes["pending_strategy_change_count"]:
        actions.append(f"审批或驳回 {strategy_config_changes['pending_strategy_change_count']} 个策略配置变更草稿。")
    if strategy_config_changes["pending_config_version_change_count"]:
        actions.append(f"审批或驳回 {strategy_config_changes['pending_config_version_change_count']} 个配置版本变更草稿。")
    if strategy_config_patch["operation_count"]:
        actions.append(f"人工复核 {strategy_config_patch['operation_count']} 个待应用策略配置补丁。")
    if strategy_config_regression["conclusion"] == "blocked":
        actions.append("配置应用后回归检查阻断，先回滚或修复配置。")
    elif strategy_config_regression["conclusion"] == "needs_review":
        actions.append("配置应用后回归检查需复核。")
    if strategy_config_pipeline["change_check_conclusion"] == "blocked":
        actions.append("配置变更流水线校验阻断，先修正变更草稿。")
    if strategy_config_pipeline["regression_conclusion"] == "blocked":
        actions.append("配置变更流水线回归阻断，先回滚或修复配置。")
    if not actions:
        actions.append("当前没有阻断项；保持观察，不因空闲而交易。")
    return actions


def manual_confirmation_item(
    records: dict[str, Any],
    *,
    confirmation_id: str,
    text: str,
    subject_type: str,
    subject_id: str,
) -> dict[str, Any]:
    record = records["by_id"].get(confirmation_id) or {}
    status = record.get("status") or "open"
    return {
        "id": confirmation_id,
        "text": text,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "status": status,
        "confirmed_by": record.get("confirmed_by") or "",
        "confirmed_at": record.get("confirmed_at"),
        "confirmation_reason": record.get("confirmation_reason") or "",
        "rejected_by": record.get("rejected_by") or "",
        "rejected_at": record.get("rejected_at"),
        "rejected_reason": record.get("rejected_reason") or "",
    }


def format_manual_confirmation(item: dict[str, Any]) -> str:
    if item["status"] == "confirmed":
        return f"已确认：{item['text']} confirmation_id={item['id']} confirmed_by={item['confirmed_by']} confirmed_at={item['confirmed_at']}"
    if item["status"] == "rejected":
        return f"已驳回：{item['text']} confirmation_id={item['id']} rejected_by={item['rejected_by']} reason={item['rejected_reason']}"
    return f"待确认：{item['text']} confirmation_id={item['id']}"


def derive_manual_confirmation_items(summary: dict[str, Any]) -> list[dict[str, Any]]:
    confirmations: list[dict[str, Any]] = []
    exits = summary["exit_plans"]
    trade_executions = summary["trade_executions"]
    exit_executions = summary["exit_executions"]
    reviews = summary["reviews"]
    strategy_health = summary["strategy_health"]
    strategy_config_changes = summary["strategy_config_changes"]
    strategy_config_patch = summary["strategy_config_patch"]
    strategy_config_regression = summary["strategy_config_regression"]
    strategy_config_pipeline = summary["strategy_config_pipeline"]
    records = summary["manual_confirmation_records"]

    urgent_exits = [row for row in exits["rows"] if row["must_exit"] or row["urgency"] == "immediate"]
    for row in urgent_exits:
        text = f"确认紧急退出计划：{row['id']} stock={row['stock']} type={row['type']}。"
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id=f"CONFIRM-EXIT-PLAN-{slug(row['id'])}",
                text=text,
                subject_type="exit_plan",
                subject_id=row["id"] or "",
            )
        )
    for row in trade_executions["rows"]:
        if row["missing_confirmation"]:
            confirmation_id = row["confirmation_id"] or f"CONFIRM-TRADE-{slug(row['id'])}"
            text = f"补齐交易执行确认记录：{row['id']} stock={row['stock']} mode={row['mode']} gate={row['gate_conclusion']}。"
            confirmations.append(
                manual_confirmation_item(
                    records,
                    confirmation_id=confirmation_id,
                    text=text,
                    subject_type="trade_execution",
                    subject_id=row["id"] or "",
                )
            )
    for row in exit_executions["rows"]:
        if row["missing_confirmation"]:
            confirmation_id = row["confirmation_id"] or f"CONFIRM-EXIT-EXEC-{slug(row['id'])}"
            text = f"补齐卖出执行确认记录：{row['id']} stock={row['stock']} mode={row['mode']} check={row['exit_check']}。"
            confirmations.append(
                manual_confirmation_item(
                    records,
                    confirmation_id=confirmation_id,
                    text=text,
                    subject_type="exit_execution",
                    subject_id=row["id"] or "",
                )
            )
    for row in reviews["rows"]:
        if row["quality_conclusion"] == "blocked":
            text = f"确认阻断级复盘修正结论：{row['id']} stock={row['stock']}。"
            confirmations.append(
                manual_confirmation_item(
                    records,
                    confirmation_id=f"CONFIRM-BLOCKED-REVIEW-{slug(row['id'])}",
                    text=text,
                    subject_type="trade_review",
                    subject_id=row["id"] or "",
                )
            )
    if strategy_health["pause_count"]:
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id="CONFIRM-STRATEGY-PAUSE-BOUNDARY",
                text=f"确认 {strategy_health['pause_count']} 个暂停新开仓策略的执行边界。",
                subject_type="strategy_health",
                subject_id="pause_required",
            )
        )
    for item in strategy_config_changes["drafts"]:
        if item["source_task_type"] == "config_version":
            text = f"审批或驳回配置版本变更草稿：{item['id']} config_version={item['config_version_id']}。"
        else:
            text = f"审批或驳回策略配置变更草稿：{item['id']} strategy={item['strategy']}。"
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id=f"CONFIRM-CONFIG-CHANGE-{slug(item['id'])}",
                text=text,
                subject_type="config_change",
                subject_id=item["id"] or "",
            )
        )
    for item in strategy_config_patch["operations"]:
        text = f"人工复核待应用配置补丁：{item['source_change_id']} path={item['path']} old={item['old_value']} new={item['new_value']}。"
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id=f"CONFIRM-CONFIG-PATCH-{slug(item['source_change_id'])}-{slug(item['path'])}",
                text=text,
                subject_type="config_patch",
                subject_id=item["source_change_id"] or "",
            )
        )
    if strategy_config_regression["conclusion"] == "blocked":
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id="CONFIRM-CONFIG-REGRESSION-BLOCKED",
                text="确认配置回归阻断后的回滚或修复方案。",
                subject_type="config_regression",
                subject_id="blocked",
            )
        )
    if strategy_config_pipeline["change_check_conclusion"] == "blocked":
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id="CONFIRM-CONFIG-PIPELINE-CHECK-BLOCKED",
                text="确认配置变更流水线校验阻断的修正方案。",
                subject_type="config_pipeline",
                subject_id="change_check_blocked",
            )
        )
    if strategy_config_pipeline["regression_conclusion"] == "blocked":
        confirmations.append(
            manual_confirmation_item(
                records,
                confirmation_id="CONFIRM-CONFIG-PIPELINE-REGRESSION-BLOCKED",
                text="确认配置变更流水线回归阻断后的回滚或修复方案。",
                subject_type="config_pipeline",
                subject_id="regression_blocked",
            )
        )
    return confirmations


def build_summary(args: argparse.Namespace, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    summary = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "watchlist": summarize_watchlist(load_json_if_exists(Path(args.watchlist_metadata))),
        "portfolio": summarize_portfolio(load_json_if_exists(Path(args.portfolio_check))),
        "holding_action_draft": summarize_holding_action_draft(load_json_if_exists(Path(args.holding_action_draft))),
        "exit_plans": summarize_exit_plans(load_yaml_files(args.exit_plans)),
        "trade_executions": summarize_trade_executions(load_yaml_files(args.trade_executions)),
        "exit_executions": summarize_exit_executions(load_yaml_files(args.exit_executions)),
        "reviews": summarize_reviews(load_yaml_files(args.reviews)),
        "review_analysis_available": Path(args.review_analysis).exists(),
        "execution_loop": summarize_execution_loop(load_json_if_exists(Path(args.execution_loop_check))),
        "cooldown": summarize_cooldown(load_json_if_exists(Path(args.cooldown_check))),
        "strategy_health": summarize_strategy_health(load_json_if_exists(Path(args.strategy_health))),
        "strategy_review_tasks": summarize_strategy_review_tasks(load_json_if_exists(Path(args.strategy_review_tasks))),
        "strategy_config_changes": summarize_strategy_config_changes(load_json_if_exists(Path(args.strategy_config_changes))),
        "strategy_config_patch": summarize_strategy_config_patch(load_json_if_exists(Path(args.strategy_config_patch))),
        "strategy_config_patch_audit": summarize_strategy_config_patch_audit(load_json_if_exists(Path(args.strategy_config_patch_audit))),
        "strategy_config_regression": summarize_strategy_config_regression(load_json_if_exists(Path(args.strategy_config_regression))),
        "strategy_config_pipeline": summarize_strategy_config_pipeline(load_json_if_exists(Path(args.strategy_config_pipeline))),
        "strategy_config_snapshot": summarize_strategy_config_snapshot(load_json_if_exists(Path(args.strategy_config_snapshot))),
        "manual_confirmation_records": summarize_manual_confirmation_records(load_json_if_exists(Path(args.manual_confirmations))),
    }
    summary["operating_actions"] = derive_operating_actions(summary)
    summary["manual_confirmation_items"] = derive_manual_confirmation_items(summary)
    summary["manual_confirmations"] = [format_manual_confirmation(item) for item in summary["manual_confirmation_items"]] or ["今日无必须人工确认事项。"]
    return summary


def render_section_list(items: list[str]) -> list[str]:
    if not items:
        return ["- 无。"]
    return [f"- {item}" for item in items]


def render_summary(summary: dict[str, Any]) -> str:
    watchlist = summary["watchlist"]
    portfolio = summary["portfolio"]
    holding_action_draft = summary["holding_action_draft"]
    exits = summary["exit_plans"]
    trade_executions = summary["trade_executions"]
    executions = summary["exit_executions"]
    reviews = summary["reviews"]
    review_analysis_available = summary["review_analysis_available"]
    execution_loop = summary["execution_loop"]
    cooldown = summary["cooldown"]
    strategy_health = summary["strategy_health"]
    strategy_review_tasks = summary["strategy_review_tasks"]
    strategy_config_changes = summary["strategy_config_changes"]
    strategy_config_patch = summary["strategy_config_patch"]
    strategy_config_patch_audit = summary["strategy_config_patch_audit"]
    strategy_config_regression = summary["strategy_config_regression"]
    strategy_config_pipeline = summary["strategy_config_pipeline"]
    strategy_config_snapshot = summary["strategy_config_snapshot"]

    lines = [
        "# 每日操作摘要",
        "",
        f"- 生成时间：{summary['generated_at']}",
        "- 决策边界：本报告只汇总事实和规则检查，不构成买卖建议。",
        "",
        "## 今日优先动作",
        "",
        *render_section_list(summary["operating_actions"]),
        "",
        "## 今日必须人工确认事项",
        "",
        *render_section_list(summary["manual_confirmations"]),
        "",
        "## 观察池",
        "",
        f"- 元数据状态：{'已读取' if watchlist['available'] else '缺失'}",
        f"- 候选池结论：{watchlist['conclusion']}",
        f"- 候选数量：{watchlist['candidate_count'] if watchlist['candidate_count'] is not None else '-'}",
        "",
        "## 组合持仓",
        "",
        f"- 检查状态：{'已读取' if portfolio['available'] else '缺失'}",
        f"- 组合结论：{portfolio['conclusion']}",
        f"- 持仓数量：{portfolio['position_count']}",
        f"- 总仓位：{portfolio.get('total_position_pct') if portfolio.get('total_position_pct') is not None else '-'}",
        f"- 需处理持仓数：{portfolio['needs_action_count']}",
        f"- 提醒持仓数：{portfolio['warning_count']}",
        "",
        "组合/持仓提示：",
        *render_section_list(portfolio["items"]),
        "",
        "## 持仓处置草案",
        "",
        f"- 草案状态：{'已读取' if holding_action_draft['available'] else '缺失'}",
        f"- 草案结论：{holding_action_draft['conclusion']}",
        f"- 覆盖持仓数：{holding_action_draft['item_count']}",
        f"- 关键触发动作数：{holding_action_draft['critical_rule_count']}",
        f"- 高优先级动作数：{holding_action_draft['high_rule_count']}",
        f"- 趋势状态分布：{holding_action_draft['trend_states'] if holding_action_draft['trend_states'] else '-'}",
        "",
        "## 退出与卖出",
        "",
        f"- 退出计划数量：{exits['count']}",
        f"- 交易执行数量：{trade_executions['count']}",
        f"- 需确认交易执行：{trade_executions['requires_confirmation_count']}",
        f"- 缺少确认快照交易执行：{trade_executions['missing_confirmation_count']}",
        f"- 卖出执行数量：{executions['count']}",
        f"- 阻断级卖出执行：{executions['blocked_count']}",
        f"- 需复核卖出执行：{executions['needs_review_count']}",
        f"- 需确认卖出执行：{executions['requires_confirmation_count']}",
        f"- 缺少确认快照卖出执行：{executions['missing_confirmation_count']}",
        "",
    ]

    if holding_action_draft["items"]:
        lines.append("持仓趋势与动作矩阵：")
        for row in holding_action_draft["items"]:
            lines.append(
                f"- {row['stock_code']} {row['stock_name']} priority={row['priority']} trend={row['trend_label']} action={row['action_label']} matrix={row['matrix_count']}"
            )
            for rule in row["critical_rules"][:2]:
                price_text = "" if rule.get("price") is None else f" price={rule['price']}"
                lines.append(f"  - critical {rule.get('trigger')}{price_text}: {rule.get('action_label')}")
            for rule in row["high_rules"][:2]:
                price_text = "" if rule.get("price") is None else f" price={rule['price']}"
                lines.append(f"  - high {rule.get('trigger')}{price_text}: {rule.get('action_label')}")
        lines.append("")

    if exits["rows"]:
        lines.append("退出计划：")
        for row in exits["rows"]:
            lines.append(f"- {row['id']} {row['stock']} {row['type']} urgency={row['urgency']} must_exit={row['must_exit']}")
        lines.append("")
    if trade_executions["rows"]:
        lines.append("交易执行：")
        for row in trade_executions["rows"]:
            lines.append(
                f"- {row['id']} {row['stock']} mode={row['mode']} side={row['side']} gate={row['gate_conclusion']} confirmation={row['confirmation_status']}"
            )
        lines.append("")
    if executions["rows"]:
        lines.append("卖出执行：")
        for row in executions["rows"]:
            lines.append(
                f"- {row['id']} {row['stock']} mode={row['mode']} check={row['exit_check']} execution_check={row['execution_check_conclusion']} confirmation={row['confirmation_status']} trade_return={row['trade_return_pct']}% portfolio={row['portfolio_return_pct']}%"
            )
        lines.append("")

    if execution_loop["fix_actions"]:
        lines.append("执行闭环修复动作：")
        for group in execution_loop["fix_actions"]:
            lines.append(f"- {group['title']}：{group['count']} 项")
            for item in group.get("items", []):
                lines.append(f"  - [{item['code']}] {item.get('subject_id') or '-'}：{item['message']}")
                if item.get("fix_hint"):
                    lines.append(f"    - fix: {item['fix_hint']}")
        lines.append("")

    lines.extend(
        [
            "## 复盘",
            "",
            f"- 复盘记录数量：{reviews['count']}",
            f"- 草稿数量：{reviews['draft_count']}",
            f"- 阻断级复盘：{reviews['quality_blocked_count']}",
            f"- 需复核复盘：{reviews['quality_needs_review_count']}",
            f"- 执行闭环总检查：{'已读取' if execution_loop['available'] else '缺失'}",
            f"- 执行闭环结论：{execution_loop['conclusion']}",
            f"- 执行闭环阻断记录：{execution_loop['blocked_count']}",
            f"- 执行闭环需复核记录：{execution_loop['needs_review_count']}",
            f"- 执行闭环缺失下游记录：{execution_loop['downstream_gap_count']}",
            f"- 执行闭环孤儿记录：{execution_loop['orphan_record_count']}",
            f"- 复盘分析：{'已生成' if review_analysis_available else '缺失'}",
            f"- 冷静期检查：{'已读取' if cooldown['available'] else '缺失'}",
            f"- 冷静期结论：{cooldown['conclusion']}",
            f"- 策略健康检查：{'已读取' if strategy_health['available'] else '缺失'}",
            f"- 策略健康结论：{strategy_health['conclusion']}",
            f"- 暂停新开仓策略数：{strategy_health['pause_count']}",
            f"- 需复核策略数：{strategy_health['needs_review_count']}",
            f"- 配置版本数量：{strategy_health['config_version_count']}",
            f"- 需复核配置版本数：{strategy_health['needs_review_config_version_count']}",
            f"- 策略复核任务：{'已读取' if strategy_review_tasks['available'] else '缺失'}",
            f"- 未完成复核任务：{strategy_review_tasks['open_task_count']}",
            f"- 未完成策略复核任务：{strategy_review_tasks['open_strategy_task_count']}",
            f"- 未完成配置版本复核任务：{strategy_review_tasks['open_config_version_task_count']}",
            f"- 已解决策略复核任务：{strategy_review_tasks['resolved_task_count']}",
            f"- 暂缓策略复核任务：{strategy_review_tasks['deferred_task_count']}",
            f"- 策略配置变更草稿：{'已读取' if strategy_config_changes['available'] else '缺失'}",
            f"- 待审批配置变更：{strategy_config_changes['pending_approval_count']}",
            f"- 待审批策略配置变更：{strategy_config_changes['pending_strategy_change_count']}",
            f"- 待审批配置版本变更：{strategy_config_changes['pending_config_version_change_count']}",
            f"- 已审批策略配置变更：{strategy_config_changes['approved_count']}",
            f"- 已驳回策略配置变更：{strategy_config_changes['rejected_count']}",
            f"- 待应用配置补丁：{'已读取' if strategy_config_patch['available'] else '缺失'}",
            f"- 待应用配置操作数：{strategy_config_patch['operation_count']}",
            f"- 配置补丁应用审计：{'已读取' if strategy_config_patch_audit['available'] else '缺失'}",
            f"- 已应用配置操作数：{strategy_config_patch_audit['operation_count']}",
            f"- 配置应用人：{strategy_config_patch_audit['applied_by'] or '-'}",
            f"- 配置应用后回归：{'已读取' if strategy_config_regression['available'] else '缺失'}",
            f"- 配置回归结论：{strategy_config_regression['conclusion']}",
            f"- 配置回归阻断数：{strategy_config_regression['blocker_count']}",
            f"- 配置回归提醒数：{strategy_config_regression['warning_count']}",
            f"- 配置变更流水线：{'已读取' if strategy_config_pipeline['available'] else '缺失'}",
            f"- 流水线校验结论：{strategy_config_pipeline['change_check_conclusion']}",
            f"- 流水线补丁操作数：{strategy_config_pipeline['patch_operation_count']}",
            f"- 流水线是否应用：{'否' if strategy_config_pipeline['apply_skipped'] else '是'}",
            f"- 流水线回归结论：{strategy_config_pipeline['regression_conclusion']}",
            f"- 策略配置版本快照：{'已读取' if strategy_config_snapshot['available'] else '缺失'}",
            f"- 当前配置版本：{strategy_config_snapshot['version_id'] or '-'}",
            f"- 配置哈希：{strategy_config_snapshot['profile_hash_short'] or '-'}",
            f"- 快照回归结论：{strategy_config_snapshot['source_regression_conclusion']}",
            "",
        ]
    )
    if reviews["rows"]:
        lines.append("复盘草稿：")
        for row in reviews["rows"]:
            lesson = row["lesson"] or "待补充"
            lines.append(
                f"- {row['id']} {row['stock']} {row['category']} quality={row['quality_conclusion']} return={row['trade_return_pct']}% lesson={lesson}"
            )
        lines.append("")
    if cooldown["actions"]:
        lines.append("冷静期动作：")
        for item in cooldown["actions"]:
            lines.append(f"- [{item.get('code')}] {item.get('message')}")
        lines.append("")
    if strategy_health["actions"]:
        lines.append("策略健康动作：")
        for item in strategy_health["actions"]:
            lines.append(f"- {item}")
        lines.append("")
    if strategy_health["config_version_actions"]:
        lines.append("配置版本健康动作：")
        for item in strategy_health["config_version_actions"]:
            lines.append(f"- {item}")
        lines.append("")
    if strategy_review_tasks["open_tasks"]:
        lines.append("未完成策略复核任务：")
        for item in strategy_review_tasks["open_tasks"]:
            if item["task_type"] == "config_version":
                lines.append(f"- {item['id']} config_version={item['config_version_id']} status={item['status']} priority={item['priority']}")
            else:
                lines.append(f"- {item['id']} strategy={item['strategy']} status={item['status']} priority={item['priority']}")
        lines.append("")
    if strategy_config_changes["drafts"]:
        lines.append("待审批策略配置变更：")
        for item in strategy_config_changes["drafts"]:
            if item["source_task_type"] == "config_version":
                lines.append(f"- {item['id']} config_version={item['config_version_id']} source_task={item['source_task_id']} changes={item['change_count']}")
            else:
                lines.append(f"- {item['id']} strategy={item['strategy']} source_task={item['source_task_id']} changes={item['change_count']}")
        lines.append("")
    if strategy_config_patch["operations"]:
        lines.append("待应用配置补丁：")
        for item in strategy_config_patch["operations"]:
            lines.append(f"- {item['source_change_id']} path={item['path']} old={item['old_value']} new={item['new_value']}")
        lines.append("")
    if strategy_config_patch_audit["operations"]:
        lines.append("已应用配置补丁：")
        lines.append(f"- applied_at={strategy_config_patch_audit['applied_at']} applied_by={strategy_config_patch_audit['applied_by']}")
        lines.append(f"- backup={strategy_config_patch_audit['backup']}")
        for item in strategy_config_patch_audit["operations"]:
            lines.append(f"- {item['source_change_id']} path={item['path']} old={item['old_value']} new={item['new_value']}")
        lines.append("")
    if strategy_config_regression["items"]:
        lines.append("配置回归问题：")
        for item in strategy_config_regression["items"]:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a daily operating summary from workflow artifacts.")
    parser.add_argument("--watchlist-metadata", default="data/metadata/watchlist_pipeline.json", help="Watchlist pipeline metadata JSON.")
    parser.add_argument("--portfolio-check", default="data/metadata/portfolio_positions.check.json", help="Portfolio position check JSON.")
    parser.add_argument("--holding-action-draft", default="data/metadata/holding-action-draft.json", help="Holding action draft JSON generated by build_holding_action_draft.py.")
    parser.add_argument("--exit-plans", nargs="+", default=["exit-plans/*.yaml"], help="Exit plan YAML paths or glob patterns.")
    parser.add_argument("--trade-executions", nargs="+", default=["executions/*.yaml"], help="Trade execution YAML paths or glob patterns.")
    parser.add_argument("--exit-executions", nargs="+", default=["exit-executions/*.yaml"], help="Sell execution YAML paths or glob patterns.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--review-analysis", default="data/metadata/review-analysis.json", help="Review analysis JSON generated by analyze_trade_reviews.py.")
    parser.add_argument("--execution-loop-check", default="data/metadata/execution-loop-check.json", help="Execution loop check JSON generated by check_execution_loop.py.")
    parser.add_argument("--cooldown-check", default="data/metadata/review-cooldown.json", help="Review cooldown JSON generated by check_review_cooldown.py.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Strategy health JSON generated by check_strategy_health.py.")
    parser.add_argument("--strategy-review-tasks", default="data/metadata/strategy-review-tasks.json", help="Strategy review task JSON generated by generate_strategy_review_tasks.py.")
    parser.add_argument("--strategy-config-changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change draft JSON generated by generate_strategy_config_changes.py.")
    parser.add_argument("--strategy-config-patch", default="data/metadata/strategy-config-patch.json", help="Strategy config patch JSON generated by generate_strategy_config_patch.py.")
    parser.add_argument("--strategy-config-patch-audit", default="data/metadata/strategy-config-patch.apply.json", help="Strategy config patch apply audit JSON generated by apply_strategy_config_patch.py.")
    parser.add_argument("--strategy-config-regression", default="data/metadata/strategy-config-regression.json", help="Strategy config regression JSON generated by check_strategy_config_regression.py.")
    parser.add_argument("--strategy-config-pipeline", default="data/metadata/strategy-config-change-pipeline.json", help="Strategy config change pipeline metadata JSON.")
    parser.add_argument("--strategy-config-snapshot", default="data/metadata/strategy-config-snapshot.json", help="Strategy config version snapshot JSON generated by create_strategy_config_snapshot.py.")
    parser.add_argument("--manual-confirmations", default="data/metadata/manual-confirmations.json", help="Manual confirmation record JSON generated by update_manual_confirmation.py.")
    parser.add_argument("--output", default="reports/daily-summary.md", help="Output Markdown report.")
    parser.add_argument("--json-output", help="Optional output JSON summary.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary instead of text status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build_summary(args)
        write_text(Path(args.output), render_summary(summary))
        if args.json_output:
            write_json(Path(args.json_output), summary)
    except Exception as exc:
        print(f"daily summary generation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"daily summary: {args.output}")
        print("actions:")
        for item in summary["operating_actions"]:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
