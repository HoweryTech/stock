import unittest

from tools.monitor_intraday_positions import analyze_quote, build_reduction_plan, build_reverse_t_plan, moving_averages, state_signature


class MonitorIntradayPositionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 9.115, "position_pct_of_total_assets": 5.3611},
        }

    def test_moving_averages(self) -> None:
        ma5, ma20 = moving_averages([float(value) for value in range(1, 21)])
        self.assertEqual(ma5, 18.0)
        self.assertEqual(ma20, 10.5)

    def test_analyzes_live_risk_state(self) -> None:
        quote = {
            "code": "000725",
            "name": "京东方A",
            "latest_price": 6.83,
            "change_pct": -10.0,
            "quote_timestamp": 1000,
        }
        item = analyze_quote(
            self.position,
            quote,
            [7.0] * 20,
            total_assets=25480,
            max_stale_seconds=60,
            now_timestamp=1003,
        )
        self.assertEqual(item["state"], "risk_review")
        self.assertAlmostEqual(item["position"]["unrealized_pnl"], -457.0)
        self.assertTrue(any(signal["code"] == "limit_down_or_near" for signal in item["signals"]))
        self.assertFalse(item["guardrails"]["t_trade_allowed"])

    def test_state_signature_ignores_price_only_changes(self) -> None:
        first = {"items": [{"code": "000725", "state": "observe", "signals": [], "quote": {"latest_price": 6.8}}]}
        second = {"items": [{"code": "000725", "state": "observe", "signals": [], "quote": {"latest_price": 6.9}}]}
        self.assertEqual(state_signature(first), state_signature(second))

    def test_reverse_t_candidate_near_intraday_high(self) -> None:
        position = {
            "stock": {"code": "000723"},
            "entry": {"shares": 1000, "entry_price": 3.843, "position_pct_of_total_assets": 12.7},
            "broker_import_snapshot": {"available_shares": 1000},
        }
        quote = {"latest_price": 3.29, "open": 3.24, "high": 3.31, "low": 3.23, "change_pct": 1.54}
        plan = build_reverse_t_plan(position, quote, stale=False)
        self.assertEqual(plan["status"], "candidate")
        self.assertEqual(plan["trade_shares"], 100)
        self.assertEqual(plan["buyback_max_price"], 3.25)
        self.assertTrue(plan["failure_as_reduction_acceptable"])

    def test_small_position_is_not_suitable_for_reverse_t(self) -> None:
        quote = {"latest_price": 6.8, "open": 6.7, "high": 6.9, "low": 6.6, "change_pct": 1.0}
        plan = build_reverse_t_plan(self.position, quote, stale=False)
        self.assertEqual(plan["status"], "not_suitable")
        self.assertTrue(any("少于300股" in blocker for blocker in plan["blockers"]))

    def test_reduction_plan_rounds_to_board_lots(self) -> None:
        position = {"entry": {"shares": 1000}}
        plan = build_reduction_plan(position, {"latest_price": 3.29}, total_assets=25480)
        self.assertEqual(plan["status"], "actionable")
        self.assertEqual(plan["minimum_reduction_shares"], 300)
        self.assertLessEqual(plan["post_reduction_position_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
