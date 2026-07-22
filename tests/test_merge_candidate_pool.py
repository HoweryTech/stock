import csv
import tempfile
import unittest
from pathlib import Path

from tools.merge_candidate_pool import merge_candidates, run_merge


class MergeCandidatePoolTest(unittest.TestCase):
    def test_merges_same_code_as_multi_strategy_candidate(self) -> None:
        trend_rows = [
            {
                "code": "300750",
                "trade_date": "2026-07-02",
                "score": "12.5",
                "reasons": "趋势强。",
                "risks": "追高风险。",
            }
        ]
        value_rows = [
            {
                "code": "300750",
                "report_period": "2026-03-31",
                "score": "20.855",
                "reasons": "质量较好。",
                "risks": "",
            }
        ]

        candidates = merge_candidates(trend_rows, value_rows)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["code"], "300750")
        self.assertEqual(candidates[0]["primary_strategy"], "multi_strategy")
        self.assertEqual(candidates[0]["strategies"], "trend_strength|value_quality")
        self.assertIn("[trend_strength] 趋势强。", candidates[0]["reasons"])
        self.assertIn("[value_quality] 质量较好。", candidates[0]["reasons"])

    def test_keeps_single_strategy_candidates(self) -> None:
        candidates = merge_candidates(
            [{"code": "600000", "score": "7", "reasons": "趋势强。", "risks": ""}],
            [{"code": "300750", "score": "20", "reasons": "质量好。", "risks": ""}],
        )

        self.assertEqual([candidate["code"] for candidate in candidates], ["300750", "600000"])
        self.assertEqual(candidates[0]["primary_strategy"], "value_quality")
        self.assertEqual(candidates[1]["primary_strategy"], "trend_strength")

    def test_limits_candidates_after_sorting(self) -> None:
        candidates = merge_candidates(
            [{"code": "600000", "score": "7", "reasons": "", "risks": ""}],
            [{"code": "300750", "score": "20", "reasons": "", "risks": ""}],
            max_candidates=1,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["code"], "300750")

    def test_enriches_name_industry_and_liquidity_from_universe(self) -> None:
        candidates = merge_candidates(
            [{"code": "300750", "score": "12.5", "turnover_avg": "10090000000", "reasons": "趋势强。", "risks": ""}],
            [],
            universe_context={
                "300750": {
                    "name": "宁德时代",
                    "industry": "电力设备",
                    "avg_daily_turnover_cny": "2850000000",
                }
            },
        )

        self.assertEqual(candidates[0]["name"], "宁德时代")
        self.assertEqual(candidates[0]["industry"], "电力设备")
        self.assertEqual(candidates[0]["liquidity_score"], "100.0")
        self.assertIn("趋势窗口平均成交额", candidates[0]["liquidity_evidence"])
        self.assertIn("股票池平均成交额", candidates[0]["liquidity_evidence"])

    def test_enriches_industry_strength_and_adds_small_score_weight(self) -> None:
        candidates = merge_candidates(
            [{"code": "300750", "score": "12.5", "reasons": "趋势强。", "risks": ""}],
            [{"code": "300750", "score": "20", "reasons": "质量好。", "risks": ""}],
            industry_context={
                "300750": {
                    "industry_strength_score": "15",
                    "industry_strength_evidence": "行业近 2 日收益率 1.52%",
                }
            },
        )

        self.assertEqual(candidates[0]["industry_strength_score"], "15")
        self.assertEqual(candidates[0]["industry_strength_evidence"], "行业近 2 日收益率 1.52%")
        self.assertEqual(candidates[0]["combined_score"], 235.5)

    def test_merges_event_catalyst_as_third_strategy(self) -> None:
        candidates = merge_candidates(
            [{"code": "300750", "score": "12.5", "reasons": "趋势强。", "risks": ""}],
            [{"code": "300750", "score": "20", "reasons": "质量好。", "risks": ""}],
            [
                {
                    "code": "300750",
                    "event_date": "2026-07-02",
                    "event_type": "major_order",
                    "score": "46",
                    "reasons": "事件真实可验证。",
                    "risks": "需跟踪订单交付。",
                }
            ],
        )

        self.assertEqual(candidates[0]["strategies"], "event_catalyst|trend_strength|value_quality")
        self.assertEqual(candidates[0]["event_score"], "46")
        self.assertEqual(candidates[0]["event_date"], "2026-07-02")
        self.assertEqual(candidates[0]["event_type"], "major_order")
        self.assertEqual(candidates[0]["combined_score"], 378.5)

    def test_run_merge_writes_csv_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            trend_path = Path(tmp_dir) / "trend.csv"
            value_path = Path(tmp_dir) / "value.csv"
            universe_path = Path(tmp_dir) / "universe.csv"
            industry_path = Path(tmp_dir) / "industry.csv"
            output_path = Path(tmp_dir) / "pool.csv"
            metadata_path = Path(tmp_dir) / "pool.json"

            with trend_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "trade_date", "score", "turnover_avg", "reasons", "risks"])
                writer.writeheader()
                writer.writerow({"code": "300750", "trade_date": "2026-07-02", "score": "12.5", "turnover_avg": "10090000000", "reasons": "趋势强。", "risks": ""})
            with value_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "report_period", "score", "reasons", "risks"])
                writer.writeheader()
                writer.writerow({"code": "300750", "report_period": "2026-03-31", "score": "20.855", "reasons": "质量好。", "risks": ""})
            with universe_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "name", "industry", "avg_daily_turnover_cny"])
                writer.writeheader()
                writer.writerow({"code": "300750", "name": "宁德时代", "industry": "电力设备", "avg_daily_turnover_cny": "2850000000"})
            with industry_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "industry_strength_score", "industry_strength_evidence"])
                writer.writeheader()
                writer.writerow({"code": "300750", "industry_strength_score": "15", "industry_strength_evidence": "行业强。"})

            metadata = run_merge(trend_path, value_path, output_path, metadata_path, universe_path=universe_path, industry_strength_path=industry_path)

            with output_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["candidate_count"], 1)
        self.assertEqual(metadata["multi_strategy_count"], 1)
        self.assertEqual(metadata["enriched_count"], 1)
        self.assertEqual(metadata["liquidity_scored_count"], 1)
        self.assertEqual(metadata["industry_strength_scored_count"], 1)
        self.assertEqual(rows[0]["primary_strategy"], "multi_strategy")
        self.assertEqual(rows[0]["name"], "宁德时代")
        self.assertEqual(rows[0]["industry"], "电力设备")
        self.assertEqual(rows[0]["industry_strength_score"], "15")


if __name__ == "__main__":
    unittest.main()
