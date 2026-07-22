#!/usr/bin/env python3
"""Helpers for retaining raw/generated market data snapshots."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def retained_snapshot_path(
    source_path: Path,
    dataset: str,
    archive_root: Path = Path("data/raw/snapshots"),
    run_at: datetime | None = None,
) -> Path:
    timestamp = run_at or datetime.now().astimezone()
    safe_dataset = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in dataset).strip("_")
    suffix = source_path.suffix or ".dat"
    name = f"{source_path.stem}.{timestamp.strftime('%H%M%S')}{suffix}"
    return archive_root / safe_dataset / timestamp.strftime("%Y-%m-%d") / name


def retain_file_snapshot(
    source_path: Path,
    dataset: str,
    archive_root: Path = Path("data/raw/snapshots"),
    run_at: datetime | None = None,
) -> dict[str, str | int]:
    destination = retained_snapshot_path(source_path, dataset, archive_root, run_at)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return {
        "dataset": dataset,
        "source": str(source_path),
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
    }
