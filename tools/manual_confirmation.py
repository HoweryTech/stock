"""Shared helpers for auditable manual confirmation records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_confirmation_record(confirmations_path: Path | None, confirmation_id: str | None) -> dict[str, Any]:
    if not confirmation_id:
        return {"available": False, "status": "missing", "id": None}
    if confirmations_path is None or not confirmations_path.exists():
        return {"available": False, "status": "missing", "id": confirmation_id}
    data = json.loads(confirmations_path.read_text(encoding="utf-8"))
    for item in data.get("confirmations", []) or []:
        if item.get("id") == confirmation_id:
            return {"available": True, **item}
    return {"available": False, "status": "missing", "id": confirmation_id}


def confirmation_is_confirmed(record: dict[str, Any]) -> bool:
    return bool(record.get("available")) and record.get("status") == "confirmed"


def validate_manual_confirmation_required(required: bool, record: dict[str, Any]) -> None:
    if not required:
        return
    if not confirmation_is_confirmed(record):
        confirmation_id = record.get("id") or "missing"
        raise ValueError(f"confirmed manual confirmation record is required: {confirmation_id}")
