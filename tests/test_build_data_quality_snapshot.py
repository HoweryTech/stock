import csv
import json
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from tools.build_data_quality_snapshot import build_report, classify_market_session, dynamic_consistency_diff_pct, render_markdown
from tools.new_trade_plan import write_yaml


def write_position(path: Path, code: str, name: str = "测试股票") -> None:
    write_yaml(path, {"stock": {"code": code, "name": name}})


def write_daily(path: Path, rows: dict[str, list[str]]) -> None:
    fields = ["trade_date", "code", "close"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for code, dates in rows.items():
            for date in dates:
                writer.writerow({"trade_date": date, "code": code, "close": 10.0})


def write_minutes(path: Path, code: str, count: int, latest: str) -> None:
    latest_dt = datetime.strptime(latest, "%Y-%m-%d %H:%M")
    first_dt = latest_dt - timedelta(minutes=count - 1)
    bars = [
        {
            "timestamp": (first_dt + timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M"),
            "code": code,
            "open": 10.0,
            "close": 10.0,
            "high": 10.1,
            "low": 9.9,
        }
        for index in range(count)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": code, "bars": bars}), encoding="utf-8")


class DataQualitySnapshotTest(unittest.TestCase):
    def test_classifies_market_sessions(self) -> None:
        pre_market = classify_market_session(datetime(2026, 7, 16, 8, 50, 0))
        trading = classify_market_session(datetime(2026, 7, 16, 10, 0, 0))
        lunch = classify_market_session(datetime(2026, 7, 16, 12, 0, 0))
        weekend = classify_market_session(datetime(2026, 7, 18, 10, 0, 0))

        self.assertEqual(pre_market["phase"], "pre_market")
        self.assertFalse(pre_market["live_quote_required"])
        self.assertEqual(trading["phase"], "continuous_trading")
        self.assertTrue(trading["live_quote_required"])
        self.assertEqual(lunch["phase"], "lunch_break")
        self.assertFalse(lunch["intraday_execution_window"])
        self.assertEqual(weekend["phase"], "non_trading_day")

    def test_builds_usable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 16)] + ["2026-07-16"]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 10.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 5, 0),
                min_daily_bars=10,
            )

        self.assertEqual(report["usable_count"], 1)
        self.assertEqual(report["trust_counts"], {"high": 1})
        self.assertEqual(report["items"][0]["overall_status"], "usable")
        self.assertEqual(report["items"][0]["data_trust"]["level"], "high")
        self.assertTrue(report["items"][0]["data_trust"]["intraday_decision_allowed"])
        self.assertEqual(report["items"][0]["source_consistency"]["status"], "pass")
        self.assertEqual(report["items"][0]["market_session"]["phase"], "continuous_trading")
        self.assertIn("持仓数据质量快照", render_markdown(report))

    def test_detects_stale_quote_and_minute_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 22)]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-14 15:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 120.0, "latest_price": 10.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 0, 0),
                max_minute_age_hours=30.0,
            )

        item = report["items"][0]
        self.assertEqual(item["overall_status"], "stale")
        self.assertEqual(item["data_trust"]["level"], "low")
        self.assertFalse(item["data_trust"]["intraday_decision_allowed"])
        self.assertEqual(item["quote"]["status"], "stale")
        self.assertEqual(item["minute"]["status"], "stale")
        self.assertTrue(item["warnings"])

    def test_marks_previous_day_minutes_stale_during_execution_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 16)] + ["2026-07-16"]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-15 15:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 9.7}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 0, 0),
                min_daily_bars=10,
                max_minute_age_hours=30.0,
            )

        item = report["items"][0]
        self.assertEqual(item["overall_status"], "stale")
        self.assertEqual(item["minute"]["status"], "stale")
        self.assertIn("盘中执行窗口要求当天分钟线", item["minute"]["message"])
        minute_check = item["source_consistency"]["checks"][0]
        self.assertEqual(minute_check["source"], "minute")
        self.assertEqual(minute_check["status"], "reference_only")
        self.assertEqual(item["source_consistency"]["status"], "skipped")

    def test_next_trading_day_open_blocks_previous_day_minute_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 18)]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-17 15:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 10.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 20, 9, 31, 0),
                min_daily_bars=10,
                max_minute_age_hours=999.0,
            )

        item = report["items"][0]
        self.assertEqual(item["market_session"]["phase"], "continuous_trading")
        self.assertEqual(item["overall_status"], "stale")
        self.assertEqual(item["minute"]["status"], "stale")
        self.assertIn("盘中执行窗口要求当天分钟线", item["minute"]["message"])
        self.assertFalse(item["data_trust"]["intraday_decision_allowed"])

    def test_next_trading_day_premarket_keeps_previous_close_as_wait_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 18)]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-17 15:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 10.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 20, 8, 50, 0),
                min_daily_bars=10,
                max_minute_age_hours=999.0,
            )

        item = report["items"][0]
        self.assertEqual(item["market_session"]["phase"], "pre_market")
        self.assertNotIn("盘中执行窗口要求当天分钟线", item["minute"]["message"])

    def test_marks_new_listing_daily_bars_as_limited_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-001248.yaml"
            write_position(position, "001248")
            write_daily(base / "daily.csv", {"001248": ["2026-07-15"] * 8})
            write_minutes(base / "minutes/001248.json", "001248", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "001248", "quote": {"quote_lag_seconds": 3.0, "latest_price": 10.0}}]}

            report = build_report([position], snapshot, base / "daily.csv", base / "minutes", as_of=datetime(2026, 7, 16, 10, 5, 0))

        item = report["items"][0]
        self.assertEqual(item["overall_status"], "limited_history")
        self.assertEqual(item["data_trust"]["level"], "medium")
        self.assertTrue(item["data_trust"]["intraday_decision_allowed"])
        self.assertEqual(item["daily"]["status"], "limited_history")
        self.assertFalse(item["blockers"])
        self.assertTrue(item["warnings"])

    def test_detects_too_few_daily_bars_as_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-001248.yaml"
            write_position(position, "001248")
            write_daily(base / "daily.csv", {"001248": ["2026-07-15"] * 4})
            write_minutes(base / "minutes/001248.json", "001248", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "001248", "quote": {"quote_lag_seconds": 3.0, "latest_price": 10.0}}]}

            report = build_report([position], snapshot, base / "daily.csv", base / "minutes", as_of=datetime(2026, 7, 16, 10, 5, 0))

        item = report["items"][0]
        self.assertEqual(item["overall_status"], "insufficient")
        self.assertEqual(item["daily"]["status"], "insufficient")
        self.assertTrue(item["blockers"])

    def test_missing_quote_price_blocks_intraday_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 16)] + ["2026-07-16"]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 5, 0),
                min_daily_bars=10,
            )

        item = report["items"][0]
        self.assertEqual(item["quote"]["status"], "missing")
        self.assertEqual(item["overall_status"], "missing")
        self.assertEqual(item["data_trust"]["level"], "low")

    def test_previous_trading_day_daily_bar_is_labeled_as_trend_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 16)]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 10.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 5, 0),
                min_daily_bars=10,
            )

        daily_check = report["items"][0]["source_consistency"]["checks"][1]
        self.assertEqual(daily_check["status"], "reference_only")
        self.assertIn("日线为上一交易日 2026-07-15", daily_check["message"])
        self.assertIn("实时判断以现价和分钟线为准", daily_check["message"])
        self.assertNotIn("不一致", daily_check["message"])

    def test_stale_daily_with_fresh_quote_is_medium_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-06-{day:02d}" for day in range(1, 22)]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 10.0}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 5, 0),
            )

        item = report["items"][0]
        self.assertEqual(item["overall_status"], "stale")
        self.assertEqual(item["data_trust"]["level"], "medium")
        self.assertFalse(item["data_trust"]["intraday_decision_allowed"])

    def test_price_conflict_is_low_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            position = base / "positions/POS-600000.yaml"
            write_position(position, "600000")
            write_daily(base / "daily.csv", {"600000": [f"2026-07-{day:02d}" for day in range(1, 22)]})
            write_minutes(base / "minutes/600000.json", "600000", 130, "2026-07-16 10:00")
            snapshot = {"items": [{"code": "600000", "quote": {"quote_lag_seconds": 5.0, "latest_price": 11.5}}]}

            report = build_report(
                [position],
                snapshot,
                base / "daily.csv",
                base / "minutes",
                as_of=datetime(2026, 7, 16, 10, 5, 0),
                min_daily_bars=10,
                max_consistency_diff_pct=1.0,
            )

        item = report["items"][0]
        self.assertEqual(item["source_consistency"]["status"], "conflict")
        self.assertEqual(item["data_trust"]["level"], "low")
        self.assertIn("一致性", item["data_trust"]["reasons"][0])

    def test_dynamic_consistency_threshold_allows_more_tick_noise_for_low_price_names(self) -> None:
        self.assertEqual(dynamic_consistency_diff_pct(3.0, 3.04, 1.0), 1.4)
        self.assertEqual(dynamic_consistency_diff_pct(60.0, 60.5, 1.0), 0.8)


if __name__ == "__main__":
    unittest.main()
