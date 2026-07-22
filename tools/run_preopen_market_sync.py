#!/usr/bin/env python3
"""Refresh yesterday market samples and rebuild the watchlist before market open."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def run_command(command: list[str], cwd: Path) -> dict[str, Any]:
    started = datetime.now()
    completed = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "duration_seconds": round((datetime.now() - started).total_seconds(), 3),
    }


def ensure_success(step: dict[str, Any]) -> None:
    if step["returncode"] != 0:
        command = " ".join(step["command"])
        raise RuntimeError(f"preopen sync command failed ({step['returncode']}): {command}\n{step['stderr']}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_commands(args: argparse.Namespace, repo_root: Path) -> list[list[str]]:
    python = args.python
    commands: list[list[str]] = [
        [
            python,
            "tools/fetch_eastmoney_stock_universe.py",
            "--output",
            args.stock_universe,
            "--metadata-output",
            args.stock_universe_metadata,
        ],
        [
            python,
            "tools/filter_universe.py",
            "--profile",
            args.profile,
            "--input",
            args.stock_universe,
            "--output",
            args.tradable_universe,
            "--report-output",
            args.tradable_universe_metadata,
            "--as-of",
            args.as_of or datetime.now().strftime("%Y-%m-%d"),
        ],
        [
            python,
            "tools/fetch_daily_bars_sina.py",
            "--codes-file",
            args.tradable_universe,
            "--datalen",
            str(args.daily_datalen),
            "--output",
            args.daily_bars,
            "--metadata-output",
            args.daily_bars_metadata,
            "--workers",
            str(args.workers),
            "--progress-every",
            str(args.progress_every),
        ],
    ]
    if args.refresh_valuation:
        commands.append(
            [
                python,
                "tools/fetch_eastmoney_valuation_metrics.py",
                "--output",
                args.valuation_metrics,
                "--metadata-output",
                args.valuation_metrics_metadata,
            ]
        )
    commands.append(
        [
            python,
            "tools/run_watchlist_pipeline.py",
            "--profile",
            args.profile,
            "--daily-bars",
            args.daily_bars,
            "--financial-metrics",
            args.financial_metrics,
            "--valuation-metrics",
            args.valuation_metrics,
            "--event-catalyst-events",
            args.event_catalyst_events,
            "--universe",
            args.tradable_universe,
            "--report-output",
            args.watchlist_report,
            "--metadata-output",
            args.watchlist_metadata,
        ]
    )
    return commands


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh yesterday market samples and rebuild watchlist before market open.")
    parser.add_argument("--python", default=".venv/bin/python")
    parser.add_argument("--profile", default="config/investment-profile.example.yaml")
    parser.add_argument("--stock-universe", default="data/processed/stock_universe.csv")
    parser.add_argument("--stock-universe-metadata", default="data/metadata/stock_universe.fetch.json")
    parser.add_argument("--tradable-universe", default="data/processed/tradable_universe.csv")
    parser.add_argument("--tradable-universe-metadata", default="data/metadata/tradable_universe.filter.json")
    parser.add_argument("--daily-bars", default="data/processed/market_daily_bars.csv")
    parser.add_argument("--daily-bars-metadata", default="data/metadata/market_daily_bars.fetch.json")
    parser.add_argument("--daily-datalen", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=200)
    parser.add_argument("--refresh-valuation", action="store_true", help="Refresh weekly/low-frequency valuation metrics as part of this run.")
    parser.add_argument("--valuation-metrics", default="data/processed/valuation_metrics.csv")
    parser.add_argument("--valuation-metrics-metadata", default="data/metadata/valuation_metrics.fetch.json")
    parser.add_argument("--financial-metrics", default="data/processed/financial_metrics.csv")
    parser.add_argument("--event-catalyst-events", default="data/processed/event_catalyst_events.csv")
    parser.add_argument("--watchlist-report", default="reports/watchlist.md")
    parser.add_argument("--watchlist-metadata", default="data/metadata/watchlist_pipeline.json")
    parser.add_argument("--metadata-output", default="data/metadata/preopen_market_sync.json")
    parser.add_argument("--as-of", help="Reference date for tradable universe filtering; defaults to today.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    steps: list[dict[str, Any]] = []
    try:
        for command in build_commands(args, repo_root):
            step = run_command(command, repo_root)
            steps.append(step)
            ensure_success(step)
        payload = {
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "repo_root": str(repo_root),
            "daily_datalen": args.daily_datalen,
            "refresh_valuation": args.refresh_valuation,
            "steps": steps,
            "conclusion": "pass",
        }
        write_json(Path(args.metadata_output), payload)
    except Exception as exc:
        payload = {
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "repo_root": str(repo_root),
            "daily_datalen": args.daily_datalen,
            "refresh_valuation": args.refresh_valuation,
            "steps": steps,
            "conclusion": "failed",
            "error": str(exc),
        }
        write_json(Path(args.metadata_output), payload)
        print(f"preopen market sync failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"preopen sync: {payload['conclusion']}")
        print(f"steps: {len(steps)}")
        print(f"metadata: {args.metadata_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
