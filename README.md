# Macro liquidity observations

[라이브 화면](https://arahant-tech.github.io/macro-liquidity-dashboard/) · [실행 상태](https://github.com/arahant-tech/macro-liquidity-dashboard/actions/workflows/daily.yml)

GitHub Actions가 매시간 등록 소스의 최신 관측을 수집하고 GitHub Pages에 게시합니다. 컴퓨터나 GPT가 켜져 있을 필요는 없습니다. 예약은 매시 23분이며 GitHub 대기열·소스 장애에 따라 늦어질 수 있습니다.

FRED의 중앙은행·은행·분포 관측, PBoC 월간 공식 통계, 공식 기업 5개사의 실제 현금 자사주 공시, 별도 크립토 공급·펀딩·OI·BTC/ETH 현물 ETF 순유입·채굴사 공시 매도량을 다룹니다. 성공 여부와 미연결 범위는 화면의 소스별 상태가 기준입니다. 실패한 요청은 마지막 정상 관측의 시각과 함께 표시합니다.

이 페이지는 원자료 수집 화면입니다. 스칼라 유동성 지표·예측·진입 신호를 생성하지 않습니다. 단위와 관측 기간이 다른 계열을 더하지 않으며, 주식과 크립토를 분리합니다. 모델의 4축 타당성·계수·kill criteria 통과를 주장하지 않습니다.

## 운영

- `.github/workflows/daily.yml`: 시간별 수집·검사·공개 배포
- `model/cloud_feeds.py`: 등록 소스 통합, 실패 보존, 공개 필드 제한
- `state/live/`: 다음 실행에 필요한 최신 원문·공급량 기준 스냅숏
- `live-data.json`: 원자료·실제 확보시각·오류·범위가 함께 있는 공개 스냅숏
- Actions `liquidity-source-receipts`: 실행별 원문과 SHA256 증거, 90일 보존
- `accounting-v8.html`: 이전 회계 화면, 갱신 중지

FRED 키는 GitHub의 암호화된 `FRED_API_KEY` secret에 저장합니다. 선택적 `SEC_USER_AGENT`는 실제 앱·연락처 식별자입니다. 키·연락처를 공개 파일에 쓰지 않습니다. 수집은 Python으로 실행하며 PDF 공시 파서만 pypdf 6.10.0에 의존합니다. SEC 직접 API가 거절되어도 기업의 공식 공시 수집은 계속됩니다.

가정은 소스 정의와 GitHub/API 접근 권한이 유지된다는 것입니다. 발표 지연·개정·접근 차단·HTML 변경·GitHub 일정 지연에서 깨집니다. `known_by`는 실제 수집 완료 시각이며 과거의 최초 발표시각이 아닙니다. 최신 관측으로 봉인한 과거 연구 자료를 갱신하지 않습니다.

자세한 범위와 복구 규칙은 [운영 문서](docs/CLOUD_OPERATION.md), [PBoC](docs/PBOC_SOURCE.md), [SEC 자사주](docs/BUYBACKS_SOURCE.md), [기업 5개사 직접 공시](docs/ISSUER_BUYBACKS_SOURCE.md), [크립토](docs/CRYPTO_SOURCES.md)에 있습니다.

추가 출처: [ETF 순유입](docs/ETF_FLOWS_SOURCE.md), [채굴사 실현 매도](docs/MINER_FLOWS_SOURCE.md). 공시한 기간과 범위를 지키며 전체 시장의 실시간 매도압으로 과장하지 않습니다.

첫 화면은 중앙은행별 요약 그래프와 기간 선택이 가능한 상세 그래프를 제공합니다. FRED 6계열의 약 3년 현재 빈티지와 PBoC 4계열의 확보된 월별 표를 사용합니다. [그래프 데이터 규율](docs/CHART_HISTORY_SOURCE.md)을 따르며, 잠재 유동성 점수·역사적 이용 가능 데이터로 해석하지 않습니다.
