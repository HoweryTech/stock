import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.calc_trend_factors import run_calculation
from tools.generate_watchlist_report import generate_report, run_report
from tools.screen_trend_strength import run_screen


ROOT = Path(__file__).resolve().parents[1]


class GenerateWatchlistReportTest(unittest.TestCase):
    def test_generate_report_includes_decision_boundary_and_candidate(self) -> None:
        candidates = [
            {
                "code": "600000",
                "trade_date": "2026-07-02",
                "score": "7.68",
                "close": "10.36",
                "return": "1.568627",
                "ma": "10.28",
                "above_ma": "True",
                "turnover_avg": "1116700000",
                "reasons": "近 2 日收益率 1.57% >= 1.00%。 | 最新收盘价 站上 MA2。",
                "risks": "",
            }
        ]

        report = generate_report(candidates, generated_at=datetime(2026, 7, 7, 10, 0, 0))

        self.assertIn("# 候选股观察池报告", report)
        self.assertIn("不构成买入建议", report)
        self.assertIn("## 1. 600000", report)
        self.assertIn("近 2 日收益率", report)
        self.assertIn("tools/new_trade_plan.py", report)

    def test_empty_report_is_explicit(self) -> None:
        report = generate_report([], generated_at=datetime(2026, 7, 7, 10, 0, 0))

        self.assertIn("候选数量：0", report)
        self.assertIn("当前没有候选股", report)

    def test_run_report_writes_markdown_from_candidate_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidates.csv"
            output = Path(tmp_dir) / "watchlist.md"
            with candidates.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["code", "trade_date", "score", "close", "return", "ma", "above_ma", "turnover_avg", "reasons", "risks"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "code": "600000",
                        "trade_date": "2026-07-02",
                        "score": "7.68",
                        "close": "10.36",
                        "return": "1.568627",
                        "ma": "10.28",
                        "above_ma": "True",
                        "turnover_avg": "1116700000",
                        "reasons": "近 2 日收益率 1.57% >= 1.00%。",
                        "risks": "",
                    }
                )

            result = run_report(candidates, output)

            self.assertEqual(result["candidate_count"], 1)
            self.assertTrue(output.exists())
            self.assertIn("## 1. 600000", output.read_text(encoding="utf-8"))

    def test_report_generation_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            factors = Path(tmp_dir) / "trend_factors.csv"
            factor_metadata = Path(tmp_dir) / "trend_factors.json"
            candidates = Path(tmp_dir) / "trend_candidates.csv"
            candidate_metadata = Path(tmp_dir) / "trend_candidates.json"
            report = Path(tmp_dir) / "watchlist.md"

            run_calculation(ROOT / "samples/daily_bars.sample.csv", None, factors, factor_metadata, [2])
            run_screen(ROOT / "config/investment-profile.example.yaml", factors, candidates, candidate_metadata)
            result = run_report(candidates, report)

            content = report.read_text(encoding="utf-8")

        self.assertEqual(result["candidate_count"], 3)
        self.assertIn("## 1. 300750", content)
        self.assertIn("交易计划入口", content)


if __name__ == "__main__":
    unittest.main()
