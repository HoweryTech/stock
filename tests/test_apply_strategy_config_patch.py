import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.apply_strategy_config_patch import apply_patch_file, apply_patch_to_profile
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def patch_doc(old_value=10.0, new_value=8.0, check_conclusion="pass") -> dict:
    return {
        "check_conclusion": check_conclusion,
        "apply_mode": "manual_review_required",
        "operation_count": 1,
        "operations": [
            {
                "op": "replace",
                "path": "risk.max_position_pct_per_stock",
                "old_value": old_value,
                "new_value": new_value,
                "source_change_id": "CONFIG-CHANGE-RISK",
                "source_task_id": "STRATEGY-REVIEW-RISK",
                "reason": "降低单票仓位。",
            }
        ],
    }


class ApplyStrategyConfigPatchTest(unittest.TestCase):
    def test_applies_patch_to_profile_and_writes_backup_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            profile_path = base / "investment-profile.yaml"
            patch_path = base / "patch.json"
            audit_path = base / "audit.json"
            backup_dir = base / "backups"
            write_yaml(profile_path, load_yaml(ROOT / "config/investment-profile.example.yaml"), overwrite=True)
            patch_path.write_text(json.dumps(patch_doc(), ensure_ascii=False), encoding="utf-8")

            audit = apply_patch_file(
                profile_path,
                patch_path,
                backup_dir=backup_dir,
                audit_output=audit_path,
                applied_by="lihongwei",
                applied_at=datetime(2026, 7, 8, 15, 0, 0),
            )
            updated = load_yaml(profile_path)
            backup_exists = Path(audit["backup"]).exists()
            audit_exists = audit_path.exists()

        self.assertEqual(updated["risk"]["max_position_pct_per_stock"], 8.0)
        self.assertEqual(audit["operation_count"], 1)
        self.assertEqual(audit["operations"][0]["old_value"], 10.0)
        self.assertEqual(audit["operations"][0]["new_value"], 8.0)
        self.assertTrue(backup_exists)
        self.assertTrue(audit_exists)

    def test_blocks_when_current_value_does_not_match_patch_old_value(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        with self.assertRaisesRegex(ValueError, "current value mismatch"):
            apply_patch_to_profile(profile, patch_doc(old_value=9.0), applied_by="lihongwei")

    def test_requires_passed_patch_check(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        with self.assertRaisesRegex(ValueError, "check_conclusion must be pass"):
            apply_patch_to_profile(profile, patch_doc(check_conclusion="blocked"), applied_by="lihongwei")

    def test_requires_applied_by(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        with self.assertRaisesRegex(ValueError, "applied_by is required"):
            apply_patch_to_profile(profile, patch_doc(), applied_by="")


if __name__ == "__main__":
    unittest.main()
