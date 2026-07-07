import tempfile
import unittest
from pathlib import Path

from tools.run_watchlist_pipeline import resolve_windows, run_pipeline
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class RunWatchlistPipelineTest(unittest.TestCase):
    def test_resolve_windows_uses_profile_screening_window(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        self.assertEqual(resolve_windows(profile, None), [2])
        self.assertEqual(resolve_windows(profile, "5,2"), [2, 5])

    def test_run_pipeline_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            metadata = run_pipeline(
                ROOT / "config/investment-profile.example.yaml",
                ROOT / "samples/daily_bars.sample.csv",
                ROOT / "samples/financial_metrics.sample.csv",
                None,
                None,
                base / "trend_factors.csv",
                base / "trend_factors.json",
                base / "trend_candidates.csv",
                base / "trend_candidates.json",
                base / "value_quality_candidates.csv",
                base / "value_quality_candidates.json",
                base / "candidate_pool.csv",
                base / "candidate_pool.json",
                base / "watchlist.md",
                base / "pipeline.json",
            )

            report = (base / "watchlist.md").read_text(encoding="utf-8")

        self.assertEqual(metadata["windows"], [2])
        self.assertEqual(metadata["steps"]["trend_factors"]["row_count"], 3)
        self.assertEqual(metadata["steps"]["trend_candidates"]["candidate_count"], 3)
        self.assertEqual(metadata["steps"]["value_quality_candidates"]["candidate_count"], 1)
        self.assertEqual(metadata["steps"]["candidate_pool"]["candidate_count"], 3)
        self.assertIn("主策略：多策略共振", report)


if __name__ == "__main__":
    unittest.main()
