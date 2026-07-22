import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.apply_candidate_portfolio_fit import apply_portfolio_fit
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class ApplyCandidatePortfolioFitTest(unittest.TestCase):
    def make_position(self, code: str, industry: str, position_pct: float) -> dict:
        position = load_yaml(ROOT / "templates/position.example.yaml")
        position["position"]["id"] = f"POS-{code}"
        position["stock"]["code"] = code
        position["stock"]["industry"] = industry
        position["entry"]["position_pct_of_total_assets"] = position_pct
        return position

    def write_candidates(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["code", "industry", "strategies", "primary_strategy", "combined_score"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "code": "300750",
                    "industry": "电力设备",
                    "strategies": "trend_strength",
                    "primary_strategy": "trend_strength",
                    "combined_score": "120",
                }
            )
            writer.writerow(
                {
                    "code": "000001",
                    "industry": "银行",
                    "strategies": "value_quality",
                    "primary_strategy": "value_quality",
                    "combined_score": "118",
                }
            )
            writer.writerow(
                {
                    "code": "600519",
                    "industry": "食品饮料",
                    "strategies": "value_quality",
                    "primary_strategy": "value_quality",
                    "combined_score": "116",
                }
            )
            writer.writerow(
                {
                    "code": "688001",
                    "industry": "医药",
                    "strategies": "event_catalyst",
                    "primary_strategy": "event_catalyst",
                    "combined_score": "114",
                }
            )

    def test_applies_portfolio_fit_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            candidates = base / "candidate_pool.csv"
            output = base / "candidate_pool.fit.csv"
            metadata_output = base / "candidate_portfolio_fit.json"
            position_a = base / "pos-a.yaml"
            position_b = base / "pos-b.yaml"
            strategy_health = base / "strategy-health.json"

            self.write_candidates(candidates)
            write_yaml(position_a, self.make_position("300750", "电力设备", 5.0))
            write_yaml(position_b, self.make_position("601398", "银行", 21.0))
            strategy_health.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {"strategy": "event_catalyst", "status": "needs_review"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            metadata = apply_portfolio_fit(
                ROOT / "config/investment-profile.example.yaml",
                candidates,
                output,
                metadata_output,
                [str(base / "pos-*.yaml")],
                planned_position_pct=5.0,
                strategy_health_path=strategy_health,
            )
            with output.open(encoding="utf-8", newline="") as file:
                rows = {row["code"]: row for row in csv.DictReader(file)}

        self.assertEqual(metadata["status_counts"]["deferred_by_portfolio"], 2)
        self.assertEqual(metadata["status_counts"]["ready_for_plan"], 1)
        self.assertEqual(metadata["status_counts"]["watch"], 1)
        self.assertEqual(rows["300750"]["portfolio_fit_status"], "deferred_by_portfolio")
        self.assertIn("当前已持有", rows["300750"]["portfolio_fit_evidence"])
        self.assertEqual(rows["000001"]["portfolio_fit_status"], "deferred_by_portfolio")
        self.assertIn("行业仓位", rows["000001"]["portfolio_fit_evidence"])
        self.assertEqual(rows["600519"]["portfolio_fit_status"], "ready_for_plan")
        self.assertEqual(rows["688001"]["portfolio_fit_status"], "watch")
        self.assertIn("需要复核", rows["688001"]["portfolio_fit_evidence"])


if __name__ == "__main__":
    unittest.main()
