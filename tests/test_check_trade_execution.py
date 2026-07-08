import copy
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.complete_trade_plan import apply_completion
from tools.check_trade_execution import check_execution, run_check
from tools.new_trade_execution import create_execution
from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def plan_args() -> Namespace:
    return Namespace(
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        template=str(ROOT / "templates/trade-plan.example.yaml"),
        output_dir="plans",
        output=None,
        overwrite=False,
        id="TP-EXEC-CHECK-0001",
        code="600000",
        name="测试股票",
        exchange="SSE",
        industry="银行",
        is_st=False,
        is_suspended=False,
        has_delisting_risk=False,
        abnormal_trading_status=False,
        strategy="trend_strength",
        timeframe="swing",
        buy_reason="来自观察池候选，趋势证据明确。",
        key_evidence=["[trend_strength] 趋势强。", "[trend_strength] 成交额支持。"],
        risk=["[trend_strength] 若跌破止损价说明趋势失效。"],
        stop_loss_condition=["收盘价跌破 9.2。"],
        take_profit_condition=["达到目标区后分批止盈。"],
        invalidation_condition=["趋势强度消失。"],
        observation_item=["观察成交额。"],
        planned_buy_price=10.0,
        current_price=None,
        stop_loss_price=9.2,
        position_pct=5.0,
        current_stock_pct=0.0,
        current_industry_pct=10.0,
        current_total_pct=40.0,
    )


def completion_args() -> Namespace:
    return Namespace(
        status=None,
        mark_ready=True,
        buy_reason=None,
        key_evidence=[],
        risk=[],
        stop_loss_condition=[],
        take_profit_condition=[],
        invalidation_condition=[],
        observation_item=[],
        review_focus=["执行是否符合计划。"],
        replace_evidence=False,
        replace_risks=False,
        replace_exit_rules=False,
        replace_observation_items=False,
        replace_review_focus=False,
        planned_buy_price=None,
        current_price=None,
        stop_loss_price=None,
        position_pct=None,
        current_stock_pct=None,
        current_industry_pct=None,
        current_total_pct=None,
    )


def execution_args(plan_path: Path) -> Namespace:
    return Namespace(
        template=str(ROOT / "templates/trade-execution.example.yaml"),
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        plan=str(plan_path),
        gate=None,
        output_dir="executions",
        output=None,
        overwrite=False,
        id="EXEC-CHECK-0001",
        status="recorded",
        mode="paper",
        side="buy",
        execution_date="2026-07-07",
        execution_price=10.1,
        shares=1000,
        position_pct=5.0,
        fees=5.0,
        user_confirmed=True,
        note=["模拟成交。"],
    )


class CheckTradeExecutionTest(unittest.TestCase):
    def create_execution_record(self, tmp_dir: str) -> dict:
        plan, _ = create_trade_plan(plan_args())
        plan = apply_completion(plan, load_yaml(ROOT / "config/investment-profile.example.yaml"), completion_args())
        plan_path = Path(tmp_dir) / "plan.yaml"
        write_yaml(plan_path, plan)
        execution, _ = create_execution(execution_args(plan_path))
        return execution

    def test_warns_positive_slippage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertTrue(any(item["code"] == "positive_slippage" for item in result["warnings"]))

    def test_blocks_execution_above_max_acceptable_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["order"]["execution_price"] = 10.5
        execution["order"]["price_within_max_acceptable"] = False

        result = check_execution(execution)
        blocker_codes = {item["code"] for item in result["blockers"]}

        self.assertEqual(result["conclusion"], "blocked")
        self.assertIn("execution_price_above_max_acceptable", blocker_codes)
        self.assertIn("execution_marked_outside_max_price", blocker_codes)

    def test_blocks_actual_position_above_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["order"]["position_pct_of_total_assets"] = 6.0

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "execution_position_above_plan" for item in result["blockers"]))

    def test_blocks_missing_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["execution"]["user_confirmed"] = False

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "missing_user_confirmation" for item in result["blockers"]))

    def test_blocks_cooldown_exception_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["execution"]["cooldown_conclusion"] = "cooldown_required"
        execution["execution"]["cooldown_exception_reason"] = ""
        execution["cooldown_snapshot"] = {"conclusion": "cooldown_required"}

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "missing_cooldown_exception_reason" for item in result["blockers"]))

    def test_blocks_cooldown_exception_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["execution"]["gate_conclusion"] = "pass"
        execution["execution"]["user_confirmed"] = False
        execution["execution"]["cooldown_conclusion"] = "cooldown_required"
        execution["execution"]["cooldown_exception_reason"] = "例外原因。"
        execution["cooldown_snapshot"] = {"conclusion": "cooldown_required"}

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "cooldown_exception_without_confirmation" for item in result["blockers"]))

    def test_passes_confirmed_cooldown_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution = copy.deepcopy(execution)
        execution["order"]["execution_price"] = 9.9
        execution["order"]["slippage_pct_vs_plan"] = -1.0
        execution["execution"]["cooldown_conclusion"] = "cooldown_required"
        execution["execution"]["cooldown_exception_reason"] = "小仓位验证。"
        execution["cooldown_snapshot"] = {"conclusion": "cooldown_required"}

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "pass")

    def test_blocks_strategy_health_exception_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["execution"]["strategy_health_conclusion"] = "pause_required"
        execution["execution"]["cooldown_exception_reason"] = ""
        execution["strategy_health_snapshot"] = {
            "conclusion": "pause_required",
            "strategies": [{"strategy": "trend_strength", "status": "pause_new_entries"}],
        }

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "missing_strategy_health_exception_reason" for item in result["blockers"]))

    def test_blocks_strategy_health_exception_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution["execution"]["gate_conclusion"] = "pass"
        execution["execution"]["user_confirmed"] = False
        execution["execution"]["strategy_health_conclusion"] = "pause_required"
        execution["execution"]["cooldown_exception_reason"] = "例外原因。"
        execution["strategy_health_snapshot"] = {
            "conclusion": "pause_required",
            "strategies": [{"strategy": "trend_strength", "status": "pause_new_entries"}],
        }

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "blocked")
        self.assertTrue(any(item["code"] == "strategy_health_exception_without_confirmation" for item in result["blockers"]))

    def test_passes_confirmed_strategy_health_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution = copy.deepcopy(execution)
        execution["order"]["execution_price"] = 9.9
        execution["order"]["slippage_pct_vs_plan"] = -1.0
        execution["execution"]["strategy_health_conclusion"] = "pause_required"
        execution["execution"]["cooldown_exception_reason"] = "策略暂停期小仓位例外。"
        execution["strategy_health_snapshot"] = {
            "conclusion": "pause_required",
            "strategies": [{"strategy": "trend_strength", "status": "pause_new_entries"}],
        }

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "pass")

    def test_run_check_reads_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
            path = Path(tmp_dir) / "execution.yaml"
            write_yaml(path, execution)

            result = run_check(path)

        self.assertEqual(result["execution_id"], "EXEC-CHECK-0001")

    def test_passes_below_plan_price_and_lower_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution = self.create_execution_record(tmp_dir)
        execution = copy.deepcopy(execution)
        execution["order"]["execution_price"] = 9.9
        execution["order"]["slippage_pct_vs_plan"] = -1.0
        execution["order"]["position_pct_of_total_assets"] = 4.0

        result = check_execution(execution)

        self.assertEqual(result["conclusion"], "pass")
        self.assertTrue(any(item["code"] == "execution_below_plan_price" for item in result["info"]))
        self.assertTrue(any(item["code"] == "execution_position_below_plan" for item in result["info"]))


if __name__ == "__main__":
    unittest.main()
