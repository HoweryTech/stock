import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_position import create_position
from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml
from tools.update_position_daily import run_update, update_position_tracking


ROOT = Path(__file__).resolve().parents[1]


def plan_args() -> Namespace:
    return Namespace(
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        template=str(ROOT / "templates/trade-plan.example.yaml"),
        output_dir="plans",
        output=None,
        overwrite=False,
        id="TP-DAILY-0001",
        code="600000",
        name="测试股票",
        exchange="SSE",
        industry="银行",
        is_st=False,
        is_suspended=False,
        has_delisting_risk=False,
        abnormal_trading_status=False,
        strategy="trend_strength",
        timeframe="swing",
        buy_reason="测试买入理由",
        key_evidence=["测试关键证据"],
        risk=["测试风险"],
        stop_loss_condition=["跌破止损价。"],
        take_profit_condition=["达到目标区后分批止盈。"],
        invalidation_condition=["趋势强度消失。"],
        observation_item=["观察成交额。"],
        planned_buy_price=10.0,
        current_price=None,
        stop_loss_price=9.2,
        position_pct=5.0,
        current_stock_pct=0.0,
        current_industry_pct=10.0,
        current_total_pct=40.0,
    )


def position_args(plan_path: Path) -> Namespace:
    return Namespace(
        template=str(ROOT / "templates/position.example.yaml"),
        plan=str(plan_path),
        output_dir="positions",
        output=None,
        overwrite=False,
        id="POS-DAILY-0001",
        status="normal",
        entry_date="2026-07-07",
        entry_price=10.0,
        current_price=10.0,
        position_pct=5.0,
        shares=1000,
        stop_loss_price=None,
        days_held=0,
        note=["按计划建仓。"],
    )


class UpdatePositionDailyTest(unittest.TestCase):
    def create_position_yaml(self, tmp_dir: str) -> Path:
        plan, _ = create_trade_plan(plan_args())
        plan_path = Path(tmp_dir) / "plan.yaml"
        position_path = Path(tmp_dir) / "position.yaml"
        write_yaml(plan_path, plan)
        position, _ = create_position(position_args(plan_path))
        write_yaml(position_path, position)
        return position_path

    def test_updates_tracking_fields(self) -> None:
        position = load_yaml(ROOT / "templates/position.example.yaml")

        updated = update_position_tracking(position, 21.0, 3, ["继续观察成交额。"])

        self.assertEqual(updated["tracking"]["current_price"], 21.0)
        self.assertEqual(updated["tracking"]["current_return_pct"], 5.0)
        self.assertEqual(updated["tracking"]["current_portfolio_return_pct"], 0.4)
        self.assertEqual(updated["tracking"]["days_held"], 3)
        self.assertTrue(any("继续观察成交额" in note for note in updated["tracking"]["notes"]))

    def test_run_update_writes_position_and_check_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            position_path = self.create_position_yaml(tmp_dir)
            output_path = Path(tmp_dir) / "updated_position.yaml"
            check_output = Path(tmp_dir) / "daily_check.json"

            result = run_update(
                ROOT / "config/investment-profile.example.yaml",
                position_path,
                10.5,
                output_path,
                check_output,
                days_held=2,
                notes=["价格更新。"],
            )

            updated = load_yaml(output_path)
            check_output_exists = check_output.exists()

        self.assertEqual(result["check"]["conclusion"], "normal")
        self.assertEqual(updated["tracking"]["current_price"], 10.5)
        self.assertTrue(check_output_exists)

    def test_run_update_detects_stop_loss_triggered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            position_path = self.create_position_yaml(tmp_dir)

            result = run_update(
                ROOT / "config/investment-profile.example.yaml",
                position_path,
                9.1,
                None,
                None,
                days_held=1,
                notes=[],
            )

        self.assertEqual(result["check"]["conclusion"], "needs_action")
        self.assertTrue(any(item["code"] == "stop_loss_triggered" for item in result["check"]["actions"]))

    def test_rejects_invalid_current_price(self) -> None:
        position = load_yaml(ROOT / "templates/position.example.yaml")

        with self.assertRaises(ValueError):
            update_position_tracking(position, 0, None, [])


if __name__ == "__main__":
    unittest.main()
