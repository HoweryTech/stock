import copy
import unittest
from pathlib import Path

from tools.risk_check import load_yaml, validate_plan


ROOT = Path(__file__).resolve().parents[1]


class RiskCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        self.plan = load_yaml(ROOT / "templates/trade-plan.example.yaml")

    def test_default_trade_plan_needs_confirmation_without_blockers(self) -> None:
        result = validate_plan(self.profile, self.plan)

        self.assertEqual(result["conclusion"], "needs_confirmation")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["calculations"]["calculated_max_loss_pct_of_total_assets"], 0.6)

    def test_blocks_when_price_risk_and_position_limits_are_exceeded(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["price_plan"]["current_price"] = 22.0
        plan["position_plan"]["planned_position_pct_of_total_assets"] = 20.0
        plan["position_plan"]["expected_stock_position_pct_after_buy"] = 20.0
        plan["position_plan"]["expected_total_position_pct_after_buy"] = 95.0

        result = validate_plan(self.profile, plan)
        blocker_codes = {item["code"] for item in result["blockers"]}

        self.assertEqual(result["conclusion"], "blocked")
        self.assertIn("price_too_far_above_plan", blocker_codes)
        self.assertIn("risk_per_trade_exceeded", blocker_codes)
        self.assertIn("stock_position_limit_exceeded", blocker_codes)
        self.assertIn("total_position_limit_exceeded", blocker_codes)


if __name__ == "__main__":
    unittest.main()

