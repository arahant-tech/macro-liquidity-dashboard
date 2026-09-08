"""Current, forward-only crypto observations; no prices, backfill or trade actions.

collect(output_root, transport=None, clock=None) returns observations and errors.
transport(url) may return parsed JSON or raw JSON bytes. Each source is isolated:
an unavailable derivative endpoint does not remove valid supply observations.
No alternative host/venue is tried after a regional or other HTTP restriction.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.request
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = "BTC-USDT-SWAP"
SUPPLY_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=false"
FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=1"
OI_URL = "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP"
URLS = {"stablecoin_supply": SUPPLY_URL, "settled_funding": FUNDING_URL, "open_interest": OI_URL}
STABLECOINS = {"1": "USDT", "2": "USDC"}
SUPPLY_METHOD = "defillama_circulating_peggedUSD_native_tokens_v1"
ASSUMPTIONS = [
    "This is a separate crypto observation track, not the equity equation or a trading signal.",
    "known_by is actual retrieval completion; an archive does not certify an original publication timestamp.",
    "DefiLlama circulating.peggedUSD is used as circulating token quantity for USDT and USDC; no price multiplication or authorized-mint field is used.",
    "The supply change is derived only from two compatible, actually stored captures with increasing capture times.",
    "OKX observations refer only to the fixed BTC-USDT-SWAP contract and are exchange-reported measurements.",
]
LIMITATIONS = [
    "USDT/USDC supply is a third-party proxy, not audited issuer data. Adapter changes, bridges, exclusions and revisions can change the measured supply.",
    "The supply endpoint does not provide an observation timestamp; economic reference time and upstream cache freshness are unknown.",
    "Captured supply change is a net-circulation-change proxy, not gross authorized minting, daily issuance, a price-adjusted USD flow or proof of capital entering BTC.",
    "A single exchange's funding and OI do not measure all crypto leverage. OI contracts, BTC quantity and USD notional are distinct units.",
    "Spot ETF flows and miner selling pressure remain missing; neither can be inferred from OI, funding or supply.",
    "These current captures are not research eligible and do not reopen any sealed historical outcome window.",
    "Regional denial, rate limits and network failures are reported without switching hosts, venues or routes. GitHub runner reachability must be tested on that runner.",
]


class ProviderError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise ProviderError("unapproved_redirect")


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now(clock):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock_must_return_timezone_aware_datetime")
    return value.astimezone(timezone.utc)


def _bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)+"\n").encode()


def _immutable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise ProviderError("immutable_capture_conflict")
        return
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic(path, value):
    temp = path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root/".collect.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProviderError("crypto_collection_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _root(path):
    target = Path(path).expanduser().resolve()
    protected = [PROJECT_ROOT/"research", PROJECT_ROOT/"backtest", PROJECT_ROOT/"model", PROJECT_ROOT/"tests"]
    if target == PROJECT_ROOT or any(target == p or target.is_relative_to(p) or p.is_relative_to(target) for p in protected):
        raise ValueError("output_root_must_be_isolated_from_research_and_code")
    return target


def _request(url, transport, clock, root):
    if url not in URLS.values():
        raise ProviderError("endpoint_not_allowlisted")
    try:
        if transport:
            supplied = transport(url)
            body = supplied if isinstance(supplied, bytes) else _bytes(supplied)
            headers, byte_semantics = {}, "injected_transport_json"
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "MacroLiquidityLive/1.0", "Accept": "application/json"})
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
                # A redirect cannot silently change the approved source or host.
                if response.geturl() != url:
                    raise ProviderError("unapproved_redirect")
                body = response.read(4_000_001)
                headers = {name: response.headers.get(name) for name in ("Date", "Last-Modified", "Age", "ETag")
                           if response.headers.get(name) is not None}
            byte_semantics = "original_http_response_body"
        captured = _now(clock)
        if len(body) > 4_000_000:
            raise ProviderError("response_size_limit")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ProviderError("response_must_be_json_object")
        digest = hashlib.sha256(body).hexdigest()
        raw_path = Path("raw")/(digest+".json")
        _immutable(root/raw_path, body)
        evidence = {"source_url": url, "retrieved_at": _stamp(captured), "known_by": _stamp(captured),
                    "raw_path": str(raw_path), "raw_sha256": digest,
                    "raw_byte_semantics": byte_semantics, "http_response_headers": headers,
                    "original_release_at": None, "original_release_verified": False}
        return data, evidence, captured
    except urllib.error.HTTPError as exc:
        label = "regional_or_access_denial" if exc.code in (403, 451) else "http_error"
        raise ProviderError(f"{label}_{exc.code}") from None
    except ProviderError:
        raise
    except Exception as exc:
        # Never echo a remote body, URL supplied by an exception, or credentials.
        raise ProviderError("transport_"+type(exc).__name__) from None


def _number(value, *, nonnegative=False):
    if isinstance(value, bool):
        raise ProviderError("invalid_numeric_value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ProviderError("invalid_numeric_value") from None
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise ProviderError("invalid_numeric_value")
    return number


def _observation(sid, value, unit, evidence, *, observed_at=None, provider, **extra):
    return {"series_id": sid, "value": value, "unit": unit, "units": unit,
            "observed_at": observed_at, "observation_date": observed_at[:10] if observed_at else None,
            "provider": provider, "track": "crypto", "layer": 5, "frequency": "current_snapshot",
            "research_eligible": False, "value_kind": "raw_observation",
            "publication_time_semantics": "known_by_retrieval_only", **evidence, **extra}


def _supply(data):
    assets = data.get("peggedAssets")
    if not isinstance(assets, list):
        raise ProviderError("stablecoin_schema_missing_peggedAssets")
    result = {}
    for sid, symbol in STABLECOINS.items():
        matches = [r for r in assets if isinstance(r, dict) and str(r.get("id")) == sid]
        if len(matches) != 1 or matches[0].get("symbol") != symbol or matches[0].get("pegType") != "peggedUSD":
            raise ProviderError("stablecoin_identity_or_peg_mismatch_"+symbol)
        circulation = matches[0].get("circulating", {})
        if not isinstance(circulation, dict) or "peggedUSD" not in circulation:
            raise ProviderError("stablecoin_circulating_tokens_missing_"+symbol)
        result[symbol] = _number(circulation["peggedUSD"], nonnegative=True)
    return result


def _prior_supply(root):
    pointer = root/"stablecoin_previous.json"
    if not pointer.exists():
        return None
    try:
        ref = json.loads(pointer.read_text())
        path = (root/ref["path"]).resolve()
        if not path.is_relative_to(root/"stablecoin_snapshots"):
            raise ProviderError("prior_snapshot_path_invalid")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != ref["sha256"]:
            raise ProviderError("prior_snapshot_hash_mismatch")
        snapshot = json.loads(body)
        if (snapshot.get("method") != SUPPLY_METHOD or snapshot.get("source_url") != SUPPLY_URL
                or snapshot.get("unit") != "native_tokens" or set(snapshot.get("circulating", {})) != {"USDT", "USDC"}):
            raise ProviderError("prior_snapshot_incompatible")
        return {**snapshot, "snapshot_path": ref["path"], "snapshot_sha256": ref["sha256"]}
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("prior_snapshot_invalid") from None


def _capture_supply(root, data, evidence, captured):
    values = _supply(data)
    observations = [_observation("CRYPTO_"+symbol+"_CIRCULATING", value, symbol+" tokens", evidence,
        provider="DefiLlama", symbol=symbol, source_quality="third_party_proxy", upstream_observation_time_unknown=True,
        methodology=SUPPLY_METHOD) for symbol, value in values.items()]
    observations.append(_observation("CRYPTO_USDT_USDC_CIRCULATING", sum(values.values()),
        "USDT+USDC native token units at nominal par", evidence, provider="DefiLlama",
        source_quality="third_party_proxy", components=values, methodology=SUPPLY_METHOD,
        is_market_value=False, upstream_observation_time_unknown=True))
    errors, derived = [], []
    try:
        previous = _prior_supply(root)
    except ProviderError as exc:
        errors.append({"source": "stablecoin_net_issuance", "code": str(exc)})
        previous = None
    snapshot = {"method": SUPPLY_METHOD, "source_url": SUPPLY_URL, "unit": "native_tokens",
                "captured_at": _stamp(captured), "circulating": values, **evidence}
    body = _bytes(snapshot)
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("stablecoin_snapshots")/(captured.strftime("%Y%m%dT%H%M%S%fZ")+"_"+digest[:16]+".json")
    _immutable(root/relative, body)
    if previous:
        try:
            earlier = datetime.fromisoformat(previous["captured_at"].replace("Z", "+00:00"))
            elapsed = (captured-earlier).total_seconds()
            if elapsed < 0:
                errors.append({"source": "stablecoin_net_issuance", "code": "capture_clock_moved_backwards"})
                return observations, errors  # Do not replace the chronologically later baseline.
            if elapsed > 0:
                changes = {s: values[s]-_number(previous["circulating"][s], nonnegative=True) for s in values}
                provenance = {"period_start": previous["captured_at"], "period_end": _stamp(captured),
                    "elapsed_capture_seconds": elapsed, "is_daily_rate": False, "is_market_value": False,
                    "value_kind": "derived_net_circulation_change_proxy", "source_quality": "third_party_proxy",
                    "methodology": SUPPLY_METHOD, "prior_snapshot_path": previous["snapshot_path"],
                    "prior_snapshot_sha256": previous["snapshot_sha256"], "current_snapshot_path": str(relative),
                    "current_snapshot_sha256": digest, "upstream_observation_time_unknown": True}
                for symbol, change in changes.items():
                    derived.append(_observation("CRYPTO_"+symbol+"_NET_ISSUANCE_PROXY", change,
                        symbol+" tokens over capture interval", evidence, provider="DefiLlama", **provenance))
                derived.append(_observation("CRYPTO_USDT_USDC_NET_ISSUANCE_PROXY", sum(changes.values()),
                    "USDT+USDC token change over capture interval", evidence, provider="DefiLlama", components=changes, **provenance))
        except (KeyError, ValueError, TypeError, ProviderError):
            errors.append({"source": "stablecoin_net_issuance", "code": "prior_snapshot_time_or_value_invalid"})
    _atomic(root/"stablecoin_previous.json", {"path": str(relative), "sha256": digest})
    return observations+derived, errors


def _okx_row(data):
    if data.get("code") != "0":
        raise ProviderError("exchange_api_error")
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ProviderError("exchange_expected_one_fixed_contract_row")
    row = rows[0]
    if row.get("instId") != CONTRACT or row.get("instType", "SWAP") != "SWAP":
        raise ProviderError("exchange_contract_mismatch")
    return row


def _exchange_time(value, captured, *, stale_seconds, clock_skew_seconds=0):
    number = _number(value, nonnegative=True)
    try:
        observed = datetime.fromtimestamp(number/1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ProviderError("exchange_timestamp_invalid") from None
    age = (captured-observed).total_seconds()
    if age < -clock_skew_seconds:
        raise ProviderError("exchange_timestamp_in_future")
    if age > stale_seconds:
        raise ProviderError("exchange_observation_stale")
    return _stamp(observed), age


def _funding(data, evidence, captured):
    row = _okx_row(data)
    observed, age = _exchange_time(row.get("fundingTime"), captured, stale_seconds=36*3600)
    # fundingRate can be indicative; do not replace absent realizedRate with it.
    rate = _number(row.get("realizedRate"))
    return [_observation("CRYPTO_OKX_BTC_USDT_SWAP_REALIZED_FUNDING", rate,
        "decimal funding rate per reported settlement", evidence, observed_at=observed, provider="OKX",
        venue="OKX", contract=CONTRACT, settlement_method=row.get("method"),
        formula_type=row.get("formulaType"), age_seconds=age, funding_interval_hours=None,
        frequency="latest_settled_funding", annualized=False, indicative_rate_used=False,
        source_quality="official_exchange_reported")]


def _oi(data, evidence, captured):
    row = _okx_row(data)
    reported, age = _exchange_time(row.get("ts"), captured, stale_seconds=30*60, clock_skew_seconds=5)
    # OKX ts is its data-return timestamp. A slightly faster source clock does
    # not establish a future economic observation; retain that timestamp only
    # as source metadata and leave observed_at unknown. Larger offsets fail.
    observed = None if age < 0 else reported
    quantities = [("CONTRACTS", "oi", "contracts"), ("BTC", "oiCcy", "BTC"),
                  ("USD_NOTIONAL", "oiUsd", "USD notional")]
    return [_observation("CRYPTO_OKX_BTC_USDT_SWAP_OI_"+suffix, _number(row.get(field), nonnegative=True),
        unit, evidence, observed_at=observed, provider="OKX", venue="OKX", contract=CONTRACT,
        source_field=field, age_seconds=max(0., age), source_quality="official_exchange_reported",
        source_reported_at=reported, source_clock_ahead_seconds=max(0., -age),
        timestamp_quality="source_clock_ahead_observation_time_unknown" if age < 0 else "exchange_data_return_time",
        is_notional=(field == "oiUsd"), is_market_price=False) for suffix, field, unit in quantities]


def collect(output_root, transport=None, clock=None):
    """Persist source evidence and return current crypto observations independently.

    Does not retry on a second venue or write the aggregator's latest.json.
    The caller should preserve its last-good observation when a source fails.
    On the first valid supply capture, net issuance is unavailable, never zero.
    """
    root = _root(output_root)
    started = _now(clock)
    observations, errors, sources = [], [], []
    with _lock(root):
        for name, url in URLS.items():
            try:
                data, evidence, captured = _request(url, transport, clock, root)
                sources.append({"name": name, **evidence})
                if name == "stablecoin_supply":
                    new, problems = _capture_supply(root, data, evidence, captured)
                    observations.extend(new)
                    errors.extend(problems)
                else:
                    observations.extend((_funding if name == "settled_funding" else _oi)(data, evidence, captured))
            except ProviderError as exc:
                errors.append({"source": name, "code": str(exc), "source_url": url})
            except Exception as exc:
                errors.append({"source": name, "code": "parse_or_persistence_"+type(exc).__name__, "source_url": url})
        result = {"provider": "crypto", "status": "ok" if not errors else "partial" if observations else "error",
            "started_at": _stamp(started), "completed_at": _stamp(_now(clock)), "track": "crypto",
            "observations": observations, "errors": errors, "sources": sources,
            "coverage": "partial_crypto_layer", "missing_channels": ["spot_etf_net_flows", "miner_selling_pressure"],
            "net_issuance_available": any(o["series_id"] == "CRYPTO_USDT_USDC_NET_ISSUANCE_PROXY" for o in observations),
            "assumptions": ASSUMPTIONS, "limitations": LIMITATIONS, "research_eligible": False}
        body = _bytes(result)
        digest = hashlib.sha256(body).hexdigest()
        capture = Path("captures")/(started.strftime("%Y%m%dT%H%M%S%fZ")+"_"+digest[:16]+".json")
        _immutable(root/capture, body)
        result["capture_path"], result["capture_sha256"] = str(capture), digest
    return result
