# 5개 발행자의 자체 공시 자사주 현금지급 연결

## 1. 무엇을 검증했는가

Microsoft·Apple·Alphabet·Meta·Visa의 자체 공식 공시에서 실제 보고된 자사주 관련 현금지급을 원래 기간·단위로 자동 수집할 수 있는지 검증했다. SEC API의 HTTP 403을 해결했다고 주장하거나, 매입 승인 금액을 실제 매입으로 대체한 작업이 아니다.

## 2. 수집 스펙

구현은 model/providers/issuer_buybacks.py의 collect(output_root, transport=None, clock=None)이며 기본 패널은 처음 정한 5개 회사로 고정한다. 각 회사에 별도의 IR_BUYBACKS 계열을 부여한다. HTML 파서에는 기존 pboc.py의 표준 라이브러리 도구를 사용하고, PDF에는 pypdf==6.10.0을 사용한다.

| 회사 | 최신 문서 발견 경로 | 선택한 공식 계정 |
|---|---|---|
| Microsoft | [공식 실적 인덱스](https://www.microsoft.com/en-us/Investor/earnings/) → Press Release & Webcast | Common stock repurchased |
| Apple | [공식 회사 뉴스](https://www.apple.com/newsroom/topics/company-news/) → 최신 분기 실적 → 재무제표 PDF | Repurchases of common stock |
| Alphabet | [공식 실적 페이지](https://abc.xyz/investor/Earnings/default.aspx)의 공개 FinancialReport JSON 피드 → Earnings Release PDF | Repurchases of stock |
| Meta | [공식 재무자료 페이지](https://investor.atmeta.com/financials/)의 공개 FinancialReport JSON 피드 → Earnings Release PDF | Repurchases of Class A common stock |
| Visa | [공식 분기 실적 페이지](https://investor.visa.com/financial-information/quarterly-earnings/default.aspx)의 공개 FinancialReport JSON 피드 → Financial Report PDF | Repurchases of class A common stock |

Q4 피드는 각 공식 웹사이트의 공개 위젯 JS가 사용하는 /feed/FinancialReport.svc/GetFinancialReportList 경로다. 공개 연도·보고서 유형 파라미터로 요청하며, API 키나 로그인 정보가 필요하지 않았다. 피드가 연결한 발행자별 공식 CDN 폴더만 허용하고 SEC URL은 이 어댑터에서 요청하지 않는다. 사이트 CMS의 ReportDate는 경제적 관측기간이나 발표일로 사용하지 않는다. 실제 기간은 재무제표에서 읽는다.

관측값은 현금지급 규모를 양수로 표시하고 원문 현금유출 부호를 raw_signed_value에 보존한다. 단위는 원문 그대로 million USD다. 현재 연도·비교 연도·분기·YTD 열을 구분한다. Apple의 회계연도 시작일은 같은 PDF 대차대조표의 직전 회계연도 말일 다음 날로 산출했다는 근거를 기록한다.

**분기와 누적 기간을 합치거나 월별로 반복하지 않는다.** 현재 Apple·Visa는 9개월 누적 현금흐름이며 나머지 3사는 직접 공표된 3개월 현금흐름이다. 누적 값 차분으로 분기를 만들지 않는다. 모든 관측은 aggregation_allowed=false, research_eligible=false이며 동일 회사 SEC_BUYBACKS 관측과 not_additive_with 관계를 표시한다.

Meta의 대시는 무조건 0으로 바꾸지 않는다. 현금흐름표 자금조달 활동의 모든 행과 당기·전기·당기누적·전기누적 **4개 열의 소계 항등식**이 맞는지 확인한 뒤 그 계정의 회계상 0을 기록한다. 일반 결측은 0으로 채우지 않는다.

원문 HTML·JSON·PDF는 SHA256별 파일에 보존하고 실제 known_by·수집시각·관측 시작일과 종료일·원문 URL·선택한 PDF 페이지를 붙인다. 원문 발표일도 일 단위 증거에 그치므로 original_release_at=null이다. 수집 당시 문서는 과거 실시간 빈티지를 복원하지 않는다.

## 3. 실제 결과와 검증

2026-09-09 한국시간의 실제 수집은 **5/5 성공**, errors=[], discovery_warnings=[]였다. 다섯 회사 모두 discovery_status=automatic으로 최신 문서를 발견했다.

| 계열 | 현금지급 규모, million USD | 관측기간 | 기간 종류 | 원문 |
|---|---:|---|---|---|
| IR_BUYBACKS_MSFT | 4,579 | 2026-04-01 ~ 2026-06-30 | 3개월 | [Microsoft FY2026 Q4](https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast) |
| IR_BUYBACKS_AAPL | 62,094 | 2025-09-28 ~ 2026-06-27 | 9개월 YTD | [Apple 공식 재무제표](https://www.apple.com/newsroom/pdfs/fy2026q3/FY26_Q3_Consolidated_Financial_Statements.pdf) |
| IR_BUYBACKS_GOOGL | 0 | 2026-04-01 ~ 2026-06-30 | 3개월 | [Alphabet 공식 실적](https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf) |
| IR_BUYBACKS_META | 0 | 2026-04-01 ~ 2026-06-30 | 3개월 | [Meta 공식 실적](https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/Meta-06-30-2026-Exhibit-99-1-FINAL.pdf) |
| IR_BUYBACKS_V | 16,430 | 2025-10-01 ~ 2026-06-30 | 9개월 YTD | [Visa 공식 실적](https://s1.q4cdn.com/050606653/files/doc_financials/2026/q3/Q3-2026-Earnings-Release_vF.pdf) |

이 숫자를 합산한 시장 자사주 플로우는 만들지 않았다. Alphabet의 숫자 0과 Meta의 대시를 비교 연도 값과 구분했다. PDF 현금흐름표를 렌더링하여 제목·기간·단위·숫자 열을 직접 대조했다. Apple PDF 내부 객체 경고가 있었지만 해당 표의 추출값과 렌더링 결과가 일치했다.

tests/test_issuer_buybacks_provider.py의 32개 테스트는 최근 분기·전기·YTD 구분, 부호·단위, 발표일·관측기간, Apple 회계기간 근거, Meta 소계, 공개 피드의 null 메타데이터, 원문 SHA, URL 범위, 발견 실패 시 상태를 확인한다.

| 평가 축 | 이번 작업의 범위 |
|---|---|
| 축1 | 원문·관측기간·수집시각·단위·계정 검증. 역사적 빈티지 인증 아님 |
| 축2 | 이벤트·벤치마크 설명력 미검증 |
| 축3 | 수익률·분위회귀·투자 성과 미검증 |
| 축4 | 최초 5개사 패널의 수집 확대. 시장 성과로 계량 스펙을 선택하지 않음 |

## 4. Kill criteria

자료 연결 성공으로 모형의 kill criteria를 통과 판정하지 않는다. 계정명·부호·단위·기간·표 구조가 바뀌면 해당 관측을 거부하고 오류를 반환한다. 집계기는 이전 정상 관측과 현재 실행 실패를 구분해야 한다.

Meta 공개 목록이 일시적으로 접근 불가하면 정상 공식 재무자료 페이지에서 검증·등록한 독립 배포 PDF를 재확인하는 보조 경로가 있다. 이 경우 discovery_status=blocked, discovery_mode=registered_official_document와 경고를 반환한다. **등록 문서 재확인은 미래 새 실적의 자동 발견 성공이 아니다. 이번 5/5 검증에서는 보조 경로를 사용하지 않았다.**

## 5. 가정과 깨지는 곳

가정: 각 발행자의 현금흐름표가 해당 기간의 자사주 관련 현금지급을 보고하며 공식 최신 목록이 현재 문서를 가리킨다. 매매 집행과 현금 정산이 같다는 가정은 하지 않는다.

깨지는 곳: 현금 정산과 시장 매매 시점 차이, 가속 자사주 매입, 세금·지분 종류·계정 차이, 서로 다른 회계기간, 원문 수정·공표 지연, 사이트 개편·요청 차단이다. 5개사는 시장 전체를 대표하지 않으며 표본 선택·생존 편향이 남는다. 1시간 확인은 분기·누적 보고의 경제적 빈도를 높이지 않는다. 로컬 성공과 GitHub 실행 환경의 접근성은 별도로 확인해야 한다.
