import csv
import tempfile
import unittest
from pathlib import Path

from tools.track_candidate_performance import build_report, render_markdown, track_candidate


class TrackCandidatePerformanceTest(unittest.TestCase):
    def test_tracks_forward_returns_from_entry_date(self) -> None:
        candidate = {
            "code": "300750",
            "name": "宁德时代",
            "strategies": "trend_strength|value_quality",
            "trade_date": "2026-07-02",
        }
        bars = {
            "300750": [
                {"trade_date": "2026-07-01", "close": 100.0},
                {"trade_date": "2026-07-02", "close": 110.0},
                {"trade_date": "2026-07-03", "close": 121.0},
                {"trade_date": "2026-07-06", "close": 99.0},
            ]
        }

        result = track_candidate(candidate, bars, [1, 2])

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["entry_trade_date"], "2026-07-02")
        self.assertEqual(result["horizons"]["1"]["return_pct"], 10.0)
        self.assertEqual(result["horizons"]["2"]["return_pct"], -10.0)

    def test_marks_pending_when_future_bars_are_missing(self) -> None:
        result = track_candidate(
            {"code": "600000", "trade_date": "2026-07-02"},
            {"600000": [{"trade_date": "2026-07-02", "close": 10.0}]},
            [1],
        )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["horizons"]["1"]["status"], "insufficient_future_bars")

    def test_build_report_reads_csv_and_renders_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            candidates = base / "candidate_pool.csv"
            daily_bars = base / "daily_bars.csv"
            with candidates.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "name", "strategies", "trade_date"])
                writer.writeheader()
                writer.writerow({"code": "300750", "name": "宁德时代", "strategies": "trend_strength|value_quality", "trade_date": "2026-07-02"})
            with daily_bars.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["trade_date", "code", "close"])
                writer.writeheader()
                writer.writerow({"trade_date": "2026-07-02", "code": "300750", "close": "110"})
                writer.writerow({"trade_date": "2026-07-03", "code": "300750", "close": "121"})

            report = build_report(candidates, daily_bars, [1])
            markdown = render_markdown(report)

        self.assertEqual(report["summary"]["candidate_count"], 1)
        self.assertEqual(report["summary"]["horizons"]["1"]["average_return_pct"], 10.0)
        self.assertIn("候选池入池后表现跟踪", markdown)
        self.assertIn("300750", markdown)
        self.assertIn("trend_strength, value_quality", markdown)
        self.assertNotIn("trend_strength|value_quality", markdown)


if __name__ == "__main__":
    unittest.main()
