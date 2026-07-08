import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.complete_trade_plan import apply_completion
from tools.new_trade_execution import create_execution, validate_cooldown_allowed, validate_execution_allowed, validate_strategy_health_allowed
from tools.new_trade_plan import create_trade_plan, write_yaml
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def plan_args(**overrides):
    defaults = {
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-plan.example.yaml"),
        "output_dir": "plans",
        "output": None,
        "overwrite": False,
        "id": "TP-EXEC-0001",
        "code": "600000",
        "name": "测试股票",
        "exchange": "SSE",
        "industry": "银行",
        "is_st": False,
        "is_suspended": False,
        "has_delisting_risk": False,
        "abnormal_trading_status": False,
        "strategy": "trend_strength",
        "timeframe": "swing",
        "buy_reason": "来自观察池候选，趋势证据明确。",
        "key_evidence": ["[trend_strength] 趋势强。", "[trend_strength] 成交额支持。"],
        "risk": ["[trend_strength] 若跌破止损价说明趋势失效。"],
        "stop_loss_condition": ["收盘价跌破 9.2。"],
        "take_profit_condition": ["达到目标区后分批止盈。"],
        "invalidation_condition": ["趋势强度消失。"],
        "observation_item": ["观察成交额。"],
        "planned_buy_price": 10.0,
        "current_price": None,
        "stop_loss_price": 9.2,
        "position_pct": 5.0,
        "current_stock_pct": 0.0,
        "current_industry_pct": 10.0,
        "current_total_pct": 40.0,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def execution_args(plan_path: Path, **overrides):
    defaults = {
        "template": str(ROOT / "templates/trade-execution.example.yaml"),
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "strategy_config_snapshot": None,
        "plan": str(plan_path),
        "gate": None,
        "cooldown_check": None,
        "strategy_health": None,
        "manual_confirmations": None,
        "output_dir": "executions",
        "output": None,
        "overwrite": False,
        "id": "EXEC-TEST-0001",
        "status": "recorded",
        "mode": "paper",
        "side": "buy",
        "execution_date": "2026-07-07",
        "execution_price": 10.1,
        "shares": 1000,
        "position_pct": 5.0,
        "fees": 5.0,
        "user_confirmed": True,
        "confirmation_id": None,
        "allow_cooldown_exception": False,
        "cooldown_exception_reason": None,
        "note": ["模拟成交。"],
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class NewTradeExecutionTest(unittest.TestCase):
    def write_confirmation(self, tmp_dir: str, confirmation_id: str = "CONFIRM-TRADE-TP-EXEC-0001") -> Path:
        path = Path(tmp_dir) / "manual-confirmations.json"
        path.write_text(
            json.dumps(
                {
                    "confirmations": [
                        {
                            "id": confirmation_id,
                            "status": "confirmed",
                            "confirmed_by": "lihongwei",
                            "confirmed_at": "2026-07-08T14:00:00",
                            "confirmation_reason": "已阅读计划、反证和最大亏损。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def confirmed_execution_args(self, tmp_dir: str, plan_path: Path, **overrides) -> Namespace:
        confirmation_id = overrides.pop("confirmation_id", "CONFIRM-TRADE-TP-EXEC-0001")
        confirmations_path = self.write_confirmation(tmp_dir, confirmation_id)
        return execution_args(
            plan_path,
            manual_confirmations=str(confirmations_path),
            confirmation_id=confirmation_id,
            **overrides,
        )

    def write_snapshot(self, tmp_dir: str) -> Path:
        path = Path(tmp_dir) / "strategy-config-snapshot.json"
        path.write_text(
            '{"version_id":"CONFIG-VERSION-20260708-173000","profile_hash":"4e0e64d3354b0d4bc865d57a0582e0119dd05a0074c612687a3f1a69705f3edd"}',
            encoding="utf-8",
        )
        return path

    def write_ready_plan(self, tmp_dir: str) -> Path:
        plan, _ = create_trade_plan(plan_args())
        profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
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
        write_yaml(plan_path, plan)
        return plan_path

    def test_creates_execution_from_ready_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)

            execution, output_path = create_execution(self.confirmed_execution_args(tmp_dir, plan_path))

        self.assertEqual(output_path, Path("executions/EXEC-TEST-0001.yaml"))
        self.assertEqual(execution["execution"]["source_trade_plan_id"], "TP-EXEC-0001")
        self.assertEqual(execution["execution"]["gate_conclusion"], "needs_confirmation")
        self.assertEqual(execution["execution"]["cooldown_conclusion"], "missing")
        self.assertEqual(execution["execution"]["strategy_health_conclusion"], "missing")
        self.assertEqual(execution["order"]["slippage_pct_vs_plan"], 1.0)
        self.assertTrue(execution["order"]["price_within_max_acceptable"])
        self.assertEqual(execution["trade_plan_snapshot"]["trade_plan"]["id"], "TP-EXEC-0001")

    def test_fills_strategy_config_snapshot_for_legacy_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)
            plan = load_yaml(plan_path)
            plan.pop("strategy_config_snapshot", None)
            write_yaml(plan_path, plan, overwrite=True)
            snapshot_path = self.write_snapshot(tmp_dir)

            execution, _ = create_execution(self.confirmed_execution_args(tmp_dir, plan_path, strategy_config_snapshot=str(snapshot_path)))

        self.assertEqual(execution["trade_plan_snapshot"]["strategy_config_snapshot"]["version_id"], "CONFIG-VERSION-20260708-173000")
        self.assertTrue(execution["trade_plan_snapshot"]["strategy_config_snapshot"]["available"])

    def test_records_manual_confirmation_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)
            confirmations_path = self.write_confirmation(tmp_dir)

            execution, _ = create_execution(
                execution_args(
                    plan_path,
                    manual_confirmations=str(confirmations_path),
                    confirmation_id="CONFIRM-TRADE-TP-EXEC-0001",
                )
            )

        self.assertEqual(execution["execution"]["confirmation_id"], "CONFIRM-TRADE-TP-EXEC-0001")
        self.assertTrue(execution["confirmation_snapshot"]["available"])
        self.assertEqual(execution["confirmation_snapshot"]["status"], "confirmed")
        self.assertEqual(execution["confirmation_snapshot"]["confirmed_by"], "lihongwei")

    def test_rejects_unconfirmed_needs_confirmation_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)

            with self.assertRaises(ValueError):
                create_execution(execution_args(plan_path, user_confirmed=False))

    def test_rejects_user_confirmed_without_manual_confirmation_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)

            with self.assertRaisesRegex(ValueError, "confirmed manual confirmation"):
                create_execution(execution_args(plan_path, user_confirmed=True))

    def test_rejects_blocked_gate(self) -> None:
        with self.assertRaises(ValueError):
            validate_execution_allowed({"conclusion": "blocked_by_quality"}, True, "paper")

    def test_rejects_buy_during_cooldown(self) -> None:
        with self.assertRaisesRegex(ValueError, "cooldown"):
            validate_cooldown_allowed({"conclusion": "cooldown_required"}, "buy", False, None, True)

    def test_cooldown_exception_requires_confirmation_and_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "user-confirmed"):
            validate_cooldown_allowed({"conclusion": "cooldown_required"}, "buy", True, "例外原因。", False)
        with self.assertRaisesRegex(ValueError, "cooldown-exception-reason"):
            validate_cooldown_allowed({"conclusion": "cooldown_required"}, "buy", True, "", True)

    def test_allows_confirmed_cooldown_exception(self) -> None:
        validate_cooldown_allowed({"conclusion": "cooldown_required"}, "buy", True, "高确定性计划。", True)

    def test_sell_is_not_blocked_by_cooldown(self) -> None:
        validate_cooldown_allowed({"conclusion": "cooldown_required"}, "sell", False, None, False)

    def test_rejects_buy_when_strategy_is_paused(self) -> None:
        health = {"conclusion": "pause_required", "strategies": [{"strategy": "trend_strength", "status": "pause_new_entries"}]}
        with self.assertRaisesRegex(ValueError, "strategy trend_strength"):
            validate_strategy_health_allowed(health, "trend_strength", "buy", False, None, True)

    def test_strategy_health_exception_requires_confirmation_and_reason(self) -> None:
        health = {"conclusion": "pause_required", "strategies": [{"strategy": "trend_strength", "status": "pause_new_entries"}]}
        with self.assertRaisesRegex(ValueError, "user-confirmed"):
            validate_strategy_health_allowed(health, "trend_strength", "buy", True, "例外原因。", False)
        with self.assertRaisesRegex(ValueError, "cooldown-exception-reason"):
            validate_strategy_health_allowed(health, "trend_strength", "buy", True, "", True)

    def test_strategy_health_allows_unpaused_strategy(self) -> None:
        health = {"conclusion": "needs_review", "strategies": [{"strategy": "trend_strength", "status": "needs_review"}]}
        validate_strategy_health_allowed(health, "trend_strength", "buy", False, None, True)

    def test_records_strategy_health_exception_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)
            health_path = Path(tmp_dir) / "strategy-health.json"
            health_path.write_text(
                '{"conclusion":"pause_required","strategies":[{"strategy":"trend_strength","status":"pause_new_entries"}]}',
                encoding="utf-8",
            )

            execution, _ = create_execution(
                self.confirmed_execution_args(
                    tmp_dir,
                    plan_path,
                    strategy_health=str(health_path),
                    allow_cooldown_exception=True,
                    cooldown_exception_reason="策略暂停期小仓位例外。",
                )
            )

        self.assertEqual(execution["execution"]["strategy_health_conclusion"], "pause_required")
        self.assertEqual(execution["strategy_health_snapshot"]["strategies"][0]["status"], "pause_new_entries")

    def test_records_cooldown_exception_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)
            cooldown_path = Path(tmp_dir) / "cooldown.json"
            cooldown_path.write_text(
                '{"conclusion":"cooldown_required","actions":[{"code":"overall_losing_streak_cooldown","message":"暂停新开仓。"}]}',
                encoding="utf-8",
            )

            execution, _ = create_execution(
                self.confirmed_execution_args(
                    tmp_dir,
                    plan_path,
                    cooldown_check=str(cooldown_path),
                    allow_cooldown_exception=True,
                    cooldown_exception_reason="高确定性小仓位例外。",
                )
            )

        self.assertEqual(execution["execution"]["cooldown_conclusion"], "cooldown_required")
        self.assertEqual(execution["execution"]["cooldown_exception_reason"], "高确定性小仓位例外。")
        self.assertEqual(execution["cooldown_snapshot"]["actions"][0]["code"], "overall_losing_streak_cooldown")

    def test_rejects_execution_price_not_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = self.write_ready_plan(tmp_dir)

            with self.assertRaises(ValueError):
                create_execution(self.confirmed_execution_args(tmp_dir, plan_path, execution_price=0))


if __name__ == "__main__":
    unittest.main()
