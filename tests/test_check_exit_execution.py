import tempfile
import unittest
from pathlib import Path

from tools.check_exit_execution import check_exit_execution, run_check
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def valid_exit_execution() -> dict:
    execution = load_yaml(ROOT / "templates/exit-execution.example.yaml")
    execution["execution"]["id"] = "EXITEXEC-CHECK-0001"
    execution["execution"]["mode"] = "paper"
    execution["execution"]["source_exit_plan_id"] = "EXIT-CHECK-0001"
    execution["execution"]["source_position_id"] = "POS-CHECK-0001"
    execution["execution"]["source_trade_plan_id"] = "TP-CHECK-0001"
    execution["execution"]["exit_check_conclusion"] = "pass"
    execution["execution"]["user_confirmed"] = False
    execution["stock"]["code"] = "600000"
    execution["order"]["execution_date"] = "2026-07-08"
    execution["order"]["execution_price"] = 9.2
    execution["order"]["exited_position_pct_of_total_assets"] = 5.0
    execution["order"]["slippage_pct_vs_plan"] = 1.0989
    execution["order"]["price_above_min_acceptable"] = True
    execution["exit_snapshot"]["planned_exit_price"] = 9.1
    execution["exit_snapshot"]["min_acceptable_exit_price"] = 9.0
    execution["exit_plan_snapshot"] = {"position_snapshot": {"position_pct_of_total_assets": 5.0}}
    execution["confirmation_snapshot"] = {"available": False, "status": "missing"}
    return execution


class CheckExitExecutionTest(unittest.TestCase):
    def test_passes_valid_paper_exit_execution(self) -> None:
        result = check_exit_execution(valid_exit_execution())

        self.assertEqual(result["conclusion"], "pass")
        self.assertEqual(result["exit_execution_id"], "EXITEXEC-CHECK-0001")

    def test_blocks_missing_confirmed_manual_confirmation_record(self) -> None:
        execution = valid_exit_execution()
        execution["execution"]["mode"] = "real"
        execution["execution"]["user_confirmed"] = True
        execution["execution"]["confirmation_id"] = "CONFIRM-EXITEXEC-CHECK-0001"
        execution["confirmation_snapshot"] = {"available": False, "status": "missing"}

        result = check_exit_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "missing_confirmed_manual_confirmation_record" for item in result["blockers"]))

    def test_passes_confirmed_real_exit_execution(self) -> None:
        execution = valid_exit_execution()
        execution["execution"]["mode"] = "real"
        execution["execution"]["user_confirmed"] = True
        execution["execution"]["confirmation_id"] = "CONFIRM-EXITEXEC-CHECK-0001"
        execution["confirmation_snapshot"] = {"available": True, "status": "confirmed", "id": "CONFIRM-EXITEXEC-CHECK-0001"}

        result = check_exit_execution(execution)

        self.assertEqual(result["conclusion"], "pass")

    def test_blocks_price_below_min_acceptable(self) -> None:
        execution = valid_exit_execution()
        execution["order"]["execution_price"] = 8.9
        execution["order"]["price_above_min_acceptable"] = False

        result = check_exit_execution(execution)
        blocker_codes = {item["code"] for item in result["blockers"]}

        self.assertEqual(result["conclusion"], "blocked")
        self.assertIn("execution_price_below_min_acceptable", blocker_codes)
        self.assertIn("execution_marked_below_min_price", blocker_codes)

    def test_blocks_exit_position_above_current_position(self) -> None:
        execution = valid_exit_execution()
        execution["order"]["exited_position_pct_of_total_assets"] = 6.0

        result = check_exit_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "exit_position_above_current_position" for item in result["blockers"]))

    def test_warns_negative_exit_slippage(self) -> None:
        execution = valid_exit_execution()
        execution["order"]["execution_price"] = 9.05
        execution["order"]["slippage_pct_vs_plan"] = -0.5495

        result = check_exit_execution(execution)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "negative_exit_slippage" for item in result["warnings"]))

    def test_run_check_reads_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "exit_execution.yaml"
            write_yaml(path, valid_exit_execution())

            result = run_check(path)

        self.assertEqual(result["exit_execution_id"], "EXITEXEC-CHECK-0001")
        self.assertEqual(result["conclusion"], "pass")


if __name__ == "__main__":
    unittest.main()
