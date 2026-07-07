import copy
import tempfile
import unittest
from pathlib import Path

from tools.check_exit_plan import check_exit_plan, run_check
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def valid_exit_plan() -> dict:
    plan = load_yaml(ROOT / "templates/exit-plan.example.yaml")
    plan["exit_plan"]["id"] = "EXIT-CHECK-0001"
    plan["exit_plan"]["source_position_id"] = "POS-CHECK-0001"
    plan["exit_plan"]["source_trade_plan_id"] = "TP-CHECK-0001"
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


class CheckExitPlanTest(unittest.TestCase):
    def test_passes_valid_stop_loss_exit_plan(self) -> None:
        result = check_exit_plan(valid_exit_plan())

        self.assertEqual(result["conclusion"], "pass")
        self.assertTrue(any(item["code"] == "planned_exit_return" for item in result["info"]))

    def test_blocks_stop_loss_without_must_exit(self) -> None:
        plan = valid_exit_plan()
        plan["decision"]["must_exit"] = False

        result = check_exit_plan(plan)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "stop_loss_not_mandatory" for item in result["blockers"]))

    def test_blocks_exit_position_above_current_position(self) -> None:
        plan = valid_exit_plan()
        plan["decision"]["exit_position_pct"] = 6.0

        result = check_exit_plan(plan)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "exit_position_above_current" for item in result["blockers"]))

    def test_blocks_daily_stop_loss_action_mismatch(self) -> None:
        plan = valid_exit_plan()
        plan["exit_plan"]["exit_type"] = "take_profit"

        result = check_exit_plan(plan)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "stop_loss_action_mismatch" for item in result["blockers"]))

    def test_warns_when_not_matched_original_plan(self) -> None:
        plan = valid_exit_plan()
        plan["checks"]["matched_original_plan"] = False

        result = check_exit_plan(plan)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "exit_not_matched_original_plan" for item in result["warnings"]))

    def test_run_check_reads_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "exit.yaml"
            write_yaml(path, valid_exit_plan())

            result = run_check(path)

        self.assertEqual(result["exit_plan_id"], "EXIT-CHECK-0001")

    def test_does_not_mutate_input(self) -> None:
        plan = valid_exit_plan()
        before = copy.deepcopy(plan)

        check_exit_plan(plan)

        self.assertEqual(plan, before)


if __name__ == "__main__":
    unittest.main()
