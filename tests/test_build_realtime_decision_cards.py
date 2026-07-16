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


class RealtimeDecisionCardsTest(unittest.TestCase):
    def test_hard_t_blocker_takes_exit_risk_priority(self) -> None:
        report = build_report(
            {"items": [intraday_item()]},
            portfolio_result(),
            t_result(blockers=[{"code": "near_stop_loss", "message": "距离止损不足1%。"}]),
            None,
            {"items": [{"code": "600000", "verdict": "insufficient_sample", "verdict_label": "样本不足，禁止执行"}]},
            None,
            generated_at=datetime(2026, 7, 16, 9, 30, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "exit_risk_review")
        self.assertEqual(card["decision"]["action"], "create_exit_or_risk_review")
        self.assertFalse(card["decision"]["execution_allowed"])
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
                        "blockers": ["日线数量 8 少于 20。"],
                        "warnings": [],
                    }
                ]
            },
            generated_at=datetime(2026, 7, 16, 9, 33, 0),
        )

        card = report["cards"][0]

        self.assertEqual(card["state"], "data_insufficient")
        self.assertEqual(card["decision"]["action"], "complete_data_before_decision")
        self.assertIn("日线数量 8 少于 20。", card["blockers"])
        self.assertEqual(card["market_context"]["data_quality_status"], "insufficient")


if __name__ == "__main__":
    unittest.main()
