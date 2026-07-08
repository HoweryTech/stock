#!/usr/bin/env python3
"""Generate a daily operating summary from workflow artifacts."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.check_trade_review_quality import check_trade_review_quality
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
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
    for item in executions:
        data = item["data"]
        rows.append(
            {
                "path": item["path"],
                "id": value_at(data, "execution.id"),
                "stock": value_at(data, "stock.code"),
                "exit_check": value_at(data, "execution.exit_check_conclusion"),
                "trade_return_pct": value_at(data, "result_estimate.trade_return_pct"),
                "portfolio_return_pct": value_at(data, "result_estimate.portfolio_return_pct"),
            }
        )
    return {"count": len(rows), "rows": rows}


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


def derive_operating_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    watchlist = summary["watchlist"]
    portfolio = summary["portfolio"]
    exits = summary["exit_plans"]
    reviews = summary["reviews"]
    review_analysis_available = summary["review_analysis_available"]
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

    urgent_exits = [row for row in exits["rows"] if row["must_exit"] or row["urgency"] == "immediate"]
    if urgent_exits:
        actions.append(f"处理 {len(urgent_exits)} 个紧急退出计划。")
    if reviews["draft_count"]:
        actions.append(f"补全 {reviews['draft_count']} 份复盘草稿。")
    if reviews["quality_blocked_count"]:
        actions.append(f"修正 {reviews['quality_blocked_count']} 份阻断级复盘。")
    if reviews["quality_needs_review_count"]:
        actions.append(f"完善 {reviews['quality_needs_review_count']} 份需复核复盘。")
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


def build_summary(args: argparse.Namespace, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    summary = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "watchlist": summarize_watchlist(load_json_if_exists(Path(args.watchlist_metadata))),
        "portfolio": summarize_portfolio(load_json_if_exists(Path(args.portfolio_check))),
        "exit_plans": summarize_exit_plans(load_yaml_files(args.exit_plans)),
        "exit_executions": summarize_exit_executions(load_yaml_files(args.exit_executions)),
        "reviews": summarize_reviews(load_yaml_files(args.reviews)),
        "review_analysis_available": Path(args.review_analysis).exists(),
        "cooldown": summarize_cooldown(load_json_if_exists(Path(args.cooldown_check))),
        "strategy_health": summarize_strategy_health(load_json_if_exists(Path(args.strategy_health))),
        "strategy_review_tasks": summarize_strategy_review_tasks(load_json_if_exists(Path(args.strategy_review_tasks))),
        "strategy_config_changes": summarize_strategy_config_changes(load_json_if_exists(Path(args.strategy_config_changes))),
        "strategy_config_patch": summarize_strategy_config_patch(load_json_if_exists(Path(args.strategy_config_patch))),
        "strategy_config_patch_audit": summarize_strategy_config_patch_audit(load_json_if_exists(Path(args.strategy_config_patch_audit))),
        "strategy_config_regression": summarize_strategy_config_regression(load_json_if_exists(Path(args.strategy_config_regression))),
        "strategy_config_pipeline": summarize_strategy_config_pipeline(load_json_if_exists(Path(args.strategy_config_pipeline))),
        "strategy_config_snapshot": summarize_strategy_config_snapshot(load_json_if_exists(Path(args.strategy_config_snapshot))),
    }
    summary["operating_actions"] = derive_operating_actions(summary)
    return summary


def render_section_list(items: list[str]) -> list[str]:
    if not items:
        return ["- 无。"]
    return [f"- {item}" for item in items]


def render_summary(summary: dict[str, Any]) -> str:
    watchlist = summary["watchlist"]
    portfolio = summary["portfolio"]
    exits = summary["exit_plans"]
    executions = summary["exit_executions"]
    reviews = summary["reviews"]
    review_analysis_available = summary["review_analysis_available"]
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
        "## 退出与卖出",
        "",
        f"- 退出计划数量：{exits['count']}",
        f"- 卖出执行数量：{executions['count']}",
        "",
    ]

    if exits["rows"]:
        lines.append("退出计划：")
        for row in exits["rows"]:
            lines.append(f"- {row['id']} {row['stock']} {row['type']} urgency={row['urgency']} must_exit={row['must_exit']}")
        lines.append("")
    if executions["rows"]:
        lines.append("卖出执行：")
        for row in executions["rows"]:
            lines.append(
                f"- {row['id']} {row['stock']} check={row['exit_check']} trade_return={row['trade_return_pct']}% portfolio={row['portfolio_return_pct']}%"
            )
        lines.append("")

    lines.extend(
        [
            "## 复盘",
            "",
            f"- 复盘记录数量：{reviews['count']}",
            f"- 草稿数量：{reviews['draft_count']}",
            f"- 阻断级复盘：{reviews['quality_blocked_count']}",
            f"- 需复核复盘：{reviews['quality_needs_review_count']}",
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
    parser.add_argument("--exit-plans", nargs="+", default=["exit-plans/*.yaml"], help="Exit plan YAML paths or glob patterns.")
    parser.add_argument("--exit-executions", nargs="+", default=["exit-executions/*.yaml"], help="Sell execution YAML paths or glob patterns.")
    parser.add_argument("--reviews", nargs="+", default=["reviews/*.yaml"], help="Review YAML paths or glob patterns.")
    parser.add_argument("--review-analysis", default="data/metadata/review-analysis.json", help="Review analysis JSON generated by analyze_trade_reviews.py.")
    parser.add_argument("--cooldown-check", default="data/metadata/review-cooldown.json", help="Review cooldown JSON generated by check_review_cooldown.py.")
    parser.add_argument("--strategy-health", default="data/metadata/strategy-health.json", help="Strategy health JSON generated by check_strategy_health.py.")
    parser.add_argument("--strategy-review-tasks", default="data/metadata/strategy-review-tasks.json", help="Strategy review task JSON generated by generate_strategy_review_tasks.py.")
    parser.add_argument("--strategy-config-changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change draft JSON generated by generate_strategy_config_changes.py.")
    parser.add_argument("--strategy-config-patch", default="data/metadata/strategy-config-patch.json", help="Strategy config patch JSON generated by generate_strategy_config_patch.py.")
    parser.add_argument("--strategy-config-patch-audit", default="data/metadata/strategy-config-patch.apply.json", help="Strategy config patch apply audit JSON generated by apply_strategy_config_patch.py.")
    parser.add_argument("--strategy-config-regression", default="data/metadata/strategy-config-regression.json", help="Strategy config regression JSON generated by check_strategy_config_regression.py.")
    parser.add_argument("--strategy-config-pipeline", default="data/metadata/strategy-config-change-pipeline.json", help="Strategy config change pipeline metadata JSON.")
    parser.add_argument("--strategy-config-snapshot", default="data/metadata/strategy-config-snapshot.json", help="Strategy config version snapshot JSON generated by create_strategy_config_snapshot.py.")
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
