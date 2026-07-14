import unittest

from tools.backtest_reverse_t import parse_kline, simulate_day, summarize, wilson_lower_bound


class BacktestReverseTTest(unittest.TestCase):
    def setUp(self) -> None:
        self.costs = {
            "commission_rate": 0.0003, "minimum_commission": 5.0,
            "stamp_duty_rate": 0.0005, "transfer_fee_rate": 0.00001,
            "minimum_net_profit": 5.0, "verified": False,
        }

    def test_parse_kline(self) -> None:
        bar = parse_kline("000725", "2026-07-01 09:35,8.49,8.44,8.50,8.31,100,1000,2.19,-2.76,-0.24,1.97")
        self.assertEqual(bar["close"], 8.44)
        self.assertEqual(bar["timestamp"], "2026-07-01 09:35")

    def test_simulation_enters_on_next_bar_and_buys_back(self) -> None:
        bars = [
            {"timestamp": "2026-01-01 09:35", "open": 10.0, "close": 10.2, "high": 10.25, "low": 9.95},
            {"timestamp": "2026-01-01 09:40", "open": 10.2, "close": 10.22, "high": 10.3, "low": 10.15},
            {"timestamp": "2026-01-01 09:45", "open": 10.21, "close": 10.0, "high": 10.22, "low": 9.9},
        ]
        result = simulate_day(bars, max_shares=100, costs=self.costs)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sell_time"], "2026-01-01 09:40")
        self.assertGreaterEqual(result["net_profit"], 5.0)

    def test_small_sample_is_not_approved(self) -> None:
        bars = [{"timestamp": "2026-01-01 09:35", "open": 10, "close": 10, "high": 10, "low": 10}]
        result = summarize("000725", "京东方A", bars, 200, self.costs, 50)
        self.assertEqual(result["verdict"], "insufficient_sample")

    def test_current_intraday_result_is_excluded_from_validation(self) -> None:
        bars = [
            {"timestamp": "2026-07-14 09:35", "open": 10.0, "close": 10.2, "high": 10.25, "low": 9.95},
            {"timestamp": "2026-07-14 09:40", "open": 10.2, "close": 10.22, "high": 10.3, "low": 10.15},
            {"timestamp": "2026-07-14 09:45", "open": 10.21, "close": 10.0, "high": 10.22, "low": 9.9},
        ]
        result = summarize("000725", "京东方A", bars, 200, self.costs, 50, exclude_validation_date="2026-07-14")
        self.assertEqual(result["triggered_count"], 0)
        self.assertEqual(result["intraday_observation"]["status"], "completed")

    def test_wilson_lower_bound_penalizes_small_samples(self) -> None:
        self.assertLess(wilson_lower_bound(6, 10), 50)
        self.assertGreater(wilson_lower_bound(70, 100), 50)


if __name__ == "__main__":
    unittest.main()
