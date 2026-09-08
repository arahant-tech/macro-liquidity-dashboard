"""Poll current official macro data without opening the sealed research sample.

This is an observation collector, not a model estimator or a trading signal.
Assumption: a response was available no later than its recorded retrieval time.
Breaks: FRED archive dates are not original publisher timestamps; stale, revised,
or incomplete observations are therefore never automatically research eligible.
"""
from __future__ import annotations

import argparse
import calendar
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "live"
CATALOG_PATH = Path(__file__).with_name("live_catalog.json")
ALLOWED_SERIES = frozenset({
    "WALCL", "WRESBAL", "RRPONTSYD", "WTREGEN", "SOFR", "IORB",
    "TOTBKCR", "DPSACBW027SBOG", "WRBWFRBL", "WLRRAOL", "WDTGAL", "TOTLL",
    "ECBASSETSW", "JPNASSETS", "DRTSCILM", "MMMFFAQ027S",
})
ASSUMPTIONS = [
    "known_by is the capture time, not the economic reference date or original release time.",
    "Native source units and identities are retained; overlapping series are not summed.",
    "Polling cannot increase the underlying daily, weekly, monthly or quarterly release frequency.",
]
BREAKS = [
    "The original publication timestamp and historic vintages are not certified by this collector.",
    "Only the latest five native observations are fetched, so older revisions can be missed.",
    "The collection covers a subset of the five-layer model; missing sources are not imputed.",
]


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    layer: int
    block: str
    expected_frequency: str
    stale_after_days: int
    expected_units: str | None = None
    reference_period_end: bool = False


class FredError(RuntimeError):
    """A deliberately redacted error code, never a credential-bearing URL."""


def _utc(clock=None):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock_must_return_timezone_aware_datetime")
    return value.astimezone(timezone.utc)


def _stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2,
                       allow_nan=False) + "\n").encode()


def _atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _redact(value, key):
    if isinstance(value, dict):
        return {k: _redact(v, key) for k, v in value.items()
                if str(k).lower() not in {"api_key", "authorization", "password", "token"}}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        return value.replace(key, "[redacted]") if key else value
    return value


def load_credential():
    """Reuse the project's existing, read-only credential lookup in memory."""
    value = os.environ.get("FRED_API_KEY", "")
    if re.fullmatch(r"[A-Za-z0-9]{32}", value):
        return value, "environment"
    path = ROOT / "research" / "us-equities-v3_2" / "fetch.py"
    if not path.exists():
        raise FredError("credential_unavailable")
    module_spec = importlib.util.spec_from_file_location("_live_fred_credentials", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    try:
        return module.credential()
    except Exception:
        raise FredError("credential_unavailable") from None


def load_catalog():
    content = json.loads(CATALOG_PATH.read_text())
    allowed_fields = set(SeriesSpec.__dataclass_fields__)
    return [SeriesSpec(**{k: v for k, v in item.items() if k in allowed_fields})
            for item in content["series"]]


class FredClient:
    def __init__(self, key: str, transport: Callable | None = None,
                 sleeper=time.sleep, timeout=18, max_attempts=2,
                 throttle_seconds=.15):
        self.key = key
        self.transport = transport
        self.sleeper = sleeper
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.throttle_seconds = throttle_seconds
        self.deadline = None

    def _remaining(self):
        return (self.deadline - time.monotonic()) if self.deadline else self.timeout

    def request(self, endpoint, params):
        if endpoint not in {"series", "series/observations"}:
            raise FredError("endpoint_not_allowed")
        if params.get("series_id") not in ALLOWED_SERIES:
            raise FredError("series_not_allowed")
        for attempt in range(self.max_attempts):
            if self._remaining() <= 0:
                raise FredError("poll_time_budget_exhausted")
            self.sleeper(min(self.throttle_seconds, max(0, self._remaining())))
            try:
                if self.transport:
                    value = self.transport(endpoint, dict(params))
                else:
                    args = {**params, "api_key": self.key, "file_type": "json"}
                    url = "https://api.stlouisfed.org/fred/" + endpoint + "?" + urllib.parse.urlencode(args)
                    request = urllib.request.Request(url, headers={"User-Agent": "MacroLiquidityLive/1.0"})
                    with urllib.request.urlopen(request, timeout=max(.1, min(self.timeout, self._remaining()))) as response:
                        value = json.load(response)
                if not isinstance(value, dict) or "error_code" in value:
                    raise FredError("invalid_api_response")
                return _redact(value, self.key)
            except urllib.error.HTTPError as error:
                code = "http_" + str(error.code)
                retry = error.code == 429 or error.code >= 500
            except FredError:
                raise
            except Exception as error:
                code = "transport_" + type(error).__name__
                retry = True
            if not retry or attempt + 1 >= self.max_attempts:
                raise FredError(code) from None
            self.sleeper(min(.5 * (2 ** attempt), max(0, self._remaining())))
        raise FredError("request_failed")


def _validate_output_root(path):
    path = Path(path).expanduser().resolve()
    protected = [ROOT / "research", ROOT / "backtest", ROOT / "model"]
    if path == ROOT or any(path == p or path.is_relative_to(p) or p.is_relative_to(path)
                           for p in protected):
        raise ValueError("output_root_must_be_isolated_from_research")
    return path


@contextmanager
def _poll_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".poll.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("poll_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_previous(root):
    path = root / "latest.json"
    if not path.exists():
        return {}
    try:
        content = json.loads(path.read_text())
        if content.get("schema_version") != 1 or not isinstance(content.get("series"), dict):
            raise ValueError()
        return content["series"]
    except Exception:
        # Do not overwrite a damaged last-good file with a seemingly fresh blank state.
        raise RuntimeError("previous_latest_invalid") from None


def _validated_payload(spec, metadata_body, observation_body, as_of):
    metadata = metadata_body.get("seriess", [])
    if len(metadata) != 1 or metadata[0].get("id") != spec.series_id:
        raise FredError("metadata_identity_mismatch")
    metadata = metadata[0]
    frequency = str(metadata.get("frequency", ""))
    units = str(metadata.get("units", ""))
    if not frequency or not units:
        raise FredError("metadata_incomplete")
    if spec.expected_frequency and not frequency.lower().startswith(spec.expected_frequency.lower()):
        raise FredError("metadata_frequency_changed")
    if spec.reference_period_end and "end of period" not in frequency.lower():
        raise FredError("metadata_period_semantics_changed")
    if spec.expected_units and units != spec.expected_units:
        raise FredError("metadata_units_changed")
    for body in (metadata_body, observation_body):
        for field in ("realtime_start", "realtime_end"):
            if body.get(field):
                try:
                    vintage_date = date.fromisoformat(body[field])
                except Exception:
                    raise FredError("invalid_vintage_date") from None
                if vintage_date > as_of:
                    raise FredError("future_vintage_rejected")
    rows = observation_body.get("observations")
    if not isinstance(rows, list) or len(rows) > 5:
        raise FredError("unbounded_or_invalid_observations")
    valid = []
    seen = set()
    for row in rows:
        try:
            observation_date = date.fromisoformat(row["date"])
        except Exception:
            raise FredError("invalid_observation_date") from None
        if observation_date > as_of:
            raise FredError("future_observation_rejected")
        if row.get("realtime_start"):
            try:
                vintage_start = date.fromisoformat(row["realtime_start"])
            except Exception:
                raise FredError("invalid_vintage_date") from None
            if vintage_start > as_of:
                raise FredError("future_vintage_rejected")
        if row["date"] in seen:
            raise FredError("duplicate_observation_date")
        seen.add(row["date"])
        value = str(row.get("value", "."))
        if value in {"", "."}:
            continue
        try:
            if not math.isfinite(float(value)):
                raise ValueError()
        except Exception:
            raise FredError("invalid_observation_value") from None
        valid.append({"date": row["date"], "value": value,
                      "realtime_start": row.get("realtime_start"),
                      "realtime_end": row.get("realtime_end")})
    if not valid:
        raise FredError("no_recent_valid_observation")
    valid.sort(key=lambda row: row["date"], reverse=True)
    return metadata, valid


def _freshness(last_good, checked_at, threshold, reference_period_end=False):
    reference_date = date.fromisoformat(last_good["observation_date"])
    basis_date = reference_date
    if reference_period_end:
        # The catalog enables this only for verified end-of-quarter stock series.
        # FRED labels the quarter by its first day; retain that source date intact.
        month = ((reference_date.month - 1) // 3 + 1) * 3
        basis_date = date(reference_date.year, month, calendar.monthrange(reference_date.year, month)[1])
        if basis_date > checked_at.date():
            raise FredError("future_reference_period_end_rejected")
    age = (checked_at.date() - basis_date).days
    return {"age_calendar_days": age, "stale_after_days": threshold,
            "reference_date_stale": age > threshold, "basis_date": basis_date.isoformat(),
            "raw_reference_age_days": (checked_at.date() - reference_date).days,
            "basis": "verified_quarter_end" if reference_period_end else "native_observation_date",
            "definition": "Age of the verified economic period end where configured, otherwise the native reference date; not a certification of the release schedule."}


def _append_ledger(root, record):
    with (root / "observed.jsonl").open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _derived_spread(series):
    """Funding spread only: align exact native dates and preserve input evidence."""
    inputs = [series.get(sid, {}) for sid in ("SOFR", "IORB")]
    if any(item.get("status") == "error" or not item.get("last_good") for item in inputs):
        return {"status": "unavailable", "reason": "both_inputs_must_have_successful_capture", "research_eligible": False}
    goods = [item["last_good"] for item in inputs]
    if any(item.get("source_units") != "Percent" for item in goods):
        return {"status": "unavailable", "reason": "input_units_must_be_percent", "research_eligible": False}
    values = [{row["date"]: row["value"] for row in item["observations"]} for item in goods]
    common = set(values[0]).intersection(values[1])
    if not common:
        return {"status": "unavailable", "reason": "no_matching_observation_date", "research_eligible": False}
    reference = max(common)
    return {"status": "stale" if any(item["status"] == "stale" for item in inputs) else "ok",
            "observation_date": reference,
            "value": 100 * (float(values[0][reference]) - float(values[1][reference])),
            "units": "basis points", "formula": "100 * (SOFR - IORB), identical observation date",
            "known_by": _stamp(max(datetime.fromisoformat(item["known_by"].replace("Z", "+00:00")) for item in goods)),
            "input_captures": [{"series_id": sid, "raw_path": item["raw_path"], "raw_sha256": item["raw_sha256"]}
                               for sid, item in zip(("SOFR", "IORB"), goods)],
            "research_eligible": False}


def poll(output_root=DEFAULT_OUTPUT_ROOT, specs=None, client=None, clock=None,
         max_poll_seconds=150):
    root = _validate_output_root(output_root)
    specs = list(specs if specs is not None else load_catalog())
    if not specs or len({spec.series_id for spec in specs}) != len(specs):
        raise ValueError("empty_or_duplicate_series_catalog")
    for spec in specs:
        if spec.series_id not in ALLOWED_SERIES or spec.layer not in (1, 2):
            raise ValueError("series_not_allowed")
        if spec.reference_period_end and spec.series_id != "MMMFFAQ027S":
            raise ValueError("unverified_reference_period_semantics")
        if spec.stale_after_days < 1:
            raise ValueError("invalid_staleness_threshold")
    credential_error = False
    if client is None:
        try:
            key, _ = load_credential()
            client = FredClient(key)
        except Exception:
            credential_error = True
    started = _utc(clock)
    capture_id = started.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:12]
    with _poll_lock(root):
        previous = _read_previous(root)
        series = {}
        changes = 0
        captures = []
        if client is not None:
            client.deadline = time.monotonic() + max_poll_seconds
        for spec in specs:
            checked_at = _utc(clock)
            entry = {"series_id": spec.series_id, "layer": spec.layer, "block": spec.block,
                     "checked_at": _stamp(checked_at), "research_eligible": False}
            old = previous.get(spec.series_id, {})
            old_good = old.get("last_good") or {}
            try:
                if credential_error:
                    raise FredError("credential_unavailable")
                day = started.date().isoformat()
                params = {"series_id": spec.series_id, "realtime_start": day, "realtime_end": day}
                metadata_body = client.request("series", params)
                observation_body = client.request("series/observations", {
                    **params, "observation_end": day, "sort_order": "desc", "limit": 5,
                    "units": "lin", "output_type": 1,
                })
                retrieved_at = _utc(clock)
                metadata, observations = _validated_payload(spec, metadata_body, observation_body, started.date())
                # Only authenticated successful public payloads are persisted. Request URLs and keys are absent.
                raw = {"schema_version": 1, "series_id": spec.series_id,
                       "retrieved_at": _stamp(retrieved_at), "known_by": _stamp(retrieved_at),
                       "request": {"provider": "FRED", "observations": {
                           **params, "observation_end": day, "sort_order": "desc", "limit": 5,
                           "units": "lin", "output_type": 1}},
                       "metadata_response": metadata_body, "observation_response": observation_body,
                       "research_eligible": False}
                raw_bytes = _json_bytes(raw)
                relative_path = Path("raw") / capture_id / (spec.series_id + ".json")
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    handle.write(raw_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                capture_hash = hashlib.sha256(raw_bytes).hexdigest()
                # Query-specific realtime dates change on each polling day. Values/reference dates define revisions.
                fingerprint_value = {"observations": [{"date": row["date"], "value": row["value"]}
                                                       for row in observations],
                                     "units": metadata["units"], "frequency": metadata["frequency"],
                                     "source_last_updated": metadata.get("last_updated")}
                fingerprint = hashlib.sha256(_json_bytes(fingerprint_value)).hexdigest()
                latest_observation = observations[0]
                good = {"observation_date": latest_observation["date"], "value": latest_observation["value"],
                        "retrieved_at": _stamp(retrieved_at), "known_by": _stamp(retrieved_at),
                        "raw_path": relative_path.as_posix(), "raw_sha256": capture_hash,
                        "source_frequency": metadata["frequency"], "source_units": metadata["units"],
                        "source_last_updated": metadata.get("last_updated"),
                        "source_vintage_start": observation_body.get("realtime_start"),
                        "source_vintage_end": observation_body.get("realtime_end"),
                        "original_release_at": None, "release_timestamp_verified": False,
                        "source_url": "https://fred.stlouisfed.org/series/" + spec.series_id,
                        "observations": observations, "fingerprint": fingerprint,
                        "research_eligible": False}
                changed = old_good.get("fingerprint") != fingerprint
                if not changed:
                    # Preserve the earliest time this unchanged capture was actually observed.
                    good = old["last_good"]
                entry.update({"last_good": good, "checked_at": _stamp(retrieved_at), "error": None,
                              "last_capture": {"raw_path": relative_path.as_posix(), "raw_sha256": capture_hash,
                                               "retrieved_at": _stamp(retrieved_at)},
                              "freshness": _freshness(good, retrieved_at, spec.stale_after_days, spec.reference_period_end)})
                entry["status"] = "stale" if entry["freshness"]["reference_date_stale"] else "ok"
                captures.append(entry["last_capture"])
                if changed:
                    changes += 1
                    _append_ledger(root, {"schema_version": 1, "capture_id": capture_id,
                                         "series_id": spec.series_id, "event": "first_observed" if not old.get("last_good") else "changed",
                                         "previous_fingerprint": old_good.get("fingerprint"),
                                         "last_good": good, "research_eligible": False})
            except Exception as error:
                code = str(error) if isinstance(error, FredError) else type(error).__name__
                if client is not None and client.key:
                    code = code.replace(client.key, "[redacted]")
                # FredError messages produced internally are fixed codes. Never persist arbitrary text.
                if not re.fullmatch(r"[A-Za-z0-9_]+", code):
                    code = "fetch_failed"
                entry.update({"status": "error", "error": code, "last_good": old.get("last_good"),
                              "checked_at": _stamp(_utc(clock)), "fetch_failed": True})
                if entry["last_good"]:
                    entry["freshness"] = _freshness(entry["last_good"], _utc(clock), spec.stale_after_days, spec.reference_period_end)
            series[spec.series_id] = entry
        completed = _utc(clock)
        errors = sum(item["status"] == "error" for item in series.values())
        stale = sum(item["status"] == "stale" for item in series.values())
        common = {"schema_version": 1, "capture_id": capture_id, "started_at": _stamp(started),
                  "completed_at": _stamp(completed), "research_eligible": False,
                  "assumptions": ASSUMPTIONS, "breaks": BREAKS}
        latest = {**common, "series": series, "derived": {"SOFR_IORB": _derived_spread(series)}}
        status = {**common, "total": len(specs), "success": len(specs) - errors,
                  "errors": errors, "stale": stale, "changed": changes,
                  "outcome": "error" if errors == len(specs) else "partial" if errors or stale else "ok",
                  "series": {sid: {k: value[k] for k in ("status", "checked_at", "error")}
                             for sid, value in series.items()}}
        _atomic_json(root / "latest.json", latest)
        _atomic_json(root / "manifest.json", {**common, "catalog": [asdict(item) for item in specs],
                                             "latest_poll_captures": captures,
                                             "immutable_capture_directory": "raw", "append_only_ledger": "observed.jsonl"})
        _atomic_json(root / "status.json", status)
        return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect current official macro observations into an isolated live store.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--check-config", action="store_true", help="Check the catalog and credential availability without network requests.")
    args = parser.parse_args(argv)
    try:
        _validate_output_root(args.output_root)
        if args.check_config:
            specs = load_catalog()
            _, location = load_credential()
            print(json.dumps({"configured_series": len(specs), "credential_available": True,
                              "credential_source": location, "network_called": False,
                              "research_eligible": False}))
            return 0
        result = poll(args.output_root)
        print(json.dumps({k: result[k] for k in ("outcome", "total", "success", "errors", "stale", "changed", "completed_at")}, ensure_ascii=False))
        return 0 if result["outcome"] == "ok" else 2
    except Exception as error:
        code = str(error) if isinstance(error, (FredError, ValueError, RuntimeError)) else type(error).__name__
        if not re.fullmatch(r"[A-Za-z0-9_]+", code):
            code = "live_collection_failed"
        print(json.dumps({"outcome": "error", "error": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
