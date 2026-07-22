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
                        "latest_price",
                        "portfolio_fit_status",
                        "data_quality_status",
                        "technical_health_status",
                        "technical_health_score",
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
                        "latest_price": "55.2",
                        "portfolio_fit_status": "watch",
                        "data_quality_status": "partial",
                        "technical_health_status": "weak",
                        "technical_health_score": "-12",
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
                        "latest_price": "289.66",
                        "portfolio_fit_status": "ready_for_plan",
                        "data_quality_status": "complete",
                        "technical_health_status": "strong",
                        "technical_health_score": "18",
                    }
                )
                writer.writerow(
                    {
                        "code": "920438",
                        "name": "北交样例",
                        "exchange": "BSE",
                        "industry": "机械",
                        "strategies": "trend_strength",
                        "combined_score": "160",
                        "latest_price": "126.61",
                        "portfolio_fit_status": "watch",
                        "data_quality_status": "complete",
                        "technical_health_status": "weak",
                        "technical_health_score": "-6",
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
                        "technical_health_status": ["weak"],
                        "sort": ["technical_health_score"],
                        "direction": ["asc"],
                    }
                )
            finally:
                dashboard.CANDIDATE_POOL_FILE = old_candidate
                dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = old_portfolio

        self.assertEqual(result["filtered_count"], 1)
        self.assertEqual(result["items"][0]["code"], "688001")
        self.assertEqual(result["items"][0]["board"], "star")
        self.assertEqual(result["items"][0]["technical_health_status"], "weak")
        self.assertEqual(result["items"][0]["technical_health_score"], -12)
        self.assertEqual(result["items"][0]["latest_price"], 55.2)
        self.assertEqual(result["filters"]["board"]["bse"], 1)
        self.assertEqual(result["filters"]["board"]["chinext"], 1)
        self.assertEqual(result["filters"]["board"]["star"], 1)
        self.assertEqual(result["filters"]["technical_health_status"]["strong"], 1)
        self.assertEqual(result["filters"]["technical_health_status"]["weak"], 2)

    def test_excludes_multiple_boards_and_returns_all_matches_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            candidate_path = base / "candidate_pool.csv"
            portfolio_path = base / "missing_portfolio_fit.csv"
            with candidate_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "name", "exchange", "combined_score", "latest_price"])
                writer.writeheader()
                writer.writerow({"code": "688001", "name": "科创样例", "exchange": "SSE", "combined_score": "180", "latest_price": "55.2"})
                writer.writerow({"code": "920438", "name": "北交样例", "exchange": "BSE", "combined_score": "160", "latest_price": "126.61"})
                writer.writerow({"code": "300750", "name": "宁德时代", "exchange": "SZSE", "combined_score": "220", "latest_price": "289.66"})
                writer.writerow({"code": "600000", "name": "浦发银行", "exchange": "SSE", "combined_score": "120", "latest_price": "9.87"})

            old_candidate = dashboard.CANDIDATE_POOL_FILE
            old_portfolio = dashboard.CANDIDATE_PORTFOLIO_FIT_FILE
            dashboard.CANDIDATE_POOL_FILE = candidate_path
            dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = portfolio_path
            try:
                result = dashboard.filtered_candidates({"exclude_board": ["star", "bse", "chinext"]})
            finally:
                dashboard.CANDIDATE_POOL_FILE = old_candidate
                dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = old_portfolio

        self.assertEqual(result["filtered_count"], 1)
        self.assertEqual([item["code"] for item in result["items"]], ["600000"])
        self.assertEqual(result["sort"]["key"], "combined_score")
        self.assertEqual(result["sort"]["direction"], "desc")

    def test_sorts_by_latest_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            candidate_path = base / "candidate_pool.csv"
            portfolio_path = base / "missing_portfolio_fit.csv"
            with candidate_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "name", "exchange", "combined_score", "latest_price"])
                writer.writeheader()
                writer.writerow({"code": "600000", "name": "浦发银行", "exchange": "SSE", "combined_score": "120", "latest_price": "9.87"})
                writer.writerow({"code": "002396", "name": "星网锐捷", "exchange": "SZSE", "combined_score": "160", "latest_price": "28.35"})

            old_candidate = dashboard.CANDIDATE_POOL_FILE
            old_portfolio = dashboard.CANDIDATE_PORTFOLIO_FIT_FILE
            dashboard.CANDIDATE_POOL_FILE = candidate_path
            dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = portfolio_path
            try:
                result = dashboard.filtered_candidates({"sort": ["latest_price"], "direction": ["asc"]})
            finally:
                dashboard.CANDIDATE_POOL_FILE = old_candidate
                dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = old_portfolio

        self.assertEqual([item["code"] for item in result["items"]], ["600000", "002396"])

    def test_supports_multi_sort_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            candidate_path = base / "candidate_pool.csv"
            portfolio_path = base / "missing_portfolio_fit.csv"
            with candidate_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "name", "exchange", "combined_score", "latest_price"])
                writer.writeheader()
                writer.writerow({"code": "600000", "name": "浦发银行", "exchange": "SSE", "combined_score": "160", "latest_price": "9.87"})
                writer.writerow({"code": "002396", "name": "星网锐捷", "exchange": "SZSE", "combined_score": "160", "latest_price": "28.35"})
                writer.writerow({"code": "603118", "name": "共进股份", "exchange": "SSE", "combined_score": "150", "latest_price": "6.25"})

            old_candidate = dashboard.CANDIDATE_POOL_FILE
            old_portfolio = dashboard.CANDIDATE_PORTFOLIO_FIT_FILE
            dashboard.CANDIDATE_POOL_FILE = candidate_path
            dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = portfolio_path
            try:
                result = dashboard.filtered_candidates({"sort": ["combined_score:desc,latest_price:asc"]})
            finally:
                dashboard.CANDIDATE_POOL_FILE = old_candidate
                dashboard.CANDIDATE_PORTFOLIO_FIT_FILE = old_portfolio

        self.assertEqual([item["code"] for item in result["items"]], ["600000", "002396", "603118"])
        self.assertEqual(
            result["sort"]["items"],
            [{"key": "combined_score", "direction": "desc"}, {"key": "latest_price", "direction": "asc"}],
        )


if __name__ == "__main__":
    unittest.main()
