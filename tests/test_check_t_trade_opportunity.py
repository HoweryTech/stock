import copy
import csv
import tempfile
import unittest
from pathlib import Path

from tools.check_t_trade_opportunity import check_t_opportunity, read_bars
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class CheckTTradeOpportunityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        self.position = load_yaml(ROOT / "templates/position.example.yaml")
        self.position["stock"]["code"] = "600000"
        self.position["stock"]["name"] = "浦发银行"
        self.position["entry"]["position_pct_of_total_assets"] = 5.0
        self.position["risk"]["stop_loss_price"] = 9.0
        self.position["tracking"]["current_price"] = 10.0

    def write_bars(self, closes: list[float], code: str = "600000") -> Path:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / "daily_bars.csv"
        fields = [
            "trade_date",
            "code",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "volume",
            "turnover",
            "turnover_rate",
            "is_limit_up",
            "is_limit_down",
            "is_suspended",
            "adjust_type",
            "data_source",
            "updated_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            previous = closes[0]
            for index, close in enumerate(closes, start=1):
                writer.writerow(
                    {
                        "trade_date": f"2026-07-{index:02d}",
                        "code": code,
                        "open": round(previous, 2),
                        "high": round(close * 1.02, 2),
                        "low": round(close * 0.98, 2),
                        "close": round(close, 2),
                        "pre_close": round(previous, 2),
                        "volume": 100000000,
                        "turnover": 1000000000,
                        "turnover_rate": 1.0,
                        "is_limit_up": "false",
                        "is_limit_down": "false",
                        "is_suspended": "false",
                        "adjust_type": "qfq",
                        "data_source": "test",
                        "updated_at": "2026-07-20",
                    }
                )
                previous = close
        return path

    def test_positive_t_candidate_after_pullback_in_intact_trend(self) -> None:
        closes = [
            10.0,
            10.1,
            10.2,
            10.3,
            10.4,
            10.5,
            10.6,
            10.7,
            10.8,
            10.9,
            11.0,
            11.1,
            11.2,
            11.3,
            11.4,
            11.2,
            11.0,
            10.85,
            10.75,
            10.85,
        ]
        bars = read_bars(self.write_bars(closes), "600000")

        result = check_t_opportunity(self.profile, self.position, bars)

        self.assertEqual(result["conclusion"], "positive_t_candidate")
        self.assertTrue(any(item["code"] == "positive_t_setup" for item in result["positive_t_evidence"]))

    def test_reverse_t_candidate_when_short_term_overextended(self) -> None:
        closes = [
            10.0,
            10.05,
            10.1,
            10.15,
            10.2,
            10.25,
            10.3,
            10.35,
            10.4,
            10.45,
            10.5,
            10.55,
            10.6,
            10.65,
            10.7,
            10.9,
            11.1,
            11.3,
            11.6,
            12.0,
        ]
        bars = read_bars(self.write_bars(closes), "600000")

        result = check_t_opportunity(self.profile, self.position, bars)

        self.assertEqual(result["conclusion"], "reverse_t_candidate")
        self.assertTrue(any(item["code"] == "reverse_t_setup" for item in result["reverse_t_evidence"]))

    def test_blocks_when_latest_close_is_near_stop_loss(self) -> None:
        position = copy.deepcopy(self.position)
        position["risk"]["stop_loss_price"] = 10.5
        closes = [
            10.0,
            10.1,
            10.2,
            10.3,
            10.4,
            10.5,
            10.6,
            10.7,
            10.8,
            10.9,
            11.0,
            11.1,
            11.2,
            11.3,
            11.4,
            11.2,
            11.0,
            10.85,
            10.75,
            10.7,
        ]
        bars = read_bars(self.write_bars(closes), "600000")

        result = check_t_opportunity(self.profile, position, bars)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "near_stop_loss" for item in result["blockers"]))

    def test_new_listing_limited_history_still_produces_analysis(self) -> None:
        position = copy.deepcopy(self.position)
        position["stock"]["code"] = "001248"
        position["risk"]["stop_loss_price"] = None
        closes = [13.8, 13.6, 13.4, 13.2, 13.0, 12.8, 13.1, 13.3, 13.0, 12.9, 12.7]

        result = check_t_opportunity(self.profile, position, read_bars(self.write_bars(closes, "001248"), "001248"))
        warning_codes = {item["code"] for item in result["warnings"]}
        blocker_codes = {item["code"] for item in result["blockers"]}

        self.assertIn("limited_history_new_listing", warning_codes)
        self.assertNotIn("insufficient_daily_bars", blocker_codes)
        self.assertTrue(result["calculations"]["limited_history_mode"])

    def test_unconfirmed_imported_stop_loss_is_warning_not_hard_blocker(self) -> None:
        position = copy.deepcopy(self.position)
        position["strategy"]["source"] = "imported_holding"
        position["risk"]["stop_loss_price"] = 10.5
        position["risk"]["observation_items"] = ["止损价采用“当前价下方5%”作为当前存量仓位风险边界，需在下一次人工复核中确认。"]
        closes = [
            10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9,
            11.0, 11.1, 11.2, 11.3, 11.4, 11.2, 11.0, 10.85, 10.75, 10.7,
        ]

        result = check_t_opportunity(self.profile, position, read_bars(self.write_bars(closes), "600000"))
        blocker_codes = {item["code"] for item in result["blockers"]}
        warning_codes = {item["code"] for item in result["warnings"]}

        self.assertNotIn("near_stop_loss", blocker_codes)
        self.assertIn("unconfirmed_stop_loss_reference", warning_codes)
        self.assertFalse(result["calculations"]["stop_loss_confirmed"])

    def test_uses_profile_near_stop_block_threshold(self) -> None:
        profile = copy.deepcopy(self.profile)
        profile["t_trading"] = {"near_stop_block_pct": 1.0}
        position = copy.deepcopy(self.position)
        position["risk"]["stop_loss_price"] = 10.5
        closes = [
            10.0,
            10.1,
            10.2,
            10.3,
            10.4,
            10.5,
            10.6,
            10.7,
            10.8,
            10.9,
            11.0,
            11.1,
            11.2,
            11.3,
            11.4,
            11.2,
            11.0,
            10.85,
            10.75,
            10.7,
        ]

        result = check_t_opportunity(profile, position, read_bars(self.write_bars(closes), "600000"))

        self.assertNotEqual(result["conclusion"], "blocked")
        self.assertEqual(result["calculations"]["near_stop_block_pct"], 1.0)

    def test_retains_market_setup_when_execution_is_blocked(self) -> None:
        position = copy.deepcopy(self.position)
        position["risk"]["stop_loss_price"] = None
        closes = [
            10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9,
            11.0, 11.1, 11.2, 11.3, 11.4, 11.2, 11.0, 10.85, 10.75, 10.85,
        ]

        result = check_t_opportunity(self.profile, position, read_bars(self.write_bars(closes), "600000"))

        self.assertEqual(result["conclusion"], "blocked")
        self.assertEqual(result["market_setup"], "positive_t_candidate")
        self.assertEqual(result["positive_t_evidence"], [])


if __name__ == "__main__":
    unittest.main()
