import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.complete_trade_plan import apply_completion
from tools.new_position_from_execution import create_position_from_execution, extract_plan_from_execution
from tools.new_trade_execution import create_execution
from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def plan_args():
    return Namespace(
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        template=str(ROOT / "templates/trade-plan.example.yaml"),
        output_dir="plans",
        output=None,
        overwrite=False,
        id="TP-POS-EXEC-0001",
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


def strategy_config_snapshot() -> dict:
    return {
        "available": True,
        "version_id": "CONFIG-VERSION-TEST",
        "profile_hash": "abc123",
        "source": {"regression": {"conclusion": "pass"}},
    }


def execution_args(plan_path: Path, output: Path):
    return Namespace(
        template=str(ROOT / "templates/trade-execution.example.yaml"),
        profile=str(ROOT / "config/investment-profile.example.yaml"),
        plan=str(plan_path),
        gate=None,
        output_dir="executions",
        output=str(output),
        overwrite=True,
        id="EXEC-POS-0001",
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


def position_args(execution_path: Path, output: Path):
    return Namespace(
        execution=str(execution_path),
        template=str(ROOT / "templates/position.example.yaml"),
        output_dir="positions",
        output=str(output),
        overwrite=True,
        id="POS-FROM-EXEC-0001",
        status="normal",
        temp_plan_path=None,
        keep_temp_plan=False,
        allow_blocked_execution=False,
        entry_date=None,
        entry_price=None,
        current_price=10.3,
        position_pct=None,
        shares=None,
        stop_loss_price=None,
        days_held=1,
        note=["从执行记录建仓。"],
    )


class NewPositionFromExecutionTest(unittest.TestCase):
    def write_execution(self, tmp_dir: str) -> Path:
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
        plan, _ = create_trade_plan(plan_args())
        plan["strategy_config_snapshot"] = strategy_config_snapshot()
        plan = apply_completion(
            plan,
            profile,
            Namespace(
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
            ),
        )
        plan_path = Path(tmp_dir) / "plan.yaml"
        execution_path = Path(tmp_dir) / "execution.yaml"
        write_yaml(plan_path, plan)
        execution, _ = create_execution(execution_args(plan_path, execution_path))
        write_yaml(execution_path, execution, overwrite=True)
        return execution_path

    def test_extract_plan_requires_snapshot(self) -> None:
        with self.assertRaises(ValueError):
            extract_plan_from_execution({})

    def test_creates_position_from_execution_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution_path = self.write_execution(tmp_dir)
            output = Path(tmp_dir) / "position.yaml"

            position, output_path = create_position_from_execution(position_args(execution_path, output))

        self.assertEqual(output_path, output)
        self.assertEqual(position["position"]["source_trade_plan_id"], "TP-POS-EXEC-0001")
        self.assertEqual(position["entry"]["entry_price"], 10.1)
        self.assertEqual(position["entry"]["shares"], 1000)
        self.assertEqual(position["tracking"]["current_return_pct"], 1.9802)
        self.assertEqual(position["execution_snapshot"]["execution"]["id"], "EXEC-POS-0001")
        self.assertEqual(position["execution_check_snapshot"]["conclusion"], "needs_review")
        self.assertEqual(position["strategy_config_snapshot"]["version_id"], "CONFIG-VERSION-TEST")
        self.assertTrue(any("来源执行记录" in note for note in position["tracking"]["notes"]))

    def test_blocks_position_creation_from_blocked_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution_path = self.write_execution(tmp_dir)
            execution = load_yaml(execution_path)
            execution["order"]["execution_price"] = 11.0
            execution["order"]["price_within_max_acceptable"] = False
            write_yaml(execution_path, execution, overwrite=True)

            with self.assertRaises(ValueError):
                create_position_from_execution(position_args(execution_path, Path(tmp_dir) / "position.yaml"))

    def test_can_override_blocked_execution_for_correction_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            execution_path = self.write_execution(tmp_dir)
            execution = load_yaml(execution_path)
            execution["order"]["execution_price"] = 11.0
            execution["order"]["price_within_max_acceptable"] = False
            write_yaml(execution_path, execution, overwrite=True)
            args = position_args(execution_path, Path(tmp_dir) / "position.yaml")
            args.allow_blocked_execution = True

            position, _ = create_position_from_execution(args)

        self.assertEqual(position["execution_check_snapshot"]["conclusion"], "blocked")


if __name__ == "__main__":
    unittest.main()
