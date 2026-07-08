import unittest
from copy import deepcopy
from pathlib import Path

from tools.check_strategy_config_regression import check_regression
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    return load_yaml(ROOT / "config/investment-profile.example.yaml")


def audit() -> dict:
    return {
        "applied_at": "2026-07-08T15:00:00",
        "applied_by": "lihongwei",
        "backup": "data/backups/investment-profile.20260708-150000.yaml",
        "operation_count": 1,
        "operations": [{"path": "risk.max_position_pct_per_stock", "old_value": 10.0, "new_value": 8.0}],
    }


class CheckStrategyConfigRegressionTest(unittest.TestCase):
    def test_passes_valid_profile_with_audit(self) -> None:
        result = check_regression(profile(), audit())

        self.assertEqual(result["conclusion"], "pass")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["warnings"], [])
        self.assertTrue(any(item["code"] == "apply_audit_loaded" for item in result["info"]))

    def test_warns_when_audit_is_missing(self) -> None:
        result = check_regression(profile(), None)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "missing_apply_audit" for item in result["warnings"]))

    def test_blocks_unsafe_risk_field(self) -> None:
        data = profile()
        data["risk"]["max_position_pct_per_stock"] = 20.0

        result = check_regression(data, audit())

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "risk_field_out_of_safe_range" for item in result["blockers"]))

    def test_blocks_missing_preferred_strategy(self) -> None:
        data = profile()
        data = deepcopy(data)
        del data["strategies"]["trend_strength"]

        result = check_regression(data, audit())

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "preferred_strategy_missing" for item in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
