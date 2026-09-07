# 미국 달러 유동성 대시보드

[대시보드 열기](https://arahant-tech.github.io/macro-liquidity-dashboard/)

매일 한국시간 오전 9시에 GitHub Actions가 공식 자료를 확인하고 같은 S=R+P, p=(R/S,P/S) 정의로 모델을 재계산한다. 컴퓨터나 브라우저를 켜 둘 필요가 없다. 실행 대기 때문에 정확한 시작 시각은 지연될 수 있다.

- H.4.1 준비금·역레포 Others: FRED 공식 계열의 수요일 잔액.
- ICI 주간 상품 설정: combined·mutual·ETF 발표일·단위·분류·합계 교차 확인. 각 발표의 겹치는 주는 수정치를 반영하고 이전에 검증한 주를 보존한다. 접근이 차단되면 이전 관측과 실패 상태를 유지한다.
- MMF·자금 수요: OFR 전체 MMF 투자처, Treasury DTS 순발행, NY Fed 딜러 국채 보유와 TGCR, FRED IOER/IORB. 월별 조달 자료까지 검증된 묶음만 반영한다.
- Z.1: 실제 current release의 FU 비계절조정 분기 거래. preview는 사용하지 않는다. 공식 부문·계열 정의가 달라지면 임의로 대응시키지 않고 이전 자료를 유지하며 검토 필요를 표시한다.

매일 새 숫자가 발표되는 것은 아니다. 페이지의 **최근 자료 확인**, **모델 계산**, **출처별 자료 기준일**은 별개다. 일부 출처 실패는 해당 묶음을 유지하며, 코어 수집 또는 전체 계산 검증 실패는 모델 전체를 그대로 유지한다. 실패를 새 자료 확인 성공으로 표시하지 않는다. 페이지를 열면 검증된 공개 JSON을 읽고, 열어 둔 페이지는 5분마다 새 게시본을 확인한다. 데이터와 상태의 SHA-256이 일치하지 않으면 현재 표시를 유지한다.

이전 연구 보관본은 고정 기록이며 자동 갱신하지 않는다. 현재 수정치 기반 측정으로, 당시 공표 빈티지(PIT)나 매매 성과를 재검증하는 작업은 아니다. 원문 HTTP 응답과 해시는 각 실행의 `official-source-snapshots` 자료에서 90일 동안 확인할 수 있다. 과거 미수집 이력을 새로 확보했다고 주장하지 않는다.

[실행 기록 및 수동 재실행](https://github.com/arahant-tech/macro-liquidity-dashboard/actions/workflows/daily.yml). 수동 실행은 저장소 권한이 있는 소유자만 가능하다. 인증정보는 페이지나 수집 코드에 넣지 않으며 게시에는 이 저장소에 한정된 GitHub Actions 토큰을 사용한다.

기존 `liqdesk`·`liqdesk-page` 저장소 및 해당 자동 갱신과는 연결하지 않는다.

GitHub Pages는 검증된 `_site` 패키지만 배포한다. `live-model.json`과 `update-status.json`의 일일 기록은 강제 푸시 없이 이력으로 남긴다. GitHub가 장기 비활동으로 일정을 중지하거나 실행을 지연시키면 페이지가 마지막 확인 시각과 36시간 경과 안내를 표시한다.

구현 근거: [GitHub Pages 사용자 지정 워크플로](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages), [예약 실행 규칙](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
