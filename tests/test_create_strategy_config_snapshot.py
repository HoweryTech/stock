import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.create_strategy_config_snapshot import build_snapshot, profile_hash, render_snapshot
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def pipeline_doc() -> dict:
    return {
        "generated_at": "2026-07-08T17:00:00",
        "apply_requested": True,
        "steps": {
            "change_check": {"conclusion": "pass", "blocker_count": 0, "warning_count": 0},
            "patch": {"operation_count": 1, "skipped": False},
            "apply": {"operation_count": 1, "skipped": False},
            "regression": {"conclusion": "pass", "blocker_count": 0, "warning_count": 0, "skipped": False},
        },
    }


def audit_doc() -> dict:
    return {
        "applied_at": "2026-07-08T17:05:00",
        "applied_by": "lihongwei",
        "backup": "data/backups/investment-profile.20260708-170500.yaml",
        "operation_count": 1,
    }


class CreateStrategyConfigSnapshotTest(unittest.TestCase):
    def test_builds_versioned_snapshot_with_source_metadata(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        snapshot = build_snapshot(
            profile,
            profile_path="config/investment-profile.example.yaml",
            pipeline=pipeline_doc(),
            audit=audit_doc(),
            regression={"conclusion": "pass", "blockers": [], "warnings": []},
            generated_at=datetime(2026, 7, 8, 17, 30, 0),
        )
        content = render_snapshot(snapshot)

        self.assertEqual(snapshot["version_id"], "CONFIG-VERSION-20260708-173000")
        self.assertEqual(len(snapshot["profile_hash"]), 64)
        self.assertEqual(snapshot["profile"]["name"], "personal-a-share-investment-profile")
        self.assertEqual(snapshot["risk"]["max_position_pct_per_stock"], 10.0)
        self.assertEqual(snapshot["source"]["audit"]["applied_by"], "lihongwei")
        self.assertEqual(snapshot["source"]["pipeline"]["regression_conclusion"], "pass")
        self.assertIn("策略配置版本快照", content)
        self.assertIn("CONFIG-VERSION-20260708-173000", content)
        self.assertIn("配置哈希", content)

    def test_profile_hash_changes_when_config_changes(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        changed_profile = copy.deepcopy(profile)
        changed_profile["risk"]["max_position_pct_per_stock"] = 8.0

        self.assertNotEqual(profile_hash(profile), profile_hash(changed_profile))

    def test_missing_source_metadata_is_explicit(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

        snapshot = build_snapshot(
            profile,
            profile_path="config/investment-profile.example.yaml",
            generated_at=datetime(2026, 7, 8, 18, 0, 0),
            version_id="CONFIG-VERSION-MANUAL",
        )
        content = render_snapshot(snapshot)

        self.assertEqual(snapshot["version_id"], "CONFIG-VERSION-MANUAL")
        self.assertFalse(snapshot["source"]["pipeline"]["available"])
        self.assertTrue(snapshot["source"]["pipeline"]["apply_skipped"])
        self.assertEqual(snapshot["source"]["regression"]["conclusion"], "missing")
        self.assertIn("配置变更流水线：缺失", content)

    def test_rendered_snapshot_can_be_written_without_extra_context(self) -> None:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        snapshot = build_snapshot(profile, profile_path="investment-profile.yaml", generated_at=datetime(2026, 7, 8, 18, 30, 0))

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "snapshot.md"
            output.write_text(render_snapshot(snapshot) + "\n", encoding="utf-8")
            content = output.read_text(encoding="utf-8")

        self.assertIn("CONFIG-VERSION-20260708-183000", content)


if __name__ == "__main__":
    unittest.main()
