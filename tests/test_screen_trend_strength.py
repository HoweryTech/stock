import csv
import tempfile
import unittest
from pathlib import Path

from tools.calc_trend_factors import run_calculation
from tools.risk_check import load_yaml
from tools.screen_trend_strength import candidate_from_row, run_screen, screen_candidates, trend_screening_config


ROOT = Path(__file__).resolve().parents[1]


class ScreenTrendStrengthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        self.config = trend_screening_config(self.profile)

    def test_candidate_from_row_includes_reasons(self) -> None:
        row = {
            "code": "600000",
            "trade_date": "2026-07-02",
            "close": "10.36",
            "return_2d": "1.568627",
            "ma_2": "10.28",
            "above_ma_2": "true",
            "turnover_avg_2": "1116700000",
            "is_suspended": "false",
            "is_limit_up": "false",
            "is_limit_down": "false",
        }

        candidate, reasons = candidate_from_row(row, self.config)

        self.assertEqual(reasons, [])
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["code"], "600000")
        self.assertIn("近 2 日收益率", candidate["reasons"])
        self.assertEqual(candidate["risks"], "")

    def test_screen_candidates_excludes_weak_rows(self) -> None:
        rows = [
            {
                "code": "600000",
                "trade_date": "2026-07-02",
                "close": "10.36",
                "return_2d": "1.568627",
                "ma_2": "10.28",
                "above_ma_2": "true",
                "turnover_avg_2": "1116700000",
                "is_suspended": "false",
                "is_limit_up": "false",
                "is_limit_down": "false",
            },
            {
                "code": "000001",
                "trade_date": "2026-07-02",
                "close": "12.54",
                "return_2d": "0.5",
                "ma_2": "12.3",
                "above_ma_2": "true",
                "turnover_avg_2": "2000000000",
                "is_suspended": "false",
                "is_limit_up": "false",
                "is_limit_down": "false",
            },
        ]

        candidates, exclusions = screen_candidates(rows, self.config)

        self.assertEqual([item["code"] for item in candidates], ["600000"])
        self.assertEqual(exclusions[0]["code"], "000001")
        self.assertIn("低于阈值", exclusions[0]["reasons"][0])

    def test_run_screen_writes_candidates_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            factors = Path(tmp_dir) / "trend_factors.csv"
            factor_metadata = Path(tmp_dir) / "trend_factors.json"
            output = Path(tmp_dir) / "trend_candidates.csv"
            metadata_output = Path(tmp_dir) / "trend_candidates.json"

            run_calculation(
                ROOT / "samples/daily_bars.sample.csv",
                None,
                factors,
                factor_metadata,
                [2],
            )
            metadata = run_screen(
                ROOT / "config/investment-profile.example.yaml",
                factors,
                output,
                metadata_output,
            )
            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

            self.assertTrue(metadata_output.exists())
            self.assertEqual(metadata["candidate_count"], 3)
            self.assertEqual(rows[0]["code"], "300750")
            self.assertIn("近 2 日收益率", rows[0]["reasons"])

    def test_limit_down_is_excluded_by_default(self) -> None:
        row = {
            "code": "600000",
            "trade_date": "2026-07-02",
            "close": "10.36",
            "return_2d": "1.568627",
            "ma_2": "10.28",
            "above_ma_2": "true",
            "turnover_avg_2": "1116700000",
            "is_suspended": "false",
            "is_limit_up": "false",
            "is_limit_down": "true",
        }

        candidate, reasons = candidate_from_row(row, self.config)

        self.assertIsNone(candidate)
        self.assertEqual(reasons, ["跌停，排除。"])


if __name__ == "__main__":
    unittest.main()

