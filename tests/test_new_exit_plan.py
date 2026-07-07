import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_exit_plan import create_exit_plan, infer_exit_type
from tools.new_position import create_position
from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml
from tools.update_position_daily import run_update


ROOT = Path(__file__).resolve().parents[1]


def plan_args() -> Namespace:
    return Namespace(
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        template=str(ROOT / "templates/trade-plan.example.yaml"),
        output_dir="plans",
        output=None,
        overwrite=False,
        id="TP-EXIT-0001",
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
        id="POS-EXIT-0001",
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


def exit_args(position_path: Path, **overrides) -> Namespace:
    defaults = {
        "template": str(ROOT / "templates/exit-plan.example.yaml"),
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "position": str(position_path),
        "daily_check": None,
        "output_dir": "exit-plans",
        "output": None,
        "overwrite": False,
        "id": "EXIT-TEST-0001",
        "near_stop_pct": 3.0,
        "exit_type": None,
        "urgency": None,
        "exit_reason": None,
        "evidence": [],
        "risk_if_hold": [],
        "planned_exit_price": None,
        "exit_position_pct": None,
        "min_acceptable_exit_price": None,
        "must_exit": False,
        "matched_original_plan": True,
        "execution_note": None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewExitPlanTest(unittest.TestCase):
    def create_position_yaml(self, tmp_dir: str) -> Path:
        plan, _ = create_trade_plan(plan_args())
        plan_path = Path(tmp_dir) / "plan.yaml"
        position_path = Path(tmp_dir) / "position.yaml"
        write_yaml(plan_path, plan)
        position, _ = create_position(position_args(plan_path))
        write_yaml(position_path, position)
        return position_path

    def test_infers_stop_loss_from_daily_check(self) -> None:
        self.assertEqual(infer_exit_type({}, {"check": {"actions": [{"code": "stop_loss_triggered"}]}}, None), "stop_loss")

    def test_creates_stop_loss_exit_plan_from_daily_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            position_path = self.create_position_yaml(tmp_dir)
            check_path = Path(tmp_dir) / "daily_check.json"
            run_update(
                ROOT / "config/investment-profile.example.yaml",
                position_path,
                9.1,
                None,
                check_path,
                days_held=1,
            )

            exit_plan, output_path = create_exit_plan(exit_args(position_path, daily_check=str(check_path)))

        self.assertEqual(output_path, Path("exit-plans/EXIT-TEST-0001.yaml"))
        self.assertEqual(exit_plan["exit_plan"]["exit_type"], "stop_loss")
        self.assertEqual(exit_plan["exit_plan"]["urgency"], "immediate")
        self.assertTrue(exit_plan["decision"]["must_exit"])
        self.assertIn("stop_loss_triggered", exit_plan["checks"]["source_action_codes"])

    def test_creates_take_profit_exit_plan_without_daily_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            position_path = self.create_position_yaml(tmp_dir)
            position = load_yaml(position_path)
            position["tracking"]["current_price"] = 10.8
            position["tracking"]["current_return_pct"] = 8.0
            write_yaml(position_path, position, overwrite=True)

            exit_plan, _ = create_exit_plan(exit_args(position_path, exit_reason="达到目标区。"))

        self.assertEqual(exit_plan["exit_plan"]["exit_type"], "take_profit")
        self.assertEqual(exit_plan["decision"]["planned_exit_price"], 10.8)
        self.assertFalse(exit_plan["decision"]["must_exit"])

    def test_allows_manual_exit_type_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            position_path = self.create_position_yaml(tmp_dir)

            exit_plan, _ = create_exit_plan(
                exit_args(
                    position_path,
                    exit_type="risk_reduction",
                    evidence=["组合降仓。"],
                    risk_if_hold=["市场环境转弱。"],
                    exit_position_pct=2.5,
                )
            )

        self.assertEqual(exit_plan["exit_plan"]["exit_type"], "risk_reduction")
        self.assertEqual(exit_plan["decision"]["evidence"], ["组合降仓。"])
        self.assertEqual(exit_plan["decision"]["exit_position_pct"], 2.5)


if __name__ == "__main__":
    unittest.main()
