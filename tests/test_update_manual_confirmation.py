import unittest
from datetime import datetime

from tools.update_manual_confirmation import update_confirmation


class UpdateManualConfirmationTest(unittest.TestCase):
    def test_confirms_new_manual_item_with_history(self) -> None:
        doc = {"confirmations": []}

        item = update_confirmation(
            doc,
            confirmation_id="CONFIRM-CONFIG-CHANGE-RISK",
            action="confirm",
            actor="lihongwei",
            reason="已核对旧值、新值和来源证据。",
            subject_type="config_change",
            subject_id="CONFIG-CHANGE-RISK",
            text="人工复核待应用配置补丁。",
            updated_at=datetime(2026, 7, 8, 14, 0, 0),
        )

        self.assertEqual(item["status"], "confirmed")
        self.assertEqual(item["confirmed_by"], "lihongwei")
        self.assertEqual(item["confirmed_at"], "2026-07-08T14:00:00")
        self.assertEqual(item["confirmation_reason"], "已核对旧值、新值和来源证据。")
        self.assertEqual(item["subject_type"], "config_change")
        self.assertEqual(item["subject_id"], "CONFIG-CHANGE-RISK")
        self.assertEqual(len(item["history"]), 1)
        self.assertEqual(doc["confirmed_count"], 1)
        self.assertEqual(doc["open_count"], 0)

    def test_reject_requires_reason_and_records_rejection(self) -> None:
        doc = {"confirmations": []}

        with self.assertRaisesRegex(ValueError, "reject requires"):
            update_confirmation(doc, confirmation_id="CONFIRM-EXIT", action="reject", actor="lihongwei", reason="")

        item = update_confirmation(
            doc,
            confirmation_id="CONFIRM-EXIT",
            action="reject",
            actor="lihongwei",
            reason="退出证据不足。",
            updated_at=datetime(2026, 7, 8, 14, 30, 0),
        )

        self.assertEqual(item["status"], "rejected")
        self.assertEqual(item["rejected_by"], "lihongwei")
        self.assertEqual(item["rejected_reason"], "退出证据不足。")
        self.assertEqual(doc["rejected_count"], 1)

    def test_reopens_confirmed_item(self) -> None:
        doc = {"confirmations": []}
        update_confirmation(
            doc,
            confirmation_id="CONFIRM-CONFIG-CHANGE-RISK",
            action="confirm",
            actor="lihongwei",
            reason="已确认。",
            updated_at=datetime(2026, 7, 8, 14, 0, 0),
        )

        item = update_confirmation(
            doc,
            confirmation_id="CONFIRM-CONFIG-CHANGE-RISK",
            action="reopen",
            actor="lihongwei",
            reason="新增证据需要复核。",
            updated_at=datetime(2026, 7, 8, 15, 0, 0),
        )

        self.assertEqual(item["status"], "open")
        self.assertEqual(item["confirmed_by"], "")
        self.assertIsNone(item["confirmed_at"])
        self.assertEqual(item["confirmation_reason"], "")
        self.assertEqual(len(item["history"]), 2)
        self.assertEqual(doc["open_count"], 1)

    def test_confirm_requires_actor_and_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "actor is required"):
            update_confirmation({}, confirmation_id="CONFIRM-1", action="confirm", actor="", reason="已确认。")
        with self.assertRaisesRegex(ValueError, "confirm requires"):
            update_confirmation({}, confirmation_id="CONFIRM-1", action="confirm", actor="lihongwei", reason="")


if __name__ == "__main__":
    unittest.main()
