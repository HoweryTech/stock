import unittest

from tools.check_strategy_config_changes import check_changes
from tools.risk_check import load_yaml

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    return load_yaml(ROOT / "config/investment-profile.example.yaml")


def changes_doc(status: str = "approved", path: str = "risk.max_position_pct_per_stock", proposed_value=8.0) -> dict:
    return {
        "drafts": [
            {
                "id": "CONFIG-CHANGE-RISK",
                "status": status,
                "effective_date": "2026-07-09",
                "change_items": [
                    {
                        "path": path,
                        "proposed_change": "调整风险参数。",
                        "proposed_value": proposed_value,
                    }
                ],
                "approval": {"required": True, "approved_by": "lihongwei", "approved_at": "2026-07-08T13:00:00"},
            }
        ]
    }


class CheckStrategyConfigChangesTest(unittest.TestCase):
    def test_passes_approved_safe_risk_change(self) -> None:
        result = check_changes(profile(), changes_doc())

        self.assertEqual(result["conclusion"], "pass")
        self.assertEqual(result["blockers"], [])
        self.assertTrue(any(item["code"] == "risk_value_within_safe_range" for item in result["info"]))

    def test_blocks_unapproved_change(self) -> None:
        result = check_changes(profile(), changes_doc(status="draft"))

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "change_not_approved" for item in result["blockers"]))

    def test_blocks_missing_path(self) -> None:
        result = check_changes(profile(), changes_doc(path="strategies.trend_strength.discipline.exception_position_limit_pct", proposed_value=2.0))

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "change_path_not_found" for item in result["blockers"]))

    def test_blocks_unsafe_risk_value(self) -> None:
        result = check_changes(profile(), changes_doc(proposed_value=20.0))

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "unsafe_risk_value" for item in result["blockers"]))

    def test_warns_missing_proposed_value(self) -> None:
        data = changes_doc()
        del data["drafts"][0]["change_items"][0]["proposed_value"]

        result = check_changes(profile(), data)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "missing_proposed_value" for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
