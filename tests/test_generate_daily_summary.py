import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from tools.generate_daily_summary import build_summary, render_summary
from tools.new_trade_plan import write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def args(tmp_dir: str) -> Namespace:
    base = Path(tmp_dir)
    return Namespace(
        watchlist_metadata=str(base / "watchlist.json"),
        portfolio_check=str(base / "portfolio.json"),
        exit_plans=[str(base / "exit-plans/*.yaml")],
        exit_executions=[str(base / "exit-executions/*.yaml")],
        reviews=[str(base / "reviews/*.yaml")],
        review_analysis=str(base / "review-analysis.json"),
        cooldown_check=str(base / "review-cooldown.json"),
        output=str(base / "daily-summary.md"),
        json_output=None,
        json=False,
    )


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exit_plan() -> dict:
    plan = load_yaml(ROOT / "templates/exit-plan.example.yaml")
    plan["exit_plan"]["id"] = "EXIT-SUMMARY-0001"
    plan["exit_plan"]["exit_type"] = "stop_loss"
    plan["exit_plan"]["urgency"] = "immediate"
    plan["stock"]["code"] = "600000"
    plan["decision"]["must_exit"] = True
    plan["checks"]["daily_check_conclusion"] = "needs_action"
    return plan


def exit_execution() -> dict:
    execution = load_yaml(ROOT / "templates/exit-execution.example.yaml")
    execution["execution"]["id"] = "EXITEXEC-SUMMARY-0001"
    execution["execution"]["exit_check_conclusion"] = "pass"
    execution["stock"]["code"] = "600000"
    execution["result_estimate"]["trade_return_pct"] = -9.0
    execution["result_estimate"]["portfolio_return_pct"] = -0.45
    return execution


def review() -> dict:
    data = load_yaml(ROOT / "templates/trade-review.example.yaml")
    data["review"]["id"] = "TR-SUMMARY-0001"
    data["review"]["status"] = "draft"
    data["stock"]["code"] = "600000"
    data["result"]["result_category"] = "strategy_loss"
    data["result"]["trade_return_pct"] = -9.0
    return data


class GenerateDailySummaryTest(unittest.TestCase):
    def test_builds_summary_with_priority_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json(
                base / "watchlist.json",
                {"steps": {"candidate_pool_check": {"conclusion": "needs_review", "warnings": [{"code": "risk", "message": "需补风险。"}]}}},
            )
            write_json(
                base / "portfolio.json",
                {
                    "conclusion": "needs_action",
                    "position_count": 1,
                    "total_position_pct": 5.0,
                    "needs_action_count": 1,
                    "warning_count": 0,
                    "portfolio_actions": [{"code": "portfolio_total_position_exceeded", "message": "总仓位超限。"}],
                    "positions": [],
                },
            )
            write_yaml(base / "exit-plans" / "exit.yaml", exit_plan())
            write_yaml(base / "exit-executions" / "exit_execution.yaml", exit_execution())
            write_yaml(base / "reviews" / "review.yaml", review())

            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 15, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["watchlist"]["conclusion"], "needs_review")
        self.assertEqual(summary["portfolio"]["conclusion"], "needs_action")
        self.assertEqual(summary["exit_plans"]["count"], 1)
        self.assertEqual(summary["exit_executions"]["count"], 1)
        self.assertEqual(summary["reviews"]["draft_count"], 1)
        self.assertEqual(summary["reviews"]["quality_needs_review_count"], 1)
        self.assertIn("优先处理组合或持仓日检中的 needs_action。", summary["operating_actions"])
        self.assertIn("处理 1 个紧急退出计划。", summary["operating_actions"])
        self.assertIn("补全 1 份复盘草稿。", summary["operating_actions"])
        self.assertIn("完善 1 份需复核复盘。", summary["operating_actions"])
        self.assertIn("生成或刷新交易复盘分析。", summary["operating_actions"])
        self.assertIn("执行复盘冷静期检查。", summary["operating_actions"])
        self.assertIn("# 每日操作摘要", content)
        self.assertIn("EXIT-SUMMARY-0001", content)

    def test_missing_inputs_still_generate_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            summary = build_summary(args(tmp_dir), generated_at=datetime(2026, 7, 8, 9, 0, 0))
            content = render_summary(summary)

        self.assertEqual(summary["watchlist"]["conclusion"], "missing")
        self.assertEqual(summary["portfolio"]["conclusion"], "missing")
        self.assertIn("生成或刷新观察池流水线。", summary["operating_actions"])
        self.assertIn("执行组合持仓日检。", summary["operating_actions"])
        self.assertIn("元数据状态：缺失", content)


if __name__ == "__main__":
    unittest.main()
