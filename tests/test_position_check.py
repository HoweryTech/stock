import copy
import unittest
from pathlib import Path

from tools.position_check import validate_position
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class PositionCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        self.position = load_yaml(ROOT / "templates/position.example.yaml")

    def test_default_position_is_normal(self) -> None:
        result = validate_position(self.profile, self.position)

        self.assertEqual(result["conclusion"], "normal")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["warnings"], [])

    def test_near_stop_loss_warns(self) -> None:
        position = copy.deepcopy(self.position)
        position["tracking"]["current_price"] = 19.0
        position["risk"]["stop_loss_price"] = 18.5

        result = validate_position(self.profile, position)
        warning_codes = {item["code"] for item in result["warnings"]}

        self.assertEqual(result["conclusion"], "warning")
        self.assertIn("near_stop_loss", warning_codes)

    def test_uses_profile_near_stop_warning_threshold(self) -> None:
        profile = copy.deepcopy(self.profile)
        profile["risk"]["near_stop_warning_pct"] = 1.0
        position = copy.deepcopy(self.position)
        position["tracking"]["current_price"] = 19.0
        position["risk"]["stop_loss_price"] = 18.5

        result = validate_position(profile, position)

        self.assertEqual(result["conclusion"], "normal")
        self.assertEqual(result["calculations"]["near_stop_warning_pct"], 1.0)

    def test_stop_loss_and_position_limits_need_action(self) -> None:
        position = copy.deepcopy(self.position)
        position["tracking"]["current_price"] = 18.4
        position["entry"]["position_pct_of_total_assets"] = 12.0
        position["portfolio_context"] = {
            "industry_position_pct": 30.0,
            "total_position_pct": 85.0,
        }

        result = validate_position(self.profile, position)
        action_codes = {item["code"] for item in result["actions"]}

        self.assertEqual(result["conclusion"], "needs_action")
        self.assertIn("stop_loss_triggered", action_codes)
        self.assertIn("stock_position_limit_exceeded", action_codes)
        self.assertIn("industry_position_limit_exceeded", action_codes)
        self.assertIn("total_position_limit_exceeded", action_codes)

    def test_imported_unconfirmed_stop_loss_does_not_trigger_exit_action(self) -> None:
        position = copy.deepcopy(self.position)
        position["strategy"]["source"] = "imported_holding"
        position["tracking"]["current_price"] = 6.01
        position["risk"]["stop_loss_price"] = 6.49
        position["risk"]["observation_items"] = ["止损价采用“当前价下方5%”作为当前存量仓位风险边界，需在下一次人工复核中确认。"]

        result = validate_position(self.profile, position)
        action_codes = {item["code"] for item in result["actions"]}
        warning_codes = {item["code"] for item in result["warnings"]}

        self.assertNotIn("stop_loss_triggered", action_codes)
        self.assertIn("unconfirmed_stop_loss_reference", warning_codes)
        self.assertFalse(result["calculations"]["stop_loss_confirmed"])

    def test_reference_only_stop_loss_stays_non_hard_stop(self) -> None:
        position = copy.deepcopy(self.position)
        position["strategy"]["source"] = "imported_holding"
        position["tracking"]["current_price"] = 6.01
        position["risk"]["stop_loss_price"] = 6.49
        position["risk"]["stop_loss_confirmed"] = False
        position["risk"]["stop_loss_confirmation_status"] = "reference_only"
        position["risk"]["stop_loss_confirmation_label"] = "仅保留参考"
        position["risk"]["observation_items"] = ["止损价采用“当前价下方5%”作为当前存量仓位风险边界，需在下一次人工复核中确认。"]

        result = validate_position(self.profile, position)
        action_codes = {item["code"] for item in result["actions"]}

        self.assertNotIn("stop_loss_triggered", action_codes)
        self.assertFalse(result["calculations"]["stop_loss_confirmed"])
        self.assertEqual(result["calculations"]["stop_loss_confirmation_status"], "reference_only")
        self.assertEqual(result["calculations"]["stop_loss_confirmation_label"], "仅保留参考")

    def test_missing_strategy_fields_warns(self) -> None:
        position = copy.deepcopy(self.position)
        position["strategy"]["buy_reason"] = ""
        position["risk"]["invalidation_conditions"] = []

        result = validate_position(self.profile, position)
        warning_codes = {item["code"] for item in result["warnings"]}

        self.assertEqual(result["conclusion"], "warning")
        self.assertIn("missing_buy_reason", warning_codes)
        self.assertIn("missing_invalidation_conditions", warning_codes)


if __name__ == "__main__":
    unittest.main()
