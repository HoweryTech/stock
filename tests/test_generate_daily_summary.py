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
        exit_executions=[str(base / "exit-executions/*.yaml")],
        reviews=[str(base / "reviews/*.yaml")],
        review_analysis=str(base / "review-analysis.json"),
        cooldown_check=str(base / "review-cooldown.json"),
        strategy_health=str(base / "strategy-health.json"),
        strategy_review_tasks=str(base / "strategy-review-tasks.json"),
        strategy_config_changes=str(base / "strategy-config-changes.json"),
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
        self.assertIn("元数据状态：缺失", content)

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
        self.assertEqual(summary["strategy_review_tasks"]["deferred_task_count"], 1)
        self.assertEqual(summary["strategy_review_tasks"]["resolved_task_count"], 1)
        self.assertIn("处理 1 个未完成策略复核任务。", summary["operating_actions"])
        self.assertIn("复查 1 个暂缓策略复核任务。", summary["operating_actions"])
        self.assertIn("STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW", content)

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
                            "status": "draft",
                            "change_items": [{"path": "strategies.value_quality.screening"}],
                            "approval": {"required": True, "approved_by": "lihongwei", "approved_at": "2026-07-08T12:00:00"},
                        },
                    ],
                },
            )

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 11, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["strategy_config_changes"]["draft_count"], 2)
        self.assertEqual(summary["strategy_config_changes"]["pending_approval_count"], 1)
        self.assertIn("审批或驳回 1 个策略配置变更草稿。", summary["operating_actions"])
        self.assertIn("CONFIG-CHANGE-TREND", content)
        self.assertNotIn("CONFIG-CHANGE-VALUE strategy=value_quality", content)


if __name__ == "__main__":
    unittest.main()
