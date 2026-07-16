import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.backtest_positive_timing import build_report, simulate_day, summarize_threshold, trade_bounds, trade_fees


def make_day(day: str = "2026-07-16") -> list[dict]:
    closes = [9.70, 9.74, 9.78, 9.82, 9.86, 9.90, 9.94, 9.98, 10.02, 10.06, 10.10, 10.08, 10.04, 10.02, 10.00, 9.98, 9.96, 9.98, 9.96, 10.02, 10.04, 10.18, 10.20, 10.22]
    bars = []
    for index, close in enumerate(closes):
        bars.append(
            {
                "timestamp": f"{day} 10:{index:02d}",
                "code": "600000",
                "open": close - 0.01,
                "high": close + 0.04,
                "low": close - 0.02,
                "close": close,
                "volume": 1000 + index * 20,
            }
        )
    return bars


def make_unconfirmed_day(day: str = "2026-07-16") -> list[dict]:
    bars = []
    for bar in make_day(day):
        item = dict(bar)
        item["open"] = item["close"] + 0.01
        item["volume"] = 600
        bars.append(item)
    return bars


class BacktestPositiveTimingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.costs = {
            "commission_rate": 0.0003,
            "minimum_commission": 0.0,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
        }

    def test_trade_fees_include_sell_stamp_duty(self) -> None:
        fees = trade_fees(10.0, 10.2, 100, self.costs)

        self.assertGreater(fees["fees"], 0)
        self.assertLess(fees["net_profit"], fees["gross_profit"])

    def test_simulate_day_records_completed_trade_after_score_signal(self) -> None:
        trades = simulate_day(
            "600000",
            make_day(),
            threshold=60,
            horizon_bars=6,
            target_pct=1.2,
            stop_pct=1.0,
            trade_shares=100,
            costs=self.costs,
            adaptive_bounds=False,
            minimum_net_profit=0.0,
        )

        self.assertTrue(trades)
        self.assertEqual(trades[0]["outcome"], "completed")
        self.assertGreaterEqual(trades[0]["score"], 60)

    def test_simulate_day_requires_confirmed_timing_status(self) -> None:
        trades = simulate_day(
            "600000",
            make_unconfirmed_day(),
            threshold=60,
            horizon_bars=6,
            target_pct=1.2,
            stop_pct=1.0,
            trade_shares=100,
            costs=self.costs,
            adaptive_bounds=False,
            minimum_net_profit=0.0,
        )

        self.assertEqual(trades, [])

    def test_simulate_day_blocks_bearish_higher_timeframe_context(self) -> None:
        trades = simulate_day(
            "600000",
            make_day(),
            threshold=60,
            horizon_bars=6,
            target_pct=1.2,
            stop_pct=1.0,
            trade_shares=100,
            costs=self.costs,
            adaptive_bounds=False,
            minimum_net_profit=0.0,
            technical_context_by_day={
                "2026-07-16": {
                    "available": True,
                    "score": -25,
                    "label": "bearish",
                    "signals": ["测试用偏空背景"],
                    "periods": {},
                }
            },
        )

        self.assertEqual(trades, [])

    def test_summarize_threshold(self) -> None:
        summary = summarize_threshold(
            [
                {"outcome": "completed", "net_profit": 10},
                {"outcome": "stopped", "net_profit": -8},
            ],
            65,
        )

        self.assertEqual(summary["triggered_count"], 2)
        self.assertEqual(summary["success_rate_pct"], 50.0)
        self.assertEqual(summary["stop_rate_pct"], 50.0)

    def test_build_report_recommends_threshold_when_sample_passes(self) -> None:
        position_path = Path("positions/POS-600000.yaml")
        fake_position = {"stock": {"code": "600000", "name": "浦发银行"}}
        minute_bars = {"600000": make_day("2026-07-15") + make_day("2026-07-16")}
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch("tools.backtest_positive_timing.load_yaml", return_value=fake_position),
            patch("tools.backtest_positive_timing.load_minute_bars", return_value=minute_bars),
        ):
            report = build_report(
                [position_path],
                cache_dir=Path(tmp_dir),
                thresholds=[60, 65, 70],
                horizon_bars=6,
                target_pct=1.2,
                stop_pct=1.0,
                trade_shares=100,
                costs=self.costs,
                min_triggers=1,
                adaptive_bounds=False,
                minimum_net_profit=0.0,
            )

        self.assertEqual(report["portfolio_recommended_threshold"], 70)
        self.assertEqual(report["items"][0]["recommended"]["verdict"], "usable_for_watch")

    def test_adaptive_bounds_raise_target_to_cover_minimum_fee(self) -> None:
        costs = {
            "commission_rate": 0.0003,
            "minimum_commission": 5.0,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
        }
        prefix = make_day()[:20]

        bounds = trade_bounds(
            prefix,
            6.0,
            100,
            costs,
            target_pct=1.2,
            stop_pct=1.0,
            adaptive=True,
            min_target_pct=1.2,
            max_target_pct=4.0,
            min_stop_pct=0.8,
            max_stop_pct=2.0,
            range_target_multiplier=1.2,
            range_stop_multiplier=0.8,
            minimum_net_profit=5.0,
        )

        self.assertFalse(bounds["fee_blocked"])
        self.assertGreater(bounds["target_pct"], 1.2)
        self.assertGreaterEqual(bounds["stop_pct"], 0.8)


if __name__ == "__main__":
    unittest.main()
