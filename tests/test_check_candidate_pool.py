import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.check_candidate_pool import CheckContext, check_candidate, check_candidates, run_check


class CheckCandidatePoolTest(unittest.TestCase):
    def test_passes_well_evidenced_multi_strategy_candidate(self) -> None:
        candidate = {
            "code": "300750",
            "strategies": "trend_strength|value_quality",
            "primary_strategy": "multi_strategy",
            "trend_score": "11.5",
            "value_quality_score": "18.8",
            "trade_date": "2026-07-02",
            "report_period": "2026-03-31",
            "reasons": "[trend_strength] 趋势强，近 2 日平均成交额 10090000000。 | [value_quality] PE 分位 68.00 <= 80.00。",
            "risks": "[value_quality] 估值分位接近上限，需确认安全边际。",
        }

        result = check_candidates([candidate])

        self.assertEqual(result["conclusion"], "pass")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["warnings"], [])

    def test_blocks_missing_evidence(self) -> None:
        result = check_candidates([{"code": "600000", "strategies": "trend_strength", "primary_strategy": "trend_strength"}])

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "600000" for item in result["blockers"]))
        self.assertTrue(any("缺少入选证据" in item["message"] for item in result["blockers"]))

    def test_warns_single_strategy_and_missing_risks(self) -> None:
        candidate = {
            "code": "600000",
            "strategies": "trend_strength",
            "primary_strategy": "trend_strength",
            "trend_score": "7.6",
            "trade_date": "2026-07-02",
            "reasons": "[trend_strength] 趋势强。",
            "risks": "",
        }

        items = check_candidate(candidate)

        self.assertTrue(any(item.level == "warning" and "单策略候选" in item.message for item in items))
        self.assertTrue(any(item.level == "warning" and "缺少显式风险提示" in item.message for item in items))
        self.assertTrue(any(item.level == "warning" and "少于 2 个非风险维度" in item.message for item in items))

    def test_blocks_candidate_outside_tradable_universe(self) -> None:
        result = check_candidates(
            [
                {
                    "code": "300750",
                    "strategies": "trend_strength|value_quality",
                    "primary_strategy": "multi_strategy",
                    "trend_score": "11.5",
                    "value_quality_score": "18.8",
                    "trade_date": "2026-07-02",
                    "report_period": "2026-03-31",
                    "reasons": "[trend_strength] 趋势强，近 2 日平均成交额 10090000000。 | [value_quality] PE 分位 68.00 <= 80.00。",
                    "risks": "[value_quality] 估值分位接近上限。",
                }
            ],
            context=CheckContext(tradable_codes={"600000"}),
        )

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any("不在可交易股票池" in item["message"] for item in result["blockers"]))

    def test_warns_stale_trend_candidate_when_as_of_is_supplied(self) -> None:
        context = CheckContext(
            as_of=datetime(2026, 7, 20),
            max_trend_age_days=5,
        )
        items = check_candidate(
            {
                "code": "300750",
                "strategies": "trend_strength|value_quality",
                "primary_strategy": "multi_strategy",
                "trend_score": "11.5",
                "value_quality_score": "18.8",
                "trade_date": "2026-07-02",
                "report_period": "2026-03-31",
                "reasons": "[trend_strength] 趋势强，近 2 日平均成交额 10090000000。 | [value_quality] PE 分位 68.00 <= 80.00。",
                "risks": "[value_quality] 估值分位接近上限。",
            },
            context,
        )

        self.assertTrue(any(item.level == "warning" and "趋势交易日早于检查日" in item.message for item in items))

    def test_run_check_reads_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidate_pool.csv"
            with candidates.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "code",
                        "strategies",
                        "primary_strategy",
                        "trend_score",
                        "value_quality_score",
                        "trade_date",
                        "report_period",
                        "reasons",
                        "risks",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "code": "300750",
                        "strategies": "trend_strength|value_quality",
                        "primary_strategy": "multi_strategy",
                        "trend_score": "11.5",
                        "value_quality_score": "18.8",
                        "trade_date": "2026-07-02",
                        "report_period": "2026-03-31",
                        "reasons": "[trend_strength] 趋势强，近 2 日平均成交额 10090000000。 | [value_quality] PE 分位 68.00 <= 80.00。",
                        "risks": "[value_quality] 估值分位接近上限。",
                    }
                )

            result = run_check(candidates)

        self.assertEqual(result["conclusion"], "pass")


if __name__ == "__main__":
    unittest.main()
