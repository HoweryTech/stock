import unittest
from datetime import datetime

from tools.build_realtime_decision_cards import build_report, build_technical_operation, build_technical_unlock_alert, render_markdown, technical_dimension_summary


def intraday_item(code: str = "600000", *, signals=None, reverse_status: str = "watch") -> dict:
    return {
        "code": code,
        "name": "浦发银行",
        "quote": {"latest_price": 10.0, "change_pct": 1.0, "quote_lag_seconds": 3.0},
        "position": {"shares": 1000, "entry_price": 9.5, "return_pct": 5.2632},
        "technicals": {"ma5": 9.9, "ma20": 9.7},
        "capital_flow": {"main_net_inflow_ratio_pct": 1.2},
        "signals": signals or [],
        "reverse_t_plan": {"status": reverse_status, "sell_zone": [10.2, 10.3], "buyback_max_price": 10.0},
        "reduction_plan": {"status": "within_limit"},
    }


def portfolio_result(code: str = "600000", *, actions=None, warnings=None) -> dict:
    return {
        "positions": [
            {
                "path": f"positions/POS-{code}.yaml",
                "result": {
                    "stock_code": code,
                    "stock_name": "浦发银行",
                    "conclusion": "warning" if warnings else "normal",
                    "actions": actions or [],
                    "warnings": warnings or [],
                    "calculations": {
                        "current_price": 10.0,
                        "stop_loss_price": 9.5,
                        "distance_to_stop_pct": 5.0,
                        "near_stop_warning_pct": 3.0,
                    },
                },
            }
        ]
    }


def t_result(code: str = "600000", *, conclusion: str = "watch_only", blockers=None, warnings=None) -> dict:
    return {
        "items": [
            {
                "path": f"positions/POS-{code}.yaml",
                "result": {
                    "stock_code": code,
                    "stock_name": "浦发银行",
                    "market_setup": "positive_t_candidate" if conclusion == "positive_t_candidate" else "no_clear_t_setup",
                    "conclusion": conclusion,
                    "blockers": blockers or [],
                    "warnings": warnings or [],
                    "calculations": {
                        "latest_close": 10.0,
                        "ma_short": 9.9,
                        "ma_mid": 9.7,
                        "near_stop_block_pct": 1.0,
                        "recent_high": 10.8,
                        "recent_low": 9.2,
                    },
                },
            }
        ]
    }


def bearish_technical_doc(code: str = "600000") -> dict:
    weak_period = {
        "bar_count": 60,
        "latest_trade_date": "2026-07-16",
        "close": 10.0,
        "macd": {"status": "ok", "dif": -0.2, "dea": -0.1, "histogram": -0.2},
        "boll": {"status": "ok", "middle": 11.0, "upper": 12.0, "lower": 10.0, "percent_b": 0.05, "width_pct": 18.0},
        "rsi": {"status": "ok", "rsi6": 20.0, "rsi14": 25.0},
        "kdj": {"status": "ok", "k": 15.0, "d": 30.0, "j": -5.0},
        "atr": {"status": "ok", "atr": 0.9, "atr_pct": 9.0},
        "volume": {"status": "ok", "latest_volume": 100.0, "avg_volume_5": 150.0, "avg_volume_20": 200.0, "volume_ratio_20": 0.5},
    }
    return {"items": [{"code": code, "periods": {"daily": weak_period, "weekly": weak_period, "monthly": weak_period}}]}


def bullish_technical_doc(code: str = "600000") -> dict:
    strong_period = {
        "bar_count": 60,
        "latest_trade_date": "2026-07-16",
        "close": 10.0,
        "macd": {"status": "ok", "dif": 0.2, "dea": 0.1, "histogram": 0.2},
        "boll": {"status": "ok", "middle": 9.8, "upper": 10.8, "lower": 8.8, "percent_b": 0.6, "width_pct": 20.0},
        "rsi": {"status": "ok", "rsi6": 58.0, "rsi14": 56.0},
        "kdj": {"status": "ok", "k": 65.0, "d": 55.0, "j": 85.0},
        "atr": {"status": "ok", "atr": 0.3, "atr_pct": 3.0},
        "volume": {"status": "ok", "latest_volume": 300.0, "avg_volume_5": 250.0, "avg_volume_20": 180.0, "volume_ratio_20": 1.67},
    }
    return {"items": [{"code": code, "periods": {"daily": strong_period, "weekly": strong_period, "monthly": strong_period}}]}


def slightly_bearish_technical_doc(code: str = "600000") -> dict:
    mild_weak_period = {
        "bar_count": 60,
        "latest_trade_date": "2026-07-16",
        "close": 10.0,
        "macd": {"status": "ok", "dif": -0.02, "dea": -0.01, "histogram": -0.02},
        "boll": {"status": "ok", "middle": 10.2, "upper": 11.0, "lower": 9.4, "percent_b": 0.4, "width_pct": 15.0},
        "rsi": {"status": "ok", "rsi6": 42.0, "rsi14": 42.0},
        "kdj": {"status": "ok", "k": 40.0, "d": 45.0, "j": 30.0},
        "atr": {"status": "ok", "atr": 0.3, "atr_pct": 3.0},
        "volume": {"status": "ok", "latest_volume": 120.0, "avg_volume_5": 130.0, "avg_volume_20": 160.0, "volume_ratio_20": 0.75},
    }
    return {"items": [{"code": code, "periods": {"daily": mild_weak_period, "weekly": mild_weak_period, "monthly": mild_weak_period}}]}


def positive_minute_bars(code: str = "600000") -> dict[str, list[dict]]:
    closes = [9.70, 9.74, 9.78, 9.82, 9.86, 9.90, 9.94, 9.98, 10.02, 10.06, 10.10, 10.08, 10.04, 10.02, 10.00, 9.98, 9.96, 9.98, 9.96, 10.02]
    bars = []
    for index, close in enumerate(closes):
        bars.append(
            {
                "timestamp": f"2026-07-16 10:{index:02d}",
                "code": code,
                "open": close - 0.01,
                "high": close + 0.02,
                "low": close - 0.02,
                "close": close,
                "volume": 1000 + index * 20,
            }
        )
    return {code: bars}


def unconfirmed_minute_bars(code: str = "600000") -> dict[str, list[dict]]:
    bars = positive_minute_bars(code)[code]
    adjusted = []
    for bar in bars:
        item = dict(bar)
        item["open"] = item["close"] + 0.01
        item["volume"] = 600
        adjusted.append(item)
    return {code: adjusted}


class RealtimeDecisionCardsTest(unittest.TestCase):
    def test_hard_t_blocker_takes_exit_risk_priority(self) -> None:
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 10.5
        report = build_report(
            {"items": [intraday_item()]},
            portfolio,
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "exit_risk_review")
        self.assertEqual(card["decision"]["action"], "create_exit_or_risk_review")
        self.assertEqual(card["decision"]["action_label"], "止损风险优先：不补仓、不做T")
        self.assertFalse(card["decision"]["execution_allowed"])
        self.assertIn("禁止做T", card["decision"]["next_step"])
        exit_actions = {row["action"]: row for row in card["price_action_table"]["rows"]}
        self.assertEqual(card["price_action_table"]["rows"][0]["action"], "止损/退出")
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "止损/退出")
        self.assertEqual(exit_actions["止损/退出"]["status"], "ready")
        self.assertEqual(exit_actions["止损/退出"]["operation"], "卖出风险仓位")
        self.assertEqual(exit_actions["做T阻断"]["status"], "blocked")
        self.assertIn("禁止买入、补仓、做T", card["decision"]["action_steps"][0])
        self.assertTrue(any("操作后果" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("仓位后果" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("交易/卖出" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("卖出数量输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("卖出价格输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("成交后的下一步计划" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("做T阻断价" in step for step in card["decision"]["action_steps"]))
        self.assertIn("距离止损不足1%。", card["blockers"])
        self.assertEqual(report["priority_queue"]["top_items"][0]["code"], "600000")
        self.assertEqual(report["priority_queue"]["top_items"][0]["category"], "risk_exit")
        self.assertEqual(report["priority_queue"]["top_items"][0]["urgency"], "high")

    def test_unconfirmed_imported_stop_reference_does_not_take_exit_priority(self) -> None:
        portfolio = portfolio_result()
        result = portfolio["positions"][0]["result"]
        result["warnings"] = [{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]
        result["calculations"]["stop_loss_price"] = 10.5
        result["calculations"]["stop_loss_confirmed"] = False
        report = build_report(
            {"items": [intraday_item()]},
            portfolio,
            t_result(warnings=[{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        card = report["cards"][0]
        actions = {row["action"]: row for row in card["price_action_table"]["rows"]}

        self.assertNotEqual(card["state"], "exit_risk_review")
        self.assertIn("止损复核", actions)
        self.assertNotIn("止损/退出", actions)
        self.assertEqual(actions["止损复核"]["status_label"], "未确认")

    def test_positive_t_candidate_is_watch_only(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            {"items": [{"path": "positions/POS-600000.yaml", "stock": {"code": "600000"}, "weak_rule_count": 0}]},
            None,
            None,
            None,
            None,
            positive_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 31, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "positive_t_watch")
        self.assertEqual(card["decision"]["action"], "watch_positive_t_only")
        self.assertFalse(card["decision"]["execution_allowed"])
        self.assertEqual(card["price_levels"]["near_stop_block_price"], 9.596)
        self.assertEqual(card["capital_plan"]["status"], "watch")
        self.assertEqual(card["positive_timing"]["status"], "confirmed")
        self.assertGreaterEqual(card["positive_timing"]["score"], 65.0)
        self.assertEqual(card["positive_timing"]["blockers"], [])
        self.assertIn("可进入正T买入观察区", card["positive_timing"]["next_action"])
        self.assertFalse(card["capital_plan"]["account_cash_required"])
        self.assertEqual(card["capital_plan"]["single_add_tier"], "base")
        self.assertEqual(card["capital_plan"]["effective_single_add_pct_total_assets"], 3.0)
        self.assertEqual(card["capital_plan"]["max_single_add_pct_total_assets"], 5.0)
        self.assertEqual(card["capital_plan"]["suggested_buy_shares"], 100)
        actions = {row["action"]: row for row in card["price_action_table"]["rows"]}
        self.assertIn("正T买入", actions)
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "正T买入")
        self.assertEqual(actions["正T买入"]["shares"], 100)
        self.assertEqual(actions["正T买入"]["status"], "watch")
        self.assertIn("禁止追买", actions)
        self.assertTrue(any("最多只准备追加" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("买入后目标不是长期摊低成本" in step for step in card["decision"]["action_steps"]))

    def test_bullish_positive_t_can_raise_supplemental_capital_limit_to_five_pct(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            None,
            None,
            None,
            None,
            bullish_technical_doc(),
            positive_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 35, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "positive_t_watch")
        self.assertEqual(card["technical_assessment"]["label"], "bullish")
        self.assertGreater(card["technical_assessment"]["dimension_scores"]["trend"], 0)
        self.assertGreater(card["technical_assessment"]["dimension_scores"]["volume_confirmation"], 0)
        self.assertIn("multi_timeframe", card["technical_assessment"]["dimension_scores"])
        self.assertIn("趋势和量能", card["technical_assessment"]["summary"])
        self.assertEqual(card["decision"]["technical_operation"]["tier"], "watch_candidate")
        self.assertTrue(card["decision"]["technical_operation"]["allow_buy_watch"])
        self.assertEqual(card["decision"]["technical_operation"]["post_unlock_review"]["status"], "manual_candidate")
        self.assertEqual(card["decision"]["technical_operation"]["post_unlock_review"]["candidate"], "positive_t")
        self.assertEqual(card["post_unlock_review_summary"]["status"], "manual_candidate")
        self.assertEqual(card["post_unlock_review_summary"]["candidate"], "positive_t")
        self.assertEqual(report["post_unlock_review_alerts"][0]["action_label"], "正T人工候选")
        self.assertEqual(card["manual_execution_plan"]["status"], "ready_for_manual_confirm")
        self.assertEqual(card["manual_execution_plan"]["side"], "buy")
        self.assertEqual(card["manual_execution_plan"]["shares"], 200)
        self.assertTrue(any("买入价格只填" in step for step in card["manual_execution_plan"]["steps"]))
        self.assertEqual(card["capital_plan"]["single_add_tier"], "strong")
        self.assertEqual(card["capital_plan"]["effective_single_add_pct_total_assets"], 5.0)
        self.assertEqual(card["capital_plan"]["max_additional_capital"], 2500.0)
        self.assertTrue(any("放宽到5%" in reason for reason in card["capital_plan"]["reasons"]))
        self.assertTrue(any("总资产 5.0%" in step for step in card["decision"]["action_steps"]))

    def test_positive_t_score_without_confirmation_waits(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        item["capital_flow"]["main_net_inflow_ratio_pct"] = 0.0
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            None,
            None,
            None,
            None,
            None,
            unconfirmed_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 36, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "positive_t_watch")
        self.assertEqual(card["positive_timing"]["status"], "watch")
        self.assertGreaterEqual(card["positive_timing"]["score"], 65.0)
        self.assertLess(card["positive_timing"]["metrics"]["confirmation_count"], 2)
        self.assertTrue(any(blocker["code"] == "confirmation_insufficient" for blocker in card["positive_timing"]["blockers"]))
        self.assertIn("当前不买入", card["positive_timing"]["next_action"])
        self.assertEqual(card["capital_plan"]["status"], "waiting_intraday_confirmation")

    def test_positive_t_confirmed_intraday_waits_when_daily_context_is_weak(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            None,
            None,
            None,
            None,
            slightly_bearish_technical_doc(),
            positive_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 37, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "hold_no_add")
        self.assertIn(card["technical_assessment"]["label"], {"bearish", "slightly_bearish"})
        self.assertTrue(card["technical_assessment"]["summary"])
        self.assertFalse(card["decision"]["technical_operation"]["allow_buy_watch"])
        self.assertEqual(card["decision"]["technical_operation"]["post_unlock_review"]["status"], "technical_locked")
        self.assertEqual(card["post_unlock_review_summary"]["title"], "技术未解锁")
        self.assertEqual(report["post_unlock_review_alerts"], [])
        self.assertFalse(card["positive_timing"]["metrics"]["technical_supported"])
        self.assertTrue(any(blocker["code"] == "technical_operation_blocked" for blocker in card["positive_timing"]["blockers"]))
        self.assertEqual(card["positive_timing"]["metrics"]["technical_operation_tier"], "risk_control_first")
        self.assertFalse(card["capital_plan"]["applicable"])

    def test_post_unlock_review_alert_reports_data_quality_block(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            None,
            None,
            None,
            {
                "items": [
                    {
                        "code": "600000",
                        "overall_status": "insufficient",
                        "data_trust": {"level": "low"},
                    }
                ]
            },
            bullish_technical_doc(),
            positive_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 38, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["post_unlock_review_summary"]["status"], "blocked_after_unlock")
        self.assertIn("数据质量", card["post_unlock_review_summary"]["blocking_checks"])
        self.assertEqual(report["post_unlock_review_alerts"][0]["action_label"], "复核阻断，只观察")

    def test_reverse_t_manual_candidate_builds_sell_plan(self) -> None:
        item = intraday_item(reverse_status="candidate")
        item["position"]["shares"] = 500
        item["position"]["entry_price"] = 9.8
        item["reverse_t_plan"]["trade_shares"] = 100
        item["reverse_t_plan"]["buyback_max_price"] = 9.95
        item["t_closure_performance"] = {
            "status": "profitable",
            "total_count": 1,
            "profitable_count": 1,
            "loss_count": 0,
            "win_rate_pct": 100.0,
            "total_net_profit": 22.67,
            "recent_closures": [{"net_profit": 22.67}],
        }
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(),
            None,
            {"items": [{"code": "600000", "verdict": "pass", "verdict_label": "反T回测通过"}]},
            None,
            None,
            bullish_technical_doc(),
            {},
            generated_at=datetime(2026, 7, 16, 9, 39, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["post_unlock_review_summary"]["status"], "manual_candidate")
        self.assertEqual(card["post_unlock_review_summary"]["candidate"], "reverse_t")
        self.assertEqual(card["t_performance_gate"]["status"], "caution")
        self.assertEqual(card["manual_execution_plan"]["side"], "sell")
        self.assertEqual(card["manual_execution_plan"]["trade_intent"], "reverse_t_open")
        self.assertEqual(card["manual_execution_plan"]["post_trade_shares"], 400)
        reverse_actions = {row["action"]: row for row in card["price_action_table"]["rows"]}
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "反T卖出")
        self.assertEqual(reverse_actions["反T卖出"]["status"], "ready")
        self.assertEqual(reverse_actions["反T卖出"]["shares"], 100)
        self.assertEqual(reverse_actions["反T回补"]["price"], "≤ 9.95 元")
        self.assertTrue(any("只有价格不高于" in step for step in card["manual_execution_plan"]["steps"]))

    def test_negative_t_performance_blocks_reverse_t_candidate(self) -> None:
        item = intraday_item(reverse_status="candidate")
        item["position"]["shares"] = 500
        item["position"]["entry_price"] = 9.8
        item["reverse_t_plan"]["trade_shares"] = 100
        item["reverse_t_plan"]["buyback_max_price"] = 9.95
        item["t_closure_performance"] = {
            "status": "needs_review",
            "total_count": 2,
            "profitable_count": 0,
            "loss_count": 2,
            "win_rate_pct": 0.0,
            "total_net_profit": -6.5,
            "recent_closures": [{"net_profit": -2.0}, {"net_profit": -4.5}],
        }
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(),
            None,
            {"items": [{"code": "600000", "verdict": "pass", "verdict_label": "反T回测通过"}]},
            None,
            None,
            bullish_technical_doc(),
            {},
            generated_at=datetime(2026, 7, 16, 9, 40, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "hold_no_add")
        self.assertEqual(card["decision"]["action"], "hold_without_adding")
        self.assertEqual(card["t_performance_gate"]["status"], "blocked")
        self.assertEqual(card["post_unlock_review_summary"]["status"], "blocked_after_unlock")
        self.assertIn("做T实盘绩效", card["post_unlock_review_summary"]["blocking_checks"])
        self.assertTrue(any("累计净收益 -6.50 元" in blocker for blocker in card["blockers"]))
        blocked_actions = {row["action"]: row for row in card["price_action_table"]["rows"]}
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "反T卖出")
        self.assertEqual(blocked_actions["反T卖出"]["status"], "blocked")
        self.assertEqual(blocked_actions["反T卖出"]["status_label"], "绩效阻断")
        self.assertTrue(any("做T实盘绩效阻断" in step for step in card["decision"]["action_steps"]))
        self.assertFalse(card["manual_execution_plan"]["applicable"])

    def test_poor_execution_quality_blocks_reverse_t_candidate(self) -> None:
        item = intraday_item(reverse_status="candidate")
        item["position"]["shares"] = 500
        item["position"]["entry_price"] = 9.8
        item["reverse_t_plan"]["trade_shares"] = 100
        item["reverse_t_plan"]["buyback_max_price"] = 9.95
        item["t_closure_performance"] = {
            "status": "profitable",
            "total_count": 3,
            "profitable_count": 3,
            "loss_count": 0,
            "win_rate_pct": 100.0,
            "total_net_profit": 36.0,
            "recent_closures": [{"net_profit": 12.0}, {"net_profit": 11.0}, {"net_profit": 13.0}],
        }
        item["execution_quality_summary"] = {
            "status": "blocked",
            "review_count": 2,
            "recent_count": 2,
            "average_score": 57.5,
            "failed_count": 1,
            "needs_review_count": 1,
            "poor_score_count": 2,
            "recent_reviews": [{"score": 45, "status": "failed"}, {"score": 70, "status": "needs_review"}],
            "latest_review": {"score": 70, "status_label": "需要复盘"},
        }
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(),
            None,
            {"items": [{"code": "600000", "verdict": "pass", "verdict_label": "反T回测通过"}]},
            None,
            None,
            bullish_technical_doc(),
            {},
            generated_at=datetime(2026, 7, 16, 9, 41, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "hold_no_add")
        self.assertEqual(card["execution_quality_gate"]["status"], "blocked")
        self.assertEqual(card["post_unlock_review_summary"]["status"], "blocked_after_unlock")
        self.assertIn("执行质量评分", card["post_unlock_review_summary"]["blocking_checks"])
        self.assertTrue(any("执行质量阻断" in step for step in card["decision"]["action_steps"]))
        blocked_actions = {row["action"]: row for row in card["price_action_table"]["rows"]}
        self.assertEqual(blocked_actions["反T卖出"]["status"], "blocked")
        self.assertEqual(blocked_actions["反T卖出"]["status_label"], "执行评分阻断")
        self.assertFalse(card["manual_execution_plan"]["applicable"])

    def test_positive_t_candidate_without_intraday_confirmation_waits(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            None,
            None,
            None,
            None,
            None,
            {},
            generated_at=datetime(2026, 7, 16, 9, 36, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "positive_t_watch")
        self.assertEqual(card["positive_timing"]["status"], "insufficient")
        self.assertTrue(any(blocker["code"] == "minute_sample_insufficient" for blocker in card["positive_timing"]["blockers"]))
        self.assertIn("不买入", card["positive_timing"]["next_action"])
        self.assertEqual(card["capital_plan"]["status"], "waiting_intraday_confirmation")
        self.assertIn("分时评分未确认", card["capital_plan"]["status_label"])

    def test_reverse_t_price_levels_prefer_indicator_forecast_zone(self) -> None:
        report = build_report(
            {"items": [intraday_item(reverse_status="watch")]},
            portfolio_result(),
            t_result(),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            {
                "items": [
                    {
                        "code": "600000",
                        "status": "watch",
                        "status_label": "指标预测区间仍需观察",
                        "as_of": "2026-07-16 10:40",
                        "predicted_sell_zone": [10.01, 10.08],
                        "predicted_buyback_max_price": 9.82,
                        "reach_probability_pct": 55.0,
                        "roundtrip_probability_pct": 62.0,
                        "joint_roundtrip_probability_pct": 34.1,
                    }
                ]
            },
            generated_at=datetime(2026, 7, 16, 10, 40, 0),
        )

        card = report["cards"][0]
        levels = card["price_levels"]

        self.assertEqual(levels["reverse_t_sell_zone"], [10.01, 10.08])
        self.assertEqual(levels["reverse_t_buyback_max_price"], 9.82)
        self.assertEqual(levels["reverse_t_zone_source"], "forecast")
        self.assertEqual(levels["reverse_t_forecast_as_of"], "2026-07-16 10:40")
        self.assertTrue(any("[反T预测区间]" in evidence for evidence in card["evidence"]))

    def test_reverse_t_forecast_without_buyback_does_not_fallback_to_intraday_high_zone(self) -> None:
        report = build_report(
            {"items": [intraday_item(reverse_status="watch")]},
            portfolio_result(),
            t_result(),
            None,
            None,
            {
                "items": [
                    {
                        "code": "600000",
                        "status": "fee_blocked",
                        "status_label": "预测价差不足以覆盖费用",
                        "as_of": "2026-07-16 10:45",
                        "predicted_sell_zone": [10.0, 10.04],
                        "predicted_buyback_max_price": None,
                    }
                ]
            },
            generated_at=datetime(2026, 7, 16, 10, 45, 0),
        )

        card = report["cards"][0]
        levels = card["price_levels"]

        self.assertEqual(levels["reverse_t_sell_zone"], [10.0, 10.04])
        self.assertIsNone(levels["reverse_t_buyback_max_price"])
        self.assertEqual(levels["reverse_t_zone_source"], "forecast")
        self.assertTrue(any("未给出可执行回补上限" in evidence for evidence in card["evidence"]))

    def test_stale_quote_pauses_intraday_decision(self) -> None:
        report = build_report(
            {"items": [intraday_item(signals=[{"code": "stale_quote", "severity": "block", "message": "行情过期。"}])]},
            portfolio_result(),
            t_result(),
            None,
            None,
            None,
            generated_at=datetime(2026, 7, 16, 9, 32, 0),
        )
        content = render_markdown(report)

        card = report["cards"][0]

        self.assertEqual(card["state"], "data_stale")
        self.assertEqual(card["decision"]["action"], "pause_intraday_decision")
        self.assertIn("实时持仓决策卡", content)
        self.assertIn("今日处理顺序", content)
        self.assertIn("补齐数据后再决策", content)
        self.assertIn("行情过期", content)

    def test_off_session_stale_quote_waits_for_market(self) -> None:
        report = build_report(
            {"items": [intraday_item(signals=[{"code": "stale_quote", "severity": "block", "message": "行情过期。"}])]},
            portfolio_result(),
            t_result(),
            None,
            None,
            None,
            {
                "items": [
                    {
                        "code": "600000",
                        "overall_status": "stale",
                        "status_label": "数据过期",
                        "market_session": {
                            "phase": "pre_market",
                            "label": "盘前",
                            "live_quote_required": False,
                            "intraday_execution_window": False,
                            "message": "当前不在连续盘中执行窗口，行情停留在上一撮合时段通常属于正常等待。",
                        },
                        "quote": {"status": "stale"},
                        "data_trust": {
                            "level": "low",
                            "label": "低可信",
                            "intraday_decision_allowed": False,
                            "reasons": ["行情: 行情延迟 300.0 秒，超过 60.0 秒阈值。"],
                        },
                        "blockers": [],
                        "warnings": ["行情延迟 300.0 秒，超过 60.0 秒阈值。"],
                    }
                ]
            },
            generated_at=datetime(2026, 7, 16, 8, 50, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "market_wait")
        self.assertEqual(card["decision"]["action"], "wait_for_market_session")
        self.assertEqual(card["market_context"]["market_session_phase"], "pre_market")
        self.assertFalse(card["market_context"]["live_quote_required"])
        self.assertIn("[交易时段] 盘前", "\n".join(card["evidence"]))

    def test_data_quality_insufficient_blocks_decision(self) -> None:
        report = build_report(
            {"items": [intraday_item()]},
            portfolio_result(),
            t_result(),
            None,
            None,
            None,
            {
                "items": [
                    {
                        "code": "600000",
                        "overall_status": "insufficient",
                        "status_label": "样本不足",
                        "data_trust": {
                            "level": "low",
                            "label": "低可信",
                            "intraday_decision_allowed": False,
                            "reasons": ["日线: 日线数量 8 少于 20。"],
                        },
                        "blockers": ["日线数量 8 少于 20。"],
                        "warnings": [],
                        "source_consistency": {
                            "status": "conflict",
                            "max_diff_pct": 1.0,
                            "issues": ["东方财富现价与分钟线最新收盘价差 2.00%。"],
                        },
                    }
                ]
            },
            generated_at=datetime(2026, 7, 16, 9, 33, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "data_insufficient")
        self.assertEqual(card["decision"]["action"], "complete_data_before_decision")
        self.assertEqual(card["decision"]["next_step"], "本轮不交易；先修复数据阻断，再重新生成实时决策卡。")
        self.assertTrue(any("补齐日线历史数据" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("刷新5分钟线缓存" in step for step in card["decision"]["action_steps"]))
        self.assertIn("日线数量 8 少于 20。", card["blockers"])
        self.assertEqual(card["market_context"]["data_quality_status"], "insufficient")
        self.assertEqual(card["market_context"]["data_trust_level"], "low")
        self.assertEqual(card["market_context"]["source_consistency_status"], "conflict")
        self.assertIn("[数据一致性] conflict · 阈值 1.0%", card["evidence"])
        self.assertIn("[数据源冲突] 东方财富现价与分钟线最新收盘价差 2.00%。", card["evidence"])

    def test_new_listing_limited_history_allows_conservative_analysis(self) -> None:
        report = build_report(
            {"items": [intraday_item(code="001248")]},
            portfolio_result(code="001248"),
            t_result(code="001248", warnings=[{"code": "limited_history_new_listing", "message": "日线数量 11 少于中期窗口 20，按新股有限样本模式分析。"}]),
            None,
            None,
            None,
            {
                "items": [
                    {
                        "code": "001248",
                        "overall_status": "limited_history",
                        "status_label": "新股样本有限",
                        "data_trust": {
                            "level": "medium",
                            "label": "中可信",
                            "intraday_decision_allowed": True,
                            "reasons": ["日线: 新股日线数量 11 少于 20，趋势/回测降级为有限样本分析。"],
                        },
                        "blockers": [],
                        "warnings": ["新股日线数量 11 少于 20，趋势/回测降级为有限样本分析。"],
                        "source_consistency": {"status": "pass", "max_diff_pct": 1.0, "issues": []},
                    }
                ]
            },
            generated_at=datetime(2026, 7, 16, 9, 33, 0),
        )

        card = report["cards"][0]

        self.assertNotEqual(card["state"], "data_insufficient")
        self.assertEqual(card["market_context"]["data_quality_status"], "limited_history")
        self.assertEqual(card["market_context"]["data_trust_level"], "medium")
        self.assertIn("[数据质量] 新股样本有限 · 中可信", card["evidence"])

    def test_bearish_technical_indicators_block_t_watch(self) -> None:
        report = build_report(
            {"items": [intraday_item()]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            None,
            None,
            None,
            None,
            bearish_technical_doc(),
            generated_at=datetime(2026, 7, 16, 9, 34, 0),
        )
        content = render_markdown(report)

        card = report["cards"][0]

        self.assertEqual(card["state"], "hold_no_add")
        self.assertEqual(card["decision"]["action"], "hold_without_adding")
        self.assertEqual(card["technical_assessment"]["label"], "bearish")
        self.assertLess(card["technical_assessment"]["score"], -18)
        self.assertLess(card["technical_assessment"]["dimension_scores"]["trend"], 0)
        self.assertTrue(card["technical_assessment"]["dimension_signals"])
        self.assertFalse(card["capital_plan"]["applicable"])
        self.assertEqual(card["market_context"]["technical_label"], "bearish")
        self.assertIn("多周期技术指标偏弱，本轮禁止补仓和做T。", card["blockers"])
        self.assertIn("[技术指标] bearish", "\n".join(card["evidence"]))
        self.assertIn("技术判断", content)

    def test_technical_dimension_summary_explains_reversal_risk_conflict(self) -> None:
        self.assertEqual(
            technical_dimension_summary(
                {
                    "trend": -2.8,
                    "risk": -25.3,
                    "reversal": 4.4,
                    "volume_confirmation": -2.8,
                    "multi_timeframe": 0.0,
                }
            ),
            "有一点反转迹象，但风险分明显拖累，趋势和量能还没确认，所以不支持继续追买或继续做T。",
        )

    def test_technical_operation_blocks_reversal_when_risk_and_volume_disagree(self) -> None:
        operation = build_technical_operation(
            {
                "available": True,
                "label": "slightly_bearish",
                "summary": "有一点反转迹象，但风险分明显拖累，趋势和量能还没确认，所以不支持继续追买或继续做T。",
                "dimension_scores": {
                    "trend": -2.8,
                    "risk": -25.3,
                    "reversal": 4.4,
                    "volume_confirmation": -2.8,
                    "multi_timeframe": 0.0,
                },
            }
        )

        self.assertEqual(operation["tier"], "risk_control_first")
        self.assertEqual(operation["tier_label"], "风险优先")
        self.assertFalse(operation["allow_buy_watch"])
        self.assertFalse(operation["allow_t_watch"])
        self.assertIn("不追买、不补仓、不做T", operation["next_step"])
        self.assertEqual(
            [condition["code"] for condition in operation["unlock_conditions"]],
            ["risk_recovered", "trend_positive", "volume_confirmed"],
        )
        self.assertTrue(all(condition["passed"] is False for condition in operation["unlock_conditions"]))
        self.assertEqual(operation["unlock_conditions"][0]["gap"], 7.3)
        self.assertIn("还差 7.3 分", operation["unlock_conditions"][0]["gap_text"])
        self.assertIn("风险项继续修复", operation["unlock_conditions"][0]["hint"])

    def test_technical_unlock_alert_emits_when_blocked_condition_is_near(self) -> None:
        operation = build_technical_operation(
            {
                "available": True,
                "label": "slightly_bearish",
                "summary": "风险分明显拖累，当前先控制风险，不支持追买、补仓或做T。",
                "dimension_scores": {
                    "trend": 0.5,
                    "risk": -19.0,
                    "reversal": 1.0,
                    "volume_confirmation": 0.8,
                    "multi_timeframe": 0.0,
                },
            }
        )

        alert = build_technical_unlock_alert(
            {
                "code": "600000",
                "name": "浦发银行",
                "decision": {"technical_operation": operation},
            }
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert["type"], "technical_unlock_near")
        self.assertEqual(alert["severity"], "watch")
        self.assertEqual(alert["min_gap"], 1.0)
        self.assertEqual(alert["action_label"], "接近解锁，只观察")
        self.assertTrue(any("不买入、不补仓、不做T" in item for item in alert["checklist"]))
        self.assertTrue(any(condition["code"] == "risk_recovered" for condition in alert["matched_conditions"]))


if __name__ == "__main__":
    unittest.main()
