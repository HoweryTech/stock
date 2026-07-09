import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.risk_check import load_yaml
from tools.run_daily_check_pipeline import run_pipeline


def args(base: Path) -> Namespace:
    return Namespace(
        watchlist_metadata=str(base / "watchlist.json"),
        portfolio_check=str(base / "portfolio.json"),
        exit_plans=[str(base / "exit-plans/*.yaml")],
        trade_executions=[str(base / "executions/*.yaml")],
        exit_executions=[str(base / "exit-executions/*.yaml")],
        positions=[str(base / "positions/*.yaml")],
        reviews=[str(base / "reviews/*.yaml")],
        review_analysis=str(base / "review-analysis.json"),
        cooldown_check=str(base / "review-cooldown.json"),
        strategy_health=str(base / "strategy-health.json"),
        strategy_review_tasks=str(base / "strategy-review-tasks.json"),
        strategy_config_changes=str(base / "strategy-config-changes.json"),
        strategy_config_patch=str(base / "strategy-config-patch.json"),
        strategy_config_patch_audit=str(base / "strategy-config-patch.apply.json"),
        strategy_config_regression=str(base / "strategy-config-regression.json"),
        strategy_config_pipeline=str(base / "strategy-config-change-pipeline.json"),
        strategy_config_snapshot=str(base / "strategy-config-snapshot.json"),
        manual_confirmations=str(base / "manual-confirmations.json"),
        execution_loop_output=str(base / "reports/execution-loop-check.md"),
        execution_loop_json_output=str(base / "metadata/execution-loop-check.json"),
        daily_summary_output=str(base / "reports/daily-summary.md"),
        daily_summary_json_output=str(base / "metadata/daily-summary.json"),
        metadata_output=str(base / "metadata/daily-check-pipeline.json"),
        json=False,
    )


class RunDailyCheckPipelineTest(unittest.TestCase):
    def test_runs_execution_loop_before_daily_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)

            metadata = run_pipeline(args(base))

            loop_json = load_yaml(base / "metadata/execution-loop-check.json")
            daily_summary = load_yaml(base / "metadata/daily-summary.json")
            pipeline_metadata = load_yaml(base / "metadata/daily-check-pipeline.json")

        self.assertEqual(metadata["steps"]["execution_loop_check"]["conclusion"], "pass")
        self.assertEqual(loop_json["conclusion"], "pass")
        self.assertEqual(daily_summary["execution_loop"]["conclusion"], "pass")
        self.assertEqual(pipeline_metadata["steps"]["daily_summary"]["json_output"], str(base / "metadata/daily-summary.json"))


if __name__ == "__main__":
    unittest.main()
