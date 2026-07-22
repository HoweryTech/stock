import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tools.fetch_eastmoney_announcements import (
    classify_event,
    fetch_event_catalyst_events,
    merge_rows,
    normalize_announcement,
)


class FetchEastmoneyAnnouncementsTest(unittest.TestCase):
    def test_classifies_positive_and_risk_titles(self) -> None:
        self.assertEqual(classify_event("关于以集中竞价方式回购公司股份的公告")[0], "share_repurchase")
        self.assertEqual(classify_event("关于收到上海证券交易所监管问询函的公告")[0], "regulatory_inquiry")
        self.assertEqual(classify_event("董事长增持公司股份计划公告")[0], "shareholder_increase")

    def test_normalizes_announcement_fields(self) -> None:
        row = {
            "art_code": "ANN-1",
            "notice_date": "2026-07-21 00:00:00",
            "title_ch": "关于收到监管问询函的公告",
            "columns": [{"column_name": "风险提示"}, {"column_name": "监管"}],
        }

        normalized = normalize_announcement("600000", row, "2026-07-22")

        self.assertEqual(normalized["event_date"], "2026-07-21")
        self.assertEqual(normalized["code"], "600000")
        self.assertEqual(normalized["event_type"], "regulatory_inquiry")
        self.assertEqual(normalized["impact_score"], "-4")
        self.assertEqual(normalized["confidence"], "3")
        self.assertEqual(normalized["announcement_id"], "ANN-1")
        self.assertEqual(normalized["categories"], "风险提示|监管")
        self.assertIn("问询", normalized["risk_keywords"])
        self.assertIn("风险事件", normalized["risk_disclosure"])

    def test_merge_rows_deduplicates_by_announcement_id_and_prefers_fetched(self) -> None:
        existing = [
            {
                "announcement_id": "ANN-1",
                "code": "600000",
                "event_type": "share_repurchase",
                "event_date": "2026-07-20",
                "title": "回购公司股份",
                "expected_impact": "old",
            }
        ]
        fetched = [
            {
                "announcement_id": "ANN-1",
                "code": "600000",
                "event_type": "share_repurchase",
                "event_date": "2026-07-20",
                "title": "回购公司股份",
                "expected_impact": "new",
            },
            {
                "announcement_id": "ANN-2",
                "code": "300750",
                "event_type": "major_order",
                "event_date": "2026-07-21",
                "title": "签订重大订单",
            },
        ]

        merged = merge_rows(existing, fetched)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["announcement_id"], "ANN-2")
        self.assertEqual(merged[1]["expected_impact"], "new")

    def test_fetch_event_catalyst_events_writes_csv_metadata_and_snapshot(self) -> None:
        today = date.today().isoformat()
        sample_rows = {
            "600000": [
                {
                    "art_code": "ANN-1",
                    "notice_date": f"{today} 00:00:00",
                    "title_ch": "关于以集中竞价方式回购公司股份的公告",
                    "columns": [{"column_name": "临时公告"}],
                }
            ],
            "300750": [
                {
                    "art_code": "ANN-2",
                    "notice_date": f"{today} 00:00:00",
                    "title_ch": "关于签订重大订单的公告",
                    "columns": [{"column_name": "重大事项"}],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "event_catalyst_events.csv"
            archive_root = Path(tmp_dir) / "snapshots"
            with patch(
                "tools.fetch_eastmoney_announcements.fetch_announcements_for_code",
                side_effect=lambda code, page_size, timeout: sample_rows[code],
            ):
                metadata = fetch_event_catalyst_events(
                    ["600000", "300750", "600000"],
                    output,
                    lookback_days=7,
                    page_size=20,
                    merge_existing=False,
                    archive_root=archive_root,
                    workers=1,
                )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(metadata["requested_code_count"], 2)
        self.assertEqual(metadata["success_code_count"], 2)
        self.assertEqual(metadata["fetched_row_count"], 2)
        self.assertEqual(metadata["output_row_count"], 2)
        self.assertEqual(metadata["event_type_counts"]["major_order"], 1)
        self.assertEqual(metadata["event_type_counts"]["share_repurchase"], 1)
        self.assertTrue(metadata["retained_snapshot"]["path"].endswith(".csv"))
        self.assertEqual({row["code"] for row in rows}, {"600000", "300750"})


if __name__ == "__main__":
    unittest.main()
