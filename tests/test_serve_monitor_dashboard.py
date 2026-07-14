import json
import unittest
from unittest.mock import patch

from tools.serve_monitor_dashboard import monitor_status, recent_events


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


if __name__ == "__main__":
    unittest.main()
