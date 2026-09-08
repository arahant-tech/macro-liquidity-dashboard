"""Issuer-published cash-repurchase statements for a fixed five-company panel.

Assumption: the source cash-flow line is a reported cash outflow for the stated
three-month period. Breaks: cash settlements need not equal market executions.
This official HTML adapter is independent of SEC's separately hosted API.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from .pboc import _HTML, _expanded

INDEX_URL = "https://www.microsoft.com/en-us/Investor/earnings/"
SHORT_URL = "https://aka.ms/latestearnings"
MAX_BYTES = 8_000_000
PANEL = {"MSFT": ("Microsoft", "0000789019"), "AAPL": ("Apple", "0000320193"),
         "GOOGL": ("Alphabet", "0001652044"), "META": ("Meta Platforms", "0001326801"),
         "V": ("Visa", "0001403161")}
APPLE_INDEX = "https://www.apple.com/newsroom/topics/company-news/"
Q4_HOSTS = {"GOOGL": "https://abc.xyz", "V": "https://investor.visa.com",
            "META": "https://investor.atmeta.com"}
Q4_INDEXES = {"GOOGL": "https://abc.xyz/investor/Earnings/default.aspx",
              "V": "https://investor.visa.com/financial-information/quarterly-earnings/default.aspx",
              "META": "https://investor.atmeta.com/financials/default.aspx"}
META_REGISTERED_DOCUMENT = "https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/Meta-06-30-2026-Exhibit-99-1-FINAL.pdf"
USER_AGENT = "MacroLiquidityDashboard/1.0 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
ASSUMPTIONS = [
    "Each issuer's cash-flow statement reports cash payments for its explicit native duration, separately from repurchase authorizations.",
    "The official earnings navigation identifies the latest published earnings release.",
    "The same issuer cash payments appearing in SEC and issuer IR sources are overlapping observations and must not be added.",
]
LIMITATIONS = [
    "The fixed five-company convenience panel is not market-wide buybacks and is not representative.",
    "Cash settlement can differ from market purchase execution timing, including accelerated repurchase settlements.",
    "Quarterly and fiscal-year-to-date observations have different durations, are not summed, and are never repeated across months.",
    "HTML layouts or accounting labels can change; mismatches stop extraction instead of substituting another line.",
    "A source publication date has day precision only; known_by is actual retrieval time and original release timestamp is unverified.",
    "The native issuer cash-flow label is retained; equivalence to an exact SEC taxonomy concept is not certified.",
]


class IssuerBuybackError(RuntimeError):
    pass


def _url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port not in (None, 443) or parsed.fragment):
        raise IssuerBuybackError("source_url_not_allowed")
    feed = (parsed.hostname in {"abc.xyz", "investor.visa.com", "investor.atmeta.com"}
            and parsed.path == "/feed/FinancialReport.svc/GetFinancialReportList")
    if parsed.query and not feed:
        raise IssuerBuybackError("source_url_not_allowed")
    if feed:
        args = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        if set(args) != {"year", "reportSubType", "reportSubTypeList"} or any(len(v) != 1 for v in args.values()):
            raise IssuerBuybackError("feed_query_not_allowed")
        if not re.fullmatch(r"20\d{2}", args["year"][0]) or args["reportSubType"] != ["Quarterly Report"] or args["reportSubTypeList"] != ["Quarterly Report"]:
            raise IssuerBuybackError("feed_query_not_allowed")
    allowed = (feed or (parsed.hostname == "www.microsoft.com" and
                parsed.path.lower().startswith("/en-us/investor/earnings/")) or
               (parsed.hostname == "aka.ms" and parsed.path == "/latestearnings") or
               (parsed.hostname == "www.apple.com" and parsed.path.startswith("/newsroom/")) or
               url in Q4_INDEXES.values() or
               (parsed.hostname == "s206.q4cdn.com" and parsed.path.startswith("/479360582/files/doc_financials/")) or
               (parsed.hostname == "s21.q4cdn.com" and parsed.path.startswith("/399680738/files/doc_financials/")) or
               (parsed.hostname == "s1.q4cdn.com" and parsed.path.startswith("/050606653/files/doc_financials/")))
    if not allowed:
        raise IssuerBuybackError("source_url_not_allowed")
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Document(_HTML):
    def __init__(self, text):
        self.visible, self.ignored = [], []
        super().__init__(text)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored.append(tag)
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.ignored and tag == self.ignored[-1]:
            self.ignored.pop()
        super().handle_endtag(tag)

    def handle_data(self, text):
        if not self.ignored:
            self.visible.append(text)
            super().handle_data(text)


def _now(clock):
    current = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return current.astimezone(timezone.utc)


def _stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _request(url, output_root, transport, clock):
    _url(url)
    if transport:
        response = transport(url)
        if isinstance(response, bytes):
            body, final_url = response, url
        else:
            body, final_url = response
    else:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,application/pdf"})
        with urllib.request.build_opener(_Redirect()).open(request, timeout=20) as response:
            final_url = response.geturl()
            body = response.read(MAX_BYTES + 1)
    _url(final_url)
    captured = _stamp(_now(clock))
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise IssuerBuybackError("invalid_response_size")
    digest = hashlib.sha256(body).hexdigest()
    extension = ".pdf" if body.startswith(b"%PDF-") else ".json" if body.lstrip().startswith(b"{") else ".html"
    relative = Path("raw") / "issuer_buybacks" / (digest + extension)
    destination = output_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(body)
    except FileExistsError:
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise IssuerBuybackError("raw_archive_hash_mismatch") from None
    if extension == ".pdf":
        text = body
    else:
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise IssuerBuybackError("invalid_html_encoding") from None
    return text, {"requested_url": url, "source_url": final_url,
                  "source_sha256": digest, "raw_path": relative.as_posix(),
                  "fetched_at": captured, "known_by": captured,
                  "original_release_at": None}


def discover_release(html, base_url=INDEX_URL):
    links = _Document(html).links
    urls = set()
    for link in links:
        if link["text"].strip().lower() == "press release & webcast":
            urls.add(_url(urllib.parse.urljoin(base_url, link["href"])))
    if len(urls) != 1:
        raise IssuerBuybackError("latest_earnings_link_missing_or_ambiguous")
    return urls.pop()


def _money(text):
    value = text.replace("$", "").replace(",", "").strip()
    match = re.fullmatch(r"\((\d+(?:\.\d+)?)\)", value)
    if match:
        return -float(match.group(1))
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return float(value)
    raise IssuerBuybackError("invalid_cash_payment_cell")


def parse_microsoft(html, source_url, current_date):
    """Take the explicit latest three-month column, never a YTD difference."""
    match = re.fullmatch(r"https://www\.microsoft\.com/en-us/[Ii]nvestor/earnings/FY-(20\d{2})-Q([1-4])/press-release-webcast/?", source_url)
    if not match:
        raise IssuerBuybackError("unrecognized_earnings_release_url")
    fiscal_year, quarter = map(int, match.groups())
    document = _Document(html)
    plain = " ".join(" ".join(document.visible).split())
    expected_title = f"Earnings Release FY{str(fiscal_year)[2:]} Q{quarter}"
    if expected_title not in plain:
        raise IssuerBuybackError("fiscal_period_title_mismatch")
    rows = [_expanded(row) for row in document.rows]
    target_rows = [(index, row) for index, row in enumerate(rows)
                   if row and row[0] == "Common stock repurchased"]
    if len(target_rows) != 1:
        raise IssuerBuybackError("cash_repurchase_row_missing_or_ambiguous")
    position, payments = target_rows[0]
    # The issuer publishes cash flow statements, not a generic authorization table.
    unit_heading = re.search(r"CASH\s+FLOWS?\s+STATEMENTS?\s*\(In millions\)\s*\(Unaudited\)", plain, re.I)
    if not unit_heading:
        raise IssuerBuybackError("cash_flow_units_or_statement_identity_changed")
    section = plain[unit_heading.end():]
    if "Common stock repurchased" not in section or "Common stock issued" not in section:
        raise IssuerBuybackError("cash_flow_statement_identity_missing")
    preceding = [(index, row) for index, row in enumerate(rows[:position])
                 if any(re.fullmatch(r"Three Months Ended [A-Za-z]+ \d{1,2},?", cell)
                        for cell in row)]
    if not preceding:
        raise IssuerBuybackError("explicit_quarter_header_missing")
    header_index, header = preceding[-1]
    if position - header_index > 80:
        raise IssuerBuybackError("cash_row_detached_from_header")
    preceding_labels = [row[0] for row in rows[header_index:position] if row]
    following_labels = [row[0] for row in rows[position + 1:position + 8] if row]
    if ("Common stock issued" not in preceding_labels or
            "Net cash used in financing" not in following_labels):
        raise IssuerBuybackError("cash_flow_neighboring_rows_changed")
    year_rows = [row for row in rows[header_index + 1:header_index + 5]
                 if sum(bool(re.fullmatch(r"20\d{2}", cell)) for cell in row) >= 2]
    if len(year_rows) != 1:
        raise IssuerBuybackError("year_columns_missing_or_ambiguous")
    years = year_rows[0]
    # Pick a column only when its period header AND year label identify this quarter.
    end_month = {1: 9, 2: 12, 3: 3, 4: 6}[quarter]
    end_year = fiscal_year - (1 if quarter in (1, 2) else 0)
    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    start = date(end_year, end_month - 2, 1)
    columns = [index for index, label in enumerate(header)
               if label.startswith("Three Months Ended ") and index < len(years)
               and years[index] == str(end_year)]
    if len(columns) != 1:
        raise IssuerBuybackError("quarter_column_missing_or_ambiguous")
    column = columns[0]
    expected_header = "Three Months Ended " + end.strftime("%B ") + str(end.day) + ","
    if header[column].rstrip(",") != expected_header.rstrip(","):
        raise IssuerBuybackError("quarter_end_header_mismatch")
    if column >= len(payments):
        raise IssuerBuybackError("short_cash_payment_row")
    signed = _money(payments[column])
    if signed > 0:
        raise IssuerBuybackError("repurchase_cash_outflow_sign_changed")
    if end > current_date:
        raise IssuerBuybackError("future_reference_period")
    release = re.search(r"REDMOND, Wash\.\s*[—–-]\s*([A-Za-z]+ \d{1,2}, 20\d{2})\s*[—–-]", plain)
    published = None
    if release:
        try:
            published = datetime.strptime(release.group(1), "%B %d, %Y").date()
        except ValueError:
            raise IssuerBuybackError("invalid_publication_date") from None
        if not end <= published <= current_date:
            raise IssuerBuybackError("publication_date_outside_valid_interval")
    return {
        "id": "IR_BUYBACKS_MSFT", "series_id": "IR_BUYBACKS_MSFT",
        "issuer": "Microsoft", "ticker": "MSFT", "cik": "0000789019",
        "name": "Microsoft reported common-stock repurchase cash payments",
        "source_label": "Common stock repurchased", "value": -signed,
        "raw_signed_value": signed, "units": "million USD", "unit": "million USD",
        "currency": "USD", "period": end.isoformat(),
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "frequency": "quarterly", "fiscal_year": fiscal_year,
        "fiscal_quarter": quarter, "duration_months": 3,
        "layer": 4, "block": "terminal_flows", "track": "equity",
        "measurement_kind": "reported_repurchase_cash_payment",
        "published_date": published.isoformat() if published else None,
        "publication_precision": "date_only" if published else "unknown",
        "research_eligible": False, "coverage": "single_issuer_not_market_aggregate",
        "aggregation_allowed": False,
        "not_additive_with": ["SEC_BUYBACKS_MSFT"],
        "overlap_group": "MSFT_common_stock_cash_repurchase",
    }


def _pdf_pages(body):
    if not isinstance(body, bytes) or not body.startswith(b"%PDF-"):
        raise IssuerBuybackError("expected_official_pdf")
    try:
        from pypdf import PdfReader
    except ImportError:
        raise IssuerBuybackError("pypdf_dependency_missing") from None
    reader = PdfReader(io.BytesIO(body))
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 30:
        raise IssuerBuybackError("unexpected_pdf_structure")
    return [page.extract_text().replace("\xa0", " ") for page in reader.pages]


def _cash_page(pages, issuer, label):
    matches = [(i + 1, page) for i, page in enumerate(pages)
               if re.search(r"STATEMENTS? OF CASH FLOWS", " ".join(page.split()), re.I)
               and re.search(r"\b" + re.escape(label) + r"\s", page)]
    if len(matches) != 1:
        raise IssuerBuybackError("cash_flow_pdf_page_missing_or_ambiguous")
    number, page = matches[0]
    if issuer.lower() not in page[:300].lower() or not re.search(r"\(in millions(?:, unaudited)?\)", page, re.I):
        raise IssuerBuybackError("pdf_issuer_or_units_changed")
    return number, page


def _line_values(page, label, count, dash_zero=False):
    matches = re.findall(r"^" + re.escape(label) + r"[ \t]+([^\n]+)$", page, re.M)
    if len(matches) != 1:
        raise IssuerBuybackError("pdf_cash_line_missing_or_ambiguous")
    tokens = matches[0].replace("$", "").split()
    if len(tokens) != count:
        raise IssuerBuybackError("pdf_numeric_columns_changed")
    return [0.0 if dash_zero and token == "—" else _money(token) for token in tokens]


def _date(text):
    return datetime.strptime(" ".join(text.split()), "%B %d, %Y").date()


def _item(ticker, label, signed, start, end, duration, current_date, published=None):
    if signed > 0 or not start <= end <= current_date:
        raise IssuerBuybackError("invalid_cash_sign_or_reference_period")
    if published and not end <= published <= current_date:
        raise IssuerBuybackError("publication_date_outside_valid_interval")
    name, cik = PANEL[ticker]
    return {"id": "IR_BUYBACKS_" + ticker, "series_id": "IR_BUYBACKS_" + ticker,
            "issuer": name, "ticker": ticker, "cik": cik, "name": name + " reported stock-repurchase cash payments",
            "source_label": label, "value": -signed if signed else 0.0,
            "raw_signed_value": signed, "units": "million USD", "unit": "million USD", "currency": "USD",
            "period": end.isoformat(), "period_start": start.isoformat(), "period_end": end.isoformat(),
            "frequency": "quarterly" if duration == 3 else "annual" if duration == 12 else "fiscal_year_to_date",
            "duration_months": duration, "native_period_basis": "quarter" if duration == 3 else "fiscal_year_to_date",
            "layer": 4, "block": "terminal_flows", "track": "equity",
            "measurement_kind": "reported_repurchase_cash_payment", "research_eligible": False,
            "published_date": published.isoformat() if published else None,
            "publication_precision": "date_only" if published else "unknown",
            "coverage": "fixed_five_issuer_convenience_panel_not_market_aggregate",
            "aggregation_allowed": False, "not_additive_with": ["SEC_BUYBACKS_" + ticker],
            "overlap_group": ticker + "_common_stock_cash_repurchase"}


def parse_alphabet_pdf(pages, current_date):
    label = "Repurchases of stock"
    number, page = _cash_page(pages, "Alphabet", label)
    flat = " ".join(page.split())
    header = re.search(r"Quarter Ended ([A-Za-z]+ \d{1,2}), Year To Date \1, (20\d{2}) (20\d{2}) \2 \3", flat)
    columns = 4
    if not header:
        header = re.search(r"Quarter Ended ([A-Za-z]+ \d{1,2}), (20\d{2}) (20\d{2})", flat)
        columns = 2
    if not header or int(header[3]) != int(header[2]) + 1:
        raise IssuerBuybackError("alphabet_quarter_year_columns_changed")
    end = _date(header[1] + ", " + header[3])
    if end.month not in (3, 6, 9, 12) or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise IssuerBuybackError("alphabet_quarter_end_changed")
    values = _line_values(page, label, columns)
    release = re.search(r"MOUNTAIN VIEW, Calif\.\s*[–—-]\s*([A-Za-z]+ \d{1,2}, 20\d{2})", pages[0])
    published = _date(release[1]) if release else None
    item = _item("GOOGL", label, values[1], date(end.year, end.month - 2, 1), end, 3, current_date, published)
    return {**item, "source_page": number, "cash_flow_columns_verified": [int(header[2]), int(header[3])],
            "selected_native_column": "current_quarter"}


def parse_visa_pdf(pages, current_date):
    label = "Repurchases of class A common stock"
    number, page = _cash_page(pages, "Visa", label)
    header = re.search(r"(Three|Six|Nine|Twelve) Months Ended\s+([A-Za-z]+ \d{1,2}),\s+(20\d{2}) (20\d{2})", page)
    if not header or int(header[3]) != int(header[4]) + 1:
        raise IssuerBuybackError("visa_duration_or_year_columns_changed")
    duration = {"Three": 3, "Six": 6, "Nine": 9, "Twelve": 12}[header[1]]
    end = _date(header[2] + ", " + header[3])
    expected_month = {3: 12, 6: 3, 9: 6, 12: 9}[duration]
    if end.month != expected_month or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise IssuerBuybackError("visa_fiscal_period_mismatch")
    start = date(end.year - (end.month < 10), 10, 1)
    values = _line_values(page, label, 2)
    release = re.search(r"San Francisco, CA,\s*([A-Za-z]+ \d{1,2}, 20\d{2})", pages[0])
    published = _date(release[1]) if release else None
    item = _item("V", label, values[0], start, end, duration, current_date, published)
    return {**item, "source_page": number, "native_period_basis": "fiscal_year_to_date",
            "selected_native_column": "current_fiscal_year_to_date"}


def parse_apple_pdf(pages, current_date, published=None):
    label = "Repurchases of common stock"
    number, page = _cash_page(pages, "Apple", label)
    duration_match = re.search(r"(Three|Six|Nine|Twelve) Months Ended|Years Ended", page)
    if not duration_match:
        raise IssuerBuybackError("apple_native_duration_missing")
    duration = {"Three": 3, "Six": 6, "Nine": 9, "Twelve": 12, None: 12}[duration_match[1]]
    count = 3 if duration_match[0] == "Years Ended" else 2
    header = page[duration_match.end():page.index("Cash", duration_match.end())]
    dates = re.findall(r"([A-Za-z]+\s+\d{1,2},\s+20\d{2})", header)
    if len(dates) != count:
        raise IssuerBuybackError("apple_native_date_columns_changed")
    end = _date(dates[0])
    if any(_date(other) >= end for other in dates[1:]):
        raise IssuerBuybackError("apple_date_column_order_changed")
    balances = [text for text in pages if re.search(r"CONSOLIDATED BALANCE SHEETS", text, re.I)]
    if len(balances) != 1:
        raise IssuerBuybackError("apple_fiscal_start_evidence_missing")
    balance_dates = [_date(value) for value in re.findall(r"([A-Za-z]+\s+\d{1,2},\s+20\d{2})", balances[0][:1400])]
    prior_ends = {value for value in balance_dates if value < end and
                  duration * 28 - 14 <= (end - value).days <= duration * 31 + 14}
    if len(prior_ends) != 1:
        raise IssuerBuybackError("apple_fiscal_start_ambiguous")
    start = prior_ends.pop() + timedelta(days=1)
    # Some official Apple PDFs expose the entire statement as one text line.
    # Exact target label + exact numeric column count + next text row bound it.
    tails = page.split(label)
    if len(tails) != 2:
        raise IssuerBuybackError("apple_cash_row_ambiguous")
    token = r"(?:\([\d,]+\)|-?[\d,]+)"
    match = re.match(r"\s*((?:" + token + r"\s+){" + str(count) + r"})(?=[A-Za-z])", tails[1])
    if not match:
        raise IssuerBuybackError("apple_cash_columns_changed")
    values = [_money(value) for value in match[1].split()]
    item = _item("AAPL", label, values[0], start, end, duration, current_date, published)
    return {**item, "source_page": number, "native_period_basis": "fiscal_year_to_date",
            "period_start_derivation": "day_after_previous_fiscal_year_end_in_same_pdf_balance_sheet",
            "selected_native_column": "current_fiscal_year_to_date"}


def parse_meta_pdf(pages, current_date):
    label = "Repurchases of Class A common stock"
    number, page = _cash_page(pages, "Meta Platforms", label)
    flat = " ".join(page.split())
    header = re.search(r"Three Months Ended ([A-Za-z]+ \d{1,2}), (?:Six|Nine|Twelve) Months Ended \1, (20\d{2}) (20\d{2}) \2 \3", flat)
    columns = 4
    if not header:
        header = re.search(r"Three Months Ended ([A-Za-z]+ \d{1,2}), (20\d{2}) (20\d{2})", flat)
        columns = 2
    if not header or int(header[2]) != int(header[3]) + 1:
        raise IssuerBuybackError("meta_quarter_year_columns_changed")
    end = _date(header[1] + ", " + header[2])
    if end.month not in (3, 6, 9, 12) or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise IssuerBuybackError("meta_quarter_end_changed")
    values = _line_values(page, label, columns, dash_zero=True)
    # Treat an accounting dash as zero only after the statement's financing
    # subtotal reconciles across every printed financing row and all four columns.
    section = page.split("Cash flows from financing activities", 1)
    if len(section) != 2:
        raise IssuerBuybackError("meta_financing_identity_missing")
    parts = section[1].split("Net cash provided by (used in) financing activities", 1)
    if len(parts) != 2:
        raise IssuerBuybackError("meta_financing_identity_missing")
    totals = _line_values(page, "Net cash provided by (used in) financing activities", columns, dash_zero=True)
    sums = [0.0] * columns
    lines = [line.strip() for line in parts[0].splitlines() if line.strip()]
    if not 3 <= len(lines) <= 15:
        raise IssuerBuybackError("meta_financing_component_count_changed")
    for line in lines:
        match = re.match(r"(.+?)\s{2,}(.+)$", line)
        if not match:
            raise IssuerBuybackError("meta_financing_component_layout_changed")
        components = _line_values(line, match[1], columns, dash_zero=True)
        sums = [a + b for a, b in zip(sums, components)]
    if any(abs(a - b) > .01 for a, b in zip(sums, totals)):
        raise IssuerBuybackError("meta_financing_subtotal_failed")
    release = re.search(r"MENLO PARK, Calif\.\s*[–—-]\s*([A-Za-z]+ \d{1,2}, 20\d{2})", pages[0])
    published = _date(release[1]) if release else None
    item = _item("META", label, values[0], date(end.year, end.month - 2, 1), end, 3, current_date, published)
    return {**item, "source_page": number, "selected_native_column": "current_quarter",
            "zero_validation": "all_financing_columns_reconcile_in_million_USD"}


def _q4_feed_url(ticker, year):
    return Q4_HOSTS[ticker] + "/feed/FinancialReport.svc/GetFinancialReportList?" + urllib.parse.urlencode({
        "year": year, "reportSubType": "Quarterly Report", "reportSubTypeList": "Quarterly Report"})


def discover_q4_document(payload, ticker):
    if isinstance(payload, str):
        payload = json.loads(payload)
    items = payload.get("GetFinancialReportListResult")
    if not isinstance(items, list):
        raise IssuerBuybackError("q4_public_feed_schema_changed")
    quarter_names = {"First Quarter": 1, "Second Quarter": 2, "Third Quarter": 3, "Fourth Quarter": 4}
    candidates = []
    for item in items:
        year, quarter = item.get("ReportYear"), quarter_names.get(item.get("ReportSubType"))
        if not isinstance(year, int) or not quarter:
            continue
        for document in item.get("Documents", []):
            title = (document.get("DocumentTitle") or "").lower()
            category = (document.get("DocumentCategory") or "").lower()
            if (document.get("DocumentFileType") or "").upper() != "PDF":
                continue
            accepted = (ticker == "GOOGL" and title == "earnings release" or
                        ticker == "V" and category == "financial report" or
                        ticker == "META" and "earnings release" in title)
            if accepted:
                candidates.append(((year, quarter), _url(document["DocumentPath"])))
    if not candidates:
        raise IssuerBuybackError("latest_issuer_pdf_not_found")
    latest = max(period for period, _ in candidates)
    urls = {url for period, url in candidates if period == latest}
    if len(urls) != 1:
        raise IssuerBuybackError("latest_issuer_pdf_ambiguous")
    return urls.pop()


def _collect_apple(fetch, current):
    index, _ = fetch(APPLE_INDEX)
    candidates = []
    for link in _Document(index).links:
        url = urllib.parse.urljoin(APPLE_INDEX, link["href"])
        match = re.fullmatch(r"https://www\.apple\.com/newsroom/(20\d{2})/(\d{2})/apple-reports-(?:first|second|third|fourth)-quarter-results/?", url)
        if match:
            candidates.append(((int(match[1]), int(match[2])), _url(url)))
    if not candidates:
        raise IssuerBuybackError("apple_latest_release_not_found")
    latest = max(period for period, _ in candidates)
    urls = {url for period, url in candidates if period == latest}
    if len(urls) != 1:
        raise IssuerBuybackError("apple_latest_release_ambiguous")
    release, release_source = fetch(urls.pop())
    documents = {_url(urllib.parse.urljoin(release_source["source_url"], link["href"]))
                 for link in _Document(release).links if ("consolidated financial statements" in link["text"].lower()
                 or "consolidated_financial_statements" in link["href"].lower())
                 and link["href"].lower().endswith(".pdf")}
    if len(documents) != 1:
        raise IssuerBuybackError("apple_statements_pdf_not_found_or_ambiguous")
    body, source = fetch(documents.pop())
    published_match = re.search(r'"datePublished"\s*:\s*"(20\d{2}-\d{2}-\d{2})(?:Z)?"', release)
    published = date.fromisoformat(published_match[1]) if published_match else None
    return {**parse_apple_pdf(_pdf_pages(body), current.date(), published), **source,
            "discovery_status": "automatic", "discovery_mode": "official_newsroom_latest_release"}


def collect(output_root, transport=None, clock=None, tickers=None):
    """Return each issuer's native-duration observation, never market aggregates.

    Injectable transport returns bytes or (bytes, final_redirect_url).
    Actual captures are archived even if validation subsequently rejects them.
    """
    output_root = Path(output_root).resolve()
    project = Path(__file__).resolve().parents[2]
    if output_root == project or any(output_root == project / name or
                                    project / name in output_root.parents
                                    for name in ("research", "backtest", "model")):
        raise ValueError("output_root_must_be_isolated_from_research")
    current = _now(clock)
    tickers = list(PANEL) if tickers is None else list(tickers)
    if len(tickers) != len(set(tickers)) or any(ticker not in PANEL for ticker in tickers):
        raise ValueError("ticker_not_in_fixed_panel")
    result = {"provider": "issuer_buybacks", "status": "error",
              "fetched_at": _stamp(current), "observations": [], "errors": [],
              "assumptions": list(ASSUMPTIONS), "limitations": list(LIMITATIONS),
              "sources": [], "total_series": len(tickers), "discovery_warnings": []}

    def fetch(url):
        content, source = _request(url, output_root, transport, clock)
        result["sources"].append(source)
        return content, source

    def error_code(error):
        if isinstance(error, IssuerBuybackError):
            return str(error)
        elif isinstance(error, urllib.error.HTTPError):
            return "http_" + str(error.code)
        return "transport_or_parse_" + type(error).__name__

    for ticker in tickers:
        try:
            if ticker == "MSFT":
                index, index_source = fetch(INDEX_URL)
                html, source = fetch(discover_release(index, index_source["source_url"]))
                item = {**parse_microsoft(html, source["source_url"], current.date()), **source,
                        "discovery_status": "automatic", "discovery_mode": "official_latest_earnings_link"}
            elif ticker == "AAPL":
                item = _collect_apple(fetch, current)
            else:
                discovery_status, discovery_mode = "automatic", "official_public_financial_report_feed"
                try:
                    feed, _ = fetch(_q4_feed_url(ticker, current.year))
                    try:
                        url = discover_q4_document(feed, ticker)
                    except IssuerBuybackError as error:
                        if str(error) != "latest_issuer_pdf_not_found":
                            raise
                        feed, _ = fetch(_q4_feed_url(ticker, current.year - 1))
                        url = discover_q4_document(feed, ticker)
                except Exception as error:
                    if ticker != "META":
                        raise
                    # An independently hosted document explicitly linked by the
                    # issuer's normal public IR UI was verified on 2026-09-09.
                    # This does not claim to fix blocked future-release discovery.
                    url = META_REGISTERED_DOCUMENT
                    discovery_status, discovery_mode = "blocked", "registered_official_document"
                    result["discovery_warnings"].append({"ticker": ticker, "code": error_code(error),
                        "message": "Latest listing unavailable; rechecking registered issuer document. Future release discovery is unverified."})
                body, source = fetch(url)
                parser = {"GOOGL": parse_alphabet_pdf, "V": parse_visa_pdf, "META": parse_meta_pdf}[ticker]
                item = {**parser(_pdf_pages(body), current.date()), **source,
                        "discovery_status": discovery_status, "discovery_mode": discovery_mode}
            result["observations"].append(item)
        except Exception as error:
            result["errors"].append({"stage": ticker, "ticker": ticker, "code": error_code(error)})
    result["success_count"] = len(result["observations"])
    result["status"] = ("ok" if result["success_count"] == len(tickers) else
                        "partial" if result["observations"] else "error")
    result["fetched_at"] = _stamp(_now(clock))
    return result
