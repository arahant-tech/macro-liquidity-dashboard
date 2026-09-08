"""Executed issuer cash repurchases from a fixed SEC XBRL panel.

Assumes the standard US-GAAP cash-payment concept matches the requested executed
buyback observation. It does not measure authorizations, daily market orders, or
the whole equity market. Fiscal durations and capture times remain explicit.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import uuid

from model.live_api import _atomic_json, _poll_lock, _stamp, _utc, _validate_output_root

CONCEPT = "PaymentsForRepurchaseOfCommonStock"
TAXONOMY = "us-gaap"
# Frozen before API-value inspection. This convenience panel is not a market sample.
PANEL = (
    {"issuer": "Apple", "ticker": "AAPL", "cik": "0000320193"},
    {"issuer": "Microsoft", "ticker": "MSFT", "cik": "0000789019"},
    {"issuer": "Alphabet", "ticker": "GOOGL", "cik": "0001652044"},
    {"issuer": "Meta Platforms", "ticker": "META", "cik": "0001326801"},
    {"issuer": "Visa", "ticker": "V", "cik": "0001403161"},
)
ASSUMPTIONS = [
    "The exact standard cash-repurchase concept measures executed corporate cash payments, not announced authorization.",
    "Each filing's start/end dates define its fiscal duration; cumulative observations are never repeated across months.",
    "The fixed five-issuer convenience panel was selected before inspecting values and is not market representative.",
]
LIMITATIONS = [
    "A five-issuer panel is not aggregate US equity buybacks and has selection and survivorship bias.",
    "Cash payments can differ in timing from execution of market purchases and can include accelerated-repurchase settlements.",
    "Quarterly and annual filings are delayed; fiscal calendars and YTD durations differ across issuers.",
    "Only the exact common-stock payment concept is used. Missing custom concepts, other equity classes and filing contexts are not imputed.",
    "Filing dates are date-only evidence. Original publication timestamps are unknown; known_by is actual retrieval time.",
    "Current SEC concept history is revised; these captures do not recreate historic point-in-time availability.",
]
SOURCES = [
    "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
    "https://www.sec.gov/about/developer-resources",
]


class BuybackError(RuntimeError):
    pass


def concept_url(cik):
    if cik not in {item["cik"] for item in PANEL}:
        raise BuybackError("issuer_not_in_fixed_panel")
    return f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{TAXONOMY}/{CONCEPT}.json"


def _user_agent():
    # No invented email, copied personal profile or credential search.
    value = os.environ.get("SEC_USER_AGENT", "").strip()
    if not value:
        return "MacroLiquidityDashboard/1.0 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
    if "\r" in value or "\n" in value or len(value) > 300:
        raise BuybackError("invalid_sec_user_agent")
    return value


def _request(url, headers, timeout=20):
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise BuybackError("response_exceeds_concept_size_limit")
        return body
    except urllib.error.HTTPError as error:
        # In particular, do not route around a 403 or masquerade as a browser.
        raise BuybackError("sec_http_" + str(error.code)) from None
    except BuybackError:
        raise
    except Exception as error:
        raise BuybackError("transport_" + type(error).__name__) from None


def select_latest_facts(payload, issuer, retrieved_at):
    """Return latest disclosed period(s), preserving fiscal duration and accession."""
    try:
        if int(payload["cik"]) != int(issuer["cik"]):
            raise ValueError()
    except Exception:
        raise BuybackError("issuer_identity_mismatch") from None
    if payload.get("taxonomy") != TAXONOMY or payload.get("tag") != CONCEPT:
        raise BuybackError("concept_identity_mismatch")
    rows = payload.get("units", {}).get("USD")
    if not isinstance(rows, list):
        raise BuybackError("required_usd_cash_concept_unavailable")
    today = retrieved_at.date()
    eligible = []
    for row in rows:
        if row.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
            continue
        try:
            start = date.fromisoformat(row["start"])
            end = date.fromisoformat(row["end"])
            filed = date.fromisoformat(row["filed"])
            value = float(row["val"])
        except Exception:
            raise BuybackError("malformed_cash_fact") from None
        if not math.isfinite(value) or value < 0:
            raise BuybackError("invalid_cash_payment_value")
        if start > end or end > filed:
            raise BuybackError("invalid_fiscal_or_filing_dates")
        if filed > today or end > today:
            raise BuybackError("future_filing_or_period_rejected")
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(row.get("accn", ""))):
            raise BuybackError("invalid_accession")
        duration = (end - start).days + 1
        if duration > 400:
            raise BuybackError("unsupported_fiscal_duration")
        eligible.append(row)
    if not eligible:
        raise BuybackError("no_eligible_executed_cash_fact")
    latest_end = max(row["end"] for row in eligible)
    latest_filed = max(row["filed"] for row in eligible if row["end"] == latest_end)
    selected = [row for row in eligible if row["end"] == latest_end and row["filed"] == latest_filed]
    unique = {}
    for row in selected:
        key = (row["accn"], row["start"], row["end"], row["form"])
        if key in unique and unique[key]["val"] != row["val"]:
            raise BuybackError("conflicting_same_filing_cash_facts")
        unique[key] = row
    # Latest filing can disclose both quarterly and cumulative amounts. Use its
    # longest current-period duration (YTD or annual), never their sum/difference.
    earliest_start = min(row["start"] for row in unique.values())
    selected_duration = [row for row in unique.values() if row["start"] == earliest_start]
    if len({row["val"] for row in selected_duration}) > 1:
        raise BuybackError("ambiguous_latest_filing_cash_fact")
    chosen = sorted(selected_duration, key=lambda item: item["accn"])[-1:]
    result = []
    for row in sorted(chosen, key=lambda item: (item["start"], item["accn"])):
        duration = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days + 1
        result.append({
            "series_id": "SEC_BUYBACKS_" + issuer["ticker"], "provider": "SEC",
            "issuer": issuer["issuer"], "entity_name": payload.get("entityName"),
            "ticker": issuer["ticker"], "cik": issuer["cik"], "layer": 4,
            "block": "executed_buybacks_fixed_issuer_panel", "track": "equity", "role": "flow",
            "taxonomy": TAXONOMY, "concept": CONCEPT,
            "concept_label": payload.get("label"), "concept_description": payload.get("description"),
            "value": row["val"], "units": "USD", "unit": "USD",
            "observation_date": row["end"], "fiscal_start": row["start"], "fiscal_end": row["end"],
            "period_start": row["start"], "period_end": row["end"],
            "duration_days": duration, "fiscal_year": row.get("fy"), "fiscal_period": row.get("fp"),
            "duration_treatment": "longest_current_as_filed_duration_YTD_or_annual_no_monthly_repetition",
            "alternative_current_durations": [dict(item) for item in unique.values() if item["start"] != row["start"]],
            "frequency": "filing_duration", "accession": row["accn"], "form": row["form"],
            "filed_date": row["filed"], "frame": row.get("frame"),
            "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(issuer['cik'])}/{row['accn'].replace('-', '')}/{row['accn']}-index.html",
            "source_url": concept_url(issuer["cik"]), "retrieved_at": _stamp(retrieved_at),
            "known_by": _stamp(retrieved_at), "original_release_at": None,
            "release_timestamp_verified": False, "research_eligible": False,
            "not_market_aggregate": True,
        })
    return result


def collect(output_root, transport=None, clock=None):
    """Collect five official concept responses. Inject transport(url, headers) for tests."""
    root = _validate_output_root(output_root)
    started = _utc(clock)
    capture_id = started.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:12]
    observations, errors, captures = [], [], []
    with _poll_lock(root):
        previous_path = root / "latest.json"
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
        previous_by_issuer = {item["ticker"]: item for item in previous.get("issuer_status", [])}
        issuer_status = []
        try:
            user_agent = _user_agent()
        except BuybackError:
            user_agent = None
        provider_denied = False
        for issuer in PANEL:
            checked = _utc(clock)
            try:
                if provider_denied:
                    raise BuybackError("sec_http_403_remaining_panel_not_requested")
                if user_agent is None:
                    raise BuybackError("invalid_sec_user_agent")
                # One sequential request per issuer, <= 2/sec even with zero network latency.
                if transport is None:
                    time.sleep(.5)
                url = concept_url(issuer["cik"])
                raw = (transport or _request)(url, {"User-Agent": user_agent, "Accept": "application/json"})
                if isinstance(raw, dict):
                    raw = json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                if not isinstance(raw, bytes):
                    raise BuybackError("transport_must_return_bytes_or_dict")
                if len(raw) > 2_000_000:
                    raise BuybackError("response_exceeds_concept_size_limit")
                try:
                    payload = json.loads(raw)
                except Exception:
                    raise BuybackError("invalid_sec_json") from None
                retrieved_at = _utc(clock)
                selected = select_latest_facts(payload, issuer, retrieved_at)
                relative = Path("raw") / capture_id / (issuer["cik"] + ".json")
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                digest = hashlib.sha256(raw).hexdigest()
                for item in selected:
                    item.update({"raw_path": relative.as_posix(), "raw_sha256": digest})
                fingerprint = hashlib.sha256(json.dumps([
                    {k: item[k] for k in ("cik", "value", "fiscal_start", "fiscal_end", "accession", "filed_date", "form")}
                    for item in selected], sort_keys=True).encode()).hexdigest()
                old = previous_by_issuer.get(issuer["ticker"], {})
                changed = old.get("fingerprint") != fingerprint
                if not changed and old.get("last_good_observations"):
                    selected = old["last_good_observations"]
                age = (retrieved_at.date() - date.fromisoformat(selected[0]["fiscal_end"])).days
                status = "stale" if age > 180 else "ok"
                observations.extend(selected)
                issuer_status.append({**issuer, "status": status, "checked_at": _stamp(retrieved_at),
                                      "fiscal_end": selected[0]["fiscal_end"], "filed_date": selected[0]["filed_date"],
                                      "age_calendar_days": age, "stale_after_days": 180,
                                      "fingerprint": fingerprint, "last_good_observations": selected,
                                      "changed": changed, "error": None})
                captures.append({"ticker": issuer["ticker"], "path": relative.as_posix(), "sha256": digest,
                                 "retrieved_at": _stamp(retrieved_at)})
                if changed:
                    with (root / "observed.jsonl").open("a") as ledger:
                        ledger.write(json.dumps({"capture_id": capture_id, "ticker": issuer["ticker"],
                                                 "observations": selected, "research_eligible": False}, sort_keys=True) + "\n")
                        ledger.flush()
                        os.fsync(ledger.fileno())
            except Exception as error:
                code = str(error) if isinstance(error, BuybackError) else type(error).__name__
                if not re.fullmatch(r"[A-Za-z0-9_]+", code):
                    code = "buybacks_collection_failed"
                if code == "sec_http_403":
                    provider_denied = True
                errors.append({"series_id": "SEC_BUYBACKS_" + issuer["ticker"], "ticker": issuer["ticker"], "error": code})
                old = previous_by_issuer.get(issuer["ticker"], {})
                issuer_status.append({**issuer, "status": "error", "checked_at": _stamp(checked),
                                      "error": code, "last_good_observations": old.get("last_good_observations", []),
                                      "fingerprint": old.get("fingerprint")})
        completed = _utc(clock)
        success = sum(item["status"] != "error" for item in issuer_status)
        stale = sum(item["status"] == "stale" for item in issuer_status)
        result = {"schema_version": 1, "provider": "buybacks", "capture_id": capture_id,
                  "started_at": _stamp(started), "retrieved_at": _stamp(completed), "completed_at": _stamp(completed), "fetched_at": _stamp(completed),
                  "status": "error" if success == 0 else "partial" if errors or stale else "ok",
                  "outcome": "error" if success == 0 else "partial" if errors or stale else "ok",
                  "total": len(PANEL), "success": success, "stale": stale,
                  "observations": observations, "errors": errors, "issuer_status": issuer_status,
                  "raw_captures": captures, "research_eligible": False,
                  "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS, "breaks": LIMITATIONS,
                  "sources": SOURCES, "panel": list(PANEL), "market_aggregate": None,
                  "access_configuration": "Set SEC_USER_AGENT to a truthful application and contact identity if SEC rejects access; do not bypass HTTP 403."}
        _atomic_json(root / "latest.json", result)
        _atomic_json(root / "status.json", {k: v for k, v in result.items() if k not in {"observations", "issuer_status", "raw_captures"}})
        return result
