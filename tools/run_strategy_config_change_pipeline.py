#!/usr/bin/env python3
"""Run the strategy config change workflow as one auditable pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.apply_strategy_config_patch import apply_patch_file
    from tools.check_strategy_config_changes import check_changes
    from tools.check_strategy_config_regression import check_regression
    from tools.generate_strategy_config_patch import build_patch, render_patch, write_text
    from tools.risk_check import load_yaml
except ModuleNotFoundError:
    from apply_strategy_config_patch import apply_patch_file
    from check_strategy_config_changes import check_changes
    from check_strategy_config_regression import check_regression
    from generate_strategy_config_patch import build_patch, render_patch, write_text
    from risk_check import load_yaml


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply and not args.applied_by:
        raise ValueError("--applied-by is required when --apply is set")

    profile_path = Path(args.profile)
    changes_path = Path(args.changes)
    profile = load_yaml(profile_path)
    changes = load_json(changes_path)
    check_result = check_changes(profile, changes)
    write_json(Path(args.check_output), check_result)

    patch = None
    if check_result["conclusion"] == "pass":
        patch = build_patch(profile, changes, check_result)
        write_text(Path(args.patch_output), render_patch(patch))
        write_json(Path(args.patch_json_output), patch)

    audit = None
    regression = None
    if args.apply:
        if check_result["conclusion"] != "pass":
            raise ValueError("cannot apply config patch when change check did not pass")
        audit = apply_patch_file(
            profile_path,
            Path(args.patch_json_output),
            backup_dir=Path(args.backup_dir),
            audit_output=Path(args.audit_output),
            applied_by=args.applied_by,
        )
        regression = check_regression(load_yaml(profile_path), audit)
        write_json(Path(args.regression_output), regression)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile": args.profile,
        "changes": args.changes,
        "apply_requested": bool(args.apply),
        "steps": {
            "change_check": {
                "output": args.check_output,
                "conclusion": check_result["conclusion"],
                "blocker_count": len(check_result["blockers"]),
                "warning_count": len(check_result["warnings"]),
            },
            "patch": {
                "output": args.patch_output if patch else None,
                "json_output": args.patch_json_output if patch else None,
                "operation_count": patch["operation_count"] if patch else 0,
                "skipped": patch is None,
            },
            "apply": {
                "audit_output": args.audit_output if audit else None,
                "operation_count": audit["operation_count"] if audit else 0,
                "skipped": audit is None,
            },
            "regression": {
                "output": args.regression_output if regression else None,
                "conclusion": regression["conclusion"] if regression else "skipped",
                "blocker_count": len(regression["blockers"]) if regression else 0,
                "warning_count": len(regression["warnings"]) if regression else 0,
                "skipped": regression is None,
            },
        },
    }
    write_json(Path(args.metadata_output), metadata)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the strategy config change workflow.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Investment profile YAML.")
    parser.add_argument("--changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change JSON.")
    parser.add_argument("--check-output", default="data/metadata/strategy-config-changes.check.json", help="Output config change check JSON.")
    parser.add_argument("--patch-output", default="reports/strategy-config-patch.md", help="Output Markdown config patch.")
    parser.add_argument("--patch-json-output", default="data/metadata/strategy-config-patch.json", help="Output JSON config patch.")
    parser.add_argument("--apply", action="store_true", help="Apply the generated patch after checks pass.")
    parser.add_argument("--backup-dir", default="data/backups", help="Directory for profile backups when applying.")
    parser.add_argument("--audit-output", default="data/metadata/strategy-config-patch.apply.json", help="Output patch apply audit JSON.")
    parser.add_argument("--applied-by", help="Person applying the patch. Required with --apply.")
    parser.add_argument("--regression-output", default="data/metadata/strategy-config-regression.json", help="Output regression check JSON.")
    parser.add_argument("--metadata-output", default="data/metadata/strategy-config-change-pipeline.json", help="Output pipeline metadata JSON.")
    parser.add_argument("--json", action="store_true", help="Print pipeline metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = run_pipeline(args)
    except Exception as exc:
        print(f"strategy config change pipeline failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"change check: {metadata['steps']['change_check']['conclusion']}")
        print(f"patch operations: {metadata['steps']['patch']['operation_count']}")
        print(f"apply skipped: {metadata['steps']['apply']['skipped']}")
        print(f"regression: {metadata['steps']['regression']['conclusion']}")
        print(f"metadata: {args.metadata_output}")
    if metadata["steps"]["change_check"]["conclusion"] == "blocked":
        return 1
    if metadata["steps"]["regression"]["conclusion"] == "blocked":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
