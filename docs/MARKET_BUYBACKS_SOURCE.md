# S&P 500 자사주 집계: 공개 관측과 공백

## 1. 무엇을 검증했는가

S&P Dow Jones Indices가 공개 보도자료에 직접 보고한 분기 자사주 매입액을 고정 5개사 관측과 별도로 수집할 수 있는지 검증한다. 현재 시장 전체 자사주 매입액이나 잠재 유동성을 완전히 관측했다는 주장이 아니다.

## 2. 스펙

`model.providers.market_buybacks.collect(output_root, transport=None, clock=None)`의 총 등록 계열은 1개, `SPDJI_SP500_REPORTED_BUYBACKS`다. `layer=4`, `track=equity`, `role=flow`, `frequency=quarterly`, `duration_months=3`, `unit=billion USD`다.

[공식 보도자료 검색](https://press.spglobal.com/index.php?s=2429&keywords=buybacks)에서 S&P 500의 대상 분기가 명시된 buybacks 보고서를 매번 찾는다. 분기와 공표일로 최신 문서를 고르고 같은 날 같은 분기의 상충 문서는 거부한다. 더 새로운 문서의 제목 형식을 해석하지 못하면 과거 문서로 조용히 후퇴하지 않는다.

본문의 공표 도시·날짜 및 실제 발표 대상 분기를 대조한다. 해당 분기의 `buybacks were`와 `share repurchases were` 두 직접 보고액이 일치해야 한다. TTM·전망·승인 한도·배당 포함 주주환원액·순발행 역산은 사용하지 않는다. billion USD 금액을 그대로 보존하고 공표 반올림 단위도 기록한다.

## 3. 실제 결과

2026-09-08 19:31 UTC의 직접 수집에서 공식 검색과 문서가 HTTP200이었다. 최신 확인 문서는 [2025년 12월 18일 공식 발표](https://press.spglobal.com/2025-12-18-S-P-500-Q3-2025-Buybacks-Post-Modest-6-2-Gain-to-249-0-Billion-After-Declining-20-1-Amidst-Uncertainty-in-Q2-Q4-2025-Expenditures-Expected-to-Post-Similar-Growth,-As-2025-Anticipates-a-Record-1-Trillion)다. 2025Q3의 잠정 자사주 매입액은 **249.0 billion USD**, 관측 기간은 2025-07-01~2025-09-30, 공표일까지의 시차는 79일이었다. 본문은 잠정치라고 명시한다.

이 값은 현재 기준으로 **stale**다. 연결 성공 수 `success=1`과 품질 상태 `status=partial`을 구분하고, 관측 행에도 `status=stale`, 오류에 `latest_public_quarter_stale`를 기록한다. 2025Q4·2026Q1·2026Q2 값을 추정하거나 이 값으로 채우지 않는다.

S&P DJI index 페이지와 연결된 공식 XLS는 로컬 정상 HTTP 요청에 403이었다. 따라서 공개 press archive가 최신 라이선스 데이터와 같다고 가정하지 않는다. 5개사보다 넓은 독립 관측 범위를 추가했지만, 최신 시장 전체 관측 공백을 해소한 것은 아니다.

단위 테스트는 실제분기/TTM 분리, 전망·승인금액 배제, 본문 합치, 공표일 대조, 최신 문서 충돌, 신규 제목 형식, 미래 기간, 원천 호스트 제한, 부분 실패와 오래된 값 표시를 확인한다. 네 평가축의 계수·표준오차·OOS 결과는 이 수집기로 산출하지 않는다.

## 4. Kill criteria

모형의 Kill criteria 1~6은 미평가다. 자료 연결과 모델 설명력은 별개다. 수집에서 거부 조건은 날짜·정의 불일치, 실제 분기 지출값 미확인, 상충액, 접근 실패다. 기준 기간이 190일을 넘으면 수집 성공이어도 최신 정상 관측으로 표시하지 않는다.

## 5. 가정과 깨지는 곳

- S&P DJI가 보고하는 S&P 500 구성종목의 분기 자사주 매입액을 사용한다. 정확한 구성종목 확정일과 개별 회사 회계 처리·현금 결제 시각은 독립 검증하지 못했으며 `constituent_date_verified=false`다. S&P 500 자체도 전체 미국 상장시장과 같지 않다.
- SEC/기업 IR 5개사 자료는 집계에 포함될 수 있으므로 **추가 합산 금지**다. Z.1 순발행에도 매입·소각이 반영되므로 합산하지 않는다. `aggregation_allowed=false`와 `not_additive_with`로 표시한다.
- 실제 수신 완료 시각을 `known_by`로, 공표일을 `original_release_date`로 각각 저장한다. 오늘 받은 잠정치·개정치에 공표일을 소급 적용하지 않는다. 원문은 SHA-256 파일명으로 불변 보관한다.
- 새 보도자료의 형식이나 제목, 원천 접근 정책이 바뀌면 중단될 수 있다. 시간당 검색은 분기 발표를 실시간 매매 신호로 바꾸지 않는다. 연구·홀드아웃에는 편입하지 않는다.
