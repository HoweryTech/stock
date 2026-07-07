import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml, validate_plan


ROOT = Path(__file__).resolve().parents[1]


def args(**overrides):
    defaults = {
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-plan.example.yaml"),
        "output_dir": "plans",
        "output": None,
        "overwrite": False,
        "id": "TP-TEST-0001",
        "code": "600000",
        "name": "测试股票",
        "exchange": "SSE",
        "industry": "银行",
        "is_st": False,
        "is_suspended": False,
        "has_delisting_risk": False,
        "abnormal_trading_status": False,
        "strategy": "trend_strength",
        "timeframe": "swing",
        "buy_reason": "测试买入理由",
        "key_evidence": ["测试关键证据"],
        "risk": ["测试风险"],
        "stop_loss_condition": ["跌破止损价。"],
        "take_profit_condition": ["达到目标区后分批止盈。"],
        "invalidation_condition": ["趋势强度消失。"],
        "observation_item": ["观察成交额。"],
        "planned_buy_price": 10.0,
        "current_price": None,
        "stop_loss_price": 9.2,
        "position_pct": 5.0,
        "current_stock_pct": 0.0,
        "current_industry_pct": 10.0,
        "current_total_pct": 40.0,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewTradePlanTest(unittest.TestCase):
    def test_creates_trade_plan_with_derived_risk_fields(self) -> None:
        plan, output_path = create_trade_plan(args())

        self.assertEqual(output_path, Path("plans/TP-TEST-0001.yaml"))
        self.assertEqual(plan["trade_plan"]["id"], "TP-TEST-0001")
        self.assertEqual(plan["stock"]["code"], "600000")
        self.assertEqual(plan["price_plan"]["current_price"], 10.0)
        self.assertEqual(plan["price_plan"]["max_acceptable_buy_price"], 10.3)
        self.assertEqual(plan["position_plan"]["expected_industry_position_pct_after_buy"], 15.0)
        self.assertEqual(plan["risk_calculation"]["max_loss_pct_of_total_assets"], 0.4)
        self.assertEqual(plan["exit_plan"]["invalidation_conditions"], ["趋势强度消失。"])

    def test_generated_plan_can_be_written_and_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "plan.yaml"
            plan, _ = create_trade_plan(args(output=str(output)))
            write_yaml(output, plan)

            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
            written_plan = load_yaml(output)
            result = validate_plan(profile, written_plan)

        self.assertEqual(result["conclusion"], "needs_confirmation")
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
