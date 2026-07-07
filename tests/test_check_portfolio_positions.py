import tempfile
import unittest
from pathlib import Path

from tools.check_portfolio_positions import expand_position_paths, summarize_positions
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class CheckPortfolioPositionsTest(unittest.TestCase):
    def make_position(self, code: str, industry: str, position_pct: float, current_price: float = 20.0, stop_loss: float = 18.5) -> dict:
        position = load_yaml(ROOT / "templates/position.example.yaml")
        position["position"]["id"] = f"POS-{code}"
        position["stock"]["code"] = code
        position["stock"]["industry"] = industry
        position["entry"]["position_pct_of_total_assets"] = position_pct
        position["tracking"]["current_price"] = current_price
        position["risk"]["stop_loss_price"] = stop_loss
        return position

    def test_expand_position_paths_deduplicates_globs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            first = Path(tmp_dir) / "a.yaml"
            second = Path(tmp_dir) / "b.yaml"
            first.write_text("a: 1\n", encoding="utf-8")
            second.write_text("b: 2\n", encoding="utf-8")

            paths = expand_position_paths([str(Path(tmp_dir) / "*.yaml"), str(first)])

        self.assertEqual(len(paths), 2)

    def test_summarizes_normal_positions(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        with tempfile.TemporaryDirectory() as tmp_dir:
            first = Path(tmp_dir) / "first.yaml"
            second = Path(tmp_dir) / "second.yaml"
            write_yaml(first, self.make_position("600000", "银行", 5.0))
            write_yaml(second, self.make_position("300750", "电力设备", 4.0))

            result = summarize_positions(profile, [first, second])

        self.assertEqual(result["conclusion"], "normal")
        self.assertEqual(result["position_count"], 2)
        self.assertEqual(result["total_position_pct"], 9.0)
        self.assertEqual(result["industry_position_pct"]["银行"], 5.0)

    def test_summarizes_stop_loss_action(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "position.yaml"
            write_yaml(path, self.make_position("600000", "银行", 5.0, current_price=18.0))

            result = summarize_positions(profile, [path])

        self.assertEqual(result["conclusion"], "needs_action")
        self.assertEqual(result["needs_action_count"], 1)

    def test_summarizes_portfolio_position_limits(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = []
            for index in range(4):
                path = Path(tmp_dir) / f"p{index}.yaml"
                write_yaml(path, self.make_position(f"60000{index}", "银行", 7.0))
                paths.append(path)

            result = summarize_positions(profile, paths)

        self.assertEqual(result["conclusion"], "needs_action")
        self.assertTrue(any(item["code"] == "portfolio_industry_position_exceeded" for item in result["portfolio_actions"]))


if __name__ == "__main__":
    unittest.main()
