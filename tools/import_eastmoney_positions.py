#!/usr/bin/env python3
"""Import Eastmoney exported holdings into project position YAML files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from tools.new_position import calculate_return
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import as_float, load_yaml
except ModuleNotFoundError:
    from new_position import calculate_return
    from new_trade_plan import set_value, write_yaml
    from risk_check import as_float, load_yaml


FIELD_ALIASES = {
    "code": ["证券代码", "股票代码", "代码", "stock_code", "code"],
    "name": ["证券名称", "股票名称", "名称", "stock_name", "name"],
    "shares": ["股票余额", "证券余额", "持仓数量", "持有数量", "股份余额", "当前持仓", "shares"],
    "available_shares": ["可用余额", "可用数量", "可卖数量", "可用股份", "available_shares"],
    "entry_price": ["成本价", "持仓成本", "成本价格", "买入均价", "参考成本价", "entry_price"],
    "current_price": ["当前价", "最新价", "现价", "市价", "参考市价", "current_price"],
    "market_value": ["市值", "最新市值", "股票市值", "证券市值", "参考市值", "market_value"],
    "position_pct": ["持仓占比", "仓位占比", "持仓比例", "市值占比", "position_pct"],
    "profit_loss": ["浮动盈亏", "持仓盈亏", "盈亏", "参考盈亏", "profit_loss"],
    "return_pct": ["盈亏比例", "盈亏比", "盈亏率", "收益率", "return_pct"],
}


def normalize_header(value: str) -> str:
    return re.sub(r"[\s\u3000%（）()]+", "", value.strip().lower())


def sniff_delimiter(sample: str) -> str:
    if "\t" in sample:
        return "\t"
    if "," in sample:
        return ","
    return ","


def parse_delimited_text(text: str) -> list[dict[str, str]]:
    text = text.strip()
    if not text:
        return []
    delimiter = sniff_delimiter(text[:2048])
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    if rows:
        return rows
    return []


def parse_plain_table_text(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        headers = [token for token in re.split(r"\s+", lines[0]) if token not in {"√", "✓"}]
        normalized_headers = {normalize_header(header) for header in headers}
        known_headers = {
            normalize_header(alias)
            for aliases in FIELD_ALIASES.values()
            for alias in aliases
        }
        if len(normalized_headers & known_headers) >= 3:
            table_rows: list[dict[str, str]] = []
            for line in lines[1:]:
                values = re.split(r"\s+", line)
                if len(values) < len(headers):
                    continue
                table_rows.append(dict(zip(headers, values)))
            if table_rows:
                return table_rows

    rows: list[dict[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        code_match = re.search(r"\b(\d{6})\b", line)
        if not code_match:
            continue
        code = code_match.group(1)
        before = line[: code_match.start()].strip()
        after = line[code_match.end() :].strip()
        tokens = re.split(r"\s+", after)
        if len(tokens) < 4:
            continue
        name = before or tokens[0]
        numeric_tokens = [token for token in tokens if parse_number(token) is not None]
        if len(numeric_tokens) < 3:
            continue
        rows.append(
            {
                "证券代码": code,
                "证券名称": name,
                "股票余额": numeric_tokens[0],
                "成本价": numeric_tokens[1],
                "当前价": numeric_tokens[2],
                "市值": numeric_tokens[3] if len(numeric_tokens) >= 4 else "",
            }
        )
    return rows


def parse_position_text(text: str) -> list[dict[str, str]]:
    delimited_rows = parse_delimited_text(text)
    if delimited_rows and any(find_value(row, "code") for row in delimited_rows):
        return delimited_rows
    return parse_plain_table_text(text)


def read_text_file(path: Path) -> str:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return ""


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    return parse_position_text(read_text_file(path))


def read_clipboard_text() -> str:
    try:
        completed = subprocess.run(["pbpaste"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("failed to read macOS clipboard with pbpaste") from exc
    return completed.stdout


def read_input_rows(input_path: str | None, from_clipboard: bool) -> list[dict[str, str]]:
    if from_clipboard:
        return parse_position_text(read_clipboard_text())
    if not input_path:
        raise ValueError("provide --input or --from-clipboard")
    rows = read_csv_rows(Path(input_path))
    if not rows:
        raise ValueError(f"no position rows parsed from {input_path}")
    return rows


def find_value(row: dict[str, str], field: str) -> str:
    normalized_map = {normalize_header(key): value for key, value in row.items() if key is not None}
    for alias in FIELD_ALIASES[field]:
        value = normalized_map.get(normalize_header(alias))
        if value is not None:
            return str(value).strip()
    return ""


def parse_number(value: str, default: float | None = None) -> float | None:
    value = (value or "").strip()
    if not value:
        return default
    value = value.replace(",", "").replace("，", "").replace("%", "")
    if value in {"--", "-", "—"}:
        return default
    return as_float(value, default)


def parse_code(value: str) -> str:
    match = re.search(r"(\d{6})", value or "")
    if not match:
        raise ValueError(f"missing or invalid security code: {value!r}")
    return match.group(1)


def infer_exchange(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SSE"
    if code.startswith(("0", "2", "3")):
        return "SZSE"
    if code.startswith(("4", "8")):
        return "BSE"
    return "UNKNOWN"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def build_position_id(code: str, index: int, explicit_prefix: str | None = None) -> str:
    prefix = explicit_prefix or f"POS-EASTMONEY-{date.today().strftime('%Y%m%d')}"
    return f"{prefix}-{index:04d}-{code}"


def normalize_holding_rows(rows: list[dict[str, str]], *, total_assets: float | None, cash: float) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        code_value = find_value(row, "code")
        if not code_value:
            continue
        code = parse_code(code_value)
        shares = parse_number(find_value(row, "shares"), 0.0) or 0.0
        if shares <= 0:
            continue
        entry_price = parse_number(find_value(row, "entry_price"))
        current_price = parse_number(find_value(row, "current_price"), entry_price)
        market_value = parse_number(find_value(row, "market_value"))
        if market_value is None and current_price is not None:
            market_value = current_price * shares
        position_pct = parse_number(find_value(row, "position_pct"))
        if entry_price is None or entry_price <= 0:
            raise ValueError(f"row {row_number} {code}: missing entry/cost price")
        if current_price is None or current_price <= 0:
            raise ValueError(f"row {row_number} {code}: missing current price")
        holdings.append(
            {
                "code": code,
                "name": find_value(row, "name") or code,
                "exchange": infer_exchange(code),
                "shares": shares,
                "available_shares": parse_number(find_value(row, "available_shares")),
                "entry_price": entry_price,
                "current_price": current_price,
                "market_value": market_value,
                "position_pct": position_pct,
                "profit_loss": parse_number(find_value(row, "profit_loss")),
                "return_pct": parse_number(find_value(row, "return_pct")),
            }
        )

    if total_assets is None:
        market_total = sum(item["market_value"] or 0.0 for item in holdings)
        total_assets = market_total + cash if market_total else None
    if total_assets is None or total_assets <= 0:
        raise ValueError("cannot calculate position percent; provide --total-assets or rows with market value")

    for item in holdings:
        if item["position_pct"] is None:
            item["position_pct"] = (item["market_value"] or 0.0) / total_assets * 100
    return holdings


def create_position_yaml(
    template: dict[str, Any],
    holding: dict[str, Any],
    *,
    position_id: str,
    imported_at: str,
    stop_loss_pct: float | None,
    note: str | None,
) -> dict[str, Any]:
    position = deepcopy(template)
    entry_price = holding["entry_price"]
    current_price = holding["current_price"]
    position_pct = holding["position_pct"]
    current_return_pct, current_portfolio_return_pct = calculate_return(entry_price, current_price, position_pct)
    stop_loss_price = round(entry_price * (1 - stop_loss_pct / 100), 4) if stop_loss_pct is not None else None

    set_value(position, "position.id", position_id)
    set_value(position, "position.status", "normal")
    set_value(position, "position.created_at", imported_at[:10])
    set_value(position, "position.source_trade_plan_id", "IMPORT-EASTMONEY")
    set_value(position, "stock.code", holding["code"])
    set_value(position, "stock.name", holding["name"])
    set_value(position, "stock.exchange", holding["exchange"])
    set_value(position, "stock.industry", "UNKNOWN")
    set_value(position, "entry.entry_date", imported_at[:10])
    set_value(position, "entry.entry_price", entry_price)
    set_value(position, "entry.shares", holding["shares"])
    set_value(position, "entry.position_pct_of_total_assets", round(position_pct, 4))
    set_value(position, "entry.planned_buy_price", entry_price)
    set_value(position, "entry.max_acceptable_buy_price", None)
    set_value(position, "risk.stop_loss_price", stop_loss_price)
    set_value(position, "risk.max_loss_pct_of_total_assets", None)
    set_value(position, "risk.take_profit_conditions", [])
    set_value(position, "risk.invalidation_conditions", [] if stop_loss_price is None else [f"跌破导入止损价 {stop_loss_price}。"])
    set_value(position, "risk.observation_items", ["由东方财富持仓导入，需补充行业、买入逻辑和交易计划。"])
    set_value(position, "strategy.source", "imported_holding")
    set_value(position, "strategy.timeframe", "unknown")
    set_value(position, "strategy.buy_reason", "东方财富持仓导入，原始买入理由待补充。")
    set_value(position, "strategy.key_evidence", [])
    set_value(position, "strategy.counter_evidence_and_risks", [])
    set_value(position, "tracking.current_price", current_price)
    set_value(position, "tracking.current_return_pct", current_return_pct)
    set_value(position, "tracking.current_portfolio_return_pct", current_portfolio_return_pct)
    set_value(position, "tracking.days_held", 0)
    set_value(position, "tracking.notes", [note] if note else ["从东方财富导出持仓导入。"])
    set_value(position, "strategy_config_snapshot", {})
    set_value(position, "trade_plan_snapshot", {})
    position["broker_import_snapshot"] = {
        "source": "eastmoney_export",
        "imported_at": imported_at,
        "market_value": holding["market_value"],
        "available_shares": holding["available_shares"],
        "profit_loss": holding["profit_loss"],
        "return_pct": holding["return_pct"],
    }
    return position


def import_positions(args: argparse.Namespace) -> dict[str, Any]:
    raw_rows = read_input_rows(args.input, args.from_clipboard)
    total_assets = parse_number(str(args.total_assets)) if args.total_assets is not None else None
    holdings = normalize_holding_rows(raw_rows, total_assets=total_assets, cash=args.cash)
    template = load_yaml(Path(args.template))
    imported_at = datetime.now().isoformat(timespec="seconds")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.id_prefix or f"POS-EASTMONEY-{now_stamp()}"

    written: list[str] = []
    for index, holding in enumerate(holdings, start=1):
        position_id = build_position_id(holding["code"], index, prefix)
        position = create_position_yaml(
            template,
            holding,
            position_id=position_id,
            imported_at=imported_at,
            stop_loss_pct=args.default_stop_loss_pct,
            note=args.note,
        )
        output_path = output_dir / f"{position_id}.yaml"
        write_yaml(output_path, position, overwrite=args.overwrite)
        written.append(str(output_path))

    return {
        "imported_at": imported_at,
        "source": "eastmoney_export",
        "input": "clipboard" if args.from_clipboard else args.input,
        "position_count": len(written),
        "output_dir": str(output_dir),
        "written": written,
        "requires_follow_up": [
            "补充行业、买入理由、止盈/失效条件。",
            "如未传入 --default-stop-loss-pct，做T检查会因缺少止损价而阻断。",
            "导入后建议运行自动行情刷新和持仓检查。",
        ],
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"source: {metadata['source']}")
    print(f"positions: {metadata['position_count']}")
    print(f"output dir: {metadata['output_dir']}")
    for path in metadata["written"]:
        print(f"- {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Eastmoney holdings export into position YAML files.")
    parser.add_argument("--input", help="Eastmoney exported holdings CSV/TSV/text table.")
    parser.add_argument("--from-clipboard", action="store_true", help="Read copied Eastmoney holdings table from macOS clipboard.")
    parser.add_argument("--template", default="templates/position.example.yaml", help="Position template YAML.")
    parser.add_argument("--output-dir", default="positions", help="Output directory for generated position YAML files.")
    parser.add_argument("--metadata-output", default="data/metadata/eastmoney-positions.import.json", help="Import metadata JSON.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite generated files if they already exist.")
    parser.add_argument("--id-prefix", help="Custom position id prefix.")
    parser.add_argument("--total-assets", type=float, help="Total account assets for position percent calculation.")
    parser.add_argument("--cash", type=float, default=0.0, help="Cash to add when total assets are inferred from market values.")
    parser.add_argument("--default-stop-loss-pct", type=float, help="Optional default stop-loss percent below cost price.")
    parser.add_argument("--note", help="Tracking note to add to imported positions.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        metadata = import_positions(args)
        write_metadata(Path(args.metadata_output), metadata)
    except Exception as exc:
        print(f"import Eastmoney positions failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
