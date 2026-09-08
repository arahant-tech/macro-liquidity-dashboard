"""Publisher-reported daily US spot BTC/ETH ETF net flows.

The source is Farside's public flow table, not issuer-certified settlement cash.
No AUM differences, price returns or synthetic NAV-based flows are computed.
Only complete, reconciled prior-New-York-date rows are eligible for display.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from model.live_api import _atomic_json, _poll_lock, _stamp, _utc, _validate_output_root

SPECS = (
    {"asset": "BTC", "series_id": "ETF_BTC_SPOT_NET_FLOW_FARSIDE", "url": "https://farside.co.uk/btc/",
     "funds": ("IBIT", "FBTC", "BITB", "ARKB", "BTCO", "EZBC", "BRRR", "HODL", "BTCW", "MSBT", "GBTC", "BTC")},
    {"asset": "ETH", "series_id": "ETF_ETH_SPOT_NET_FLOW_FARSIDE", "url": "https://farside.co.uk/eth/",
     "funds": ("ETHA", "ETHB", "FETH", "ETHW", "TETH", "ETHV", "QETH", "EZET", "ETHE", "ETH")},
)
MONTHS = {name: number for number, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
ASSUMPTIONS = [
    "Farside's published flow values are observations from a third-party data publisher; issuer cash-settlement methods are not independently certified.",
    "The table's enumerated fund universe is the covered universe, not every crypto investment product worldwide.",
    "A complete prior-New-York-date row with a reconciled total is less provisional than an unfinished current-day row.",
]
LIMITATIONS = [
    "Underlying flow construction and revisions are not independently audited against each issuer's creations and redemptions.",
    "The current New York calendar date is deliberately excluded, even if all cells are filled; this introduces a conservative reporting delay.",
    "A dash, blank or nonnumeric component is missing, not zero. Incomplete dates cannot produce an aggregate.",
    "Rounded component sums must reconcile with the publisher total; explicit fund-universe changes require a code review.",
    "Public HTML is a programmatic source, not a documented JSON API contract; layout changes or access denial can stop collection.",
    "Only BTC and ETH US spot-product universes on the source tables are covered; equity ETF flows and other crypto products remain absent.",
    "Capture time is the earliest knowability certified by this collector; a business date is not an original publication timestamp.",
]


class FlowError(RuntimeError):
    pass


class TableParser(HTMLParser):
    """Read tables while retaining their exact HTML fragment, excluding page footers."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.depth = 0
        self.skip = 0
        self.tables = []
        self.row = None
        self.cell = None
        self.current = None
        self.headings = []
        self.heading_parts = None

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("h1", "h2"):
            self.heading_parts = []
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.current = {"rows": [], "start": self.position()}
        if self.depth == 1 and tag == "tr":
            self.row = []
        if self.depth == 1 and tag in ("td", "th"):
            self.cell = []
        if self.cell is not None and tag == "br":
            self.cell.append(" ")

    def handle_data(self, text):
        if self.heading_parts is not None and not self.skip:
            self.heading_parts.append(text)
        if self.cell is not None and not self.skip:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in ("h1", "h2") and self.heading_parts is not None:
            self.headings.append(" ".join("".join(self.heading_parts).split()))
            self.heading_parts = None
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if self.depth == 1 and tag in ("td", "th") and self.cell is not None:
            if self.row is None:
                raise FlowError("malformed_table_cell")
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if self.depth == 1 and tag == "tr" and self.row is not None:
            self.current["rows"].append(self.row)
            self.row = None
        if tag == "table":
            if self.depth == 1 and self.current is not None:
                end = self.source.find(">", self.position()) + 1
                self.current["fragment"] = self.source[self.current["start"]:end].encode("utf-8")
                self.tables.append(self.current)
                self.current = None
            self.depth = max(0, self.depth - 1)


def _number(text):
    value = text.strip().replace("\u2212", "-")
    if value in ("", "-", "—", "–", "N/A", "n/a"):
        return None
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    if not re.fullmatch(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", value):
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _business_date(text):
    match = re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3}) (\d{4})", text.strip())
    if not match or match[2] not in MONTHS:
        return None
    try:
        return date(int(match[3]), MONTHS[match[2]], int(match[1]))
    except ValueError:
        raise FlowError("invalid_source_business_date") from None


def parse_latest(body, spec, retrieved_at):
    if not isinstance(body, bytes) or len(body) > 2_000_000:
        raise FlowError("invalid_or_oversize_html_response")
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise FlowError("unsupported_source_encoding") from None
    parser = TableParser(source)
    parser.feed(source)
    unit_headings = [heading for heading in parser.headings if re.search(r"ETF\s+Flow\s*\(US\$m\)", heading)]
    if not unit_headings:
        raise FlowError("source_usd_million_unit_unverified")
    matching = []
    anchor = spec["funds"][0]
    for table in parser.tables:
        for index, row in enumerate(table["rows"]):
            if anchor in row and row and row[0] in ("", "Date"):
                if tuple(row[1:-1]) != spec["funds"]:
                    raise FlowError("source_fund_universe_changed")
                if row[-1] not in ("", "Total"):
                    raise FlowError("unrecognized_total_column")
                # The compact table can put Total one row above the ticker row.
                if row[-1] != "Total" and not any(prior and prior[-1] == "Total" for prior in table["rows"][:index]):
                    raise FlowError("total_header_unverified")
                matching.append((table, index))
    if len(matching) != 1:
        raise FlowError("unique_flow_table_not_found")
    table, header_index = matching[0]
    local_today = retrieved_at.astimezone(ZoneInfo("America/New_York")).date()
    candidates, skipped = [], []
    nfunds = len(spec["funds"])
    tolerance = Decimal("0.05") * (nfunds + 1)
    for row in table["rows"][header_index + 1:]:
        if not row:
            continue
        business_date = _business_date(row[0])
        if business_date is None:
            continue
        if business_date > local_today:
            raise FlowError("future_business_date_rejected")
        if business_date == local_today:
            skipped.append({"date": business_date.isoformat(), "reason": "current_new_york_date_provisional"})
            continue
        if business_date.weekday() >= 5:
            raise FlowError("weekend_flow_date_requires_review")
        if len(row) != nfunds + 2:
            raise FlowError("source_table_column_count_changed")
        values = [_number(item) for item in row[1:]]
        if any(value is None for value in values):
            skipped.append({"date": business_date.isoformat(), "reason": "missing_or_nonnumeric_component"})
            continue
        component_sum = sum(values[:-1], Decimal(0))
        difference = abs(component_sum - values[-1])
        if difference > tolerance:
            skipped.append({"date": business_date.isoformat(), "reason": "published_total_does_not_reconcile"})
            continue
        candidates.append({"date": business_date.isoformat(), "values": values,
                           "component_sum": component_sum, "rounding_difference": difference})
    if not candidates:
        raise FlowError("no_complete_reconciled_prior_date")
    latest_date = max(item["date"] for item in candidates)
    latest = [item for item in candidates if item["date"] == latest_date]
    if any(item["values"] != latest[0]["values"] for item in latest[1:]):
        raise FlowError("conflicting_duplicate_business_date")
    selected = latest[0]
    # A newer unreconciled row is not silently treated as a clean completed feed.
    newer_excluded = [item for item in skipped if item["date"] >= latest_date]
    observation = {
        "provider": "etf_flows", "series_id": spec["series_id"],
        "label": spec["asset"] + " 현물 ETF · 일별 순유입 (Farside)",
        "asset": spec["asset"], "layer": 5, "track": "crypto", "role": "flow",
        "value": float(selected["values"][-1]), "unit": "million USD",
        "period_start": latest_date, "period_end": latest_date, "observation_date": latest_date,
        "frequency": "daily_business_date", "known_by": _stamp(retrieved_at),
        "retrieved_at": _stamp(retrieved_at), "source_url": spec["url"],
        "source_publisher": "Farside Investors", "method": "publisher_reported_net_flow",
        "unit_evidence_heading": unit_headings[0],
        "source_quality": "third_party_reported_underlying_cash_method_unverified",
        "source_universe": list(spec["funds"]), "covered_funds": nfunds,
        "components": [{"ticker": ticker, "value": float(value), "unit": "million USD", "business_date": latest_date}
                       for ticker, value in zip(spec["funds"], selected["values"][:-1])],
        "components_complete": True, "component_sum": float(selected["component_sum"]),
        "published_total": float(selected["values"][-1]),
        "rounding_difference": float(selected["rounding_difference"]),
        "rounding_tolerance": float(tolerance), "latest_excluded_dates": newer_excluded,
        "original_release_at": None, "release_timestamp_verified": False, "research_eligible": False,
        "notes": ["게시자 보고 순유입; 발행사 결제현금 독립 인증 아님",
                  f"{nfunds}개 공시 열이 완전한 동일 영업일; 당일 NY 날짜 제외",
                  "음수는 순유출, 0은 명시적 영(零) 흐름; AUM 변화로 계산하지 않음"],
    }
    for excluded in newer_excluded:
        if excluded["reason"] != "current_new_york_date_provisional":
            observation["notes"].append("더 최근의 " + excluded["date"] + " 행 제외: " + excluded["reason"])
    return observation, table["fragment"]


def _request(url, headers):
    if url not in {spec["url"] for spec in SPECS}:
        raise FlowError("source_url_not_allowed")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise FlowError("html_response_too_large")
        return body
    except urllib.error.HTTPError as error:
        raise FlowError("farside_http_" + str(error.code)) from None
    except FlowError:
        raise
    except Exception as error:
        raise FlowError("transport_" + type(error).__name__) from None


def collect(output_root, transport=None, clock=None):
    """Fetch two public flow tables. Inject transport(url, headers)->bytes for tests."""
    root = _validate_output_root(output_root)
    started = _utc(clock)
    capture_id = started.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:12]
    observations, errors, asset_status, captures = [], [], [], []
    headers = {"User-Agent": "MacroLiquidityDashboard/1.0 (https://github.com/arahant-tech/macro-liquidity-dashboard)", "Accept": "text/html"}
    with _poll_lock(root):
        previous_path = root / "latest.json"
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
        previous_assets = {item["asset"]: item for item in previous.get("asset_status", [])}
        denied = False
        for spec in SPECS:
            old = previous_assets.get(spec["asset"], {})
            try:
                if denied:
                    raise FlowError("farside_http_403_remaining_asset_not_requested")
                if transport is None:
                    time.sleep(.5)
                body = (transport or _request)(spec["url"], headers)
                retrieved_at = _utc(clock)
                observation, fragment = parse_latest(body, spec, retrieved_at)
                relative = Path("raw") / capture_id / (spec["asset"].lower() + "_flow_table.html")
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(fragment)
                    handle.flush()
                    os.fsync(handle.fileno())
                observation.update({"raw_path": relative.as_posix(), "raw_sha256": hashlib.sha256(fragment).hexdigest(),
                                    "raw_scope": "exact_flow_table_html_fragment_excluding_page_footer",
                                    "source_response_sha256": hashlib.sha256(body).hexdigest()})
                fingerprint = hashlib.sha256(json.dumps({key: observation[key] for key in
                    ("series_id", "observation_date", "components", "published_total", "source_universe")}, sort_keys=True).encode()).hexdigest()
                changed = old.get("fingerprint") != fingerprint
                if not changed and old.get("last_good_observation"):
                    prior = old["last_good_observation"]
                    for key in ("known_by", "retrieved_at", "raw_path", "raw_sha256", "source_response_sha256"):
                        observation[key] = prior[key]
                age = (retrieved_at.astimezone(ZoneInfo("America/New_York")).date() - date.fromisoformat(observation["observation_date"])).days
                selection_warning = any(item["reason"] != "current_new_york_date_provisional" for item in observation["latest_excluded_dates"])
                status = "stale" if age > 7 else "partial" if selection_warning else "ok"
                observation["status"] = status
                observations.append(observation)
                asset_status.append({"asset": spec["asset"], "series_id": spec["series_id"], "status": status,
                                     "checked_at": _stamp(retrieved_at), "business_date": observation["observation_date"],
                                     "covered_funds": len(spec["funds"]), "age_calendar_days": age, "stale_after_days": 7,
                                     "fingerprint": fingerprint, "last_good_observation": observation,
                                     "changed": changed, "error": None})
                captures.append({"asset": spec["asset"], "raw_path": relative.as_posix(),
                                 "raw_sha256": hashlib.sha256(fragment).hexdigest(), "retrieved_at": _stamp(retrieved_at)})
                if changed:
                    with (root / "observed.jsonl").open("a") as ledger:
                        ledger.write(json.dumps({"capture_id": capture_id, "observation": observation,
                                                 "research_eligible": False}, sort_keys=True) + "\n")
                        ledger.flush()
                        os.fsync(ledger.fileno())
            except Exception as error:
                code = str(error) if isinstance(error, FlowError) else type(error).__name__
                if not re.fullmatch(r"[A-Za-z0-9_]+", code):
                    code = "etf_flow_collection_failed"
                if code == "farside_http_403":
                    denied = True
                errors.append({"series_id": spec["series_id"], "asset": spec["asset"], "code": code, "error": code})
                asset_status.append({"asset": spec["asset"], "series_id": spec["series_id"], "status": "error",
                                     "checked_at": _stamp(_utc(clock)), "error": code,
                                     "fingerprint": old.get("fingerprint"), "last_good_observation": old.get("last_good_observation")})
        completed = _utc(clock)
        success = len(observations)
        stale = sum(item["status"] == "stale" for item in asset_status)
        result = {"schema_version": 1, "provider": "etf_flows", "capture_id": capture_id,
                  "fetched_at": _stamp(completed), "started_at": _stamp(started),
                  "status": "error" if success == 0 else "partial" if errors or stale or any(item["status"] == "partial" for item in asset_status) else "ok",
                  "total": len(SPECS), "success": success, "stale": stale,
                  "observations": observations, "errors": errors, "asset_status": asset_status,
                  "raw_captures": captures, "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS,
                  "breaks": LIMITATIONS, "research_eligible": False}
        _atomic_json(root / "latest.json", result)
        _atomic_json(root / "status.json", {key: value for key, value in result.items()
                                           if key not in {"observations", "asset_status", "raw_captures"}})
        return result
