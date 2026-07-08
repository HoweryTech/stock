import unittest
from datetime import datetime

from tools.update_strategy_review_task import update_task


def tasks_doc() -> dict:
    return {
        "generated_at": "2026-07-08T10:00:00",
        "task_count": 1,
        "tasks": [
            {
                "id": "STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
                "strategy": "trend_strength",
                "status": "needs_review",
                "task_status": "open",
                "resolution": "",
                "resolved_at": None,
                "history": [],
            }
        ],
    }


class UpdateStrategyReviewTaskTest(unittest.TestCase):
    def test_resolves_task_with_history(self) -> None:
        data = tasks_doc()

        task = update_task(
            data,
            task_id="STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
            status="resolved",
            resolution="维持策略，但降低例外仓位上限到 2%。",
            updated_by="lihongwei",
            updated_at=datetime(2026, 7, 8, 11, 0, 0),
        )

        self.assertEqual(task["task_status"], "resolved")
        self.assertEqual(task["resolved_at"], "2026-07-08T11:00:00")
        self.assertEqual(task["updated_by"], "lihongwei")
        self.assertEqual(len(task["history"]), 1)
        self.assertEqual(task["history"][0]["from_status"], "open")
        self.assertEqual(task["history"][0]["to_status"], "resolved")
        self.assertEqual(data["open_task_count"], 0)
        self.assertEqual(data["resolved_task_count"], 1)
        self.assertEqual(data["deferred_task_count"], 0)

    def test_final_status_requires_resolution(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a non-empty resolution"):
            update_task(
                tasks_doc(),
                task_id="STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
                status="deferred",
                resolution="",
                updated_by="lihongwei",
            )

    def test_reopens_task_and_clears_resolved_at(self) -> None:
        data = tasks_doc()
        update_task(
            data,
            task_id="STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
            status="resolved",
            resolution="先暂停。",
            updated_by="lihongwei",
            updated_at=datetime(2026, 7, 8, 11, 0, 0),
        )

        task = update_task(
            data,
            task_id="STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
            status="open",
            resolution="新复盘显示仍需讨论。",
            updated_by="lihongwei",
            updated_at=datetime(2026, 7, 8, 12, 0, 0),
        )

        self.assertEqual(task["task_status"], "open")
        self.assertIsNone(task["resolved_at"])
        self.assertEqual(len(task["history"]), 2)
        self.assertEqual(data["open_task_count"], 1)
        self.assertEqual(data["resolved_task_count"], 0)

    def test_missing_task_raises_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "task not found"):
            update_task(
                tasks_doc(),
                task_id="UNKNOWN",
                status="open",
                resolution="",
                updated_by="lihongwei",
            )


if __name__ == "__main__":
    unittest.main()
