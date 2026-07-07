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

    def test_run_merge_writes_csv_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            trend_path = Path(tmp_dir) / "trend.csv"
            value_path = Path(tmp_dir) / "value.csv"
            output_path = Path(tmp_dir) / "pool.csv"
            metadata_path = Path(tmp_dir) / "pool.json"

            with trend_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "trade_date", "score", "reasons", "risks"])
                writer.writeheader()
                writer.writerow({"code": "300750", "trade_date": "2026-07-02", "score": "12.5", "reasons": "趋势强。", "risks": ""})
            with value_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "report_period", "score", "reasons", "risks"])
                writer.writeheader()
                writer.writerow({"code": "300750", "report_period": "2026-03-31", "score": "20.855", "reasons": "质量好。", "risks": ""})

            metadata = run_merge(trend_path, value_path, output_path, metadata_path)

            with output_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["candidate_count"], 1)
        self.assertEqual(metadata["multi_strategy_count"], 1)
        self.assertEqual(rows[0]["primary_strategy"], "multi_strategy")


if __name__ == "__main__":
    unittest.main()
