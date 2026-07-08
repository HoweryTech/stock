import unittest
from datetime import datetime
from pathlib import Path

from tools.generate_strategy_config_patch import build_patch, render_patch
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    return load_yaml(ROOT / "config/investment-profile.example.yaml")


def changes_doc() -> dict:
    return {
        "drafts": [
            {
                "id": "CONFIG-CHANGE-RISK",
                "source_task_id": "STRATEGY-REVIEW-RISK",
                "status": "approved",
                "resolution": "降低单票仓位。",
                "change_items": [
                    {
                        "path": "risk.max_position_pct_per_stock",
                        "proposed_value": 8.0,
                        "reason": "连续亏损后降低风险暴露。",
                    }
                ],
            }
        ]
    }


class GenerateStrategyConfigPatchTest(unittest.TestCase):
    def test_builds_patch_from_passed_check(self) -> None:
        patch = build_patch(profile(), changes_doc(), {"conclusion": "pass"}, generated_at=datetime(2026, 7, 8, 14, 0, 0))
        content = render_patch(patch)

        self.assertEqual(patch["operation_count"], 1)
        self.assertEqual(patch["operations"][0]["op"], "replace")
        self.assertEqual(patch["operations"][0]["path"], "risk.max_position_pct_per_stock")
        self.assertEqual(patch["operations"][0]["old_value"], 10.0)
        self.assertEqual(patch["operations"][0]["new_value"], 8.0)
        self.assertIn("待应用策略配置补丁", content)
        self.assertIn("CONFIG-CHANGE-RISK", content)

    def test_requires_passed_check(self) -> None:
        with self.assertRaisesRegex(ValueError, "must pass"):
            build_patch(profile(), changes_doc(), {"conclusion": "blocked"})

    def test_ignores_unapproved_or_incomplete_items(self) -> None:
        data = changes_doc()
        data["drafts"][0]["status"] = "draft"
        data["drafts"].append(
            {
                "id": "CONFIG-CHANGE-NO-VALUE",
                "source_task_id": "STRATEGY-REVIEW-NO-VALUE",
                "status": "approved",
                "change_items": [{"path": "risk.max_position_pct_per_stock"}],
            }
        )

        patch = build_patch(profile(), data, {"conclusion": "pass"}, generated_at=datetime(2026, 7, 8, 14, 0, 0))

        self.assertEqual(patch["operation_count"], 0)


if __name__ == "__main__":
    unittest.main()
