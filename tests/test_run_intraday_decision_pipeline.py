import csv
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml
from tools.run_intraday_decision_pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]


def write_daily_bars(path: Path) -> None:
    fields = [
        "trade_date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume",
        "turnover",
        "turnover_rate",
        "is_limit_up",
        "is_limit_down",
        "is_suspended",
        "adjust_type",
        "data_source",
        "updated_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        previous = 10.0
        for index in range(1, 26):
            close = 10 + index * 0.05
            writer.writerow(
                {
                    "trade_date": f"2026-07-{index:02d}",
                    "code": "600000",
                    "open": previous,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "pre_close": previous,
                    "volume": 1000000,
                    "turnover": 10000000,
                    "turnover_rate": 1.0,
                    "is_limit_up": "false",
                    "is_limit_down": "false",
                    "is_suspended": "false",
                    "adjust_type": "qfq",
                    "data_source": "test",
                    "updated_at": "2026-07-16",
                }
            )
            previous = close


def write_minute_bars(path: Path) -> None:
    bars = [
        {
            "timestamp": f"2026-07-16 {9 + (index // 60):02d}:{index % 60:02d}",
            "code": "600000",
            "open": 11.2,
            "high": 11.3,
            "low": 11.1,
            "close": 11.25,
        }
        for index in range(130)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "浦发银行", "bars": bars}), encoding="utf-8")


def make_args(base: Path) -> Namespace:
    return Namespace(
        positions=[str(base / "positions/*.yaml")],
        profile=str(base / "investment-profile.yaml"),
        daily_bars=str(base / "daily_bars.csv"),
        total_assets=100000.0,
        max_stale_seconds=60,
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        minimum_net_profit=5.0,
        cost_model_verified=False,
        max_reverse_t_position_ratio=50.0,
        max_position_pct=10.0,
        warning_position_pct=None,
        position_limit_verified=False,
        position_near_stop_pct=None,
        t_near_stop_pct=None,
        short_window=5,
        mid_window=20,
        pullback_pct=3.0,
        overextended_pct=6.0,
        min_spread_pct=1.2,
        minute_cache_dir=str(base / "minute-bars"),
        max_quote_lag_seconds=60.0,
        min_daily_bars=20,
        max_daily_age_days=5,
        min_minute_bars=120,
        max_minute_age_hours=999.0,
        max_consistency_diff_pct=1.0,
        action_backtests=str(base / "missing-action-backtests.json"),
        reverse_t_backtest=str(base / "missing-reverse-backtest.json"),
        reverse_t_forecast=str(base / "missing-reverse-forecast.json"),
        technical_indicators=str(base / "missing-technical-indicators.json"),
        intraday_output=str(base / "metadata/intraday.json"),
        intraday_markdown_output=str(base / "reports/intraday.md"),
        portfolio_check_output=str(base / "metadata/portfolio.json"),
        t_opportunities_output=str(base / "metadata/t.json"),
        data_quality_output=str(base / "metadata/data-quality.json"),
        data_quality_markdown_output=str(base / "reports/data-quality.md"),
        decision_cards_output=str(base / "metadata/cards.json"),
        decision_cards_markdown_output=str(base / "reports/cards.md"),
        metadata_output=str(base / "metadata/pipeline.json"),
        json=False,
    )


class RunIntradayDecisionPipelineTest(unittest.TestCase):
    def test_run_pipeline_writes_all_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
            profile["risk"]["position_limits_confirmed"] = True
            write_yaml(base / "investment-profile.yaml", profile)
            position = load_yaml(ROOT / "templates/position.example.yaml")
            position["stock"]["code"] = "600000"
            position["stock"]["name"] = "浦发银行"
            position["entry"]["shares"] = 1000
            position["entry"]["entry_price"] = 10.0
            position["entry"]["position_pct_of_total_assets"] = 10.0
            position["tracking"]["current_price"] = 11.25
            position["risk"]["stop_loss_price"] = 9.5
            write_yaml(base / "positions/POS-600000.yaml", position)
            write_daily_bars(base / "daily_bars.csv")
            write_minute_bars(base / "minute-bars/600000.json")
            fake_snapshot = {
                "generated_at": "2026-07-16T09:30:00+08:00",
                "success_count": 1,
                "position_count": 1,
                "errors": [],
                "items": [
                    {
                        "code": "600000",
                        "name": "浦发银行",
                        "state": "observe",
                        "quote": {"latest_price": 11.25, "change_pct": 1.0, "quote_lag_seconds": 3.0},
                        "position": {"shares": 1000, "entry_price": 10.0, "market_value": 11250.0, "unrealized_pnl": 1250.0, "return_pct": 12.5},
                        "technicals": {"ma5": 11.15, "ma20": 10.75},
                        "capital_flow": {"main_net_inflow_ratio_pct": 1.0},
                        "signals": [],
                        "reverse_t_plan": {"status": "watch"},
                        "reduction_plan": {"status": "within_limit"},
                    }
                ],
            }

            with patch("tools.run_intraday_decision_pipeline.build_snapshot", return_value=fake_snapshot):
                metadata = run_pipeline(make_args(base))

            self.assertEqual(metadata["steps"]["intraday_snapshot"]["success_count"], 1)
            self.assertEqual(metadata["steps"]["decision_cards"]["card_count"], 1)
            self.assertTrue((base / "metadata/intraday.json").exists())
            self.assertTrue((base / "metadata/portfolio.json").exists())
            self.assertTrue((base / "metadata/t.json").exists())
            self.assertTrue((base / "metadata/data-quality.json").exists())
            self.assertTrue((base / "metadata/cards.json").exists())
            self.assertTrue((base / "reports/data-quality.md").exists())
            self.assertTrue((base / "reports/cards.md").exists())
            cards = load_yaml(base / "metadata/cards.json")

        self.assertEqual(cards["card_count"], 1)
        self.assertIn(cards["cards"][0]["state"], {"observe", "positive_t_watch", "hold_no_add"})
        self.assertEqual(cards["cards"][0]["market_context"]["data_quality_status"], "usable")


if __name__ == "__main__":
    unittest.main()
