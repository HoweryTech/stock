import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.check_execution_loop import build_loop_check, render_loop_check
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def args(base: Path) -> Namespace:
    return Namespace(
        trade_executions=[str(base / "executions/*.yaml")],
        exit_executions=[str(base / "exit-executions/*.yaml")],
        reviews=[str(base / "reviews/*.yaml")],
        output=str(base / "reports/execution-loop-check.md"),
        json_output=None,
        json=False,
    )


def valid_trade_execution() -> dict:
    execution = load_yaml(ROOT / "templates/trade-execution.example.yaml")
    execution["execution"]["id"] = "EXEC-LOOP-0001"
    execution["execution"]["mode"] = "paper"
    execution["execution"]["source_trade_plan_id"] = "TP-LOOP-0001"
    execution["execution"]["gate_conclusion"] = "pass"
    execution["execution"]["user_confirmed"] = False
    execution["stock"]["code"] = "600000"
    execution["order"]["side"] = "buy"
    execution["order"]["execution_date"] = "2026-07-08"
    execution["order"]["execution_price"] = 9.9
    execution["order"]["position_pct_of_total_assets"] = 5.0
    execution["order"]["slippage_pct_vs_plan"] = -1.0
    execution["order"]["price_within_max_acceptable"] = True
    execution["risk_snapshot"]["planned_buy_price"] = 10.0
    execution["risk_snapshot"]["max_acceptable_buy_price"] = 10.2
    execution["trade_plan_snapshot"] = {
        "strategy": {"source": "trend_strength"},
        "position_plan": {"planned_position_pct_of_total_assets": 5.0},
    }
    return execution


def blocked_exit_execution() -> dict:
    execution = load_yaml(ROOT / "templates/exit-execution.example.yaml")
    execution["execution"]["id"] = "EXITEXEC-LOOP-0001"
    execution["execution"]["mode"] = "real"
    execution["execution"]["source_exit_plan_id"] = "EXIT-LOOP-0001"
    execution["execution"]["source_position_id"] = "POS-LOOP-0001"
    execution["execution"]["source_trade_plan_id"] = "TP-LOOP-0001"
    execution["execution"]["exit_check_conclusion"] = "needs_review"
    execution["execution"]["user_confirmed"] = True
    execution["execution"]["confirmation_id"] = "CONFIRM-EXITEXEC-LOOP-0001"
    execution["stock"]["code"] = "600000"
    execution["order"]["execution_date"] = "2026-07-08"
    execution["order"]["execution_price"] = 9.1
    execution["order"]["exited_position_pct_of_total_assets"] = 5.0
    execution["order"]["price_above_min_acceptable"] = True
    execution["exit_plan_snapshot"] = {"position_snapshot": {"position_pct_of_total_assets": 5.0}}
    execution["confirmation_snapshot"] = {"available": False, "status": "missing"}
    return execution


def review_needs_review() -> dict:
    review = load_yaml(ROOT / "templates/trade-review.example.yaml")
    review["review"]["id"] = "TR-LOOP-0001"
    review["review"]["source_trade_plan_id"] = "TP-LOOP-0001"
    review["stock"]["code"] = "600000"
    review["execution"]["entry_date"] = "2026-07-01"
    review["execution"]["exit_date"] = "2026-07-08"
    review["execution"]["entry_price"] = 10.0
    review["execution"]["exit_price"] = 9.1
    review["execution"]["position_pct_of_total_assets"] = 5.0
    review["execution"]["exit_reason"] = "触发止损。"
    review["execution"]["followed_plan"] = True
    review["result"]["trade_return_pct"] = -9.0
    review["result"]["portfolio_return_pct"] = -0.45
    review["result"]["result_category"] = "strategy_loss"
    review["result"]["error_tags"] = []
    review["review_questions"]["buy_reason_still_valid"] = False
    review["review_questions"]["exit_reason_matches_plan"] = True
    review["review_questions"]["risk_control_followed"] = None
    review["review_questions"]["position_sizing_followed"] = True
    review["review_questions"]["lesson"] = ""
    review["review_questions"]["next_action"] = "复查策略弱市表现。"
    return review


class CheckExecutionLoopTest(unittest.TestCase):
    def test_builds_loop_check_from_all_record_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "executions" / "execution.yaml", valid_trade_execution())
            write_yaml(base / "exit-executions" / "exit_execution.yaml", blocked_exit_execution())
            write_yaml(base / "reviews" / "review.yaml", review_needs_review())

            result = build_loop_check(args(base))
            content = render_loop_check(result)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertEqual(result["trade_executions"]["pass_count"], 1)
        self.assertEqual(result["exit_executions"]["blocked_count"], 1)
        self.assertEqual(result["reviews"]["needs_review_count"], 1)
        self.assertIn("missing_confirmed_manual_confirmation_record", content)
        self.assertIn("missing_lesson", content)

    def test_empty_inputs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = build_loop_check(args(Path(tmp_dir)))

        self.assertEqual(result["conclusion"], "pass")
        self.assertEqual(result["blocked_count"], 0)
        self.assertEqual(result["needs_review_count"], 0)


if __name__ == "__main__":
    unittest.main()
