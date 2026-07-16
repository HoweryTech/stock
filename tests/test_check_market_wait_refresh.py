import unittest
from datetime import datetime

from tools.check_market_wait_refresh import build_refresh_check


def cards(*states: str) -> dict:
    return {
        "cards": [
            {"code": f"60000{index}", "name": f"测试{index}", "state": state, "state_label": state}
            for index, state in enumerate(states)
        ]
    }


class MarketWaitRefreshTest(unittest.TestCase):
    def test_waits_before_market_opens(self) -> None:
        report = build_refresh_check(
            cards("market_wait", "exit_risk_review"),
            as_of=datetime(2026, 7, 16, 8, 55, 0),
            positions=["positions/*.yaml"],
            daily_bars="data/processed/daily_bars.csv",
            total_assets=25480.0,
        )

        self.assertEqual(report["conclusion"], "wait_for_market")
        self.assertFalse(report["action_required"])
        self.assertEqual(report["market_wait_count"], 1)

    def test_refresh_due_during_trading_session(self) -> None:
        report = build_refresh_check(
            cards("market_wait", "market_wait"),
            as_of=datetime(2026, 7, 16, 9, 35, 0),
            positions=["positions/*.yaml"],
            daily_bars="data/processed/daily_bars.csv",
            total_assets=25480.0,
            python_bin=".venv/bin/python",
        )

        self.assertEqual(report["conclusion"], "refresh_due")
        self.assertTrue(report["action_required"])
        self.assertEqual(report["market_wait_count"], 2)
        self.assertTrue(report["refresh_command"]["ready"])
        self.assertIn("--total-assets 25480.0", report["refresh_command"]["shell"])

    def test_refresh_due_requires_total_assets(self) -> None:
        report = build_refresh_check(
            cards("market_wait"),
            as_of=datetime(2026, 7, 16, 10, 0, 0),
            positions=["positions/*.yaml"],
            daily_bars="data/processed/daily_bars.csv",
        )

        self.assertEqual(report["conclusion"], "refresh_due_missing_total_assets")
        self.assertTrue(report["action_required"])
        self.assertFalse(report["refresh_command"]["ready"])
        self.assertIn("<TOTAL_ASSETS>", report["refresh_command"]["shell"])

    def test_no_market_wait_needs_no_action(self) -> None:
        report = build_refresh_check(
            cards("exit_risk_review", "observe"),
            as_of=datetime(2026, 7, 16, 9, 35, 0),
            positions=["positions/*.yaml"],
            daily_bars="data/processed/daily_bars.csv",
            total_assets=25480.0,
        )

        self.assertEqual(report["conclusion"], "no_market_wait")
        self.assertFalse(report["action_required"])


if __name__ == "__main__":
    unittest.main()
