#!/usr/bin/env python3
"""Run regression checks after applying strategy config patches."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.check_strategy_config_changes import SAFE_NUMERIC_LIMITS
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_strategy_config_changes import SAFE_NUMERIC_LIMITS
    from risk_check import load_yaml, value_at


REQUIRED_TOP_LEVEL_SECTIONS = ["profile", "risk", "strategies", "review"]
REQUIRED_RISK_FIELDS = [
    "max_loss_per_trade_pct_of_total_assets",
    "max_position_pct_per_stock",
    "max_position_pct_per_industry",
    "max_total_position_pct",
    "max_total_position_pct_in_weak_market",
    "min_cash_pct",
    "chase_limit_pct_above_plan_price",
]


@dataclass
class CheckItem:
    code: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {"code": self.code, "message": self.message}
        if self.path:
            data["path"] = self.path
        return data


def load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def check_required_sections(profile: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    info: list[CheckItem] = []
    for section in REQUIRED_TOP_LEVEL_SECTIONS:
        if not isinstance(profile.get(section), dict):
            blockers.append(CheckItem("missing_required_section", f"缺少配置段：{section}。", section))
        else:
            info.append(CheckItem("required_section_present", f"配置段存在：{section}。", section))
    return blockers, info


def check_risk_fields(profile: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    info: list[CheckItem] = []
    risk = profile.get("risk", {})
    for field in REQUIRED_RISK_FIELDS:
        path = f"risk.{field}"
        value = risk.get(field)
        number = as_float(value)
        if number is None:
            blockers.append(CheckItem("missing_or_invalid_risk_field", f"风险字段缺失或不是数字：{path}。", path))
            continue
        limit = SAFE_NUMERIC_LIMITS.get(path)
        if limit and (number < limit["min"] or number > limit["max"]):
            blockers.append(CheckItem("risk_field_out_of_safe_range", f"风险字段 {path}={number} 超出安全范围 [{limit['min']}, {limit['max']}]。", path))
        else:
            info.append(CheckItem("risk_field_valid", f"风险字段有效：{path}={number}。", path))
    return blockers, info


def check_strategy_sections(profile: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    info: list[CheckItem] = []
    strategies = profile.get("strategies", {})
    preferred = value_at(profile, "profile.preferred_styles") or []
    if not isinstance(strategies, dict) or not strategies:
        blockers.append(CheckItem("missing_strategies", "缺少 strategies 配置段。", "strategies"))
        return blockers, info
    for strategy in preferred:
        config = strategies.get(strategy)
        path = f"strategies.{strategy}"
        if not isinstance(config, dict):
            blockers.append(CheckItem("preferred_strategy_missing", f"首选策略缺少配置：{strategy}。", path))
            continue
        if not isinstance(config.get("enabled"), bool):
            blockers.append(CheckItem("strategy_enabled_not_bool", f"策略 enabled 必须是布尔值：{strategy}。", f"{path}.enabled"))
        if not isinstance(config.get("required_evidence"), list) or not config.get("required_evidence"):
            blockers.append(CheckItem("strategy_missing_required_evidence", f"策略缺少 required_evidence：{strategy}。", f"{path}.required_evidence"))
        if "screening" in config and not isinstance(config.get("screening"), dict):
            blockers.append(CheckItem("strategy_screening_not_object", f"策略 screening 必须是对象：{strategy}。", f"{path}.screening"))
        info.append(CheckItem("preferred_strategy_present", f"首选策略配置存在：{strategy}。", path))
    return blockers, info


def check_audit(audit: dict[str, Any] | None) -> tuple[list[CheckItem], list[CheckItem]]:
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []
    if audit is None:
        warnings.append(CheckItem("missing_apply_audit", "未读取配置补丁应用审计。"))
        return warnings, info
    if not audit.get("applied_by"):
        warnings.append(CheckItem("missing_applied_by", "应用审计缺少 applied_by。"))
    if not audit.get("backup"):
        warnings.append(CheckItem("missing_backup", "应用审计缺少备份路径。"))
    operation_count = len(audit.get("operations", []) or [])
    if operation_count != int(audit.get("operation_count") or 0):
        warnings.append(CheckItem("audit_operation_count_mismatch", "应用审计 operation_count 与明细数量不一致。"))
    else:
        info.append(CheckItem("apply_audit_loaded", f"已读取应用审计，操作数 {operation_count}。"))
    return warnings, info


def check_regression(profile: dict[str, Any], audit: dict[str, Any] | None = None) -> dict[str, Any]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []

    section_blockers, section_info = check_required_sections(profile)
    risk_blockers, risk_info = check_risk_fields(profile)
    strategy_blockers, strategy_info = check_strategy_sections(profile)
    audit_warnings, audit_info = check_audit(audit)

    blockers.extend(section_blockers)
    blockers.extend(risk_blockers)
    blockers.extend(strategy_blockers)
    warnings.extend(audit_warnings)
    info.extend(section_info)
    info.extend(risk_info)
    info.extend(strategy_info)
    info.extend(audit_info)

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"
    return {
        "conclusion": conclusion,
        "blockers": [item.as_dict() for item in blockers],
        "warnings": [item.as_dict() for item in warnings],
        "info": [item.as_dict() for item in info],
    }


def print_result(result: dict[str, Any]) -> None:
    print(f"conclusion: {result['conclusion']}")
    for label, key in (("blockers", "blockers"), ("warnings", "warnings"), ("info", "info")):
        print(f"{label}:")
        if not result[key]:
            print("- none")
        for item in result[key]:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run regression checks after applying strategy config patches.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Investment profile YAML.")
    parser.add_argument("--audit", default="data/metadata/strategy-config-patch.apply.json", help="Strategy config patch apply audit JSON.")
    parser.add_argument("--output", default="data/metadata/strategy-config-regression.json", help="Output JSON regression result.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = check_regression(load_yaml(Path(args.profile)), load_json_if_exists(Path(args.audit)))
        write_json(Path(args.output), result)
    except Exception as exc:
        print(f"strategy config regression check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
