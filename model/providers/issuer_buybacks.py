"""Microsoft's own published quarterly cash-repurchase statement.

Assumption: the source cash-flow line is a reported cash outflow for the stated
three-month period. Breaks: cash settlements need not equal market executions.
This official HTML adapter is independent of SEC's separately hosted API.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from .pboc import _HTML, _expanded

INDEX_URL = "https://www.microsoft.com/en-us/Investor/earnings/"
SHORT_URL = "https://aka.ms/latestearnings"
MAX_BYTES = 3_000_000
USER_AGENT = "MacroLiquidityDashboard/1.0 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
ASSUMPTIONS = [
    "Microsoft's cash-flow statement reports cash payments for the specified quarter, separately from announced repurchase authorization.",
    "The official earnings navigation identifies the latest published earnings release.",
    "The same issuer cash payments appearing in SEC and issuer IR sources are overlapping observations and must not be added.",
]
LIMITATIONS = [
    "One issuer is not aggregate market buybacks and is not a representative company panel.",
    "Cash settlement can differ from market purchase execution timing, including accelerated repurchase settlements.",
    "Quarterly observations are not monthly flows and are never repeated across months.",
    "HTML layouts or accounting labels can change; mismatches stop extraction instead of substituting another line.",
    "A source publication date has day precision only; known_by is actual retrieval time and original release timestamp is unverified.",
    "The native issuer cash-flow label is retained; equivalence to an exact SEC taxonomy concept is not certified.",
]


class IssuerBuybackError(RuntimeError):
    pass


def _url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port not in (None, 443) or parsed.query or parsed.fragment):
        raise IssuerBuybackError("source_url_not_allowed")
    allowed = ((parsed.hostname == "www.microsoft.com" and
                parsed.path.lower().startswith("/en-us/investor/earnings/")) or
               (parsed.hostname == "aka.ms" and parsed.path == "/latestearnings"))
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
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        with urllib.request.build_opener(_Redirect()).open(request, timeout=20) as response:
            final_url = response.geturl()
            body = response.read(MAX_BYTES + 1)
    _url(final_url)
    captured = _stamp(_now(clock))
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise IssuerBuybackError("invalid_response_size")
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw") / "issuer_buybacks" / (digest + ".html")
    destination = output_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(body)
    except FileExistsError:
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise IssuerBuybackError("raw_archive_hash_mismatch") from None
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


def collect(output_root, transport=None, clock=None):
    """Return one issuer observation and source evidence; never fill SEC failures.

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
    result = {"provider": "issuer_buybacks", "status": "error",
              "fetched_at": _stamp(current), "observations": [], "errors": [],
              "assumptions": list(ASSUMPTIONS), "limitations": list(LIMITATIONS),
              "sources": []}
    try:
        index, index_source = _request(INDEX_URL, output_root, transport, clock)
        result["sources"].append(index_source)
        url = discover_release(index, index_source["source_url"])
        html, source = _request(url, output_root, transport, clock)
        result["sources"].append(source)
        item = parse_microsoft(html, source["source_url"], current.date())
        result["observations"].append({**item, **source})
        result["status"] = "ok"
    except Exception as error:
        if isinstance(error, IssuerBuybackError):
            code = str(error)
        elif isinstance(error, urllib.error.HTTPError):
            code = "http_" + str(error.code)
        else:
            code = "transport_or_parse_" + type(error).__name__
        result["errors"].append({"stage": "microsoft", "code": code})
    result["fetched_at"] = _stamp(_now(clock))
    return result
