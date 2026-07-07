import csv
import tempfile
import unittest
from pathlib import Path

from tools.import_daily_bars import import_daily_bars, read_daily_bars


ROOT = Path(__file__).resolve().parents[1]


class ImportDailyBarsTest(unittest.TestCase):
    def test_imports_sample_daily_bars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "daily_bars.csv"
            metadata_output = Path(tmp_dir) / "metadata.json"

            metadata = import_daily_bars(
                ROOT / "samples/daily_bars.sample.csv",
                output,
                metadata_output,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["row_count"], 6)
        self.assertEqual(metadata["issue_count"], 0)
        self.assertEqual(metadata["code_count"], 3)
        self.assertEqual(metadata["start_date"], "2026-07-01")
        self.assertEqual(metadata["end_date"], "2026-07-02")
        self.assertEqual(rows[0]["code"], "000001")

    def test_reports_invalid_ohlc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "trade_date,code,open,high,low,close,volume,turnover,is_limit_up,is_limit_down,is_suspended\n"
                "2026-07-01,600000,10,9,9.5,9.8,100,1000,false,false,false\n",
                encoding="utf-8",
            )

            rows, issues = read_daily_bars(source)

        self.assertEqual(rows, [])
        self.assertEqual(len(issues), 1)
        self.assertIn("high must be >= low", issues[0].message)

    def test_reports_duplicate_trade_date_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "trade_date,code,open,high,low,close,volume,turnover,is_limit_up,is_limit_down,is_suspended\n"
                "2026-07-01,600000,10,10.2,9.9,10.1,100,1000,false,false,false\n"
                "2026-07-01,600000,10,10.2,9.9,10.1,100,1000,false,false,false\n",
                encoding="utf-8",
            )

            rows, issues = read_daily_bars(source)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(issues), 1)
        self.assertIn("duplicate bar", issues[0].message)

    def test_strict_import_fails_on_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "trade_date,code,open,high,low,close,volume,turnover,is_limit_up,is_limit_down,is_suspended\n"
                "bad-date,600000,10,10.2,9.9,10.1,100,1000,false,false,false\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                import_daily_bars(source, output, metadata)

            self.assertTrue(metadata.exists())
            self.assertFalse(output.exists())

    def test_allow_invalid_writes_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "trade_date,code,open,high,low,close,volume,turnover,is_limit_up,is_limit_down,is_suspended\n"
                "2026-07-01,600000,10,10.2,9.9,10.1,100,1000,false,false,false\n"
                "bad-date,000001,10,10.2,9.9,10.1,100,1000,false,false,false\n",
                encoding="utf-8",
            )

            result = import_daily_bars(source, output, metadata, strict=False)

            self.assertEqual(result["row_count"], 1)
            self.assertEqual(result["issue_count"], 1)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()

