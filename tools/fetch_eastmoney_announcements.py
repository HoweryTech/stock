#!/usr/bin/env python3
"""Fetch Eastmoney announcements and normalize initial event catalyst inputs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from tools.data_retention import retain_file_snapshot
except ModuleNotFoundError:
    from data_retention import retain_file_snapshot


ANNOUNCEMENT_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
DATA_SOURCE = "eastmoney_security_ann"

OUTPUT_FIELDS = [
    "event_date",
    "code",
    "event_type",
    "title",
    "expected_impact",
    "impact_score",
    "confidence",
    "counter_evidence",
    "risk_disclosure",
    "source",
    "updated_at",
    "announcement_id",
    "categories",
    "risk_keywords",
]

RISK_KEYWORDS = (
    "立案", "调查", "处罚", "问询", "警示", "诉讼", "仲裁", "违规", "风险提示",
    "退市", "ST", "减持", "质押", "冻结", "预亏", "亏损", "停牌", "终止", "债务", "逾期",
)

EVENT_RULES: list[tuple[str, tuple[str, ...], str, float, float]] = [
    ("shareholder_reduce", ("减持",), "股东减持可能压制风险偏好，需先复核减持规模和期限。", -4.0, 3.0),
    ("regulatory_inquiry", ("问询", "监管", "警示", "处罚", "立案", "调查"), "监管事项需要先完成风险复核。", -4.0, 3.0),
    ("litigation", ("诉讼", "仲裁"), "诉讼仲裁事项需要核查金额、进展和潜在损失。", -3.0, 3.0),
    ("financial_correction", ("会计差错", "更正", "追溯调整"), "财务更正影响报表可信度，需要先复核。", -3.0, 3.0),
    ("unlock", ("解禁", "限售股上市流通"), "解禁可能带来供给压力，需要核查规模和股东性质。", -2.0, 2.0),
    ("share_repurchase", ("回购",), "回购可能改善市场预期和股东回报。", 3.5, 2.5),
    ("shareholder_increase", ("增持",), "股东增持可能改善短期风险偏好。", 3.0, 2.5),
    ("earnings_forecast", ("业绩预告", "预增", "扭亏", "业绩快报"), "业绩变化可能影响盈利预期。", 3.5, 2.5),
    ("major_order", ("重大合同", "重大订单", "中标", "签订合同"), "订单或合同可能改善收入预期。", 3.5, 2.5),
    ("merger_restructuring", ("重组", "并购", "收购", "资产购买"), "并购重组可能改变资产质量和盈利预期。", 3.0, 2.0),
    ("new_product", ("新产品", "获批", "注册证", "临床", "上市许可"), "产品进展可能带来业务增量。", 3.0, 2.0),
    ("policy_catalyst", ("补贴", "专项资金", "政策", "项目获批"), "政策或项目支持可能改善业务预期。", 3.0, 2.0),
]


def get_json(url: str, params: dict[str, Any], timeout: float = 20.0, retries: int = 3) -> dict[str, Any]:
    full_url = f"{url}?{urlencode(params)}"
    request = Request(
        full_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/notices/",
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
    raise RuntimeError(f"failed to fetch Eastmoney announcements: {last_error}") from last_error


def parse_date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if text else ""


def risk_keywords(title: str) -> list[str]:
    lowered = title.lower()
    return [keyword for keyword in RISK_KEYWORDS if keyword.lower() in lowered]


def classify_event(title: str) -> tuple[str, str, float, float]:
    for event_type, keywords, expected_impact, impact_score, confidence in EVENT_RULES:
        if any(keyword in title for keyword in keywords):
            return event_type, expected_impact, impact_score, confidence
    return "other", "公告标题暂未命中明确催化规则，先保留为人工复核输入。", 0.0, 1.0


def counter_evidence_for(event_type: str) -> str:
    if event_type in {"share_repurchase", "shareholder_increase"}:
        return "需核查金额、实施期限、资金来源和历史完成率。"
    if event_type == "earnings_forecast":
        return "需核查业绩增长来源、一次性损益和后续持续性。"
    if event_type == "major_order":
        return "需核查合同金额、交付节奏、毛利率和回款条件。"
    if event_type == "merger_restructuring":
        return "需核查交易对价、业绩承诺、审批进度和整合风险。"
    if event_type == "new_product":
        return "需核查商业化进度、市场空间、竞争格局和审批限制。"
    if event_type == "policy_catalyst":
        return "需核查政策兑现路径、补贴金额和实际落地时间。"
    if event_type == "other":
        return "事件类型未明确，需人工阅读公告原文。"
    return "风险事件需先阅读公告原文并确认影响范围。"


def risk_disclosure_for(event_type: str, title: str) -> str:
    risks = risk_keywords(title)
    if event_type in {"shareholder_reduce", "regulatory_inquiry", "litigation", "financial_correction", "unlock"}:
        return f"风险事件：{', '.join(risks) if risks else event_type}；未复核前不进入正向催化候选。"
    return "公告标题分类仅为初筛，若后续公告内容不及预期或存在反证，事件催化失效。"


def normalize_announcement(code: str, row: dict[str, Any], updated_at: str) -> dict[str, str]:
    title = str(row.get("title_ch") or row.get("title") or "").strip()
    event_type, expected_impact, impact_score, confidence = classify_event(title)
    announcement_id = str(row.get("art_code") or row.get("article_code") or "").strip()
    categories = [str(item.get("column_name") or "") for item in row.get("columns", []) if item.get("column_name")]
    return {
        "event_date": parse_date(row.get("notice_date")),
        "code": code,
        "event_type": event_type,
        "title": title,
        "expected_impact": expected_impact,
        "impact_score": str(int(impact_score)) if float(impact_score).is_integer() else str(impact_score),
        "confidence": str(int(confidence)) if float(confidence).is_integer() else str(confidence),
        "counter_evidence": counter_evidence_for(event_type),
        "risk_disclosure": risk_disclosure_for(event_type, title),
        "source": DATA_SOURCE,
        "updated_at": updated_at,
        "announcement_id": announcement_id,
        "categories": "|".join(categories),
        "risk_keywords": "|".join(risk_keywords(title)),
    }


def fetch_announcements_for_code(code: str, page_size: int = 20, timeout: float = 20.0) -> list[dict[str, Any]]:
    payload = get_json(
        ANNOUNCEMENT_URL,
        {
            "sr": -1,
            "page_size": page_size,
            "page_index": 1,
            "ann_type": "A",
            "client_source": "web",
            "stock_list": code,
        },
        timeout=timeout,
    )
    return list(((payload.get("data") or {}).get("list") or []))


def read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def merge_rows(existing: list[dict[str, str]], fetched: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in existing + fetched:
        announcement_id = (row.get("announcement_id") or "").strip()
        key = (
            announcement_id or (row.get("code") or "").strip(),
            (row.get("event_type") or "").strip(),
            (row.get("event_date") or "").strip() + "|" + (row.get("title") or "").strip(),
        )
        if key[0] and key[1] and key[2]:
            merged[key] = row
    return [merged[key] for key in sorted(merged, key=lambda item: (item[2], item[0], item[1]), reverse=True)]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})


def extract_codes_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "code" not in reader.fieldnames:
            raise ValueError(f"codes file must contain a code column: {path}")
        return [str(row.get("code") or "").strip() for row in reader if str(row.get("code") or "").strip()]


def fetch_event_catalyst_events(
    codes: list[str],
    output: Path,
    lookback_days: int,
    page_size: int,
    merge_existing: bool = True,
    archive_root: Path | None = Path("data/raw/snapshots"),
    workers: int = 1,
    timeout: float = 20.0,
    progress_every: int = 0,
) -> dict[str, Any]:
    started_at = datetime.now()
    updated_at = date.today().isoformat()
    cutoff = date.today() - timedelta(days=lookback_days)
    unique_codes = list(dict.fromkeys(code.strip() for code in codes if code.strip()))
    fetched: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    def normalize_for_code(code: str) -> list[dict[str, str]]:
        rows = fetch_announcements_for_code(code, page_size=page_size, timeout=timeout)
        normalized = [normalize_announcement(code, row, updated_at) for row in rows]
        return [row for row in normalized if row["title"] and row["event_date"] and date.fromisoformat(row["event_date"]) >= cutoff]

    if workers <= 1:
        for index, code in enumerate(unique_codes, start=1):
            try:
                fetched.extend(normalize_for_code(code))
            except Exception as exc:
                errors.append({"code": code, "message": str(exc)})
            if progress_every and index % progress_every == 0:
                print(f"progress: {index}/{len(unique_codes)} codes", file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(normalize_for_code, code): code for code in unique_codes}
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
    retained = retain_file_snapshot(output, "event_catalyst_events", archive_root) if archive_root is not None else None
    event_type_counts: dict[str, int] = {}
    for row in rows:
        event_type_counts[row["event_type"]] = event_type_counts.get(row["event_type"], 0) + 1
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": DATA_SOURCE,
        "lookback_days": lookback_days,
        "cutoff_date": cutoff.isoformat(),
        "codes": unique_codes,
        "requested_code_count": len(unique_codes),
        "success_code_count": len(unique_codes) - len(errors),
        "fetched_row_count": len(fetched),
        "output_row_count": len(rows),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "output": str(output),
        "errors": errors,
        "retained_snapshot": retained,
        "workers": workers,
        "duration_seconds": round((datetime.now() - started_at).total_seconds(), 3),
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(metadata: dict[str, Any]) -> None:
    print(f"source: {metadata['source']}")
    print(f"success codes: {metadata['success_code_count']}/{metadata['requested_code_count']}")
    print(f"fetched rows: {metadata['fetched_row_count']}")
    print(f"output rows: {metadata['output_row_count']}")
    print(f"event types: {metadata['event_type_counts']}")
    print(f"output: {metadata['output']}")
    if metadata["errors"]:
        print("errors:")
        for item in metadata["errors"]:
            print(f"- {item['code']}: {item['message']}")
    if metadata.get("retained_snapshot"):
        print(f"retained snapshot: {metadata['retained_snapshot']['path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Eastmoney announcements and normalize event catalyst inputs.")
    parser.add_argument("--codes", nargs="*", default=[], help="6-digit stock codes.")
    parser.add_argument("--codes-file", help="CSV file with a code column, for example data/processed/tradable_universe.csv.")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--output", default="data/processed/event_catalyst_events.csv")
    parser.add_argument("--metadata-output", default="data/metadata/event_catalyst_events.fetch.json")
    parser.add_argument("--replace", action="store_true", help="Replace output instead of merging with existing rows.")
    parser.add_argument("--archive-root", default="data/raw/snapshots")
    parser.add_argument("--no-archive", action="store_true", help="Do not retain a raw snapshot copy.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress-every", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        file_codes = extract_codes_from_csv(Path(args.codes_file)) if args.codes_file else []
        codes = list(dict.fromkeys(args.codes + file_codes))
        if not codes:
            raise ValueError("provide --codes or --codes-file")
        if args.lookback_days < 0:
            raise ValueError("--lookback-days must be non-negative")
        metadata = fetch_event_catalyst_events(
            codes,
            Path(args.output),
            args.lookback_days,
            args.page_size,
            merge_existing=not args.replace,
            archive_root=None if args.no_archive else Path(args.archive_root),
            workers=args.workers,
            timeout=args.timeout,
            progress_every=args.progress_every,
        )
        write_metadata(Path(args.metadata_output), metadata)
    except Exception as exc:
        print(f"fetch Eastmoney announcements failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print_summary(metadata)
    return 1 if metadata["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
