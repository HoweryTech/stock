import unittest
from pathlib import Path

from tools.backtest_holding_action_matrix import build_report
from tools.risk_check import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def bars_from_closes(closes: list[float]) -> list[dict]:
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes, start=1):
        rows.append(
            {
                "trade_date": f"2026-01-{index:02d}",
                "code": "600000",
                "open": previous,
                "high": round(close * 1.02, 2),
                "low": round(close * 0.98, 2),
                "close": close,
                "turnover": 100000000,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_suspended": False,
            }
        )
        previous = close
    return rows


class BacktestHoldingActionMatrixTest(unittest.TestCase):
    def test_groups_trend_states_and_rule_triggers(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        position = load_yaml(ROOT / "templates/position.example.yaml")
        position["stock"]["code"] = "600000"
        position["stock"]["name"] = "测试股票"
        position["entry"]["entry_price"] = 10.0
        position["entry"]["position_pct_of_total_assets"] = 5.0
        position["risk"]["stop_loss_price"] = 8.5
        closes = [
            10.0, 10.1, 10.2, 10.3, 10.4,
            10.5, 10.6, 10.7, 10.8, 10.9,
            11.0, 11.1, 11.2, 11.3, 11.4,
            11.2, 11.0, 10.8, 10.6, 10.4,
            10.2, 10.0, 9.8, 9.6, 9.4,
        ]

        report = build_report(
            position=position,
            bars=bars_from_closes(closes),
            profile=profile,
            horizons=[1, 3, 5],
            min_history=20,
        )

        self.assertGreater(report["event_count"], 0)
        self.assertEqual(report["source"]["risk"]["stop_loss_price"], 8.5)
        self.assertIn("trend_weakened", report["summary_by_trend_state"])
        self.assertIn("close_lt_ma20", report["summary_by_rule_trigger"])
        recent = report["recent_events"][-1]
        self.assertIn("5", recent["future"])
        self.assertIn("return_pct", recent["future"]["1"])


if __name__ == "__main__":
    unittest.main()
