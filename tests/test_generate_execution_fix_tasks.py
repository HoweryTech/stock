import unittest
from datetime import datetime

from tools.generate_execution_fix_tasks import build_tasks, render_tasks


def loop_check_with_fix_actions() -> dict:
    return {
        "conclusion": "needs_review",
        "blocked_count": 0,
        "needs_review_count": 2,
        "fix_actions": [
            {
                "group": "position",
                "title": "生成持仓记录",
                "count": 1,
                "items": [
                    {
                        "code": "missing_position_from_trade_execution",
                        "subject_id": "EXEC-FIX-0001",
                        "message": "买入执行已通过检查，但未找到持仓记录。",
                        "fix_hint": "运行 tools/new_position_from_execution.py --execution <executions/EXEC-FIX-0001.yaml> 生成持仓记录。",
                    }
                ],
            },
            {
                "group": "source_link",
                "title": "修正来源引用",
                "count": 1,
                "items": [
                    {
                        "code": "review_source_exit_execution_not_found",
                        "subject_id": "TR-FIX-0001",
                        "message": "复盘来源卖出执行不存在。",
                        "fix_hint": "补回来源卖出执行记录，或修正 review.source_exit_execution_id。",
                    }
                ],
            },
        ],
    }


class GenerateExecutionFixTasksTest(unittest.TestCase):
    def test_builds_tasks_from_fix_actions(self) -> None:
        tasks = build_tasks(loop_check_with_fix_actions(), generated_at=datetime(2026, 7, 10, 9, 0, 0))
        content = render_tasks(tasks)

        self.assertEqual(tasks["task_count"], 2)
        self.assertEqual(tasks["open_task_count"], 2)
        self.assertEqual(tasks["tasks"][0]["id"], "EXEC-FIX-POSITION-MISSING-POSITION-FROM-TRADE-EXECUTION-EXEC-FIX-0001")
        self.assertIn("生成持仓记录", content)
        self.assertIn("修正来源引用", content)
        self.assertIn("tools/new_position_from_execution.py", content)

    def test_empty_fix_actions_render_no_tasks(self) -> None:
        tasks = build_tasks({"conclusion": "pass", "fix_actions": []}, generated_at=datetime(2026, 7, 10, 9, 0, 0))
        content = render_tasks(tasks)

        self.assertEqual(tasks["task_count"], 0)
        self.assertIn("当前没有执行闭环修复任务", content)


if __name__ == "__main__":
    unittest.main()
