"""Capture official PBoC monthly tables, without inventing historical vintages.

Assumption: the public response was available by its recorded capture time.
Breaks: current tables may revise past months; capture time is not release time.
The publisher exposes HTML/XLS downloads, not a documented public JSON API.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import math
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

INDEX_URL = "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html"
MAX_BYTES = 3_000_000
ASSUMPTIONS = [
    "known_by is the actual capture time; original publication time is unverified.",
    "Monthly native CNY units and source accounting identities are retained.",
    "The newest linked annual statistics page is the current publication surface.",
]
LIMITATIONS = [
    "PBoC provides public HTML tables rather than a documented public JSON API; layout changes can stop parsing.",
    "Current-year table snapshots do not reconstruct historical real-time vintages.",
    "Deposits of Other Depository Corporations are not excess reserves or currency issue.",
    "TSF stock changes need not equal reported TSF flows because of revisions and coverage changes.",
    "Hourly polling does not make monthly publications intraday data; observations remain research-ineligible pending transformation and provenance review.",
]


class PBoCError(RuntimeError):
    """Stable error codes suitable for publication."""


def _clean(value):
    return " ".join(value.replace("\xa0", " ").replace("\u3000", " ").split())


class _HTML(HTMLParser):
    """Keep row cells, spans and anchor text in ordinary and Excel-export HTML."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.rows, self.links = [], []
        self._rows, self._cells, self._anchors = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._rows.append([])
        elif tag in ("td", "th") and self._rows:
            try:
                span = int(attrs.get("colspan", 1))
            except ValueError:
                raise PBoCError("invalid_column_span") from None
            if not 1 <= span <= 100:
                raise PBoCError("invalid_column_span")
            self._cells.append({"text": [], "links": [], "span": span,
                                "row": self._rows[-1]})
        elif tag == "a":
            self._anchors.append({"href": attrs.get("href", ""), "text": []})
        elif tag == "br":
            self.handle_data(" ")

    def handle_data(self, text):
        if self._cells:
            self._cells[-1]["text"].append(text)
        if self._anchors:
            self._anchors[-1]["text"].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchors:
            anchor = self._anchors.pop()
            anchor["text"] = _clean("".join(anchor["text"]))
            self.links.append(anchor)
            if self._cells:
                self._cells[-1]["links"].append(anchor)
        elif tag in ("td", "th") and self._cells:
            cell = self._cells.pop()
            cell["text"] = _clean("".join(cell["text"]))
            cell.pop("row").append(cell)
        elif tag == "tr" and self._rows:
            self.rows.append(self._rows.pop())


def _utc(clock):
    result = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return result.astimezone(timezone.utc)


def _stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _official_url(base, href):
    url = urllib.parse.urljoin(base, href)
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ("http", "https") or parsed.hostname != "www.pbc.gov.cn"
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)
            or parsed.query or parsed.fragment):
        raise PBoCError("non_official_source_url")
    return urllib.parse.urlunsplit(("https", "www.pbc.gov.cn", parsed.path, "", ""))


def _decode(raw):
    # PBoC's category pages are UTF-8; Excel-generated tables declare gb2312.
    match = re.search(br"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", raw[:4000], re.I)
    declared = match.group(1).decode("ascii").lower() if match else "utf-8"
    encoding = "gb18030" if declared in ("gb2312", "gbk", "gb18030") else declared
    if encoding not in ("utf-8", "utf8", "gb18030"):
        raise PBoCError("unsupported_html_encoding")
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        raise PBoCError("invalid_html_encoding") from None


def _request(url, transport, clock, output_root):
    url = _official_url(url, url)
    if transport:
        raw = transport(url)
        if not isinstance(raw, bytes):
            raise PBoCError("transport_must_return_bytes")
    else:
        request = urllib.request.Request(url, headers={
            "User-Agent": "MacroLiquidityCollector/1.0 (public monthly statistics)",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            _official_url(url, response.geturl())
            raw = response.read(MAX_BYTES + 1)
    captured = _stamp(_utc(clock))
    if not raw or len(raw) > MAX_BYTES:
        raise PBoCError("invalid_response_size")
    digest = hashlib.sha256(raw).hexdigest()
    relative = Path("raw") / "pboc" / (digest + ".html")
    path = output_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PBoCError("raw_archive_hash_mismatch") from None
    provenance = {"source_url": url, "source_sha256": digest,
                  "raw_path": relative.as_posix(), "known_by": captured,
                  "fetched_at": captured, "original_release_at": None}
    return _decode(raw), provenance


def _linked_url(html, base, label):
    links = [link for link in _HTML(html).links if label in link["text"]]
    urls = {_official_url(base, link["href"]) for link in links}
    if len(urls) != 1:
        raise PBoCError("category_link_missing_or_ambiguous")
    return urls.pop()


def _table_url(html, base, label):
    candidates = set()
    for row in _HTML(html).rows:
        if not any(label in cell["text"] for cell in row):
            continue
        for cell in row:
            for link in cell["links"]:
                if link["text"].strip().lower() in ("htm", "html"):
                    url = _official_url(base, link["href"])
                    if not urllib.parse.urlsplit(url).path.lower().endswith((".htm", ".html")):
                        raise PBoCError("table_link_is_not_html")
                    candidates.add(url)
    if len(candidates) != 1:
        raise PBoCError("table_link_missing_or_ambiguous")
    return candidates.pop()


def _expanded(row):
    return [cell["text"] for cell in row for _ in range(cell["span"])]


def _month(text):
    match = re.fullmatch(r"(20\d{2})[.]([01]?\d)", text.strip())
    if not match:
        return None
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise PBoCError("invalid_reference_month")
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _number(text):
    if not text or text.strip() in ("-", "--", "—", "…"):
        return None
    value = text.replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        raise PBoCError("invalid_numeric_cell")
    result = float(value)
    if not math.isfinite(result):
        raise PBoCError("non_finite_value")
    return result


def _horizontal_series(html, label):
    rows = [_expanded(row) for row in _HTML(html).rows]
    headers = [row for row in rows if sum(_month(cell) is not None for cell in row) >= 2]
    values = [row for row in rows if row and re.match(r"^" + re.escape(label) + r"(?:\s|$)", row[0])]
    if len(headers) != 1 or len(values) != 1:
        raise PBoCError("table_header_or_series_ambiguous")
    header, row = headers[0], values[0]
    output, seen_periods = {}, set()
    for index, cell in enumerate(header):
        period = _month(cell)
        if period is None or period in seen_periods:
            continue
        seen_periods.add(period)
        # For TSF stock, the first column in each colspan is Stock, then growth.
        if index >= len(row):
            raise PBoCError("short_data_row")
        value = _number(row[index])
        if value is not None:
            output[period] = value
    if not output:
        raise PBoCError("no_published_observations")
    return output


def parse_balance(html):
    if "万亿元人民币" in html or ("100 Million Yuan" not in _clean(html) and "亿元人民币" not in html):
        raise PBoCError("balance_units_changed")
    assets = _horizontal_series(html, "总资产")
    liabilities = _horizontal_series(html, "总负债")
    deposits = _horizontal_series(html, "其他存款性公司存款")
    if set(assets) != set(liabilities) or set(assets) != set(deposits):
        raise PBoCError("balance_periods_disagree")
    for period in assets:
        if abs(assets[period] - liabilities[period]) > .05:
            raise PBoCError("balance_accounting_identity_failed")
        if not 0 <= deposits[period] <= assets[period] or assets[period] <= 0:
            raise PBoCError("balance_value_out_of_range")
    return [
        ("PBOC_TOTAL_ASSETS", "Total Assets", "总资产", assets, "100 million CNY", "pboc", "stock"),
        ("PBOC_DEPOSITS_OTHER_DEPOSITORY_CORPORATIONS", "Deposits of Other Depository Corporations",
         "其他存款性公司存款", deposits, "100 million CNY", "pboc", "stock"),
    ]


def parse_tsf_stock(html):
    if "万亿元人民币" not in html:
        raise PBoCError("tsf_stock_units_changed")
    result = _horizontal_series(html, "社会融资规模存量")
    if any(value <= 0 for value in result.values()):
        raise PBoCError("tsf_stock_out_of_range")
    return [("PBOC_TSF_STOCK", "Aggregate Financing to the Real Economy (Stock)",
             "社会融资规模存量", result, "trillion CNY", "bank_credit", "stock")]


def parse_tsf_flow(html):
    if "亿元人民币" not in html or "万亿元人民币" in html or "AFRE(flow)" not in html:
        raise PBoCError("tsf_flow_units_or_identity_changed")
    rows = [_expanded(row) for row in _HTML(html).rows]
    # Verify the total AFRE field precedes RMB loans rather than assuming any
    # generic financing table's second column has that meaning.
    identities = [row for row in rows if row and row[0] == "社会融资规模增量"]
    if len(identities) != 1 or len(identities[0]) < 2 or "人民币贷款" not in identities[0][1]:
        raise PBoCError("tsf_flow_column_identity_changed")
    result = {}
    for row in rows:
        period = _month(row[0]) if row else None
        if period is None:
            continue
        if len(row) < 2:
            raise PBoCError("short_data_row")
        value = _number(row[1])
        if value is not None:
            if period in result:
                raise PBoCError("duplicate_reference_month")
            result[period] = value
    if not result:
        raise PBoCError("no_published_observations")
    return [("PBOC_TSF_FLOW", "Aggregate Financing to the Real Economy (Flow)",
             "社会融资规模增量", result, "100 million CNY", "bank_credit", "flow")]


def _error(error):
    if isinstance(error, PBoCError):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"http_{error.code}"
    return "transport_or_io_" + type(error).__name__


def collect(output_root, transport=None, clock=None):
    """Return the four latest monthly observations plus immutable source evidence.

    ``transport(url) -> bytes`` and timezone-aware ``clock()`` are injectable.
    Failure returns no fabricated data and never overwrites a last-good record;
    retaining last-good observations is the calling aggregator's responsibility.
    """
    output_root = Path(output_root).resolve()
    project = Path(__file__).resolve().parents[2]
    if output_root == project or any(output_root == project / folder or
                                    (project / folder) in output_root.parents
                                    for folder in ("research", "backtest", "model")):
        raise ValueError("output_root_must_be_isolated_from_research")
    now = _utc(clock)
    result = {"provider": "pboc", "status": "error", "fetched_at": _stamp(now),
              "observations": [], "errors": [], "assumptions": list(ASSUMPTIONS),
              "limitations": list(LIMITATIONS), "sources": []}

    def fetch(url):
        html, provenance = _request(url, transport, clock, output_root)
        result["sources"].append(provenance)
        return html, provenance

    try:
        index, _ = fetch(INDEX_URL)
        annual = {}
        for link in _HTML(index).links:
            match = re.search(r"(20\d{2})年统计数据", link["text"])
            if match and int(match.group(1)) <= now.year:
                annual[int(match.group(1))] = _official_url(INDEX_URL, link["href"])
        if not annual:
            raise PBoCError("annual_statistics_link_missing")
        year = max(annual)
        result["publication_year"] = year
        annual_url = annual[year]
        annual_html, _ = fetch(annual_url)
    except Exception as error:
        result["errors"].append({"stage": "discovery", "code": _error(error)})
        return result

    jobs = [
        ("货币统计概览", [("balance", "货币当局资产负债表", parse_balance)]),
        ("社会融资规模", [("tsf_stock", "社会融资规模存量统计表", parse_tsf_stock),
                     ("tsf_flow", "社会融资规模增量统计表", parse_tsf_flow)]),
    ]
    for category, tables in jobs:
        try:
            category_url = _linked_url(annual_html, annual_url, category)
            category_html, _ = fetch(category_url)
        except Exception as error:
            result["errors"].append({"stage": category, "code": _error(error)})
            continue
        for stage, label, parser in tables:
            try:
                table_url = _table_url(category_html, category_url, label)
                table_html, provenance = fetch(table_url)
                for series_id, name, native, values, units, block, kind in parser(table_html):
                    period = max(values)
                    if period[:4] != str(year) or period > now.date().isoformat():
                        raise PBoCError("future_or_wrong_year_observation")
                    result["observations"].append({
                        "id": series_id, "series_id": series_id, "name": name,
                        "source_label": native, "period": period,
                        "period_start": period[:7] + "-01", "period_end": period,
                        "value": values[period], "units": units, "unit": units,
                        "frequency": "monthly", "layer": 1, "block": block,
                        "measurement_kind": kind, "currency": "CNY",
                        "research_eligible": False, **provenance,
                    })
            except Exception as error:
                result["errors"].append({"stage": stage, "code": _error(error)})
    result["fetched_at"] = _stamp(_utc(clock))
    result["status"] = ("ok" if not result["errors"] and len(result["observations"]) == 4
                        else "partial" if result["observations"] else "error")
    return result
