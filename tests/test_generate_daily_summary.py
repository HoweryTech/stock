import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from tools.generate_daily_summary import build_summary, render_summary
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def args(tmp_dir: str) -> Namespace:
    base = Path(tmp_dir)
    return Namespace(
        watchlist_metadata=str(base / "watchlist.json"),
        portfolio_check=str(base / "portfolio.json"),
        exit_plans=[str(base / "exit-plans/*.yaml")],
        trade_executions=[str(base / "executions/*.yaml")],
        exit_executions=[str(base / "exit-executions/*.yaml")],
        reviews=[str(base / "reviews/*.yaml")],
        review_analysis=str(base / "review-analysis.json"),
        cooldown_check=str(base / "review-cooldown.json"),
        strategy_health=str(base / "strategy-health.json"),
        strategy_review_tasks=str(base / "strategy-review-tasks.json"),
        strategy_config_changes=str(base / "strategy-config-changes.json"),
        strategy_config_patch=str(base / "strategy-config-patch.json"),
        strategy_config_patch_audit=str(base / "strategy-config-patch.apply.json"),
        strategy_config_regression=str(base / "strategy-config-regression.json"),
        strategy_config_pipeline=str(base / "strategy-config-change-pipeline.json"),
        strategy_config_snapshot=str(base / "strategy-config-snapshot.json"),
        manual_confirmations=str(base / "manual-confirmations.json"),
        output=str(base / "daily-summary.md"),
        json_output=None,
        json=False,
    )


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exit_plan() -> dict:
    plan = load_yaml(ROOT / "templates/exit-plan.example.yaml")
    plan["exit_plan"]["id"] = "EXIT-SUMMARY-0001"
    plan["exit_plan"]["exit_type"] = "stop_loss"
    plan["exit_plan"]["urgency"] = "immediate"
    plan["stock"]["code"] = "600000"
    plan["decision"]["must_exit"] = True
    plan["checks"]["daily_check_conclusion"] = "needs_action"
    return plan


def exit_execution() -> dict:
    execution = load_yaml(ROOT / "templates/exit-execution.example.yaml")
    execution["execution"]["id"] = "EXITEXEC-SUMMARY-0001"
    execution["execution"]["exit_check_conclusion"] = "pass"
    execution["stock"]["code"] = "600000"
    execution["result_estimate"]["trade_return_pct"] = -9.0
    execution["result_estimate"]["portfolio_return_pct"] = -0.45
    return execution


def trade_execution() -> dict:
    data = load_yaml(ROOT / "templates/trade-execution.example.yaml")
    data["execution"]["id"] = "EXEC-SUMMARY-0001"
    data["execution"]["mode"] = "paper"
    data["execution"]["source_trade_plan_id"] = "TP-SUMMARY-0001"
    data["execution"]["gate_conclusion"] = "needs_confirmation"
    data["execution"]["confirmation_id"] = "CONFIRM-TRADE-EXEC-SUMMARY-0001"
    data["stock"]["code"] = "600000"
    data["order"]["side"] = "buy"
    data["confirmation_snapshot"] = {"available": False, "status": "missing"}
    return data


def review() -> dict:
    data = load_yaml(ROOT / "templates/trade-review.example.yaml")
    data["review"]["id"] = "TR-SUMMARY-0001"
    data["review"]["status"] = "draft"
    data["stock"]["code"] = "600000"
    data["result"]["result_category"] = "strategy_loss"
    data["result"]["trade_return_pct"] = -9.0
    return data


class GenerateDailySummaryTest(unittest.TestCase):
    def test_builds_summary_with_priority_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "watchlist.json",
                {"steps": {"candidate_pool_check": {"conclusion": "needs_review", "warnings": [{"code": "risk", "message": "需补风险。"}]}}},
            )
            write_json(
                base / "portfolio.json",
                {
                    "conclusion": "needs_action",
                    "position_count": 1,
                    "total_position_pct": 5.0,
                    "needs_action_count": 1,
                    "warning_count": 0,
                    "portfolio_actions": [{"code": "portfolio_total_position_exceeded", "message": "总仓位超限。"}],
                    "positions": [],
                },
            )
            write_yaml(base / "exit-plans" / "exit.yaml", exit_plan())
            write_yaml(base / "exit-executions" / "exit_execution.yaml", exit_execution())
            write_yaml(base / "reviews" / "review.yaml", review())

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 15, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["watchlist"]["conclusion"], "needs_review")
        self.assertEqual(summary["portfolio"]["conclusion"], "needs_action")
        self.assertEqual(summary["exit_plans"]["count"], 1)
        self.assertEqual(summary["exit_executions"]["count"], 1)
        self.assertEqual(summary["reviews"]["draft_count"], 1)
        self.assertEqual(summary["reviews"]["quality_needs_review_count"], 1)
        self.assertIn("优先处理组合或持仓日检中的 needs_action。", summary["operating_actions"])
        self.assertIn("处理 1 个紧急退出计划。", summary["operating_actions"])
        self.assertIn("待确认：确认紧急退出计划：EXIT-SUMMARY-0001 stock=600000 type=stop_loss。 confirmation_id=CONFIRM-EXIT-PLAN-EXIT-SUMMARY-0001", summary["manual_confirmations"])
        self.assertIn("补全 1 份复盘草稿。", summary["operating_actions"])
        self.assertIn("完善 1 份需复核复盘。", summary["operating_actions"])
        self.assertIn("生成或刷新交易复盘分析。", summary["operating_actions"])
        self.assertIn("执行复盘冷静期检查。", summary["operating_actions"])
        self.assertIn("执行策略健康检查。", summary["operating_actions"])
        self.assertIn("# 每日操作摘要", content)
        self.assertIn("EXIT-SUMMARY-0001", content)

    def test_missing_inputs_still_generate_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 9, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["watchlist"]["conclusion"], "missing")
        self.assertEqual(summary["portfolio"]["conclusion"], "missing")
        self.assertIn("生成或刷新观察池流水线。", summary["operating_actions"])
        self.assertIn("执行组合持仓日检。", summary["operating_actions"])
        self.assertEqual(summary["manual_confirmations"], ["今日无必须人工确认事项。"])
        self.assertIn("## 今日必须人工确认事项", content)
        self.assertIn("今日无必须人工确认事项", content)
        self.assertIn("元数据状态：缺失", content)

    def test_daily_summary_shows_trade_execution_missing_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "executions" / "execution.yaml", trade_execution())

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 9, 10, 0))
            content = render_summary(summary)

        self.assertEqual(summary["trade_executions"]["count"], 1)
        self.assertEqual(summary["trade_executions"]["missing_confirmation_count"], 1)
        self.assertIn("修正 1 笔缺少确认快照的交易执行记录。", summary["operating_actions"])
        self.assertIn(
            "待确认：补齐交易执行确认记录：EXEC-SUMMARY-0001 stock=600000 mode=paper gate=needs_confirmation。 confirmation_id=CONFIRM-TRADE-EXEC-SUMMARY-0001",
            summary["manual_confirmations"],
        )
        self.assertIn("缺少确认快照交易执行：1", content)
        self.assertIn("EXEC-SUMMARY-0001", content)

    def test_daily_summary_shows_strategy_health_action_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-health.json",
                {
                    "conclusion": "needs_review",
                    "pause_count": 0,
                    "needs_review_count": 1,
                    "strategies": [
                        {
                            "strategy": "trend_strength",
                            "status": "needs_review",
                            "actions": [
                                {
                                    "code": "loss_making_discipline_exception",
                                    "message": "策略 trend_strength 存在 1 笔亏损纪律例外交易，需要复查破例规则。",
                                }
                            ],
                        }
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 9, 30, 0))
            content = render_summary(summary)

        self.assertIn("存在需复核的策略。", summary["operating_actions"])
        self.assertEqual(summary["strategy_health"]["needs_review_count"], 1)
        self.assertIn("loss_making_discipline_exception", summary["strategy_health"]["actions"][0])
        self.assertIn("亏损纪律例外交易", content)

    def test_daily_summary_shows_config_version_health_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-health.json",
                {
                    "conclusion": "needs_review",
                    "pause_count": 0,
                    "needs_review_count": 0,
                    "config_version_count": 1,
                    "needs_review_config_version_count": 1,
                    "strategies": [],
                    "config_versions": [
                        {
                            "version_id": "CONFIG-VERSION-RISK",
                            "status": "needs_review",
                            "actions": [
                                {
                                    "code": "config_version_negative_portfolio_contribution",
                                    "message": "配置版本 CONFIG-VERSION-RISK 组合收益贡献为 -0.20%。",
                                }
                            ],
                        }
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 9, 45, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_health"]["needs_review_config_version_count"], 1)
        self.assertNotIn("存在需复核的策略。", summary["operating_actions"])
        self.assertIn("复核 1 个表现异常的策略配置版本。", summary["operating_actions"])
        self.assertIn("配置版本健康动作", content)
        self.assertIn("CONFIG-VERSION-RISK", content)

    def test_daily_summary_shows_strategy_review_task_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-review-tasks.json",
                {
                    "task_count": 3,
                    "tasks": [
                        {
                            "id": "STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
                            "strategy": "trend_strength",
                            "status": "needs_review",
                            "priority": "medium",
                            "task_status": "open",
                        },
                        {
                            "id": "STRATEGY-REVIEW-VALUE-QUALITY-NEEDS-REVIEW",
                            "strategy": "value_quality",
                            "status": "needs_review",
                            "priority": "medium",
                            "task_status": "deferred",
                        },
                        {
                            "id": "STRATEGY-REVIEW-EVENT-CATALYST-NEEDS-REVIEW",
                            "strategy": "event_catalyst",
                            "status": "needs_review",
                            "priority": "medium",
                            "task_status": "resolved",
                        },
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 10, 30, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_review_tasks"]["task_count"], 3)
        self.assertEqual(summary["strategy_review_tasks"]["open_task_count"], 1)
        self.assertEqual(summary["strategy_review_tasks"]["open_strategy_task_count"], 1)
        self.assertEqual(summary["strategy_review_tasks"]["deferred_task_count"], 1)
        self.assertEqual(summary["strategy_review_tasks"]["resolved_task_count"], 1)
        self.assertIn("处理 1 个未完成策略复核任务。", summary["operating_actions"])
        self.assertIn("复查 1 个暂缓策略复核任务。", summary["operating_actions"])
        self.assertIn("STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW", content)

    def test_daily_summary_shows_config_version_review_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-review-tasks.json",
                {
                    "task_count": 1,
                    "tasks": [
                        {
                            "id": "CONFIG-VERSION-REVIEW-CONFIG-VERSION-RISK",
                            "task_type": "config_version",
                            "strategy": None,
                            "config_version_id": "CONFIG-VERSION-RISK",
                            "status": "needs_review",
                            "priority": "medium",
                            "task_status": "open",
                        }
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 10, 45, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_review_tasks"]["open_task_count"], 1)
        self.assertEqual(summary["strategy_review_tasks"]["open_strategy_task_count"], 0)
        self.assertEqual(summary["strategy_review_tasks"]["open_config_version_task_count"], 1)
        self.assertNotIn("处理 1 个未完成策略复核任务。", summary["operating_actions"])
        self.assertIn("处理 1 个未完成配置版本复核任务。", summary["operating_actions"])
        self.assertIn("config_version=CONFIG-VERSION-RISK", content)

    def test_daily_summary_shows_pending_strategy_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-changes.json",
                {
                    "draft_count": 2,
                    "drafts": [
                        {
                            "id": "CONFIG-CHANGE-TREND",
                            "source_task_id": "STRATEGY-REVIEW-TREND",
                            "strategy": "trend_strength",
                            "status": "draft",
                            "change_items": [{"path": "strategies.trend_strength.enabled"}],
                            "approval": {"required": True, "approved_by": "", "approved_at": None},
                        },
                        {
                            "id": "CONFIG-CHANGE-VALUE",
                            "source_task_id": "STRATEGY-REVIEW-VALUE",
                            "strategy": "value_quality",
                            "status": "approved",
                            "change_items": [{"path": "strategies.value_quality.screening"}],
                            "approval": {"required": True, "approved_by": "lihongwei", "approved_at": "2026-07-08T12:00:00"},
                        },
                        {
                            "id": "CONFIG-CHANGE-REJECTED",
                            "source_task_id": "STRATEGY-REVIEW-REJECTED",
                            "strategy": "event_catalyst",
                            "status": "rejected",
                            "change_items": [{"path": "strategies.event_catalyst"}],
                            "approval": {"required": True, "rejected_by": "lihongwei", "rejected_at": "2026-07-08T12:30:00", "rejected_reason": "证据不足。"},
                        },
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 11, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_changes"]["draft_count"], 3)
        self.assertEqual(summary["strategy_config_changes"]["pending_approval_count"], 1)
        self.assertEqual(summary["strategy_config_changes"]["pending_strategy_change_count"], 1)
        self.assertEqual(summary["strategy_config_changes"]["approved_count"], 1)
        self.assertEqual(summary["strategy_config_changes"]["rejected_count"], 1)
        self.assertIn("审批或驳回 1 个策略配置变更草稿。", summary["operating_actions"])
        self.assertIn("CONFIG-CHANGE-TREND", content)
        self.assertIn("已审批策略配置变更：1", content)
        self.assertIn("已驳回策略配置变更：1", content)
        self.assertNotIn("CONFIG-CHANGE-VALUE strategy=value_quality", content)

    def test_daily_summary_shows_pending_config_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-changes.json",
                {
                    "drafts": [
                        {
                            "id": "CONFIG-CHANGE-CONFIG-VERSION-RISK",
                            "source_task_type": "config_version",
                            "source_task_id": "CONFIG-VERSION-REVIEW-CONFIG-VERSION-RISK",
                            "strategy": "CONFIG_VERSION",
                            "config_version_id": "CONFIG-VERSION-RISK",
                            "status": "draft",
                            "change_items": [{"path": "risk.max_total_position_pct"}],
                            "approval": {"required": True, "approved_by": "", "approved_at": None},
                        }
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 11, 15, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_changes"]["pending_config_version_change_count"], 1)
        self.assertEqual(summary["strategy_config_changes"]["pending_strategy_change_count"], 0)
        self.assertNotIn("审批或驳回 1 个策略配置变更草稿。", summary["operating_actions"])
        self.assertIn("审批或驳回 1 个配置版本变更草稿。", summary["operating_actions"])
        self.assertIn(
            "待确认：审批或驳回配置版本变更草稿：CONFIG-CHANGE-CONFIG-VERSION-RISK config_version=CONFIG-VERSION-RISK。 confirmation_id=CONFIRM-CONFIG-CHANGE-CONFIG-CHANGE-CONFIG-VERSION-RISK",
            summary["manual_confirmations"],
        )
        self.assertEqual(summary["manual_confirmation_items"][0]["status"], "open")
        self.assertIn("## 今日必须人工确认事项", content)
        self.assertIn("config_version=CONFIG-VERSION-RISK", content)

    def test_daily_summary_shows_pending_strategy_config_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-patch.json",
                {
                    "operation_count": 1,
                    "operations": [
                        {
                            "op": "replace",
                            "path": "risk.max_position_pct_per_stock",
                            "old_value": 10.0,
                            "new_value": 8.0,
                            "source_change_id": "CONFIG-CHANGE-RISK",
                        }
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 11, 30, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_patch"]["operation_count"], 1)
        self.assertIn("人工复核 1 个待应用策略配置补丁。", summary["operating_actions"])
        self.assertIn(
            "待确认：人工复核待应用配置补丁：CONFIG-CHANGE-RISK path=risk.max_position_pct_per_stock old=10.0 new=8.0。 confirmation_id=CONFIRM-CONFIG-PATCH-CONFIG-CHANGE-RISK-RISK-MAX-POSITION-PCT-PER-STOCK",
            summary["manual_confirmations"],
        )
        self.assertIn("risk.max_position_pct_per_stock", content)
        self.assertIn("CONFIG-CHANGE-RISK", content)

    def test_daily_summary_marks_confirmed_manual_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-patch.json",
                {
                    "operations": [
                        {
                            "path": "risk.max_position_pct_per_stock",
                            "old_value": 10.0,
                            "new_value": 8.0,
                            "source_change_id": "CONFIG-CHANGE-RISK",
                        }
                    ],
                },
            )
            write_json(
                base / "manual-confirmations.json",
                {
                    "confirmations": [
                        {
                            "id": "CONFIRM-CONFIG-PATCH-CONFIG-CHANGE-RISK-RISK-MAX-POSITION-PCT-PER-STOCK",
                            "status": "confirmed",
                            "confirmed_by": "lihongwei",
                            "confirmed_at": "2026-07-08T14:00:00",
                            "confirmation_reason": "已核对旧值、新值和来源证据。",
                        }
                    ]
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 11, 45, 0))
            content = render_summary(summary)

        self.assertEqual(summary["manual_confirmation_items"][0]["status"], "confirmed")
        self.assertIn(
            "已确认：人工复核待应用配置补丁：CONFIG-CHANGE-RISK path=risk.max_position_pct_per_stock old=10.0 new=8.0。 confirmation_id=CONFIRM-CONFIG-PATCH-CONFIG-CHANGE-RISK-RISK-MAX-POSITION-PCT-PER-STOCK confirmed_by=lihongwei confirmed_at=2026-07-08T14:00:00",
            summary["manual_confirmations"],
        )
        self.assertIn("已确认：人工复核待应用配置补丁", content)

    def test_daily_summary_shows_applied_strategy_config_patch_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-patch.apply.json",
                {
                    "applied_at": "2026-07-08T15:00:00",
                    "applied_by": "lihongwei",
                    "backup": "data/backups/investment-profile.20260708-150000.yaml",
                    "operation_count": 1,
                    "operations": [
                        {
                            "op": "replace",
                            "path": "risk.max_position_pct_per_stock",
                            "old_value": 10.0,
                            "new_value": 8.0,
                            "source_change_id": "CONFIG-CHANGE-RISK",
                        }
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 16, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_patch_audit"]["operation_count"], 1)
        self.assertEqual(summary["strategy_config_patch_audit"]["applied_by"], "lihongwei")
        self.assertIn("已应用配置操作数：1", content)
        self.assertIn("配置应用人：lihongwei", content)
        self.assertIn("data/backups/investment-profile.20260708-150000.yaml", content)

    def test_daily_summary_shows_strategy_config_regression_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(base / "strategy-config-regression.json", {"conclusion": "pass", "blockers": [], "warnings": []})

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 16, 30, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_regression"]["conclusion"], "pass")
        self.assertEqual(summary["strategy_config_regression"]["blocker_count"], 0)
        self.assertIn("配置回归结论：pass", content)

    def test_daily_summary_prioritizes_blocked_strategy_config_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-regression.json",
                {
                    "conclusion": "blocked",
                    "blockers": [{"code": "risk_field_out_of_safe_range", "message": "风险字段超限。"}],
                    "warnings": [],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 16, 45, 0))
            content = render_summary(summary)

        self.assertIn("配置应用后回归检查阻断，先回滚或修复配置。", summary["operating_actions"])
        self.assertEqual(summary["strategy_config_regression"]["blocker_count"], 1)
        self.assertIn("[risk_field_out_of_safe_range] 风险字段超限。", content)

    def test_daily_summary_shows_strategy_config_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-change-pipeline.json",
                {
                    "apply_requested": False,
                    "steps": {
                        "change_check": {"conclusion": "pass", "blocker_count": 0, "warning_count": 0},
                        "patch": {"operation_count": 1, "skipped": False},
                        "apply": {"operation_count": 0, "skipped": True},
                        "regression": {"conclusion": "skipped", "blocker_count": 0, "warning_count": 0, "skipped": True},
                    },
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 17, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_pipeline"]["change_check_conclusion"], "pass")
        self.assertEqual(summary["strategy_config_pipeline"]["patch_operation_count"], 1)
        self.assertTrue(summary["strategy_config_pipeline"]["apply_skipped"])
        self.assertIn("配置变更流水线：已读取", content)
        self.assertIn("流水线补丁操作数：1", content)

    def test_daily_summary_prioritizes_blocked_strategy_config_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-change-pipeline.json",
                {
                    "apply_requested": False,
                    "steps": {
                        "change_check": {"conclusion": "blocked", "blocker_count": 1, "warning_count": 0},
                        "patch": {"operation_count": 0, "skipped": True},
                        "apply": {"operation_count": 0, "skipped": True},
                        "regression": {"conclusion": "skipped", "blocker_count": 0, "warning_count": 0, "skipped": True},
                    },
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 17, 15, 0))

        self.assertIn("配置变更流水线校验阻断，先修正变更草稿。", summary["operating_actions"])

    def test_daily_summary_shows_strategy_config_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "strategy-config-snapshot.json",
                {
                    "version_id": "CONFIG-VERSION-20260708-173000",
                    "generated_at": "2026-07-08T17:30:00",
                    "profile_hash": "4e0e64d3354b0d4bc865d57a0582e0119dd05a0074c612687a3f1a69705f3edd",
                    "profile": {"name": "personal-a-share-investment-profile"},
                    "source": {"regression": {"conclusion": "pass"}},
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 18, 0, 0))
            content = render_summary(summary)

        self.assertTrue(summary["strategy_config_snapshot"]["available"])
        self.assertEqual(summary["strategy_config_snapshot"]["version_id"], "CONFIG-VERSION-20260708-173000")
        self.assertEqual(summary["strategy_config_snapshot"]["profile_hash_short"], "4e0e64d3354b")
        self.assertIn("策略配置版本快照：已读取", content)
        self.assertIn("当前配置版本：CONFIG-VERSION-20260708-173000", content)
        self.assertIn("配置哈希：4e0e64d3354b", content)


if __name__ == "__main__":
    unittest.main()
