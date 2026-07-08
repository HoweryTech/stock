import unittest
from datetime import datetime

from tools.update_strategy_config_change import update_change


def changes_doc() -> dict:
    return {
        "generated_at": "2026-07-08T12:00:00",
        "draft_count": 1,
        "drafts": [
            {
                "id": "CONFIG-CHANGE-TREND",
                "source_task_id": "STRATEGY-REVIEW-TREND",
                "strategy": "trend_strength",
                "status": "draft",
                "effective_date": None,
                "approval": {
                    "required": True,
                    "approved_by": "",
                    "approved_at": None,
                    "approval_reason": "",
                    "rejected_by": "",
                    "rejected_at": None,
                    "rejected_reason": "",
                },
                "history": [],
            }
        ],
    }


class UpdateStrategyConfigChangeTest(unittest.TestCase):
    def test_approves_change_with_effective_date_and_history(self) -> None:
        data = changes_doc()

        draft = update_change(
            data,
            change_id="CONFIG-CHANGE-TREND",
            action="approve",
            actor="lihongwei",
            reason="复核通过。",
            effective_date="2026-07-09",
            updated_at=datetime(2026, 7, 8, 13, 0, 0),
        )

        self.assertEqual(draft["status"], "approved")
        self.assertEqual(draft["effective_date"], "2026-07-09")
        self.assertEqual(draft["approval"]["approved_by"], "lihongwei")
        self.assertEqual(draft["approval"]["approved_at"], "2026-07-08T13:00:00")
        self.assertEqual(draft["approval"]["approval_reason"], "复核通过。")
        self.assertEqual(len(draft["history"]), 1)
        self.assertEqual(draft["history"][0]["action"], "approve")
        self.assertEqual(data["approved_count"], 1)
        self.assertEqual(data["pending_approval_count"], 0)

    def test_reject_requires_reason_and_records_rejection(self) -> None:
        data = changes_doc()

        with self.assertRaisesRegex(ValueError, "reject requires"):
            update_change(data, change_id="CONFIG-CHANGE-TREND", action="reject", actor="lihongwei")

        draft = update_change(
            data,
            change_id="CONFIG-CHANGE-TREND",
            action="reject",
            actor="lihongwei",
            reason="证据不足。",
            updated_at=datetime(2026, 7, 8, 13, 30, 0),
        )

        self.assertEqual(draft["status"], "rejected")
        self.assertEqual(draft["approval"]["rejected_by"], "lihongwei")
        self.assertEqual(draft["approval"]["rejected_reason"], "证据不足。")
        self.assertEqual(data["rejected_count"], 1)

    def test_reopens_approved_change(self) -> None:
        data = changes_doc()
        update_change(
            data,
            change_id="CONFIG-CHANGE-TREND",
            action="approve",
            actor="lihongwei",
            effective_date="2026-07-09",
            updated_at=datetime(2026, 7, 8, 13, 0, 0),
        )

        draft = update_change(
            data,
            change_id="CONFIG-CHANGE-TREND",
            action="reopen",
            actor="lihongwei",
            reason="新证据需要补充。",
            updated_at=datetime(2026, 7, 8, 14, 0, 0),
        )

        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["approval"]["approved_by"], "")
        self.assertIsNone(draft["approval"]["approved_at"])
        self.assertEqual(draft["approval"]["approval_reason"], "")
        self.assertEqual(len(draft["history"]), 2)
        self.assertEqual(data["pending_approval_count"], 1)

    def test_missing_change_raises_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "not found"):
            update_change(changes_doc(), change_id="UNKNOWN", action="approve", actor="lihongwei")

    def test_config_version_approval_requires_reason(self) -> None:
        data = changes_doc()
        draft = data["drafts"][0]
        draft["source_task_type"] = "config_version"
        draft["config_version_id"] = "CONFIG-VERSION-RISK"

        with self.assertRaisesRegex(ValueError, "config version approval requires"):
            update_change(
                data,
                change_id="CONFIG-CHANGE-TREND",
                action="approve",
                actor="lihongwei",
                updated_at=datetime(2026, 7, 8, 13, 0, 0),
            )


if __name__ == "__main__":
    unittest.main()
