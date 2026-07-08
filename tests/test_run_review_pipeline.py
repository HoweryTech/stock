import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml
from tools.run_review_pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]


def args(tmp_dir: str) -> Namespace:
    base = Path(tmp_dir)
    return Namespace(
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        reviews=[str(base / "reviews/*.yaml")],
        analysis_output=str(base / "reports/review-analysis.md"),
        analysis_json_output=str(base / "metadata/review-analysis.json"),
        cooldown_output=str(base / "metadata/review-cooldown.json"),
        strategy_health_output=str(base / "reports/strategy-health.md"),
        strategy_health_json_output=str(base / "metadata/strategy-health.json"),
        metadata_output=str(base / "metadata/review-pipeline.json"),
        min_trades=3,
        min_win_rate_pct=40.0,
        min_avg_return_pct=0.0,
        json=False,
    )


def review(review_id: str, exit_date: str, trade_return: float) -> dict:
    data = load_yaml(ROOT / "templates/trade-review.example.yaml")
    data["review"]["id"] = review_id
    data["review"]["source_trade_plan_id"] = f"TP-{review_id}"
    data["stock"]["code"] = "600000"
    data["stock"]["name"] = "测试股票"
    data["execution"]["entry_date"] = "2026-07-01"
    data["execution"]["exit_date"] = exit_date
    data["execution"]["entry_price"] = 10.0
    data["execution"]["exit_price"] = 10.0 * (1 + trade_return / 100)
    data["execution"]["position_pct_of_total_assets"] = 5.0
    data["execution"]["exit_reason"] = "按计划退出。"
    data["execution"]["followed_plan"] = True
    data["result"]["trade_return_pct"] = trade_return
    data["result"]["portfolio_return_pct"] = trade_return * 5.0 / 100
    data["result"]["result_category"] = "strategy_loss" if trade_return < 0 else "strategy_profit"
    data["review_questions"]["buy_reason_still_valid"] = trade_return >= 0
    data["review_questions"]["exit_reason_matches_plan"] = True
    data["review_questions"]["risk_control_followed"] = True
    data["review_questions"]["position_sizing_followed"] = True
    data["review_questions"]["lesson"] = "记录复盘。"
    data["review_questions"]["next_action"] = "继续观察。"
    data["trade_plan_snapshot"] = {"strategy": {"source": "trend_strength"}}
    return data


class RunReviewPipelineTest(unittest.TestCase):
    def test_runs_review_analysis_and_cooldown_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "reviews").mkdir()
            write_yaml(base / "reviews" / "review1.yaml", review("TR-1", "2026-07-01", -1.0))
            write_yaml(base / "reviews" / "review2.yaml", review("TR-2", "2026-07-02", -2.0))
            write_yaml(base / "reviews" / "review3.yaml", review("TR-3", "2026-07-03", -3.0))

            metadata = run_pipeline(args(tmp_dir))

            analysis = json.loads((base / "metadata/review-analysis.json").read_text(encoding="utf-8"))
            cooldown = json.loads((base / "metadata/review-cooldown.json").read_text(encoding="utf-8"))
            strategy_health = json.loads((base / "metadata/strategy-health.json").read_text(encoding="utf-8"))
            pipeline = json.loads((base / "metadata/review-pipeline.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["review_count"], 3)
        self.assertEqual(analysis["review_count"], 3)
        self.assertEqual(cooldown["conclusion"], "cooldown_required")
        self.assertEqual(strategy_health["conclusion"], "pause_required")
        self.assertEqual(pipeline["steps"]["cooldown_check"]["conclusion"], "cooldown_required")
        self.assertEqual(pipeline["steps"]["strategy_health"]["conclusion"], "pause_required")


if __name__ == "__main__":
    unittest.main()
