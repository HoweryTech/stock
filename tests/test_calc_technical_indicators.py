import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tools.calc_technical_indicators import aggregate_period, build_report, render_markdown


def make_bar_csv(path: Path, code: str = "600000", days: int = 90) -> None:
    lines = [
        "trade_date,code,open,high,low,close,pre_close,volume,turnover,turnover_rate,is_limit_up,is_limit_down,is_suspended,adjust_type,data_source,updated_at"
    ]
    start = date(2026, 1, 1)
    previous_close = 10.0
    for index in range(days):
        current = start + timedelta(days=index)
        close = 10.0 + index * 0.08 + (index % 5) * 0.03
        open_price = previous_close + 0.02
        high = max(open_price, close) + 0.25
        low = min(open_price, close) - 0.2
        volume = 1_000_000 + index * 12_000
        turnover = volume * close
        lines.append(
            ",".join(
                [
                    current.isoformat(),
                    code,
                    f"{open_price:.2f}",
                    f"{high:.2f}",
                    f"{low:.2f}",
                    f"{close:.2f}",
                    f"{previous_close:.2f}",
                    str(volume),
                    f"{turnover:.2f}",
                    "1.0",
                    "false",
                    "false",
                    "false",
                    "none",
                    "test",
                    current.isoformat(),
                ]
            )
        )
        previous_close = close
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class CalcTechnicalIndicatorsTest(unittest.TestCase):
    def test_build_report_calculates_daily_weekly_monthly_indicators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            daily_bars = Path(tmp_dir) / "daily_bars.csv"
            make_bar_csv(daily_bars, days=150)

            report = build_report(daily_bars)

        self.assertEqual(report["source"]["code_count"], 1)
        self.assertEqual(report["indicator_policy"]["computed_from"], "local_ohlcv_bars")
        item = report["items"][0]
        self.assertEqual(item["code"], "600000")

        daily = item["periods"]["daily"]
        weekly = item["periods"]["weekly"]
        monthly = item["periods"]["monthly"]

        self.assertEqual(daily["bar_count"], 150)
        self.assertGreater(weekly["bar_count"], 20)
        self.assertGreaterEqual(monthly["bar_count"], 5)
        for name in ("macd", "boll", "rsi", "kdj", "atr", "volume"):
            self.assertEqual(daily[name]["status"], "ok")
        self.assertIsNotNone(daily["macd"]["histogram"])
        self.assertIn(daily["macd"]["cross_status"], {"bullish", "bearish", "turning_weak", "golden_cross", "dead_cross", "neutral"})
        self.assertIsNotNone(daily["macd"]["histogram_delta"])
        self.assertIsNotNone(daily["boll"]["percent_b"])
        self.assertIsNotNone(daily["rsi"]["rsi14"])
        self.assertIsNotNone(daily["kdj"]["j"])
        self.assertIsNotNone(daily["atr"]["atr_pct"])
        self.assertIsNotNone(daily["volume"]["volume_ratio_20"])

    def test_aggregate_period_builds_ohlcv_buckets(self) -> None:
        rows = [
            {"trade_date": "2026-01-01", "code": "600000", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 100.0, "turnover": 1020.0},
            {"trade_date": "2026-01-02", "code": "600000", "open": 10.2, "high": 10.8, "low": 10.0, "close": 10.6, "volume": 200.0, "turnover": 2120.0},
            {"trade_date": "2026-01-05", "code": "600000", "open": 10.6, "high": 11.0, "low": 10.4, "close": 10.9, "volume": 300.0, "turnover": 3270.0},
        ]

        weekly = aggregate_period(rows, "weekly")
        monthly = aggregate_period(rows, "monthly")

        self.assertEqual(len(weekly), 2)
        self.assertEqual(weekly[0]["trade_date"], "2026-01-02")
        self.assertEqual(weekly[0]["open"], 10.0)
        self.assertEqual(weekly[0]["high"], 10.8)
        self.assertEqual(weekly[0]["low"], 9.8)
        self.assertEqual(weekly[0]["close"], 10.6)
        self.assertEqual(weekly[0]["volume"], 300.0)
        self.assertEqual(len(monthly), 1)
        self.assertEqual(monthly[0]["volume"], 600.0)

    def test_render_markdown_outputs_summary_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            daily_bars = Path(tmp_dir) / "daily_bars.csv"
            make_bar_csv(daily_bars, days=45)

            markdown = render_markdown(build_report(daily_bars))

        self.assertIn("# 多周期技术指标", markdown)
        self.assertIn("| 代码 | 周期 | 日期 | 收盘 | MACD柱 |", markdown)
        self.assertIn("| 600000 | daily |", markdown)

    def test_json_report_is_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            daily_bars = Path(tmp_dir) / "daily_bars.csv"
            make_bar_csv(daily_bars, days=45)

            payload = json.dumps(build_report(daily_bars), ensure_ascii=False)

        self.assertIn("MACD(12,26,9)", payload)


if __name__ == "__main__":
    unittest.main()
