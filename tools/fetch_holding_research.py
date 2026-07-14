#!/usr/bin/env python3
"""Fetch industry, valuation, financial metrics, and announcements for holdings."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from tools.check_portfolio_positions import expand_position_paths
    from tools.new_trade_plan import set_value, write_yaml
    from tools.risk_check import load_yaml, value_at
except ModuleNotFoundError:
    from check_portfolio_positions import expand_position_paths
    from new_trade_plan import set_value, write_yaml
    from risk_check import load_yaml, value_at


QUOTE_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
FINANCE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
ANNOUNCEMENT_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
RISK_KEYWORDS = (
    "立案", "调查", "处罚", "问询", "警示", "诉讼", "仲裁", "违规", "风险提示",
    "退市", "ST", "减持", "质押", "冻结", "预亏", "亏损", "停牌", "终止", "债务", "逾期",
)


def security_id(code: str) -> str:
    return f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"


def security_code_with_exchange(code: str) -> str:
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def get_json(url: str, params: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    full_url = f"{url}?{urlencode(params, safe=',()')}"
    request = Request(
        full_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://quote.eastmoney.com/",
            "Connection": "close",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as urllib_error:
        try:
            completed = subprocess.run(
                ["curl", "-L", "--fail", "--silent", "--show-error", "--max-time", str(int(timeout)), full_url],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(completed.stdout)
        except Exception as curl_error:
            raise RuntimeError(f"urllib failed: {urllib_error}; curl fallback failed: {curl_error}") from curl_error


def scaled(value: Any, divisor: float = 100.0) -> float | None:
    if value in (None, "-"):
        return None
    try:
        return round(float(value) / divisor, 6)
    except (TypeError, ValueError):
        return None


def fetch_quote_profile(code: str) -> dict[str, Any]:
    payload = get_json(
        QUOTE_URL,
        {"secid": security_id(code), "fields": "f43,f57,f58,f116,f117,f127,f128,f129,f162,f167"},
    )
    data = payload.get("data") or {}
    if not data:
        raise ValueError("empty quote profile")
    return {
        "code": str(data.get("f57") or code),
        "name": data.get("f58"),
        "latest_price": scaled(data.get("f43")),
        "total_market_value": data.get("f116"),
        "float_market_value": data.get("f117"),
        "industry": data.get("f127"),
        "region": data.get("f128"),
        "concepts": [item for item in str(data.get("f129") or "").split(",") if item],
        "pe_ttm": scaled(data.get("f162")),
        "pb": scaled(data.get("f167")),
    }


def fetch_realtime_quotes(codes: list[str]) -> list[dict[str, Any]]:
    payload = get_json(
        QUOTE_URL.replace("/stock/get", "/ulist.np/get"),
        {
            "secids": ",".join(security_id(code) for code in codes),
            "fields": "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18,f62,f66,f69,f72,f75,f78,f81,f84,f87,f184,f86,f124",
        },
    )
    rows = ((payload.get("data") or {}).get("diff") or [])
    quotes: list[dict[str, Any]] = []
    for row in rows:
        quotes.append(
            {
                "code": str(row.get("f12") or ""),
                "name": row.get("f14"),
                "latest_price": scaled(row.get("f2")),
                "change_pct": scaled(row.get("f3")),
                "change_amount": scaled(row.get("f4")),
                "volume": row.get("f5"),
                "turnover": row.get("f6"),
                "high": scaled(row.get("f15")),
                "low": scaled(row.get("f16")),
                "open": scaled(row.get("f17")),
                "previous_close": scaled(row.get("f18")),
                "main_net_inflow": row.get("f62"),
                "main_net_inflow_ratio_pct": scaled(row.get("f184")),
                "super_large_net_inflow": row.get("f66"),
                "super_large_net_inflow_ratio_pct": scaled(row.get("f69")),
                "large_net_inflow": row.get("f72"),
                "large_net_inflow_ratio_pct": scaled(row.get("f75")),
                "medium_net_inflow": row.get("f78"),
                "medium_net_inflow_ratio_pct": scaled(row.get("f81")),
                "small_net_inflow": row.get("f84"),
                "small_net_inflow_ratio_pct": scaled(row.get("f87")),
                "quote_clock": row.get("f86"),
                "quote_timestamp": row.get("f124"),
            }
        )
    return quotes


def fetch_latest_financials(code: str) -> dict[str, Any] | None:
    payload = get_json(
        FINANCE_URL,
        {
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": "ALL",
            "filter": f'(SECUCODE="{security_code_with_exchange(code)}")',
            "pageNumber": 1,
            "pageSize": 1,
            "sortColumns": "REPORT_DATE",
            "sortTypes": -1,
        },
    )
    rows = ((payload.get("result") or {}).get("data") or [])
    if not rows:
        return None
    row = rows[0]
    return {
        "report_date": row.get("REPORT_DATE"),
        "report_type": row.get("REPORT_TYPE"),
        "notice_date": row.get("NOTICE_DATE"),
        "revenue": row.get("TOTALOPERATEREVE"),
        "revenue_yoy_pct": row.get("TOTALOPERATEREVETZ"),
        "parent_net_profit": row.get("PARENTNETPROFIT"),
        "parent_net_profit_yoy_pct": row.get("PARENTNETPROFITTZ"),
        "deducted_net_profit_yoy_pct": row.get("KCFJCXSYJLRTZ"),
        "roe_weighted_pct": row.get("ROEJQ"),
        "gross_margin_pct": row.get("XSMLL"),
        "net_margin_pct": row.get("XSJLL"),
        "debt_ratio_pct": row.get("ZCFZL"),
        "operating_cash_flow": row.get("NETCASH_OPERATE_PK"),
    }


def announcement_risk_keywords(title: str) -> list[str]:
    return [keyword for keyword in RISK_KEYWORDS if keyword.lower() in title.lower()]


def financial_review_flags(quote: dict[str, Any], financials: dict[str, Any] | None) -> list[dict[str, str]]:
    if not financials:
        return [{"code": "missing_financials", "message": "未获取到最新财务指标。"}]
    flags: list[dict[str, str]] = []
    revenue_yoy = financials.get("revenue_yoy_pct")
    profit_yoy = financials.get("parent_net_profit_yoy_pct")
    roe = financials.get("roe_weighted_pct")
    debt_ratio = financials.get("debt_ratio_pct")
    pe_ttm = quote.get("pe_ttm")
    if revenue_yoy is not None and revenue_yoy <= -10:
        flags.append({"code": "revenue_decline", "message": f"最新报告期营收同比下降 {abs(revenue_yoy):.2f}%。"})
    if profit_yoy is not None and profit_yoy <= -20:
        flags.append({"code": "profit_decline", "message": f"最新报告期归母净利润同比下降 {abs(profit_yoy):.2f}%。"})
    if roe is not None and roe < 0:
        flags.append({"code": "negative_roe", "message": f"最新报告期加权 ROE 为 {roe:.2f}%。"})
    industry = str(quote.get("industry") or "")
    financial_industry = any(keyword in industry for keyword in ("银行", "证券", "保险"))
    if debt_ratio is not None and debt_ratio > 75 and not financial_industry:
        flags.append({"code": "high_debt_ratio", "message": f"最新报告期资产负债率为 {debt_ratio:.2f}%。"})
    if pe_ttm is not None and pe_ttm < 0:
        flags.append({"code": "negative_pe", "message": "滚动 PE 为负，通常表示滚动口径净利润为负。"})
    return flags


def fetch_announcements(code: str, page_size: int = 20) -> list[dict[str, Any]]:
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
    )
    rows = ((payload.get("data") or {}).get("list") or [])
    items: list[dict[str, Any]] = []
    for row in rows:
        title = str(row.get("title_ch") or row.get("title") or "")
        items.append(
            {
                "article_code": row.get("art_code"),
                "notice_date": row.get("notice_date"),
                "title": title,
                "categories": [item.get("column_name") for item in row.get("columns", []) if item.get("column_name")],
                "risk_keywords": announcement_risk_keywords(title),
            }
        )
    return items


def build_holding_research(position_paths: list[Path], announcement_count: int = 20) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in position_paths:
        position = load_yaml(path)
        code = str(value_at(position, "stock.code") or "")
        try:
            quote = fetch_quote_profile(code)
            financials = fetch_latest_financials(code)
            announcements = fetch_announcements(code, page_size=announcement_count)
            risk_announcements = [item for item in announcements if item["risk_keywords"]]
            financial_flags = financial_review_flags(quote, financials)
            items.append(
                {
                    "position_path": str(path),
                    "code": code,
                    "broker_name": value_at(position, "stock.name"),
                    "quote_profile": quote,
                    "latest_financials": financials,
                    "financial_review": {
                        "requires_manual_review": bool(financial_flags),
                        "flags": financial_flags,
                        "note": "阈值筛查仅用于定位需要复核的财务变化，不等同于基本面结论。",
                    },
                    "announcements": announcements,
                    "risk_review": {
                        "requires_manual_review": bool(risk_announcements),
                        "matched_announcement_count": len(risk_announcements),
                        "matched_announcements": risk_announcements,
                        "note": "关键词命中仅表示需要阅读原公告，不代表利空或交易结论。",
                    },
                }
            )
        except Exception as exc:
            errors.append({"code": code, "position_path": str(path), "message": str(exc)})
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": "eastmoney_public_web_api",
        "position_count": len(position_paths),
        "success_count": len(items),
        "error_count": len(errors),
        "items": items,
        "errors": errors,
    }


def update_position_industries(report: dict[str, Any]) -> int:
    updated = 0
    for item in report["items"]:
        industry = item["quote_profile"].get("industry")
        if not industry:
            continue
        path = Path(item["position_path"])
        position = load_yaml(path)
        set_value(position, "stock.industry", industry)
        write_yaml(path, position, overwrite=True)
        updated += 1
    return updated


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 持仓基本面与公告快照",
        "",
        f"抓取时间：{report['fetched_at']}",
        "",
        "数据用于筛查，公告关键词命中必须阅读原文后才能形成结论。",
        "",
        "| 代码 | 名称 | 行业 | PE(TTM) | PB | 报告期 | 营收同比 | 归母净利同比 | ROE | 公告风险命中 |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report["items"]:
        quote = item["quote_profile"]
        fin = item["latest_financials"] or {}
        def fmt(value: Any) -> str:
            return "-" if value is None else f"{float(value):.2f}"
        def fmt_pct(value: Any) -> str:
            return "-" if value is None else f"{float(value):.2f}%"
        report_date = str(fin.get("report_date") or "-")[:10]
        lines.append(
            f"| {item['code']} | {quote.get('name') or item['broker_name']} | {quote.get('industry') or '-'} | "
            f"{fmt(quote.get('pe_ttm'))} | {fmt(quote.get('pb'))} | {report_date} | "
            f"{fmt_pct(fin.get('revenue_yoy_pct'))} | {fmt_pct(fin.get('parent_net_profit_yoy_pct'))} | "
            f"{fmt_pct(fin.get('roe_weighted_pct'))} | {item['risk_review']['matched_announcement_count']} |"
        )
    if report["errors"]:
        lines.extend(["", "## 抓取错误", ""])
        lines.extend(f"- {item['code']}: {item['message']}" for item in report["errors"])
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch holding fundamentals and announcements.")
    parser.add_argument("--positions", nargs="+", required=True)
    parser.add_argument("--announcement-count", type=int, default=20)
    parser.add_argument("--output", default="data/metadata/holding-research.json")
    parser.add_argument("--markdown-output", default="reports/holding-research.md")
    parser.add_argument("--update-position-industries", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_holding_research(expand_position_paths(args.positions), args.announcement_count)
        report["updated_position_industries"] = update_position_industries(report) if args.update_position_industries else 0
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown = Path(args.markdown_output)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(report), encoding="utf-8")
    except Exception as exc:
        print(f"fetch holding research failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"positions: {report['position_count']}")
        print(f"success: {report['success_count']}")
        print(f"errors: {report['error_count']}")
        print(f"updated industries: {report['updated_position_industries']}")
        print(f"output: {args.output}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
