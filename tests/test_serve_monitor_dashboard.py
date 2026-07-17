import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.serve_monitor_dashboard import API_FILES, build_post_trade_tracking, dashboard_position_paths, handle_manual_trade, load_json, market_wait_refresh_status, monitor_status, recent_events, recent_flow_history


class ServeMonitorDashboardTest(unittest.TestCase):
    def test_recent_events_returns_newest_first(self) -> None:
        class FakePath:
            def exists(self):
                return True

            def read_text(self, encoding):
                return '\n'.join(json.dumps({"id": value}) for value in range(3))

        with patch("tools.serve_monitor_dashboard.EVENT_FILE", FakePath()):
            self.assertEqual([item["id"] for item in recent_events(2)], [2, 1])

    def test_recent_flow_history_reads_archives_and_latest(self) -> None:
        class FakeArchiveDir:
            def glob(self, pattern):
                return [Path("/tmp/snapshot-1.json")]

        def fake_load_json(path, retries=3, delay=0.05):
            return {
                "generated_at": "2026-07-17T09:30:00+08:00",
                "items": [
                    {
                        "code": "000001",
                        "name": "测试股",
                        "quote": {"latest_price": 10.0, "high": 10.2},
                        "capital_flow": {"main_net_inflow": 1000, "main_net_inflow_ratio_pct": 5.5},
                    }
                ],
            }

        class ExistingLatest:
            def exists(self):
                return False

        with (
            patch("tools.serve_monitor_dashboard.FLOW_HISTORY_FILE", ExistingLatest()),
            patch("tools.serve_monitor_dashboard.ARCHIVE_DIR", FakeArchiveDir()),
            patch("tools.serve_monitor_dashboard.API_FILES", {**API_FILES, "/api/snapshot": ExistingLatest()}),
            patch("tools.serve_monitor_dashboard.load_json", side_effect=fake_load_json),
        ):
            report = recent_flow_history(10)

        self.assertEqual(report["samples"][0]["code"], "000001")
        self.assertEqual(report["samples"][0]["main_net_inflow_ratio_pct"], 5.5)

    def test_monitor_status_without_pid(self) -> None:
        class MissingPath:
            def exists(self):
                return False

        with patch("tools.serve_monitor_dashboard.PID_FILE", MissingPath()):
            self.assertEqual(monitor_status(), {"running": False, "pid": None})

    def test_exposes_decision_cards_api(self) -> None:
        self.assertIn("/api/decision-cards", API_FILES)
        self.assertEqual(API_FILES["/api/decision-cards"].name, "realtime-decision-cards.json")

    def test_load_json_retries_transient_partial_write(self) -> None:
        class FlakyPath:
            def __init__(self):
                self.calls = 0

            def exists(self):
                return True

            def read_text(self, encoding):
                self.calls += 1
                return '{"ok": true}' if self.calls > 1 else '{"ok":'

        with patch("tools.serve_monitor_dashboard.time.sleep"):
            self.assertEqual(load_json(FlakyPath(), retries=2), {"ok": True})

    def test_market_wait_refresh_status_uses_snapshot_assets(self) -> None:
        def fake_load_json(path):
            if path == API_FILES["/api/snapshot"]:
                return {"total_assets": 25480.0}
            if path == API_FILES["/api/decision-cards"]:
                return {"cards": [{"code": "600000", "state": "market_wait"}]}
            return None

        with patch("tools.serve_monitor_dashboard.load_json", side_effect=fake_load_json):
            with patch("tools.serve_monitor_dashboard.datetime") as fake_datetime:
                fake_datetime.now.return_value.astimezone.return_value = __import__("datetime").datetime(2026, 7, 16, 9, 35, 0)
                report = market_wait_refresh_status()

        self.assertEqual(report["conclusion"], "refresh_due")
        self.assertIn("--total-assets 25480.0", report["refresh_command"]["shell"])

    def test_handle_manual_trade_updates_position_and_refreshes_outputs(self) -> None:
        def fake_load_json(path):
            if path == API_FILES["/api/snapshot"]:
                return {
                    "total_assets": 25480.0,
                    "items": [{"code": "000725", "name": "京东方Ａ", "quote": {"latest_price": 6.05}}],
                }
            if path == API_FILES["/api/decision-cards"]:
                return {"cards": [{"code": "000725", "state_label": "退出风险优先", "decision": {"action_label": "禁止追买", "next_step": "止损风险优先"}}]}
            return None

        with (
            patch("tools.serve_monitor_dashboard.load_json", side_effect=fake_load_json),
            patch(
                "tools.serve_monitor_dashboard.apply_manual_trade",
                return_value=(
                    {
                        "trade": {
                            "code": "000725",
                            "side": "sell",
                            "shares_after": 100,
                            "execution_quality_review": {"status": "needs_review", "score": 70, "checks": []},
                        },
                        "position": {"shares": 100},
                    },
                    None,
                ),
            ) as apply_trade,
            patch("tools.serve_monitor_dashboard.run_refresh_commands", return_value=[{"returncode": 0}]) as refresh,
        ):
            result = handle_manual_trade({"code": "000725", "side": "sell", "shares": 100, "price": 6.32})

        self.assertTrue(result["ok"])
        self.assertEqual(result["update"]["trade"]["code"], "000725")
        self.assertEqual(result["post_trade_tracking"]["refreshed_action"], "禁止追买")
        self.assertEqual(result["post_trade_tracking"]["execution_quality_review"]["status"], "needs_review")
        self.assertIn("少于200股", result["post_trade_tracking"]["warnings"][0])
        apply_trade.assert_called_once()
        called_args = apply_trade.call_args.args[0]
        self.assertTrue(called_args.positions)
        self.assertTrue(all(Path(path).is_absolute() for path in called_args.positions))
        refresh.assert_called_once_with(25480.0)

    def test_build_post_trade_tracking_includes_next_price_action(self) -> None:
        update = {
            "trade": {
                "id": "MANUAL-1",
                "code": "000725",
                "side": "buy",
                "trade_intent": "reverse_t_close",
                "price": 5.99,
                "shares": 100,
                "shares_after": 200,
                "fees": {"total_fees": 5.0},
                "reverse_t_closure": {"next_plan": "反T闭环完成。", "net_profit": 22.0},
                "execution_quality_review": {"status": "good", "score": 95, "checks": [{"code": "closure_profit_target"}]},
            },
            "position": {"shares": 200, "entry_price": 7.58},
        }
        snapshot = {"items": [{"code": "000725", "name": "京东方Ａ", "quote": {"latest_price": 6.06}}]}
        decision_report = {
            "cards": [
                {
                    "code": "000725",
                    "state_label": "持有观察",
                    "decision": {"action_label": "不买不卖", "next_step": "等待新信号"},
                    "price_action_table": {"primary_action": {"action": "反T卖出", "status_label": "仅观察", "price": "6.10-6.12元"}},
                }
            ]
        }

        tracking = build_post_trade_tracking(update, snapshot, decision_report)

        self.assertEqual(tracking["intent_label"], "反T回补")
        self.assertEqual(tracking["execution_quality_review"]["score"], 95)
        self.assertTrue(tracking["can_reverse_t"])
        self.assertEqual(tracking["primary_action"]["action"], "反T卖出")
        self.assertTrue(any("刷新后当前建议" in step for step in tracking["next_steps"]))

    def test_dashboard_position_paths_are_absolute_files(self) -> None:
        paths = dashboard_position_paths()

        self.assertTrue(paths)
        self.assertTrue(all(Path(path).is_absolute() for path in paths))
        self.assertTrue(any(path.endswith("000725.yaml") for path in paths))

    def test_handle_manual_trade_reports_refresh_error_after_saved(self) -> None:
        with (
            patch("tools.serve_monitor_dashboard.load_json", return_value={"total_assets": 25480.0}),
            patch("tools.serve_monitor_dashboard.apply_manual_trade", return_value=({"trade": {"code": "000725", "side": "buy"}}, None)),
            patch("tools.serve_monitor_dashboard.run_refresh_commands", side_effect=RuntimeError("refresh failed")),
        ):
            result = handle_manual_trade({"code": "000725", "side": "buy", "shares": 100, "price": 6.01})

        self.assertTrue(result["ok"])
        self.assertEqual(result["update"]["trade"]["side"], "buy")
        self.assertIn("refresh failed", result["refresh_error"])


if __name__ == "__main__":
    unittest.main()
