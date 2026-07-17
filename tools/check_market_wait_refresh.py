#!/usr/bin/env python3
"""Check whether market-wait decision cards should be refreshed now."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.build_data_quality_snapshot import classify_market_session, parse_datetime
except ModuleNotFoundError:
    from build_data_quality_snapshot import classify_market_session, parse_datetime


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def append_existing_input(parts: list[str], option: str, path_text: str) -> None:
    if Path(path_text).exists():
        parts.extend([option, path_text])


def build_pipeline_command(
    *,
    positions: list[str],
    daily_bars: str,
    total_assets: float | None,
    python_bin: str,
) -> dict[str, Any]:
    parts = [
        python_bin,
        "tools/run_intraday_decision_pipeline.py",
        "--positions",
        *positions,
        "--daily-bars",
        daily_bars,
        "--total-assets",
    ]
    ready = total_assets is not None
    parts.append(str(total_assets) if total_assets is not None else "<TOTAL_ASSETS>")
    append_existing_input(parts, "--minute-cache-dir", "data/processed/minute-bars")
    append_existing_input(parts, "--action-backtests", "data/metadata/portfolio-action-matrix-backtests.after-plan.json")
    append_existing_input(parts, "--reverse-t-backtest", "data/metadata/reverse-t-backtest.json")
    append_existing_input(parts, "--reverse-t-forecast", "data/metadata/reverse-t-forecast.json")
    append_existing_input(parts, "--technical-indicators", "data/metadata/technical-indicators.json")
    return {"ready": ready, "argv": parts, "shell": shell_join(parts)}


def stale_intraday_inputs(cards: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    stale_items: list[dict[str, Any]] = []
    for card in cards:
        quality = card.get("data_quality") if isinstance(card.get("data_quality"), dict) else {}
        minute = quality.get("minute") if isinstance(quality.get("minute"), dict) else {}
        quote = quality.get("quote") if isinstance(quality.get("quote"), dict) else {}
        minute_dt = parse_datetime(minute.get("latest_timestamp"))
        reasons: list[str] = []
        if minute_dt and minute_dt.date() != as_of.date():
            reasons.append(f"分钟线最新 {minute_dt.date().isoformat()} 不是当前交易日 {as_of.date().isoformat()}")
        if quote.get("status") == "stale":
            reasons.append(str(quote.get("message") or "实时行情过期"))
        if quality.get("overall_status") in {"stale", "insufficient"} and reasons:
            stale_items.append({"code": card.get("code"), "name": card.get("name"), "reasons": reasons})
    return stale_items


def build_refresh_check(
    cards_doc: dict[str, Any] | None,
    *,
    as_of: datetime,
    positions: list[str],
    daily_bars: str,
    total_assets: float | None = None,
    python_bin: str = "python3",
) -> dict[str, Any]:
    session = classify_market_session(as_of)
    command = build_pipeline_command(positions=positions, daily_bars=daily_bars, total_assets=total_assets, python_bin=python_bin)
    if not cards_doc:
        return {
            "generated_at": as_of.isoformat(timespec="seconds"),
            "conclusion": "missing_decision_cards",
            "action_required": True,
            "message": "缺少实时决策卡，建议先刷新完整日内决策链。",
            "market_session": session,
            "market_wait_count": 0,
            "refresh_command": command,
        }

    cards = cards_doc.get("cards", []) or []
    cards_generated_at = parse_datetime(cards_doc.get("generated_at"))
    cards_date = cards_generated_at.date() if cards_generated_at else None
    if session["live_quote_required"] and cards_date != as_of.date():
        conclusion = "refresh_due_stale_decision_cards" if command["ready"] else "refresh_due_missing_total_assets"
        return {
            "generated_at": as_of.isoformat(timespec="seconds"),
            "conclusion": conclusion,
            "action_required": True,
            "message": "实时决策卡不是当前交易日生成，必须刷新完整日内决策链，不能把历史建议当当前建议。",
            "market_session": session,
            "market_wait_count": 0,
            "decision_cards_generated_at": cards_doc.get("generated_at"),
            "refresh_command": command,
        }
    if session["live_quote_required"]:
        stale_inputs = stale_intraday_inputs(cards, as_of)
        if stale_inputs:
            conclusion = "refresh_due_stale_intraday_inputs" if command["ready"] else "refresh_due_missing_total_assets"
            return {
                "generated_at": as_of.isoformat(timespec="seconds"),
                "conclusion": conclusion,
                "action_required": True,
                "message": "实时决策卡存在旧分钟线或过期行情输入，必须刷新后才能作为当前盘中建议。",
                "market_session": session,
                "market_wait_count": 0,
                "stale_input_items": stale_inputs[:10],
                "refresh_command": command,
            }
    market_wait_items = [card for card in cards if card.get("state") == "market_wait"]
    if not market_wait_items:
        return {
            "generated_at": as_of.isoformat(timespec="seconds"),
            "conclusion": "no_market_wait",
            "action_required": False,
            "message": "当前没有等待交易时段的实时决策卡。",
            "market_session": session,
            "market_wait_count": 0,
            "refresh_command": command,
        }

    if session["live_quote_required"]:
        conclusion = "refresh_due" if command["ready"] else "refresh_due_missing_total_assets"
        message = (
            f"{len(market_wait_items)} 只持仓仍处于等待交易时段，但当前已进入{session['label']}，应刷新完整日内决策链。"
            if command["ready"]
            else f"{len(market_wait_items)} 只持仓仍处于等待交易时段，但当前已进入{session['label']}；请补充账户总资产后刷新。"
        )
        return {
            "generated_at": as_of.isoformat(timespec="seconds"),
            "conclusion": conclusion,
            "action_required": True,
            "message": message,
            "market_session": session,
            "market_wait_count": len(market_wait_items),
            "market_wait_items": [
                {"code": card.get("code"), "name": card.get("name"), "state_label": card.get("state_label")}
                for card in market_wait_items[:10]
            ],
            "refresh_command": command,
        }

    return {
        "generated_at": as_of.isoformat(timespec="seconds"),
        "conclusion": "wait_for_market",
        "action_required": False,
        "message": f"{len(market_wait_items)} 只持仓等待行情；当前是{session['label']}，尚不需要刷新盘中决策。",
        "market_session": session,
        "market_wait_count": len(market_wait_items),
        "market_wait_items": [
            {"code": card.get("code"), "name": card.get("name"), "state_label": card.get("state_label")}
            for card in market_wait_items[:10]
        ],
        "refresh_command": command,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether market-wait decision cards should be refreshed now.")
    parser.add_argument("--decision-cards", default="data/metadata/realtime-decision-cards.json")
    parser.add_argument("--positions", nargs="+", default=["positions/POS-EASTMONEY-*.yaml"])
    parser.add_argument("--daily-bars", default="data/processed/daily_bars.csv")
    parser.add_argument("--total-assets", type=float)
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--as-of", help="Override current time for checks, for example 2026-07-16T09:31:00+08:00.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = parse_datetime(args.as_of) if args.as_of else datetime.now().astimezone()
    if as_of is None:
        print(f"invalid --as-of: {args.as_of}", file=sys.stderr)
        return 2
    report = build_refresh_check(
        load_json_if_exists(Path(args.decision_cards)),
        as_of=as_of,
        positions=args.positions,
        daily_bars=args.daily_bars,
        total_assets=args.total_assets,
        python_bin=args.python_bin,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["message"])
        print(f"market session: {report['market_session']['label']} ({report['market_session']['phase']})")
        if report["action_required"]:
            print(f"refresh command: {report['refresh_command']['shell']}")
    return 1 if report["conclusion"] == "refresh_due_missing_total_assets" else 0


if __name__ == "__main__":
    raise SystemExit(main())
