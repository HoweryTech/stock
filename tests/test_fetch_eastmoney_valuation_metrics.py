import unittest
from pathlib import Path

from tools.fetch_eastmoney_valuation_metrics import build_metadata, normalize_row


class FetchEastmoneyValuationMetricsTest(unittest.TestCase):
    def test_normalize_row_maps_quote_fields_to_valuation_schema(self) -> None:
        row = normalize_row(
            {
                "f12": "600000",
                "f9": 4.2,
                "f23": 0.42,
                "f20": 294756668955,
                "f21": 294700000000,
                "f115": 4.13,
            },
            "2026-07-22",
            "2026-07-22",
        )

        self.assertEqual(row["code"], "600000")
        self.assertEqual(row["pe_ttm"], "4.13")
        self.assertEqual(row["pb"], "0.42")
        self.assertEqual(row["market_cap"], "294756668955")
        self.assertEqual(row["float_market_cap"], "294700000000")
        self.assertEqual(row["ps_ttm"], "")
        self.assertEqual(row["data_source"], "eastmoney_quote_list")

    def test_normalize_row_falls_back_to_dynamic_pe_when_ttm_missing(self) -> None:
        row = normalize_row({"f12": "000001", "f9": 5.8, "f115": "-"}, "2026-07-22", "2026-07-22")

        self.assertEqual(row["pe_ttm"], "5.8")

    def test_metadata_reports_missing_reserved_fields(self) -> None:
        metadata = build_metadata(
            [
                normalize_row({"f12": "600000", "f9": 4.2, "f23": 0.42, "f20": 100, "f21": 80}, "2026-07-22", "2026-07-22")
            ],
            output=Path("valuation_metrics.csv"),
        )

        self.assertEqual(metadata["row_count"], 1)
        self.assertEqual(metadata["missing_by_field"]["ps_ttm"], 1)
        self.assertEqual(metadata["missing_by_field"]["pcf_ttm"], 1)


if __name__ == "__main__":
    unittest.main()
