import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.check_trade_plan_gate import gate_conclusion, run_gate
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class CheckTradePlanGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = ROOT / "config/investment-profile.example.yaml"
        self.plan = load_yaml(ROOT / "templates/trade-plan.example.yaml")

    def write_plan(self, plan: dict) -> Path:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / "plan.yaml"
        path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def test_gate_skips_risk_when_quality_blocked(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["exit_plan"]["take_profit_conditions"] = []
        path = self.write_plan(plan)

        result = run_gate(self.profile, path)

        self.assertEqual(result["conclusion"], "blocked_by_quality")
        self.assertEqual(result["quality"]["conclusion"], "blocked")
        self.assertIsNone(result["risk"])

    def test_gate_can_run_risk_even_if_quality_blocked(self) -> None:
        path = self.write_plan(self.plan)

        result = run_gate(self.profile, path, skip_risk_when_quality_blocked=False)

        self.assertEqual(result["conclusion"], "blocked_by_quality")
        self.assertIsNotNone(result["risk"])

    def test_gate_reports_risk_blocker_after_quality_passes(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["stock"]["name"] = "宁德时代"
        plan["stock"]["industry"] = "电力设备"
        plan["strategy"]["buy_reason"] = "来自观察池候选，趋势和价值质量证据一致。"
        plan["strategy"]["key_evidence"] = [
            {"type": "manual", "description": "[trend_strength] 趋势强。"},
            {"type": "manual", "description": "[value_quality] PE 分位 68.00 <= 80.00。"},
        ]
        plan["strategy"]["counter_evidence_and_risks"] = [{"type": "manual", "description": "[value_quality] 估值分位接近上限。"}]
        plan["price_plan"]["current_price"] = 30.0
        path = self.write_plan(plan)

        result = run_gate(self.profile, path)

        self.assertEqual(result["quality"]["conclusion"], "pass")
        self.assertEqual(result["risk"]["conclusion"], "blocked")
        self.assertEqual(result["conclusion"], "blocked_by_risk")

    def test_gate_conclusion_passes_when_both_pass(self) -> None:
        self.assertEqual(gate_conclusion({"conclusion": "pass"}, {"conclusion": "pass"}), "pass")


if __name__ == "__main__":
    unittest.main()
