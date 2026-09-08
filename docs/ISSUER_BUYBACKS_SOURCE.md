# Microsoft 자체 공시의 자사주 현금지급 관측

## 1. 무엇을 검증했는가

SEC API의 접근 상태와 별개로, Microsoft가 자체 IR 사이트에서 공개한 현금흐름표에서 실제 보고된 보통주 매입 현금지급액을 분기별로 자동 수집할 수 있는지 검증했다. 매입 승인·계획 금액이나 주식수 감소로 대체하지 않는다.

## 2. 수집 스펙

구현은 `model/providers/issuer_buybacks.py`의 `collect(output_root, transport=None, clock=None)`다. 표 추출은 기존 `model/providers/pboc.py`의 표준 라이브러리 HTML 파서를 재사용하며 외부 패키지나 API 키가 필요 없다.

1. [Microsoft 공식 실적 인덱스](https://www.microsoft.com/en-us/Investor/earnings/)에서 `Press Release & Webcast` 링크를 발견한다.
2. 공식 인덱스가 제공하는 [최신 실적 링크](https://aka.ms/latestearnings)를 따른다. 리다이렉트는 해당 Microsoft 단축 링크와 `www.microsoft.com/en-us/Investor/earnings/` 경로만 허용한다.
3. 최종 URL의 회계연도·분기와 본문 실적 제목이 일치해야 한다. 현금흐름표의 단위, `Common stock repurchased` 행, 인접 자금조달 행, 현재 연도와 `Three Months Ended` 열을 확인한다.
4. 명시된 최근 **3개월 값만** 수집한다. 전년 비교 값이나 6·9·12개월 누적 값을 선택하지 않고, 누적 값 차분으로 분기를 추정하지 않는다. 분기를 월별로 반복하지 않는다.

| 항목 | 명세 |
|---|---|
| 계열 ID | `IR_BUYBACKS_MSFT` |
| 층·트랙 | 4층 종단 플로우 · 주식 |
| 원문 계정명 | `Common stock repurchased` |
| 관측값 | 자사주 현금지급 규모를 양수로 표현 |
| 원문 부호 | `raw_signed_value`에 현금유출의 음수 부호 보존 |
| 단위 | 원문 단위인 `million USD` |
| 빈도 | 분기, 관측 시작일·종료일 명시 |
| 중복 관계 | `SEC_BUYBACKS_MSFT`와 동일 경제현상을 겹쳐 관측할 수 있으므로 합산 금지 |
| 연구 투입 | `research_eligible=false`, `aggregation_allowed=false` |

이 연결은 **발행자가 자체 제공하는 공개 HTML 어댑터**다. SEC 데이터를 다른 경로로 몰래 요청하거나 SEC의 403을 우회하는 기능이 아니다. 통일된 금융 REST API도 아니다. SEC 표준 태그와 본문 계정명의 완전한 의미 일치를 인증하지 않으므로 별도 계열 ID를 사용한다.

원문 bytes를 내용의 SHA256별 HTML 파일로 보존하고 관측에 원문 URL·요청 URL·SHA256·상대 저장경로·실제 수집시각을 첨부한다. `known_by`는 수집시각이다. 본문에 명확한 보도자료 날짜가 있으면 `published_date`로 저장하되, 시각과 시간대가 확인되지 않으므로 `original_release_at=null`을 유지한다.

## 3. 결과와 검증

2026-09-09 한국시간의 실제 수집에서 공식 최신 링크는 [FY2026 Q4 실적 발표](https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast)로 연결됐고, 관측 1개를 성공적으로 수집했다.

| 원문 계정 | 관측기간 | 현금지급 규모 | 원문 부호 | 본문 발표일 |
|---|---|---:|---:|---|
| Common stock repurchased | 2026-04-01 ~ 2026-06-30 | 4,579 million USD | −4,579 million USD | 2026-07-29 |

`tests/test_issuer_buybacks_provider.py`의 17개 테스트는 최근 분기·비교 연도·누적 기간 열의 분리, 원문 부호·단위, 원문 SHA, 보수적 known-by, 미래 기간·발표일 오류 거부, 승인 금액 대체 방지, 공식 URL 범위와 실패 시 결측 유지를 확인한다. 현금흐름표 외 주가·성과 API를 요청하지 않는다.

| 평가 축 | 이번 검증의 범위 |
|---|---|
| 축1 내적 타당성 | 원문·관측기간·수집시각과 개념 보존. 과거 실시간 빈티지 인증 아님 |
| 축2 구성 타당성 | 이벤트·벤치마크 설명력 미검증 |
| 축3 외적 타당성 | 수익률·분위회귀·투자 성과 미검증 |
| 축4 연구자 자유도 | 수집기 구현만 추가. 실제 시장 성과를 보고 모형을 선택하지 않음 |

## 4. Kill criteria

모형의 kill criteria는 이번 수집 성공으로 통과 판정하지 않는다. 표 구조·단위·행 부호·기간이 바뀌면 오류와 빈 관측 목록을 반환한다. 호출하는 집계기는 마지막 정상 자료를 유지하면서 해당 실행의 실패를 명시해야 한다. SEC와 자체 IR 관측을 함께 보여줄 수 있지만 중복 합산해서 전체 자사주 매입 플로우를 만들면 안 된다.

## 5. 가정과 깨지는 곳

가정: Microsoft의 해당 현금흐름표가 그 분기의 보통주 매입 관련 실제 현금지급을 보고하며, 공식 최신 실적 링크가 최신 발표를 가리킨다.

깨지는 곳: 현금 정산 시점과 시장에서 주식을 매수한 시점이 다를 수 있고, 가속 자사주 매입 계약 등의 정산이 포함될 수 있다. 따라서 이 값은 분기별 **보고된 현금지급**이며 일별 실제 거래 집행액이 아니다. Microsoft 한 회사는 미국 주식시장 전체를 대표하지 않는다. SEC 계열과 겹치는 관측, 서로 다른 회계 기간, 보고 지연·소급 정정, 사이트 개편이 해석과 수집을 깨뜨릴 수 있다. 1시간 갱신 확인이 분기 자료의 경제적 빈도를 높이지 않는다.
