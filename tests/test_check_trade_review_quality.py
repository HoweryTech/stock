import copy
import tempfile
import unittest
from pathlib import Path

from tools.check_trade_review_quality import check_trade_review_quality, run_check
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def complete_review() -> dict:
    review = load_yaml(ROOT / "templates/trade-review.example.yaml")
    review["review"]["id"] = "TR-CHECK-0001"
    review["review"]["source_trade_plan_id"] = "TP-CHECK-0001"
    review["stock"]["code"] = "600000"
    review["stock"]["name"] = "测试股票"
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
    review["review_questions"]["risk_control_followed"] = True
    review["review_questions"]["position_sizing_followed"] = True
    review["review_questions"]["lesson"] = "止损执行及时。"
    review["review_questions"]["next_action"] = "复查策略弱市表现。"
    return review


class CheckTradeReviewQualityTest(unittest.TestCase):
    def test_passes_complete_review(self) -> None:
        result = check_trade_review_quality(complete_review())

        self.assertEqual(result["conclusion"], "pass")

    def test_blocks_missing_required_field(self) -> None:
        review = complete_review()
        review["execution"]["exit_price"] = None

        result = check_trade_review_quality(review)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "missing_execution_exit_price" for item in result["blockers"]))

    def test_warns_unanswered_review_questions(self) -> None:
        review = complete_review()
        review["review_questions"]["lesson"] = ""
        review["review_questions"]["risk_control_followed"] = None

        result = check_trade_review_quality(review)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "missing_lesson" for item in result["warnings"]))
        self.assertTrue(any(item["code"] == "unanswered_review_questions_risk_control_followed" for item in result["warnings"]))

    def test_warns_return_mismatch(self) -> None:
        review = complete_review()
        review["result"]["trade_return_pct"] = -8.0

        result = check_trade_review_quality(review)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "trade_return_mismatch" for item in result["warnings"]))

    def test_warns_execution_error_without_tags(self) -> None:
        review = complete_review()
        review["result"]["result_category"] = "execution_error_loss"

        result = check_trade_review_quality(review)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "missing_error_tags" for item in result["warnings"]))

    def test_run_check_reads_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "review.yaml"
            write_yaml(path, complete_review())

            result = run_check(path)

        self.assertEqual(result["review_id"], "TR-CHECK-0001")

    def test_does_not_mutate_input(self) -> None:
        review = complete_review()
        before = copy.deepcopy(review)

        check_trade_review_quality(review)

        self.assertEqual(review, before)


if __name__ == "__main__":
    unittest.main()
