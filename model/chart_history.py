"""Native current-vintage source histories for display only, never model inputs.

Historical dates describe the observation period, not historical availability.
No outcome data, aggregate liquidity score, resampling or imputation is used.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil

from model import live_api
from model.providers import funding_structure, intermediary, pboc, terminal_flows

FRED_IDS = ("WRESBAL", "WALCL", "ECBASSETSW", "JPNASSETS", "RRPONTSYD", "WTREGEN")
PBOC_PARSERS = {"PBOC_TOTAL_ASSETS": pboc.parse_balance,
                "PBOC_DEPOSITS_OTHER_DEPOSITORY_CORPORATIONS": pboc.parse_balance,
                "PBOC_TSF_STOCK": pboc.parse_tsf_stock, "PBOC_TSF_FLOW": pboc.parse_tsf_flow}
LABELS = {"WRESBAL": "Fed 지급준비금", "WALCL": "Fed 총자산", "ECBASSETSW": "ECB 총자산",
          "JPNASSETS": "BOJ 총자산", "RRPONTSYD": "미국 익일물 역레포", "WTREGEN": "미 재무부 일반계정",
          "PBOC_TOTAL_ASSETS": "PBoC 총자산",
          "PBOC_DEPOSITS_OTHER_DEPOSITORY_CORPORATIONS": "PBoC 예금취급기관 예금",
          "PBOC_TSF_STOCK": "중국 사회융자총량 잔액", "PBOC_TSF_FLOW": "중국 사회융자총량 월간 흐름"}
MONTHLY_SOURCES = {
    "NYFED_ACM_TP10_MONTHLY": ("funding_structure", funding_structure.ACM_URL, "csv", "line"),
    "FINRA_MARGIN_DEBT": ("intermediary", intermediary.FINRA, "html", "line"),
    "TIC_US_EQUITY_FOREIGN_NET_PURCHASES": ("terminal_flows", terminal_flows.TIC_URL, "txt", "bar"),
}


class HistoryError(ValueError):
    pass


def monthly_history(sid, body, start, end):
    """Validate the publisher's definition before selecting native monthly cells.

    A month label is a reference period, never its publication date. ACM's
    actual business-day labels are retained; FINRA/TIC calendar month ends
    merely label their published balance/transaction reference months.
    """
    text = body.decode("utf-8-sig")
    values = []
    if sid == "NYFED_ACM_TP10_MONTHLY":
        latest = funding_structure.parse_acm(text, end)
        for row in csv.DictReader(io.StringIO(text)):
            when = datetime.strptime(row["RunDates"], "%d-%b-%Y").date()
            values.append((when, funding_structure.number(row["TERMYld"])))
    elif sid == "FINRA_MARGIN_DEBT":
        latest = next(r for r in intermediary.parse_finra(body, end) if r["series_id"] == sid)
        for row in (pboc._expanded(r) for r in pboc._HTML(text).rows):
            if row and re.fullmatch(r"[A-Z][a-z]{2}-\d{2}", row[0]):
                when = datetime.strptime(row[0], "%b-%y")
                values.append((intermediary.month_end(when.year, when.month, end), intermediary.numeric(row[1])))
    elif sid == "TIC_US_EQUITY_FOREIGN_NET_PURCHASES":
        latest = terminal_flows.parse_tic(text, end)
        rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        index = next(i for i, row in enumerate(rows) if row and row[0] == "country")
        header = rows[index]
        code_index = header.index("country_code")
        for row in rows[index + 1:]:
            if len(row) <= code_index or row[code_index] != "99996":
                continue
            # parse_tic already validates every Grand Total identity, date and
            # column count. Pre-February-2023 net flows have a structural break.
            item = dict(zip(header, row))
            if item["date"] < "2023-02":
                continue
            year, month = map(int, item["date"].split("-"))
            when = intermediary.month_end(year, month, end)
            raw = item["for_lt_eqty_net"]
            value = None if raw.strip().lower() == "n.a." else float(terminal_flows.number(raw))
            values.append((when, value))
    else:
        raise HistoryError("unknown_monthly_history_series")
    months = [(d.year, d.month) for d, _ in values]
    if len(set(months)) != len(months):
        raise HistoryError("monthly_history_duplicate_reference_month")
    if any(d > end or (v is not None and not math.isfinite(v)) for d, v in values):
        raise HistoryError("monthly_history_future_or_invalid")
    selected = sorted((d, v) for d, v in values if start <= d <= end)[-36:]
    if sum(v is not None for _, v in selected) < 12:
        raise HistoryError("monthly_history_fewer_than_12_observations")
    points = [{"date": d.isoformat(), "value": v} for d, v in selected]
    if points[-1] != {"date": latest["period_end"], "value": latest["value"]}:
        raise HistoryError("monthly_history_latest_point_mismatch")
    return latest, points


def cached_monthly_history(sid, live_root, start, captured):
    """Read this provider run's complete raw receipt, never an old cell's raw.

    An unchanged latest value can retain an older observation receipt while
    prior months have been revised. Full-history equality is tested separately
    by collect.accept, which preserves its first evidence and known_by together.
    """
    provider, url, suffix, chart_type = MONTHLY_SOURCES[sid]
    root = (Path(live_root) / provider).resolve()
    state = read(root / "latest.json")
    observations = [o for o in state.get("observations", []) if o.get("series_id") == sid]
    receipts = [r for r in state.get("sources", []) if r.get("source_url") == url]
    if len(observations) != 1 or len(receipts) != 1:
        raise HistoryError("fresh_monthly_history_source_unavailable")
    obs, receipt = observations[0], receipts[0]
    if obs.get("source_url") != url:
        raise HistoryError("monthly_history_source_identity_changed")
    rel = Path(receipt.get("raw_path", ""))
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != "raw":
        raise HistoryError("invalid_monthly_history_evidence_path")
    source = (root / rel).resolve()
    if not source.is_relative_to(root) or not source.is_file() or not 0 < source.stat().st_size <= 4_000_000:
        raise HistoryError("invalid_monthly_history_evidence_file")
    body = source.read_bytes()
    if hashlib.sha256(body).hexdigest() != receipt.get("raw_sha256"):
        raise HistoryError("monthly_history_evidence_hash_mismatch")
    try:
        known = datetime.fromisoformat(receipt["known_by"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoryError("invalid_monthly_history_known_by") from exc
    if known.tzinfo is None or known > captured:
        raise HistoryError("invalid_monthly_history_known_by")
    latest, points = monthly_history(sid, body, start, captured.date())
    if any(obs.get(k) != latest.get(k) for k in ("value", "unit", "period_end", "frequency")):
        raise HistoryError("monthly_history_live_observation_mismatch")
    if known.date() < date.fromisoformat(points[-1]["date"]):
        raise HistoryError("monthly_history_known_before_reference_period")
    known_by = live_api._stamp(known.astimezone(timezone.utc))
    row = {"series_id": sid, "label": latest["label"], "provider": provider,
           "points": points, "unit": latest["unit"], "frequency": "Monthly",
           "known_by": known_by, "source_retrieved_at": known_by,
           "checked_at": live_api._stamp(captured), "source_url": url,
           "source_universe": latest["source_universe"],
           "native_period_basis": latest.get("native_period_basis", latest.get("reference_basis", "reported monthly transactions, calendar-month reference period")),
           "status": latest["status"], "chart_type": chart_type, "track": "equity",
           "history_basis": "current_vintage_available_monthly_history_max_36_observations",
           "research_eligible": False}
    for key in ("source_page", "source_series_id", "sign_convention", "structural_break", "measurement_kind", "notes"):
        if key in latest:
            row[key] = latest[key]
    return row, body, suffix


def read(path, fallback=None):
    return json.loads(path.read_text()) if path.exists() else (fallback or {})


def validate_fred(spec, metadata, payload, start, end):
    rows = payload.get("observations")
    if not isinstance(rows, list) or not rows or len(rows) > 2000:
        raise live_api.FredError("invalid_history_length")
    if payload.get("count", len(rows)) != len(rows):
        raise live_api.FredError("history_pagination_incomplete")
    # Reuse identity, units, frequency and vintage validation without weakening
    # the live collector's five-observation contract.
    sample = [r for r in rows if str(r.get("value", ".")) not in ("", ".")][:1]
    meta, _ = live_api._validated_payload(spec, metadata, {**payload, "observations": sample}, end)
    points, seen = [], set()
    for r in rows:
        d = date.fromisoformat(r["date"])
        if not start <= d <= end or d in seen:
            raise live_api.FredError("invalid_history_date")
        seen.add(d)
        for field in ("realtime_start", "realtime_end"):
            if r.get(field) and date.fromisoformat(r[field]) > end:
                raise live_api.FredError("future_history_vintage")
        raw = str(r.get("value", "."))
        value = None if raw in ("", ".") else float(raw)
        if value is not None and not math.isfinite(value):
            raise live_api.FredError("invalid_history_value")
        points.append({"date": d.isoformat(), "value": value})
    return meta, sorted(points, key=lambda p: p["date"])


def evidence(body, runtime, suffix="json"):
    digest = hashlib.sha256(body).hexdigest()
    rel = "raw/" + digest + "." + suffix
    p = runtime / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        if p.read_bytes() != body:
            raise HistoryError("immutable_history_evidence_conflict")
    else:
        p.write_bytes(body)
    return {"raw_path": rel, "raw_sha256": digest}


def collect(runtime, live_root, checkpoint, *, client=None, clock=None):
    runtime = live_api._validate_output_root(runtime)
    checkpoint = live_api._validate_output_root(checkpoint)
    if runtime == checkpoint or runtime.is_relative_to(checkpoint) or checkpoint.is_relative_to(runtime):
        raise ValueError("history_state_and_runtime_must_differ")
    runtime.mkdir(parents=True, exist_ok=True)
    now = clock or (lambda: datetime.now(timezone.utc))
    end = live_api._utc(now).date()
    # Monthly FRED observations can be returned with the first day of the
    # intersecting month. Ask from that boundary and retain the native date.
    start = (end - timedelta(days=1096)).replace(day=1)
    previous = read(checkpoint / "latest.json")
    old = {s["series_id"]: s for s in previous.get("series", [])}
    specs = {s.series_id: s for s in live_api.load_catalog()}
    output, errors = [], []
    credential_error = False
    if client is None:
        try:
            key, _ = live_api.load_credential()
            client = live_api.FredClient(key)
        except Exception:
            credential_error = True

    def accept(row):
        prior = old.get(row["series_id"])
        # Revisions never acquire the economic date as their knowledge date.
        identity = ("points", "unit", "frequency", "source_url", "source_universe",
                    "native_period_basis", "sign_convention", "structural_break")
        if prior and all(prior.get(k) == row.get(k) for k in identity):
            source = checkpoint / prior["raw_path"]
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != prior["raw_sha256"]:
                raise ValueError("first_history_evidence_missing")
            target = runtime / prior["raw_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            for key in ("known_by", "raw_path", "raw_sha256", "source_retrieved_at"):
                if key in prior:
                    row[key] = prior[key]
        output.append(row)

    def fail(sid, error):
        code = str(error) if isinstance(error, (live_api.FredError, pboc.PBoCError, HistoryError,
                                               funding_structure.FundingError, intermediary.IntermediaryError,
                                               terminal_flows.TerminalError)) else type(error).__name__
        errors.append({"series_id": sid, "code": code})
        if sid in old:
            row = {**old[sid], "status": "retained", "checked_at": live_api._stamp(live_api._utc(now))}
            source = checkpoint / row["raw_path"]
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != row["raw_sha256"]:
                raise ValueError("retained_history_evidence_missing")
            target = runtime / row["raw_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            output.append(row)

    for sid in FRED_IDS:
        try:
            if credential_error:
                raise live_api.FredError("credential_unavailable")
            params = {"series_id": sid, "realtime_start": end.isoformat(), "realtime_end": end.isoformat()}
            metadata = client.request("series", params)
            payload = client.request("series/observations", {**params, "observation_start": start.isoformat(),
                                     "observation_end": end.isoformat(), "units": "lin", "output_type": 1,
                                     "sort_order": "asc", "limit": 2000})
            captured = live_api._stamp(live_api._utc(now))
            meta, points = validate_fred(specs[sid], metadata, payload, start, end)
            proof = evidence(live_api._json_bytes({"metadata": metadata, "response": payload, "captured_at": captured}), runtime)
            last = max(p["date"] for p in points if p["value"] is not None)
            stale = (end - date.fromisoformat(last)).days > specs[sid].stale_after_days
            accept({"series_id": sid, "label": LABELS[sid], "provider": "fred", "points": points,
                    "unit": meta["units"], "frequency": meta["frequency"], "known_by": captured,
                    "checked_at": captured, "source_url": "https://fred.stlouisfed.org/series/" + sid,
                    "status": "stale" if stale else "ok", "chart_type": "line", "track": "equity",
                    "history_basis": "current_vintage_3_years", "research_eligible": False, **proof})
        except Exception as e:
            fail(sid, e)

    pboc_state = read(Path(live_root) / "pboc/latest.json")
    rows = {o["series_id"]: o for o in pboc_state.get("observations", [])}
    for sid, parser in PBOC_PARSERS.items():
        try:
            obs = rows.get(sid)
            if not obs:
                raise pboc.PBoCError("current_pboc_source_unavailable")
            # Latest-month value stability does not imply older cells have not
            # been revised. Use this run's actual table, not the last-good row.
            proofs = [s for s in pboc_state.get("sources", []) if s.get("source_url") == obs["source_url"]]
            if len(proofs) != 1:
                raise pboc.PBoCError("fresh_pboc_history_source_unavailable")
            receipt = proofs[0]
            rel = Path(receipt["raw_path"])
            if rel.is_absolute() or ".." in rel.parts or rel.parts[0] != "raw":
                raise pboc.PBoCError("invalid_pboc_evidence_path")
            body = (Path(live_root) / "pboc" / rel).read_bytes()
            if hashlib.sha256(body).hexdigest() != receipt["source_sha256"]:
                raise pboc.PBoCError("pboc_evidence_hash_mismatch")
            matches = [r for r in parser(pboc._decode(body)) if r[0] == sid]
            if len(matches) != 1:
                raise pboc.PBoCError("pboc_history_identity")
            _, _, _, values, units, _, kind = matches[0]
            if units != obs["unit"] or len(values) > 36:
                raise pboc.PBoCError("pboc_history_units_or_length")
            if any(date.fromisoformat(d) > end or not math.isfinite(v) for d, v in values.items()):
                raise pboc.PBoCError("pboc_history_future_or_invalid")
            captured = live_api._stamp(live_api._utc(now))
            proof = evidence(body, runtime, "html")
            accept({"series_id": sid, "label": LABELS[sid], "provider": "pboc",
                    "unit": units, "frequency": "Monthly", "points": [{"date": d, "value": v} for d, v in sorted(values.items())],
                    # Previously selected last-good timestamps did not certify
                    # every older cell. Record when the full table is parsed.
                    "known_by": captured, "checked_at": captured, "source_url": obs["source_url"],
                    "source_retrieved_at": receipt["known_by"], "status": "stale" if (end - date.fromisoformat(max(values))).days > 120 else "ok",
                    "chart_type": "bar" if kind == "flow" else "line", "track": "equity",
                    "history_basis": "available_official_monthly_table", "research_eligible": False, **proof})
        except Exception as e:
            fail(sid, e)
    for sid in MONTHLY_SOURCES:
        try:
            row, body, suffix = cached_monthly_history(sid, live_root, start, live_api._utc(now))
            accept({**row, **evidence(body, runtime, suffix)})
        except Exception as e:
            fail(sid, e)
    run_id = os.environ.get("GITHUB_RUN_ID")
    public = {"schema_version": 1, "generated_at": live_api._stamp(live_api._utc(now)),
              "github_run_url": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run_id}" if run_id else None,
              "vintage_policy": "current_snapshot_not_historical_availability", "research_eligible": False,
              "series": output, "errors": errors,
              "assumptions": ["각 계열의 원단위·원빈도를 유지합니다. 현재 빈티지를 과거에 알았다고 가정하지 않습니다.",
                              "스톡의 수준은 원자료 표시이며 잠재 유동성 점수나 회귀 입력이 아닙니다."],
              "breaks": ["자료 개정·공표 지연·이력 부족이 남습니다. 국가 간 합산·빈도 늘리기·결측 보간을 하지 않습니다."]}
    live_api._atomic_json(runtime / "latest.json", public)
    staging = checkpoint.with_name(checkpoint.name + ".candidate")
    if staging.exists():
        raise ValueError("history_candidate_exists")
    staging.mkdir(parents=True)
    for row in output:
        source = runtime / row["raw_path"]
        target = staging / row["raw_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    live_api._atomic_json(staging / "latest.json", public)
    backup = checkpoint.with_name(checkpoint.name + ".previous")
    if backup.exists():
        raise ValueError("history_backup_exists")
    if checkpoint.exists():
        checkpoint.rename(backup)
    try:
        staging.rename(checkpoint)
    except BaseException:
        if backup.exists():
            backup.rename(checkpoint)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return public


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--live-root", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--public-output", type=Path, required=True)
    args = p.parse_args()
    result = collect(args.output_root, args.live_root, args.checkpoint)
    public = {**result, "series": [{k: v for k, v in s.items() if k != "raw_path"} for s in result["series"]]}
    live_api._atomic_json(args.public_output, public)
    print(json.dumps({"chart_series": len(result["series"]), "errors": result["errors"]}))


if __name__ == "__main__":
    main()
