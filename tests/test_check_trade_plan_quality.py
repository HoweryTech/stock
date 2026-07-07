import copy
import tempfile
import unittest
from pathlib import Path

from tools.check_trade_plan_quality import check_trade_plan_quality, run_check
from tools.new_trade_plan_from_candidate import create_plan_from_candidate
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class CheckTradePlanQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = load_yaml(ROOT / "templates/trade-plan.example.yaml")

    def test_template_plan_is_blocked_by_placeholder_content(self) -> None:
        result = check_trade_plan_quality(self.plan)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "placeholder_strategy_buy_reason" for item in result["blockers"]))
        self.assertTrue(any(item["code"] == "missing_candidate_pool_trace" for item in result["warnings"]))

    def test_blocks_missing_exit_conditions_and_placeholder_stock(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["stock"]["name"] = "待补充"
        plan["exit_plan"]["take_profit_conditions"] = []
        plan["exit_plan"]["invalidation_conditions"] = []

        result = check_trade_plan_quality(plan)
        blocker_codes = {item["code"] for item in result["blockers"]}

        self.assertEqual(result["conclusion"], "blocked")
        self.assertIn("placeholder_stock_name", blocker_codes)
        self.assertIn("missing_exit_plan_take_profit_conditions", blocker_codes)
        self.assertIn("missing_exit_plan_invalidation_conditions", blocker_codes)

    def test_blocks_invalid_stop_loss(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["price_plan"]["stop_loss_price"] = 21.0

        result = check_trade_plan_quality(plan)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "invalid_stop_loss_price" for item in result["blockers"]))

    def test_candidate_generated_plan_is_blocked_until_exit_rules_are_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidate_pool = Path(tmp_dir) / "candidate_pool.csv"
            output = Path(tmp_dir) / "plan.yaml"
            candidate_pool.write_text(
                "code,strategies,primary_strategy,trend_score,value_quality_score,trade_date,report_period,reasons,risks\n"
                "300750,trend_strength|value_quality,multi_strategy,11.5,18.8,2026-07-02,2026-03-31,"
                "\"[trend_strength] 趋势强。 | [value_quality] PE 分位 68.00 <= 80.00。\","
                "\"[value_quality] 估值分位接近上限。\"\n",
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "candidates": str(candidate_pool),
                    "profile": str(ROOT / "config/investment-profile.example.yaml"),
                    "template": str(ROOT / "templates/trade-plan.example.yaml"),
                    "output_dir": "plans",
                    "output": str(output),
                    "overwrite": False,
                    "id": "TP-CHECK-0001",
                    "code": "300750",
                    "name": "宁德时代",
                    "exchange": "SZSE",
                    "industry": "电力设备",
                    "strategy": None,
                    "timeframe": None,
                    "buy_reason": None,
                    "planned_buy_price": 200.0,
                    "current_price": None,
                    "stop_loss_price": 185.0,
                    "position_pct": 5.0,
                    "current_stock_pct": 0.0,
                    "current_industry_pct": 0.0,
                    "current_total_pct": 0.0,
                    "stop_loss_condition": [],
                    "take_profit_condition": [],
                    "invalidation_condition": [],
                    "observation_item": [],
                },
            )()

            plan, _ = create_plan_from_candidate(args)
            result = check_trade_plan_quality(plan)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "missing_exit_plan_take_profit_conditions" for item in result["blockers"]))
        self.assertFalse(any(item["code"] == "missing_candidate_pool_trace" for item in result["warnings"]))

    def test_run_check_reads_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = Path(tmp_dir) / "plan.yaml"
            import yaml

            plan_path.write_text(yaml.safe_dump(self.plan, allow_unicode=True, sort_keys=False), encoding="utf-8")

            result = run_check(plan_path)

        self.assertEqual(result["trade_plan_id"], "TP-YYYYMMDD-0001")


if __name__ == "__main__":
    unittest.main()
