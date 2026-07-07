import csv
import tempfile
import unittest
from pathlib import Path

from tools.risk_check import load_yaml
from tools.screen_value_quality import (
    candidate_from_row,
    run_screen,
    screen_candidates,
    value_quality_screening_config,
)


ROOT = Path(__file__).resolve().parents[1]


class ScreenValueQualityTest(unittest.TestCase):
    def test_loads_value_quality_config(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        config = value_quality_screening_config(profile)

        self.assertEqual(config["min_roe"], 4.0)
        self.assertEqual(config["max_debt_ratio"], 75.0)

    def test_builds_candidate_with_reasons(self) -> None:
        row = {
            "report_period": "2026-03-31",
            "code": "300750",
            "roe": "6.9",
            "roa": "3.9",
            "gross_margin": "27.5",
            "net_margin": "12.8",
            "debt_ratio": "68.4",
            "operating_cash_flow": "14500000000",
            "revenue_growth_yoy": "18.2",
            "net_profit_growth_yoy": "21.6",
            "deducted_net_profit_growth_yoy": "19.4",
            "eps": "2.35",
        }
        config = value_quality_screening_config(load_yaml(ROOT / "config/investment-profile.example.yaml"))

        candidate, exclusions = candidate_from_row(row, config)

        self.assertEqual(exclusions, [])
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["code"], "300750")
        self.assertIn("ROE 6.90%", candidate["reasons"])
        self.assertGreater(candidate["score"], 0)

    def test_excludes_high_debt_or_weak_profitability(self) -> None:
        row = {
            "report_period": "2026-03-31",
            "code": "600000",
            "roe": "3.8",
            "roa": "0.32",
            "gross_margin": "0",
            "debt_ratio": "91.2",
            "operating_cash_flow": "48200000000",
            "revenue_growth_yoy": "2.1",
            "deducted_net_profit_growth_yoy": "2.9",
        }
        config = value_quality_screening_config(load_yaml(ROOT / "config/investment-profile.example.yaml"))

        candidate, exclusions = candidate_from_row(row, config)

        self.assertIsNone(candidate)
        self.assertTrue(any("ROE" in reason for reason in exclusions))
        self.assertTrue(any("资产负债率" in reason for reason in exclusions))

    def test_uses_latest_report_per_code_and_limits_candidates(self) -> None:
        rows = [
            {
                "report_period": "2025-12-31",
                "code": "300750",
                "roe": "3.0",
                "roa": "1.2",
                "gross_margin": "20",
                "debt_ratio": "60",
                "operating_cash_flow": "1",
                "revenue_growth_yoy": "10",
                "deducted_net_profit_growth_yoy": "10",
            },
            {
                "report_period": "2026-03-31",
                "code": "300750",
                "roe": "6.9",
                "roa": "3.9",
                "gross_margin": "27.5",
                "debt_ratio": "68.4",
                "operating_cash_flow": "14500000000",
                "revenue_growth_yoy": "18.2",
                "deducted_net_profit_growth_yoy": "19.4",
            },
        ]
        config = value_quality_screening_config(load_yaml(ROOT / "config/investment-profile.example.yaml"))
        config["max_candidates"] = 1

        candidates, exclusions = screen_candidates(rows, config)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["report_period"], "2026-03-31")
        self.assertEqual(exclusions, [])

    def test_runs_screen_on_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "value_quality_candidates.csv"
            metadata_output = Path(tmp_dir) / "value_quality_candidates.json"

            metadata = run_screen(
                ROOT / "config/investment-profile.example.yaml",
                ROOT / "samples/financial_metrics.sample.csv",
                output,
                metadata_output,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["strategy"], "value_quality")
        self.assertEqual(metadata["input_count"], 3)
        self.assertEqual(metadata["candidate_count"], 1)
        self.assertEqual(metadata["excluded_count"], 2)
        self.assertEqual(rows[0]["code"], "300750")


if __name__ == "__main__":
    unittest.main()
