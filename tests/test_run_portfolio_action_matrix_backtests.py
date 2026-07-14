import csv
import tempfile
import unittest
from pathlib import Path

from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml
from tools.run_portfolio_action_matrix_backtests import apply_stop_loss_assumption, build_portfolio_report


ROOT = Path(__file__).resolve().parents[1]


def write_daily_bars(path: Path, code: str, closes: list[float]) -> None:
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
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not exists:
            writer.writeheader()
        previous = closes[0]
        for index, close in enumerate(closes, start=1):
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


class RunPortfolioActionMatrixBacktestsTest(unittest.TestCase):
    def test_applies_stop_loss_assumption_without_mutating_source(self) -> None:
        position = load_yaml(ROOT / "templates/position.example.yaml")
        position["entry"]["entry_price"] = 10.0
        position["risk"]["stop_loss_price"] = None

        result = apply_stop_loss_assumption(position, 12.0)

        self.assertIsNone(position["risk"]["stop_loss_price"])
        self.assertEqual(result["risk"]["stop_loss_price"], 8.8)

    def test_builds_portfolio_report_and_collects_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
            template = load_yaml(ROOT / "templates/position.example.yaml")
            first = template.copy()
            first["stock"] = {**template["stock"], "code": "600000", "name": "测试一"}
            first["entry"] = {**template["entry"], "entry_price": 10.0, "position_pct_of_total_assets": 5.0}
            first["risk"] = {**template["risk"], "stop_loss_price": None}
            second = template.copy()
            second["stock"] = {**template["stock"], "code": "000001", "name": "测试二"}
            second["entry"] = {**template["entry"], "entry_price": 5.0, "position_pct_of_total_assets": 4.0}
            second["risk"] = {**template["risk"], "stop_loss_price": 4.5}
            first_path = base / "positions" / "first.yaml"
            missing_path = base / "positions" / "missing.yaml"
            write_yaml(first_path, first)
            write_yaml(base / "positions" / "second.yaml", second)
            bars_path = base / "daily_bars.csv"
            closes = [10 + index * 0.02 for index in range(30)]
            write_daily_bars(bars_path, "600000", closes)

            report = build_portfolio_report(
                position_paths=[first_path, missing_path],
                daily_bars=bars_path,
                profile=profile,
                horizons=[1, 5],
                min_history=20,
                stop_loss_pct_from_entry=10.0,
            )

        self.assertEqual(report["position_count"], 2)
        self.assertEqual(report["backtested_count"], 1)
        self.assertEqual(report["error_count"], 1)
        self.assertEqual(report["items"][0]["stop_loss_price"], 9.0)


if __name__ == "__main__":
    unittest.main()
