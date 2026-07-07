import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.new_trade_review import create_trade_review
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def plan_args(**overrides):
    defaults = {
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-plan.example.yaml"),
        "output_dir": "plans",
        "output": None,
        "overwrite": False,
        "id": "TP-REVIEW-0001",
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


def review_args(plan_path: Path, **overrides):
    defaults = {
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-review.example.yaml"),
        "plan": str(plan_path),
        "output_dir": "reviews",
        "output": None,
        "overwrite": False,
        "id": "TR-TEST-0001",
        "entry_date": "2026-07-01",
        "exit_date": "2026-07-07",
        "entry_price": 10.0,
        "exit_price": 10.8,
        "position_pct": 5.0,
        "exit_reason": "达到目标区后退出。",
        "followed_plan": True,
        "result_category": None,
        "error_tag": [],
        "lesson": "按计划执行。",
        "next_action": "归档并继续观察。",
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewTradeReviewTest(unittest.TestCase):
    def test_creates_review_from_trade_plan_and_infers_strategy_profit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan, _ = create_trade_plan(plan_args())
            plan_path = Path(tmp_dir) / "plan.yaml"
            write_yaml(plan_path, plan)

            review, output_path = create_trade_review(review_args(plan_path))

        self.assertEqual(output_path, Path("reviews/TR-TEST-0001.yaml"))
        self.assertEqual(review["review"]["source_trade_plan_id"], "TP-REVIEW-0001")
        self.assertEqual(review["stock"]["code"], "600000")
        self.assertEqual(review["result"]["trade_return_pct"], 8.0)
        self.assertEqual(review["result"]["portfolio_return_pct"], 0.4)
        self.assertEqual(review["result"]["result_category"], "strategy_profit")
        self.assertEqual(review["trade_plan_snapshot"]["strategy"]["source"], "trend_strength")

    def test_infers_execution_error_loss_when_plan_was_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan, _ = create_trade_plan(plan_args())
            plan_path = Path(tmp_dir) / "plan.yaml"
            write_yaml(plan_path, plan)

            review, _ = create_trade_review(
                review_args(
                    plan_path,
                    exit_price=9.0,
                    followed_plan=False,
                    error_tag=["late_stop_loss"],
                )
            )

        self.assertEqual(review["result"]["trade_return_pct"], -10.0)
        self.assertEqual(review["result"]["portfolio_return_pct"], -0.5)
        self.assertEqual(review["result"]["result_category"], "execution_error_loss")
        self.assertEqual(review["result"]["error_tags"], ["late_stop_loss"])

    def test_rejects_unknown_error_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan, _ = create_trade_plan(plan_args())
            plan_path = Path(tmp_dir) / "plan.yaml"
            write_yaml(plan_path, plan)

            with self.assertRaises(ValueError):
                create_trade_review(review_args(plan_path, error_tag=["unknown_tag"]))

    def test_review_template_is_valid_yaml(self) -> None:
        template = load_yaml(ROOT / "templates/trade-review.example.yaml")
        self.assertEqual(template["review"]["id"], "TR-YYYYMMDD-0001")


if __name__ == "__main__":
    unittest.main()
