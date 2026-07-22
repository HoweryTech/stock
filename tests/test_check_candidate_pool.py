import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.check_candidate_pool import CheckContext, check_candidate, check_candidates, run_check


def scoring_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "combined_score": "244.3",
        "strategy_confluence_score": "200.0",
        "strategy_confluence_evidence": "命中 2 个策略：trend_strength, value_quality",
        "data_quality_score": "20.0",
        "data_quality_status": "complete",
        "data_quality_evidence": "已具备：入选证据, 风险提示, 流动性",
        "risk_penalty_score": "-3.0",
        "risk_penalty_evidence": "风险提示 1 条",
        "liquidity_score": "100.0",
        "liquidity_evidence": "趋势窗口平均成交额 10090000000",
    }
    fields.update(overrides)
    return fields


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
            **scoring_fields(),
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
            **scoring_fields(),
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
                **scoring_fields(),
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
                        "combined_score",
                        "strategy_confluence_score",
                        "strategy_confluence_evidence",
                        "data_quality_score",
                        "data_quality_status",
                        "data_quality_evidence",
                        "risk_penalty_score",
                        "risk_penalty_evidence",
                        "liquidity_score",
                        "liquidity_evidence",
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
                        **scoring_fields(),
                    }
                )

            result = run_check(candidates)

        self.assertEqual(result["conclusion"], "pass")

    def test_warns_when_score_explanation_fields_are_missing(self) -> None:
        candidate = {
            "code": "300750",
            "strategies": "trend_strength|value_quality",
            "primary_strategy": "multi_strategy",
            "trend_score": "11.5",
            "value_quality_score": "18.8",
            "trade_date": "2026-07-02",
            "report_period": "2026-03-31",
            "liquidity_score": "100.0",
            "liquidity_evidence": "趋势窗口平均成交额 10090000000",
            "reasons": "[trend_strength] 趋势强，近 2 日平均成交额 10090000000。 | [value_quality] PE 分位 68.00 <= 80.00。",
            "risks": "[value_quality] 估值分位接近上限。",
        }

        result = check_candidates([candidate])

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any("缺少评分解释字段" in item["message"] for item in result["warnings"]))

    def test_warns_weak_data_quality_and_high_risk_penalty(self) -> None:
        candidate = {
            "code": "300750",
            "strategies": "trend_strength|value_quality",
            "primary_strategy": "multi_strategy",
            "trend_score": "11.5",
            "value_quality_score": "18.8",
            "trade_date": "2026-07-02",
            "report_period": "2026-03-31",
            "liquidity_score": "15.0",
            "liquidity_evidence": "股票池平均成交额 150000000",
            "reasons": "[trend_strength] 趋势强，近 2 日平均成交额 150000000。 | [value_quality] PE 分位 68.00 <= 80.00。",
            "risks": "[trend_strength] 追高风险。 | [value_quality] 高估风险。",
            **scoring_fields(
                combined_score="180.0",
                data_quality_score="10.0",
                data_quality_status="weak",
                risk_penalty_score="-22.0",
                risk_penalty_evidence="风险提示 2 条，命中高关注关键词：追高, 高估",
                liquidity_score="15.0",
                liquidity_evidence="股票池平均成交额 150000000",
            ),
        }

        result = check_candidates([candidate])

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any("数据质量状态为 weak" in item["message"] for item in result["warnings"]))
        self.assertTrue(any("风险扣分" in item["message"] for item in result["warnings"]))
        self.assertTrue(any("流动性评分" in item["message"] for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
