"""Native observations of intermediation; no latent A, headroom or liquidity score.

Public first-party NYFed/FINRA/issuer/ICI sources. Missing fields and access
failures are not replaced with an economic proxy or a remembered web value.
"""
from __future__ import annotations
import calendar
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from .pboc import _HTML, _expanded
from model.live_api import _validate_output_root

PROVIDER = "intermediary"
USER_AGENT = "MacroLiquidityDashboard/1.2 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
NY_BASE = "https://markets.newyorkfed.org"
NY_BREAKS = NY_BASE + "/api/pd/list/seriesbreaks.json"
NY_DEFS = NY_BASE + "/api/pd/list/timeseries.json"
FINRA = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
JPM_BASE = "https://www.jpmorganchase.com"
JPM_FEED = JPM_BASE + "/services/json/v1/investor-relations/quarterly-earnings.json"
ICI_BASE = "https://www.ici.org"
ICI_INDEX = ICI_BASE + "/monthly-active-and-index-data"
PD_SERIES = {
    "PDPOSGST-TOT": ("NYFED_PD_UST_NET_POSITION", "딜러 미 국채 순포지션 · TIPS 제외", "U.S. TREASURY SECURITIES", "DEALER POSITION"),
    "PDSORA-UTSETTOT": ("NYFED_PD_UST_REPO", "딜러 미 국채 repo 잔액 · TIPS 제외", "Repurchase Agreements:", "excluding TIPS"),
    "PDSIRRA-UTSETTOT": ("NYFED_PD_UST_REVERSE_REPO", "딜러 미 국채 reverse repo 잔액 · TIPS 제외", "Reverse Repurchase Agreements:", "excluding TIPS"),
}
FINRA_SERIES = [
    ("FINRA_MARGIN_DEBT", "FINRA 고객 증권 마진부채", "Debit Balances in Customers' Securities Margin Accounts"),
    ("FINRA_CASH_FREE_CREDIT", "FINRA 현금계좌 자유신용잔액", "Free Credit Balances in Customers' Cash Accounts"),
    ("FINRA_MARGIN_FREE_CREDIT", "FINRA 마진계좌 자유신용잔액", "Free Credit Balances in Customers' Securities Margin Accounts"),
]
ICI_SERIES = {
    "Domestic equity": ("ICI_DOMESTIC_EQUITY_INDEX_SHARE", "ICI 미국 주식 펀드 인덱스 비중"),
    "World equity": ("ICI_WORLD_EQUITY_INDEX_SHARE", "ICI 해외 주식 펀드 인덱스 비중"),
    "Total": ("ICI_LONG_TERM_INDEX_SHARE", "ICI 장기 펀드 인덱스 비중"),
}
TOTAL_SERIES = 10
ASSUMPTIONS = [
    "NYFed reports dealer inventories and gross repo financing; these are observations of balance-sheet use, not remaining capacity.",
    "FINRA monthly customer margin balances use settlement-date reporting and retain the reported member-firm population.",
    "JPM SLR is one consolidated issuer's reported quarterly ratio. No universal regulatory minimum or headroom is imposed.",
    "ICI index share uses reported index mutual fund plus index ETF assets divided by active plus index assets for the identical fund category.",
    "All levels are current-vintage source observations only; model transformations, A_t estimation and historical point-in-time research are separate.",
]
LIMITATIONS = [
    "No series identifies the causal effect of intermediation on prices, and no single global liquidity score is constructed.",
    "Position signs have no fixed capacity interpretation. Repo and reverse repo are gross sides, not additive market liquidity or collateral reuse.",
    "One bank's SLR does not measure all dealers, intra-group constraints, binding requirements or usable headroom.",
    "Index fund share is not all-market passive ownership or an estimate of the Gabaix–Koijen multiplier.",
    "ICI public pages can return HTTP 403. The collector reports this failure without bypassing access controls or importing search-engine values.",
    "Original release instants are not verified. known_by is first collector capture; historical revisions are not backdated.",
]


class IntermediaryError(RuntimeError):
    pass


def now(clock):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def allowed_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
        raise IntermediaryError("unapproved_source_url")
    valid = url in (NY_BREAKS, NY_DEFS, FINRA, JPM_FEED, ICI_INDEX)
    valid |= p.netloc == "markets.newyorkfed.org" and bool(re.fullmatch(r"/api/pd/latest/SB[A-Z]\d{4}\.json", p.path))
    valid |= p.netloc == "www.jpmorganchase.com" and bool(re.fullmatch(r"/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/quarterly-earnings/20\d{2}/(?:1st|2nd|3rd|4th)-quarter/[A-Za-z0-9-]+\.xlsx", p.path))
    valid |= p.netloc == "www.ici.org" and bool(re.fullmatch(r"/research/stats/combined_active_index_\d{4}", p.path))
    if not valid:
        raise IntermediaryError("unapproved_source_url")
    return url


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url, root, transport, clock):
    allowed_url(url)
    if transport:
        body = transport(url)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        with urllib.request.build_opener(Redirect()).open(req, timeout=25) as response:
            allowed_url(response.geturl())
            body = response.read(8_000_001)
    if not isinstance(body, bytes) or not body or len(body) > 8_000_000:
        raise IntermediaryError("invalid_source_size")
    captured = stamp(now(clock))
    digest = hashlib.sha256(body).hexdigest()
    suffix = ".xlsx" if url.endswith(".xlsx") else ".json" if url.endswith(".json") else ".html"
    relative = Path("raw/intermediary") / (digest + suffix)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body:
            raise IntermediaryError("immutable_source_conflict")
    else:
        with target.open("xb") as handle:
            handle.write(body)
    return body, {"source_url": url, "raw_path": str(relative), "raw_sha256": digest,
                  "known_by": captured, "retrieved_at": captured, "original_release_at": None,
                  "release_timestamp_verified": False, "raw_scope": "original_public_first_party_document"}


def numeric(value, *, signed=False):
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value.strip()):
        raise IntermediaryError("missing_or_invalid_number")
    result = float(value.replace(",", ""))
    if not math.isfinite(result) or abs(result) > 1e12 or (result < 0 and not signed):
        raise IntermediaryError("number_out_of_range")
    return result


def base(series, label, value, end, frequency, block, *, unit="million USD", **extra):
    return {"series_id": series, "label": label, "value": value, "unit": unit, "units": unit,
            "provider": PROVIDER, "layer": 3, "track": "equity", "role": "operator_observation",
            "block": block, "frequency": frequency, "observation_date": end, "period_start": end,
            "period_end": end, "measurement_kind": "reported_native_stock_or_ratio", "research_eligible": False,
            "aggregation_allowed": False, **extra}


def choose_break(payload, today):
    rows = payload.get("pd", {}).get("seriesbreaks")
    if not isinstance(rows, list):
        raise IntermediaryError("nyfed_seriesbreak_schema_changed")
    active = [r for r in rows if date.fromisoformat(r["startdate"]) <= today <= date.fromisoformat(r["enddate"])]
    if len(active) != 1 or active[0].get("seriesbreak") != "SBN2024":
        raise IntermediaryError("nyfed_seriesbreak_requires_definition_review")
    return active[0]["seriesbreak"]


def parse_pd(payload, definitions, seriesbreak, today):
    defs = definitions.get("pd", {}).get("timeseries")
    rows = payload.get("pd", {}).get("timeseries")
    if not isinstance(defs, list) or not isinstance(rows, list):
        raise IntermediaryError("nyfed_schema_changed")
    result = []
    for key, (series, label, token1, token2) in PD_SERIES.items():
        found = [r for r in defs if r.get("seriesbreak") == seriesbreak and r.get("keyid") == key]
        if len(found) != 1 or any(t.casefold() not in found[0].get("description", "").casefold() for t in (token1, token2)):
            raise IntermediaryError("nyfed_series_definition_changed")
        observations = [r for r in rows if r.get("keyid") == key]
        if len(observations) != 1:
            raise IntermediaryError("nyfed_latest_series_missing_or_duplicated")
        row = observations[0]
        end = date.fromisoformat(row["asofdate"])
        if end > today or end.weekday() != 2:
            raise IntermediaryError("nyfed_invalid_reference_date")
        value = numeric(row["value"], signed=key == "PDPOSGST-TOT")
        result.append(base(series, label, value, end.isoformat(), "weekly", "dealer_balance_sheet_use",
                           native_series_id=key, seriesbreak=seriesbreak, source_label=found[0]["description"],
                           source_universe="New York Fed reporting primary dealers; U.S. Treasury securities excluding TIPS",
                           status="stale" if (today-end).days > 21 else "ok",
                           notes="딜러 잔액 관측. 잔여 여력·담보 재사용률·A_t 추정치가 아님."))
    if len({r["period_end"] for r in result}) != 1:
        raise IntermediaryError("nyfed_latest_dates_disagree")
    return result


def month_end(year, month, today):
    end = date(year, month, calendar.monthrange(year, month)[1])
    if end > today:
        raise IntermediaryError("future_reference_period")
    return end


def parse_finra(body, today):
    text = body.decode("utf-8-sig")
    if "shown in $ millions" not in text:
        raise IntermediaryError("finra_units_changed")
    rows = [_expanded(r) for r in _HTML(text).rows]
    headers = [r for r in rows if r and r[0] == "Month/Year"]
    if headers != [["Month/Year", *(r[2] for r in FINRA_SERIES)]]:
        raise IntermediaryError("finra_columns_changed")
    candidates = {}
    for row in rows:
        if not row or not re.fullmatch(r"[A-Z][a-z]{2}-\d{2}", row[0]):
            continue
        dt = datetime.strptime(row[0], "%b-%y")
        end = month_end(dt.year, dt.month, today)
        if end in candidates or len(row) != 4:
            raise IntermediaryError("finra_duplicate_or_invalid_row")
        candidates[end] = [numeric(v) for v in row[1:]]
    if not candidates:
        raise IntermediaryError("finra_monthly_balances_missing")
    end = max(candidates)
    return [base(series, label, value, end.isoformat(), "monthly", "customer_margin", source_label=source,
                 source_universe="FINRA reporting member firms carrying customer margin accounts",
                 reference_basis="last_business_day_settlement_date_balances_labelled_by_calendar_month",
                 status="stale" if (today-end).days > 75 else "ok",
                 notes="월말 고객계좌 잔액. 월간 변동은 보고 방식·회원사 범위 변화도 포함할 수 있음.")
            for (series, label, source), value in zip(FINRA_SERIES, candidates[end])]


def select_jpm(payload, today):
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise IntermediaryError("jpm_feed_schema_changed")
    choices = []
    quarters = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
    for row in rows:
        if row.get("quarter") not in quarters:
            raise IntermediaryError("jpm_quarter_schema_changed")
        year, quarter = int(row["year"]), quarters[row["quarter"]]
        end = month_end(year, quarter * 3, today)
        choices.append((end, row))
    if not choices:
        raise IntermediaryError("jpm_current_supplement_missing")
    latest = max(d for d, row in choices)
    selected = []
    # Do not let unrelated legacy workbook URL formats block the current quarter,
    # and never silently fall back to an older quarter if its current file is absent.
    for end, row in choices:
        if end != latest:
            continue
        doc = row.get("docs", {}).get("tenkSupplementalDoc")
        if not doc:
            raise IntermediaryError("jpm_current_supplement_missing")
        year, quarter = end.year, quarters[row["quarter"]]
        if doc.get("title") != f"{quarter}Q{year%100:02d} Earnings Supplement (xls)":
            raise IntermediaryError("jpm_document_identity_changed")
        url = allowed_url(urllib.parse.urljoin(JPM_BASE, doc["link"]))
        if f"/{year}/{row['quarter']}-quarter/" not in url:
            raise IntermediaryError("jpm_url_period_mismatch")
        selected.append((end, url))
    selected = set(selected)
    if len(selected) != 1:
        raise IntermediaryError("jpm_current_documents_ambiguous")
    return selected.pop()


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def parse_jpm(body, end, today):
    # The original workbook is preserved. Only the capital table is interpreted.
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        if sum(i.file_size for i in z.infolist()) > 40_000_000 or len(z.infolist()) > 300:
            raise IntermediaryError("xlsx_expansion_limit")
        if any(i.filename.endswith("vbaProject.bin") for i in z.infolist()):
            raise IntermediaryError("unexpected_workbook_macro")
        shared = ET.fromstring(z.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.findall(".//m:t", NS)) for si in shared]
        styles = ET.fromstring(z.read("xl/styles.xml"))
        custom = {int(x.get("numFmtId")): x.get("formatCode", "") for x in styles.findall("m:numFmts/m:numFmt", NS)}
        formats = [int(x.get("numFmtId", "0")) for x in styles.findall("m:cellXfs/m:xf", NS)]
        capital = []
        for name in z.namelist():
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            rows = []
            for row in ET.fromstring(z.read(name)).findall("m:sheetData/m:row", NS):
                cells = {}
                for c in row.findall("m:c", NS):
                    v = c.find("m:v", NS)
                    value = v.text if v is not None else ""
                    if c.get("t") == "s" and value:
                        value = strings[int(value)]
                    elif c.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in c.findall(".//m:t", NS))
                    cells[re.sub(r"\d", "", c.get("r", ""))] = (value or "", int(c.get("s", "0")))
                rows.append(cells)
            if any(any(v[0] == "SLR" for v in row.values()) for row in rows):
                capital.append((name, rows))
        if len(capital) != 1:
            raise IntermediaryError("jpm_capital_table_missing_or_ambiguous")
        name, rows = capital[0]
        words = " ".join(v[0] for row in rows for v in row.values())
        if not all(t in words for t in ("JPMORGAN CHASE", "CAPITAL AND OTHER SELECTED BALANCE SHEET ITEMS", "Leverage-based capital metrics", "in millions")):
            raise IntermediaryError("jpm_capital_table_identity_changed")
        # Identify the reference-date column via two adjacent header rows.
        dates = []
        for index, row in enumerate(rows[:-1]):
            for column, (value, style) in row.items():
                if value != end.strftime("%b %d,"):
                    continue
                if rows[index+1].get(column, ("", 0))[0] == str(end.year):
                    dates.append(column)
        if len(dates) != 1:
            raise IntermediaryError("jpm_reference_column_ambiguous")
        column = dates[0]

        def row_value(label):
            matches = [r[column] for r in rows if any(c[0] == label for c in r.values()) and column in r]
            if not matches or len(set(matches)) != 1:
                raise IntermediaryError("jpm_capital_value_missing_or_conflicting")
            return matches[0]

        raw_slr, style = row_value("SLR")
        fmt = formats[style]
        if fmt not in (9, 10) and "%" not in custom.get(fmt, ""):
            raise IntermediaryError("jpm_slr_percent_format_missing")
        slr = float(raw_slr) * 100
        capital_value = float(row_value("Tier 1 capital")[0])
        exposure = float(row_value("Total leverage exposure")[0])
        if not all(math.isfinite(v) for v in (slr, capital_value, exposure)) or not 0 < slr < 100 or not 0 < capital_value < exposure:
            raise IntermediaryError("jpm_capital_values_invalid")
        if abs(slr - 100 * capital_value / exposure) > 0.055:
            raise IntermediaryError("jpm_slr_does_not_reconcile_to_capital_and_exposure")
        return [base("JPM_REPORTED_SLR", "JPM 보고 SLR · 단일 연결회사", round(slr, 6), end.isoformat(), "quarterly", "reported_regulatory_ratio", unit="percent",
                     issuer="JPMorgan Chase & Co.", ticker="JPM", source_universe="One consolidated holding company; not its individual broker-dealers",
                     components={"tier1_capital_million_usd": capital_value, "total_leverage_exposure_million_usd": exposure},
                     source_label="SLR in Capital and other selected balance sheet items", source_sheet=name,
                     status="stale" if (today-end).days > 180 else "ok",
                     notes="단일 회사 분기 SLR. 규제 최소치·여유분·전체 딜러의 자본 제약으로 대체 해석하지 않음.")]


def select_ici(body, today):
    choices = []
    for link in _HTML(body.decode("utf-8-sig")).links:
        m = re.fullmatch(r"Release: Active and Index Investing, ([A-Za-z]+) (20\d{2})", link["text"])
        if not m:
            continue
        month = datetime.strptime(m[1], "%B").month
        end = month_end(int(m[2]), month, today)
        url = allowed_url(urllib.parse.urljoin(ICI_BASE, link["href"]))
        if not url.endswith(f"{month:02d}{end.year%100:02d}"):
            raise IntermediaryError("ici_report_url_period_mismatch")
        choices.append((end, url))
    if not choices:
        raise IntermediaryError("ici_current_public_report_missing")
    latest = max(d for d, u in choices)
    selected = set(item for item in choices if item[0] == latest)
    if len(selected) != 1:
        raise IntermediaryError("ici_current_documents_ambiguous")
    return selected.pop()


def parse_ici(body, end, today):
    text = body.decode("utf-8-sig")
    # Limit parsing to the assets table, excluding subsequent flow/count tables.
    marker = re.search(r"Total Net Assets Long-Term Mutual Funds and ETFs", text)
    if not marker:
        raise IntermediaryError("ici_asset_table_heading_missing")
    tail = text[marker.start():]
    table = re.search(r"<table\b.*?</table>", tail, re.S | re.I)
    if not table or "Billions of dollars" not in tail[:table.start()]:
        raise IntermediaryError("ici_asset_units_or_table_changed")
    rows = [_expanded(r) for r in _HTML(table[0]).rows]
    header = next((r for r in rows if len(r) == 4 and "Active" in r[1] and "Index" in r[2]), None)
    month = end.strftime("%b %Y")
    if not header or month not in header[1] or month not in header[2] or not re.search(r"Index as a\s*% of Total", header[3]):
        raise IntermediaryError("ici_index_denominator_or_period_changed")
    result = []
    for label, (series, korean) in ICI_SERIES.items():
        found = [r for r in rows if r and r[0] == label]
        if len(found) != 1 or len(found[0]) != 4:
            raise IntermediaryError("ici_asset_category_missing_or_ambiguous")
        active, index, reported = map(numeric, found[0][1:])
        if active+index <= 0 or reported > 100 or abs(100*index/(active+index)-reported) > 0.055:
            raise IntermediaryError("ici_index_share_does_not_reconcile")
        result.append(base(series, korean, reported, end.isoformat(), "monthly", "fund_mandate_composition", unit="percent",
                           components={"active_assets_billion_usd": active, "index_assets_billion_usd": index},
                           source_universe=f"ICI U.S. registered long-term mutual funds and ETFs: {label}; excludes funds primarily investing in other mutual funds",
                           methodology="reported index share; reconcile 100 * index assets / (active assets + index assets)",
                           status="stale" if (today-end).days > 75 else "ok",
                           notes="인덱스 뮤추얼펀드+인덱스 ETF / 동일 분류 전체 펀드. 시장 전체 패시브 소유 비중·M 추정치가 아님."))
    return result


def collect(output_root, transport=None, clock=None):
    root = _validate_output_root(output_root)
    current = now(clock)
    result = {"provider": PROVIDER, "total": TOTAL_SERIES, "success": 0, "observations": [], "errors": [],
              "sources": [], "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS, "research_eligible": False}

    def get(url):
        body, evidence = request(url, root, transport, clock)
        result["sources"].append(evidence)
        return body, evidence

    groups = {"nyfed_pd": [v[0] for v in PD_SERIES.values()], "finra": [r[0] for r in FINRA_SERIES],
              "jpm_slr": ["JPM_REPORTED_SLR"], "ici_index": [v[0] for v in ICI_SERIES.values()]}
    for group, series_ids in groups.items():
        try:
            if group == "nyfed_pd":
                breaks, _ = get(NY_BREAKS)
                seriesbreak = choose_break(json.loads(breaks), current.date())
                definitions, _ = get(NY_DEFS)
                body, evidence = get(f"{NY_BASE}/api/pd/latest/{seriesbreak}.json")
                observations = parse_pd(json.loads(body), json.loads(definitions), seriesbreak, current.date())
            elif group == "finra":
                body, evidence = get(FINRA)
                observations = parse_finra(body, current.date())
            elif group == "jpm_slr":
                feed, _ = get(JPM_FEED)
                end, url = select_jpm(json.loads(feed), current.date())
                body, evidence = get(url)
                observations = parse_jpm(body, end, current.date())
            else:
                listing, _ = get(ICI_INDEX)
                end, url = select_ici(listing, current.date())
                body, evidence = get(url)
                observations = parse_ici(body, end, current.date())
            result["observations"].extend({**o, **evidence} for o in observations)
            result["success"] += len(observations)
        except Exception as exc:
            code = str(exc) if isinstance(exc, IntermediaryError) else "http_"+str(exc.code) if isinstance(exc, urllib.error.HTTPError) else "source_"+type(exc).__name__
            result["errors"].append({"source_group": group, "series_ids": series_ids, "code": code})
    result["status"] = "ok" if result["success"] == TOTAL_SERIES else "partial" if result["success"] else "error"
    result["fetched_at"] = stamp(now(clock))
    return result
