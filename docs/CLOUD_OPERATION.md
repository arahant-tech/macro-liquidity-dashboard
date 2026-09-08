# 클라우드 유동성 관측

## 1. 무엇을 검증하는가

컴퓨터와 GPT가 꺼진 동안에도 GitHub Actions가 등록 소스의 새 관측을 수집하고 GitHub Pages에 수집 상태를 게시할 수 있는지 확인한다. 잠재 유동성 모형의 통계적 검증과는 별개다.

## 2. 명세

`.github/workflows/daily.yml`의 기존 일간 작업을 시간별 작업으로 교체했다. 매시 23분 예약이며 GitHub 실행 대기열에 따라 늦어질 수 있다. Pages는 정적 화면을 제공하고, 수집 코드는 GitHub의 Ubuntu 실행기에서 구동한다. PDF 재무제표 추출에는 pypdf 6.10.0, TLS 인증서 검증에는 certifi 2026.7.22를 고정해 사용한다. GPT 호출과 로컬 컴퓨터는 필요하지 않다.

FRED 인증은 저장소의 암호화된 `FRED_API_KEY` secret을 사용한다. SEC 요청은 실제 연락처를 포함하는 `SEC_USER_AGENT` secret을 선택적으로 사용할 수 있다. 코드·Pages·수집 로그에는 키나 연락처를 쓰지 않는다. API를 접근할 수 없는 경우 원인을 기록하며 우회하지 않는다.

등록 범위는 FRED 16개, PBoC 월간 4개, 기업 공식 공시의 자사주 현금 지급 5개사(AAPL·MSFT·GOOGL·META·V), 별도 크립토 공급·펀딩·OI, BTC·ETH 현물 ETF 일별 순유입 2개 집계, CLSK·MARA의 직접 공시 BTC 매도량 2개다. SEC 직접 API는 보조 경로로 유지하며 접근 오류가 기업 공시 수집을 막지 않는다. 동일 기업의 SEC와 직접 공시를 합산하지 않는다. 현재 범위와 실패는 `live-data.json`의 provider 상태가 기준이다. 자사주·스테이블코인·단일 거래소 데이터의 범위는 각 소스 문서에 따른다.

원자료의 native 빈도·통화·단위를 유지한다. 발표·개정 때만 경제적 관측이 달라진다. 분기를 월별로 반복하지 않는다. `known_by`는 실제 확보시각이며 원래 발표시각과 다르다. 모든 신규 관측은 `research_eligible=false`다. 이 경로는 VIX·주가·BTC 수익률 및 봉인된 연구 표본을 읽거나 갱신하지 않는다.

## 3. 보존과 운영 결과

원문 SHA256과 최초 확보시각을 기록한다. 오류 때 기존 정상값의 값·시각을 유지하고 `retained`로 표시한다. 스테이블코인 변화는 실제로 저장된 두 수집 시각 사이 차이이며 첫 실행은 결측이다. 공급자의 캐시 시각을 알 수 없으므로 시간별·일별 경제적 발행량을 보장하지 않는다.

`state/live/`는 다음 실행에 필요한 최신 관측·원문·스테이블코인 기준 스냅숏을 담는 제한된 체크포인트다. 각 실행의 원문과 상태는 Actions의 `liquidity-source-receipts` artifact에 90일 보존한다. 이전 체크포인트는 Git 이력으로 남지만 이 운영 저장소를 영구적 전체 빈티지 데이터베이스로 해석하지 않는다. 데이터가 개정되면 과거 값은 과거 커밋·실행 영수증에서 확인해야 한다.

연결 성공 여부는 실제 Actions 실행 결과와 공개 화면의 소스별 상태를 확인한다. 단순히 워크플로 파일이 존재한다고 성공으로 판정하지 않는다. 부분 실패도 화면에 게시한다. 모든 소스가 실패하면 상태 게시 후 작업을 실패로 표시한다.

## 4. Kill criteria

계수·표준오차·4축 타당성·kill criteria는 이 데이터 수집 변경에서 미평가다. API 연결 성공은 모형 통과의 근거가 아니다. 과거 v8 회계 화면은 보존하되 갱신을 중지한다.

## 5. 가정과 깨지는 곳

가정: 소스 정의, GitHub 저장소·Actions·Pages 권한, FRED 키가 유지된다. 수집 주기는 공개 제공자에 과도한 요청을 하지 않는 범위다.

깨지는 곳: API 차단·rate limit·HTML 구조 변경·발표 지연·기관 개정·GitHub 장애가 수집을 멈추거나 늦출 수 있다. GitHub는 정시 실행을 보장하지 않고 공개 저장소의 장기 무활동 시 예약을 비활성화할 수 있다. 화면은 마지막 수집 후 3시간을 넘기면 갱신 지연을 표시한다. GitHub Actions 실행 이력에서 실패를 확인할 수 있다. 다수 미연결 채널 때문에 잠재 상태와 전환 연산자·종단 플로우의 전체 명세는 여전히 완비되지 않았다.

공식 운영 근거: [GitHub Pages 사용자 워크플로](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages), [GitHub 예약 실행 제약](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).


ETF는 Farside의 공표 순유입으로 펀드별 합과 총계를 검증하며 불완전·뉴욕 당일 행은 제외한다. 발행사의 결제현금을 독립 인증한 자료는 아니다. 채굴사는 명시적 실현 BTC 매도 공시이며 현재 시장 전체의 온체인 매도압이 아니다. 서로 다른 기간을 월별로 반복하거나 합산하지 않는다. 자세한 원천 규율은 [ETF](ETF_FLOWS_SOURCE.md), [채굴사](MINER_FLOWS_SOURCE.md), [자사주](ISSUER_BUYBACKS_SOURCE.md)에 기록했다.


SEC 자동 요청에 실제 연락처를 암호화된 설정으로 추가했다. Visa의 성공 응답에서 동일 계정 USD 자료가 빈 구조일 때에만 SEC CompanyFacts의 동일 us-gaap 현금지급 계정으로 보완한다. 403에는 이 보완을 적용하지 않는다. API 응답이 성공해도 직접 기업 공시보다 관측 기간이 오래되면 지연으로 표시하며 값·기간을 임의로 당기지 않는다.

## 첫 화면 관측 이력

동일 시간당 실행에서 `chart_history`가 FRED 6계열의 현재 빈티지와 보관된 PBoC 4계열의 월별 이력을 검증하고 `chart-data.json`을 게시한다. 전체 이력의 최초 실제 수집시각과 원문은 `state/chart-history`에 보존한다. 이 출력은 현재빈티지 원자료 표시 전용이며 모델 추정에 넣지 않는다. [원천과 규율](CHART_HISTORY_SOURCE.md)을 따른다.


## 공개 원천 확장

ACM·ECB HQLA, NYFed 딜러·FINRA·JPM SLR, TIC·Z.1·ICI, BIS GLI·OFR 레포를 같은 시간별 파이프라인에서 수집한다. 각 연결의 실제 성공 여부와 늦은 관측은 분리해 표시한다. 키가 필요한 FRED는 기존 secret을 사용하고 공식 무키 API는 가입 없이 접속한다. HTML 공개 표는 정식 API와 구분한다. [무료 접근·잔여 공백·모형 미추정 이유](PUBLIC_API_SOURCE.md)를 따른다.
