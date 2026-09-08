"""BIS non-US dollar credit and OFR repo segments, with native definitions.

Credit components are not added to their own total. Repo turnover is neither
collateral reuse nor a terminal net asset flow. No latent state is estimated.
"""
from __future__ import annotations
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from model.live_api import _validate_output_root
from .funding_structure import FundingError, now, stamp, quarter, number

PROVIDER = "offshore"
TOTAL = 5
BIS_URL = "https://stats.bis.org/api/v1/data/WS_GLI/Q.USD.3P.N..I..USD/all?lastNObservations=1"
OFR_ROOT = "https://data.financialresearch.gov/v1/series/multifull"
OFR_PAGE = "https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/"
MAX_BYTES = 1_000_000
USER_AGENT = "MacroLiquidityDashboard/1.2 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
BIS_DIMS = ("FREQ", "CURR_DENOM", "BORROWERS_CTY", "BORROWERS_SECTOR", "LENDERS_SECTOR", "L_POS_TYPE", "L_INSTR", "UNIT_MEASURE")
BIS_SERIES = {
    "Q.USD.3P.N.A.I.B.USD": ("BIS_GLI_USD_NONUS_TOTAL", "BIS 비미국 비은행 USD 신용 · 합계", "USD denominated credit (bank loans & debt securities) to non-bank borrowers located outside the US"),
    "Q.USD.3P.N.B.I.G.USD": ("BIS_GLI_USD_NONUS_BANK_LOANS", "BIS 비미국 비은행 USD 신용 · 은행대출", "USD denominated bank loans to non-bank borrowers located outside the US"),
    "Q.USD.3P.N.A.I.D.USD": ("BIS_GLI_USD_NONUS_DEBT_SECURITIES", "BIS 비미국 비은행 USD 신용 · 국제채권", "USD denominated international debt securities issued by non-bank borrowers located outside the US"),
}
OFR_SERIES = {
    "REPO-DVP_OV_TOT-P": {
        "series_id": "OFR_REPO_DVP_OUTSTANDING", "label": "OFR FICC DVP 레포 잔액 · 잠정",
        "subtype": "Outstanding Volume", "name": "DVP Service Outstanding Volume: Total (Preliminary)",
        "description": "Outstanding volume of all repurchase agreements in the Fixed Income Clearing Corporation's DVP Service",
        "scope": "FICC DVP Service, total repurchase agreements", "role": "stock",
        "basis": "daily outstanding stock", "kind": "reported_repo_outstanding_stock",
    },
    "REPO-TRIV1_TV_TOT-P": {
        "series_id": "OFR_REPO_TRIPARTY_TRANSACTION_VOLUME", "label": "OFR 3자 레포 신규 거래액 · Fed 제외 · 잠정",
        "subtype": "Transaction Volume", "name": "Tri-Party Transaction Volume, excluding Federal Reserve transactions: Total (Preliminary)",
        "description": "Transaction volume of all repurchase agreements starting on a given day that were settled in tri-party repo, after excluding transactions with the Federal Reserve",
        "scope": "US tri-party repo starting that day, excluding Federal Reserve transactions", "role": "funding_activity",
        "basis": "daily gross transaction volume, not an outstanding stock or net asset flow", "kind": "reported_repo_gross_transaction_volume",
    },
}
ASSUMPTIONS = [
    "BIS GLI scope is USD-denominated credit to non-bank borrowers resident outside the US, not all offshore dollar liabilities.",
    "BIS total and its bank-loan / international-debt-securities components are presented separately and never added into a liquidity score.",
    "OFR DVP outstanding stock and tri-party transaction volume retain their different definitions and universes.",
    "Current-vintage values become known only at actual HTTP capture; native quarterly/daily reference periods are preserved.",
]
LIMITATIONS = [
    "BIS GLI draws on LBS and IDS but does not supply the full bilateral LBS matrix or currency basis.",
    "The BIS quarterly source can revise history and its publication lag is long; no historical real-time vintage is inferred.",
    "OFR figures are preliminary, may be revised or disclosure-suppressed, and cover specific repo segments rather than the complete repo market.",
    "Daily gross repo transaction volume is not collateral-reuse velocity, net risk-asset buying, or a measure of dealer balance-sheet headroom.",
    "No currency, source or market-segment totals are collapsed into a global scalar liquidity indicator; A_t and L_t remain unestimated here.",
]


class OffshoreError(RuntimeError):
    pass


def ofr_url(today):
    return OFR_ROOT + "?" + urllib.parse.urlencode({"mnemonics": ",".join(OFR_SERIES),
           "start_date": (today - timedelta(days=45)).isoformat(), "end_date": today.isoformat()})


def ofr_series_url(mnemonic):
    """Stable series identity, separate from the sliding-window request URL."""
    if mnemonic not in OFR_SERIES:
        raise OffshoreError("unapproved_source_mnemonic")
    return "https://data.financialresearch.gov/v1/series/full?" + urllib.parse.urlencode({"mnemonic": mnemonic})


def allowed_url(url):
    if url == BIS_URL:
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "data.financialresearch.gov" or parsed.path != "/v1/series/multifull" or parsed.fragment:
        raise OffshoreError("unapproved_source_url")
    args = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    if set(args) != {"mnemonics", "start_date", "end_date"} or any(len(v) != 1 for v in args.values()) or args["mnemonics"] != [",".join(OFR_SERIES)]:
        raise OffshoreError("unapproved_source_query")
    try:
        start, end = (date.fromisoformat(args[k][0]) for k in ("start_date", "end_date"))
    except ValueError as exc:
        raise OffshoreError("invalid_source_date_range") from exc
    if (end - start).days != 45:
        raise OffshoreError("unbounded_source_date_range")
    return url


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url, root, transport=None, clock=None):
    allowed_url(url)
    if transport is None:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
        opener = urllib.request.build_opener(Redirect(), urllib.request.HTTPSHandler(context=context))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml" if url == BIS_URL else "application/json"})
        with opener.open(req, timeout=25) as response:
            allowed_url(response.geturl())
            body = response.read(MAX_BYTES + 1)
    else:
        body = transport(url)
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise OffshoreError("invalid_source_size")
    captured = stamp(now(clock))
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw/offshore") / (digest + (".xml" if url == BIS_URL else ".json"))
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body:
            raise OffshoreError("immutable_source_conflict")
    else:
        with target.open("xb") as handle:
            handle.write(body)
    return body.decode("utf-8-sig"), {"source_url": url, "raw_path": str(relative), "raw_sha256": digest,
        "known_by": captured, "retrieved_at": captured, "original_release_at": None,
        "release_timestamp_verified": False, "raw_scope": "original_official_source_response"}


def base(sid, label, value, unit, start, end, **extra):
    return {"series_id": sid, "label": label, "name": label, "provider": PROVIDER,
            "layer": 1, "track": "macro", "value": value, "unit": unit, "units": unit,
            "period_start": start, "period_end": end, "observation_date": end,
            "research_eligible": False, "method": "direct_official_source_observation",
            "aggregation_allowed": False, **extra}


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def parse_bis(text, today):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise OffshoreError("bis_xml_entities_forbidden")
    root = ET.fromstring(text)
    if root.tag != "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message}StructureSpecificData":
        raise OffshoreError("bis_sdmx_schema_changed")
    refs = [e for e in root.iter() if local_name(e.tag) == "Ref"]
    if not any(e.attrib.get("agencyID") == "BIS" and e.attrib.get("id") == "WS_GLI" for e in refs):
        raise OffshoreError("bis_wrong_dataflow")
    observations, seen = [], set()
    for series in root.iter():
        if local_name(series.tag) != "Series":
            continue
        key = ".".join(series.attrib.get(k, "") for k in BIS_DIMS)
        if key not in BIS_SERIES or key in seen:
            raise OffshoreError("bis_unexpected_or_duplicate_series")
        seen.add(key)
        sid, label, title = BIS_SERIES[key]
        if any(series.attrib.get(k) != v for k, v in {"TITLE": title, "UNIT_MULT": "6", "AVAILABILITY": "A"}.items()):
            raise OffshoreError("bis_unit_scope_or_definition_changed")
        values = [e for e in series if local_name(e.tag) == "Obs"]
        if len(values) != 1:
            raise OffshoreError("bis_latest_observation_count_changed")
        obs = values[0].attrib
        if obs.get("OBS_STATUS") != "A" or obs.get("OBS_CONF") != "F":
            raise OffshoreError("bis_observation_not_normal_public_value")
        start, end = quarter(obs.get("TIME_PERIOD"), today)
        observations.append(base(sid, label, number(obs.get("OBS_VALUE"), True), "million USD", start, end,
            frequency="quarterly", role="stock", block="offshore_dollar",
            source_series_key=key, source_page="https://data.bis.org/topics/GLI",
            source_universe="USD-denominated credit to non-bank borrowers resident outside the US",
            native_period_basis="quarter-end credit outstanding; no monthly repetition", measurement_kind="bis_gli_credit_stock",
            status="stale" if (today - date.fromisoformat(end)).days > 210 else "ok",
            not_additive_with=[s[0] for s in BIS_SERIES.values() if s[0] != sid],
            notes=["비미국 거주 비은행 차주의 USD 신용. GLI 은행대출 성분은 LBS 기반이지만 전체 LBS 행렬은 아닙니다.",
                   "합계와 구성요소를 중복 합산하지 않습니다."]))
    if seen != set(BIS_SERIES):
        raise OffshoreError("bis_required_series_missing")
    periods = {row["period_end"] for row in observations}
    if len(periods) != 1:
        raise OffshoreError("bis_components_period_mismatch")
    by_id = {row["series_id"]: row for row in observations}
    total = by_id["BIS_GLI_USD_NONUS_TOTAL"]["value"]
    loans = by_id["BIS_GLI_USD_NONUS_BANK_LOANS"]["value"]
    bonds = by_id["BIS_GLI_USD_NONUS_DEBT_SECURITIES"]["value"]
    if abs(total - loans - bonds) > 0.005:
        raise OffshoreError("bis_reported_components_do_not_reconcile")
    return observations


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OffshoreError("duplicate_json_key")
        result[key] = value
    return result


def parse_ofr_series(obj, mnemonic, today):
    spec = OFR_SERIES[mnemonic]
    metadata = obj.get("metadata", {})
    description, schedule, unit = (metadata.get(k, {}) for k in ("description", "schedule", "unit"))
    if metadata.get("mnemonic") != mnemonic or any(description.get(k) != spec[k] for k in ("name", "description", "subtype")):
        raise OffshoreError("ofr_scope_or_definition_changed")
    if description.get("vintage") != "Preliminary" or description.get("subsetting") != "Total":
        raise OffshoreError("ofr_vintage_or_subsetting_changed")
    if any(schedule.get(k) != v for k, v in {"observation_frequency": "Daily", "observation_period": "Single Day", "seasonal_adjustment": "None"}.items()):
        raise OffshoreError("ofr_native_frequency_changed")
    if unit.get("name") != "USD" or unit.get("magnitude") != 0 or unit.get("type") != "Volume":
        raise OffshoreError("ofr_unit_changed")
    timeseries = obj.get("timeseries", {})
    if not isinstance(timeseries.get("aggregation"), list) or not isinstance(timeseries.get("disclosure_edits"), list):
        raise OffshoreError("ofr_timeseries_schema_changed")
    masked, values, observed = set(), {}, set()
    for key in ("disclosure_edits", "aggregation"):
        dates_seen = set()
        for row in timeseries[key]:
            if not isinstance(row, list) or len(row) != 2:
                raise OffshoreError("ofr_malformed_observation")
            when = date.fromisoformat(row[0])
            if when > today:
                raise OffshoreError("ofr_future_reference_date")
            if when in dates_seen:
                raise OffshoreError("ofr_duplicate_reference_date")
            dates_seen.add(when)
            observed.add(when)
            if key == "disclosure_edits" or row[1] is None:
                masked.add(when)
                continue
            if isinstance(row[1], bool) or not isinstance(row[1], (int, float)) or not math.isfinite(row[1]) or not 0 <= row[1] <= 1e17:
                raise OffshoreError("ofr_invalid_volume")
            values[when] = float(row[1])
    for when in masked:
        values.pop(when, None)
    if not values:
        raise OffshoreError("ofr_no_public_numeric_observation")
    latest, newest = max(values), max(observed)
    partial = newest > latest
    result = base(spec["series_id"], spec["label"], values[latest], "USD", latest.isoformat(), latest.isoformat(),
        frequency="daily", role=spec["role"], block="collateral_funding", source_mnemonic=mnemonic,
        source_page=OFR_PAGE, source_universe=spec["scope"], native_period_basis=spec["basis"],
        measurement_kind=spec["kind"], source_vintage="Preliminary", collateral_reuse_measured=False,
        source_last_update_unzoned=schedule.get("last_update"), latest_source_reference_date=newest.isoformat(),
        status="stale" if (today - latest).days > 10 else "partial" if partial else "ok",
        notes=["OFR 잠정치 · 일별 원자료 · 갱신 시각의 시간대는 확인되지 않아 발표시각으로 소급하지 않습니다.",
               "거래량/잔액은 담보 재사용 회전율·실제 위험자산 순매수와 다릅니다."])
    if partial:
        result["notes"].append("더 최신 기준일의 값이 미공개/결측이므로 마지막 유효 공시값을 표시합니다.")
    return result


def parse_ofr(text, today):
    payload = json.loads(text, object_pairs_hook=strict_object)
    if not isinstance(payload, dict) or set(payload) != set(OFR_SERIES):
        raise OffshoreError("ofr_series_universe_changed")
    rows, errors = [], []
    for mnemonic, spec in OFR_SERIES.items():
        try:
            rows.append(parse_ofr_series(payload[mnemonic], mnemonic, today))
        except Exception as exc:
            errors.append({"series_id": spec["series_id"], "code": error_code(exc)})
    return rows, errors


def error_code(exc):
    if isinstance(exc, (OffshoreError, FundingError)):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return "http_" + str(exc.code)
    return "source_" + type(exc).__name__


def collect(output_root, transport=None, clock=None):
    root = _validate_output_root(output_root)
    current = now(clock)
    result = {"provider": PROVIDER, "total": TOTAL, "success": 0, "observations": [], "sources": [], "errors": [],
              "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS, "research_eligible": False}
    for name, url in (("bis", BIS_URL), ("ofr", ofr_url(current.date()))):
        try:
            text, evidence = request(url, root, transport, clock)
            result["sources"].append(evidence)
            if name == "bis":
                rows, errors = parse_bis(text, current.date()), []
            else:
                rows, errors = parse_ofr(text, current.date())
            for row in rows:
                observation = {**row, **evidence}
                if name == "ofr":
                    # Window dates change daily even when the observation does
                    # not. Preserve a canonical identity for vintage retention,
                    # while keeping the exact fetched URL with raw provenance.
                    observation["source_url"] = ofr_series_url(row["source_mnemonic"])
                    observation["requested_url"] = evidence["source_url"]
                result["observations"].append(observation)
            result["errors"].extend(errors)
        except Exception as exc:
            series = [s[0] for s in BIS_SERIES.values()] if name == "bis" else [s["series_id"] for s in OFR_SERIES.values()]
            result["errors"].extend({"series_id": sid, "code": error_code(exc)} for sid in series)
    result["success"] = len(result["observations"])
    result["status"] = "ok" if result["success"] == TOTAL and all(r["status"] == "ok" for r in result["observations"]) else "partial" if result["success"] else "error"
    return result
