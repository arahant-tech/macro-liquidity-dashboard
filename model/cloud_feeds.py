"""Forward observation collection for hourly GitHub Actions; never fits a model.

Cloud state contains the latest source receipts and the two actual supply
captures required for changes. Full per-run receipts are retained as Actions
artifacts. No research or sealed outcome file is opened by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from datetime import datetime, timezone

from model import live_api
from model.providers import pboc, buybacks, issuer_buybacks, crypto

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {"fred": "FRED · 중앙은행·은행·분포", "pboc": "PBoC · 중국 공식 통계",
             "buybacks": "SEC · 실제 자사주 매입", "issuer_buybacks": "Microsoft IR · 실제 자사주",
             "crypto": "크립토 · 별도 트랙"}
ASSUMPTIONS = [
    "API 확인 간격은 1시간입니다. 거시통계 값은 기관의 발표·개정 때 바뀝니다.",
    "표의 레벨은 원자료 관측값이며 잠재 유동성 상태·회귀 입력·진입 신호가 아닙니다.",
    "known_by는 실제 수집 완료 시각입니다. 최초 발표시각이나 역사적 빈티지를 인증하지 않습니다.",
    "자사주는 고정 5개사의 SEC 현금 지급 공시입니다. 공시별 YTD·연간 기간을 유지하며 시장 전체로 합산하지 않습니다.",
    "Microsoft 자체 IR의 직접 분기 현금 지급을 별도 관측합니다. 동일 기업의 SEC 관측과 합산하지 않습니다.",
    "스테이블코인 변화는 실제 수집 두 시점 사이 유통량 차이입니다. 일간 순발행·BTC 순유입으로 해석하지 않습니다.",
]
BREAKS = [
    "소스 변경·개정·접근 제한·GitHub 실행 지연이 발생할 수 있습니다. 오류 시 과거 정상값과 그 수집시각을 유지합니다.",
    "자료를 모은 것만으로 측정모형의 타당성이 입증되지 않습니다. 계수·OOS·꼬리·kill criteria는 아직 이 수집기로 평가하지 않았습니다.",
    "GitHub 일정은 정시 실행을 보장하지 않으며 저장소·Actions·API 권한이 유지되어야 합니다.",
    "크립토 유통량은 DefiLlama 대리 관측, 파생상품은 OKX 단일 계약입니다. 공급자 캐시 시각과 시장 전체 대표성에는 한계가 있습니다.",
    "라이브 자료는 연구 표본과 분리합니다. 과거 컷오프·봉인 홀드아웃을 소급 갱신하지 않습니다.",
]
MISSING = [
    "2층 분포: 통화 베이시스·ACM·HQLA 미연결",
    "3층 전환 연산자: 딜러 여력·SLR·FINRA·패시브 비중 미연결; A_t 미추정",
    "4층 종단 플로우: 주식 펀드/ETF·순발행·TIC 미연결; 자사주는 고정 5개사로 범위 제한",
    "5층 크립토: 현물 ETF 순유입·채굴자 매도압 미연결",
    "BIS 역외 달러·담보 재사용 등 전체 블록을 채우지 못했으며 L_t 미추정",
]


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def normalize_observation(provider, item, *, status="ok"):
    # Explicit public allowlist. Source payloads and absolute runtime paths are
    # never serialized into the public page snapshot.
    fields = ("series_id", "value", "unit", "observation_date", "period_start", "period_end",
              "known_by", "source_url", "raw_sha256", "layer", "track", "block", "frequency",
              "elapsed_capture_seconds", "filed_date", "accession", "ticker", "observed_at",
              "source_reported_at", "source_quality", "original_release_at")
    row = {key: item.get(key) for key in fields}
    row.update(provider=provider, status=status, research_eligible=False,
               label=item.get("label") or item.get("meaning") or item.get("series_id"),
               unit=item.get("unit") or item.get("units"),
               track=item.get("track", "equity"), notes=[])
    row["raw_sha256"] = item.get("raw_sha256") or item.get("source_sha256")
    if provider in {"buybacks", "issuer_buybacks"}:
        row["label"] = f"{item.get('ticker', '')} · 실제 현금 자사주 매입"
        row["notes"].append("공시의 YTD·연간 누적 현금 지급; 다른 기업·기간과 합산 금지" if provider == "buybacks"
                             else "발행자 직접 공시의 분기 현금 지급; SEC의 같은 회사 관측과 중복 합산 금지")
    if provider == "pboc":
        row["notes"].append("공식 월간 HTML 통계표; 원단위 유지")
    if provider == "crypto":
        row["notes"].append("단일 공급자·계약; 주식과 별도 트랙")
        if item.get("upstream_observation_time_unknown"):
            row["notes"].append("공급자 원관측·캐시 시각 미확인")
        if item.get("source_reported_at") and not item.get("observed_at"):
            row["notes"].append("거래소 시계 오차; 경제적 관측시각 미확인")
        if item.get("elapsed_capture_seconds") is not None:
            row["notes"].append("수집 구간 변화량; 일간 값으로 환산하지 않음")
    if status == "retained":
        row["notes"].append("이번 수집 실패: 마지막 정상 관측 보존")
    return row


def valid_value(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (ValueError, TypeError):
        return False


def fred_rows(latest, catalog):
    rows = []
    for spec in catalog["series"]:
        entry = latest.get("series", {}).get(spec["series_id"], {})
        good = entry.get("last_good") or {}
        item = {**spec, **good, "label": spec["meaning"], "unit": good.get("source_units", spec["expected_units"]),
                "frequency": good.get("source_frequency", spec["expected_frequency"]), "track": "equity"}
        if spec.get("reference_period_end") and entry.get("freshness", {}).get("basis_date"):
            item.update(period_start=good.get("observation_date"), period_end=entry["freshness"]["basis_date"])
        rows.append(normalize_observation("fred", item, status=("retained" if good and entry.get("status") == "error"
                                                                  else entry.get("status", "error"))))
    derived = latest.get("derived", {}).get("SOFR_IORB", {})
    if derived.get("value") is not None:
        rows.append(normalize_observation("fred", {**derived, "series_id": "SOFR_IORB",
                    "label": "SOFR − IORB · 동일 관측일", "unit": "basis points", "layer": 1,
                    "known_by": derived.get("known_by"), "block": "fed_funding", "track": "equity"},
                    status=derived.get("status", "ok")))
    return rows


def merge_rows(provider, fresh, previous, now):
    """Failures cannot relabel an old receipt as newly available data."""
    merged = {r["series_id"]: normalize_observation(provider, r, status="retained")
              for r in previous if r["provider"] == provider and valid_value(r.get("value"))}
    for item in fresh.get("observations", []):
        if not valid_value(item.get("value")):
            continue
        state = "ok"
        if provider == "buybacks":
            state = next((s["status"] for s in fresh.get("issuer_status", []) if s["ticker"] == item.get("ticker")), "ok")
        if provider == "pboc" and item.get("period_end"):
            if (now.date() - datetime.fromisoformat(item["period_end"]).date()).days > 120:
                state = "stale"
        merged[item["series_id"]] = normalize_observation(provider, item, status=state)
    return list(merged.values())


def collect(output_root, *, collectors=None, clock=None):
    root = live_api._validate_output_root(Path(output_root))
    root.mkdir(parents=True, exist_ok=True)
    now = clock or (lambda: datetime.now(timezone.utc))
    previous = read_json(root / "public.json")
    old_rows = previous.get("observations", [])
    old_providers = {p["provider"]: p for p in previous.get("providers", [])}
    catalog = read_json(Path(live_api.__file__).with_name("live_catalog.json"))
    collectors = collectors or {"fred": live_api.poll, "pboc": pboc.collect, "buybacks": buybacks.collect,
                                "issuer_buybacks": issuer_buybacks.collect, "crypto": crypto.collect}
    rows, providers = [], []
    with live_api._poll_lock(root):
        for name, fn in collectors.items():
            target = root / name
            target.mkdir(parents=True, exist_ok=True)
            prior_receipt = read_json(target / "latest.json") if name != "fred" else {}
            collector_failed = False
            try:
                result = fn(output_root=target)
            except Exception as exc:
                collector_failed = True
                result = {"status": "error", "outcome": "error", "observations": [],
                          "errors": [{"code": "collector_" + type(exc).__name__}]}
            if name == "fred":
                provider_rows = fred_rows(read_json(target / "latest.json"), catalog)
                if collector_failed:
                    provider_rows = [normalize_observation("fred", r, status="retained" if valid_value(r.get("value")) else "error") for r in provider_rows]
                if not any(r["series_id"] == "SOFR_IORB" for r in provider_rows):
                    provider_rows += [normalize_observation("fred", r, status="retained") for r in old_rows
                                      if r["provider"] == "fred" and r["series_id"] == "SOFR_IORB" and valid_value(r.get("value"))]
                status = result.get("outcome", "error")
                success, total = result.get("success", 0), len(catalog["series"])
                errors = [{"code": r.get("error", "source_error"), "series_id": sid}
                          for sid, r in result.get("series", {}).items() if r.get("error")]
                if not errors and status == "error":
                    errors = [{"code": "fred_collection_failed"}]
            else:
                observations = result.get("observations", [])
                valid = [item for item in observations if valid_value(item.get("value"))]
                if len(valid) != len(observations):
                    result = {**result, "observations": valid,
                              "errors": list(result.get("errors", [])) + [{"code": "invalid_numeric_observation"}],
                              "status": "partial" if valid else "error", "success": len(valid)}
                # PBoC and crypto return a capture; the aggregator persists their
                # latest receipt. SEC also keeps an issuer-specific last-good map.
                if result.get("observations") is not None and name != "buybacks":
                    receipts = {o["series_id"]: o for o in prior_receipt.get("retained_observations", [])}
                    receipts.update({o["series_id"]: o for o in prior_receipt.get("observations", [])})
                    for index, item in enumerate(result.get("observations", [])):
                        old = receipts.get(item["series_id"], {})
                        if name in {"pboc", "issuer_buybacks"} and old and all(old.get(key) == item.get(key) for key in (
                            "value", "unit", "period_start", "period_end", "source_url")):
                            result["observations"][index] = old
                        receipts.pop(item["series_id"], None)
                    result["retained_observations"] = list(receipts.values())
                    live_api._atomic_json(target / "latest.json", result)
                provider_rows = merge_rows(name, result, old_rows, now())
                status = result.get("status", "error")
                success = result.get("success", len(result.get("observations", [])))
                total = {"pboc": 4, "buybacks": 5, "issuer_buybacks": 1, "crypto": 10}[name]
                errors = [{**{k: e[k] for k in ("source", "ticker") if k in e},
                           "code": e.get("code") or e.get("error") or "source_error"}
                          if isinstance(e, dict) else {"code": "source_error"}
                          for e in result.get("errors", [])]
            if any(r["status"] == "stale" for r in provider_rows) and status == "ok":
                status = "partial"
            rows.extend(provider_rows)
            providers.append({"provider": name, "label": PROVIDERS[name], "status": status,
                              "success": success, "total": total, "errors": errors,
                              "checked_at": live_api._stamp(now()),
                              "last_success_at": live_api._stamp(now()) if status == "ok" else old_providers.get(name, {}).get("last_success_at")})
        run_id = os.environ.get("GITHUB_RUN_ID")
        run_url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run_id}" if run_id else None
        public = {"schema_version": 1, "generated_at": live_api._stamp(now()), "github_run_url": run_url,
                  "research_eligible": False, "schedule_minutes": 60,
                  "providers": providers, "observations": rows, "missing_coverage": MISSING,
                  "assumptions": ASSUMPTIONS, "breaks": BREAKS}
        live_api._atomic_json(root / "public.json", public)
    return public


def _references(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and (key.endswith("_path") or key == "path"):
                path = Path(value)
                if not path.is_absolute() and ".." not in path.parts and path.parts and path.parts[0] in {"raw", "captures", "stablecoin_snapshots"}:
                    yield path
            yield from _references(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _references(item)


def save_checkpoint(runtime, destination):
    """Keep bounded latest receipts; older complete captures remain run artifacts.

    Traverse only approved state filenames and relative evidence paths. Never
    sweep a working tree, credential directory or research data into GitHub.
    """
    runtime, destination = Path(runtime).resolve(), Path(destination).resolve()
    if runtime == destination or runtime.is_relative_to(destination) or destination.is_relative_to(runtime):
        raise ValueError("checkpoint_and_runtime_must_be_separate")
    live_api._validate_output_root(destination)
    staging = destination.with_name(destination.name + ".candidate")
    if staging.exists():
        raise ValueError("checkpoint_candidate_already_exists")
    staging.mkdir(parents=True)
    try:
        if (runtime / "public.json").is_symlink():
            raise ValueError("unsafe_public_snapshot")
        shutil.copy2(runtime / "public.json", staging / "public.json")
        for name in PROVIDERS:
            source, target = runtime / name, staging / name
            target.mkdir()
            pending = [Path(n) for n in ("latest.json", "status.json", "stablecoin_previous.json") if (source / n).exists()]
            copied = set()
            while pending:
                relative = pending.pop()
                if relative in copied:
                    continue
                origin = source / relative
                if origin.is_symlink() or not origin.resolve().is_relative_to(source.resolve()) or not origin.is_file():
                    raise ValueError("checkpoint_evidence_missing_or_unsafe")
                dest = target / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, dest)
                copied.add(relative)
                if relative.suffix == ".json" and relative.parts[0] != "raw":
                    pending.extend(_references(read_json(origin)))
        backup = destination.with_name(destination.name + ".previous")
        if backup.exists():
            raise ValueError("checkpoint_backup_already_exists")
        had_previous = destination.exists()
        if had_previous:
            destination.rename(backup)
        try:
            staging.rename(destination)
        except BaseException:
            if had_previous:
                backup.rename(destination)
            raise
        if had_previous:
            shutil.rmtree(backup)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "cloud-live")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--public-output", type=Path)
    args = parser.parse_args()
    if args.checkpoint and args.checkpoint.exists():
        if args.output_root.exists():
            raise SystemExit("Cloud runtime must be fresh before restoring a checkpoint")
        shutil.copytree(args.checkpoint, args.output_root)
    public = collect(args.output_root)
    if args.checkpoint:
        save_checkpoint(args.output_root, args.checkpoint)
    if args.public_output:
        live_api._atomic_json(args.public_output, public)
    print(json.dumps({"generated_at": public["generated_at"], "providers": [
        {k: p[k] for k in ("provider", "status", "success", "total")} for p in public["providers"]]}))


if __name__ == "__main__":
    main()
