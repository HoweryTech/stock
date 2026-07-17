import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.apply_manual_trade import apply_manual_trade
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


def args(base: Path, **overrides) -> Namespace:
    defaults = {
        "positions": [str(base / "positions/*.yaml")],
        "code": "000725",
        "side": "sell",
        "shares": 100.0,
        "price": 6.32,
        "total_assets": 25480.0,
        "occurred_at": "2026-07-16T10:00:00+08:00",
        "note": "manual test",
        "trade_intent": "",
        "linked_trade_id": "",
        "source": "cli",
        "commission_rate": 0.0003,
        "minimum_commission": 5.0,
        "stamp_duty_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def write_position(path: Path) -> None:
    write_yaml(
        path,
        {
            "position": {"id": "POS-000725", "status": "normal"},
            "stock": {"code": "000725", "name": "京东方A"},
            "entry": {"entry_price": 9.115, "shares": 200.0, "position_pct_of_total_assets": 5.3611, "planned_buy_price": 9.115},
            "tracking": {"current_price": 6.83},
            "broker_import_snapshot": {"available_shares": 200.0, "market_value": 1366.0},
        },
    )


class ApplyManualTradeTest(unittest.TestCase):
    def test_sell_reduces_position_and_records_realized_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            result, _ = apply_manual_trade(args(base))
            updated = load_yaml(path)

        self.assertEqual(updated["entry"]["shares"], 100.0)
        self.assertEqual(updated["broker_import_snapshot"]["available_shares"], 100.0)
        self.assertEqual(updated["manual_trade_history"][-1]["side"], "sell")
        self.assertAlmostEqual(updated["manual_trade_history"][-1]["fees"]["total_fees"], 5.3223, places=4)
        self.assertAlmostEqual(updated["manual_trade_history"][-1]["realized_pnl"], -284.8223, places=4)
        self.assertEqual(result["position"]["shares"], 100.0)

    def test_buy_updates_weighted_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            apply_manual_trade(args(base, side="buy", shares=100.0, price=6.11))
            updated = load_yaml(path)

        self.assertEqual(updated["entry"]["shares"], 300.0)
        self.assertAlmostEqual(updated["entry"]["entry_price"], 8.13, places=4)
        self.assertEqual(updated["manual_trade_history"][-1]["side"], "buy")

    def test_records_reverse_t_trade_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            apply_manual_trade(args(base, trade_intent="reverse_t_open"))
            updated = load_yaml(path)

        self.assertEqual(updated["manual_trade_history"][-1]["trade_intent"], "reverse_t_open")

    def test_records_positive_t_trade_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            apply_manual_trade(args(base, side="buy", shares=100.0, price=6.10, trade_intent="positive_t_open"))
            updated = load_yaml(path)

        self.assertEqual(updated["manual_trade_history"][-1]["trade_intent"], "positive_t_open")
        self.assertEqual(updated["manual_trade_history"][-1]["side"], "buy")

    def test_records_positive_t_close_trade_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            apply_manual_trade(args(base, side="buy", shares=100.0, price=6.10, trade_intent="positive_t_open"))
            opened = load_yaml(path)["manual_trade_history"][-1]
            apply_manual_trade(args(base, side="sell", shares=100.0, price=6.18, trade_intent="positive_t_close", linked_trade_id=opened["id"]))
            updated = load_yaml(path)

        self.assertEqual(updated["manual_trade_history"][-1]["trade_intent"], "positive_t_close")
        self.assertEqual(updated["manual_trade_history"][-1]["linked_trade_id"], opened["id"])

    def test_positive_t_close_requires_linked_open_buy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            with self.assertRaisesRegex(ValueError, "linked positive T open trade not found"):
                apply_manual_trade(args(base, side="sell", shares=100.0, price=6.18, trade_intent="positive_t_close", linked_trade_id="missing"))

    def test_reverse_t_close_records_closure_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            path = base / "positions/POS-000725.yaml"
            path.parent.mkdir()
            write_position(path)

            apply_manual_trade(args(base, trade_intent="reverse_t_open", price=6.32))
            opened = load_yaml(path)["manual_trade_history"][-1]
            apply_manual_trade(
                args(
                    base,
                    side="buy",
                    shares=100.0,
                    price=6.04,
                    trade_intent="reverse_t_close",
                    linked_trade_id=opened["id"],
                )
            )
            updated = load_yaml(path)

        closed = updated["manual_trade_history"][-1]
        closure = closed["reverse_t_closure"]
        self.assertEqual(closed["trade_intent"], "reverse_t_close")
        self.assertEqual(closure["sell_trade_id"], opened["id"])
        self.assertEqual(closure["buy_trade_id"], closed["id"])
        self.assertAlmostEqual(closure["gross_profit"], 28.0, places=4)
        self.assertAlmostEqual(closure["fees"]["total_fees"], 10.3283, places=4)
        self.assertAlmostEqual(closure["net_profit"], 17.6717, places=4)
        self.assertEqual(updated["tracking"]["latest_reverse_t_closure"]["buy_trade_id"], closed["id"])


if __name__ == "__main__":
    unittest.main()
