import unittest
from datetime import date, timedelta

from tools.monitor_intraday_positions import analyze_quote, build_reduction_plan, build_reverse_t_plan, moving_averages, multi_timeframe_metrics, state_signature, trade_costs


class MonitorIntradayPositionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 9.115, "position_pct_of_total_assets": 5.3611},
        }
        self.costs = {
            "commission_rate": 0.0003,
            "minimum_commission": 5.0,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
            "minimum_net_profit": 5.0,
            "verified": False,
        }

    def history(self, count: int = 260, close: float = 7.0) -> list[dict]:
        start = date(2025, 1, 1)
        return [{"trade_date": (start + timedelta(days=index)).isoformat(), "close": close} for index in range(count)]

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
            self.history(),
            total_assets=25480,
            max_stale_seconds=60,
            costs=self.costs,
            max_reverse_t_position_ratio_pct=50.0,
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
        plan = build_reverse_t_plan(
            position,
            quote,
            stale=False,
            costs=self.costs,
            timeframe={"alignment": "bearish"},
            preferred_reduction_shares=300,
        )
        self.assertEqual(plan["status"], "candidate")
        self.assertEqual(plan["trade_shares"], 200)
        self.assertEqual(plan["buyback_max_price"], 3.21)
        self.assertGreater(plan["required_gap_pct"], 2.0)
        self.assertGreaterEqual(plan["cost_estimate"]["net_profit"], 5.0)
        self.assertTrue(plan["failure_as_reduction_acceptable"])

    def test_small_position_is_not_suitable_for_reverse_t(self) -> None:
        quote = {"latest_price": 6.8, "open": 6.7, "high": 6.9, "low": 6.6, "change_pct": 1.0}
        plan = build_reverse_t_plan(self.position, quote, stale=False, costs=self.costs, timeframe={"alignment": "bearish"})
        self.assertEqual(plan["status"], "not_suitable")
        self.assertTrue(plan["high_position_ratio_warning"])

    def test_one_lot_position_cannot_keep_base_position(self) -> None:
        position = {"entry": {"shares": 100}}
        quote = {"latest_price": 5.5, "open": 5.4, "high": 5.6, "low": 5.3, "change_pct": 1.0}
        plan = build_reverse_t_plan(position, quote, stale=False, costs=self.costs, timeframe={"alignment": "mixed"})
        self.assertEqual(plan["status"], "not_suitable")
        self.assertTrue(any("无法保留底仓" in blocker for blocker in plan["blockers"]))

    def test_reduction_plan_rounds_to_board_lots(self) -> None:
        position = {"entry": {"shares": 1000}}
        plan = build_reduction_plan(position, {"latest_price": 3.29}, total_assets=25480)
        self.assertEqual(plan["status"], "actionable")
        self.assertEqual(plan["minimum_reduction_shares"], 300)
        self.assertLessEqual(plan["post_reduction_position_pct"], 10.0)

    def test_trade_costs_include_minimum_commissions_and_sell_tax(self) -> None:
        costs = trade_costs(3.29, 3.25, 100, self.costs)
        self.assertEqual(costs["sell_commission"], 5.0)
        self.assertEqual(costs["buy_commission"], 5.0)
        self.assertLess(costs["net_profit"], 0)

    def test_multi_timeframe_metrics(self) -> None:
        metrics = multi_timeframe_metrics(self.history(close=7.0), 7.5)
        self.assertGreaterEqual(metrics["weekly_closes_count"], 12)
        self.assertGreaterEqual(metrics["monthly_closes_count"], 6)
        self.assertEqual(metrics["alignment"], "bullish")


if __name__ == "__main__":
    unittest.main()
