import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_position import calculate_return, create_position
from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def plan_args(**overrides):
    defaults = {
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-plan.example.yaml"),
        "output_dir": "plans",
        "output": None,
        "overwrite": False,
        "id": "TP-POS-0001",
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


def position_args(plan_path: Path, **overrides):
    defaults = {
        "template": str(ROOT / "templates/position.example.yaml"),
        "plan": str(plan_path),
        "output_dir": "positions",
        "output": None,
        "overwrite": False,
        "id": "POS-TEST-0001",
        "status": "normal",
        "entry_date": "2026-07-07",
        "entry_price": 10.0,
        "current_price": 10.5,
        "position_pct": 5.0,
        "shares": 1000,
        "stop_loss_price": None,
        "days_held": 2,
        "note": ["按计划建仓。"],
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewPositionTest(unittest.TestCase):
    def test_calculates_position_returns(self) -> None:
        self.assertEqual(calculate_return(10.0, 10.5, 5.0), (5.0, 0.25))
        self.assertEqual(calculate_return(10.0, 9.2, 5.0), (-8.0, -0.4))

    def test_creates_position_from_trade_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan, _ = create_trade_plan(plan_args())
            plan_path = Path(tmp_dir) / "plan.yaml"
            write_yaml(plan_path, plan)

            position, output_path = create_position(position_args(plan_path))

        self.assertEqual(output_path, Path("positions/POS-TEST-0001.yaml"))
        self.assertEqual(position["position"]["source_trade_plan_id"], "TP-POS-0001")
        self.assertEqual(position["stock"]["code"], "600000")
        self.assertEqual(position["entry"]["entry_price"], 10.0)
        self.assertEqual(position["risk"]["stop_loss_price"], 9.2)
        self.assertEqual(position["tracking"]["current_return_pct"], 5.0)
        self.assertEqual(position["tracking"]["current_portfolio_return_pct"], 0.25)
        self.assertEqual(position["strategy"]["buy_reason"], "测试买入理由")
        self.assertEqual(position["trade_plan_snapshot"]["trade_plan"]["id"], "TP-POS-0001")

    def test_position_template_is_valid_yaml(self) -> None:
        template = load_yaml(ROOT / "templates/position.example.yaml")
        self.assertEqual(template["position"]["id"], "POS-YYYYMMDD-0001")


if __name__ == "__main__":
    unittest.main()
