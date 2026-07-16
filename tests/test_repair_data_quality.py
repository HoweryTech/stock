import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tools.new_trade_plan import write_yaml
from tools.repair_data_quality import build_plan, execute_plan, fetch_minute_cache_for_code, render_markdown


def write_position(path: Path, code: str) -> None:
    write_yaml(path, {"stock": {"code": code, "name": f"股票{code}"}})


def quality_item(code: str, *, quote: str = "usable", daily: str = "usable", minute: str = "usable") -> dict:
    return {
        "code": code,
        "name": f"股票{code}",
        "overall_status": "usable",
        "quote": {"status": quote, "message": f"quote {quote}"},
        "daily": {"status": daily, "message": f"daily {daily}"},
        "minute": {"status": minute, "message": f"minute {minute}"},
        "blockers": [],
        "warnings": [],
    }


def make_args(base: Path) -> Namespace:
    return Namespace(
        total_assets=100000.0,
        profile=str(base / "profile.yaml"),
        daily_bars=str(base / "daily.csv"),
        daily_metadata_output=str(base / "daily.fetch.json"),
        minute_cache_dir=str(base / "minutes"),
        minute_begin="20260101",
        minute_end="20260716",
        intraday_output=str(base / "intraday.json"),
        intraday_markdown_output=str(base / "intraday.md"),
        quality_output=str(base / "quality.json"),
        quality_markdown_output=str(base / "quality.md"),
        max_stale_seconds=60,
        max_quote_lag_seconds=60.0,
        min_daily_bars=20,
        max_daily_age_days=5,
        min_minute_bars=120,
        max_minute_age_hours=30.0,
        fetch_datalen=320,
        request_interval_seconds=0,
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
    )


class RepairDataQualityTest(unittest.TestCase):
    def test_build_plan_groups_repair_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position_a = base / "positions/POS-600000.yaml"
            position_b = base / "positions/POS-001248.yaml"
            write_position(position_a, "600000")
            write_position(position_b, "001248")
            quality = {
                "generated_at": "2026-07-16T10:00:00+08:00",
                "position_count": 2,
                "status_counts": {"insufficient": 1, "stale": 1},
                "items": [
                    quality_item("600000", quote="stale", minute="stale"),
                    quality_item("001248", daily="insufficient", minute="stale"),
                ],
            }

            plan = build_plan(quality, [position_a, position_b])
            content = render_markdown({"generated_at": "now", "plan": plan, "execution": None})

        self.assertEqual(plan["action_count"], 3)
        self.assertEqual(plan["actions"][0]["type"], "refresh_intraday_snapshot")
        self.assertEqual(plan["actions"][1]["codes"], ["001248"])
        self.assertEqual(plan["actions"][2]["codes"], ["001248", "600000"])
        self.assertIn("数据质量修复计划", content)

    def test_fetch_minute_cache_prefers_sina(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir)
            bars = [{"timestamp": "2026-07-16 10:00", "open": 10, "high": 10, "low": 10, "close": 10}]
            with patch("tools.repair_data_quality.fetch_sina_minute_bars", return_value=bars):
                result = fetch_minute_cache_for_code("600000", cache_dir, "20260101", "20260716")

            saved = json.loads((cache_dir / "600000.json").read_text(encoding="utf-8"))

        self.assertEqual(result["source"], "sina_5minute")
        self.assertEqual(result["latest_timestamp"], "2026-07-16 10:00")
        self.assertEqual(saved["bars"], bars)

    def test_execute_plan_runs_selected_repairs_and_rebuilds_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            args = make_args(base)
            plan = {
                "actions": [
                    {"type": "fetch_daily_bars", "codes": ["600000"]},
                    {"type": "refresh_minute_cache", "codes": ["600000"]},
                ]
            }
            with patch("tools.repair_data_quality.refresh_daily_bars", return_value={"errors": []}) as daily:
                with patch("tools.repair_data_quality.refresh_minute_cache", return_value={"items": [], "errors": []}) as minute:
                    with patch("tools.repair_data_quality.rebuild_quality", return_value={"usable_count": 1, "status_counts": {"usable": 1}}):
                        result = execute_plan(plan, args, [position])

        self.assertEqual(len(result["executed_actions"]), 2)
        daily.assert_called_once()
        minute.assert_called_once()
        self.assertEqual(result["refreshed_quality"]["status_counts"], {"usable": 1})


if __name__ == "__main__":
    unittest.main()
