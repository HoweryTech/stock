import unittest
from datetime import date, timedelta

from tools.monitor_intraday_positions import analyze_quote, build_action_decision, build_execution_quality_summary, build_positive_t_plan, build_reduction_plan, build_reverse_t_plan, build_t_closure_performance, dynamic_price_zone_width, moving_averages, multi_timeframe_metrics, state_signature, trade_costs


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

    def test_state_signature_tracks_reverse_t_price_alert(self) -> None:
        first = {"items": [{"code": "000725", "state": "observe", "signals": [], "reverse_t_plan": {"price_in_sell_zone": False}}]}
        second = {"items": [{"code": "000725", "state": "observe", "signals": [], "reverse_t_plan": {"price_in_sell_zone": True}}]}
        self.assertNotEqual(state_signature(first), state_signature(second))

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
        self.assertEqual(plan["sell_zone"], [3.3, 3.31])
        self.assertEqual(plan["buyback_max_price"], 3.22)
        self.assertGreater(plan["required_gap_pct"], 2.0)
        self.assertGreaterEqual(plan["cost_estimate"]["net_profit"], 5.0)
        self.assertTrue(plan["failure_as_reduction_acceptable"])
        self.assertEqual(plan["blocker_details"], [])
        self.assertIn("可以进入反T人工候选", plan["next_action"])
        self.assertTrue(any("第1步" in step and "3.30-3.31" in step for step in plan["execution_steps"]))
        self.assertTrue(any("3.22" in step and "买回同等" in step for step in plan["execution_steps"]))
        self.assertTrue(any("当天最多执行一轮" in step for step in plan["execution_steps"]))

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
        self.assertTrue(any(blocker["code"] == "base_position_insufficient" for blocker in plan["blocker_details"]))
        self.assertIn("当前不执行反T", plan["next_action"])
        self.assertTrue(any("当前不要卖出" in step for step in plan["execution_steps"]))

    def test_blocked_reverse_t_still_exposes_fee_aware_reference_range(self) -> None:
        quote = {"latest_price": 13.66, "open": 12.96, "high": 13.68, "low": 12.7, "change_pct": 4.59}
        plan = build_reverse_t_plan(self.position, quote, stale=False, costs=self.costs, timeframe={"alignment": "insufficient"})
        self.assertEqual(plan["status"], "not_suitable")
        self.assertEqual(plan["sell_zone"], [13.65, 13.68])
        self.assertIsNotNone(plan["buyback_max_price"])
        self.assertGreaterEqual(plan["cost_estimate"]["net_profit"], 5.0)
        self.assertTrue(any(blocker["code"] == "timeframe_insufficient" for blocker in plan["blocker_details"]))

    def test_dynamic_price_zone_width_scales_with_anchor_price(self) -> None:
        self.assertEqual(dynamic_price_zone_width(3.31), 0.01)
        self.assertEqual(dynamic_price_zone_width(13.68), 0.03)

    def test_fee_blocked_reverse_t_explains_missing_buyback(self) -> None:
        quote = {"latest_price": 6.32, "open": 6.2, "high": 6.36, "low": 6.1, "change_pct": 1.5}
        expensive_costs = dict(self.costs)
        expensive_costs["minimum_net_profit"] = 100.0
        plan = build_reverse_t_plan(
            self.position,
            quote,
            stale=False,
            costs=expensive_costs,
            timeframe={"alignment": "mixed"},
            max_trade_ratio_pct=50.0,
        )

        self.assertEqual(plan["status"], "fee_blocked")
        self.assertIsNone(plan["buyback_max_price"])
        self.assertTrue(any(blocker["code"] == "fee_not_viable" for blocker in plan["blocker_details"]))
        self.assertIn("费用模型", plan["next_action"])
        self.assertTrue(any("不要提前挂反T回补单" in step for step in plan["execution_steps"]))

    def test_open_reverse_t_leg_triggers_buyback_ready(self) -> None:
        position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 100, "entry_price": 9.115, "position_pct_of_total_assets": 2.48},
            "manual_trade_history": [
                {
                    "id": "MANUAL-OPEN-000725",
                    "side": "sell",
                    "trade_intent": "reverse_t_open",
                    "price": 6.32,
                    "shares": 100,
                    "occurred_at": "2026-07-16T09:30:00+08:00",
                }
            ],
        }
        quote = {"latest_price": 6.04, "open": 6.25, "high": 6.39, "low": 6.02, "change_pct": -5.49}

        plan = build_reverse_t_plan(position, quote, stale=False, costs=self.costs, timeframe={"alignment": "bearish"})

        self.assertEqual(plan["status"], "buyback_ready")
        self.assertEqual(plan["trade_shares"], 100)
        self.assertLessEqual(6.04, plan["buyback_max_price"])
        self.assertEqual(plan["open_reverse_t_leg"]["id"], "MANUAL-OPEN-000725")
        self.assertTrue(any("买回" in step for step in plan["execution_steps"]))

    def test_analyze_quote_exposes_latest_reverse_t_closure(self) -> None:
        closure = {
            "status": "closed_profitable",
            "sell_trade_id": "MANUAL-OPEN-000725",
            "buy_trade_id": "MANUAL-CLOSE-000725",
            "sell_price": 6.32,
            "buy_price": 6.04,
            "shares": 100.0,
            "net_profit": 17.6717,
            "cost_reduction_per_remaining_share": 0.0884,
            "next_plan": "闭环完成。",
        }
        position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 7.6025, "position_pct_of_total_assets": 4.72},
            "tracking": {"latest_reverse_t_closure": closure},
        }
        quote = {"code": "000725", "latest_price": 6.04, "change_pct": -5.49, "quote_timestamp": 1000}

        item = analyze_quote(
            position, quote, self.history(), total_assets=25480, max_stale_seconds=60,
            costs=self.costs, max_reverse_t_position_ratio_pct=50, now_timestamp=1001,
        )

        self.assertEqual(item["latest_reverse_t_closure"]["buy_trade_id"], "MANUAL-CLOSE-000725")
        self.assertEqual(item["latest_reverse_t_closure"]["status"], "closed_profitable")

    def test_analyze_quote_exposes_latest_positive_t_closure(self) -> None:
        closure = {
            "status": "closed_profitable",
            "buy_trade_id": "MANUAL-POSITIVE-OPEN",
            "sell_trade_id": "MANUAL-POSITIVE-CLOSE",
            "buy_price": 6.1,
            "sell_price": 6.25,
            "shares": 100.0,
            "net_profit": 4.67,
            "next_plan": "闭环完成。",
        }
        position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 7.6025, "position_pct_of_total_assets": 4.72},
            "tracking": {"latest_positive_t_closure": closure},
        }
        quote = {"code": "000725", "latest_price": 6.25, "change_pct": 1.2, "quote_timestamp": 1000}

        item = analyze_quote(
            position, quote, self.history(), total_assets=25480, max_stale_seconds=60,
            costs=self.costs, max_reverse_t_position_ratio_pct=50, now_timestamp=1001,
        )

        self.assertEqual(item["latest_positive_t_closure"]["sell_trade_id"], "MANUAL-POSITIVE-CLOSE")
        self.assertEqual(item["latest_positive_t_closure"]["status"], "closed_profitable")

    def test_t_closure_performance_summarizes_manual_closures(self) -> None:
        position = {
            "manual_trade_history": [
                {
                    "id": "MANUAL-REVERSE-CLOSE",
                    "side": "buy",
                    "trade_intent": "reverse_t_close",
                    "linked_trade_id": "MANUAL-REVERSE-OPEN",
                    "occurred_at": "2026-07-16T14:40:00+08:00",
                    "reverse_t_closure": {
                        "status": "closed_profitable",
                        "sell_trade_id": "MANUAL-REVERSE-OPEN",
                        "buy_trade_id": "MANUAL-REVERSE-CLOSE",
                        "sell_price": 6.32,
                        "buy_price": 6.04,
                        "shares": 100,
                        "gross_profit": 28.0,
                        "fees": {"total_fees": 10.3283},
                        "net_profit": 17.6717,
                    },
                },
                {
                    "id": "MANUAL-POSITIVE-CLOSE",
                    "side": "sell",
                    "trade_intent": "positive_t_close",
                    "linked_trade_id": "MANUAL-POSITIVE-OPEN",
                    "occurred_at": "2026-07-17T10:18:00+08:00",
                    "positive_t_closure": {
                        "status": "closed_loss",
                        "buy_trade_id": "MANUAL-POSITIVE-OPEN",
                        "sell_trade_id": "MANUAL-POSITIVE-CLOSE",
                        "buy_price": 6.10,
                        "sell_price": 6.18,
                        "shares": 100,
                        "gross_profit": 8.0,
                        "fees": {"total_fees": 10.3213},
                        "net_profit": -2.3213,
                    },
                },
            ],
        }

        performance = build_t_closure_performance(position)

        self.assertEqual(performance["total_count"], 2)
        self.assertEqual(performance["profitable_count"], 1)
        self.assertEqual(performance["loss_count"], 1)
        self.assertEqual(performance["win_rate_pct"], 50.0)
        self.assertAlmostEqual(performance["total_net_profit"], 15.3504, places=4)
        self.assertAlmostEqual(performance["average_net_profit"], 7.6752, places=4)
        self.assertEqual(performance["reverse_t_count"], 1)
        self.assertEqual(performance["positive_t_count"], 1)
        self.assertEqual(performance["status"], "profitable")
        self.assertEqual(performance["recent_closures"][-1]["type"], "positive_t")

    def test_analyze_quote_exposes_t_closure_performance(self) -> None:
        position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 7.6025, "position_pct_of_total_assets": 4.72},
            "manual_trade_history": [
                {
                    "id": "MANUAL-REVERSE-CLOSE",
                    "side": "buy",
                    "trade_intent": "reverse_t_close",
                    "linked_trade_id": "MANUAL-REVERSE-OPEN",
                    "reverse_t_closure": {
                        "status": "closed_profitable",
                        "sell_trade_id": "MANUAL-REVERSE-OPEN",
                        "buy_trade_id": "MANUAL-REVERSE-CLOSE",
                        "sell_price": 6.32,
                        "buy_price": 6.04,
                        "shares": 100,
                        "gross_profit": 28.0,
                        "fees": {"total_fees": 10.3283},
                        "net_profit": 17.6717,
                    },
                },
            ],
        }
        quote = {"code": "000725", "latest_price": 6.25, "change_pct": 1.2, "quote_timestamp": 1000}

        item = analyze_quote(
            position, quote, self.history(), total_assets=25480, max_stale_seconds=60,
            costs=self.costs, max_reverse_t_position_ratio_pct=50, now_timestamp=1001,
        )

        self.assertEqual(item["t_closure_performance"]["total_count"], 1)
        self.assertEqual(item["t_closure_performance"]["status"], "profitable")
        self.assertAlmostEqual(item["t_closure_performance"]["total_net_profit"], 17.6717, places=4)

    def test_execution_quality_summary_blocks_recent_failed_review(self) -> None:
        position = {
            "manual_trade_history": [
                {
                    "id": "MANUAL-LOSS",
                    "side": "sell",
                    "occurred_at": "2026-07-17T10:00:00+08:00",
                    "execution_quality_review": {
                        "status": "failed",
                        "status_label": "执行失败复盘",
                        "score": 45,
                        "next_action": "暂停同类操作。",
                    },
                }
            ]
        }

        summary = build_execution_quality_summary(position)

        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["latest_review"]["trade_id"], "MANUAL-LOSS")

    def test_analyze_quote_exposes_execution_quality_summary(self) -> None:
        position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 200, "entry_price": 7.6025, "position_pct_of_total_assets": 4.72},
            "tracking": {"latest_execution_quality_review": {"status": "good", "score": 90}},
            "manual_trade_history": [
                {
                    "id": "MANUAL-GOOD",
                    "side": "buy",
                    "execution_quality_review": {"status": "good", "status_label": "执行质量良好", "score": 90},
                }
            ],
        }
        quote = {"code": "000725", "latest_price": 6.25, "change_pct": 1.2, "quote_timestamp": 1000}

        item = analyze_quote(
            position, quote, self.history(), total_assets=25480, max_stale_seconds=60,
            costs=self.costs, max_reverse_t_position_ratio_pct=50, now_timestamp=1001,
        )

        self.assertEqual(item["latest_execution_quality_review"]["status"], "good")
        self.assertEqual(item["execution_quality_summary"]["status"], "pass")
        self.assertEqual(item["execution_quality_summary"]["average_score"], 90.0)

    def test_open_positive_t_leg_triggers_target_sell_ready(self) -> None:
        position = {
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"shares": 300, "entry_price": 8.13, "position_pct_of_total_assets": 7.18},
            "risk": {"stop_loss_price": 5.9},
            "manual_trade_history": [
                {
                    "id": "MANUAL-POSITIVE-OPEN-000725",
                    "side": "buy",
                    "trade_intent": "positive_t_open",
                    "price": 6.10,
                    "shares": 100,
                    "occurred_at": "2026-07-16T10:00:00+08:00",
                    "fees": {"total_fees": 5.0},
                }
            ],
        }
        quote = {"latest_price": 6.18, "quote_timestamp": 1000}

        plan = build_positive_t_plan(position, quote, stale=False, costs=self.costs)

        self.assertEqual(plan["status"], "target_sell_ready")
        self.assertEqual(plan["trade_shares"], 100)
        self.assertEqual(plan["target_sell_zone"], [6.17, 6.19])
        self.assertEqual(plan["open_positive_t_leg"]["id"], "MANUAL-POSITIVE-OPEN-000725")
        self.assertTrue(any("卖出新增的 100 股" in step for step in plan["execution_steps"]))

    def test_open_positive_t_leg_triggers_failure_review(self) -> None:
        position = {
            "entry": {"shares": 300, "entry_price": 8.13},
            "risk": {"stop_loss_price": 5.95},
            "manual_trade_history": [
                {"id": "MANUAL-POSITIVE-OPEN", "side": "buy", "trade_intent": "positive_t_open", "price": 6.10, "shares": 100}
            ],
        }
        quote = {"latest_price": 5.94}

        plan = build_positive_t_plan(position, quote, stale=False, costs=self.costs)

        self.assertEqual(plan["status"], "failure_review")
        self.assertEqual(plan["failure_price"], 5.95)
        self.assertIn("不要继续补仓", plan["next_action"])

    def test_closed_positive_t_leg_is_not_reopened(self) -> None:
        position = {
            "entry": {"shares": 200, "entry_price": 8.13},
            "manual_trade_history": [
                {"id": "MANUAL-POSITIVE-OPEN", "side": "buy", "trade_intent": "positive_t_open", "price": 6.10, "shares": 100},
                {"id": "MANUAL-POSITIVE-CLOSE", "side": "sell", "trade_intent": "positive_t_close", "linked_trade_id": "MANUAL-POSITIVE-OPEN", "price": 6.18, "shares": 100},
            ],
        }

        plan = build_positive_t_plan(position, {"latest_price": 6.2}, stale=False, costs=self.costs)

        self.assertEqual(plan["status"], "not_applicable")

    def test_reduction_plan_rounds_to_board_lots(self) -> None:
        position = {"entry": {"shares": 1000}}
        plan = build_reduction_plan(position, {"latest_price": 3.29}, total_assets=25480)
        self.assertEqual(plan["status"], "actionable")
        self.assertEqual(plan["minimum_reduction_shares"], 300)
        self.assertLessEqual(plan["post_reduction_position_pct"], 10.0)

    def test_reduction_plan_explains_cash_and_realized_loss(self) -> None:
        position = {"entry": {"shares": 200, "entry_price": 21.41}}
        plan = build_reduction_plan(position, {"latest_price": 13.57}, total_assets=25480, costs=self.costs)
        self.assertEqual(plan["minimum_reduction_shares"], 100)
        self.assertAlmostEqual(plan["estimated_net_proceeds"], 1351.31, places=2)
        self.assertAlmostEqual(plan["estimated_realized_pnl_after_fees"], -789.69, places=2)
        self.assertIn("降低单票风险", plan["objective"])
        self.assertTrue(any("卖出后不回补" in step for step in plan["steps"]))
        self.assertFalse(plan["position_limit_verified"])

    def test_reduction_plan_can_mark_position_limit_verified(self) -> None:
        position = {"entry": {"shares": 1000, "entry_price": 3.8}}
        plan = build_reduction_plan(position, {"latest_price": 3.4}, total_assets=25480, costs=self.costs, position_limit_verified=True)
        self.assertTrue(plan["position_limit_verified"])

    def test_action_decision_is_explicit_when_history_is_insufficient(self) -> None:
        reverse = {
            "status": "not_suitable", "trade_shares": 100, "sell_zone": [13.66, 13.68],
            "buyback_max_price": 13.49, "cost_estimate": {"net_profit": 6.29},
            "blockers": ["周线或月线历史不足，无法完成多周期验证。"],
            "failure_result": "未回补可计入计划降仓。",
        }
        reduction = {"status": "granularity_review", "minimum_reduction_shares": 100}
        decision = build_action_decision(reverse, reduction)
        self.assertEqual(decision["verdict"], "do_not_execute_now")
        self.assertEqual(decision["headline"], "现在不做反T")
        self.assertEqual(decision["action_tier"], "observe_only")
        self.assertEqual(decision["action_tier_label"], "只观察")
        self.assertIn("不因轻微超限", decision["reduction_decision"])

    def test_action_decision_marks_reverse_t_buyback_priority(self) -> None:
        reverse = {
            "status": "buyback_ready", "trade_shares": 100, "sell_zone": [6.32, 6.32],
            "buyback_max_price": 6.16, "cost_estimate": {"net_profit": 5.67},
            "failure_result": "开放反T腿等待回补。",
        }
        reduction = {"status": "within_limit"}
        decision = build_action_decision(reverse, reduction)

        self.assertEqual(decision["verdict"], "buyback_ready")
        self.assertEqual(decision["action_tier"], "reverse_buyback_first")
        self.assertEqual(decision["action_tier_label"], "反T回补优先")

    def test_position_warning_does_not_trigger_hard_limit(self) -> None:
        position = {
            "stock": {"code": "000723", "name": "美锦能源"},
            "entry": {"shares": 1000, "entry_price": 3.843, "position_pct_of_total_assets": 13.2},
        }
        quote = {"code": "000723", "latest_price": 3.36, "open": 3.24, "high": 3.37, "low": 3.23, "change_pct": 3.7, "quote_timestamp": 1000}
        item = analyze_quote(
            position, quote, self.history(), total_assets=25480, max_stale_seconds=60,
            costs=self.costs, max_reverse_t_position_ratio_pct=50, now_timestamp=1001,
            max_position_pct=15, warning_position_pct=12, position_limit_verified=True,
        )
        codes = {signal["code"] for signal in item["signals"]}
        self.assertIn("position_near_limit", codes)
        self.assertNotIn("position_limit_exceeded", codes)
        self.assertEqual(item["reduction_plan"]["status"], "within_limit")

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
