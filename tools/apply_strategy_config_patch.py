#!/usr/bin/env python3
"""Apply a checked strategy config patch with backup and audit output."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from risk_check import load_yaml, value_at


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


def set_value(data: dict[str, Any], path: str, value: Any) -> None:
    current: Any = data
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"config path not found: {path}")
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise ValueError(f"config path not found: {path}")
    current[parts[-1]] = value


def backup_path(profile_path: Path, backup_dir: Path, applied_at: datetime) -> Path:
    stamp = applied_at.strftime("%Y%m%d-%H%M%S")
    return backup_dir / f"{profile_path.stem}.{stamp}{profile_path.suffix}"


def apply_patch_to_profile(
    profile: dict[str, Any],
    patch: dict[str, Any],
    *,
    applied_by: str,
    applied_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if patch.get("check_conclusion") != "pass":
        raise ValueError("patch check_conclusion must be pass")
    if patch.get("apply_mode") != "manual_review_required":
        raise ValueError("patch apply_mode must be manual_review_required")
    if not applied_by.strip():
        raise ValueError("applied_by is required")

    applied_at = applied_at or datetime.now()
    updated_profile = deepcopy(profile)
    applied_operations: list[dict[str, Any]] = []
    for operation in patch.get("operations", []) or []:
        if operation.get("op") != "replace":
            raise ValueError(f"unsupported patch operation: {operation.get('op')}")
        path = operation.get("path")
        old_value = operation.get("old_value")
        current_value = value_at(updated_profile, path)
        if current_value != old_value:
            raise ValueError(f"current value mismatch at {path}: expected {old_value!r}, got {current_value!r}")
        set_value(updated_profile, path, operation.get("new_value"))
        applied_operations.append(
            {
                "op": "replace",
                "path": path,
                "old_value": old_value,
                "new_value": operation.get("new_value"),
                "source_change_id": operation.get("source_change_id"),
                "source_task_id": operation.get("source_task_id"),
                "reason": operation.get("reason"),
            }
        )

    audit = {
        "applied_at": applied_at.isoformat(timespec="seconds"),
        "applied_by": applied_by,
        "operation_count": len(applied_operations),
        "operations": applied_operations,
    }
    return updated_profile, audit


def apply_patch_file(
    profile_path: Path,
    patch_path: Path,
    *,
    backup_dir: Path,
    audit_output: Path,
    applied_by: str,
    applied_at: datetime | None = None,
) -> dict[str, Any]:
    applied_at = applied_at or datetime.now()
    profile = load_yaml(profile_path)
    patch = load_json(patch_path)
    updated_profile, audit = apply_patch_to_profile(profile, patch, applied_by=applied_by, applied_at=applied_at)
    backup = backup_path(profile_path, backup_dir, applied_at)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(profile_path, backup)
    write_yaml(profile_path, updated_profile)
    audit["profile"] = str(profile_path)
    audit["patch"] = str(patch_path)
    audit["backup"] = str(backup)
    write_json(audit_output, audit)
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a checked strategy config patch with backup and audit output.")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml", help="Investment profile YAML to update.")
    parser.add_argument("--patch", default="data/metadata/strategy-config-patch.json", help="Strategy config patch JSON.")
    parser.add_argument("--backup-dir", default="data/backups", help="Directory for profile backups.")
    parser.add_argument("--audit-output", default="data/metadata/strategy-config-patch.apply.json", help="Output JSON audit record.")
    parser.add_argument("--applied-by", required=True, help="Person applying the patch.")
    parser.add_argument("--json", action="store_true", help="Print audit JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        audit = apply_patch_file(
            Path(args.profile),
            Path(args.patch),
            backup_dir=Path(args.backup_dir),
            audit_output=Path(args.audit_output),
            applied_by=args.applied_by,
        )
    except Exception as exc:
        print(f"strategy config patch apply failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    else:
        print(f"applied operations: {audit['operation_count']}")
        print(f"backup: {audit['backup']}")
        print(f"audit: {args.audit_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
