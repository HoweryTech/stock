import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.calc_trend_factors import run_calculation
from tools.generate_watchlist_report import generate_report, run_report
from tools.merge_candidate_pool import run_merge
from tools.screen_trend_strength import run_screen
from tools.screen_value_quality import run_screen as run_value_quality_screen


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

    def test_generate_report_supports_unified_candidate_pool(self) -> None:
        candidates = [
            {
                "code": "300750",
                "name": "宁德时代",
                "industry": "电力设备",
                "exchange": "SZSE",
                "board": "chinext",
                "strategies": "event_catalyst|trend_strength|value_quality",
                "strategy_count": "3",
                "combined_score": "278.377248",
                "primary_strategy": "multi_strategy",
                "trend_score": "11.522248",
                "value_quality_score": "20.855",
                "event_score": "46",
                "event_date": "2026-07-02",
                "event_type": "major_order",
                "liquidity_score": "100.0",
                "liquidity_evidence": "趋势窗口平均成交额 10090000000",
                "industry_strength_score": "15",
                "industry_strength_evidence": "行业近 2 日收益率 1.52%",
                "technical_health_score": "12.5",
                "technical_health_status": "watch",
                "technical_health_evidence": "daily MACD偏强；weekly RSI14健康",
                "technical_risk_flags": "",
                "portfolio_fit_status": "ready_for_plan",
                "portfolio_fit_action": "prepare_trade_plan",
                "portfolio_fit_evidence": "组合仓位、行业暴露和策略健康检查未发现阻断。",
                "expected_stock_position_pct_after_buy": "5.0",
                "expected_industry_position_pct_after_buy": "10.0",
                "expected_total_position_pct_after_buy": "35.0",
                "trade_date": "2026-07-02",
                "report_period": "2026-03-31",
                "reasons": "[trend_strength] 趋势强。 | [value_quality] 质量好。",
                "risks": "",
            }
        ]

        report = generate_report(candidates, generated_at=datetime(2026, 7, 7, 10, 0, 0))

        self.assertIn("主策略：多策略共振", report)
        self.assertIn("## 1. 300750 宁德时代", report)
        self.assertIn("行业：电力设备", report)
        self.assertIn("交易所：SZSE", report)
        self.assertIn("板块：chinext", report)
        self.assertIn("策略来源：事件催化, 趋势强度, 价值质量", report)
        self.assertIn("综合排序分：278.377248", report)
        self.assertIn("事件分：46", report)
        self.assertIn("事件类型：major_order", report)
        self.assertIn("流动性分：100.0", report)
        self.assertIn("趋势窗口平均成交额", report)
        self.assertIn("行业强度分：15", report)
        self.assertIn("行业近 2 日收益率", report)
        self.assertIn("技术健康状态：watch", report)
        self.assertIn("daily MACD偏强", report)
        self.assertIn("组合适配状态：ready_for_plan", report)
        self.assertIn("买入后总仓位：35.0", report)
        self.assertIn("[value_quality] 质量好。", report)

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

    def test_unified_report_generation_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            factors = Path(tmp_dir) / "trend_factors.csv"
            factor_metadata = Path(tmp_dir) / "trend_factors.json"
            trend_candidates = Path(tmp_dir) / "trend_candidates.csv"
            trend_metadata = Path(tmp_dir) / "trend_candidates.json"
            value_candidates = Path(tmp_dir) / "value_quality_candidates.csv"
            value_metadata = Path(tmp_dir) / "value_quality_candidates.json"
            candidate_pool = Path(tmp_dir) / "candidate_pool.csv"
            pool_metadata = Path(tmp_dir) / "candidate_pool.json"
            report = Path(tmp_dir) / "watchlist.md"

            run_calculation(ROOT / "samples/daily_bars.sample.csv", None, factors, factor_metadata, [2])
            run_screen(ROOT / "config/investment-profile.example.yaml", factors, trend_candidates, trend_metadata)
            run_value_quality_screen(
                ROOT / "config/investment-profile.example.yaml",
                ROOT / "samples/financial_metrics.sample.csv",
                value_candidates,
                value_metadata,
                ROOT / "samples/valuation_metrics.sample.csv",
            )
            run_merge(trend_candidates, value_candidates, candidate_pool, pool_metadata, universe_path=ROOT / "samples/stock_universe.sample.csv")
            result = run_report(candidate_pool, report)

            content = report.read_text(encoding="utf-8")

        self.assertEqual(result["candidate_count"], 3)
        self.assertIn("主策略：多策略共振", content)
        self.assertIn("300750 宁德时代", content)
        self.assertIn("行业：电力设备", content)
        self.assertIn("策略来源：趋势强度, 价值质量", content)


if __name__ == "__main__":
    unittest.main()
