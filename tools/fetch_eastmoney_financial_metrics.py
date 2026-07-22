#!/usr/bin/env python3
"""Fetch A-share financial metrics from Eastmoney public financial reports."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from tools.data_retention import retain_file_snapshot
    from tools.import_financial_metrics import STANDARD_FIELDS
except ModuleNotFoundError:
    from data_retention import retain_file_snapshot
    from import_financial_metrics import STANDARD_FIELDS


FINANCE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_F10_FINANCE_MAINFINADATA"
DATA_SOURCE = "eastmoney_finance_mainfinadata"


def security_code_with_exchange(code: str) -> str:
    code = code.strip()
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def as_number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_number(value: Any) -> str:
    number = as_number(value)
    if number is None:
        return ""
    rounded = round(number, 6)
    return str(int(rounded)) if float(rounded).is_integer() else str(rounded)


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


def get_json(url: str, params: dict[str, Any], timeout: float = 20.0, retries: int = 3) -> dict[str, Any]:
    full_url = f"{url}?{urlencode(params, safe=',()')}"
    request = Request(
        full_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://emweb.securities.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * attempt)
    raise RuntimeError(f"failed to fetch Eastmoney financial data: {last_error}") from last_error


def normalize_financial_row(row: dict[str, Any], code: str, updated_at: str) -> dict[str, str]:
    return {
        "report_period": normalize_date(row.get("REPORT_DATE")),
        "code": code,
        "roe": format_number(row.get("ROEJQ")),
        "roa": format_number(row.get("ROAZZL") if row.get("ROAZZL") is not None else row.get("ROA")),
        "gross_margin": format_number(row.get("XSMLL")),
        "net_margin": format_number(row.get("XSJLL")),
        "debt_ratio": format_number(row.get("ZCFZL")),
        "operating_cash_flow": format_number(row.get("NETCASH_OPERATE") if row.get("NETCASH_OPERATE") is not None else row.get("NETCASH_OPERATE_PK")),
        "revenue_growth_yoy": format_number(row.get("TOTALOPERATEREVETZ")),
        "net_profit_growth_yoy": format_number(row.get("PARENTNETPROFITTZ")),
        "deducted_net_profit_growth_yoy": format_number(row.get("KCFJCXSYJLRTZ")),
        "eps": format_number(row.get("BASIC_EPS")),
        "data_source": DATA_SOURCE,
        "updated_at": updated_at,
    }


def fetch_financial_rows_for_code(code: str, report_count: int, timeout: float = 20.0) -> list[dict[str, str]]:
    payload = get_json(
        FINANCE_URL,
        {
            "reportName": REPORT_NAME,
            "columns": "ALL",
            "filter": f'(SECUCODE="{security_code_with_exchange(code)}")',
            "pageNumber": 1,
            "pageSize": report_count,
            "sortColumns": "REPORT_DATE",
            "sortTypes": -1,
        },
        timeout=timeout,
    )
    rows = ((payload.get("result") or {}).get("data") or [])
    updated_at = date.today().isoformat()
    normalized = [normalize_financial_row(row, code, updated_at) for row in rows]
    return [row for row in normalized if row["report_period"] and row["code"]]


def read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def merge_rows(existing: list[dict[str, str]], fetched: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for row in existing + fetched:
        key = ((row.get("code") or "").strip(), (row.get("report_period") or "").strip())
        if key[0] and key[1]:
            merged[key] = row
    return [merged[key] for key in sorted(merged, key=lambda item: (item[0], item[1]))]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STANDARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in STANDARD_FIELDS})


def extract_codes_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "code" not in reader.fieldnames:
            raise ValueError(f"codes file must contain a code column: {path}")
        return [str(row.get("code") or "").strip() for row in reader if str(row.get("code") or "").strip()]


def fetch_financial_metrics(
    codes: list[str],
    output: Path,
    report_count: int,
    merge_existing: bool = True,
    archive_root: Path | None = Path("data/raw/snapshots"),
    workers: int = 1,
    timeout: float = 20.0,
    progress_every: int = 0,
) -> dict[str, Any]:
    started_at = datetime.now()
    fetched: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    unique_codes = list(dict.fromkeys(code.strip() for code in codes if code.strip()))

    if workers <= 1:
        for index, code in enumerate(unique_codes, start=1):
            try:
                fetched.extend(fetch_financial_rows_for_code(code, report_count, timeout))
            except Exception as exc:
                errors.append({"code": code, "message": str(exc)})
            if progress_every and index % progress_every == 0:
                print(f"progress: {index}/{len(unique_codes)} codes", file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_financial_rows_for_code, code, report_count, timeout): code for code in unique_codes}
            for index, future in enumerate(as_completed(futures), start=1):
                code = futures[future]
                try:
                    fetched.extend(future.result())
                except Exception as exc:
                    errors.append({"code": code, "message": str(exc)})
                if progress_every and index % progress_every == 0:
                    print(f"progress: {index}/{len(unique_codes)} codes", file=sys.stderr)

    existing = read_existing_rows(output) if merge_existing else []
    rows = merge_rows(existing, fetched)
    write_rows(output, rows)
    retained = retain_file_snapshot(output, "financial_metrics", archive_root) if archive_root is not None else None
    periods = sorted({row["report_period"] for row in fetched if row.get("report_period")})
    missing_by_field = {
        field: sum(1 for row in rows if not row.get(field))
        for field in ("roe", "roa", "gross_margin", "net_margin", "debt_ratio", "operating_cash_flow", "revenue_growth_yoy", "net_profit_growth_yoy")
    }
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": DATA_SOURCE,
        "mode": "incremental" if report_count <= 1 else "history",
        "report_count": report_count,
        "codes": unique_codes,
        "requested_code_count": len(unique_codes),
        "success_code_count": len(unique_codes) - len(errors),
        "fetched_row_count": len(fetched),
        "output_row_count": len(rows),
        "start_period": periods[0] if periods else None,
        "end_period": periods[-1] if periods else None,
        "output": str(output),
        "errors": errors,
        "missing_by_field": missing_by_field,
        "retained_snapshot": retained,
        "workers": workers,
        "duration_seconds": round((datetime.now() - started_at).total_seconds(), 3),
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"source: {metadata['source']}")
    print(f"mode: {metadata['mode']}")
    print(f"success codes: {metadata['success_code_count']}/{metadata['requested_code_count']}")
    print(f"fetched rows: {metadata['fetched_row_count']}")
    print(f"period range: {metadata['start_period'] or '-'} -> {metadata['end_period'] or '-'}")
    print(f"output: {metadata['output']}")
    if metadata["errors"]:
        print("errors:")
        for item in metadata["errors"]:
            print(f"- {item['code']}: {item['message']}")
    if metadata.get("retained_snapshot"):
        print(f"retained snapshot: {metadata['retained_snapshot']['path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch A-share financial metrics from Eastmoney.")
    parser.add_argument("--codes", nargs="*", default=[], help="6-digit stock codes.")
    parser.add_argument("--codes-file", help="CSV file with a code column, for example data/processed/tradable_universe.csv.")
    parser.add_argument("--report-count", type=int, default=1, help="Reports per code. Use 20 for initial 3-5 year bootstrap.")
    parser.add_argument("--output", default="data/processed/financial_metrics.csv")
    parser.add_argument("--metadata-output", default="data/metadata/financial_metrics.fetch.json")
    parser.add_argument("--replace", action="store_true", help="Replace output instead of merging with existing rows.")
    parser.add_argument("--archive-root", default="data/raw/snapshots")
    parser.add_argument("--no-archive", action="store_true", help="Do not retain a raw snapshot copy.")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent fetch workers. Keep modest for public endpoints.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress-every", type=int, default=0, help="Print progress to stderr every N completed codes.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        file_codes = extract_codes_from_csv(Path(args.codes_file)) if args.codes_file else []
        codes = list(dict.fromkeys(args.codes + file_codes))
        if not codes:
            raise ValueError("provide --codes or --codes-file")
        if args.report_count <= 0:
            raise ValueError("--report-count must be positive")
        metadata = fetch_financial_metrics(
            codes,
            Path(args.output),
            args.report_count,
            merge_existing=not args.replace,
            archive_root=None if args.no_archive else Path(args.archive_root),
            workers=args.workers,
            timeout=args.timeout,
            progress_every=args.progress_every,
        )
        write_metadata(Path(args.metadata_output), metadata)
    except Exception as exc:
        print(f"fetch Eastmoney financial metrics failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 1 if metadata["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
