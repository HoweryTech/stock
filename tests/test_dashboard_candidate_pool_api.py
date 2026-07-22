import csv
import tempfile
import unittest
from pathlib import Path

import tools.serve_monitor_dashboard as dashboard


class DashboardCandidatePoolApiTest(unittest.TestCase):
    def test_filters_and_sorts_candidate_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            candidate_path = base / "candidate_pool.csv"
            portfolio_path = base / "missing_portfolio_fit.csv"
            with candidate_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "code",
                        "name",
                        "exchange",
                        "industry",
                        "strategies",
                        "combined_score",
                        "portfolio_fit_status",
                        "data_quality_status",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "code": "688001",
                        "name": "科创样例",
                        "exchange": "SSE",
                        "industry": "医药",
                        "strategies": "event_catalyst",
                        "combined_score": "180",
                        "portfolio_fit_status": "watch",
                        "data_quality_status": "partial",
                    }
                )
                writer.writerow(
                    {
                        "code": "300750",
                        "name": "宁德时代",
                        "exchange": "SZSE",
                        "industry": "电力设备",
                        "strategies": "trend_strength|value_quality",
                        "combined_score": "220",
                        "portfolio_fit_status": "ready_for_plan",
                        "data_quality_status": "complete",
                    }
                )

            old_candidate = dashboard.CANDIDATE_POOL_FILE
            old_portfolio = dashboard.CANDIDATE_PORTFOLIO_FIT_FILE
            dashboard.CANDIDATE_POOL_FILE = candidate_path
            dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = portfolio_path
            try:
                result = dashboard.filtered_candidates(
                    {
                        "board": ["star"],
                        "sort": ["combined_score"],
                        "direction": ["desc"],
                    }
                )
            finally:
                dashboard.CANDIDATE_POOL_FILE = old_candidate
                dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = old_portfolio

        self.assertEqual(result["filtered_count"], 1)
        self.assertEqual(result["items"][0]["code"], "688001")
        self.assertEqual(result["items"][0]["board"], "star")
        self.assertEqual(result["filters"]["board"]["chinext"], 1)
        self.assertEqual(result["filters"]["board"]["star"], 1)


if __name__ == "__main__":
    unittest.main()
