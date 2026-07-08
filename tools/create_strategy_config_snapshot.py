#!/usr/bin/env python3
"""Create a versioned snapshot for the active strategy configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import load_yaml, value_at


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def profile_hash(profile: dict[str, Any]) -> str:
    canonical = yaml.safe_dump(profile, allow_unicode=True, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summarize_strategies(profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    strategies = profile.get("strategies", {}) or {}
    for name in sorted(strategies):
        config = strategies.get(name) or {}
        required_evidence = config.get("required_evidence") or []
        screening = config.get("screening") or {}
        rows.append(
            {
                "name": name,
                "enabled": bool(config.get("enabled")),
                "timeframe": config.get("timeframe"),
                "required_evidence_count": len(required_evidence),
                "screening_keys": sorted(screening.keys()) if isinstance(screening, dict) else [],
            }
        )
    return rows


def summarize_source(
    pipeline: dict[str, Any] | None,
    audit: dict[str, Any] | None,
    regression: dict[str, Any] | None,
) -> dict[str, Any]:
    apply_skipped = value_at(pipeline or {}, "steps.apply.skipped")
    if apply_skipped is None:
        apply_skipped = True
    return {
        "pipeline": {
            "available": bool(pipeline),
            "generated_at": value_at(pipeline or {}, "generated_at"),
            "apply_requested": bool(value_at(pipeline or {}, "apply_requested")),
            "change_check_conclusion": value_at(pipeline or {}, "steps.change_check.conclusion") or "missing",
            "patch_operation_count": value_at(pipeline or {}, "steps.patch.operation_count") or 0,
            "apply_skipped": bool(apply_skipped),
            "regression_conclusion": value_at(pipeline or {}, "steps.regression.conclusion") or "missing",
        },
        "audit": {
            "available": bool(audit),
            "applied_at": (audit or {}).get("applied_at"),
            "applied_by": (audit or {}).get("applied_by"),
            "operation_count": (audit or {}).get("operation_count", 0),
            "backup": (audit or {}).get("backup"),
        },
        "regression": {
            "available": bool(regression),
            "conclusion": (regression or {}).get("conclusion") or "missing",
            "blocker_count": len((regression or {}).get("blockers", []) or []),
            "warning_count": len((regression or {}).get("warnings", []) or []),
        },
    }


def default_version_id(generated_at: datetime) -> str:
    return f"CONFIG-VERSION-{generated_at.strftime('%Y%m%d-%H%M%S')}"


def build_snapshot(
    profile: dict[str, Any],
    *,
    profile_path: str,
    pipeline: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    regression: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now()
    risk = profile.get("risk", {}) or {}
    profile_meta = profile.get("profile", {}) or {}
    return {
        "version_id": version_id or default_version_id(generated_at),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "decision_boundary": "snapshot_only_no_trading_advice",
        "profile_path": profile_path,
        "profile_hash": profile_hash(profile),
        "profile": {
            "name": profile_meta.get("name"),
            "market_scope": profile_meta.get("market_scope") or [],
            "decision_mode": profile_meta.get("decision_mode"),
            "primary_goal": profile_meta.get("primary_goal"),
        },
        "risk": {
            "max_loss_per_trade_pct_of_total_assets": risk.get("max_loss_per_trade_pct_of_total_assets"),
            "max_position_pct_per_stock": risk.get("max_position_pct_per_stock"),
            "max_position_pct_per_industry": risk.get("max_position_pct_per_industry"),
            "max_total_position_pct": risk.get("max_total_position_pct"),
            "max_total_position_pct_in_weak_market": risk.get("max_total_position_pct_in_weak_market"),
            "min_cash_pct": risk.get("min_cash_pct"),
        },
        "strategies": summarize_strategies(profile),
        "source": summarize_source(pipeline, audit, regression),
    }


def render_snapshot(snapshot: dict[str, Any]) -> str:
    source = snapshot["source"]
    lines = [
        "# 策略配置版本快照",
        "",
        f"- 版本：{snapshot['version_id']}",
        f"- 生成时间：{snapshot['generated_at']}",
        f"- 配置文件：{snapshot['profile_path']}",
        f"- 配置哈希：{snapshot['profile_hash']}",
        "- 决策边界：本快照只记录投资体系配置版本，不构成买卖建议。",
        "",
        "## 投资体系",
        "",
        f"- 名称：{snapshot['profile'].get('name') or '-'}",
        f"- 市场范围：{', '.join(snapshot['profile'].get('market_scope') or []) or '-'}",
        f"- 决策模式：{snapshot['profile'].get('decision_mode') or '-'}",
        f"- 主要目标：{snapshot['profile'].get('primary_goal') or '-'}",
        "",
        "## 风控边界",
        "",
    ]
    for key, value in snapshot["risk"].items():
        lines.append(f"- {key}: {value if value is not None else '-'}")
    lines.extend(["", "## 策略摘要", ""])
    for row in snapshot["strategies"]:
        lines.append(
            f"- {row['name']} enabled={row['enabled']} timeframe={row['timeframe']} "
            f"evidence={row['required_evidence_count']} screening_keys={','.join(row['screening_keys']) or '-'}"
        )
    lines.extend(
        [
            "",
            "## 来源元数据",
            "",
            f"- 配置变更流水线：{'已读取' if source['pipeline']['available'] else '缺失'}",
            f"- 流水线校验结论：{source['pipeline']['change_check_conclusion']}",
            f"- 流水线补丁操作数：{source['pipeline']['patch_operation_count']}",
            f"- 流水线是否应用：{'否' if source['pipeline']['apply_skipped'] else '是'}",
            f"- 流水线回归结论：{source['pipeline']['regression_conclusion']}",
            f"- 应用审计：{'已读取' if source['audit']['available'] else '缺失'}",
            f"- 应用人：{source['audit']['applied_by'] or '-'}",
            f"- 应用时间：{source['audit']['applied_at'] or '-'}",
            f"- 备份文件：{source['audit']['backup'] or '-'}",
            f"- 回归检查：{'已读取' if source['regression']['available'] else '缺失'}",
            f"- 回归结论：{source['regression']['conclusion']}",
            f"- 回归阻断数：{source['regression']['blocker_count']}",
            f"- 回归提醒数：{source['regression']['warning_count']}",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a versioned snapshot for the active strategy configuration.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Investment profile YAML.")
    parser.add_argument("--pipeline", default="data/metadata/strategy-config-change-pipeline.json", help="Optional strategy config pipeline metadata JSON.")
    parser.add_argument("--audit", default="data/metadata/strategy-config-patch.apply.json", help="Optional strategy config apply audit JSON.")
    parser.add_argument("--regression", default="data/metadata/strategy-config-regression.json", help="Optional strategy config regression JSON.")
    parser.add_argument("--version-id", help="Optional explicit config version id.")
    parser.add_argument("--output", default="reports/strategy-config-snapshot.md", help="Output Markdown snapshot.")
    parser.add_argument("--json-output", default="data/metadata/strategy-config-snapshot.json", help="Output JSON snapshot.")
    parser.add_argument("--json", action="store_true", help="Print snapshot JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile_path = Path(args.profile)
        profile = load_yaml(profile_path)
        snapshot = build_snapshot(
            profile,
            profile_path=args.profile,
            pipeline=load_json_if_exists(Path(args.pipeline)),
            audit=load_json_if_exists(Path(args.audit)),
            regression=load_json_if_exists(Path(args.regression)),
            version_id=args.version_id,
        )
        write_json(Path(args.json_output), snapshot)
        write_text(Path(args.output), render_snapshot(snapshot))
    except Exception as exc:
        print(f"strategy config snapshot failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(f"strategy config snapshot: {args.output}")
        print(f"version: {snapshot['version_id']}")
        print(f"profile hash: {snapshot['profile_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
