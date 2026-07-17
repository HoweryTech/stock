import argparse
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.apply_stop_loss_confirmation import apply_stop_loss_confirmation


def args(base: Path, **overrides):
    values = {
        "positions": [str(base / "POS-EASTMONEY-000001.yaml")],
        "code": "000001",
        "action": "confirm_hard_stop",
        "stop_loss_price": 9.8,
        "current_price": 9.75,
        "dynamic_source": "atr_buffer",
        "reason": "ATR缓冲价接近现价。",
        "note": "人工确认",
        "source": "dashboard",
        "confirmed_at": "2026-07-17T10:30:00+08:00",
        "audit_output": str(base / "audit.jsonl"),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ApplyStopLossConfirmationTest(unittest.TestCase):
    def write_position(self, base: Path) -> Path:
        path = base / "POS-EASTMONEY-000001.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "stock": {"code": "000001", "name": "测试股"},
                    "risk": {"stop_loss_price": 9.5},
                    "entry": {"shares": 100.0, "entry_price": 10.0},
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_confirm_hard_stop_updates_risk_fields_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = self.write_position(base)

            result, written_path = apply_stop_loss_confirmation(args(base))

            position = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(written_path, path)
            self.assertEqual(result["confirmation"]["action"], "confirm_hard_stop")
            self.assertTrue(position["risk"]["stop_loss_confirmed"])
            self.assertEqual(position["risk"]["stop_loss_confirmation_status"], "hard_stop")
            self.assertEqual(position["risk"]["stop_loss_confirmation_source"], "atr_buffer")
            self.assertEqual(position["stop_loss_confirmation_history"][0]["current_price"], 9.75)
            self.assertTrue((base / "audit.jsonl").read_text(encoding="utf-8").strip())

    def test_keep_reference_does_not_confirm_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = self.write_position(base)

            apply_stop_loss_confirmation(args(base, action="keep_reference", stop_loss_price=9.7))

            position = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertFalse(position["risk"]["stop_loss_confirmed"])
            self.assertEqual(position["risk"]["stop_loss_confirmation_status"], "reference_only")
            self.assertEqual(position["stop_loss_confirmation_history"][0]["action_label"], "仅保留参考")


if __name__ == "__main__":
    unittest.main()
