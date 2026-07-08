import unittest
from datetime import datetime

from tools.generate_strategy_config_changes import build_change_drafts, render_change_drafts


def task(task_id: str, strategy: str, status: str, code: str, resolution: str) -> dict:
    return {
        "id": task_id,
        "strategy": strategy,
        "status": "needs_review",
        "priority": "medium",
        "task_status": status,
        "resolution": resolution,
        "resolved_at": "2026-07-08T11:00:00" if status == "resolved" else None,
        "stats": {"count": 3},
        "actions": [{"code": code, "message": "需要复核。"}],
    }


def config_version_task(status: str, resolution: str) -> dict:
    return {
        "id": "CONFIG-VERSION-REVIEW-CONFIG-VERSION-RISK",
        "task_type": "config_version",
        "strategy": None,
        "config_version_id": "CONFIG-VERSION-RISK",
        "profile_hash": "abcdef1234567890",
        "profile_hash_short": "abcdef123456",
        "status": "needs_review",
        "priority": "medium",
        "task_status": status,
        "resolution": resolution,
        "resolved_at": "2026-07-08T11:30:00" if status == "resolved" else None,
        "stats": {"count": 3, "total_portfolio_return_pct": -0.2},
        "actions": [{"code": "config_version_negative_portfolio_contribution", "message": "配置版本表现偏弱。"}],
    }


class GenerateStrategyConfigChangesTest(unittest.TestCase):
    def test_generates_change_draft_from_resolved_task(self) -> None:
        tasks_doc = {
            "tasks": [
                task(
                    "STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW",
                    "trend_strength",
                    "resolved",
                    "loss_making_discipline_exception",
                    "维持策略，但降低例外仓位上限到 2%。",
                ),
                task("STRATEGY-REVIEW-VALUE-QUALITY-NEEDS-REVIEW", "value_quality", "open", "low_win_rate", ""),
            ]
        }

        result = build_change_drafts(tasks_doc, generated_at=datetime(2026, 7, 8, 12, 0, 0))
        content = render_change_drafts(result)

        self.assertEqual(result["source_task_count"], 2)
        self.assertEqual(result["draft_count"], 1)
        draft = result["drafts"][0]
        self.assertEqual(draft["id"], "CONFIG-CHANGE-STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW")
        self.assertEqual(draft["source_task_id"], "STRATEGY-REVIEW-TREND-STRENGTH-NEEDS-REVIEW")
        self.assertEqual(draft["approval"]["required"], True)
        self.assertEqual(draft["approval"]["approved_by"], "")
        self.assertEqual(draft["approval"]["approval_reason"], "")
        self.assertEqual(draft["approval"]["rejected_by"], "")
        self.assertEqual(draft["approval"]["rejected_reason"], "")
        self.assertEqual(draft["history"], [])
        self.assertEqual(draft["change_items"][0]["path"], "strategies.trend_strength.discipline.exception_position_limit_pct")
        self.assertIn("降低或取消纪律例外仓位上限", draft["change_items"][0]["proposed_change"])
        self.assertIn("策略配置变更草稿", content)
        self.assertIn("维持策略", content)
        self.assertNotIn("STRATEGY-REVIEW-VALUE-QUALITY-NEEDS-REVIEW", content)

    def test_maps_cooldown_to_strategy_enabled_review(self) -> None:
        tasks_doc = {
            "tasks": [
                task(
                    "STRATEGY-REVIEW-TREND-STRENGTH-PAUSE-NEW-ENTRIES",
                    "trend_strength",
                    "resolved",
                    "strategy_cooldown_required",
                    "暂停该策略 5 个交易日。",
                )
            ]
        }

        result = build_change_drafts(tasks_doc, generated_at=datetime(2026, 7, 8, 12, 0, 0))

        self.assertEqual(result["drafts"][0]["change_items"][0]["path"], "strategies.trend_strength.enabled")

    def test_generates_change_draft_from_config_version_task(self) -> None:
        tasks_doc = {"tasks": [config_version_task("resolved", "降低该配置版本风险暴露，并复查策略适用场景。")]}

        result = build_change_drafts(tasks_doc, generated_at=datetime(2026, 7, 8, 12, 30, 0))
        content = render_change_drafts(result)

        self.assertEqual(result["draft_count"], 1)
        draft = result["drafts"][0]
        self.assertEqual(draft["source_task_type"], "config_version")
        self.assertEqual(draft["strategy"], "CONFIG_VERSION")
        self.assertEqual(draft["config_version_id"], "CONFIG-VERSION-RISK")
        self.assertEqual(draft["profile_hash"], "abcdef1234567890")
        paths = [item["path"] for item in draft["change_items"]]
        self.assertIn("risk.max_total_position_pct", paths)
        self.assertIn("risk.max_position_pct_per_stock", paths)
        self.assertIn("strategies", paths)
        self.assertIn("CONFIG-VERSION-RISK", content)

    def test_renders_empty_when_no_resolved_tasks(self) -> None:
        result = build_change_drafts({"tasks": [task("T-1", "trend_strength", "deferred", "low_win_rate", "暂缓。")]}, generated_at=datetime(2026, 7, 8, 12, 0, 0))
        content = render_change_drafts(result)

        self.assertEqual(result["draft_count"], 0)
        self.assertIn("无配置变更草稿", content)


if __name__ == "__main__":
    unittest.main()
