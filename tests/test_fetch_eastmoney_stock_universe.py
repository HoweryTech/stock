import unittest

from tools.fetch_eastmoney_stock_universe import normalize_row


class FetchEastmoneyStockUniverseTest(unittest.TestCase):
    def test_normalize_row_maps_quote_fields_to_universe_schema(self) -> None:
        row = normalize_row(
            {
                "f12": "688001",
                "f14": "华兴源创",
                "f13": 1,
                "f100": "专用设备",
                "f2": 32.5,
                "f6": 123456789.12,
                "f26": 20190722,
            },
            "2026-07-22",
        )

        self.assertEqual(row["code"], "688001")
        self.assertEqual(row["exchange"], "SSE")
        self.assertEqual(row["industry"], "专用设备")
        self.assertEqual(row["listing_date"], "2019-07-22")
        self.assertEqual(row["avg_daily_turnover_cny"], 123456789.12)
        self.assertFalse(row["is_st"])
        self.assertFalse(row["is_suspended"])

    def test_normalize_row_identifies_beijing_exchange_codes(self) -> None:
        row = normalize_row(
            {
                "f12": "920079",
                "f14": "北交样例",
                "f13": 0,
                "f100": "汽车零部件",
                "f2": 19.76,
                "f6": 658136601.64,
                "f26": 20260722,
            },
            "2026-07-22",
        )

        self.assertEqual(row["exchange"], "BSE")


if __name__ == "__main__":
    unittest.main()
