import unittest

from tools.fetch_daily_bars_sina import merge_rows, normalize_sina_rows, symbol_for_code


class FetchDailyBarsSinaTest(unittest.TestCase):
    def test_symbol_for_code_maps_a_share_markets(self) -> None:
        self.assertEqual(symbol_for_code("600000"), "sh600000")
        self.assertEqual(symbol_for_code("000001"), "sz000001")
        self.assertEqual(symbol_for_code("300750"), "sz300750")
        self.assertEqual(symbol_for_code("830799"), "bj830799")
        self.assertEqual(symbol_for_code("920079"), "bj920079")

    def test_normalize_sina_rows_outputs_standard_daily_bar_fields(self) -> None:
        rows = normalize_sina_rows(
            "600000",
            [
                {"day": "2026-07-01", "open": "10.00", "high": "10.20", "low": "9.90", "close": "10.00", "volume": "1000"},
                {"day": "2026-07-02", "open": "10.10", "high": "11.10", "low": "10.00", "close": "11.00", "volume": "2000"},
            ],
            updated_at="2026-07-10",
        )

        self.assertEqual(rows[0]["trade_date"], "2026-07-01")
        self.assertEqual(rows[0]["code"], "600000")
        self.assertEqual(rows[0]["pre_close"], "")
        self.assertEqual(rows[0]["turnover"], "10000")
        self.assertEqual(rows[0]["data_source"], "sina_kline")
        self.assertEqual(rows[1]["pre_close"], "10")
        self.assertEqual(rows[1]["is_limit_up"], True)
        self.assertEqual(rows[1]["updated_at"], "2026-07-10")

    def test_merge_rows_replaces_same_code_date_and_sorts(self) -> None:
        existing = [
            {"trade_date": "2026-07-02", "code": "600000", "close": "10"},
            {"trade_date": "2026-07-01", "code": "000001", "close": "12"},
        ]
        fetched = [{"trade_date": "2026-07-02", "code": "600000", "close": "11"}]

        rows = merge_rows(existing, fetched)

        self.assertEqual([(row["trade_date"], row["code"], row["close"]) for row in rows], [("2026-07-01", "000001", "12"), ("2026-07-02", "600000", "11")])


if __name__ == "__main__":
    unittest.main()
