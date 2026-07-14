#!/usr/bin/env python3
"""Stop the intraday holding monitor using its PID file."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path


def stop_monitor(pid_path: Path, timeout: float = 10.0) -> int:
    if not pid_path.exists():
        print("intraday monitor is not running")
        return 0
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except ValueError:
        pid_path.unlink(missing_ok=True)
        raise RuntimeError("invalid monitor PID file")
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        print("removed stale intraday monitor PID file")
        return 0

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_path.exists():
            print(f"stopped intraday monitor pid {pid}")
            return 0
        time.sleep(0.1)
    raise RuntimeError(f"monitor pid {pid} did not stop within {timeout:.1f}s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop the intraday holding monitor.")
    parser.add_argument("--pid-file", default="data/metadata/intraday-monitor.pid")
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return stop_monitor(Path(args.pid_file), args.timeout)
    except Exception as exc:
        print(f"stop intraday monitor failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
