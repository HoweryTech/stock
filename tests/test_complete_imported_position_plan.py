import csv
import tempfile
import unittest
from pathlib import Path

from tools.complete_imported_position_plan import build_report, draft_for_position
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def write_bars(path: Path, code: str = "600000") -> None:
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
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        previous = 10.0
        for index in range(1, 26):
            close = round(10 + index * 0.05, 2)
            writer.writerow(
                {
                    "trade_date": f"2026-01-{index:02d}",
                    "code": code,
                    "open": previous,
                    "high": round(close * 1.02, 2),
                    "low": round(close * 0.98, 2),
                    "close": close,
                    "pre_close": previous,
                    "volume": 100000000,
                    "turnover": 100000000,
                    "turnover_rate": 1.0,
                    "is_limit_up": "false",
                    "is_limit_down": "false",
                    "is_suspended": "false",
                    "adjust_type": "qfq",
                    "data_source": "test",
                    "updated_at": "2026-02-01",
                }
            )
            previous = close


def imported_position() -> dict:
    position = load_yaml(ROOT / "templates/position.example.yaml")
    position["position"]["source_trade_plan_id"] = "IMPORT-EASTMONEY"
    position["stock"]["code"] = "600000"
    position["stock"]["name"] = "测试股票"
    position["entry"]["entry_price"] = 10.0
    position["entry"]["position_pct_of_total_assets"] = 12.0
    position["risk"]["stop_loss_price"] = None
    position["risk"]["take_profit_conditions"] = []
    position["risk"]["invalidation_conditions"] = []
    position["strategy"]["source"] = "imported_holding"
    position["strategy"]["buy_reason"] = "东方财富持仓导入，原始买入理由待补充。"
    position["strategy"]["key_evidence"] = []
    position["tracking"]["current_price"] = 10.5
    return position


class CompleteImportedPositionPlanTest(unittest.TestCase):
    def test_drafts_missing_risk_plan_without_mutating_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            bars = base / "daily_bars.csv"
            write_bars(bars)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
            position = imported_position()

            item = draft_for_position(base / "position.yaml", position, profile, bars, stop_loss_pct_from_entry=12.0)

        self.assertIn("risk.stop_loss_price", item["missing_fields"])
        self.assertIn("strategy.buy_reason", item["missing_fields"])
        self.assertEqual(item["stop_loss_candidates"][0]["price"], 8.8)
        self.assertFalse(item["draft_plan"]["add_allowed"])
        self.assertIsNone(position["risk"]["stop_loss_price"])

    def test_build_report_sorts_risk_reduction_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            bars = base / "daily_bars.csv"
            write_bars(bars)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
            high_position = imported_position()
            low_position = imported_position()
            low_position["entry"]["position_pct_of_total_assets"] = 2.0
            high_path = base / "high.yaml"
            low_path = base / "low.yaml"
            write_yaml(high_path, high_position)
            write_yaml(low_path, low_position)

            report = build_report([low_path, high_path], profile, bars, stop_loss_pct_from_entry=10.0)

        self.assertEqual(report["position_count"], 2)
        self.assertEqual(report["needs_completion_count"], 2)
        self.assertEqual(report["items"][0]["status"], "risk_reduction_first")


if __name__ == "__main__":
    unittest.main()
