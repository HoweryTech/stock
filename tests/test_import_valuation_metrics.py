import csv
import tempfile
import unittest
from pathlib import Path

from tools.import_valuation_metrics import import_valuation_metrics, read_valuation_metrics


ROOT = Path(__file__).resolve().parents[1]


class ImportValuationMetricsTest(unittest.TestCase):
    def test_imports_sample_valuation_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "valuation_metrics.csv"
            metadata_output = Path(tmp_dir) / "metadata.json"

            metadata = import_valuation_metrics(
                ROOT / "samples/valuation_metrics.sample.csv",
                output,
                metadata_output,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["row_count"], 3)
        self.assertEqual(metadata["issue_count"], 0)
        self.assertEqual(metadata["code_count"], 3)
        self.assertEqual(metadata["start_date"], "2026-07-02")
        self.assertEqual(rows[0]["code"], "000001")

    def test_reports_invalid_percentile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "trade_date,code,pe_percentile\n"
                "2026-07-02,600000,120\n",
                encoding="utf-8",
            )

            rows, issues = read_valuation_metrics(source)

        self.assertEqual(rows, [])
        self.assertEqual(len(issues), 1)
        self.assertIn("percentile must be between 0 and 100", issues[0].message)

    def test_reports_duplicate_date_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "trade_date,code,pe_ttm\n"
                "2026-07-02,600000,5.2\n"
                "2026-07-02,600000,5.3\n",
                encoding="utf-8",
            )

            rows, issues = read_valuation_metrics(source)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(issues), 1)
        self.assertIn("duplicate metrics", issues[0].message)

    def test_strict_import_fails_on_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "trade_date,code,pe_ttm\n"
                "bad-date,600000,5.2\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                import_valuation_metrics(source, output, metadata)

            self.assertTrue(metadata.exists())
            self.assertFalse(output.exists())

    def test_allow_invalid_writes_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "trade_date,code,pe_ttm\n"
                "2026-07-02,600000,5.2\n"
                "bad-date,000001,5.8\n",
                encoding="utf-8",
            )

            result = import_valuation_metrics(source, output, metadata, strict=False)

            self.assertEqual(result["row_count"], 1)
            self.assertEqual(result["issue_count"], 1)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
