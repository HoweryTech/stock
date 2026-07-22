import unittest
from datetime import datetime

from tools.build_realtime_decision_cards import build_minute_confirmation, build_positive_timing, build_report, build_technical_operation, build_technical_unlock_alert, render_markdown, technical_dimension_summary


def intraday_item(code: str = "600000", *, signals=None, reverse_status: str = "watch") -> dict:
    return {
        "code": code,
        "name": "浦发银行",
        "quote": {"latest_price": 10.0, "change_pct": 1.0, "quote_lag_seconds": 3.0, "turnover": 80_000_000.0, "volume": 800_000},
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


def weak_minute_bars(code: str = "600000") -> dict[str, list[dict]]:
    closes = [10.80 - index * 0.035 for index in range(30)]
    bars = []
    for index, close in enumerate(closes):
        bars.append(
            {
                "timestamp": f"2026-07-16 10:{index:02d}",
                "code": code,
                "open": close + 0.02,
                "high": close + 0.03,
                "low": close - 0.02,
                "close": close,
                "volume": 900 + index * 80,
            }
        )
    return {code: bars}


def early_session_minute_bars(code: str = "600000") -> list[dict]:
    closes = [9.90, 9.92, 9.91, 9.93, 9.95, 9.96, 9.98, 10.00, 10.02, 10.03, 10.04, 10.05, 10.06, 10.07, 10.08, 10.09, 10.10, 10.12, 10.14, 10.16]
    timestamps = [
        "2026-07-15 13:45",
        "2026-07-15 13:50",
        "2026-07-15 13:55",
        "2026-07-15 14:00",
        "2026-07-15 14:05",
        "2026-07-15 14:10",
        "2026-07-15 14:15",
        "2026-07-15 14:20",
        "2026-07-15 14:25",
        "2026-07-15 14:30",
        "2026-07-15 14:35",
        "2026-07-15 14:40",
        "2026-07-15 14:45",
        "2026-07-15 14:50",
        "2026-07-15 14:55",
        "2026-07-15 15:00",
        "2026-07-16 09:35",
        "2026-07-16 09:40",
        "2026-07-16 09:45",
        "2026-07-16 09:50",
    ]
    return [
        {
            "timestamp": timestamp,
            "code": code,
            "open": close - 0.01,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "volume": 1000 + index * 20,
        }
        for index, (timestamp, close) in enumerate(zip(timestamps, closes))
    ]


class RealtimeDecisionCardsTest(unittest.TestCase):
    def test_minute_confirmation_uses_previous_session_warmup_early_in_day(self) -> None:
        result = build_minute_confirmation(
            {"quote": {"latest_price": 10.16}},
            early_session_minute_bars(),
            bullish_technical_doc()["items"][0],
        )

        self.assertNotEqual(result["status"], "not_available")
        self.assertTrue(result["metrics"]["warmup_used"])
        self.assertEqual(result["metrics"]["current_day_bar_count"], 4)
        self.assertTrue(any("预热样本" in item for item in result["signals"]))

    def test_positive_timing_uses_previous_session_warmup_early_in_day(self) -> None:
        result = build_positive_timing(
            {"quote": {"latest_price": 10.16}, "capital_flow": {"main_net_inflow_ratio_pct": 1.5}},
            {"conclusion": "positive_t_candidate"},
            early_session_minute_bars(),
            bullish_technical_doc()["items"][0],
        )

        self.assertNotEqual(result["status"], "insufficient")
        self.assertTrue(result["metrics"]["warmup_used"])
        self.assertEqual(result["metrics"]["current_day_bar_count"], 4)
        self.assertTrue(any("预热样本" in item for item in result["signals"]))

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
        self.assertEqual(card["price_action_table"]["rows"][0]["action"], "止损减仓")
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "止损减仓")
        self.assertEqual(exit_actions["止损减仓"]["status"], "ready")
        self.assertEqual(exit_actions["止损减仓"]["operation"], "卖出风险仓位")
        self.assertEqual(exit_actions["止损减仓"]["shares"], 500)
        self.assertEqual(card["manual_execution_plan"]["candidate"], "risk_exit")
        self.assertEqual(card["manual_execution_plan"]["plan_type"], "risk_reduce")
        self.assertEqual(card["manual_execution_plan"]["shares"], 500)
        self.assertEqual(card["manual_execution_plan"]["post_trade_shares"], 500)
        self.assertEqual(exit_actions["做T阻断"]["status"], "blocked")
        self.assertIn("禁止买入、补仓、做T", card["decision"]["action_steps"][0])
        self.assertTrue(any("当前计划" in step for step in card["decision"]["action_steps"]))
        self.assertFalse(any("全仓卖出后该股持仓变为0股" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("交易/卖出" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("卖出数量输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("卖出价格输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("成交后的下一步计划" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("做T阻断价" in step for step in card["decision"]["action_steps"]))
        self.assertIn("距离止损不足1%。", card["blockers"])
        self.assertEqual(report["priority_queue"]["top_items"][0]["code"], "600000")
        self.assertEqual(report["priority_queue"]["top_items"][0]["category"], "risk_exit")
        self.assertEqual(report["priority_queue"]["top_items"][0]["urgency"], "high")
        self.assertEqual(report["intraday_trigger_alerts"][0]["type"], "intraday_trigger")
        self.assertEqual(report["intraday_trigger_alerts"][0]["severity"], "action")
        self.assertEqual(report["intraday_trigger_alerts"][0]["active_path"], "price_action_ready")
        self.assertIn("止损减仓", report["intraday_trigger_alerts"][0]["title"])

    def test_open_reverse_t_leg_takes_buyback_review_over_exit_risk(self) -> None:
        item = intraday_item(reverse_status="buyback_ready")
        item["quote"]["latest_price"] = 5.79
        item["position"]["shares"] = 100
        item["position"]["entry_price"] = 11.88
        item["reverse_t_plan"].update(
            {
                "trade_shares": 100,
                "sell_zone": [6.17, 6.17],
                "buyback_max_price": 5.96,
                "open_reverse_t_leg": {
                    "id": "MANUAL-OPEN",
                    "side": "sell",
                    "shares": 100,
                    "sell_price": 6.17,
                    "trade_intent": "reverse_t_open",
                },
            }
        )
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["current_price"] = 5.79
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 6.04
        report = build_report(
            {"items": [item]},
            portfolio,
            t_result(blockers=[{"code": "stop_loss_triggered", "message": "已触发止损价。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "pass", "verdict_label": "反T回测通过"}]},
            {"items": [{"code": "600000", "as_of": "2026-07-16 09:39", "predicted_sell_zone": [6.17, 6.17], "predicted_buyback_max_price": 5.96}]},
            None,
            bullish_technical_doc(),
            positive_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 39, 0),
        )

        card = report["cards"][0]
        actions = {row["action"]: row for row in card["price_action_table"]["rows"]}

        self.assertEqual(card["state"], "reverse_buyback_review")
        self.assertEqual(card["state_label"], "反T回补复核")
        self.assertEqual(card["decision"]["action"], "review_reverse_t_buyback")
        self.assertEqual(card["decision"]["action_label"], "反T回补复核：只处理已卖出腿")
        self.assertEqual(card["manual_execution_plan"]["candidate"], "reverse_t_buyback")
        self.assertEqual(card["manual_execution_plan"]["trade_intent"], "reverse_t_close")
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "反T回补")
        self.assertEqual(actions["反T回补"]["status"], "ready")
        self.assertEqual(actions["反T回补"]["shares"], 100)
        self.assertEqual(actions["止损风险复核"]["status"], "watch")
        self.assertEqual(report["priority_queue"]["top_items"][0]["category"], "manual_candidate")

    def test_stop_loss_with_reversal_uses_rebound_reduce_plan(self) -> None:
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 10.1
        report = build_report(
            {"items": [intraday_item()]},
            portfolio,
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            technical_indicators=slightly_bearish_technical_doc(),
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        card = report["cards"][0]
        actions = {row["action"]: row for row in card["price_action_table"]["rows"]}

        self.assertEqual(card["manual_execution_plan"]["plan_type"], "rebound_reduce")
        self.assertEqual(card["manual_execution_plan"]["status"], "wait_rebound_reduce")
        self.assertEqual(card["manual_execution_plan"]["shares"], 500)
        self.assertIn("反弹减仓", actions)
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "反弹减仓")
        self.assertEqual(actions["反弹减仓"]["status_label"], "等反弹")
        self.assertTrue(any("只等待价格反弹到" in step for step in card["manual_execution_plan"]["steps"]))

    def test_near_confirmed_stop_loss_keeps_risk_review_primary(self) -> None:
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 9.95
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_confirmed"] = True
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
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "止损风险复核")
        self.assertEqual(card["price_action_table"]["primary_action"]["status_label"], "近硬止损")
        self.assertEqual(card["manual_execution_plan"]["candidate"], "risk_exit")
        self.assertEqual(card["manual_execution_plan"]["plan_type"], "near_stop_playbook")
        self.assertEqual(card["manual_execution_plan"]["status"], "near_stop_review")
        self.assertEqual(card["manual_execution_plan"]["shares"], 500)
        self.assertTrue(any("路径1-下破" in step for step in card["manual_execution_plan"]["steps"]))
        self.assertTrue(any("路径2-反抽" in step for step in card["manual_execution_plan"]["steps"]))
        self.assertTrue(any("路径3-站稳" in step for step in card["manual_execution_plan"]["steps"]))
        self.assertTrue(any("当前不是立即卖出" in step for step in card["decision"]["action_steps"]))
        self.assertFalse(any("当前计划：止损风险复核 500 股" in step for step in card["decision"]["action_steps"]))
        content = render_markdown(report)
        self.assertEqual(report["intraday_trigger_alerts"][0]["active_path"], None)
        self.assertEqual(report["intraday_trigger_alerts"][0]["action_label"], "盯盘中，不提前交易")
        self.assertTrue(any(trigger["path"] == "path1_break" for trigger in report["intraday_trigger_alerts"][0]["triggers"]))
        self.assertIn("## 盘中盯盘提醒", content)
        self.assertIn("人工候选计划：近硬止损盘中预案；等待三路径触发", content)
        self.assertNotIn("人工候选计划：近硬止损盘中预案；卖出 500 股", content)
        arbitration = card["decision"]["action_arbitration"]
        structured = card["decision"]["structured_conclusion"]
        self.assertEqual(arbitration["primary_action"], "止损风险复核")
        self.assertIn("正T、反T和补仓全部让位", arbitration["summary"])
        self.assertTrue(any("T" in item["action"] or "买入" in item["action"] for item in arbitration["suppressed_actions"]))
        self.assertIn("止损风险复核", structured["current_action"])
        self.assertIn("三路径", structured["trigger_condition"])
        self.assertTrue(any("禁止补仓" in item for item in structured["forbidden_actions"]))

    def test_near_stop_rebound_zone_is_anchored_not_chasing_current_price(self) -> None:
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 9.95
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_confirmed"] = True
        item = intraday_item()
        item["quote"]["latest_price"] = 10.06
        item["technicals"]["ma5"] = 10.04
        item["technicals"]["ma20"] = 9.95
        report = build_report(
            {"items": [item]},
            portfolio,
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 35, 0),
        )

        card = report["cards"][0]
        plan = card["manual_execution_plan"]
        alert = report["intraday_trigger_alerts"][0]

        self.assertEqual(plan["plan_type"], "near_stop_playbook")
        self.assertLessEqual(plan["price_zone"][0], item["quote"]["latest_price"])
        self.assertEqual(alert["active_path"], "path2_rebound")
        self.assertEqual(alert["severity"], "action")

    def test_near_stop_path3_recovery_downgrades_exit_risk_state(self) -> None:
        portfolio = portfolio_result(actions=[{"code": "stop_loss_triggered", "message": "旧持仓价触发止损。"}])
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 9.50
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_confirmed"] = True
        item = intraday_item()
        item["quote"]["latest_price"] = 10.02
        item["technicals"]["ma5"] = 9.90
        report = build_report(
            {"items": [item]},
            portfolio,
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            minute_bars=positive_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 40, 0),
        )

        card = report["cards"][0]

        self.assertEqual(report["intraday_trigger_alerts"][0]["active_path"], "path3_recover")
        self.assertEqual(report["intraday_trigger_alerts"][0]["action_label"], "风险降级观察")
        self.assertEqual(card["state"], "risk_downgrade_watch")
        self.assertEqual(card["state_label"], "风险降级观察")
        self.assertEqual(card["manual_execution_plan"]["status_label"], "风险降级观察")
        self.assertFalse(card["decision"]["execution_allowed"])
        self.assertFalse(any("距离止损不足" in item for item in card["blockers"]))

    def test_near_stop_without_minute_confirmation_keeps_exit_risk_state(self) -> None:
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 9.50
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_confirmed"] = True
        item = intraday_item()
        item["quote"]["latest_price"] = 10.02
        item["technicals"]["ma5"] = 9.90
        report = build_report(
            {"items": [item]},
            portfolio,
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 40, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "exit_risk_review")
        self.assertEqual(card["state_label"], "退出风险优先")
        self.assertEqual(report["intraday_trigger_alerts"][0]["active_path"], None)

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
        self.assertNotIn("止损复核", actions)
        self.assertNotIn("止损/退出", actions)
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "当前动作")
        self.assertEqual(card["price_levels"]["dynamic_stop_loss_price"], 9.506)

    def test_reference_only_stop_status_is_exposed_to_dashboard(self) -> None:
        portfolio = portfolio_result()
        result = portfolio["positions"][0]["result"]
        result["warnings"] = [{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]
        result["calculations"]["stop_loss_price"] = 10.5
        result["calculations"]["stop_loss_confirmed"] = False
        result["calculations"]["stop_loss_confirmation_status"] = "reference_only"
        result["calculations"]["stop_loss_confirmation_label"] = "仅保留参考"
        report = build_report(
            {"items": [intraday_item()]},
            portfolio,
            t_result(warnings=[{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        levels = report["cards"][0]["price_levels"]
        actions = {row["action"]: row for row in report["cards"][0]["price_action_table"]["rows"]}

        self.assertEqual(levels["stop_loss_confirmation_status"], "reference_only")
        self.assertEqual(levels["stop_loss_confirmation_label"], "仅保留参考")
        self.assertNotIn("止损/退出", actions)

    def test_dynamic_unconfirmed_stop_reference_can_use_market_levels(self) -> None:
        portfolio = portfolio_result()
        result = portfolio["positions"][0]["result"]
        result["warnings"] = [{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]
        result["calculations"]["stop_loss_price"] = 8.0
        result["calculations"]["stop_loss_confirmed"] = False
        report = build_report(
            {"items": [intraday_item()]},
            portfolio,
            t_result(warnings=[{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        levels = report["cards"][0]["price_levels"]
        actions = {row["action"]: row for row in report["cards"][0]["price_action_table"]["rows"]}

        self.assertEqual(levels["stop_loss_price"], 8.0)
        self.assertEqual(levels["dynamic_stop_loss_price"], 9.108)
        self.assertEqual(levels["dynamic_stop_loss_source"], "recent_low_buffer")
        self.assertNotIn("止损复核", actions)
        self.assertEqual(report["cards"][0]["price_action_table"]["primary_action"]["action"], "当前动作")

    def test_unconfirmed_stop_reference_enters_steps_only_when_near_review_price(self) -> None:
        portfolio = portfolio_result()
        result = portfolio["positions"][0]["result"]
        result["warnings"] = [{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]
        result["calculations"]["stop_loss_price"] = 8.0
        result["calculations"]["stop_loss_confirmed"] = False
        item = intraday_item()
        item["quote"]["latest_price"] = 9.3
        report = build_report(
            {"items": [item]},
            portfolio,
            t_result(warnings=[{"code": "unconfirmed_stop_loss_reference", "message": "导入草案止损未确认。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        actions = {row["action"]: row for row in report["cards"][0]["price_action_table"]["rows"]}

        self.assertEqual(actions["止损复核"]["price"], "9.11 元")
        self.assertEqual(actions["止损复核"]["status_label"], "接近复核")
        self.assertNotIn("止损/退出", actions)

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

    def test_intraday_capital_budget_links_multiple_positive_t_candidates(self) -> None:
        codes = ["600000", "600001", "600002"]
        items = []
        for code in codes:
            item = intraday_item(code)
            item["position"]["shares"] = 100
            item["position"]["market_value"] = 1000.0
            item["position"]["live_position_pct"] = 2.0
            items.append(item)
        portfolio = {"positions": [portfolio_result(code)["positions"][0] for code in codes]}
        t_doc = {"items": [t_result(code, conclusion="positive_t_candidate")["items"][0] for code in codes]}
        technical_doc = {"items": [bullish_technical_doc(code)["items"][0] for code in codes]}
        minute_doc = {}
        for code in codes:
            minute_doc.update(positive_minute_bars(code))

        report = build_report(
            {"total_assets": 50000.0, "items": items},
            portfolio,
            t_doc,
            None,
            None,
            None,
            None,
            technical_doc,
            minute_doc,
            generated_at=datetime(2026, 7, 16, 9, 35, 0),
        )

        usage = report["intraday_capital_usage"]
        cards = {card["code"]: card for card in report["cards"]}

        self.assertEqual(usage["max_intraday_add_pct_total_assets"], 8.0)
        self.assertEqual(usage["max_intraday_add_amount"], 4000.0)
        self.assertEqual(usage["candidate_count"], 3)
        self.assertEqual(usage["reserved_candidate_amount"], 3992.0)
        self.assertEqual(usage["remaining_add_amount"], 8.0)
        self.assertEqual(cards["600000"]["capital_plan"]["portfolio_capital_link"]["status"], "allocated")
        self.assertEqual(cards["600001"]["capital_plan"]["portfolio_capital_link"]["status"], "allocated")
        self.assertEqual(cards["600002"]["capital_plan"]["portfolio_capital_link"]["status"], "portfolio_budget_blocked")
        self.assertEqual(cards["600002"]["capital_plan"]["status"], "portfolio_budget_blocked")
        self.assertEqual(cards["600002"]["manual_execution_plan"]["status"], "blocked")
        self.assertEqual(
            {row["action"]: row for row in cards["600002"]["price_action_table"]["rows"]}["正T买入"]["status_label"],
            "组合预算阻断",
        )

    def test_intraday_capital_budget_uses_profile_config(self) -> None:
        codes = ["600000", "600001"]
        items = []
        for code in codes:
            item = intraday_item(code)
            item["position"]["shares"] = 100
            item["position"]["market_value"] = 1000.0
            item["position"]["live_position_pct"] = 2.0
            items.append(item)
        portfolio = {"positions": [portfolio_result(code)["positions"][0] for code in codes]}
        t_doc = {"items": [t_result(code, conclusion="positive_t_candidate")["items"][0] for code in codes]}
        technical_doc = {"items": [bullish_technical_doc(code)["items"][0] for code in codes]}
        minute_doc = {}
        for code in codes:
            minute_doc.update(positive_minute_bars(code))

        report = build_report(
            {"total_assets": 50000.0, "items": items},
            portfolio,
            t_doc,
            None,
            None,
            None,
            None,
            technical_doc,
            minute_doc,
            generated_at=datetime(2026, 7, 16, 9, 35, 0),
            investment_profile={"t_trading": {"supplemental_capital": {"max_intraday_add_pct_total_assets": 4.0}}},
        )

        cards = {card["code"]: card for card in report["cards"]}

        self.assertEqual(report["intraday_capital_usage"]["max_intraday_add_amount"], 2000.0)
        self.assertEqual(cards["600000"]["capital_plan"]["portfolio_capital_link"]["status"], "allocated")
        self.assertEqual(cards["600001"]["capital_plan"]["portfolio_capital_link"]["status"], "portfolio_budget_blocked")

    def test_low_liquidity_blocks_positive_t_manual_candidate(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        item["quote"]["turnover"] = 5_000_000.0
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
        actions = {row["action"]: row for row in card["price_action_table"]["rows"]}

        self.assertEqual(card["liquidity_activity_gate"]["status"], "blocked")
        self.assertEqual(card["post_unlock_review_summary"]["status"], "blocked_after_unlock")
        self.assertIn("成交活跃度", card["post_unlock_review_summary"]["blocking_checks"])
        self.assertEqual(card["manual_execution_plan"]["status"], "not_applicable")
        self.assertEqual(actions["正T买入"]["status"], "blocked")
        self.assertEqual(actions["正T买入"]["status_label"], "活跃度阻断")
        self.assertTrue(any("成交活跃度" in item for item in card["evidence"]))

    def test_liquidity_gate_does_not_block_risk_exit_plan(self) -> None:
        item = intraday_item()
        item["quote"]["turnover"] = 5_000_000.0
        portfolio = portfolio_result()
        portfolio["positions"][0]["result"]["calculations"]["stop_loss_price"] = 10.5
        report = build_report(
            {"items": [item]},
            portfolio,
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["liquidity_activity_gate"]["status"], "blocked")
        self.assertEqual(card["state"], "exit_risk_review")
        self.assertEqual(card["manual_execution_plan"]["candidate"], "risk_exit")
        self.assertEqual(card["manual_execution_plan"]["status"], "ready_for_manual_confirm")
        self.assertEqual(card["price_action_table"]["primary_action"]["action"], "止损减仓")

    def test_daily_trade_rhythm_blocks_new_positive_t_after_risk_exit(self) -> None:
        item = intraday_item()
        item["position"]["shares"] = 100
        item["position"]["market_value"] = 1000.0
        item["position"]["live_position_pct"] = 2.0
        item["daily_trade_rhythm"] = {
            "status": "risk_exit_cooldown",
            "status_label": "风控卖出后冷静",
            "trade_date": "2026-07-16",
            "trade_count": 1,
            "risk_exit_count": 1,
            "blockers": ["今日已执行风控卖出，禁止立刻反向买回、补仓或新增做T。"],
            "forbidden_actions": ["禁止补仓", "禁止正T买入"],
            "next_action": "今日已执行风控卖出；不立刻买回、不补仓、不新增做T。",
        }
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

        self.assertEqual(card["daily_trade_rhythm"]["status"], "risk_exit_cooldown")
        self.assertIn("今日已执行风控卖出，禁止立刻反向买回、补仓或新增做T。", card["blockers"])
        self.assertEqual(card["manual_execution_plan"]["status"], "blocked")
        self.assertEqual(card["manual_execution_plan"]["status_label"], "日内节奏冷静期，禁止新增做T")
        self.assertTrue(any("不补仓" in step for step in card["decision"]["action_steps"]))

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

    def test_minute_confirmation_blocks_when_short_term_signals_are_weak(self) -> None:
        item = intraday_item()
        item["quote"]["latest_price"] = 9.78
        report = build_report(
            {"items": [item]},
            portfolio_result(),
            t_result(),
            None,
            None,
            None,
            None,
            bullish_technical_doc(),
            weak_minute_bars(),
            generated_at=datetime(2026, 7, 16, 10, 0, 0),
        )

        card = report["cards"][0]
        confirmation = card["minute_confirmation"]

        self.assertTrue(confirmation["available"])
        self.assertEqual(confirmation["status"], "block")
        self.assertLessEqual(confirmation["score"], -18)
        self.assertLess(confirmation["metrics"]["return_6_pct"], 0)
        self.assertTrue(any("5分钟MACD" in item for item in confirmation["blockers"]))
        self.assertTrue(any("[分钟阻断]" in item for item in card["evidence"]))

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
            {"items": [{"code": "600000", "as_of": "2026-07-16 09:39", "predicted_sell_zone": [10.2, 10.3], "predicted_buyback_max_price": 9.95}]},
            None,
            bullish_technical_doc(),
            positive_minute_bars(),
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

    def test_reverse_t_candidate_waits_when_minute_confirmation_blocks(self) -> None:
        item = intraday_item(reverse_status="candidate")
        item["position"]["shares"] = 500
        item["position"]["entry_price"] = 9.8
        item["quote"]["latest_price"] = 9.78
        item["reverse_t_plan"]["trade_shares"] = 100
        item["reverse_t_plan"]["buyback_max_price"] = 9.95
        report = build_report(
            {"total_assets": 50000.0, "items": [item]},
            portfolio_result(),
            t_result(),
            None,
            {"items": [{"code": "600000", "verdict": "pass", "verdict_label": "反T回测通过"}]},
            {"items": [{"code": "600000", "as_of": "2026-07-16 09:39", "predicted_sell_zone": [10.2, 10.3], "predicted_buyback_max_price": 9.95}]},
            None,
            bullish_technical_doc(),
            weak_minute_bars(),
            generated_at=datetime(2026, 7, 16, 9, 39, 0),
        )

        card = report["cards"][0]
        reverse_actions = {row["action"]: row for row in card["price_action_table"]["rows"]}

        self.assertEqual(card["minute_confirmation"]["status"], "block")
        self.assertEqual(card["post_unlock_review_summary"]["status"], "blocked_after_unlock")
        self.assertFalse(card["manual_execution_plan"]["applicable"])
        self.assertEqual(reverse_actions["反T卖出"]["status"], "blocked")
        self.assertEqual(reverse_actions["反T卖出"]["status_label"], "分钟阻断")
        structured = card["decision"]["structured_conclusion"]
        self.assertIn("不交易", structured["current_action"])
        self.assertTrue(any("分钟阻断" in item for item in structured["forbidden_actions"]))

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
            {"items": [{"code": "600000", "as_of": "2026-07-16 09:40", "predicted_sell_zone": [10.2, 10.3], "predicted_buyback_max_price": 9.95}]},
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
            {"items": [{"code": "600000", "as_of": "2026-07-16 09:41", "predicted_sell_zone": [10.2, 10.3], "predicted_buyback_max_price": 9.95}]},
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
        self.assertEqual(card["capital_plan"]["status"], "waiting_minute_confirmation")
        self.assertIn("分钟二次确认未通过", card["capital_plan"]["status_label"])

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

    def test_stale_reverse_t_forecast_does_not_use_intraday_zone(self) -> None:
        item = intraday_item(reverse_status="watch")
        item["reverse_t_plan"]["current_reference_zone"] = [10.0, 10.02]
        item["reverse_t_plan"]["current_reference_buyback_max_price"] = 9.82
        item["reverse_t_plan"]["current_reference_reason"] = "锚定现价10.00生成。"
        item["reverse_t_plan"]["current_reference_required_gap_pct"] = 1.8
        report = build_report(
            {"items": [item]},
            portfolio_result(),
            t_result(),
            None,
            None,
            {
                "items": [
                    {
                        "code": "600000",
                        "status": "watch",
                        "as_of": "2026-07-16 15:00",
                        "predicted_sell_zone": [9.82, 9.84],
                        "predicted_buyback_max_price": 9.61,
                    }
                ]
            },
            generated_at=datetime(2026, 7, 17, 10, 20, 0),
        )

        card = report["cards"][0]
        levels = card["price_levels"]
        actions = {row["action"]: row for row in card["price_action_table"]["rows"]}

        self.assertIsNone(levels["reverse_t_sell_zone"])
        self.assertIsNone(levels["reverse_t_buyback_max_price"])
        self.assertEqual(levels["reverse_t_intraday_reference_zone"], [10.2, 10.3])
        self.assertEqual(levels["reverse_t_intraday_reference_buyback_max_price"], 10.0)
        self.assertEqual(levels["reverse_t_current_reference_zone"], [10.0, 10.02])
        self.assertEqual(levels["reverse_t_current_reference_buyback_max_price"], 9.82)
        self.assertEqual(levels["reverse_t_current_reference_required_gap_pct"], 1.8)
        self.assertIn("锚定现价", levels["reverse_t_current_reference_reason"])
        self.assertEqual(levels["reverse_t_zone_source"], "forecast_stale")
        self.assertTrue(levels["reverse_t_forecast_stale"])
        self.assertNotIn("反T卖出", actions)
        self.assertNotIn("反T回补", actions)

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
        self.assertEqual(card["decision_mode"]["mode"], "observe_only")
        self.assertEqual(card["decision_mode"]["label"], "只观察")
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
        self.assertEqual(card["decision_mode"]["mode"], "blocked")
        self.assertEqual(card["decision_mode"]["label"], "禁止决策")
        self.assertIn("日线数量 8 少于 20", card["decision_mode"]["reason"])
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
        self.assertEqual(card["decision_mode"]["mode"], "tradable")
        self.assertEqual(card["decision_mode"]["label"], "可人工确认")
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
