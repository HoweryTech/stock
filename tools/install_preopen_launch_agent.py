#!/usr/bin/env python3
"""Install the pre-open market sync as a macOS LaunchAgent."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path


DEFAULT_LABEL = "com.local.stock.preopen-sync"


def weekday_intervals(hour: int, minute: int) -> list[dict[str, int]]:
    return [{"Weekday": weekday, "Hour": hour, "Minute": minute} for weekday in range(1, 6)]


def build_plist(label: str, repo_root: Path, hour: int, minute: int, daily_datalen: int, workers: int) -> dict[str, object]:
    command = (
        f"cd {repo_root} && "
        f".venv/bin/python tools/run_preopen_market_sync.py --daily-datalen {daily_datalen} --workers {workers} "
        f"--metadata-output data/metadata/preopen_market_sync.json"
    )
    return {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-lc", command],
        "StartCalendarInterval": weekday_intervals(hour, minute),
        "WorkingDirectory": str(repo_root),
        "StandardOutPath": str(repo_root / "data" / "metadata" / "preopen_market_sync.launchd.log"),
        "StandardErrorPath": str(repo_root / "data" / "metadata" / "preopen_market_sync.launchd.err"),
        "RunAtLoad": False,
    }


def run_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def install(label: str, repo_root: Path, hour: int, minute: int, daily_datalen: int, workers: int, load: bool) -> dict[str, object]:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{label}.plist"
    plist = build_plist(label, repo_root, hour, minute, daily_datalen, workers)
    plist_path.write_bytes(plistlib.dumps(plist, sort_keys=False))

    result: dict[str, object] = {"label": label, "plist": str(plist_path), "loaded": False}
    if load:
        domain = f"gui/{os.getuid()}"
        run_launchctl(["bootout", domain, str(plist_path)])
        bootstrap = run_launchctl(["bootstrap", domain, str(plist_path)])
        if bootstrap.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap failed: {bootstrap.stderr.strip() or bootstrap.stdout.strip()}")
        enable = run_launchctl(["enable", f"{domain}/{label}"])
        if enable.returncode != 0:
            raise RuntimeError(f"launchctl enable failed: {enable.stderr.strip() or enable.stdout.strip()}")
        result["loaded"] = True
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install pre-open market sync LaunchAgent.")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--hour", type=int, default=8)
    parser.add_argument("--minute", type=int, default=40)
    parser.add_argument("--daily-datalen", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-load", action="store_true", help="Write plist but do not load it with launchctl.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = install(
            args.label,
            Path(args.repo_root).resolve(),
            args.hour,
            args.minute,
            args.daily_datalen,
            args.workers,
            load=not args.no_load,
        )
    except Exception as exc:
        print(f"install preopen LaunchAgent failed: {exc}", file=sys.stderr)
        return 2

    print(f"plist: {result['plist']}")
    print(f"loaded: {result['loaded']}")
    print(f"schedule: weekday {args.hour:02d}:{args.minute:02d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
