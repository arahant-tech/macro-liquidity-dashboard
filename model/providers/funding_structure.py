"""Layer-2 observations: NY Fed ACM and ECB regulatory liquidity composition.

No aggregate liquidity factor, FX proxy, monthly repetition or historical
availability is inferred. Current-vintage sources become known at capture.
"""
from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import math
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from model.live_api import _validate_output_root

PROVIDER = "funding_structure"
ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv"
ACM_PAGE = "https://www.newyorkfed.org/research/data_indicators/term-premia-tabs"
ECB_ROOT = "https://data-api.ecb.europa.eu/service/data/SUP/"
ECB_PAGE = "https://data.ecb.europa.eu/data/datasets/SUP/"
ECB_SCOPE = "SSM significant institutions, changing composition, consolidated supervisory aggregate"
USER_AGENT = "MacroLiquidityDashboard/1.2 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
MAX_BYTES = 1_000_000

# All use the same geography, institution scope, native quarter-end, EUR billions.
# L2/L2B are deliberately excluded: the latest source reports Q with no value.
ECB_ITEMS = {
    "A6310": ("ECB_SI_LCR_LIQUIDITY_BUFFER", "ECB SSM 중요은행 · LCR 유동성 버퍼", "Liquidity buffer, Significant institutions"),
    "A0000": ("ECB_SI_TOTAL_ASSETS", "ECB SSM 중요은행 · 총자산", "Total assets, Significant institutions"),
    "A6400": ("ECB_SI_HQLA_LEVEL1", "ECB SSM 중요은행 · LCR Level 1 자산", "Level 1 assets, unadjusted, Significant institutions"),
    "A6401": ("ECB_SI_HQLA_LEVEL1_CASH", "ECB SSM 중요은행 · L1 현금·중앙은행자산", "Level 1 assets, cash, central bank reserves and central bank assets, Significant institutions"),
    "A6250": ("ECB_SI_HQLA_LEVEL1_GOVERNMENT", "ECB SSM 중요은행 · L1 중앙정부자산", "Level 1 assets, central government assets, Significant institutions"),
    "A6403": ("ECB_SI_HQLA_LEVEL1_OTHER", "ECB SSM 중요은행 · 기타 L1 증권", "Other Level 1 securities assets, Significant institutions"),
    "A6404": ("ECB_SI_HQLA_LEVEL1_COVERED", "ECB SSM 중요은행 · L1 EHQCB", "Level 1 assets, Extremely High Quality Covered Bonds (EHQCB), Significant institutions"),
    "A6280": ("ECB_SI_HQLA_LEVEL2A", "ECB SSM 중요은행 · LCR Level 2A 자산", "Level 2A assets, unadjusted, Significant institutions"),
}
ECB_KEY = "Q.B01.W0._Z." + "+".join(ECB_ITEMS) + "._T.SII._Z.ALL.LE.E.C"
ECB_URL = ECB_ROOT + ECB_KEY + "?format=csvdata&lastNObservations=1"
DERIVED = (
    ("ECB_SI_LCR_BUFFER_ASSET_SHARE", "ECB SSM 중요은행 · LCR 버퍼/총자산", "A6310", "A0000"),
    ("ECB_SI_LEVEL1_CASH_SHARE", "ECB SSM 중요은행 · L1 내 현금·중앙은행자산 비중", "A6401", "A6400"),
    ("ECB_SI_LEVEL1_GOVERNMENT_SHARE", "ECB SSM 중요은행 · L1 내 중앙정부자산 비중", "A6250", "A6400"),
)
TOTAL = 1 + len(ECB_ITEMS) + len(DERIVED)
ASSUMPTIONS = [
    "NY Fed's plotted TERMYld is the 10-year ACM term premium in percent, at the source's monthly end-of-period dates.",
    "ECB supervisory classifications are preserved. LCR liquidity buffer / total assets is a regulatory buffer share, not a reserve ratio or global HQLA share.",
    "Shares use only same-quarter, same-unit, same-scope observations. L1 quality components are not independent liquidity creation stocks.",
    "All observations are current vintage, first usable at actual capture; no historical release timestamp is reconstructed.",
]
LIMITATIONS = [
    "ACM is itself a model estimate, re-estimated monthly using a full sample; historical revisions prohibit treating this download as a real-time research vintage.",
    "ECB covers significant institutions in the SSM with a changing sample. It does not cover the world, all EU banks, dealers or nonbanks.",
    "ECB quarterly LCR categories are delayed and unadjusted where labeled; L1 components shown are not an exhaustive decomposition.",
    "ECB latest aggregate L2 and L2B are marked Q with no numerical observation; no zero or residual substitution is allowed.",
    "Exact EURUSD and USDJPY 3-month cross-currency basis quotes remain unconnected; neither FX spot nor an interest-rate differential is substituted.",
    "These are source observations only. A_t and L_t are not estimated by this provider.",
]
UNCONNECTED = [
    {"series_id": "EURUSD_XCCY_BASIS_3M", "reason": "no_verified_free_continuing_source", "required": "licensed exact-tenor basis quote, leg/sign conventions and public redistribution entitlement"},
    {"series_id": "USDJPY_XCCY_BASIS_3M", "reason": "no_verified_free_continuing_source", "required": "licensed exact-tenor basis quote, leg/sign conventions and public redistribution entitlement"},
    {"series_id": "ECB_SI_HQLA_LEVEL2B", "reason": "source_observation_status_Q_and_empty_value", "required": "public numerical observation with original classification"},
]


class FundingError(RuntimeError):
    pass


def now(clock=None):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def allowed_url(url):
    if url not in (ACM_URL, ECB_URL):
        raise FundingError("unapproved_source_url")
    return url


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url, root, transport=None, clock=None):
    allowed_url(url)
    if transport is not None:
        body = transport(url)
    else:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
        opener = urllib.request.build_opener(Redirect(), urllib.request.HTTPSHandler(context=context))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
        with opener.open(req, timeout=25) as response:
            allowed_url(response.geturl())
            body = response.read(MAX_BYTES + 1)
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise FundingError("invalid_source_size")
    captured = stamp(now(clock))
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw/funding_structure") / (digest + ".csv")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body:
            raise FundingError("immutable_source_conflict")
    else:
        with target.open("xb") as handle:
            handle.write(body)
    return body.decode("utf-8-sig"), {
        "source_url": url, "raw_path": str(relative), "raw_sha256": digest,
        "known_by": captured, "retrieved_at": captured, "original_release_at": None,
        "release_timestamp_verified": False, "raw_scope": "original_public_source_csv",
    }


def number(value, nonnegative=False):
    if not isinstance(value, str) or not re.fullmatch(r"-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?", value):
        raise FundingError("missing_or_invalid_numeric_observation")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise FundingError("invalid_numeric_observation")
    return result


def base(series_id, label, value, unit, start, end, **extra):
    return {"series_id": series_id, "label": label, "name": label,
            "value": value, "unit": unit, "units": unit,
            "provider": PROVIDER, "layer": 2, "track": "macro", "role": "distribution",
            "block": "funding_structure", "period_start": start, "period_end": end,
            "observation_date": end, "research_eligible": False,
            "method": "direct_official_source_observation", **extra}


def parse_acm(text, today):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["RunDates", "TERMYld", "ACMFITYld", "GSWYld"]:
        raise FundingError("acm_schema_changed")
    values = {}
    months = set()
    for row in reader:
        if None in row:
            raise FundingError("acm_malformed_row")
        try:
            when = datetime.strptime(row["RunDates"], "%d-%b-%Y").date()
        except (TypeError, ValueError) as exc:
            raise FundingError("acm_invalid_date") from exc
        if when > today:
            raise FundingError("acm_future_reference_date")
        month = (when.year, when.month)
        if month in months:
            raise FundingError("acm_duplicate_month_or_frequency_changed")
        months.add(month)
        value = number(row["TERMYld"])
        if abs(value) > 100:
            raise FundingError("acm_implausible_percent")
        values[when] = value
    if not values:
        raise FundingError("acm_empty")
    when = max(values)
    return base("NYFED_ACM_TP10_MONTHLY", "미국 ACM 10년 텀 프리미엄 · 월말", values[when], "percent",
                when.isoformat(), when.isoformat(), frequency="monthly", source_page=ACM_PAGE,
                native_period_basis="monthly end-of-period observation, not monthly average",
                measurement_kind="publisher_estimated_term_premium", source_universe="US 10-year Treasury",
                tenor_years=10, status="stale" if (today - when).days > 75 else "ok",
                notes=["NY Fed ACM 월말 추정치. 매월 재추정되어 과거값도 개정됩니다.",
                       "원자료 수준 표시이며 측정모형 입력·백테스트에는 사용하지 않습니다."])


def quarter(value, today):
    match = re.fullmatch(r"(20\d{2}|19\d{2})-Q([1-4])", value or "")
    if not match:
        raise FundingError("ecb_invalid_quarter")
    year, q = map(int, match.groups())
    month = 3 * q
    end = date(year, month, calendar.monthrange(year, month)[1])
    if end > today:
        raise FundingError("ecb_future_reference_quarter")
    return date(year, month - 2, 1).isoformat(), end.isoformat()


def parse_ecb(text, today):
    reader = csv.DictReader(io.StringIO(text))
    required = {"KEY", "FREQ", "REF_AREA", "COUNT_AREA", "COUNTERPART_SECTOR", "CB_ITEM", "SBS_BREAKDOWN", "SBS_DI_1", "SBS_DI_2", "CB_EXP_TYPE", "DATA_TYPE", "BS_SUFFIX", "SBS_SAMPLE_TYPE", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "CONF_STATUS", "TIME_PER_COLLECT", "TITLE", "UNIT_MEASURE", "UNIT_MULT"}
    if not required.issubset(reader.fieldnames or []):
        raise FundingError("ecb_schema_changed")
    parsed, errors, seen = {}, [], set()
    for row in reader:
        item = row.get("CB_ITEM")
        if item not in ECB_ITEMS or item in seen or None in row:
            raise FundingError("ecb_unexpected_or_duplicate_series")
        seen.add(item)
        sid, label, title = ECB_ITEMS[item]
        try:
            key = "Q.B01.W0._Z." + item + "._T.SII._Z.ALL.LE.E.C"
            dimensions = {"KEY": "SUP." + key, "FREQ": "Q", "REF_AREA": "B01", "COUNT_AREA": "W0", "COUNTERPART_SECTOR": "_Z", "SBS_BREAKDOWN": "_T", "SBS_DI_1": "SII", "SBS_DI_2": "_Z", "CB_EXP_TYPE": "ALL", "DATA_TYPE": "LE", "BS_SUFFIX": "E", "SBS_SAMPLE_TYPE": "C", "UNIT_MEASURE": "EUR", "UNIT_MULT": "9", "TIME_PER_COLLECT": "E", "TITLE": title}
            if any(row.get(k) != v for k, v in dimensions.items()):
                raise FundingError("ecb_scope_unit_or_definition_changed")
            if row["OBS_STATUS"] != "A" or row["CONF_STATUS"] != "F":
                raise FundingError("ecb_observation_not_normal_public_value")
            start, end = quarter(row["TIME_PERIOD"], today)
            parsed[item] = base(sid, label, number(row["OBS_VALUE"], True), "billion EUR", start, end,
                frequency="quarterly", source_page=ECB_PAGE + "SUP." + key,
                source_series_key="SUP." + key, native_period_basis="quarter-end stock; no monthly repetition",
                source_universe=ECB_SCOPE, sample_type="changing_composition",
                measurement_kind="supervisory_regulatory_liquidity_classification" if item != "A0000" else "supervisory_total_assets",
                source_observation_status=row["OBS_STATUS"], reference_quarter=row["TIME_PERIOD"],
                status="stale" if (today - date.fromisoformat(end)).days > 210 else "ok",
                notes=["SSM 중요은행 집계 · 구성은행 변동 · 분기말", "LCR 자산분류와 전체 자산을 구별하며 분기값을 월별 반복하지 않습니다."])
        except FundingError as exc:
            errors.append({"series_id": sid, "code": str(exc)})
    for item in ECB_ITEMS.keys() - seen:
        errors.append({"series_id": ECB_ITEMS[item][0], "code": "ecb_series_missing_from_response"})
    return parsed, errors


def derive_shares(items):
    observations, errors = [], []
    for sid, label, numerator_key, denominator_key in DERIVED:
        try:
            numerator, denominator = items.get(numerator_key), items.get(denominator_key)
            if numerator is None or denominator is None:
                raise FundingError("required_share_component_unavailable")
            for key in ("period_start", "period_end", "source_universe", "units", "native_period_basis"):
                if numerator[key] != denominator[key]:
                    raise FundingError("share_components_period_scope_or_unit_mismatch")
            if not 0 <= numerator["value"] <= denominator["value"] or denominator["value"] <= 0:
                raise FundingError("share_components_out_of_range")
            observations.append(base(sid, label, 100 * numerator["value"] / denominator["value"], "percent",
                numerator["period_start"], numerator["period_end"], frequency="quarterly",
                method="same_scope_same_quarter_regulatory_share", source_universe=ECB_SCOPE,
                source_page=numerator["source_page"], native_period_basis=numerator["native_period_basis"],
                sample_type="changing_composition", measurement_kind="derived_regulatory_composition_share",
                components={"numerator_series": numerator["series_id"], "denominator_series": denominator["series_id"],
                            "numerator_billion_eur": numerator["value"], "denominator_billion_eur": denominator["value"]},
                formula="100 * numerator / denominator", aggregation_allowed=False,
                status="stale" if "stale" in (numerator["status"], denominator["status"]) else "ok",
                notes=["동일 분기·동일 SSM 범위의 공시 분자/분모로만 계산합니다.",
                       "구성 비율이며 스칼라 유동성 지표·A_t·L_t가 아닙니다."]))
        except FundingError as exc:
            errors.append({"series_id": sid, "code": str(exc)})
    return observations, errors


def error_code(exc):
    if isinstance(exc, FundingError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return "http_" + str(exc.code)
    return "source_" + type(exc).__name__


def collect(output_root, transport=None, clock=None):
    root = _validate_output_root(output_root)
    current = now(clock)
    result = {"provider": PROVIDER, "total": TOTAL, "success": 0, "observations": [], "sources": [], "errors": [],
              "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS, "unconnected": UNCONNECTED, "research_eligible": False}
    try:
        text, evidence = request(ACM_URL, root, transport, clock)
        result["sources"].append(evidence)
        result["observations"].append({**parse_acm(text, current.date()), **evidence})
    except Exception as exc:
        result["errors"].append({"series_id": "NYFED_ACM_TP10_MONTHLY", "code": error_code(exc)})
    try:
        text, evidence = request(ECB_URL, root, transport, clock)
        result["sources"].append(evidence)
        rows, errors = parse_ecb(text, current.date())
        derived, derivative_errors = derive_shares(rows)
        result["errors"].extend(errors + derivative_errors)
        result["observations"].extend({**row, **evidence} for row in [*rows.values(), *derived])
    except Exception as exc:
        for sid in [*(spec[0] for spec in ECB_ITEMS.values()), *(spec[0] for spec in DERIVED)]:
            result["errors"].append({"series_id": sid, "code": error_code(exc)})
    result["success"] = len(result["observations"])
    result["status"] = "ok" if result["success"] == TOTAL else "partial" if result["success"] else "error"
    return result
