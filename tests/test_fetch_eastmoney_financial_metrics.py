import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.fetch_eastmoney_financial_metrics import (
    fetch_financial_metrics,
    merge_rows,
    normalize_financial_row,
    security_code_with_exchange,
)


class FetchEastmoneyFinancialMetricsTest(unittest.TestCase):
    def test_security_code_with_exchange_maps_a_share_markets(self) -> None:
        self.assertEqual(security_code_with_exchange("600000"), "600000.SH")
        self.assertEqual(security_code_with_exchange("000001"), "000001.SZ")
        self.assertEqual(security_code_with_exchange("300750"), "300750.SZ")
        self.assertEqual(security_code_with_exchange("920438"), "920438.BJ")
        self.assertEqual(security_code_with_exchange("830799"), "830799.BJ")

    def test_normalize_financial_row_maps_eastmoney_fields(self) -> None:
        row = normalize_financial_row(
            {
                "REPORT_DATE": "2026-03-31 00:00:00",
                "ROEJQ": 5.21,
                "ZZCJLL": 1.32,
                "XSMLL": 42.5,
                "XSJLL": 9.8,
                "ZCFZL": 55.2,
                "NETCASH_OPERATE": 123456789,
                "TOTALOPERATEREVETZ": 8.9,
                "PARENTNETPROFITTZ": 12.3,
                "KCFJCXSYJLRTZ": 10.1,
                "BASIC_EPS": 0.42,
            },
            "600000",
            "2026-07-22",
        )

        self.assertEqual(row["report_period"], "2026-03-31")
        self.assertEqual(row["code"], "600000")
        self.assertEqual(row["roe"], "5.21")
        self.assertEqual(row["roa"], "1.32")
        self.assertEqual(row["operating_cash_flow"], "123456789")
        self.assertEqual(row["revenue_growth_yoy"], "8.9")
        self.assertEqual(row["net_profit_growth_yoy"], "12.3")
        self.assertEqual(row["deducted_net_profit_growth_yoy"], "10.1")
        self.assertEqual(row["eps"], "0.42")
        self.assertEqual(row["data_source"], "eastmoney_finance_mainfinadata")

    def test_merge_rows_replaces_same_code_period_and_sorts(self) -> None:
        existing = [
            {"code": "600000", "report_period": "2026-03-31", "roe": "3.8"},
            {"code": "000001", "report_period": "2025-12-31", "roe": "4.1"},
        ]
        fetched = [{"code": "600000", "report_period": "2026-03-31", "roe": "4.2"}]

        rows = merge_rows(existing, fetched)

        self.assertEqual(
            [(row["code"], row["report_period"], row["roe"]) for row in rows],
            [("000001", "2025-12-31", "4.1"), ("600000", "2026-03-31", "4.2")],
        )

    def test_fetch_financial_metrics_writes_output_metadata_and_snapshot(self) -> None:
        payloads = {
            "600000": [
                {"REPORT_DATE": "2026-03-31", "ROEJQ": 5.0, "ZCFZL": 50.0},
                {"REPORT_DATE": "2025-12-31", "ROEJQ": 4.0, "ZCFZL": 52.0},
            ],
            "000001": [{"REPORT_DATE": "2026-03-31", "ROEJQ": 6.0, "ZCFZL": 60.0}],
        }

        def fake_fetch(code: str, report_count: int, timeout: float):
            return [normalize_financial_row(row, code, "2026-07-22") for row in payloads[code][:report_count]]

        with tempfile.TemporaryDirectory() as tmp_dir, patch("tools.fetch_eastmoney_financial_metrics.fetch_financial_rows_for_code", side_effect=fake_fetch):
            base = Path(tmp_dir)
            output = base / "financial_metrics.csv"
            archive_root = base / "snapshots"

            metadata = fetch_financial_metrics(
                ["600000", "000001"],
                output,
                report_count=2,
                merge_existing=True,
                archive_root=archive_root,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["mode"], "history")
        self.assertEqual(metadata["requested_code_count"], 2)
        self.assertEqual(metadata["success_code_count"], 2)
        self.assertEqual(metadata["fetched_row_count"], 3)
        self.assertEqual(metadata["output_row_count"], 3)
        self.assertEqual(metadata["start_period"], "2025-12-31")
        self.assertEqual(metadata["end_period"], "2026-03-31")
        self.assertIsNotNone(metadata["retained_snapshot"])
        self.assertEqual(rows[0]["code"], "000001")
        self.assertEqual(rows[0]["report_period"], "2026-03-31")


if __name__ == "__main__":
    unittest.main()
