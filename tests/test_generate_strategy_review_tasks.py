import unittest
from datetime import datetime

from tools.generate_strategy_review_tasks import build_tasks, render_tasks


class GenerateStrategyReviewTasksTest(unittest.TestCase):
    def test_builds_tasks_for_paused_and_review_strategies(self) -> None:
        health = {
            "conclusion": "pause_required",
            "strategies": [
                {
                    "strategy": "trend_strength",
                    "status": "pause_new_entries",
                    "discipline_exception_loss_count": 0,
                    "stats": {"count": 3, "win_rate_pct": 0.0},
                    "actions": [
                        {
                            "code": "strategy_cooldown_required",
                            "message": "策略 trend_strength 已触发连续亏损冷静期。",
                        }
                    ],
                },
                {
                    "strategy": "value_quality",
                    "status": "needs_review",
                    "discipline_exception_loss_count": 1,
                    "stats": {"count": 3, "win_rate_pct": 66.6667},
                    "actions": [
                        {
                            "code": "loss_making_discipline_exception",
                            "message": "策略 value_quality 存在 1 笔亏损纪律例外交易，需要复查破例规则。",
                        }
                    ],
                },
                {
                    "strategy": "event_catalyst",
                    "status": "healthy",
                    "actions": [],
                },
            ],
        }

        result = build_tasks(health, generated_at=datetime(2026, 7, 8, 10, 0, 0))
        content = render_tasks(result)

        self.assertEqual(result["task_count"], 2)
        self.assertEqual(result["tasks"][0]["id"], "STRATEGY-REVIEW-TREND-STRENGTH-PAUSE-NEW-ENTRIES")
        self.assertEqual(result["tasks"][0]["priority"], "high")
        self.assertEqual(result["tasks"][0]["task_status"], "open")
        self.assertEqual(result["tasks"][0]["resolution"], "")
        self.assertIsNone(result["tasks"][0]["resolved_at"])
        self.assertEqual(result["tasks"][0]["history"], [])
        self.assertEqual(result["tasks"][1]["priority"], "medium")
        self.assertIn("复查纪律例外的触发条件", result["tasks"][1]["required_review_items"][0])
        self.assertIn("STRATEGY-REVIEW-VALUE-QUALITY-NEEDS-REVIEW", content)
        self.assertIn("亏损纪律例外交易", content)
        self.assertNotIn("event_catalyst", content)

    def test_renders_empty_task_list(self) -> None:
        result = build_tasks({"conclusion": "healthy", "strategies": []}, generated_at=datetime(2026, 7, 8, 10, 0, 0))
        content = render_tasks(result)

        self.assertEqual(result["task_count"], 0)
        self.assertIn("无待复核策略", content)


if __name__ == "__main__":
    unittest.main()
