import json
import unittest
from unittest.mock import patch

from tools.serve_monitor_dashboard import API_FILES, market_wait_refresh_status, monitor_status, recent_events


class ServeMonitorDashboardTest(unittest.TestCase):
    def test_recent_events_returns_newest_first(self) -> None:
        class FakePath:
            def exists(self):
                return True

            def read_text(self, encoding):
                return '\n'.join(json.dumps({"id": value}) for value in range(3))

        with patch("tools.serve_monitor_dashboard.EVENT_FILE", FakePath()):
            self.assertEqual([item["id"] for item in recent_events(2)], [2, 1])

    def test_monitor_status_without_pid(self) -> None:
        class MissingPath:
            def exists(self):
                return False

        with patch("tools.serve_monitor_dashboard.PID_FILE", MissingPath()):
            self.assertEqual(monitor_status(), {"running": False, "pid": None})

    def test_exposes_decision_cards_api(self) -> None:
        self.assertIn("/api/decision-cards", API_FILES)
        self.assertEqual(API_FILES["/api/decision-cards"].name, "realtime-decision-cards.json")

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


if __name__ == "__main__":
    unittest.main()
