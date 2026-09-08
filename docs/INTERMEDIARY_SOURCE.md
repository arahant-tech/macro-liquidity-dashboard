# 중개기관·레버리지·운용 방식 관측

## 1. 무엇을 검증했는가

공식 공개 원천의 딜러 잔액, 고객 마진부채, 회사별 규제비율, 인덱스 펀드 비중을 정의와 원래 빈도를 유지하며 자동 수집할 수 있는지 검증했다. 이 작업은 전환 연산자 A_t의 추정이나 가격에 대한 효과 검증이 아니다.

## 2. 스펙

수집기: `model/providers/intermediary.py`, provider `intermediary`, 3층, 주식 트랙. 시장가격·수익률·홀드아웃 타깃을 요청하지 않는다. 출력은 현재 빈티지의 원자료이며 전부 `research_eligible: false`다. 원자료 수준을 정상성 검증 없이 회귀 입력으로 넣지 않는다.

| 계열 | 원천과 정의 | 빈도/단위 | 범위 |
|---|---|---|---|
| NYFED_PD_UST_NET_POSITION | NYFed `PDPOSGST-TOT`, 미 국채 순포지션 | 주간, 백만 USD | 프라이머리 딜러, TIPS 제외 |
| NYFED_PD_UST_REPO | NYFed `PDSORA-UTSETTOT`, 미 국채 repo 잔액 | 주간, 백만 USD | 동일 딜러 보고 범위, TIPS 제외 |
| NYFED_PD_UST_REVERSE_REPO | NYFed `PDSIRRA-UTSETTOT`, 미 국채 reverse repo 잔액 | 주간, 백만 USD | 동일 딜러 보고 범위, TIPS 제외 |
| FINRA_MARGIN_DEBT | 고객 증권 마진계좌 차변 잔액 | 월간, 백만 USD | FINRA 보고 회원사 |
| FINRA_CASH_FREE_CREDIT | 고객 현금계좌 자유신용잔액 | 월간, 백만 USD | 동일 보고 회원사 |
| FINRA_MARGIN_FREE_CREDIT | 고객 마진계좌 자유신용잔액 | 월간, 백만 USD | 동일 보고 회원사 |
| JPM_REPORTED_SLR | JPMorgan Chase & Co. 연결회사 보고 SLR | 분기, % | 단일 연결회사 |
| ICI_DOMESTIC_EQUITY_INDEX_SHARE | 미국 주식 인덱스 펀드 자산 / 미국 주식 active+index 펀드 자산 | 월간, % | ICI 분류 미국 등록 뮤추얼펀드+ETF |
| ICI_WORLD_EQUITY_INDEX_SHARE | 해외 주식 인덱스 펀드 자산 / 해외 주식 active+index 펀드 자산 | 월간, % | 같은 펀드 모집단의 해외 투자 분류 |
| ICI_LONG_TERM_INDEX_SHARE | 장기 인덱스 펀드 자산 / 장기 active+index 펀드 자산 | 월간, % | 같은 펀드 모집단, 주식·혼합·채권 |

NYFed는 [공식 통계 설명](https://www.newyorkfed.org/markets/counterparties/primary-dealers-statistics)과 [공식 API](https://markets.newyorkfed.org/static/docs/markets-api.html)를 따른다. `list/seriesbreaks`, `list/timeseries`, `latest/SBN2024`에서 현재 데이터 구조, 계열 정의, 최신 관측을 각각 확인한다. 새 series break가 생기면 정의 검토까지 실패 처리하며 과거 구조와 자동 연결하지 않는다. 이 세 계열은 수요일 잔액이다. repo와 reverse repo를 더하거나 빼서 유동성 또는 담보 재사용률로 만들지 않는다. 순포지션은 음수가 허용되지만 두 gross financing 잔액은 음수를 거부한다.

[FINRA 공식 페이지](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics)는 별도 데이터 피드를 제공하지 않는다고 명시한다. 따라서 API 키 발급으로 가장하지 않고 공개 HTML 표를 수집한다. 월중 값을 생성하지 않으며 월말 라벨과 마지막 영업일 결제일 기준이라는 정의를 함께 저장한다. 원문은 일반적으로 다음 달 세 번째 주 갱신이라고 설명하지만 이 관행으로 발표일을 만들어내지 않는다.

JPM은 [현재 분기 실적 페이지](https://www.jpmorganchase.com/ir/quarterly-earnings)의 실제 프런트엔드가 사용하는 [공식 JSON 목록](https://www.jpmorganchase.com/services/json/v1/investor-relations/quarterly-earnings.json)에서 최신 분기의 공식 XLSX 보충자료를 찾는다. 최신 분기 파일이 없으면 이전 분기 파일로 조용히 대체하지 않는다. 자본 표의 회사·표제·기간 열·SLR 행·퍼센트 서식을 확인하고, 보고 SLR을 Tier 1 capital / total leverage exposure와 반올림 오차 범위 0.055%p 이내로 대사한다. 표의 캐시된 공시 값을 읽으며 수식을 재작성하거나 워크북을 실행하지 않는다. 단일 회사의 비율이므로 규제 최소치, 버퍼, usable headroom은 별도로 추정하지 않는다.

[ICI 월간 Active/Index 목록](https://www.ici.org/monthly-active-and-index-data)에서 공개 최신 보고서 링크를 찾도록 구현했다. 자산 표의 active/index 기간이 같아야 하며, `100 × index/(active+index)`를 공시 비율과 0.055%p 이내로 대사한다. ETF 자산 전체를 패시브 자산으로 분류하지 않는다. 다른 뮤추얼펀드에 주로 투자하는 펀드는 원천 모집단에서 제외된다. 인덱스 비중은 이 펀드 모집단 안의 운용 방식 구성이다.

모든 자료에는 실제 수집 UTC 시각 `known_by`, `retrieved_at`, 원문 URL, 원문 SHA256와 변경 불가능한 raw 경로를 남긴다. 최초 수집 이전에 이미 공개됐다는 이유로 과거 시점에 알려진 것으로 소급하지 않는다. 원래 발표의 정확한 시각은 확인되지 않았으므로 `release_timestamp_verified: false`다. 분기 값을 월별로 반복하지 않는다.

## 3. 결과

2026-09-08 UTC 실제 로컬 네트워크 수집: **7/10 성공**, ICI 3개만 **HTTP 403**. 저장본은 `data/live/extensions/intermediary-verified/latest.json`이다. 공개 페이지의 검색 결과에 수치가 보인다는 사실을 자동 수집 성공으로 계산하지 않았다. 코드에 검색 결과 수치를 하드코딩하지 않았으며 접근 제어 우회, 대체 호스트, 회원 전용 보고서 또는 구독 구매를 쓰지 않았다.

| 실제 수집 계열 | 값 | 원래 기준 기간 |
|---|---:|---|
| 미 국채 순포지션, TIPS 제외 | 477,607 백만 USD | 2026-08-26 |
| 미 국채 repo, TIPS 제외 | 3,010,949 백만 USD | 2026-08-26 |
| 미 국채 reverse repo, TIPS 제외 | 2,723,167 백만 USD | 2026-08-26 |
| FINRA 마진부채 | 1,417,225 백만 USD | 2026-07 월말 |
| FINRA 현금계좌 자유신용잔액 | 205,132 백만 USD | 2026-07 월말 |
| FINRA 마진계좌 자유신용잔액 | 217,305 백만 USD | 2026-07 월말 |
| JPM 연결 SLR | 5.5% | 2026-06-30 |

단위·기간·동일 모집단 분모·정의 변경·미래 기간·중복 행·missing≠0·원문 해시·HTTP403 격리를 포함한 22개 테스트를 통과했다. 계수·표준오차·축1~4 성능 지표는 이 원천 연결 작업에서 추정하지 않았다. GitHub 실행 환경의 연결 상태는 별도 실제 실행 결과를 따른다.

## 4. Kill criteria

모형 Kill criteria는 **미평가**다. 이 자료들을 연결했다는 사실로 실시간 인자 안정성, 발표시차 반영 OOS 성능, NFCI 증분 기여, 이벤트 감지, leave-one-out 또는 평균·꼬리 유의성을 통과했다고 주장할 수 없다.

## 5. 가정과 깨지는 곳

가정: 공식 보고 주체의 정의와 표 구조가 유지되고, 현재 빈티지의 공시 값이 해당 보고 범위 안에서 유효하다. XLSX에 저장된 캐시 값과 표시 퍼센트 서식이 공식 보고 수치와 일치한다. ICI가 공개 페이지를 정상 응답하면 같은 모집단의 분자·분모로 운용 방식 구성을 관측할 수 있다.

깨지는 곳: 딜러 잔액 증가가 남은 여력 증가를 뜻하지 않는다. 잔여 여력은 규제·내부 한도·자본 이동·담보·리스크 수요에 따라 달라진다. FINRA 변동에는 보고 방식과 회원사 범위 변화가 섞인다. JPM 한 회사는 전체 딜러의 SLR 제약을 대표하지 않으며, SLR 제도가 달라지면 동일 수치의 경제적 의미도 달라진다. 인덱스 펀드 비중은 모든 투자자의 패시브 소유 비중도 승수 M도 아니다. 지연 발표·수정 자료·사이트 구조 변경·403 발생 시 최신 값이 확보되지 않을 수 있다. A_t에 어떤 변수와 부호·변환을 쓸지는 훈련창과 발표시각 규율을 충족한 별도 추정 문제다.
