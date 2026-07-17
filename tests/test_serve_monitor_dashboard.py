import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.serve_monitor_dashboard import (
    API_FILES,
    build_trigger_refresh_diffs,
    build_post_trade_tracking,
    dashboard_position_paths,
    handle_manual_trade,
    handle_stop_loss_confirmation,
    handle_intraday_trigger_refresh,
    load_json,
    market_wait_refresh_status,
    monitor_status,
    recent_events,
    recent_flow_history,
    recent_trigger_refresh_events,
    run_refresh_commands,
)


class ServeMonitorDashboardTest(unittest.TestCase):
    def test_recent_events_returns_newest_first(self) -> None:
        class FakePath:
            def exists(self):
                return True

            def read_text(self, encoding):
                return '\n'.join(json.dumps({"id": value}) for value in range(3))

        with patch("tools.serve_monitor_dashboard.EVENT_FILE", FakePath()):
            self.assertEqual([item["id"] for item in recent_events(2)], [2, 1])

    def test_recent_trigger_refresh_events_returns_newest_first(self) -> None:
        class FakePath:
            def exists(self):
                return True

            def read_text(self, encoding):
                return '\n'.join(json.dumps({"id": value}) for value in range(4))

        with patch("tools.serve_monitor_dashboard.TRIGGER_REFRESH_EVENT_FILE", FakePath()):
            self.assertEqual([item["id"] for item in recent_trigger_refresh_events(2)], [3, 2])

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
                return {"generated_at": "2026-07-16T09:30:00+08:00", "cards": [{"code": "600000", "state": "market_wait"}]}
            return None

        with patch("tools.serve_monitor_dashboard.load_json", side_effect=fake_load_json):
            with patch("tools.serve_monitor_dashboard.datetime") as fake_datetime:
                fake_datetime.now.return_value.astimezone.return_value = __import__("datetime").datetime(2026, 7, 16, 9, 35, 0)
                report = market_wait_refresh_status()

        self.assertEqual(report["conclusion"], "refresh_due")
        self.assertIn("--total-assets 25480.0", report["refresh_command"]["shell"])

    def test_market_wait_refresh_status_requires_current_day_decision_cards(self) -> None:
        def fake_load_json(path):
            if path == API_FILES["/api/snapshot"]:
                return {"total_assets": 25480.0}
            if path == API_FILES["/api/decision-cards"]:
                return {"generated_at": "2026-07-17T15:00:00+08:00", "cards": [{"code": "600000", "state": "observe"}]}
            return None

        with patch("tools.serve_monitor_dashboard.load_json", side_effect=fake_load_json):
            with patch("tools.serve_monitor_dashboard.datetime") as fake_datetime:
                fake_datetime.now.return_value.astimezone.return_value = __import__("datetime").datetime(2026, 7, 20, 9, 35, 0)
                report = market_wait_refresh_status()

        self.assertEqual(report["conclusion"], "refresh_due_stale_decision_cards")
        self.assertTrue(report["action_required"])
        self.assertIn("不是当前交易日", report["message"])

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
                    "decision": {
                        "action_label": "不买不卖",
                        "next_step": "等待新信号",
                        "action_arbitration": {"summary": "反T让位于持有观察。"},
                    },
                    "minute_confirmation": {"status": "watch", "status_label": "分钟观察", "summary": "分钟信号不一致。"},
                    "price_action_table": {"primary_action": {"action": "反T卖出", "status_label": "仅观察", "price": "6.10-6.12元"}},
                }
            ]
        }

        tracking = build_post_trade_tracking(update, snapshot, decision_report)

        self.assertEqual(tracking["intent_label"], "反T回补")
        self.assertEqual(tracking["execution_quality_review"]["score"], 95)
        self.assertTrue(tracking["can_reverse_t"])
        self.assertEqual(tracking["primary_action"]["action"], "反T卖出")
        self.assertEqual(tracking["minute_confirmation"]["status_label"], "分钟观察")
        self.assertIn("反T让位", tracking["action_arbitration"]["summary"])
        self.assertTrue(any("刷新后当前建议" in step for step in tracking["next_steps"]))
        self.assertTrue(any("分钟级二次确认" in step for step in tracking["next_steps"]))

    def test_run_refresh_commands_uses_decision_context_inputs(self) -> None:
        def fake_run(command, cwd, text, capture_output, timeout):
            class Completed:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return Completed()

        with (
            patch("tools.serve_monitor_dashboard.Path.exists", return_value=True),
            patch("tools.serve_monitor_dashboard.subprocess.run", side_effect=fake_run) as run,
        ):
            result = run_refresh_commands(25480.0)

        self.assertEqual(len(result), 2)
        pipeline_command = run.call_args_list[1].args[0]
        self.assertIn("--minute-cache-dir", pipeline_command)
        self.assertIn("data/processed/minute-bars", pipeline_command)
        self.assertIn("--technical-indicators", pipeline_command)
        self.assertIn("--reverse-t-forecast", pipeline_command)

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

    def test_handle_stop_loss_confirmation_updates_position_and_refreshes_outputs(self) -> None:
        with (
            patch("tools.serve_monitor_dashboard.load_json", return_value={"total_assets": 25480.0}),
            patch(
                "tools.serve_monitor_dashboard.apply_stop_loss_confirmation",
                return_value=({"confirmation": {"code": "000725", "action": "confirm_hard_stop"}}, None),
            ) as apply_confirmation,
            patch("tools.serve_monitor_dashboard.run_refresh_commands", return_value=[{"returncode": 0}]) as refresh,
        ):
            result = handle_stop_loss_confirmation({"code": "000725", "action": "confirm_hard_stop", "stop_loss_price": 6.1})

        self.assertTrue(result["ok"])
        self.assertEqual(result["update"]["confirmation"]["action"], "confirm_hard_stop")
        apply_confirmation.assert_called_once()
        called_args = apply_confirmation.call_args.args[0]
        self.assertTrue(all(Path(path).is_absolute() for path in called_args.positions))
        refresh.assert_called_once_with(25480.0)

    def test_handle_intraday_trigger_refresh_runs_pipeline_with_snapshot_assets(self) -> None:
        calls = {"decision": 0}

        def fake_load_json(path):
            if path == API_FILES["/api/snapshot"]:
                return {"total_assets": 30000.0}
            if path == API_FILES["/api/decision-cards"]:
                calls["decision"] += 1
                if calls["decision"] == 1:
                    return {
                        "generated_at": "2026-07-17T09:59:00+08:00",
                        "cards": [
                            {
                                "code": "000723",
                                "state": "exit_risk_review",
                                "state_label": "退出风险优先",
                                "decision": {"action_label": "止损风险优先"},
                                "manual_execution_plan": {"plan_type": "near_stop_playbook", "status_label": "近硬止损盘中预案", "shares": 500},
                            }
                        ],
                    }
                return {
                    "generated_at": "2026-07-17T10:00:00+08:00",
                    "state_counts": {"exit_risk_review": 1},
                    "cards": [
                        {
                            "code": "000723",
                            "state": "exit_risk_review",
                            "state_label": "退出风险优先",
                            "decision": {"action_label": "止损风险优先"},
                            "manual_execution_plan": {"plan_type": "risk_reduce", "status_label": "止损减仓计划", "shares": 500},
                            "price_action_table": {"primary_action": {"action": "止损减仓", "status_label": "可执行", "shares": 500, "price": "3.31 元"}},
                        }
                    ],
                }
            return None

        with (
            patch("tools.serve_monitor_dashboard.load_json", side_effect=fake_load_json),
            patch("tools.serve_monitor_dashboard.run_refresh_commands", return_value=[{"returncode": 0}]) as refresh,
            patch("tools.serve_monitor_dashboard.append_jsonl") as append_event,
        ):
            result = handle_intraday_trigger_refresh({"triggers": [{"code": "000723", "active_path": "path1_break"}]})

        self.assertTrue(result["ok"])
        self.assertEqual(result["trigger_count"], 1)
        self.assertEqual(result["state_counts"], {"exit_risk_review": 1})
        self.assertTrue(result["diffs"][0]["changed"])
        self.assertIn("刷新前", result["diffs"][0]["message"])
        self.assertIn("止损减仓", result["diffs"][0]["message"])
        self.assertEqual(result["event"]["trigger_count"], 1)
        append_event.assert_called_once()
        refresh.assert_called_once_with(30000.0)

    def test_build_trigger_refresh_diffs_reports_no_change(self) -> None:
        report = {"cards": [{"code": "000723", "state_label": "退出风险优先", "decision": {"action_label": "止损风险优先"}}]}
        diffs = build_trigger_refresh_diffs(
            [{"code": "000723", "name": "美锦能源", "title": "路径1已触发"}],
            report,
            report,
        )

        self.assertFalse(diffs[0]["changed"])
        self.assertIn("结论暂未变化", diffs[0]["message"])


if __name__ == "__main__":
    unittest.main()
