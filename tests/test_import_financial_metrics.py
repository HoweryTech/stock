import csv
import tempfile
import unittest
from pathlib import Path

from tools.import_financial_metrics import import_financial_metrics, read_financial_metrics


ROOT = Path(__file__).resolve().parents[1]


class ImportFinancialMetricsTest(unittest.TestCase):
    def test_imports_sample_financial_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "financial_metrics.csv"
            metadata_output = Path(tmp_dir) / "metadata.json"

            metadata = import_financial_metrics(
                ROOT / "samples/financial_metrics.sample.csv",
                output,
                metadata_output,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["row_count"], 3)
        self.assertEqual(metadata["issue_count"], 0)
        self.assertEqual(metadata["code_count"], 3)
        self.assertEqual(metadata["start_period"], "2026-03-31")
        self.assertEqual(rows[0]["code"], "000001")

    def test_reports_invalid_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "report_period,code,roe\n"
                "2026-03-31,600000,bad\n",
                encoding="utf-8",
            )

            rows, issues = read_financial_metrics(source)

        self.assertEqual(rows, [])
        self.assertEqual(len(issues), 1)
        self.assertIn("invalid number", issues[0].message)

    def test_reports_duplicate_period_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            source.write_text(
                "report_period,code,roe\n"
                "2026-03-31,600000,3.8\n"
                "2026-03-31,600000,4.1\n",
                encoding="utf-8",
            )

            rows, issues = read_financial_metrics(source)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(issues), 1)
        self.assertIn("duplicate metrics", issues[0].message)

    def test_strict_import_fails_on_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "report_period,code,roe\n"
                "bad-date,600000,3.8\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                import_financial_metrics(source, output, metadata)

            self.assertTrue(metadata.exists())
            self.assertFalse(output.exists())

    def test_allow_invalid_writes_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input.csv"
            output = Path(tmp_dir) / "out.csv"
            metadata = Path(tmp_dir) / "meta.json"
            source.write_text(
                "report_period,code,roe\n"
                "2026-03-31,600000,3.8\n"
                "bad-date,000001,4.2\n",
                encoding="utf-8",
            )

            result = import_financial_metrics(source, output, metadata, strict=False)

            self.assertEqual(result["row_count"], 1)
            self.assertEqual(result["issue_count"], 1)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()

