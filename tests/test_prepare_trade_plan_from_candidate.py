import csv
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.prepare_trade_plan_from_candidate import run_prepare
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def args(**overrides):
    defaults = {
        "candidates": "",
        "profile": str(ROOT / "config/investment-profile.example.yaml"),
        "template": str(ROOT / "templates/trade-plan.example.yaml"),
        "output_dir": "plans",
        "output": "",
        "overwrite": True,
        "gate_output": "",
        "strategy_health": None,
        "id": "TP-PREPARE-0001",
        "code": "300750",
        "name": "宁德时代",
        "exchange": "SZSE",
        "industry": "电力设备",
        "strategy": None,
        "timeframe": None,
        "buy_reason": None,
        "planned_buy_price": 200.0,
        "current_price": None,
        "stop_loss_price": 185.0,
        "position_pct": 5.0,
        "current_stock_pct": 0.0,
        "current_industry_pct": 0.0,
        "current_total_pct": 0.0,
        "stop_loss_condition": ["收盘价跌破 185。"],
        "take_profit_condition": ["达到计划目标区后分批止盈。"],
        "invalidation_condition": ["趋势或估值证据失效。"],
        "observation_item": [],
        "review_focus": ["候选池证据是否被市场验证。"],
        "mark_ready": True,
        "json": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class PrepareTradePlanFromCandidateTest(unittest.TestCase):
    def write_candidate_pool(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["code", "strategies", "primary_strategy", "trend_score", "value_quality_score", "trade_date", "report_period", "reasons", "risks"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "code": "300750",
                    "strategies": "trend_strength|value_quality",
                    "primary_strategy": "multi_strategy",
                    "trend_score": "11.5",
                    "value_quality_score": "18.8",
                    "trade_date": "2026-07-02",
                    "report_period": "2026-03-31",
                    "reasons": "[trend_strength] 趋势强。 | [value_quality] PE 分位 68.00 <= 80.00。",
                    "risks": "[value_quality] 估值分位接近上限。",
                }
            )

    def test_prepare_creates_completed_plan_and_gate_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidate_pool.csv"
            output = Path(tmp_dir) / "plan.yaml"
            gate_output = Path(tmp_dir) / "gate.json"
            self.write_candidate_pool(candidates)

            result = run_prepare(args(candidates=str(candidates), output=str(output), gate_output=str(gate_output)))
            plan = load_yaml(output)
            gate_exists = gate_output.exists()

        self.assertEqual(result["gate"]["quality"]["conclusion"], "pass")
        self.assertEqual(result["gate"]["conclusion"], "needs_confirmation")
        self.assertEqual(plan["trade_plan"]["status"], "ready_for_gate")
        self.assertEqual(plan["exit_plan"]["take_profit_conditions"], ["达到计划目标区后分批止盈。"])
        self.assertTrue(gate_exists)

    def test_prepare_blocks_when_required_exit_rules_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidate_pool.csv"
            output = Path(tmp_dir) / "plan.yaml"
            self.write_candidate_pool(candidates)

            with self.assertRaises(ValueError):
                run_prepare(args(candidates=str(candidates), output=str(output), take_profit_condition=[]))

    def test_prepare_uses_strategy_health_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            candidates = Path(tmp_dir) / "candidate_pool.csv"
            output = Path(tmp_dir) / "plan.yaml"
            strategy_health = Path(tmp_dir) / "strategy-health.json"
            self.write_candidate_pool(candidates)
            strategy_health.write_text(
                '{"conclusion":"pause_required","strategies":[{"strategy":"trend_strength","status":"pause_new_entries"}]}',
                encoding="utf-8",
            )

            result = run_prepare(args(candidates=str(candidates), output=str(output), strategy_health=str(strategy_health), strategy="trend_strength"))

        self.assertEqual(result["gate"]["conclusion"], "blocked_by_strategy_health")
        self.assertEqual(result["gate"]["strategy_health"]["conclusion"], "blocked")


if __name__ == "__main__":
    unittest.main()
