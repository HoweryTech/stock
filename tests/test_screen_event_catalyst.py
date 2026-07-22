import csv
import tempfile
import unittest
from pathlib import Path

from tools.screen_event_catalyst import candidate_from_row, run_screen, screen_candidates


ROOT = Path(__file__).resolve().parents[1]


class ScreenEventCatalystTest(unittest.TestCase):
    def test_builds_candidate_with_reasons_and_risks(self) -> None:
        config = {
            "supported_event_types": ["major_order"],
            "min_impact_score": 3.0,
            "min_confidence": 2.0,
        }
        row = {
            "event_date": "2026-07-02",
            "code": "300750",
            "event_type": "major_order",
            "title": "签订重大订单",
            "expected_impact": "改善收入预期。",
            "impact_score": "4",
            "confidence": "3",
            "counter_evidence": "交付节奏需跟踪。",
            "risk_disclosure": "订单延期会削弱催化。",
        }

        candidate, exclusions = candidate_from_row(row, config)

        self.assertEqual(exclusions, [])
        assert candidate is not None
        self.assertEqual(candidate["code"], "300750")
        self.assertEqual(candidate["event_type"], "major_order")
        self.assertEqual(candidate["score"], 46.0)
        self.assertIn("预期影响", candidate["reasons"])
        self.assertIn("反证", candidate["risks"])

    def test_excludes_low_impact_and_risk_events(self) -> None:
        config = {
            "supported_event_types": ["shareholder_increase", "regulatory_inquiry"],
            "min_impact_score": 3.0,
            "min_confidence": 2.0,
        }
        candidates, exclusions = screen_candidates(
            [
                {
                    "event_date": "2026-07-02",
                    "code": "600000",
                    "event_type": "shareholder_increase",
                    "title": "增持",
                    "expected_impact": "改善风险偏好。",
                    "impact_score": "2",
                    "confidence": "2",
                    "counter_evidence": "金额有限。",
                    "risk_disclosure": "影响有限。",
                },
                {
                    "event_date": "2026-07-02",
                    "code": "000001",
                    "event_type": "regulatory_inquiry",
                    "title": "问询函",
                    "expected_impact": "需风险复核。",
                    "impact_score": "4",
                    "confidence": "3",
                    "counter_evidence": "等待回复。",
                    "risk_disclosure": "不确定性高。",
                },
            ],
            config,
        )

        self.assertEqual(candidates, [])
        self.assertEqual(len(exclusions), 2)

    def test_run_screen_writes_candidates_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "event_candidates.csv"
            metadata_output = Path(tmp_dir) / "event_candidates.json"

            metadata = run_screen(
                ROOT / "config/investment-profile.example.yaml",
                ROOT / "samples/event_catalyst_events.sample.csv",
                output,
                metadata_output,
            )
            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["candidate_count"], 1)
        self.assertEqual(metadata["excluded_count"], 2)
        self.assertEqual(rows[0]["code"], "300750")


if __name__ == "__main__":
    unittest.main()
