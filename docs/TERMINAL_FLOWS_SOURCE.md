# 4층 종단 플로우 수집 계약

## 1. 무엇을 검증했는가

공개 원천이 직접 보고한 주식 거래 플로우를 보유액·가격 변화와 구별하고, 각 원천의 범위와 관측 기간을 유지한 채 자동 수집할 수 있는지 검증한다. 이 작업은 잠재 유동성, 전환 연산자 또는 하방 꼬리 설명력의 검정이 아니다.

## 2. 스펙

`model.providers.terminal_flows.collect(output_root, transport=None, clock=None, api_key=None)`는 `observations`, `sources`, `errors`, `success`, `total`, `status`를 반환한다. 등록 계열은 8개다. 각 행은 `layer=4`, `track=equity`, `role=flow`, `research_eligible=false`다. 다른 수집기와 합산하지 않는다.

| ID | 직접 원천과 의미 | 빈도·단위 |
|---|---|---|
| `TIC_US_EQUITY_FOREIGN_NET_PURCHASES` | Treasury SLT Table 1, Grand Total `99996`, `for_lt_eqty_net` | 월간, million USD |
| `Z1_NFC_EQUITY_NET_ISSUANCE` | FRED `BOGZ1FU103164103Q`, Fed 비금융기업 주식부채 거래 | 분기 실제 금액, million USD, NSA |
| `Z1_ETF_EQUITY_TRANSACTIONS` | FRED `BOGZ1FU563064100Q`, Fed ETF 주식자산 거래 | 분기, million USD, NSA |
| `Z1_MF_EQUITY_TRANSACTIONS` | FRED `BOGZ1FU653064100Q`, Fed 펀드 주식자산 순매수 | 분기, million USD, NSA |
| `ICI_MF_DOMESTIC_EQUITY_FLOW` | ICI 미국 주식형 뮤추얼펀드 순유입 추정 | 수요일 종료 주간, million USD |
| `ICI_MF_WORLD_EQUITY_FLOW` | ICI 해외 주식형 뮤추얼펀드 순유입 추정 | 수요일 종료 주간, million USD |
| `ICI_ETF_DOMESTIC_EQUITY_NET_ISSUANCE` | ICI 미국 주식형 ETF 순발행 추정 | 수요일 종료 주간, million USD |
| `ICI_ETF_WORLD_EQUITY_NET_ISSUANCE` | ICI 해외 주식형 ETF 순발행 추정 | 수요일 종료 주간, million USD |

Treasury 원천은 [공개 SLT 파일](https://ticdata.treasury.gov/Publish/slt_table1.txt)이다. [Treasury 설명](https://home.treasury.gov/data/treasury-international-capital-tic-system-home-page/tic-forms-instructions/securities-b-portfolio-holdings-of-us-and-foreign-securities)에 따라 미국 주식의 `Net U.S. Sales` 양수는 외국인 순매수다. 국채·회사채·전체 증권, 보유액, 평가손익 열을 제외한다. 거주지 기준이고 2023-02에 보고서식 단절이 있다. 최신 세계합계 행만 내보내므로 지역 합계의 중복 합산이 없다.

[FRED 공식 계열](https://fred.stlouisfed.org/series/BOGZ1FU103164103Q)의 거래·분기·NSA 메타데이터를 매회 검증한다. `FRED_API_KEY`가 있으면 공식 `series`와 `series/observations` API를 사용한다. 키가 없으면 같은 계열의 공식 공개 CSV와 메타데이터를 사용한다. 분기 시작일로 표기된 관측일을 실제 분기 시작·말일로 명시하고 금액은 바꾸지 않는다. SAAR, 주식 시가총액, 전체 금융조달로 대체하지 않는다. 비금융기업 전체 범위로 비상장 주식도 포함하며 금융기업은 제외된다. 자사주 공시 관측은 독립적으로 유지한다. 순발행에 이미 퇴출·매입이 들어 있으므로 자사주와 추가 합산하면 중복이다.

Z.1의 추가 두 계열은 ICI 주간 가입유입 계열을 대체하지 않는다. [ETF 공식 정의](https://www.federalreserve.gov/apps/fof/SeriesAnalyzer.aspx?s=FA563064100&t=)는 ICI 월간 equity ETF 순발행을 분기 합산하고 commodity, hybrid, MMF 관련 조정을 포함한다. [뮤추얼펀드 공식 정의](https://www.federalreserve.gov/apps/fof/SeriesAnalyzer.aspx?s=FU653064100&t=)는 common/preferred 주식의 ICI net portfolio purchases를 합친 분기 주식자산 거래다. 가입·환매 순유입과 다르며 해외 주식 및 변액연금 펀드를 포함할 수 있다. 자체 AUM 차분이나 가격을 이용해 계산하지 않는다. 각각 별도의 공식 FRED 메타데이터와 NSA 분기 관측을 검증한다. 주간 ICI 네 계열이 차단되면 그 상태를 계속 표시한다.

[ICI 펀드 보고서](https://www.ici.org/research/stats/flows)와 [ETF 보고서](https://www.ici.org/research/stats/etf_flows)를 별도로 읽는다. 혼합·채권·상품 펀드와 전체 합계를 배제한다. Domestic·World 합계가 공표 Equity 합계와 반올림 허용폭 내에서 맞는지 검증한다. 해외 주식형 계열은 미국 주식 수요로 표시하지 않는다. AUM 차분은 사용하지 않는다. ETF 순발행은 현물 납입을 포함할 수 있으며, 같은 시각 시장에서 집행된 현금 매수와 같다고 가정하지 않는다.

## 3. 결과와 검증 범위

2026-09-09 직접 요청에서 Treasury 공개 파일과 FRED 공개 CSV/메타데이터는 HTTP200으로 확인했다. 추가 ETF/Fund 분기 거래 연결은 위의 별도 ID를 사용하며 주간 관측 공백을 메우거나 최신 주간값으로 표시하지 않는다. ICI 공개 보도자료 두 개와 공개 ETF summary XLS는 정상 식별 HTTP 요청에 403을 반환했다. 따라서 ICI는 파서 및 연결 시도만 구현했으며, 직접 수집 성공 전에는 완료라고 주장하지 않는다. 회원 전용 과거자료, 프록시, 검색 결과를 복사한 수동 값으로 대체하지 않는다. 이 범위에서 ICI의 무료 API 발급 경로는 확인되지 않았다.

네 축의 계수·표준오차·OOS 지표는 산출하지 않는다. 자료 연결 검사는 변환, 인자 부호 정규화, 빈티지 훈련, 이벤트·OOS 검증을 대신하지 않는다. 구현 테스트는 열 혼동, 미래 기간, 세계합계 중복, 결측의 0 오인, ICI 합계 불일치, Z.1 레벨·SAAR 혼입, 분기 금액 보존, API 키 비노출, 부분 실패 및 원본 해시 충돌을 확인한다.

## 4. Kill criteria

모형 Kill criteria 1~6은 미평가다. 직접 수집 불가, 정의·단위 변경, 최신값 결측, 미래 기간, 합계 불일치는 수집 단계에서 해당 원천 실패로 처리한다. 한 원천의 403이 다른 원천의 정상 결과를 지우지 않는다. 최신 관측을 확보하지 못했을 때 과거값을 신규 관측으로 재발표하지 않는다.

## 5. 무엇을 가정했고 어디서 깨지는가

- `known_by`는 실제 수신 완료 시각이다. ICI 공표일은 날짜로 따로 저장하지만 그날의 특정 시각으로 빈티지를 소급하지 않는다. 원천의 현재 개정값은 과거 실시간 정보 집합이 아니다.
- 원문 응답을 SHA-256 이름의 불변 파일로 저장한다. API 키는 요청 과정에서만 사용하며 파일·메타데이터·오류에 저장하지 않는다. 원천이 키를 응답에 반사하면 기록 전에 거부한다.
- 시간당 확인은 공표가 시간당 바뀜을 의미하지 않는다. ICI는 주간 추정치, TIC는 월간, Z.1은 분기이며 공표 지연이 있다. 분기→월 반복이나 보간이 없다.
- 원천 범위가 서로 다르고 최종 거래가 중첩될 수 있다. 여덟 값을 더한 단일 유동성/플로우 지수는 제공하지 않는다.
- 공개 HTML·열 이름·발행 분류가 바뀌면 파서가 중단된다. 현재 접근 가능한 원천도 서버 정책 변경으로 실패할 수 있다. GitHub 실제 실행 성공 여부를 별도로 확인해야 한다.
