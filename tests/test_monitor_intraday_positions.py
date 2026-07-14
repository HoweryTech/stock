import unittest

from tools.monitor_intraday_positions import analyze_quote, moving_averages, state_signature


class MonitorIntradayPositionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 9.115, "position_pct_of_total_assets": 5.3611},
        }

    def test_moving_averages(self) -> None:
        ma5, ma20 = moving_averages([float(value) for value in range(1, 21)])
        self.assertEqual(ma5, 18.0)
        self.assertEqual(ma20, 10.5)

    def test_analyzes_live_risk_state(self) -> None:
        quote = {
            "code": "000725",
            "name": "京东方A",
            "latest_price": 6.83,
            "change_pct": -10.0,
            "quote_timestamp": 1000,
        }
        item = analyze_quote(
            self.position,
            quote,
            [7.0] * 20,
            total_assets=25480,
            max_stale_seconds=60,
            now_timestamp=1003,
        )
        self.assertEqual(item["state"], "risk_review")
        self.assertAlmostEqual(item["position"]["unrealized_pnl"], -457.0)
        self.assertTrue(any(signal["code"] == "limit_down_or_near" for signal in item["signals"]))
        self.assertFalse(item["guardrails"]["t_trade_allowed"])

    def test_state_signature_ignores_price_only_changes(self) -> None:
        first = {"items": [{"code": "000725", "state": "observe", "signals": [], "quote": {"latest_price": 6.8}}]}
        second = {"items": [{"code": "000725", "state": "observe", "signals": [], "quote": {"latest_price": 6.9}}]}
        self.assertEqual(state_signature(first), state_signature(second))


if __name__ == "__main__":
    unittest.main()
