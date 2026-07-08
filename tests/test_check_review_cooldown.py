import tempfile
import unittest
from pathlib import Path

from tools.check_review_cooldown import check_cooldown
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    return load_yaml(ROOT / "config/investment-profile.example.yaml")


def review(review_id: str, exit_date: str, strategy: str, trade_return: float) -> dict:
    data = load_yaml(ROOT / "templates/trade-review.example.yaml")
    data["review"]["id"] = review_id
    data["review"]["source_trade_plan_id"] = f"TP-{review_id}"
    data["stock"]["code"] = "600000"
    data["stock"]["name"] = "测试股票"
    data["execution"]["exit_date"] = exit_date
    data["execution"]["entry_price"] = 10.0
    data["execution"]["exit_price"] = 10.0 * (1 + trade_return / 100)
    data["execution"]["position_pct_of_total_assets"] = 5.0
    data["result"]["trade_return_pct"] = trade_return
    data["result"]["portfolio_return_pct"] = trade_return * 5.0 / 100
    data["trade_plan_snapshot"] = {"strategy": {"source": strategy}}
    return data


class CheckReviewCooldownTest(unittest.TestCase):
    def write_reviews(self, tmp_dir: str, reviews: list[dict]) -> list[Path]:
        paths: list[Path] = []
        for index, item in enumerate(reviews, start=1):
            path = Path(tmp_dir) / f"review{index}.yaml"
            write_yaml(path, item)
            paths.append(path)
        return paths

    def test_requires_cooldown_after_configured_losing_streak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = self.write_reviews(
                tmp_dir,
                [
                    review("TR-1", "2026-07-01", "trend_strength", -1.0),
                    review("TR-2", "2026-07-02", "trend_strength", -2.0),
                    review("TR-3", "2026-07-03", "trend_strength", -3.0),
                ],
            )

            result = check_cooldown(profile(), paths)

        self.assertEqual(result["conclusion"], "cooldown_required")
        self.assertEqual(result["overall_losing_streak"], 3)
        self.assertEqual(result["strategy_losing_streaks"]["trend_strength"], 3)
        self.assertTrue(any(item["code"] == "overall_losing_streak_cooldown" for item in result["actions"]))
        self.assertTrue(any(item["code"] == "strategy_losing_streak_cooldown" for item in result["actions"]))

    def test_profit_resets_losing_streak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = self.write_reviews(
                tmp_dir,
                [
                    review("TR-1", "2026-07-01", "trend_strength", -1.0),
                    review("TR-2", "2026-07-02", "trend_strength", 2.0),
                    review("TR-3", "2026-07-03", "trend_strength", -3.0),
                ],
            )

            result = check_cooldown(profile(), paths)

        self.assertEqual(result["conclusion"], "normal")
        self.assertEqual(result["overall_losing_streak"], 1)

    def test_empty_reviews_are_normal(self) -> None:
        result = check_cooldown(profile(), [])

        self.assertEqual(result["conclusion"], "normal")
        self.assertEqual(result["review_count"], 0)


if __name__ == "__main__":
    unittest.main()
