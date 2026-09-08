# 공개 API 연결과 측정 범위

## 1. 무엇을 검증했는가

무료 공식 API와 공개 문서로 확보 가능한 유동성 구성 관측을, 컴퓨터와 GPT가 꺼져 있어도 시간별로 갱신할 수 있는지 검증한다.

## 2. 명세: 인증·변수·빈도·시차

|연결|인증 방식|원자료·원빈도|제한|
|---|---|---|---|
|FRED / Z.1|기존 FRED 키, GitHub 암호화 secret|중앙은행·은행, 비금융기업 주식 순발행 분기 NSA|현재 빈티지; Z.1에 자사주 중복 합산 금지|
|SEC|연락처 User-Agent, 암호화 secret|공시 현금 자사주 매입, 분기/YTD/연간|발행사별 공시기간 유지|
|NYFed / ECB|키 불필요|ACM 월말, 딜러 주간, SSM HQLA 분기|ACM 추정치 개정, 감독 표본 변화|
|BIS / OFR|키 불필요|역외 비은행 달러신용 분기, 레포 일간|총액·구성 중복 금지; 레포는 담보 재사용률 아님|
|Treasury TIC|공식 공개 텍스트, 키 불필요|외국인 미국 주식 순매수 월간|거주지 기준; 2023년 보고양식 변화|
|S&P DJI|공식 공개 공시, 키 불필요|S&P 500 분기 자사주 총액|최신 발견 공시가 오래되면 stale; 기업별 관측과 중복 금지|
|FINRA / JPM|공식 공개 HTML·재무 자료, 키 불필요|마진 월간, JPM SLR 분기|전체 딜러의 자본 여유한도 아님|
|ICI|공식 공개 표, 키 발급 경로 미확인|주식 MF·ETF 주간, index 비중 월간|접근 실패는 화면에 표시; 회원 자료로 우회하지 않음|

이 중 HTML·공시 수집을 정식 제공자 API라고 부르지 않는다. 이미 발급된 FRED 키를 재사용하며 키 없는 공식 API는 가입 없이 연결한다. 신규 키를 발급했다고 주장하지 않는다. 암호화된 secret의 값은 Pages·코드·원문 영수증에 기록하지 않는다.

모든 값은 원기간을 보존한다. `known_by`는 실제 확보시각이다. 발표일이 문서에 있어도 현재 수정본을 과거 시점에 알려진 값으로 바꾸지 않는다. 자료는 표시용이며 `research_eligible=false`다.

## 3. 결과와 네 축

현재 성공/실패, 관측기간과 수집시각은 [라이브 자료](../live-data.json)와 [GitHub 실행 이력](https://github.com/arahant-tech/macro-liquidity-dashboard/actions/workflows/daily.yml)에 기록한다. HTTP 성공과 경제적 최신성은 따로 판정한다. 오류가 생긴 원천은 이전 값과 원래 확보시각을 유지한다.

계수·표준오차는 미추정이다. 축1은 수집 코드의 시간·단위·실패 보존 검사를 수행하며 잠재인자의 실시간 안정성은 미평가다. 축2 이벤트·벤치마크, 축3 walk-forward 꼬리 성능, 축4 연구 스펙 탐색 보정은 이번 수집 변경에서 미평가다. 수집 계열 수가 늘어나는 것을 모형의 타당성으로 해석하지 않는다.

## 4. Kill criteria

모형의 6개 kill criteria는 이번 수집 작업으로 통과 판정하지 않는다. L_t와 A_t는 미추정으로 남는다. carry-funding·담보 관측과 역사적 발표시점·빈티지, 식별 가능한 훈련 표본이 추가로 필요하다. 분포를 별도의 창출 인자로 더하거나 여러 원천을 글로벌 단일 점수로 합산하지 않는다.

## 5. 가정, 깨지는 곳, 아직 확보하지 못한 API

가정: 공개 제공자의 데이터 정의·접근 정책과 GitHub 권한이 유지된다. 시계열의 모집단·분류가 같다는 가정은 각 원천의 메타데이터 검사로 제한한다. 분모·기간이 다른 비율을 합치지 않는다.

깨지는 곳: 공급자 차단, HTML/API 스키마 변경, 발표 지연과 개정, 감독 표본 변화, 사적 데이터의 재배포 권한 부족이다. 과거 모형 추정에 투입하려면 실제 시점별 발표·개정 자료와 훈련창 내 변환을 별도로 확보해야 한다.

- EURUSD·USDJPY **3개월 크로스커런시 베이시스**의 지속 무료·정확 만기·재배포 가능한 공급원을 확인하지 못했다. 환율이나 금리차로 대체하지 않는다.
- CryptoQuant의 현재 무료 API는 시장 데이터 범위다. 채굴자 온체인 흐름은 유료 제공 범위이며, 무료 계정 개설만으로 확보되지 않는다. [공식 요금·접근 범위](https://cryptoquant.com/ko/pricing), [API 제한](https://docs.cryptoquant.com/guides/plans-and-limits).
- Coin Metrics Community의 공개 metric catalog에는 광범위한 채굴자→거래소 분류 흐름이 확인되지 않았다. 채굴량·해시레이트를 매도압으로 바꾸지 않는다. [공개 카탈로그](https://community-api.coinmetrics.io/v4/catalog/asset-metrics).
- 온체인 전송을 확보하더라도 거래소 유입은 실현 매도와 같지 않으며 주소 분류의 완전성은 별도 검증 대상이다.
- 레포 잔액과 당일 거래량은 담보 재사용 회전율을 식별하지 못한다.

[OFR 무등록 API](https://www.financialresearch.gov/short-term-funding-monitor/api/), [BIS API](https://stats.bis.org/api-doc/v1/), [ACM·HQLA 정의](FUNDING_STRUCTURE_SOURCE.md), [중개기관 범위](INTERMEDIARY_SOURCE.md), [종단 플로우 정의](TERMINAL_FLOWS_SOURCE.md)를 함께 참조한다.
