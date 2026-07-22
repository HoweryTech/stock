import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.data_retention import retain_file_snapshot, retained_snapshot_path


class DataRetentionTest(unittest.TestCase):
    def test_retained_snapshot_path_groups_by_dataset_and_date(self) -> None:
        path = retained_snapshot_path(
            Path("data/processed/daily_bars.csv"),
            "daily bars",
            Path("archive"),
            datetime(2026, 7, 22, 17, 30, 5),
        )

        self.assertEqual(path, Path("archive/daily_bars/2026-07-22/daily_bars.173005.csv"))

    def test_retain_file_snapshot_copies_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "valuation_metrics.csv"
            source.write_text("code\n600000\n", encoding="utf-8")

            retained = retain_file_snapshot(
                source,
                "valuation_metrics",
                root / "snapshots",
                datetime(2026, 7, 22, 18, 1, 2),
            )

            destination = Path(str(retained["path"]))
            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), "code\n600000\n")
            self.assertEqual(retained["size_bytes"], len("code\n600000\n"))


if __name__ == "__main__":
    unittest.main()
