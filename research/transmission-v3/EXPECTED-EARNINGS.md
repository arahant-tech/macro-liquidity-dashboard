# 당시 나스닥100 예상이익 자료 가용성

**현재 입력에서는 사용 가능한 PIT 예상 EPS 패널을 확보하지 못했다. 이익 기대 경로는 미검증으로 남긴다.** 이는 모든 데이터 제공자에게 자료가 없다는 주장이 아니라, 이번 공개 자료·로컬 입력 확인의 결과다.

보유 자료와 공개 자료를 확인했으나 당시 나스닥100 예상 EPS 및 공개 시각을 갖춘 연속 자료를 확보하지 못했다. 가격을 후행 PER로 나눈 값은 당시 예상 EPS가 아니다.

공개 출처 확인:

- [FRED NASDAQ100](https://fred.stlouisfed.org/series/NASDAQ100/)은 일별 종가 지수다. 당시 예상이익 시계열이 아니다.
- [Nasdaq의 2025-04-08 관세 환경 자료](https://indexes.nasdaq.com/docs/Nasdaq-100%20in%20the%20Current%20Tariff%20Environment_2025-04-08.pdf)에는 날짜가 명시된 forward PE·예상 성장률 스냅샷이 있지만, Bloomberg·FactSet 기반이다. 검토한 자료에서 유효 공표시각·과거 vintage를 갖춘 연속적인 무료 EPS 원자료는 찾지 못했다.
- [Nasdaq Data Link 공식 제품 안내](https://docs.data.nasdaq.com/docs/data-organization)는 Zacks Earnings Estimates를 Premium으로 분류한다. 이 제품이 나스닥100 지수 집계 및 필요한 시점 필드를 모두 제공하는지는 별도 확인이 필요하며, 현재 접근권한이 있다고 가정하지 않는다.

모형에 필요한 것은 예상 기간, 전망 기준일, 최초 공표시각, 원래 vintage와 지수 집계 정의를 갖춘 나스닥100 예상이익이다. 현재 forward PE, 사후 확정 EPS, 가격/후행 PER, Nasdaq Inc. 자체의 기업 EPS 전망을 대용으로 넣지 않는다. 실질금리와 위험보상 통제는 부분 검정으로 진행할 수 있지만, 그 결과를 이익 기대까지 통제한 결론으로 표시해서는 안 된다.
