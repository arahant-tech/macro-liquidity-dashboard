# 크립토 현재 관측 연결

이 수집기는 5층 크립토 자료만 별도로 축적한다. 주식 회귀, 유동성 매매 신호, 역사적 가격 자료를 만들지 않는다. 공개 읽기 전용 API 세 개를 사용하며 API 키가 필요하지 않다.

| 관측 | 고정 원천 | 사용하는 값 | 단위와 해석 |
|---|---|---|---|
| USDT·USDC 유통량 | DefiLlama `/stablecoins?includePrices=false` | ID `1`=USDT, `2`=USDC의 `circulating.peggedUSD` | 각 토큰 수량. 가격을 곱한 시가총액과 구분 |
| 순발행 대리변수 | 실제로 저장한 호환 유통량 스냅숏 두 개 | 이번 유통량 − 직전 유통량 | 두 **수집시각 사이** 순유통량 변화. 일간 발행량으로 표시하지 않음 |
| 확정 펀딩 | OKX `/api/v5/public/funding-rate-history`, `limit=1` | `realizedRate`, `fundingTime` | 해당 정산의 소수 단위 금리. 예상 `fundingRate`로 대체하지 않음 |
| 미결제약정 | OKX `/api/v5/public/open-interest` | `oi`, `oiCcy`, `oiUsd` | 각각 계약 수, BTC 수량, USD 명목금액. 서로 대체하거나 합산하지 않음 |

거래소와 계약은 **OKX / `BTC-USDT-SWAP`**으로 고정했다. 원천은 [DefiLlama 공식 API 문서](https://api-docs.defillama.com/), [DefiLlama 유통량 어댑터 설명](https://github.com/DefiLlama/peggedassets-server#pegged-asset-adapters), [OKX 공식 API 문서](https://www.okx.com/docs-v5)로 확인했다. DefiLlama 어댑터는 발행 수량과 미유통 준비금, 브리지 항목을 구분하지만 여기서 얻는 유통량은 **제3자 집계 대리변수**다. 발행사의 감사된 원장이라고 주장하지 않는다.

## 시간과 순발행 규율

`known_by`와 `retrieved_at`은 HTTP 응답 수집이 끝난 실제 UTC 시각이다. 원래의 발표일을 추정해서 붙이지 않는다. DefiLlama 응답에는 해당 유통량의 관측시각이 없으므로 `observed_at=null`, `upstream_observation_time_unknown=true`로 저장한다. HTTP `Date`, `Age`, `Last-Modified`, `ETag`가 있으면 별도 보관한다. 이 헤더들도 경제적 관측시각을 확정하지 않는다.

첫 수집에는 순발행 값이 없다. 실제 저장된 이전 스냅숏의 해시, 자산 구성, 원천, 계산 방법, 단위가 일치하고 수집시각이 증가할 때만 차이를 계산한다. `period_start`, `period_end`, `elapsed_capture_seconds`와 두 스냅숏의 경로·해시를 남긴다. 공급량이 동일하면 그 수집 구간의 **관측된 변화가 0**이라는 뜻이다. 원천의 업데이트 지연 때문에 실제 신규 발행이 없었다는 뜻은 아니다.

`price`, `circulatingUSD`, `circulatingPrevDay` 같은 가격·과거 비교 필드와 발행 승인 수량은 계산에 쓰지 않는다. USDT+USDC 합계는 두 유통 수량의 명목 합계이며 실제 시장가격으로 평가한 USD 자금 유입량이 아니다.

OKX의 OI `ts`는 거래소가 보고한 데이터 반환 시각이다. 거래소 시계가 수집기보다 5초 이내로 앞서는 경우 `source_reported_at`과 `source_clock_ahead_seconds`를 남기고 `observed_at`은 미상으로 처리한다. 미래 관측이라고 표현하지 않는다. 5초 초과 시차, 30분 초과의 오래된 OI, 미래 펀딩 정산, 36시간 초과의 오래된 펀딩은 거부한다. 펀딩 주기는 한 관측만으로 고정할 수 없어 연율화하지 않는다.

## 호출과 저장

```python
from model.providers.crypto import collect

result = collect("data/live/extensions/crypto")
```

테스트에서는 `transport(url)`과 시간대가 있는 `datetime`을 반환하는 `clock()`을 주입한다. 반환값은 `provider='crypto'`, `status='ok'|'partial'|'error'`, `observations`, `errors`, `sources`, `limitations`, `assumptions`를 포함한다. 각 관측은 `track='crypto'`, `layer=5`, `research_eligible=False`다. `status='ok'`는 지정한 API 수집이 성공했다는 뜻이며, 크립토 측정층 전체의 자료가 완비됐다는 뜻이 아니다.

원본 HTTP JSON 바이트는 `raw/<sha256>.json`에 내용 해시로 중복 제거해 저장한다. `captures/`는 개별 실행 기록이며, `stablecoin_snapshots/`는 공급량 계산의 증거다. `stablecoin_previous.json`은 다음 수집의 비교 기준을 가리킨다. 다른 원천이 실패해도 성공한 관측은 반환하며, 집계기의 `latest.json`이나 직전 정상값을 덮어쓰지 않는다.

클라우드에서 원본은 실행 아티팩트로 보관하고 웹 화면에는 관측 요약을 게시한다. 보관 파일을 정리할 때 다음 계산에 필요한 `stablecoin_previous.json`과 그 파일이 가리키는 `stablecoin_snapshots/` 기록은 유지해야 한다. 원본을 별도 아카이브로 옮길 경우 해시와 보관 경로를 추적해야 한다.

HTTP 403·451, 리다이렉트, 네트워크 실패는 오류로 기록한다. 다른 국가 호스트·거래소·프록시로 자동 전환하지 않는다. 로컬에서 세 API의 응답과 실제 수집을 확인했으며, **GitHub 실행기의 접근 성공 여부는 그 실행기에서 별도로 확인해야 한다.**

## 가정과 깨지는 지점

- **가정:** 집계 원천의 USDT·USDC 유통량 정의가 두 수집 사이 일관되고, OKX가 해당 계약의 펀딩과 OI를 올바른 단위로 보고한다.
- **파손 지점:** 브리지 중복, 준비금 제외 방식 변경, 공급량 개정, 원천 캐시, 거래소 보고 오류와 접근 제한이 실제 유동성 변화처럼 보일 수 있다. 순발행과 크립토 가격 방향의 인과·예측 관계는 이 연결로 검증되지 않는다.
- **별도 연결:** BTC·ETH 현물 ETF 순유입은 [Farside 수집기](ETF_FLOWS_SOURCE.md), CLSK·MARA의 직접 공시 BTC 매도량은 [채굴사 수집기](MINER_FLOWS_SOURCE.md)가 담당한다. 이 문서의 공급·레버리지 수집기와 별도 트랙이며, ETF 발행사의 실제 결제현금 독립 검증 및 시장 전체의 현재 온체인 매도압은 아직 확보하지 못했다. DefiLlama 유료 ETF API를 구매·구독하지 않는다.

검증 명령: `.venv-v9/bin/python -m unittest tests.test_crypto_provider`. 합성 테스트는 승인 발행량·가격 변화의 배제, 실제 두 스냅숏의 차분, 단위 구별, 발표시각·해시, 지역 거부, 부분 실패, 오래된 관측, 시계 역행을 확인한다.
