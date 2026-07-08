import tempfile
import unittest
from pathlib import Path

from tools.analyze_trade_reviews import analyze_reviews, render_analysis
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def review(
    review_id: str,
    strategy: str,
    trade_return: float,
    portfolio_return: float,
    category: str,
    tags: list[str] | None = None,
    config_version_id: str = "CONFIG-VERSION-TEST",
) -> dict:
    data = load_yaml(ROOT / "templates/trade-review.example.yaml")
    data["review"]["id"] = review_id
    data["review"]["source_trade_plan_id"] = f"TP-{review_id}"
    data["stock"]["code"] = "600000"
    data["stock"]["name"] = "测试股票"
    data["execution"]["entry_date"] = "2026-07-01"
    data["execution"]["exit_date"] = "2026-07-08"
    data["execution"]["entry_price"] = 10.0
    data["execution"]["exit_price"] = 10.0 * (1 + trade_return / 100)
    data["execution"]["position_pct_of_total_assets"] = 5.0
    data["execution"]["exit_reason"] = "按计划退出。"
    data["execution"]["followed_plan"] = not category.startswith("execution_error")
    data["result"]["trade_return_pct"] = trade_return
    data["result"]["portfolio_return_pct"] = portfolio_return
    data["result"]["result_category"] = category
    data["result"]["error_tags"] = tags or []
    data["review_questions"]["buy_reason_still_valid"] = trade_return >= 0
    data["review_questions"]["exit_reason_matches_plan"] = True
    data["review_questions"]["risk_control_followed"] = True
    data["review_questions"]["position_sizing_followed"] = True
    data["review_questions"]["lesson"] = "记录复盘。"
    data["review_questions"]["next_action"] = "继续观察。"
    snapshot = {
        "available": True,
        "version_id": config_version_id,
        "profile_hash": f"{config_version_id.lower().replace('-', '')}abcdef123456",
    }
    data["strategy_config_snapshot"] = snapshot
    data["trade_plan_snapshot"] = {"strategy": {"source": strategy}, "strategy_config_snapshot": snapshot}
    return data


def exception_review() -> dict:
    data = review("TR-EXCEPTION", "trend_strength", -2.0, -0.1, "strategy_loss")
    data["discipline"] = {
        "cooldown_conclusion_at_entry": "cooldown_required",
        "strategy_health_conclusion_at_entry": "pause_required",
        "was_cooldown_exception": True,
        "was_strategy_health_exception": True,
        "exception_reason": "策略暂停期小仓位例外。",
    }
    return data


class AnalyzeTradeReviewsTest(unittest.TestCase):
    def test_analyzes_reviews_by_strategy_and_error_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            first = Path(tmp_dir) / "review1.yaml"
            second = Path(tmp_dir) / "review2.yaml"
            write_yaml(first, review("TR-1", "trend_strength", 8.0, 0.4, "strategy_profit"))
            write_yaml(second, review("TR-2", "trend_strength", -4.0, -0.2, "execution_error_loss", ["late_stop_loss"]))

            analysis = analyze_reviews([first, second])
            content = render_analysis(analysis)

        self.assertEqual(analysis["review_count"], 2)
        self.assertEqual(analysis["overall"]["win_count"], 1)
        self.assertEqual(analysis["overall"]["loss_count"], 1)
        self.assertEqual(analysis["overall"]["win_rate_pct"], 50.0)
        self.assertEqual(analysis["overall"]["total_portfolio_return_pct"], 0.2)
        self.assertEqual(analysis["by_strategy"]["trend_strength"]["count"], 2)
        self.assertEqual(analysis["by_config_version"]["CONFIG-VERSION-TEST"]["count"], 2)
        self.assertEqual(analysis["error_tags"]["late_stop_loss"], 1)
        self.assertIn("# 交易复盘分析", content)
        self.assertIn("trend_strength", content)
        self.assertIn("CONFIG-VERSION-TEST", content)

    def test_analyzes_reviews_by_config_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            first = Path(tmp_dir) / "review1.yaml"
            second = Path(tmp_dir) / "review2.yaml"
            write_yaml(first, review("TR-V1", "trend_strength", 8.0, 0.4, "strategy_profit", config_version_id="CONFIG-VERSION-V1"))
            write_yaml(second, review("TR-V2", "trend_strength", -4.0, -0.2, "strategy_loss", config_version_id="CONFIG-VERSION-V2"))

            analysis = analyze_reviews([first, second])
            content = render_analysis(analysis)

        self.assertEqual(analysis["by_strategy"]["trend_strength"]["count"], 2)
        self.assertEqual(analysis["by_config_version"]["CONFIG-VERSION-V1"]["win_count"], 1)
        self.assertEqual(analysis["by_config_version"]["CONFIG-VERSION-V2"]["loss_count"], 1)
        self.assertIn("按配置版本汇总", content)
        self.assertIn("CONFIG-VERSION-V1", content)
        self.assertIn("CONFIG-VERSION-V2", content)

    def test_empty_analysis_is_supported(self) -> None:
        analysis = analyze_reviews([])
        content = render_analysis(analysis)

        self.assertEqual(analysis["review_count"], 0)
        self.assertEqual(analysis["overall"]["count"], 0)
        self.assertIn("复盘数量：0", content)

    def test_analyzes_discipline_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "review.yaml"
            write_yaml(path, exception_review())

            analysis = analyze_reviews([path])
            content = render_analysis(analysis)

        self.assertEqual(analysis["discipline"]["cooldown_exception_count"], 1)
        self.assertEqual(analysis["discipline"]["strategy_health_exception_count"], 1)
        self.assertEqual(analysis["discipline"]["exception_avg_trade_return_pct"], -2.0)
        self.assertEqual(analysis["discipline"]["exception_total_portfolio_return_pct"], -0.1)
        self.assertIn("纪律例外", content)
        self.assertIn("TR-EXCEPTION", content)


if __name__ == "__main__":
    unittest.main()
