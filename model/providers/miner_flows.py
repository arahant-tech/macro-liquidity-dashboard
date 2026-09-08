"""Explicit reported BTC sales, never production or balance changes as sales.

Two issuer disclosures are a partial observation of miner selling, not a
market-wide real-time on-chain signal. Preserve each issuer's own duration.
"""
from __future__ import annotations
import calendar
from datetime import date, datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import urllib.error

from .pboc import _HTML, _expanded
from model.live_api import _validate_output_root

PROVIDER = "miner_flows"
CLSK_ROOT = "https://investors.cleanspark.com"
CLSK_FEED = CLSK_ROOT + "/feed/PressRelease.svc/GetPressReleaseList?LanguageId=1&pressReleaseDateFilter=3&categoryId=00000000-0000-0000-0000-000000000000&bodyType=0&pageSize=25&pageNumber=0&includeTags=true&year=-1"
MARA_ROOT = "https://ir.mara.com"
MARA_INDEX = MARA_ROOT + "/"
USER_AGENT = "MacroLiquidityDashboard/1.1 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
ASSUMPTIONS = [
    "Issuer disclosures explicitly identify bitcoin sales, including settled call exercises where stated.",
    "Native monthly or year-to-date durations are retained; no monthly repetition or cross-period aggregate is produced.",
    "The fixed observation panel is CleanSpark and MARA, not a representative sample of the whole mining industry.",
]
LIMITATIONS = [
    "Reported realized sales are a delayed partial observation of selling activity, not contemporaneous pressure or exchange inflow.",
    "Coins sold may come from prior treasury holdings or purchases, not just this period's mining production.",
    "The company reports may be unaudited and revised. Cash settlement, derivatives delivery and execution times differ.",
    "Two issuers and differing reporting periods cannot be added into a market-wide monthly flow.",
    "Source HTML/accounting labels may change; unsupported or missing sales fields fail closed.",
]


class MinerError(RuntimeError):
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
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise MinerError("unapproved_source_url")
    valid = ((p.netloc == "investors.cleanspark.com" and (url == CLSK_FEED or p.path.startswith("/news/news-details/"))) or
             (p.netloc == "ir.mara.com" and not p.query and (p.path == "/" or p.path.startswith("/sec-filings/all-sec-filings/content/"))))
    if not valid:
        raise MinerError("unapproved_source_url")
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
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})
        with urllib.request.build_opener(Redirect()).open(req, timeout=20) as response:
            allowed_url(response.geturl())
            body = response.read(8_000_001)
    if not isinstance(body, bytes) or not body or len(body) > 8_000_000:
        raise MinerError("invalid_source_size")
    captured = stamp(now(clock))
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw/miner_flows") / (digest + (".json" if url == CLSK_FEED else ".html"))
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body:
            raise MinerError("immutable_source_conflict")
    else:
        with target.open("xb") as handle:
            handle.write(body)
    return body.decode("utf-8-sig"), {"source_url": url, "raw_path": str(relative), "raw_sha256": digest,
             "known_by": captured, "retrieved_at": captured, "original_release_at": None,
             "release_timestamp_verified": False, "raw_scope": "original_public_issuer_document"}


def plain_text(text):
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def period(year, month, months, today):
    end = date(year, month, calendar.monthrange(year, month)[1])
    serial = year * 12 + month - months
    start = date(serial // 12, serial % 12 + 1, 1)
    if end > today:
        raise MinerError("future_reference_period")
    return start.isoformat(), end.isoformat()


def number(text):
    if not re.fullmatch(r"\(?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\)?", text):
        raise MinerError("missing_or_ambiguous_sales_number")
    if text.startswith("(") != text.endswith(")"):
        raise MinerError("unbalanced_sales_number")
    value = float(text.strip("()").replace(",", ""))
    if not 0 <= value < 21_000_000:
        raise MinerError("implausible_btc_quantity")
    return value


def base(ticker, start, end, value, **extra):
    return {"series_id": "MINER_" + ticker + "_BTC_SOLD", "ticker": ticker, "value": value,
            "unit": "BTC over disclosed period", "units": "BTC over disclosed period", "layer": 5,
            "provider": PROVIDER, "track": "crypto", "role": "flow", "block": "reported_miner_sales",
            "period_start": start, "period_end": end, "observation_date": end,
            "research_eligible": False, "market_aggregate": False,
            "measurement_kind": "issuer_reported_realized_btc_sales", **extra}


def select_clsk(feed, today):
    rows = json.loads(feed).get("GetPressReleaseListResult")
    if not isinstance(rows, list):
        raise MinerError("clsk_feed_schema_changed")
    candidates = []
    for item in rows:
        m = re.fullmatch(r"CleanSpark Releases ([A-Za-z]+) (20\d{2}) (?:Operational|Bitcoin Mining) Update", item.get("Headline", ""))
        if not m:
            continue
        month = datetime.strptime(m[1], "%B").month
        start, end = period(int(m[2]), month, 1, today)
        published = datetime.strptime(item["PressReleaseDate"].split()[0], "%m/%d/%Y").date()
        if not date.fromisoformat(end) <= published <= today:
            raise MinerError("invalid_clsk_publication_date")
        url = allowed_url(urllib.parse.urljoin(CLSK_ROOT, item["LinkToDetailPage"]))
        candidates.append((end, url, start, published.isoformat(), item["Headline"]))
    if not candidates:
        raise MinerError("clsk_current_operational_report_missing")
    # Amendments for the same month are ordered by actual publication date,
    # never the spelling of their URLs. Day-level ties remain ambiguous.
    latest_key = max((item[0], item[3]) for item in candidates)
    latest = set(item for item in candidates if (item[0], item[3]) == latest_key)
    if len(latest) != 1:
        raise MinerError("clsk_same_date_reports_ambiguous")
    return latest.pop()


def parse_clsk(text, selected):
    end, url, start, published, title = selected
    plain = plain_text(text)
    if title not in plain:
        raise MinerError("clsk_title_mismatch")
    if f"month ended {date.fromisoformat(end).strftime('%B')} {date.fromisoformat(end).day}, {end[:4]}" not in plain:
        raise MinerError("clsk_period_not_confirmed_in_document")
    text = re.sub(r"<sup\b[^>]*>.*?</sup>", "", text, flags=re.S | re.I)
    rows = [_expanded(row) for row in _HTML(text).rows]
    components = {}
    allowed = {"Bitcoin sold at spot", "Bitcoin sold pursuant to call exercises", "Bitcoin sold related to delta neutral basis trade"}
    for row in rows:
        if not row or not row[0].startswith("Bitcoin sold"):
            continue
        if row[0] not in allowed or row[0] in components or len(row) != 2:
            raise MinerError("clsk_sales_categories_changed_or_duplicated")
        components[row[0]] = number(row[1])
    if not components or "Bitcoin sold at spot" not in components:
        raise MinerError("clsk_explicit_sales_missing")
    return base("CLSK", start, end, sum(components.values()), issuer="CleanSpark",
                frequency="monthly", published_date=published, components=components,
                source_label="Explicit Bitcoin sold rows in the treasury activity table",
                includes_derivative_delivery=any("call exercises" in c for c in components),
                aggregation_rule="sum_only_explicit_sales_categories_for_one_issuer_and_one_month")


def select_mara(index, today):
    candidates = []
    for link in _HTML(index).links:
        url = urllib.parse.urljoin(MARA_ROOT, link["href"])
        m = re.search(r"/mara-(20\d{2})(\d{2})(\d{2})\.htm$", url)
        if not m or link["text"] != "HTML":
            continue
        end = date(*map(int, m.groups()))
        if end > today:
            raise MinerError("future_mara_filing")
        candidates.append((end.isoformat(), allowed_url(url)))
    if not candidates:
        raise MinerError("mara_current_html_filing_missing")
    return max(candidates)


def parse_mara(text, selected, today):
    end, url = selected
    plain = plain_text(text)
    names = {"three": 3, "six": 6, "nine": 9, "twelve": 12}
    pattern = (r"(?:During|For) the (three|six|nine|twelve) months ended ([A-Za-z]+ \d{1,2}, 20\d{2}), we sold (?:approximately )?([\d,]+(?:\.\d+)?) bitcoin"
               r"(?=\s+(?:as part\b|for\b|during\b|to\b|and\b|at\b)|[.,;]|$)")
    candidates = {}
    for m in re.finditer(pattern, plain, re.I):
        reported_end = datetime.strptime(m[2], "%B %d, %Y").date()
        if reported_end.isoformat() != end:
            continue
        months = names[m[1].lower()]
        start, check_end = period(reported_end.year, reported_end.month, months, today)
        if check_end != end:
            raise MinerError("mara_non_month_end_period")
        value = number(m[3])
        if months in candidates and candidates[months][1] != value:
            raise MinerError("mara_conflicting_same_period_sales")
        candidates[months] = (start, value)
    if not candidates:
        raise MinerError("mara_explicit_current_sales_missing")
    months = min(candidates)  # Direct quarter if disclosed; otherwise direct YTD, never subtraction.
    start, value = candidates[months]
    return base("MARA", start, end, value, issuer="MARA", frequency="disclosed_duration",
                duration_months=months, published_date=None,
                source_label="Explicit current-period bitcoin sold statement in issuer-hosted filing",
                aggregation_rule="direct_disclosed_duration_no_differencing_or_monthly_repetition")


def collect(output_root, transport=None, clock=None):
    root = _validate_output_root(output_root)
    current = now(clock)
    result = {"provider": PROVIDER, "total": 2, "success": 0, "observations": [], "errors": [],
              "sources": [], "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS, "research_eligible": False}
    for ticker in ("CLSK", "MARA"):
        try:
            index, evidence = request(CLSK_FEED if ticker == "CLSK" else MARA_INDEX, root, transport, clock)
            result["sources"].append(evidence)
            selected = (select_clsk if ticker == "CLSK" else select_mara)(index, current.date())
            document, source = request(selected[1], root, transport, clock)
            result["sources"].append(source)
            item = parse_clsk(document, selected) if ticker == "CLSK" else parse_mara(document, selected, current.date())
            result["observations"].append({**item, **source})
            result["success"] += 1
        except Exception as exc:
            code = str(exc) if isinstance(exc, MinerError) else "http_" + str(exc.code) if isinstance(exc, urllib.error.HTTPError) else "source_" + type(exc).__name__
            result["errors"].append({"ticker": ticker, "code": code})
    result["status"] = "ok" if result["success"] == 2 else "partial" if result["success"] else "error"
    result["fetched_at"] = stamp(now(clock))
    return result
