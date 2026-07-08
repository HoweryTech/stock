#!/usr/bin/env python3
"""Validate approved strategy config change drafts before applying them."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import load_yaml, value_at


SAFE_NUMERIC_LIMITS = {
    "risk.max_loss_per_trade_pct_of_total_assets": {"min": 0.0, "max": 1.0},
    "risk.max_position_pct_per_stock": {"min": 0.0, "max": 10.0},
    "risk.max_position_pct_per_industry": {"min": 0.0, "max": 25.0},
    "risk.max_total_position_pct": {"min": 0.0, "max": 80.0},
    "risk.max_total_position_pct_in_weak_market": {"min": 0.0, "max": 30.0},
    "risk.min_cash_pct": {"min": 20.0, "max": 100.0},
    "risk.chase_limit_pct_above_plan_price": {"min": 0.0, "max": 3.0},
}


@dataclass
class CheckItem:
    code: str
    message: str
    change_id: str | None = None
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {"code": self.code, "message": self.message}
        if self.change_id:
            data["change_id"] = self.change_id
        if self.path:
            data["path"] = self.path
        return data


def load_json(path: Path) -> dict[str, Any]:
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


def validate_change(profile: dict[str, Any], draft: dict[str, Any]) -> tuple[list[CheckItem], list[CheckItem], list[CheckItem]]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []
    change_id = draft.get("id") or "UNKNOWN"
    approval = draft.get("approval") or {}

    if draft.get("status") != "approved":
        blockers.append(CheckItem("change_not_approved", f"配置变更 {change_id} 尚未审批通过。", change_id))
    else:
        if not approval.get("approved_by"):
            blockers.append(CheckItem("missing_approval_actor", f"配置变更 {change_id} 缺少审批人。", change_id))
        if not approval.get("approved_at"):
            blockers.append(CheckItem("missing_approval_time", f"配置变更 {change_id} 缺少审批时间。", change_id))
    if not draft.get("effective_date"):
        warnings.append(CheckItem("missing_effective_date", f"配置变更 {change_id} 未填写生效日期。", change_id))
    if draft.get("source_task_type") == "config_version":
        review_evidence = draft.get("review_evidence") or {}
        if not draft.get("config_version_id"):
            blockers.append(CheckItem("missing_config_version_id", f"配置版本变更 {change_id} 缺少 config_version_id。", change_id))
        if not draft.get("profile_hash"):
            blockers.append(CheckItem("missing_config_version_profile_hash", f"配置版本变更 {change_id} 缺少 profile_hash。", change_id))
        if not (draft.get("resolution") or "").strip():
            blockers.append(CheckItem("missing_config_version_resolution", f"配置版本变更 {change_id} 缺少人工复核结论。", change_id))
        if not review_evidence or (not review_evidence.get("actions") and not review_evidence.get("stats")):
            blockers.append(CheckItem("missing_config_version_review_evidence", f"配置版本变更 {change_id} 缺少可复盘的复核证据。", change_id))
        if draft.get("status") == "approved" and not (approval.get("approval_reason") or "").strip():
            blockers.append(CheckItem("missing_config_version_approval_reason", f"配置版本变更 {change_id} 缺少审批理由。", change_id))

    change_items = draft.get("change_items") or []
    if not change_items:
        blockers.append(CheckItem("missing_change_items", f"配置变更 {change_id} 没有变更项。", change_id))

    for item in change_items:
        path = item.get("path") or ""
        if not path:
            blockers.append(CheckItem("missing_change_path", f"配置变更 {change_id} 存在空路径。", change_id))
            continue
        current_value = value_at(profile, path)
        if current_value is None:
            blockers.append(CheckItem("change_path_not_found", f"配置路径不存在：{path}。", change_id, path))
            continue

        if "proposed_value" not in item:
            warnings.append(CheckItem("missing_proposed_value", f"配置路径 {path} 未填写 proposed_value，不能生成可应用补丁。", change_id, path))
            continue

        proposed_value = item.get("proposed_value")
        if type(proposed_value) is not type(current_value):  # noqa: E721
            blockers.append(CheckItem("proposed_value_type_mismatch", f"配置路径 {path} 的 proposed_value 类型与当前值不一致。", change_id, path))
            continue

        limit = SAFE_NUMERIC_LIMITS.get(path)
        proposed_number = as_float(proposed_value)
        if limit and proposed_number is not None:
            if proposed_number < limit["min"] or proposed_number > limit["max"]:
                blockers.append(
                    CheckItem(
                        "unsafe_risk_value",
                        f"配置路径 {path} 的 proposed_value={proposed_number} 超出安全范围 [{limit['min']}, {limit['max']}]。",
                        change_id,
                        path,
                    )
                )
            else:
                info.append(CheckItem("risk_value_within_safe_range", f"配置路径 {path} 的 proposed_value 在安全范围内。", change_id, path))

    return blockers, warnings, info


def check_changes(profile: dict[str, Any], changes_doc: dict[str, Any]) -> dict[str, Any]:
    blockers: list[CheckItem] = []
    warnings: list[CheckItem] = []
    info: list[CheckItem] = []
    for draft in changes_doc.get("drafts", []) or []:
        draft_blockers, draft_warnings, draft_info = validate_change(profile, draft)
        blockers.extend(draft_blockers)
        warnings.extend(draft_warnings)
        info.extend(draft_info)

    if blockers:
        conclusion = "blocked"
    elif warnings:
        conclusion = "needs_review"
    else:
        conclusion = "pass"
    return {
        "conclusion": conclusion,
        "change_count": len(changes_doc.get("drafts", []) or []),
        "blockers": [item.as_dict() for item in blockers],
        "warnings": [item.as_dict() for item in warnings],
        "info": [item.as_dict() for item in info],
    }


def print_result(result: dict[str, Any]) -> None:
    print(f"conclusion: {result['conclusion']}")
    print(f"change count: {result['change_count']}")
    for label, key in (("blockers", "blockers"), ("warnings", "warnings"), ("info", "info")):
        print(f"{label}:")
        if not result[key]:
            print("- none")
        for item in result[key]:
            print(f"- [{item['code']}] {item['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate strategy config change drafts before applying them.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Investment profile YAML.")
    parser.add_argument("--changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change JSON.")
    parser.add_argument("--output", help="Optional output JSON check result.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = check_changes(load_yaml(Path(args.profile)), load_json(Path(args.changes)))
        if args.output:
            write_json(Path(args.output), result)
    except Exception as exc:
        print(f"strategy config change check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result)
    return 1 if result["conclusion"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
