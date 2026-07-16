import unittest
from datetime import datetime

from tools.build_realtime_decision_cards import build_report, render_markdown


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


def t_result(code: str = "600000", *, conclusion: str = "watch_only", blockers=None) -> dict:
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
                    "warnings": [],
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
        self.assertIn("禁止买入、补仓、做T", card["decision"]["action_steps"][0])
        self.assertTrue(any("交易/卖出" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("证券代码输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("卖出数量输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("卖出价格输入" in step for step in card["decision"]["action_steps"]))
        self.assertTrue(any("做T阻断价" in step for step in card["decision"]["action_steps"]))
        self.assertIn("距离止损不足1%。", card["blockers"])

    def test_positive_t_candidate_is_watch_only(self) -> None:
        report = build_report(
            {"items": [intraday_item()]},
            portfolio_result(),
            t_result(conclusion="positive_t_candidate"),
            {"items": [{"path": "positions/POS-600000.yaml", "stock": {"code": "600000"}, "weak_rule_count": 0}]},
            None,
            None,
            generated_at=datetime(2026, 7, 16, 9, 31, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "positive_t_watch")
        self.assertEqual(card["decision"]["action"], "watch_positive_t_only")
        self.assertFalse(card["decision"]["execution_allowed"])
        self.assertEqual(card["price_levels"]["near_stop_block_price"], 9.596)

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
        self.assertEqual(card["market_context"]["technical_label"], "bearish")
        self.assertIn("多周期技术指标偏弱，本轮禁止补仓和做T。", card["blockers"])
        self.assertIn("[技术指标] bearish", "\n".join(card["evidence"]))
        self.assertIn("技术判断", content)


if __name__ == "__main__":
    unittest.main()
