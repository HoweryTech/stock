import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import yaml

from tools.complete_trade_plan import apply_completion
from tools.new_trade_plan_from_candidate import create_plan_from_candidate
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def completion_args(**overrides):
    defaults = {
        "status": None,
        "mark_ready": False,
        "buy_reason": None,
        "key_evidence": [],
        "risk": [],
        "stop_loss_condition": [],
        "take_profit_condition": [],
        "invalidation_condition": [],
        "observation_item": [],
        "review_focus": [],
        "replace_evidence": False,
        "replace_risks": False,
        "replace_exit_rules": False,
        "replace_observation_items": False,
        "replace_review_focus": False,
        "planned_buy_price": None,
        "current_price": None,
        "stop_loss_price": None,
        "position_pct": None,
        "current_stock_pct": None,
        "current_industry_pct": None,
        "current_total_pct": None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def candidate_args(candidate_pool: Path, output: Path):
    return type(
        "Args",
        (),
        {
            "candidates": str(candidate_pool),
            "profile": str(ROOT / "config/investment-profile.example.yaml"),
            "template": str(ROOT / "templates/trade-plan.example.yaml"),
            "output_dir": "plans",
            "output": str(output),
            "overwrite": False,
            "id": "TP-COMPLETE-0001",
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
            "stop_loss_condition": [],
            "take_profit_condition": [],
            "invalidation_condition": [],
            "observation_item": [],
        },
    )()


class CompleteTradePlanTest(unittest.TestCase):
    def make_candidate_plan(self, tmp_dir: str) -> dict:
        candidate_pool = Path(tmp_dir) / "candidate_pool.csv"
        output = Path(tmp_dir) / "plan.yaml"
        candidate_pool.write_text(
            "code,strategies,primary_strategy,trend_score,value_quality_score,trade_date,report_period,reasons,risks\n"
            "300750,trend_strength|value_quality,multi_strategy,11.5,18.8,2026-07-02,2026-03-31,"
            "\"[trend_strength] 趋势强。 | [value_quality] PE 分位 68.00 <= 80.00。\","
            "\"[value_quality] 估值分位接近上限。\"\n",
            encoding="utf-8",
        )
        plan, _ = create_plan_from_candidate(candidate_args(candidate_pool, output))
        return plan

    def test_completes_exit_rules_and_marks_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = self.make_candidate_plan(tmp_dir)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

            updated = apply_completion(
                plan,
                profile,
                completion_args(
                    stop_loss_condition=["收盘价跌破 185。"],
                    take_profit_condition=["达到计划目标区后分批止盈。"],
                    invalidation_condition=["趋势或估值证据失效。"],
                    review_focus=["候选池证据是否被市场验证。"],
                    mark_ready=True,
                ),
            )

        self.assertEqual(updated["trade_plan"]["status"], "ready_for_gate")
        self.assertEqual(updated["exit_plan"]["stop_loss_conditions"], ["收盘价跌破 185。"])
        self.assertEqual(updated["exit_plan"]["take_profit_conditions"], ["达到计划目标区后分批止盈。"])
        self.assertEqual(updated["review_seed"]["review_focus"], ["候选池证据是否被市场验证。"])

    def test_recalculates_derived_fields_after_price_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = self.make_candidate_plan(tmp_dir)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

            updated = apply_completion(
                plan,
                profile,
                completion_args(planned_buy_price=210.0, stop_loss_price=195.0, position_pct=4.0),
            )

        self.assertEqual(updated["price_plan"]["max_acceptable_buy_price"], 216.3)
        self.assertEqual(updated["risk_calculation"]["max_loss_pct_of_total_assets"], 0.2857)

    def test_mark_ready_requires_exit_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = self.make_candidate_plan(tmp_dir)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")

            with self.assertRaises(ValueError):
                apply_completion(plan, profile, completion_args(mark_ready=True))

    def test_completed_plan_can_be_written_as_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan = self.make_candidate_plan(tmp_dir)
            profile = load_yaml(ROOT / "config/investment-profile.example.yaml")
            output = Path(tmp_dir) / "completed.yaml"
            updated = apply_completion(
                plan,
                profile,
                completion_args(
                    stop_loss_condition=["收盘价跌破 185。"],
                    take_profit_condition=["达到计划目标区后分批止盈。"],
                    invalidation_condition=["趋势或估值证据失效。"],
                ),
            )
            output.write_text(yaml.safe_dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")

            loaded = load_yaml(output)

        self.assertEqual(loaded["exit_plan"]["invalidation_conditions"], ["趋势或估值证据失效。"])


if __name__ == "__main__":
    unittest.main()
