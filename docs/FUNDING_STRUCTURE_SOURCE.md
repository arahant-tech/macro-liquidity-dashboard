# 2층 만기·담보 구성 수집기

## 1. 무엇을 검증했는가

NY Fed ACM 텀 프리미엄과 ECB 감독은행 LCR 자산분류를 무료 공개 원천에서 가져오되, 범위·빈도·현재 빈티지를 바꾸지 않고 보존할 수 있는지 검증했다. 유동성 잠재상태나 예측력을 검증한 결과는 아니다.

## 2. 스펙

`model/providers/funding_structure.py`의 `collect(output_root, transport=None, clock=None)`는 `total=12`, `success`, `observations`, `sources`, `errors`, `assumptions`, `limitations`, `unconnected`를 반환한다. 추가 의존성은 `certifi==2026.7.22`이며 TLS 인증서를 정상 검증한다. API 키·계정은 필요 없다. 검증 해제나 인증 우회는 사용하지 않는다.

### ACM

- [NY Fed Treasury Term Premia](https://www.newyorkfed.org/research/data_indicators/term-premia-tabs)의 공식 인터랙티브가 직접 호출하는 [CSV](https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv)를 사용한다.
- `RunDates`의 원 날짜와 `TERMYld`만 관측값으로 선택한다. 해당 차트 제목은 10년 만기이며 단위는 percent다. CSV는 1961년부터의 월말 관측값이다. 전체 파일은 출처 원문으로 보관하고 대시보드에는 최신 관측만 반환한다.
- `NYFED_ACM_TP10_MONTHLY`: 미국 국채 10년 ACM 텀 프리미엄, 월말, percent. 월평균·일간값으로 바꾸지 않는다. 음수도 정상값이다.
- NY Fed는 모형을 매월 재추정하며 과거 분해값도 바뀔 수 있다고 설명한다. 이 파일은 현재 빈티지의 추정치이고 과거 시점에서 알았던 상태가 아니다. 미국 재무부 수익률로부터 추정된 텀 프리미엄 자체도 오차가 있는 모형 산출물이며, 중앙은행의 공식 정책 추정치는 아니다.

### ECB HQLA 관련 감독 분류

[ECB SUP](https://data.ecb.europa.eu/data/datasets/SUP)의 SDMX API를 사용한다. 계열 키는 모두 `SUP.Q.B01.W0._Z.{CB_ITEM}._T.SII._Z.ALL.LE.E.C`다. CB_ITEM을 `+`로 묶어 한 번의 `format=csvdata&lastNObservations=1` 요청으로 받는다.

범위는 **SSM 중요은행(SII), 연결감독 집계, 구성 변경 표본**, 단위는 **십억 EUR**, 시점은 **분기말**이다. 미국·세계 전체·비은행 또는 모든 EU 은행을 대표하지 않는다. `REF_AREA=B01`, `SBS_DI_1=SII`, `SBS_SAMPLE_TYPE=C`, `UNIT_MULT=9`, `TIME_PER_COLLECT=E`와 원천 제목을 검증한다.

| 코드 | 관측항목 | series_id |
|---|---|---|
| A6310 | LCR 규정상 유동성 버퍼 | ECB_SI_LCR_LIQUIDITY_BUFFER |
| A0000 | 감독 집계 총자산(비율 분모) | ECB_SI_TOTAL_ASSETS |
| A6400 | Level 1 자산, unadjusted | ECB_SI_HQLA_LEVEL1 |
| A6401 | L1 현금·중앙은행 준비금·중앙은행자산 | ECB_SI_HQLA_LEVEL1_CASH |
| A6250 | L1 중앙정부자산 | ECB_SI_HQLA_LEVEL1_GOVERNMENT |
| A6403 | 기타 L1 증권 | ECB_SI_HQLA_LEVEL1_OTHER |
| A6404 | L1 extremely high quality covered bonds | ECB_SI_HQLA_LEVEL1_COVERED |
| A6280 | Level 2A 자산, unadjusted | ECB_SI_HQLA_LEVEL2A |

파생값은 같은 분기·단위·범위의 분자/분모가 모두 정상 공개 관측값일 때만 계산한다.

```
ECB_SI_LCR_BUFFER_ASSET_SHARE = 100 × A6310 / A0000
ECB_SI_LEVEL1_CASH_SHARE = 100 × A6401 / A6400
ECB_SI_LEVEL1_GOVERNMENT_SHARE = 100 × A6250 / A6400
```

첫 값은 **LCR 규제 버퍼/총자산 비중**이다. 단순 준비금 비율로 HQLA를 대체하지 않는다. 나머지는 L1 내부의 구성 비율이다. 관측항목들은 겹치는 분류와 분모를 포함하므로 독립적인 창출 원천으로 더하거나 글로벌 스칼라 유동성 점수로 합산해서는 안 된다. L1 세부항목은 완전한 분해가 아니다.

### 시점 규율

모든 값은 실제 HTTP 응답을 받은 UTC 시각을 `known_by`/`retrieved_at`에 기록한다. `original_release_at=null`, `release_timestamp_verified=false`, `research_eligible=false`다. ECB의 [2026-Q1 공개 안내](https://www.bankingsupervision.europa.eu/press/pr/date/2026/html/ssm.pr260619~b2252c1d8a.en.html)는 6월 19일 발표를 확인해 주지만, 현재 내려받은 값이 그때의 최초 빈티지와 같다는 증거로 사용하지 않는다.

원문 바이트는 SHA-256 기반 불변 파일로 저장한다. 분기를 월별로 반복하지 않으며 같은 기간이라도 개정값을 과거 `known_by`로 소급하지 않는다. 동일 관측의 최초 확인시각을 이후 실행에서 보존하는 것은 상위 클라우드 수집기의 책임이다. ACM은 참조일 이후 75일, ECB 분기는 210일을 넘기면 stale로 표시한다. 이는 운영 지연 표시 규칙이며 실제 발표시차의 추정치가 아니다.

## 3. 결과

2026-09-08 19:24 UTC 실제 공개 HTTP 수집에서 **12/12**, 원천 응답 2개, 오류 0개였다. 로컬 검증은 19개 테스트를 통과했다. 클라우드 실행 여부는 상위 배포 보고서를 따른다.

| 관측 | 원 기준일 | 값 |
|---|---|---:|
| ACM 10년 | 2026-08-31 | 0.762512945770763% |
| ECB LCR 버퍼 | 2026-03-31 | 5,159.3301 십억 EUR |
| ECB 총자산 | 2026-03-31 | 28,868.4573 십억 EUR |
| ECB L1 자산 | 2026-03-31 | 4,917.7321 십억 EUR |
| ECB L1 현금·중앙은행자산 | 2026-03-31 | 2,253.4547 십억 EUR |
| ECB L1 중앙정부자산 | 2026-03-31 | 1,668.7695 십억 EUR |
| ECB L2A | 2026-03-31 | 109.0583 십억 EUR |
| LCR 버퍼/총자산 | 2026-03-31 | 17.8718594% |
| L1 내 현금·중앙은행자산 | 2026-03-31 | 45.8230472% |
| L1 내 중앙정부자산 | 2026-03-31 | 33.9337212% |

12개는 12차원 잠재상태가 아니라 원 관측과 분포 비율의 수다. 계수·표준오차는 추정하지 않았다. 축1에서는 소급 발표일 방지·정의/단위·누락/중복·같은 분기 비율·원문 해시만 확인했다. 축1의 인자 실시간 안정성 및 축2~4의 구성/외적 타당성·연구자 자유도 평가는 미실행이다.

## 4. Kill criteria

모형 Kill criteria 1~6은 모두 **미평가**다. API 연결 성공을 통과로 주장하지 않는다. 원천 스키마 변경, 미래 기준일, 다른 통화/범위, 누락·비공개 관측, 비율 구성요소의 분기 불일치가 있으면 해당 항목 또는 원천을 실패로 처리한다. 이전 값을 새 값으로 가장하거나 누락값을 0으로 대체하지 않는다.

## 5. 가정과 깨지는 곳

- **가정:** NY Fed CSV의 정의와 ECB 감독보고 코드·모집단이 유지된다. **깨지는 곳:** 모델 재추정, 분류 변경, 감독 표본 변경, 공표 지연이 경제 변화처럼 보일 수 있다.
- **가정:** LCR 규제 분류가 담보 구성의 한 측면을 관측한다. **깨지는 곳:** 규제상 적격성은 실제 위기 중 시장 깊이·담보 전환 가능성과 같지 않다. 버퍼와 unadjusted 분류를 같은 총량으로 취급하지 않는다.
- **실제 미공개:** ECB A6500(총 L2), A6290(L2B)의 최신 2026-Q1 응답은 `OBS_STATUS=Q`와 빈 값이다. 따라서 L2B를 0으로 두거나 다른 값의 잔차로 채우지 않았다.
- **통화 베이시스:** EURUSD/USDJPY 정확한 3개월물의 무료·상시·공개 재배포 가능한 API는 확인하지 못했다. [BIS의 측정 논문](https://www.bis.org/publ/work590.pdf)은 Bloomberg 베이시스 및 만기별 FX forward를 사용한다. 단순 금리차, 스팟 환율, 만기가 다른 XCCY를 연결 완료로 표시하지 않는다.
- **계정 후보의 제한:** [BlueGamma 가격표](https://www.bluegamma.io/pricing)는 카드 없는 14일 체험을 제공하지만 API/XCCY의 지속 이용은 계약 범위에 좌우된다. 이는 영구 무료 연결이 아니다. 정확한 EURUSD/USDJPY 3m 계열·호가 leg/부호·갱신시각·GitHub Pages 공개 재배포 권한이 확인되어야 사용할 수 있다. 이 수집기는 해당 서비스를 가입하거나 구매하지 않았다.
- **관측과 모형:** 이 데이터는 층2의 만기·담보 shape 관측이다. 기존 연구 프로토콜상 분포를 A_t 또는 원천 블록 가중치에 곱셈 진입시킬지 확정하기 전까지 L_t의 추가 독립 창출 블록으로 적재하지 않는다.
