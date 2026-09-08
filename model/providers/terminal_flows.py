"""Layer-four observed equity flows, with independent scopes and native periods.

No AUM changes, stock valuations, total capital financing or repurchase inversions
are used as flows. ICI's public weekly estimates can be blocked by its publisher;
that failure remains visible, never replaced by a search-engine transcription.
"""
from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from .pboc import _HTML
from model.live_api import _validate_output_root

PROVIDER = "terminal_flows"
TIC_URL = "https://ticdata.treasury.gov/Publish/slt_table1.txt"
ICI_MF_URL = "https://www.ici.org/research/stats/flows"
ICI_ETF_URL = "https://www.ici.org/research/stats/etf_flows"
Z1_SERIES = "BOGZ1FU103164103Q"
Z1_URL = "https://fred.stlouisfed.org/series/" + Z1_SERIES
Z1_TITLE = "Nonfinancial Corporate Business; Corporate Equities; Liability, Transactions"
SERIES_IDS = ("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", "Z1_NFC_EQUITY_NET_ISSUANCE",
              "ICI_MF_DOMESTIC_EQUITY_FLOW", "ICI_MF_WORLD_EQUITY_FLOW",
              "ICI_ETF_DOMESTIC_EQUITY_NET_ISSUANCE", "ICI_ETF_WORLD_EQUITY_NET_ISSUANCE")
Z1_SPECS = {
    "Z1": {"id": Z1_SERIES, "title": Z1_TITLE, "output_id": "Z1_NFC_EQUITY_NET_ISSUANCE",
           "label": "Fed Z.1 · 비금융기업 주식 순발행", "method": "reported_z1_equity_liability_transactions_nsa",
           "universe": "US nonfinancial corporations; public and closely held corporate equity",
           "not_additive_with": ["issuer_buybacks", "buybacks"]},
    "Z1_ETF": {"id": "BOGZ1FU563064100Q", "title": "Exchange-Traded Funds; Corporate Equities; Asset, Transactions",
               "output_id": "Z1_ETF_EQUITY_TRANSACTIONS", "label": "Fed Z.1 · ETF 주식자산 거래",
               "method": "fed_ici_based_equity_etf_issuance_adjusted_quarterly_transactions",
               "universe": "ETF corporate equities; ICI equity issuance adjusted for commodities, hybrid allocation and money-market holdings; not US-stock-only",
               "not_additive_with": ["ICI_ETF_DOMESTIC_EQUITY_NET_ISSUANCE", "ICI_ETF_WORLD_EQUITY_NET_ISSUANCE"]},
    "Z1_MF": {"id": "BOGZ1FU653064100Q", "title": "Mutual Funds; Corporate Equities; Asset, Transactions",
              "output_id": "Z1_MF_EQUITY_TRANSACTIONS", "label": "Fed Z.1 · 뮤추얼펀드 주식 순매수",
              "method": "fed_ici_common_and_preferred_stock_net_portfolio_purchases_quarterly",
              "universe": "Long-term mutual funds including variable annuity funds; common and preferred stock net portfolio purchases; not subscription flows or US-stock-only",
              "not_additive_with": ["ICI_MF_DOMESTIC_EQUITY_FLOW", "ICI_MF_WORLD_EQUITY_FLOW"]},
}
Z1_IDS = {item["id"] for item in Z1_SPECS.values()}
SERIES_IDS += ("Z1_ETF_EQUITY_TRANSACTIONS", "Z1_MF_EQUITY_TRANSACTIONS")
USER_AGENT = "MacroLiquidityDashboard/1.2 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
ASSUMPTIONS = [
    "TIC net US sales of US corporate equity to foreign residents equal foreign net purchases under the source's sign convention.",
    "Z.1 corporate-equity liability transactions are the Fed's net issuance estimate, including public and closely held nonfinancial corporations; they are not standalone buyback observations.",
    "ICI domestic and world equity categories are separate investment mandates; mutual funds and ETF issuance are collected separately.",
    "Additional Z.1 fund series are quarterly asset transactions, not a replacement for ICI weekly subscription-flow observations.",
    "Actual capture time is certified knowability. Current-vintage revisions cannot establish when a historical value was originally available.",
]
LIMITATIONS = [
    "TIC uses residence, not beneficial-owner nationality; its February 2023 form change is a structural series break. Only the Grand Total equity transaction column is used.",
    "Z.1 excludes financial corporations and includes closely held equities. Cash mergers, repurchases and issuance enter its net measure, so adding independent buybacks would double count retirements.",
    "ICI weekly flows are estimates, not monthly actuals or issuer-certified settlement cash. ETF net issuance may settle in kind; it is not contemporaneous exchange buying.",
    "US-domiciled world-equity funds can invest outside the US; their flows are not US-stock demand. Fund flows, TIC and issuance also overlap in ultimate transactions, so this collector does not sum them.",
    "Z.1 ETF transactions use ICI monthly net issuance with Fed commodity, hybrid and money-market adjustments; mutual-fund transactions are stock portfolio purchases, not net subscriptions. Both include foreign equities.",
    "ICI public HTML may deny automated access with HTTP403; no authenticated, member-only, inferred-AUM or search-result fallback is used.",
    "Weekly, monthly and quarterly data retain their own intervals. No monthly repetition, annualization conversion, latency backdating or performance claim is made.",
]


class TerminalError(RuntimeError):
    pass


def utc(clock):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def plain(text):
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", text)).split())


def number(text):
    text = str(text).strip().replace("−", "-")
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise TerminalError("missing_or_invalid_flow_value")
    return Decimal(text.replace(",", ""))


def allow_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.fragment or p.port not in (None, 443):
        raise TerminalError("unapproved_source_url")
    if url in (TIC_URL, ICI_MF_URL, ICI_ETF_URL) or url in {"https://fred.stlouisfed.org/series/"+sid for sid in Z1_IDS}:
        return url
    query = urllib.parse.parse_qs(p.query)
    if (p.netloc == "fred.stlouisfed.org" and p.path == "/graph/fredgraph.csv"
            and set(query) == {"id", "cosd"} and len(query["id"]) == 1 and query["id"][0] in Z1_IDS
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["cosd"][0])):
        return url
    if (p.netloc == "api.stlouisfed.org" and p.path in ("/fred/series", "/fred/series/observations")
            and len(query.get("series_id", [])) == 1 and query["series_id"][0] in Z1_IDS
            and set(query) <= {"series_id", "api_key", "file_type", "sort_order", "limit"}):
        return url
    raise TerminalError("unapproved_source_url")


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allow_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url, root, transport, clock, key=""):
    allow_url(url)
    if transport:
        body = transport(url)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,text/plain,text/html"})
        with urllib.request.build_opener(Redirect()).open(req, timeout=20) as response:
            allow_url(response.geturl())
            body = response.read(4_000_001)
    if not isinstance(body, bytes) or not body or len(body) > 4_000_000:
        raise TerminalError("invalid_source_size")
    if key and key.encode() in body:
        raise TerminalError("credential_echo_rejected")
    digest = hashlib.sha256(body).hexdigest()
    extension = ".json" if "/fred/series" in url else ".txt" if url == TIC_URL else ".csv" if "/graph/" in url else ".html"
    relative = Path("raw/terminal_flows") / (digest + extension)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body:
            raise TerminalError("immutable_source_conflict")
    else:
        with target.open("xb") as handle:
            handle.write(body)
    captured = stamp(utc(clock))
    # The public metadata URL never contains an API key, even on API errors.
    public_url = ("https://fred.stlouisfed.org/series/" + urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["series_id"][0]) if "api.stlouisfed.org" in url else url
    return body.decode("utf-8-sig"), {"source_url": public_url, "raw_path": str(relative),
        "raw_sha256": digest, "known_by": captured, "retrieved_at": captured,
        "original_release_at": None, "release_timestamp_verified": False}


def base(series_id, label, value, start, end, frequency, **extra):
    return {"series_id": series_id, "label": label, "provider": PROVIDER, "layer": 4,
            "track": "equity", "role": "flow", "block": "terminal_equity_flows",
            "value": float(value), "unit": "million USD", "units": "million USD",
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "observation_date": end.isoformat(), "frequency": frequency,
            "research_eligible": False, "aggregation_allowed": False, **extra}


def parse_tic(text, today):
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    if not rows or rows[0][0] != "Table 1: U.S. Long-Term Securities Held by Foreign Residents":
        raise TerminalError("tic_table_identity_changed")
    if not any(r and r[0] == "Millions of dollars" for r in rows[:8]):
        raise TerminalError("tic_unit_unverified")
    if not any(r and r[0] == "A positive number for net U.S. sales to foreigners denotes an increase in a foreign position" for r in rows[:8]):
        raise TerminalError("tic_sign_convention_unverified")
    indexes = [i for i, r in enumerate(rows) if r and r[0] == "country"]
    if len(indexes) != 1:
        raise TerminalError("tic_unique_machine_header_missing")
    i = indexes[0]
    header = rows[i]
    needed = {"country", "country_code", "date", "for_lt_eqty_net"}
    if not needed <= set(header) or len(header) != len(set(header)):
        raise TerminalError("tic_equity_flow_column_missing")
    flow_index = header.index("for_lt_eqty_net")
    if i < 2 or rows[i-2][flow_index] != "U.S. Corp. Equity" or rows[i-1][flow_index] != "Net U.S. Sales":
        raise TerminalError("tic_human_machine_header_mismatch")
    candidates = []
    for row in rows[i+1:]:
        code_index = header.index("country_code")
        if len(row) <= code_index or row[code_index] != "99996":
            continue
        if len(row) != len(header):
            raise TerminalError("tic_world_total_column_count_changed")
        item = dict(zip(header, row))
        if item["country"] != "Grand Total":
            raise TerminalError("tic_world_total_identity_changed")
        if not re.fullmatch(r"20\d{2}-\d{2}", item["date"]):
            raise TerminalError("tic_invalid_reference_month")
        year, month = map(int, item["date"].split("-"))
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end > today:
            raise TerminalError("future_reference_period")
        if item["date"] >= "2023-02":
            candidates.append((end, item["for_lt_eqty_net"]))
    if not candidates:
        raise TerminalError("tic_current_flow_missing")
    latest = max(x[0] for x in candidates)
    selected = [number(x[1]) for x in candidates if x[0] == latest]
    if len(selected) != 1:
        raise TerminalError("tic_duplicate_latest_total")
    return base(SERIES_IDS[0], "TIC · 외국인의 미국 주식 순매수", selected[0], latest.replace(day=1), latest, "monthly",
                method="reported_tic_slt_net_us_equity_sales", source_publisher="U.S. Department of the Treasury",
                source_universe="All foreign residents; US corporate equity; Grand Total code 99996",
                source_series_id="for_lt_eqty_net", sign_convention="positive_foreign_net_purchase",
                structural_break="2023-02", status="stale" if (today-latest).days > 100 else "ok")


def parse_ici(text, kind, today):
    if kind not in ("MF", "ETF"):
        raise TerminalError("unknown_ici_kind")
    content = plain(text)
    identity = "Estimated Long-Term Mutual Fund Flows" if kind == "MF" else "Estimated ETF Net Issuance"
    if identity not in content or "Millions of dollars" not in content:
        raise TerminalError("ici_identity_or_units_unverified")
    # Use the release sentence, not navigation dates or unrelated page stories.
    match = re.search(r"Washington, DC;\s*([A-Za-z]+ \d{1,2}, 20\d{2})[—–-]", content)
    if not match:
        raise TerminalError("ici_release_date_missing")
    release = datetime.strptime(match[1], "%B %d, %Y").date()
    if release > today:
        raise TerminalError("future_publication_date")
    tables = [[cell["text"] for cell in row] for row in _HTML(text).rows]
    header_indexes = [i for i,r in enumerate(tables) if len(r)>1 and r[0] == "" and re.fullmatch(r"\d{1,2}/\d{1,2}/20\d{2}", r[1])]
    if len(header_indexes) != 1:
        raise TerminalError("ici_unique_weekly_header_missing")
    i = header_indexes[0]
    dates = [datetime.strptime(x, "%m/%d/%Y").date() for x in tables[i][1:]]
    if dates != sorted(set(dates), reverse=True) or dates[0] > release or any(x.weekday()!=2 for x in dates):
        raise TerminalError("ici_invalid_week_ending_dates")
    for newer, older in zip(dates, dates[1:]):
        if (newer-older).days != 7:
            raise TerminalError("ici_week_interval_changed")
    expected = "Total equity" if kind == "MF" else "Equity"
    block = tables[i+1:i+4]
    if [r[0] for r in block] != [expected, "Domestic", "World"] or any(len(r)!=len(dates)+1 for r in block):
        raise TerminalError("ici_equity_category_layout_changed")
    values = [number(r[1]) for r in block]
    if abs(values[0]-values[1]-values[2]) > Decimal("2"):
        raise TerminalError("ici_equity_total_does_not_reconcile")
    end = dates[0]
    observations = []
    for name, value in zip(("DOMESTIC", "WORLD"), values[1:]):
        series_id = "ICI_" + kind + "_" + name + "_EQUITY_" + ("FLOW" if kind == "MF" else "NET_ISSUANCE")
        label = "ICI · " + ("미국 주식형" if name == "DOMESTIC" else "해외 주식형") + (" 펀드 순유입 추정" if kind == "MF" else " ETF 순발행 추정")
        observations.append(base(series_id, label, value, end-timedelta(days=6), end, "weekly",
            source_publisher="Investment Company Institute", method="publisher_estimated_fund_flow" if kind=="MF" else "publisher_estimated_etf_net_issuance",
            source_universe="US mutual funds excluding ETFs and fund-of-funds" if kind=="MF" else "US ETFs excluding fund-of-ETFs; includes non-1940-Act funds",
            geographic_scope="domestic_equity_mandate" if name=="DOMESTIC" else "world_equity_mandate_not_us_only",
            original_release_date=release.isoformat(), reported_release_lag_days=(release-end).days,
            source_quality="industry_publisher_weekly_estimate", cash_settlement_verified=False,
            components={"published_total_equity":float(values[0]), "domestic":float(values[1]), "world":float(values[2])},
            status="stale" if (today-end).days>21 else "ok"))
    return observations


def parse_z1(metadata, observations, today, api=False, spec=None):
    spec = spec or Z1_SPECS["Z1"]
    series_id, expected_title = spec["id"], spec["title"]
    if api:
        rows = json.loads(metadata).get("seriess", [])
        if len(rows)!=1:
            raise TerminalError("z1_unique_metadata_missing")
        item = rows[0]
        if (item.get("id")!=series_id or item.get("title")!=expected_title
                or item.get("units") not in ("Millions of U.S. Dollars", "Millions of Dollars")
                or item.get("seasonal_adjustment_short")!="NSA" or item.get("frequency_short")!="Q"):
            raise TerminalError("z1_transactions_metadata_changed")
        data = json.loads(observations).get("observations", [])
        pairs = [(row.get("date"), row.get("value")) for row in data]
    else:
        title = re.search(r"<title>(.*?)</title>", metadata, re.S)
        content = plain(metadata)
        if (not title or plain(title[1])!=expected_title+" ("+series_id+") | FRED | St. Louis Fed"
                or "Millions of U.S. Dollars, Not Seasonally Adjusted" not in content
                or "Quarterly, End of Period" not in content):
            raise TerminalError("z1_transactions_metadata_changed")
        reader = csv.DictReader(io.StringIO(observations))
        if reader.fieldnames != ["observation_date", series_id]:
            raise TerminalError("z1_csv_series_mismatch")
        pairs = [(r["observation_date"], r[series_id]) for r in reader]
    if not pairs:
        raise TerminalError("z1_observation_missing")
    parsed = []
    for date_text, value in pairs:
        start = date.fromisoformat(date_text)
        if start.day!=1 or start.month not in (1,4,7,10):
            raise TerminalError("z1_quarter_date_changed")
        month = start.month+2
        end = date(start.year, month, calendar.monthrange(start.year,month)[1])
        if end>today:
            raise TerminalError("future_reference_period")
        parsed.append((end, start, value))
    latest = max(x[0] for x in parsed)
    selected = [x for x in parsed if x[0]==latest]
    if len(selected)!=1:
        raise TerminalError("z1_duplicate_latest_observation")
    end, start, value = selected[0]
    return base(spec["output_id"], spec["label"], number(value), start, end, "quarterly",
                source_publisher="Federal Reserve Board via FRED", source_series_id=series_id,
                source_universe=spec["universe"], method=spec["method"], seasonal_adjustment="NSA",
                native_period_basis="quarterly_transaction_amount_not_annual_rate", duration_months=3,
                not_additive_with=spec["not_additive_with"],
                status="stale" if (today-end).days>190 else "ok")


def collect(output_root, transport=None, clock=None, api_key=None):
    root = _validate_output_root(output_root)
    today = utc(clock).date()
    key = api_key if api_key is not None else os.environ.get("FRED_API_KEY", "")
    if key and not re.fullmatch(r"[A-Za-z0-9]{32}", key):
        key = ""
    result = {"provider":PROVIDER, "total":len(SERIES_IDS), "success":0, "observations":[], "errors":[],
              "sources":[], "assumptions":ASSUMPTIONS, "limitations":LIMITATIONS, "research_eligible":False}
    for group, url in (("TIC",TIC_URL), *((name,"https://fred.stlouisfed.org/series/"+spec["id"]) for name,spec in Z1_SPECS.items()), ("MF",ICI_MF_URL), ("ETF",ICI_ETF_URL)):
        try:
            if group in Z1_SPECS:
                spec = Z1_SPECS[group]
                if key:
                    params = {"series_id":spec["id"],"api_key":key,"file_type":"json"}
                    data_url = "https://api.stlouisfed.org/fred/series/observations?"+urllib.parse.urlencode({**params,"sort_order":"desc","limit":4})
                    meta_url = "https://api.stlouisfed.org/fred/series?"+urllib.parse.urlencode(params)
                else:
                    meta_url = url
                    data_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?"+urllib.parse.urlencode({"id":spec["id"],"cosd":str(today.replace(year=today.year-1, day=1))})
                meta, evidence = request(meta_url,root,transport,clock,key)
                result["sources"].append(evidence)
                data, source = request(data_url,root,transport,clock,key)
                result["sources"].append(source)
                source["source_url"] = url
                source["metadata_raw_path"] = evidence["raw_path"]
                source["metadata_raw_sha256"] = evidence["raw_sha256"]
                items = [parse_z1(meta,data,today,api=bool(key),spec=spec)]
            else:
                data, source = request(url,root,transport,clock)
                result["sources"].append(source)
                items = [parse_tic(data,today)] if group=="TIC" else parse_ici(data,group,today)
            result["observations"].extend({**item,**source} for item in items)
            result["success"] += len(items)
        except Exception as exc:
            code = str(exc) if isinstance(exc, TerminalError) else "http_"+str(exc.code) if isinstance(exc,urllib.error.HTTPError) else "source_"+type(exc).__name__
            result["errors"].append({"series_id":group,"code":code})
    result["status"] = "ok" if result["success"]==result["total"] else "partial" if result["success"] else "error"
    result["fetched_at"] = stamp(utc(clock))
    return result
