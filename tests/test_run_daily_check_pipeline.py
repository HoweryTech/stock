import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml
from tools.run_daily_check_pipeline import pipeline_exit_code, run_pipeline


def args(base: Path) -> Namespace:
    return Namespace(
        watchlist_metadata=str(base / "watchlist.json"),
        portfolio_check=str(base / "portfolio.json"),
        holding_action_draft=str(base / "holding-action-draft.json"),
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
        continue_on_blocked=False,
        json=False,
    )


def blocked_exit_execution() -> dict:
    execution = load_yaml(Path(__file__).resolve().parents[1] / "templates/exit-execution.example.yaml")
    execution["execution"]["id"] = "EXITEXEC-PIPELINE-BLOCKED"
    execution["execution"]["mode"] = "real"
    execution["execution"]["source_exit_plan_id"] = "EXIT-PIPELINE-BLOCKED"
    execution["execution"]["source_position_id"] = "POS-PIPELINE-BLOCKED"
    execution["execution"]["source_trade_plan_id"] = "TP-PIPELINE-BLOCKED"
    execution["execution"]["exit_check_conclusion"] = "needs_review"
    execution["execution"]["user_confirmed"] = True
    execution["execution"]["confirmation_id"] = "CONFIRM-PIPELINE-BLOCKED"
    execution["stock"]["code"] = "600000"
    execution["order"]["execution_date"] = "2026-07-08"
    execution["order"]["execution_price"] = 9.1
    execution["order"]["exited_position_pct_of_total_assets"] = 5.0
    execution["order"]["price_above_min_acceptable"] = True
    execution["exit_plan_snapshot"] = {"position_snapshot": {"position_pct_of_total_assets": 5.0}}
    execution["confirmation_snapshot"] = {"available": False, "status": "missing"}
    return execution


class RunDailyCheckPipelineTest(unittest.TestCase):
    def test_runs_execution_loop_before_daily_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)

            metadata = run_pipeline(args(base))

            loop_json = load_yaml(base / "metadata/execution-loop-check.json")
            daily_summary = load_yaml(base / "metadata/daily-summary.json")
            pipeline_metadata = load_yaml(base / "metadata/daily-check-pipeline.json")

        self.assertEqual(metadata["steps"]["execution_loop_check"]["conclusion"], "pass")
        self.assertEqual(metadata["steps"]["execution_loop_check"]["orphan_record_count"], 0)
        self.assertEqual(loop_json["conclusion"], "pass")
        self.assertEqual(daily_summary["execution_loop"]["conclusion"], "pass")
        self.assertEqual(pipeline_metadata["steps"]["daily_summary"]["json_output"], str(base / "metadata/daily-summary.json"))

    def test_continue_on_blocked_controls_exit_code_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_yaml(base / "exit-executions" / "blocked.yaml", blocked_exit_execution())
            default_args = args(base)
            continue_args = args(base)
            continue_args.continue_on_blocked = True

            blocked_metadata = run_pipeline(default_args)
            continued_metadata = run_pipeline(continue_args)

        self.assertEqual(blocked_metadata["steps"]["execution_loop_check"]["conclusion"], "blocked")
        self.assertEqual(pipeline_exit_code(blocked_metadata), 1)
        self.assertEqual(continued_metadata["steps"]["execution_loop_check"]["conclusion"], "blocked")
        self.assertTrue(continued_metadata["continue_on_blocked"])
        self.assertEqual(pipeline_exit_code(continued_metadata), 0)


if __name__ == "__main__":
    unittest.main()
