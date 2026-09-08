# BIS 역외 달러 신용 · OFR 레포 원자료

## 1. 무엇을 검증했는가

BIS의 비미국 비은행 USD 신용과 OFR의 정의가 다른 두 레포 관측을 공개 API로 수집하면서 단위·차주·빈도·빈티지를 보존할 수 있는지 검증했다. 담보 재사용 회전율이나 잠재 유동성을 검증한 결과는 아니다.

## 2. 스펙

`model/providers/offshore.py`의 `collect(output_root, transport=None, clock=None)`는 `total=5`, `success`, `observations`, `sources`, `errors`, `assumptions`, `limitations`를 반환한다. `funding_structure.py`의 날짜·유한수 검증 도우미를 공유한다. 의존성은 `certifi==2026.7.22`; HTTPS 인증서를 검증한다. 두 원천 모두 API 키·가입이 필요 없다.

### BIS GLI

[BIS Global Liquidity Indicators](https://data.bis.org/topics/GLI)의 공식 SDMX API를 사용한다.

```
GET https://stats.bis.org/api/v1/data/WS_GLI/Q.USD.3P.N..I..USD/all?lastNObservations=1
차원 순서: FREQ.CURR_DENOM.BORROWERS_CTY.BORROWERS_SECTOR.
           LENDERS_SECTOR.L_POS_TYPE.L_INSTR.UNIT_MEASURE
```

| 정확한 키 | 의미 | 내부 series_id |
|---|---|---|
| Q.USD.3P.N.A.I.B.USD | 비미국 거주 비은행 USD 신용 합계 | BIS_GLI_USD_NONUS_TOTAL |
| Q.USD.3P.N.B.I.G.USD | 위 신용 중 은행대출 | BIS_GLI_USD_NONUS_BANK_LOANS |
| Q.USD.3P.N.A.I.D.USD | 위 신용 중 국제채권 | BIS_GLI_USD_NONUS_DEBT_SECURITIES |

모두 분기말 잔액, `UNIT_MULT=6`, **million USD**다. 차주 `N`(비은행)을 `P`(비금융 차주)나 전체 은행 부문으로 바꾸지 않는다. 모든 응답의 차원, BIS/WS_GLI dataflow, 제목, 단위, 공개 정상 상태를 검사한다.

은행대출 성분은 LBS를 주 원천으로 삼는 GLI 관측이고, 국제채권 성분은 IDS를 기반으로 한다. 이 연결을 원 LBS의 전체 국가·통화·차주 교차행렬을 채운 것으로 표시하지 않는다. 세 값이 같은 분기이며 합계=은행대출+국제채권인지 0.005 million USD 허용오차로 **공시 정합성만** 검사한다. 합계에 두 구성요소를 다시 더하지 않는다.

### OFR 레포

[OFR 공개 API](https://www.financialresearch.gov/short-term-funding-monitor/api/)의 [multifull](https://www.financialresearch.gov/short-term-funding-monitor/api-specs/api-full-multi/)에 두 mnemonic을 전달한다. 매번 최근 45일 범위만 요청하고 원 빈도의 `aggregation`과 `disclosure_edits`를 함께 받는다. 결측 삭제·월별 변환 옵션은 사용하지 않는다.

| mnemonic | 관측 정의 | 내부 series_id |
|---|---|---|
| REPO-DVP_OV_TOT-P | FICC DVP Service의 전체 레포 **잔액** | OFR_REPO_DVP_OUTSTANDING |
| REPO-TRIV1_TV_TOT-P | Fed 거래를 제외한 3자 레포 중 해당 일에 시작한 **신규 총거래액** | OFR_REPO_TRIPARTY_TRANSACTION_VOLUME |

원 단위는 USD, `magnitude=0`, 빈도는 일별, `Preliminary`다. DVP는 `role=stock`, 3자 레포는 `role=funding_activity`이며 서로 더하지 않는다. 신규 총거래액은 순플로우·잔액·담보 재사용 회전율과 다르다. 두 항목 모두 `collateral_reuse_measured=false`다.

OFR의 `source_url`은 날짜가 변하지 않는 `series/full?mnemonic=...` 개별 계열 주소다. 실제 내려받은 최근 45일 multifull 주소는 `requested_url` 및 원천 영수증에 별도로 남긴다. 따라서 조회일이 바뀌어 요청 범위가 움직여도 동일 관측의 최초 `known_by`가 초기화되지 않는다. 원문 하나를 공유하는 경우 각 관측의 정확한 mnemonic도 남긴다.

### 발표시각·보관 규율

원문은 SHA-256 이름의 불변 XML/JSON으로 보관한다. 값의 `known_by`는 실제 응답 수집 UTC 시각이며 `original_release_at=null`, `release_timestamp_verified=false`, `research_eligible=false`다. BIS XML의 `Prepared`는 응답 생성시각이므로 최초 통계 발표일로 사용하지 않는다. OFR `last_update`는 시간대 없는 문자열이므로 그대로 `source_last_update_unzoned`로만 남기며 UTC로 가정하지 않는다.

이전 정상 관측의 보존은 상위 수집기가 담당한다. OFR의 가장 최근 기준일이 결측·공개제한이면 마지막 유효 관측을 partial로 표시하고 실제 원 기준일을 남긴다. 미래 기준일·중복 날짜·NaN·단위 변경·차주 변경을 실패 처리한다. 분기값을 월별로 반복하지 않는다. BIS 기준일 210일, OFR 10일 초과 시 stale로 표시하며 이는 운영 기준이지 공표시차 추정치가 아니다.

## 3. 결과

2026-09-08 UTC 실제 API 호출에서 두 원천 모두 HTTP 200, **5/5**, 오류 0개를 확인했다. 로컬 검증 20개가 통과했다. 클라우드 배포 결과는 상위 배포 보고서를 따른다.

| 항목 | 기준일 | 원 단위 값 |
|---|---|---:|
| 비미국 비은행 USD 신용 합계 | 2026-03-31 | 14,742,816.992 million USD |
| 은행대출 | 2026-03-31 | 6,683,005.559 million USD |
| 국제채권 | 2026-03-31 | 8,059,811.433 million USD |
| FICC DVP 레포 잔액 | 2026-09-04 | 3,385,261,200,166.13 USD |
| Fed 제외 3자 레포 신규 총거래액 | 2026-09-03 | 2,312,814,744,535.38 USD |

계수·표준오차는 추정하지 않았다. 축1의 수집 무결성 일부(차원·단위·결측·미래값·해시·동일 관측 최초시각)만 확인했다. 실제 상위 클라우드 수집기로 조회일이 하루 이동한 두 실행을 모의해 요청 URL이 달라져도 동일 OFR 값의 최초 `known_by`와 원문 해시가 유지됨을 검사했다. 축1의 인자 안정성, 축2~4 모형 평가는 미실행이다.

## 4. Kill criteria

모형의 여섯 Kill criteria는 모두 **미평가**다. API 성공은 모형 통과가 아니다. 응답 구조·범위·단위가 바뀌거나 BIS 합계가 두 성분과 맞지 않으면 새 값을 실패 처리한다. OFR의 결측을 0으로 바꾸거나 새로운 거래가 없었다고 단정하지 않는다.

## 5. 가정과 깨지는 곳

- **가정:** BIS GLI의 정의와 보고 범위가 유지된다. **깨지는 곳:** 표본·기초 LBS/IDS 개정과 장기 발표시차가 현재 자금상황과 다를 수 있다. 비미국 비은행 신용은 전체 역외 달러의 일부다.
- **가정:** OFR의 분절된 레포 관측이 담보금융 활동을 보여준다. **깨지는 곳:** DVP와 3자 레포 범위가 다르고 총거래액은 회전·중복 계약을 포함할 수 있다. 이 값으로 담보 재사용률이나 딜러의 추가 중개 여력을 직접 추정할 수 없다.
- **가정:** 실제 수집 시점부터 현재 빈티지를 기록한다. **깨지는 곳:** 이전 발표 빈티지·정확한 최초 발표시각은 확보되지 않았다. 과거 백테스트에 현재 값을 소급 투입하면 미래참조다.
- **모형 제한:** 통화 베이시스, 전체 LBS 교차구조, 담보 재사용, A_t와 L_t는 이 수집기에서 추정하지 않는다. raw 레벨을 보여주는 것은 정상성 검증을 통과한 회귀 설명변수라는 뜻이 아니다.
