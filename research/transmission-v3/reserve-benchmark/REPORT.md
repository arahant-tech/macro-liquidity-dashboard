# 준비금 부족 압력의 외부 연구 벤치마크: NY Fed RDE

뉴욕연은이 공개한 Reserve Demand Elasticity(RDE) 시계열을 확보했다. 공식 웹앱이 읽는 CSV를 내려받아 수치와 날짜를 정규화했다. JavaScript 파일은 다운로드 경로를 찾는 자료로만 읽었고 실행하지 않았다.

- 원자료: [공식 CSV](https://www.newyorkfed.org/medialibrary/research/interactives/data/elasticity/elasticity-data.csv), [설명 JSON](https://www.newyorkfed.org/medialibrary/research/interactives/data/elasticity/elasticity-content.json), [소개 페이지](https://www.newyorkfed.org/research/reserve-demand-elasticity)
- 확보 범위: 2010-01-20~2026-08-13, 714개 추정치.
- 현재 공개본의 게시 표기: 2026-08-20 10:00 ET. 정확한 다운로드 시각과 파일 SHA-256은 `manifest.json`에 기록했다.
- 공식 공개 다운로드 목록에는 동일 연구의 [Excel 다운로드](https://www.newyorkfed.org/medialibrary/Research/Interactives/Data/elasticity/download-data)도 있다. CSV를 직접 확보했으므로 추가 변환은 하지 않았다.

## 정의와 해석

RDE는 준비금이 상업은행 총자산의 1%만큼 증가했을 때 연방기금금리와 준비금 이자율(IORB)의 차이가 몇 bp 움직이는지를 나타내는 준비금 수요곡선의 기울기다. 0에 가까우면 금리가 일상적인 준비금 변동에 둔감하고, 더 음수이면 준비금 감소에 대한 압력이 커지는 방향이다. 95% 구간 상단이 0보다 작다는 사실만으로 특정한 ‘부족’ 경계선을 단정해서는 안 된다. 충분·풍부·부족 영역의 해석에는 기울기의 크기와 다른 자금시장 지표도 필요하다.

`rde-normalized.csv`의 열은 `date`, 중앙 추정치 `rde`, 95% 구간의 `p2_5`·`p97_5`, 68% 구간의 `p16`·`p84`다. 원문은 이 구간을 confidence intervals로 부른다. 날짜가 중복 없이 증가하고, 모든 행에서 p2_5 ≤ p16 ≤ 중앙값 ≤ p84 ≤ p97_5인지 확인했다.

## 시점과 독립성의 한계

뉴욕연은은 각 관측일 당시까지의 정보만으로 추정하며 계산은 5영업일마다 반복한다고 설명한다. 공개 갱신은 매월 세 번째 목요일 또는 FOMC blackout 이후 첫 영업일이다. 따라서 **추정 기준일과 투자자가 공개 자료를 얻는 날은 같지 않다.** 이번 파일은 현재 공개된 하나의 전체 경로이며, 과거 발표본·수정 이력·각 행의 최초 공개시각은 확보하지 않았다. 관측일을 그대로 매매 신호 가용일로 사용하지 않는다.

또한 **독립적인 실제 부족량 측정치는 아니다.** 준비금 잔액과 연방기금 거래금리, IOER/IORB, 은행자산을 사용하는 연구 추정치여서 이 프로젝트의 준비금·조달금리 검증과 개념 및 입력이 겹친다. 특히 뉴욕연은은 비공개 일별 준비금과 연방기금 거래자료를 사용한다. 우리 공개 H.4.1 잔액 및 공개 EFFR와 완전히 같은 입력은 아니지만, 이를 독립적인 정답으로 간주하면 순환 검증 위험이 있다. 당시 정보만 쓰는 추정이라는 설명 또한 이 프로젝트가 역사적 공개 vintage를 확보했다는 뜻은 아니다.

따라서 v3에서는 준비금 풍부성에 대한 **외부 연구 벤치마크와 기술적 일치도**로 사용하고, 이를 단독으로 인과적 검증·실시간 예측 성공·보편성의 증거로 세지 않는다. 공식 사이트 역시 RDE를 뉴욕연은·연준·FOMC의 공식 추정으로 규정하지 않는다.

방법론: [Afonso, Giannone, La Spada, Williams, Staff Report 1019](https://www.newyorkfed.org/research/staff_reports/sr1019).
