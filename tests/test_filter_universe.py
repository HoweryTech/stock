import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.filter_universe import filter_universe, read_universe, run_filter
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class FilterUniverseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        self.rows = read_universe(ROOT / "samples/stock_universe.risk_sample.csv")
        self.as_of = datetime.strptime("2026-07-07", "%Y-%m-%d")

    def test_filters_risky_rows(self) -> None:
        eligible, exclusions = filter_universe(self.profile, self.rows, self.as_of)
        eligible_codes = {row["code"] for row in eligible}
        excluded_reasons = {item.reason for item in exclusions}

        self.assertEqual(eligible_codes, {"600000", "000001", "300750"})
        self.assertEqual(len(exclusions), 6)
        self.assertIn("is_st", excluded_reasons)
        self.assertIn("suspended", excluded_reasons)
        self.assertIn("delisting_risk", excluded_reasons)
        self.assertIn("abnormal_trading_status", excluded_reasons)
        self.assertIn("listing_days_too_short", excluded_reasons)
        self.assertIn("turnover_too_low", excluded_reasons)

    def test_run_filter_writes_output_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "tradable.csv"
            report_output = Path(tmp_dir) / "report.json"

            report = run_filter(
                ROOT / "config/investment-profile.example.yaml",
                ROOT / "samples/stock_universe.risk_sample.csv",
                output,
                report_output,
                self.as_of,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

            self.assertTrue(report_output.exists())
            self.assertEqual(report["input_count"], 9)
            self.assertEqual(report["eligible_count"], 3)
            self.assertEqual(report["excluded_count"], 6)
            self.assertEqual([row["code"] for row in rows], ["600000", "000001", "300750"])


if __name__ == "__main__":
    unittest.main()
