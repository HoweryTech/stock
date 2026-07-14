import unittest

from tools.build_holding_action_draft import classify_holding


def position(position_pct: float = 5.0) -> dict:
    return {"entry": {"position_pct_of_total_assets": position_pct}}


def result(*, blockers=None, market_setup="no_clear_t_setup", return_mid=-5.0, close=9.0, ma_mid=10.0) -> dict:
    return {
        "stock_code": "600000",
        "stock_name": "测试股票",
        "conclusion": "blocked",
        "market_setup": market_setup,
        "blockers": [{"code": code, "message": code} for code in (blockers or [])],
        "calculations": {
            "trade_date": "2026-07-13",
            "latest_close": close,
            "ma_mid": ma_mid,
            "return_mid_pct": return_mid,
            "avg_range_pct": 3.0,
        },
    }


class BuildHoldingActionDraftTest(unittest.TestCase):
    def test_prioritizes_limit_down_review(self) -> None:
        item = classify_holding(position(), result(blockers=["limit_down", "missing_price_or_stop_loss"]))
        self.assertEqual(item["action"], "exit_risk_review")
        self.assertEqual(item["priority"], 1)
        self.assertFalse(item["add_allowed"])

    def test_prioritizes_position_reduction(self) -> None:
        item = classify_holding(position(12.0), result(blockers=["stock_position_limit_exceeded"]))
        self.assertEqual(item["action"], "risk_reduction_review")
        self.assertEqual(item["priority"], 2)

    def test_weak_trend_blocks_adding(self) -> None:
        item = classify_holding(position(), result())
        self.assertEqual(item["action"], "hold_no_add")
        self.assertTrue(any("20日均线" in rule for rule in item["unlock_conditions"]))

    def test_financial_flags_raise_review_priority(self) -> None:
        research = {"financial_review": {"flags": [{"code": "profit_decline", "message": "利润下降。"}]}, "risk_review": {}}
        item = classify_holding(position(), result(return_mid=3.0, close=11.0, ma_mid=10.0), research)
        self.assertEqual(item["action"], "fundamental_review")
        self.assertEqual(item["priority"], 3)
        self.assertIn("利润下降。", item["reasons"])


if __name__ == "__main__":
    unittest.main()
