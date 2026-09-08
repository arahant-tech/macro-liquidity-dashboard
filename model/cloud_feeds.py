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
from model.providers import pboc, buybacks, issuer_buybacks, crypto, etf_flows, miner_flows
from model.providers import funding_structure, intermediary, terminal_flows, offshore, market_buybacks

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {"fred": "FRED · 중앙은행·은행·분포", "pboc": "PBoC · 중국 공식 통계",
             "buybacks": "SEC 직접 API · 보조 경로", "issuer_buybacks": "기업 공식 공시 · 자사주 5개사",
             "crypto": "크립토 · 공급·레버리지", "etf_flows": "현물 ETF · BTC·ETH 순유입",
             "miner_flows": "채굴사 공시 · 실제 BTC 매도",
             "funding_structure": "분포 · ACM·ECB HQLA",
             "intermediary": "중개기관 · 딜러·FINRA·SLR",
             "terminal_flows": "주식 플로우 · TIC·순발행·ICI",
             "offshore": "BIS 역외 달러 · OFR 레포",
             "market_buybacks": "S&P 500 · 분기 자사주 집계"}
TOTALS = {"pboc": 4, "buybacks": 5, "issuer_buybacks": 5, "crypto": 10,
          "etf_flows": 2, "miner_flows": 2, "funding_structure": 12,
          "intermediary": 10, "terminal_flows": 8, "offshore": 5, "market_buybacks": 1}
EXTENSIONS = {"funding_structure", "intermediary", "terminal_flows", "offshore", "market_buybacks"}
API_ACCESS = {
    "fred": {"method": "official_api", "authentication": "existing_github_secret"},
    "pboc": {"method": "official_public_tables", "authentication": "none"},
    "buybacks": {"method": "official_api", "authentication": "contact_user_agent_github_secret"},
    "issuer_buybacks": {"method": "official_public_filings", "authentication": "none"},
    "crypto": {"method": "public_apis", "authentication": "none"},
    "etf_flows": {"method": "public_reported_tables", "authentication": "none"},
    "miner_flows": {"method": "official_public_disclosures", "authentication": "none"},
    "funding_structure": {"method": "ecb_api_and_nyfed_csv", "authentication": "none"},
    "intermediary": {"method": "nyfed_api_and_official_public_tables", "authentication": "none"},
    "terminal_flows": {"method": "fred_api_treasury_text_ici_public_tables", "authentication": "existing_fred_secret_only"},
    "offshore": {"method": "bis_and_ofr_official_apis", "authentication": "none"},
    "market_buybacks": {"method": "sp_dow_jones_official_public_disclosures", "authentication": "none"},
}
ASSUMPTIONS = [
    "API 확인 간격은 1시간입니다. 거시통계 값은 기관의 발표·개정 때 바뀝니다.",
    "표의 레벨은 원자료 관측값이며 잠재 유동성 상태·회귀 입력·진입 신호가 아닙니다.",
    "known_by는 실제 수집 완료 시각입니다. 최초 발표시각이나 역사적 빈티지를 인증하지 않습니다.",
    "자사주는 고정 5개사의 SEC 현금 지급 공시입니다. 공시별 YTD·연간 기간을 유지하며 시장 전체로 합산하지 않습니다.",
    "기업 자체 공시의 실제 자사주 현금 지급을 고정 5개사에서 별도 관측합니다. 동일 기업의 SEC 관측과 합산하지 않습니다.",
    "BTC·ETH 현물 ETF는 Farside의 완결된 일별 순유입 보고입니다. AUM 변화로 추정하지 않습니다.",
    "채굴사 매도량은 CLSK·MARA의 명시적 공시입니다. 생산량·보유량 차이를 매도로 대체하지 않습니다.",
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
    "2층 분포: EURUSD·USDJPY 3개월 통화 베이시스의 무료 지속 공급원 미확보. HQLA는 ECB 감독대상 은행 범위이며 세계 전체가 아님",
    "3층 전환 연산자: 딜러 포지션·레포와 FINRA는 관측 자료. SLR은 JPM 1개사 비율이며 전체 여유한도 미측정. 패시브 비중은 ICI 접근 상태 확인 필요; A_t 미추정",
    "4층 종단 플로우: ICI 주식 펀드·ETF 흐름은 접근 상태 확인 필요. Z.1 분기 ETF·펀드 주식자산 거래는 주간 가입유입과 다른 관측. TIC·순발행과 중복 가능; 기업별 자사주는 5개사, 별도 S&P 500 집계는 공표기간의 갱신 지연 확인 필요",
    "5층 범위: ETF는 Farside 보고 집계, 채굴사는 CLSK·MARA 공시 2개사; 전체 온체인 채굴자 매도압은 미측정",
    "BIS GLI의 비미국 비은행 달러신용과 은행대출 구성은 LBS 전 범위를 대체하지 않음. OFR 레포 잔액·거래량은 담보 재사용률이 아니며 재사용률 미측정",
    "L_t 미추정: 엔 캐리·담보 등 상태 블록의 관측, 역사적 발표일·빈티지와 훈련 표본이 부족. 현재 수집치는 과거 연구에 소급 사용하지 않음",
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
              "source_reported_at", "source_quality", "original_release_at", "published_date",
              "methodology", "method", "components", "universe", "fund_universe", "source_universe", "covered_funds", "discovery_status",
              "discovery_mode", "duration_months", "measurement_kind", "raw_scope", "native_period_basis",
              "not_additive_with", "aggregation_allowed", "source_page", "acquisition_route",
              "source_series_id", "native_series_id", "source_label", "scope", "seriesbreak",
              "seasonal_adjustment", "sign_convention", "source_status", "source_publisher")
    row = {key: item.get(key) for key in fields}
    row.update(provider=provider, status=status, research_eligible=False,
               label=item.get("label") or item.get("meaning") or item.get("series_id"),
               unit=item.get("unit") or item.get("units"),
               track=item.get("track", "equity"), notes=[])
    row["raw_sha256"] = item.get("raw_sha256") or item.get("source_sha256")
    if provider in {"buybacks", "issuer_buybacks"}:
        row["label"] = f"{item.get('ticker', '')} · 실제 현금 자사주 매입"
        row["notes"].append("공시의 YTD·연간 누적 현금 지급; 다른 기업·기간과 합산 금지" if provider == "buybacks"
                             else "발행자 직접 공시의 분기·YTD 원기간 유지; SEC의 같은 회사 관측과 중복 합산 금지")
        if item.get("discovery_status") and item["discovery_status"] != "automatic":
            row["notes"].append("새 공시 자동 발견 미확인: 등록된 공식 문서의 수집 상태와 구분")
    if provider == "etf_flows":
        row["notes"].append("Farside의 펀드별 보고 합계; 완결된 날짜만 사용, 결제현금 독립 인증 아님")
    if provider == "miner_flows":
        row["label"] = f"{item.get('ticker', '')} · 공시된 실제 BTC 매도량"
        row["notes"].append("공시한 원기간 동안의 실현 매도; 전체 채굴자·현재 거래소 매도압이 아님")
        if item.get("components"):
            row["notes"].append("공시 구성: " + "; ".join(f"{k}: {v} BTC" for k, v in item["components"].items()))
    if provider == "pboc":
        row["notes"].append("공식 월간 HTML 통계표; 원단위 유지")
    if provider == "funding_structure":
        row["notes"].append("ACM 월말·ECB 감독대상 은행 분기 자료; 분포 관측이며 세계 HQLA나 상태 추정치가 아님")
    if provider == "intermediary":
        row["notes"].append("포지션·마진부채·기관별 비율은 원자료. 딜러 여유한도나 추정 A_t로 해석하지 않음")
    if provider == "terminal_flows":
        row["notes"].append("TIC 거주지 기준·Z.1 비금융기업·ICI 투자범위 구분; 원기간 유지, 자사주와 중복 합산 금지")
    if provider == "market_buybacks":
        row["notes"].append("S&P DJI가 직접 보고한 S&P 500 분기 자사주 매입; 기업별 관측·Z.1과 중복 합산 금지. 관측기간의 갱신 지연을 확인")
    if provider == "offshore":
        row["notes"].append("BIS 총액과 대출·채권 구성은 중복 합산 금지. OFR 잔액·거래량은 담보 재사용률이 아님")
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
        state = item.get("status") if item.get("status") in {"ok", "partial", "stale"} else "ok"
        if provider == "buybacks":
            state = next((s["status"] for s in fresh.get("issuer_status", []) if s["ticker"] == item.get("ticker")), "ok")
        if provider == "pboc" and item.get("period_end"):
            if (now.date() - datetime.fromisoformat(item["period_end"]).date()).days > 120:
                state = "stale"
        if provider in {"issuer_buybacks", "miner_flows", "etf_flows"} and item.get("period_end"):
            age = (now.date() - datetime.fromisoformat(item["period_end"]).date()).days
            limit = 10 if provider == "etf_flows" else 75 if item.get("frequency") == "monthly" else 180
            if age > limit:
                state = "stale"
        if provider in EXTENSIONS and item.get("period_end"):
            age = (now.date() - datetime.fromisoformat(item["period_end"]).date()).days
            limit = {"daily": 10, "weekly": 21, "monthly": 100, "quarterly": 210}.get(str(item.get("frequency", "")).lower(), 210)
            if age > limit:
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
                                "issuer_buybacks": issuer_buybacks.collect, "crypto": crypto.collect,
                                "etf_flows": etf_flows.collect, "miner_flows": miner_flows.collect,
                                "funding_structure": funding_structure.collect, "intermediary": intermediary.collect,
                                "terminal_flows": terminal_flows.collect, "offshore": offshore.collect,
                                "market_buybacks": market_buybacks.collect}
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
                        if name in ({"pboc", "issuer_buybacks", "etf_flows", "miner_flows"} | EXTENSIONS) and old and all(old.get(key) == item.get(key) for key in (
                            "value", "unit", "units", "observation_date", "period_start", "period_end", "frequency", "source_url", "methodology", "components", "universe", "fund_universe", "source_universe", "measurement_kind", "scope")):
                            evidence_keys = {"known_by", "retrieved_at", "raw_path", "raw_sha256", "source_sha256", "requested_url", "request_url"}
                            evidence_keys.update(key for key in set(old) | set(item)
                                                 if key.endswith(("_raw_path", "_raw_sha256")))
                            for key in evidence_keys:
                                if key in old:
                                    item[key] = old[key]
                                else:
                                    item.pop(key, None)
                        receipts.pop(item["series_id"], None)
                    result["retained_observations"] = list(receipts.values())
                    live_api._atomic_json(target / "latest.json", result)
                provider_rows = merge_rows(name, result, old_rows, now())
                status = result.get("status", "error")
                success = result.get("success", len(result.get("observations", [])))
                total = TOTALS[name]
                errors = [{**{k: e[k] for k in ("source", "ticker", "series_id", "series_ids", "source_group") if k in e},
                           "code": e.get("code") or e.get("error") or "source_error"}
                          if isinstance(e, dict) else {"code": "source_error"}
                          for e in list(result.get("errors", [])) + list(result.get("discovery_warnings", []))]
                if result.get("discovery_warnings") and status == "ok":
                    status = "partial"
            if any(r["status"] == "stale" for r in provider_rows) and status == "ok":
                status = "partial"
            rows.extend(provider_rows)
            providers.append({"provider": name, "label": PROVIDERS[name], "status": status,
                              "success": success, "total": total, "errors": errors,
                              "checked_at": live_api._stamp(now()),
                              "last_success_at": live_api._stamp(now()) if status == "ok" else old_providers.get(name, {}).get("last_success_at")})
        run_id = os.environ.get("GITHUB_RUN_ID")
        # Successful API access does not mean the API contains the newest
        # issuer filing. Compare dates only; differing durations are not summed.
        issuer_periods = {r.get("ticker"): r.get("period_end") for r in rows
                          if r["provider"] == "issuer_buybacks" and r.get("value") is not None}
        lagging = []
        for row in rows:
            peer_end = issuer_periods.get(row.get("ticker"))
            if (row["provider"] == "buybacks" and row.get("period_end") and peer_end
                    and row["period_end"] < peer_end and row["status"] != "retained"):
                row["status"] = "stale"
                row["notes"].append("기업 직접 공시에 더 최신 기간이 있음: " + peer_end)
                lagging.append({"ticker": row.get("ticker"), "code": "newer_issuer_period_available"})
        if lagging:
            for provider in providers:
                if provider["provider"] == "buybacks":
                    provider["status"] = "partial"
                    provider["errors"].extend(lagging)
                    provider["last_success_at"] = old_providers.get("buybacks", {}).get("last_success_at")
        run_url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run_id}" if run_id else None
        public = {"schema_version": 1, "generated_at": live_api._stamp(now()), "github_run_url": run_url,
                  "research_eligible": False, "schedule_minutes": 60,
                  "providers": providers, "observations": rows, "missing_coverage": MISSING,
                  "api_access": {key: API_ACCESS[key] for key in collectors},
                  "model_readiness": {"latent_state": "not_estimated", "operator": "not_estimated",
                       "reason": "native current captures do not supply historical release vintages, full state blocks or a validated training sample",
                       "scalar_global_liquidity": False, "historical_research_eligible": False,
                       "validation_axes": {"internal": "not_evaluated", "construct": "not_evaluated",
                                           "external": "not_evaluated", "researcher_freedom": "not_evaluated"},
                       "kill_criteria": "not_evaluated"},
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
