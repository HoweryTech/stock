import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan import write_yaml
from tools.new_trade_review_from_exit_execution import create_trade_review_from_exit_execution
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def exit_execution_record() -> dict:
    execution = load_yaml(ROOT / "templates/exit-execution.example.yaml")
    execution["execution"]["id"] = "EXITEXEC-REVIEW-0001"
    execution["execution"]["source_exit_plan_id"] = "EXIT-REVIEW-0001"
    execution["execution"]["source_position_id"] = "POS-REVIEW-0001"
    execution["execution"]["source_trade_plan_id"] = "TP-REVIEW-0001"
    execution["stock"]["code"] = "600000"
    execution["stock"]["name"] = "测试股票"
    execution["stock"]["exchange"] = "SSE"
    execution["stock"]["industry"] = "银行"
    execution["order"]["execution_date"] = "2026-07-08"
    execution["order"]["execution_price"] = 9.1
    execution["order"]["exited_position_pct_of_total_assets"] = 5.0
    execution["exit_snapshot"]["exit_reason"] = "触发止损。"
    execution["exit_snapshot"]["matched_original_plan"] = True
    execution["result_estimate"]["entry_price"] = 10.0
    execution["result_estimate"]["trade_return_pct"] = -9.0
    execution["result_estimate"]["portfolio_return_pct"] = -0.45
    execution["exit_plan_snapshot"] = {
        "position_full_snapshot": {
            "entry": {"entry_date": "2026-07-01"},
            "trade_plan_snapshot": {"trade_plan": {"id": "TP-REVIEW-0001"}},
        }
    }
    return execution


def args(path: Path, **overrides) -> Namespace:
    defaults = {
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-review.example.yaml"),
        "exit_execution": str(path),
        "output_dir": "reviews",
        "output": None,
        "overwrite": False,
        "id": "TR-FROM-EXITEXEC-0001",
        "result_category": None,
        "error_tag": [],
        "lesson": None,
        "next_action": None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewTradeReviewFromExitExecutionTest(unittest.TestCase):
    def write_execution(self, tmp_dir: str, execution: dict | None = None) -> Path:
        path = Path(tmp_dir) / "exit_execution.yaml"
        write_yaml(path, execution or exit_execution_record())
        return path

    def test_creates_review_from_exit_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self.write_execution(tmp_dir)

            review, output_path = create_trade_review_from_exit_execution(args(path))

        self.assertEqual(output_path, Path("reviews/TR-FROM-EXITEXEC-0001.yaml"))
        self.assertEqual(review["review"]["source_exit_execution_id"], "EXITEXEC-REVIEW-0001")
        self.assertEqual(review["execution"]["entry_date"], "2026-07-01")
        self.assertEqual(review["execution"]["exit_price"], 9.1)
        self.assertEqual(review["result"]["trade_return_pct"], -9.0)
        self.assertEqual(review["result"]["result_category"], "strategy_loss")

    def test_infers_execution_error_profit_when_not_followed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = exit_execution_record()
            execution["exit_snapshot"]["matched_original_plan"] = False
            execution["order"]["execution_price"] = 10.8
            execution["result_estimate"]["trade_return_pct"] = 8.0
            execution["result_estimate"]["portfolio_return_pct"] = 0.4
            path = self.write_execution(tmp_dir, execution)

            review, _ = create_trade_review_from_exit_execution(args(path))

        self.assertEqual(review["execution"]["followed_plan"], False)
        self.assertEqual(review["result"]["result_category"], "execution_error_profit")

    def test_allows_lesson_and_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self.write_execution(tmp_dir)

            review, _ = create_trade_review_from_exit_execution(args(path, lesson="止损执行及时。", next_action="复查策略。"))

        self.assertEqual(review["review_questions"]["lesson"], "止损执行及时。")
        self.assertEqual(review["review_questions"]["next_action"], "复查策略。")

    def test_requires_entry_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = exit_execution_record()
            execution["result_estimate"]["entry_price"] = None
            execution["exit_plan_snapshot"] = {}
            path = self.write_execution(tmp_dir, execution)

            with self.assertRaisesRegex(ValueError, "entry price"):
                create_trade_review_from_exit_execution(args(path))


if __name__ == "__main__":
    unittest.main()
