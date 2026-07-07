import csv
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan_from_candidate import create_plan_from_candidate, infer_strategy


ROOT = Path(__file__).resolve().parents[1]


def args(**overrides):
    defaults = {
        "candidates": "",
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-plan.example.yaml"),
        "output_dir": "plans",
        "output": None,
        "overwrite": False,
        "id": "TP-CANDIDATE-0001",
        "code": "300750",
        "name": "宁德时代",
        "exchange": "SZSE",
        "industry": "电力设备",
        "strategy": None,
        "timeframe": None,
        "buy_reason": None,
        "planned_buy_price": None,
        "current_price": None,
        "stop_loss_price": None,
        "position_pct": None,
        "current_stock_pct": 0.0,
        "current_industry_pct": 0.0,
        "current_total_pct": 0.0,
        "stop_loss_condition": [],
        "take_profit_condition": [],
        "invalidation_condition": [],
        "observation_item": [],
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewTradePlanFromCandidateTest(unittest.TestCase):
    def write_candidate_pool(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "code",
                    "strategies",
                    "strategy_count",
                    "combined_score",
                    "primary_strategy",
                    "trend_score",
                    "value_quality_score",
                    "trade_date",
                    "report_period",
                    "reasons",
                    "risks",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "code": "300750",
                    "strategies": "trend_strength|value_quality",
                    "strategy_count": "2",
                    "combined_score": "232.377248",
                    "primary_strategy": "multi_strategy",
                    "trend_score": "11.522248",
                    "value_quality_score": "20.855",
                    "trade_date": "2026-07-02",
                    "report_period": "2026-03-31",
                    "reasons": "[trend_strength] 趋势强。 | [value_quality] 质量好。",
                    "risks": "",
                }
            )

    def test_infers_first_strategy_for_multi_strategy_candidate(self) -> None:
        self.assertEqual(infer_strategy({"primary_strategy": "multi_strategy", "strategies": "trend_strength|value_quality"}), "trend_strength")
        self.assertEqual(infer_strategy({"primary_strategy": "value_quality", "strategies": "value_quality"}), "value_quality")
        self.assertEqual(infer_strategy({"primary_strategy": "multi_strategy", "strategies": "trend_strength"}, "value_quality"), "value_quality")

    def test_creates_trade_plan_with_candidate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidate_pool.csv"
            output = Path(tmp_dir) / "plan.yaml"
            self.write_candidate_pool(candidates)

            plan, output_path = create_plan_from_candidate(
                args(
                    candidates=str(candidates),
                    output=str(output),
                    planned_buy_price=200.0,
                    stop_loss_price=185.0,
                    position_pct=5.0,
                )
            )

        self.assertEqual(output_path, output)
        self.assertEqual(plan["stock"]["code"], "300750")
        self.assertEqual(plan["strategy"]["source"], "trend_strength")
        self.assertIn("[trend_strength] 趋势强。", plan["strategy"]["key_evidence"][0]["description"])
        self.assertIn("观察池未输出显式风险", plan["strategy"]["counter_evidence_and_risks"][0]["description"])
        self.assertEqual(plan["risk_calculation"]["max_loss_pct_of_total_assets"], 0.375)

    def test_missing_candidate_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidate_pool.csv"
            self.write_candidate_pool(candidates)

            with self.assertRaises(ValueError):
                create_plan_from_candidate(args(candidates=str(candidates), code="600000"))


if __name__ == "__main__":
    unittest.main()
