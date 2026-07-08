#!/usr/bin/env python3
"""Approve or reject a strategy config change draft."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ACTIONS = {"approve", "reject", "reopen"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_draft(changes_doc: dict[str, Any], change_id: str) -> dict[str, Any]:
    for draft in changes_doc.get("drafts", []) or []:
        if draft.get("id") == change_id:
            return draft
    raise ValueError(f"strategy config change not found: {change_id}")


def recount(changes_doc: dict[str, Any]) -> None:
    drafts = changes_doc.get("drafts", []) or []
    changes_doc["draft_count"] = len(drafts)
    changes_doc["approved_count"] = sum(1 for draft in drafts if draft.get("status") == "approved")
    changes_doc["rejected_count"] = sum(1 for draft in drafts if draft.get("status") == "rejected")
    changes_doc["pending_approval_count"] = sum(1 for draft in drafts if draft.get("status") == "draft")


def update_change(
    changes_doc: dict[str, Any],
    *,
    change_id: str,
    action: str,
    actor: str,
    reason: str = "",
    effective_date: str | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(sorted(ACTIONS))}")
    if not actor.strip():
        raise ValueError("actor is required")
    if action == "reject" and not reason.strip():
        raise ValueError("reject requires a non-empty reason")

    updated_at = updated_at or datetime.now()
    timestamp = updated_at.isoformat(timespec="seconds")
    draft = find_draft(changes_doc, change_id)
    previous_status = draft.get("status") or "draft"
    approval = draft.setdefault("approval", {"required": True})

    if action == "approve":
        draft["status"] = "approved"
        if effective_date:
            draft["effective_date"] = effective_date
        approval["approved_by"] = actor
        approval["approved_at"] = timestamp
        approval["rejected_by"] = ""
        approval["rejected_at"] = None
        approval["rejected_reason"] = ""
    elif action == "reject":
        draft["status"] = "rejected"
        approval["rejected_by"] = actor
        approval["rejected_at"] = timestamp
        approval["rejected_reason"] = reason
        approval["approved_by"] = ""
        approval["approved_at"] = None
    else:
        draft["status"] = "draft"
        approval["approved_by"] = ""
        approval["approved_at"] = None
        approval["rejected_by"] = ""
        approval["rejected_at"] = None
        approval["rejected_reason"] = ""

    history = draft.setdefault("history", [])
    history.append(
        {
            "updated_at": timestamp,
            "actor": actor,
            "action": action,
            "from_status": previous_status,
            "to_status": draft["status"],
            "reason": reason,
            "effective_date": draft.get("effective_date"),
        }
    )
    changes_doc["updated_at"] = timestamp
    recount(changes_doc)
    return draft


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approve or reject a strategy config change draft.")
    parser.add_argument("--changes", default="data/metadata/strategy-config-changes.json", help="Strategy config change JSON.")
    parser.add_argument("--change-id", required=True, help="Config change draft id.")
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS), help="Action to apply.")
    parser.add_argument("--actor", default="human", help="Reviewer or approver.")
    parser.add_argument("--reason", default="", help="Approval note or rejection reason.")
    parser.add_argument("--effective-date", help="Effective date for approved changes.")
    parser.add_argument("--output", help="Output JSON path. Defaults to overwriting --changes.")
    parser.add_argument("--json", action="store_true", help="Print updated change as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        changes_doc = load_json(Path(args.changes))
        draft = update_change(
            changes_doc,
            change_id=args.change_id,
            action=args.action,
            actor=args.actor,
            reason=args.reason,
            effective_date=args.effective_date,
        )
        write_json(Path(args.output or args.changes), changes_doc)
    except Exception as exc:
        print(f"strategy config change update failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(draft, ensure_ascii=False, indent=2))
    else:
        print(f"updated strategy config change: {draft['id']}")
        print(f"status: {draft['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
