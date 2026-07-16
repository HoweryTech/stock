import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.backtest_positive_timing import build_report, simulate_day, summarize_threshold, trade_fees


def make_day(day: str = "2026-07-16") -> list[dict]:
    closes = [9.70, 9.74, 9.78, 9.82, 9.86, 9.90, 9.94, 9.98, 10.02, 10.06, 10.10, 10.08, 10.04, 10.02, 10.00, 9.98, 9.96, 9.98, 10.00, 10.00, 10.02, 10.14, 10.16, 10.18]
    bars = []
    for index, close in enumerate(closes):
        bars.append(
            {
                "timestamp": f"{day} 10:{index:02d}",
                "code": "600000",
                "open": close,
                "high": close + 0.04,
                "low": close - 0.02,
                "close": close,
                "volume": 1000 + index * 20,
            }
        )
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
        )

        self.assertTrue(trades)
        self.assertEqual(trades[0]["outcome"], "completed")
        self.assertGreaterEqual(trades[0]["score"], 60)

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
            )

        self.assertEqual(report["portfolio_recommended_threshold"], 65)
        self.assertEqual(report["items"][0]["recommended"]["verdict"], "usable_for_watch")


if __name__ == "__main__":
    unittest.main()
