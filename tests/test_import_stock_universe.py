import csv
import tempfile
import unittest
from pathlib import Path

from tools.import_stock_universe import import_stock_universe, read_stock_universe


ROOT = Path(__file__).resolve().parents[1]


class ImportStockUniverseTest(unittest.TestCase):
    def test_imports_sample_stock_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "stock_universe.csv"
            metadata_output = Path(tmp_dir) / "metadata.json"

            metadata = import_stock_universe(
                ROOT / "samples/stock_universe.sample.csv",
                output,
                metadata_output,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["row_count"], 3)
        self.assertEqual(metadata["issue_count"], 0)
        self.assertEqual(rows[0]["code"], "600000")
        self.assertEqual(rows[1]["code"], "000001")
        self.assertEqual(rows[2]["exchange"], "SZSE")

    def test_reports_duplicate_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "code,name,exchange,industry,is_st,is_suspended,has_delisting_risk,abnormal_trading_status\n"
                "600000,测试,SSE,银行,false,false,false,false\n"
                "600000,测试2,SSE,银行,false,false,false,false\n",
                encoding="utf-8",
            )

            rows, issues = read_stock_universe(source)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(issues), 1)
        self.assertIn("duplicate code", issues[0].message)

    def test_strict_import_fails_on_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "code,name,exchange,industry,is_st,is_suspended,has_delisting_risk,abnormal_trading_status\n"
                "abc,测试,SSE,银行,false,false,false,false\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                import_stock_universe(source, output, metadata)

            self.assertTrue(metadata.exists())
            self.assertFalse(output.exists())

    def test_allow_invalid_writes_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "code,name,exchange,industry,is_st,is_suspended,has_delisting_risk,abnormal_trading_status\n"
                "600000,测试,SSE,银行,false,false,false,false\n"
                "bad,坏数据,SSE,银行,false,false,false,false\n",
                encoding="utf-8",
            )

            result = import_stock_universe(source, output, metadata, strict=False)

            self.assertEqual(result["row_count"], 1)
            self.assertEqual(result["issue_count"], 1)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
