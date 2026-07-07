import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_exit_execution import create_exit_execution
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def valid_exit_plan() -> dict:
    plan = load_yaml(ROOT / "templates/exit-plan.example.yaml")
    plan["exit_plan"]["id"] = "EXIT-EXEC-0001"
    plan["exit_plan"]["source_position_id"] = "POS-EXEC-0001"
    plan["exit_plan"]["source_trade_plan_id"] = "TP-EXEC-0001"
    plan["exit_plan"]["exit_type"] = "stop_loss"
    plan["exit_plan"]["urgency"] = "immediate"
    plan["stock"]["code"] = "600000"
    plan["stock"]["name"] = "测试股票"
    plan["position_snapshot"]["entry_price"] = 10.0
    plan["position_snapshot"]["current_price"] = 9.1
    plan["position_snapshot"]["position_pct_of_total_assets"] = 5.0
    plan["position_snapshot"]["current_return_pct"] = -9.0
    plan["decision"]["exit_reason"] = "触发止损。"
    plan["decision"]["evidence"] = ["[daily_check:stop_loss_triggered] 已触发止损。"]
    plan["decision"]["risks_if_hold"] = ["继续持有会扩大亏损。"]
    plan["decision"]["planned_exit_price"] = 9.1
    plan["decision"]["exit_position_pct"] = 5.0
    plan["decision"]["must_exit"] = True
    plan["checks"]["triggered_by_daily_check"] = True
    plan["checks"]["daily_check_conclusion"] = "needs_action"
    plan["checks"]["source_action_codes"] = ["stop_loss_triggered"]
    return plan


def args(exit_plan_path: Path, **overrides) -> Namespace:
    defaults = {
        "template": str(ROOT / "templates/exit-execution.example.yaml"),
        "exit_plan": str(exit_plan_path),
        "check": None,
        "output_dir": "exit-executions",
        "output": None,
        "overwrite": False,
        "id": "EXITEXEC-TEST-0001",
        "status": "recorded",
        "mode": "paper",
        "execution_date": "2026-07-08",
        "execution_price": 9.1,
        "shares": 1000,
        "position_pct": None,
        "fees": 5.0,
        "user_confirmed": False,
        "allow_below_min_price": False,
        "confirmation_text": None,
        "note": ["按退出计划卖出。"],
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewExitExecutionTest(unittest.TestCase):
    def write_exit_plan(self, tmp_dir: str, plan: dict | None = None) -> Path:
        path = Path(tmp_dir) / "exit.yaml"
        write_yaml(path, plan or valid_exit_plan())
        return path

    def test_creates_sell_execution_from_passed_exit_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self.write_exit_plan(tmp_dir)

            execution, output_path = create_exit_execution(args(path))

        self.assertEqual(output_path, Path("exit-executions/EXITEXEC-TEST-0001.yaml"))
        self.assertEqual(execution["execution"]["side"], "sell")
        self.assertEqual(execution["execution"]["exit_check_conclusion"], "pass")
        self.assertEqual(execution["order"]["exited_position_pct_of_total_assets"], 5.0)
        self.assertEqual(execution["result_estimate"]["trade_return_pct"], -9.0)
        self.assertEqual(execution["result_estimate"]["portfolio_return_pct"], -0.45)

    def test_blocks_failed_exit_plan_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = valid_exit_plan()
            plan["decision"]["must_exit"] = False
            path = self.write_exit_plan(tmp_dir, plan)

            with self.assertRaisesRegex(ValueError, "blocked"):
                create_exit_execution(args(path))

    def test_needs_review_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = valid_exit_plan()
            plan["checks"]["matched_original_plan"] = False
            path = self.write_exit_plan(tmp_dir, plan)

            with self.assertRaisesRegex(ValueError, "needs review"):
                create_exit_execution(args(path))

            execution, _ = create_exit_execution(args(path, user_confirmed=True))

        self.assertEqual(execution["execution"]["exit_check_conclusion"], "needs_review")
        self.assertTrue(execution["execution"]["user_confirmed"])

    def test_blocks_below_min_price_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = valid_exit_plan()
            plan["decision"]["min_acceptable_exit_price"] = 9.0
            path = self.write_exit_plan(tmp_dir, plan)

            with self.assertRaisesRegex(ValueError, "below min acceptable"):
                create_exit_execution(args(path, execution_price=8.9))

            execution, _ = create_exit_execution(args(path, execution_price=8.9, allow_below_min_price=True))

        self.assertFalse(execution["order"]["price_above_min_acceptable"])

    def test_real_sell_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self.write_exit_plan(tmp_dir)

            with self.assertRaisesRegex(ValueError, "real sell execution requires"):
                create_exit_execution(args(path, mode="real"))


if __name__ == "__main__":
    unittest.main()
