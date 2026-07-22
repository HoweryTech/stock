import csv
import tempfile
import unittest
from pathlib import Path

from tools.calc_industry_strength import calculate_industry_strength, read_daily_bars, read_universe, run_calculation


ROOT = Path(__file__).resolve().parents[1]


class CalcIndustryStrengthTest(unittest.TestCase):
    def test_calculates_industry_strength_for_each_code(self) -> None:
        universe = read_universe(ROOT / "samples/stock_universe.sample.csv")
        grouped = read_daily_bars(ROOT / "samples/daily_bars.sample.csv", universe)

        rows = calculate_industry_strength(universe, grouped, [2])
        by_code = {row["code"]: row for row in rows}

        self.assertEqual(len(rows), 3)
        self.assertEqual(by_code["300750"]["industry"], "电力设备")
        self.assertEqual(by_code["300750"]["industry_return_2d"], "1.522248")
        self.assertEqual(by_code["300750"]["industry_up_ratio_2"], "100")
        self.assertEqual(by_code["300750"]["relative_return_vs_industry_2d"], "0")
        self.assertIn("行业近 2 日收益率", by_code["300750"]["industry_strength_evidence"])
        self.assertGreater(float(by_code["300750"]["industry_strength_score"]), 0)

    def test_run_calculation_writes_output_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "industry_strength.csv"
            metadata_output = Path(tmp_dir) / "industry_strength.json"

            metadata = run_calculation(
                ROOT / "samples/daily_bars.sample.csv",
                ROOT / "samples/stock_universe.sample.csv",
                output,
                metadata_output,
                [2],
            )
            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["row_count"], 3)
        self.assertEqual(metadata["industry_count"], 2)
        self.assertEqual(metadata["windows"], [2])
        self.assertEqual(rows[0]["code"], "000001")
        self.assertIn("industry_strength_score", rows[0])


if __name__ == "__main__":
    unittest.main()
