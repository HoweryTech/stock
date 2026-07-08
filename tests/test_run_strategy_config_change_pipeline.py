import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml
from tools.run_strategy_config_change_pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]


def args(tmp_dir: str, *, apply: bool = False, applied_by: str | None = None) -> Namespace:
    base = Path(tmp_dir)
    return Namespace(
        profile=str(base / "investment-profile.yaml"),
        changes=str(base / "strategy-config-changes.json"),
        check_output=str(base / "strategy-config-changes.check.json"),
        patch_output=str(base / "strategy-config-patch.md"),
        patch_json_output=str(base / "strategy-config-patch.json"),
        apply=apply,
        backup_dir=str(base / "backups"),
        audit_output=str(base / "strategy-config-patch.apply.json"),
        applied_by=applied_by,
        regression_output=str(base / "strategy-config-regression.json"),
        metadata_output=str(base / "strategy-config-change-pipeline.json"),
        json=False,
    )


def changes_doc(proposed_value=8.0, status: str = "approved") -> dict:
    return {
        "drafts": [
            {
                "id": "CONFIG-CHANGE-RISK",
                "source_task_id": "STRATEGY-REVIEW-RISK",
                "status": status,
                "effective_date": "2026-07-09",
                "resolution": "降低单票仓位。",
                "change_items": [
                    {
                        "path": "risk.max_position_pct_per_stock",
                        "proposed_value": proposed_value,
                        "reason": "连续亏损后降低风险暴露。",
                    }
                ],
                "approval": {"required": True, "approved_by": "lihongwei", "approved_at": "2026-07-08T13:00:00"},
            }
        ]
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class RunStrategyConfigChangePipelineTest(unittest.TestCase):
    def test_runs_check_and_patch_without_apply_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "investment-profile.yaml", load_yaml(ROOT / "config/investment-profile.example.yaml"), overwrite=True)
            write_json(base / "strategy-config-changes.json", changes_doc())

            metadata = run_pipeline(args(tmp_dir))
            profile = load_yaml(base / "investment-profile.yaml")

        self.assertEqual(metadata["steps"]["change_check"]["conclusion"], "pass")
        self.assertEqual(metadata["steps"]["patch"]["operation_count"], 1)
        self.assertTrue(metadata["steps"]["apply"]["skipped"])
        self.assertEqual(metadata["steps"]["regression"]["conclusion"], "skipped")
        self.assertEqual(profile["risk"]["max_position_pct_per_stock"], 10.0)

    def test_applies_and_runs_regression_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "investment-profile.yaml", load_yaml(ROOT / "config/investment-profile.example.yaml"), overwrite=True)
            write_json(base / "strategy-config-changes.json", changes_doc())

            metadata = run_pipeline(args(tmp_dir, apply=True, applied_by="lihongwei"))
            profile = load_yaml(base / "investment-profile.yaml")

        self.assertFalse(metadata["steps"]["apply"]["skipped"])
        self.assertEqual(metadata["steps"]["apply"]["operation_count"], 1)
        self.assertEqual(metadata["steps"]["regression"]["conclusion"], "pass")
        self.assertEqual(profile["risk"]["max_position_pct_per_stock"], 8.0)

    def test_blocks_apply_without_actor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "investment-profile.yaml", load_yaml(ROOT / "config/investment-profile.example.yaml"), overwrite=True)
            write_json(base / "strategy-config-changes.json", changes_doc())

            with self.assertRaisesRegex(ValueError, "applied-by"):
                run_pipeline(args(tmp_dir, apply=True))

    def test_does_not_patch_when_check_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "investment-profile.yaml", load_yaml(ROOT / "config/investment-profile.example.yaml"), overwrite=True)
            write_json(base / "strategy-config-changes.json", changes_doc(status="draft"))

            metadata = run_pipeline(args(tmp_dir))

        self.assertEqual(metadata["steps"]["change_check"]["conclusion"], "blocked")
        self.assertTrue(metadata["steps"]["patch"]["skipped"])
        self.assertEqual(metadata["steps"]["patch"]["operation_count"], 0)


if __name__ == "__main__":
    unittest.main()
