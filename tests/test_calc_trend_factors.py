import csv
import tempfile
import unittest
from pathlib import Path

from tools.calc_trend_factors import calculate_trend_factors, parse_windows, read_daily_bars, run_calculation


ROOT = Path(__file__).resolve().parents[1]


class CalcTrendFactorsTest(unittest.TestCase):
    def test_parse_windows_sorts_and_deduplicates(self) -> None:
        self.assertEqual(parse_windows("20,5,5, 2"), [2, 5, 20])

    def test_calculates_latest_trend_factors(self) -> None:
        grouped = read_daily_bars(ROOT / "samples/daily_bars.sample.csv")
        rows = calculate_trend_factors(grouped, [2])
        by_code = {row["code"]: row for row in rows}

        self.assertEqual(len(rows), 3)
        self.assertEqual(by_code["600000"]["trade_date"], "2026-07-02")
        self.assertEqual(by_code["600000"]["return_2d"], "1.568627")
        self.assertEqual(by_code["600000"]["ma_2"], "10.28")
        self.assertEqual(by_code["600000"]["above_ma_2"], True)
        self.assertEqual(by_code["600000"]["turnover_avg_2"], "1116700000")

    def test_insufficient_window_outputs_blank_values(self) -> None:
        grouped = read_daily_bars(ROOT / "samples/daily_bars.sample.csv")
        rows = calculate_trend_factors(grouped, [5])

        self.assertEqual(rows[0]["return_5d"], "")
        self.assertEqual(rows[0]["ma_5"], "")
        self.assertEqual(rows[0]["above_ma_5"], "")
        self.assertEqual(rows[0]["turnover_avg_5"], "")

    def test_run_calculation_writes_output_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "trend_factors.csv"
            metadata_output = Path(tmp_dir) / "trend_factors.json"

            metadata = run_calculation(
                ROOT / "samples/daily_bars.sample.csv",
                None,
                output,
                metadata_output,
                [2],
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

            self.assertTrue(metadata_output.exists())
            self.assertEqual(metadata["row_count"], 3)
            self.assertEqual(metadata["windows"], [2])
            self.assertEqual(rows[0]["code"], "000001")
            self.assertEqual(rows[0]["above_ma_2"], "True")

    def test_universe_limits_calculation_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            universe = Path(tmp_dir) / "universe.csv"
            output = Path(tmp_dir) / "trend_factors.csv"
            metadata_output = Path(tmp_dir) / "trend_factors.json"
            universe.write_text("code\n600000\n", encoding="utf-8")

            run_calculation(
                ROOT / "samples/daily_bars.sample.csv",
                universe,
                output,
                metadata_output,
                [2],
            )
            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual([row["code"] for row in rows], ["600000"])


if __name__ == "__main__":
    unittest.main()
