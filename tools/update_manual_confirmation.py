#!/usr/bin/env python3
"""Create or update a manual confirmation record."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ACTIONS = {"confirm", "reject", "reopen"}


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"confirmations": []}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    data.setdefault("confirmations", [])
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_confirmation(doc: dict[str, Any], confirmation_id: str) -> dict[str, Any] | None:
    for item in doc.get("confirmations", []) or []:
        if item.get("id") == confirmation_id:
            return item
    return None


def recount(doc: dict[str, Any]) -> None:
    confirmations = doc.get("confirmations", []) or []
    doc["confirmation_count"] = len(confirmations)
    doc["open_count"] = sum(1 for item in confirmations if item.get("status") == "open")
    doc["confirmed_count"] = sum(1 for item in confirmations if item.get("status") == "confirmed")
    doc["rejected_count"] = sum(1 for item in confirmations if item.get("status") == "rejected")


def update_confirmation(
    doc: dict[str, Any],
    *,
    confirmation_id: str,
    action: str,
    actor: str,
    reason: str,
    subject_type: str = "",
    subject_id: str = "",
    text: str = "",
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(sorted(ACTIONS))}")
    if not confirmation_id.strip():
        raise ValueError("confirmation id is required")
    if not actor.strip():
        raise ValueError("actor is required")
    if action in {"confirm", "reject"} and not reason.strip():
        raise ValueError(f"{action} requires a non-empty reason")

    updated_at = updated_at or datetime.now()
    timestamp = updated_at.isoformat(timespec="seconds")
    confirmations = doc.setdefault("confirmations", [])
    item = find_confirmation(doc, confirmation_id)
    if item is None:
        item = {
            "id": confirmation_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "text": text,
            "status": "open",
            "confirmed_by": "",
            "confirmed_at": None,
            "confirmation_reason": "",
            "rejected_by": "",
            "rejected_at": None,
            "rejected_reason": "",
            "history": [],
        }
        confirmations.append(item)
    else:
        if subject_type:
            item["subject_type"] = subject_type
        if subject_id:
            item["subject_id"] = subject_id
        if text:
            item["text"] = text

    previous_status = item.get("status") or "open"
    if action == "confirm":
        item["status"] = "confirmed"
        item["confirmed_by"] = actor
        item["confirmed_at"] = timestamp
        item["confirmation_reason"] = reason
        item["rejected_by"] = ""
        item["rejected_at"] = None
        item["rejected_reason"] = ""
    elif action == "reject":
        item["status"] = "rejected"
        item["rejected_by"] = actor
        item["rejected_at"] = timestamp
        item["rejected_reason"] = reason
        item["confirmed_by"] = ""
        item["confirmed_at"] = None
        item["confirmation_reason"] = ""
    else:
        item["status"] = "open"
        item["confirmed_by"] = ""
        item["confirmed_at"] = None
        item["confirmation_reason"] = ""
        item["rejected_by"] = ""
        item["rejected_at"] = None
        item["rejected_reason"] = ""

    item.setdefault("history", []).append(
        {
            "updated_at": timestamp,
            "actor": actor,
            "action": action,
            "from_status": previous_status,
            "to_status": item["status"],
            "reason": reason,
        }
    )
    doc["updated_at"] = timestamp
    recount(doc)
    return item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update a manual confirmation record.")
    parser.add_argument("--confirmations", default="data/metadata/manual-confirmations.json", help="Manual confirmations JSON.")
    parser.add_argument("--confirmation-id", required=True, help="Stable confirmation item id.")
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS), help="Action to apply.")
    parser.add_argument("--actor", default="human", help="Reviewer or confirmer.")
    parser.add_argument("--reason", default="", help="Confirmation or rejection reason.")
    parser.add_argument("--subject-type", default="", help="Subject type, for example config_change or exit_plan.")
    parser.add_argument("--subject-id", default="", help="Subject id related to this confirmation.")
    parser.add_argument("--text", default="", help="Human-readable confirmation text.")
    parser.add_argument("--output", help="Output JSON path. Defaults to overwriting --confirmations.")
    parser.add_argument("--json", action="store_true", help="Print updated confirmation as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        path = Path(args.confirmations)
        doc = load_json_if_exists(path)
        item = update_confirmation(
            doc,
            confirmation_id=args.confirmation_id,
            action=args.action,
            actor=args.actor,
            reason=args.reason,
            subject_type=args.subject_type,
            subject_id=args.subject_id,
            text=args.text,
        )
        write_json(Path(args.output or args.confirmations), doc)
    except Exception as exc:
        print(f"manual confirmation update failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
    else:
        print(f"updated manual confirmation: {item['id']}")
        print(f"status: {item['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
