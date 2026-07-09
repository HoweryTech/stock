import json
import tempfile
import unittest
from pathlib import Path

from tools.manual_confirmation import (
    confirmation_is_confirmed,
    confirmation_snapshot_confirmed,
    load_confirmation_record,
    validate_manual_confirmation_required,
)


class ManualConfirmationTest(unittest.TestCase):
    def test_loads_confirmed_record_by_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "manual-confirmations.json"
            path.write_text(
                json.dumps(
                    {
                        "confirmations": [
                            {
                                "id": "CONFIRM-TEST-0001",
                                "status": "confirmed",
                                "confirmed_by": "lihongwei",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            record = load_confirmation_record(path, "CONFIRM-TEST-0001")

        self.assertTrue(record["available"])
        self.assertTrue(confirmation_is_confirmed(record))
        self.assertEqual(record["confirmed_by"], "lihongwei")

    def test_missing_confirmation_id_returns_missing_record(self) -> None:
        record = load_confirmation_record(None, None)

        self.assertFalse(record["available"])
        self.assertEqual(record["status"], "missing")
        self.assertIsNone(record["id"])

    def test_missing_file_returns_requested_id(self) -> None:
        record = load_confirmation_record(Path("/tmp/not-exists-manual-confirmations.json"), "CONFIRM-MISSING")

        self.assertFalse(record["available"])
        self.assertEqual(record["status"], "missing")
        self.assertEqual(record["id"], "CONFIRM-MISSING")

    def test_validate_required_rejects_unconfirmed_record(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmed manual confirmation"):
            validate_manual_confirmation_required(True, {"available": True, "status": "open", "id": "CONFIRM-OPEN"})

    def test_validate_required_ignores_optional_missing_record(self) -> None:
        validate_manual_confirmation_required(False, {"available": False, "status": "missing", "id": None})

    def test_detects_confirmed_snapshot(self) -> None:
        self.assertTrue(confirmation_snapshot_confirmed({"confirmation_snapshot": {"available": True, "status": "confirmed"}}))

    def test_rejects_missing_snapshot(self) -> None:
        self.assertFalse(confirmation_snapshot_confirmed({"confirmation_snapshot": {"available": False, "status": "missing"}}))
        self.assertFalse(confirmation_snapshot_confirmed({}))


if __name__ == "__main__":
    unittest.main()
