/* Conditional narrative preview. No estimated state, regime, score or trade signal. */
window.mountLiquidityInterpretation = function(context) {
  const {treasuryOverview, DATA, config, series, design, observedChange, shortDate, node, grid, INTERPRETATION_COPY} = context;
  if (!treasuryOverview) return;
  const sourceLibrary = {
    fiscal: { title: 'Fed · 재정 흐름과 준비금', url: 'https://www.federalreserve.gov/econres/notes/feds-notes/fiscal-flow-volatility-and-reserves-20191216.html' },
    rrp: { title: 'Fed · MMF·레포·ON RRP의 자금 경로', url: 'https://www.federalreserve.gov/econres/notes/feds-notes/money-market-fund-repo-and-the-on-rrp-facility-20231215.html' },
    acm: { title: 'NY Fed · ACM 기간 프리미엄 정의', url: 'https://www.newyorkfed.org/research/data_indicators/term-premia-tabs' },
    finra: { title: 'FINRA · 마진 계좌 차입잔액 정의', url: 'https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics' },
    tic: { title: '미 재무부 · TIC 자료 범위', url: 'https://home.treasury.gov/data/treasury-international-capital-tic-system' }
  };
  const snapshotTime = Date.parse(DATA.captured_at);
  const facts = config.order.map(slot => {
    const s = series.get(slot), last = s.points.at(-1);
    const known = Date.parse(s.known_by);
    const eligible = s.interpretation_eligible !== false && Number.isFinite(snapshotTime) && Number.isFinite(known) && known <= snapshotTime
      && last && Number.isFinite(last.value) && Date.parse(last.date) <= snapshotTime;
    const change = eligible ? observedChange(s, design[slot], s.points, s.points.length - 1) : null;
    return { slot, id: s.id, title: design[slot].title, frequency: s.frequency, date: last?.date,
      known_by: s.known_by, last: eligible ? last.value : null, change, eligible };
  });
  const fact = slot => facts.find(f => f.slot === slot);
  const amount = value => Math.abs(value).toLocaleString('en-US', {minimumFractionDigits: 1, maximumFractionDigits: 1});
  const shortMonth = date => Number(date.slice(5, 7)) + '월';
  const complete = facts.every(f => f.eligible && f.change && Number.isFinite(f.change.delta)
    && ['전주 대비', '전월 대비'].includes(f.change.label));
  const differentDates = new Set(facts.map(f => f.date)).size > 1;
  const reserve = fact(1), treasury = fact(6), term = fact(2), margin = fact(3), flow = fact(4);
  const pairedCash = complete && reserve.change.from === treasury.change.from && reserve.change.to === treasury.change.to;

  function currentNarrative() {
    if (!complete) return {
      id: 'current', label: '현재 관측', title: '해석에 필요한 관측이 부족합니다.',
      when: '최신값과 직전값의 원자료·날짜·수집 상태를 먼저 확인해야 합니다. 이전 정상값이나 불일치한 이력으로 현재 해석을 만들지 않습니다.',
      good: '결측을 개선이나 안정으로 해석하지 않습니다.', bad: '일부 관측의 시점 또는 비교값을 확인할 수 없습니다.',
      watch: '누락된 원 관측과 최초 수집시각을 확인한 뒤 다시 읽습니다.',
      assumption: '기준시점에 알려진 실제 관측만 사용합니다.', breaks: '결측을 0으로 채우거나 아직 알려지지 않은 값으로 문구를 만들면 해석이 깨집니다.',
      sourceKeys: ['fiscal', 'acm', 'finra', 'tic']
    };
    let title = '관측별 변화는 확인되지만, 전체 판단에는 추가 확인이 필요합니다.';
    const good = [], bad = [];
    if (pairedCash && reserve.change.delta < 0 && treasury.change.delta > 0) {
      title = '준비금은 줄고, 재무부 현금은 늘었습니다.';
      bad.push(`같은 주 준비금은 ${amount(reserve.change.delta)}십억 달러 감소했고 TGA는 ${amount(treasury.change.delta)}십억 달러 증가했습니다. 현금 흡수 경로와 맞는 조합이지만, 두 변화를 독립 악재로 더하지 않습니다.`);
    } else if (pairedCash && reserve.change.delta > 0 && treasury.change.delta < 0) {
      title = '준비금은 늘고, 재무부 현금은 줄었습니다.';
      good.push('같은 주 현금 여건의 개선 가능성을 점검할 조합입니다. 실제 조달 압박이 완화됐는지는 별도 확인해야 합니다.');
    } else {
      bad.push('준비금과 TGA의 조합만으로 현금 여건의 방향을 정하지 않습니다. Fed의 다른 자산·부채 변화를 함께 확인해야 합니다.');
    }
    if (term.change.delta < 0) {
      good.push(`${shortMonth(term.date)} ACM 기간 프리미엄은 ${amount(term.change.delta)}bp 낮아졌습니다. 장기 위험보상 성분의 하락이며, 실제 조달금리 하락을 뜻하지는 않습니다.`);
    } else if (term.change.delta > 0) {
      bad.push(`${shortMonth(term.date)} ACM 기간 프리미엄이 ${amount(term.change.delta)}bp 높아졌습니다. 장기채 위험보상 요구가 커졌는지 실제 수익률과 함께 확인합니다.`);
    }
    if (flow.last > 0) {
      good.push(`${shortMonth(flow.date)} 외국인 미국주식 순매수는 ${amount(flow.last / 1e3)}십억 달러였습니다. 해당 월의 한 수요 경로가 확인된 것이며, 현재 전체 주식 수요로 확대 해석하지 않습니다.`);
    } else if (flow.last < 0) {
      bad.push(`${shortMonth(flow.date)} 외국인은 미국주식을 ${amount(flow.last / 1e3)}십억 달러 순매도했습니다. 국내 펀드와 자사주 매입을 포함한 전체 수요는 별도 확인이 필요합니다.`);
    }
    if (margin.change.delta < 0) {
      const movement = Number.isFinite(margin.change.percent) ? Math.abs(margin.change.percent).toFixed(2) + '%' : amount(margin.change.delta) + '십억 달러';
      bad.push(`${shortMonth(margin.date)} 마진부채는 ${movement} 줄었습니다. 자발적 상환과 강제 축소 중 어느 쪽인지는 이 자료로 구분할 수 없습니다.`);
    } else if (margin.change.delta > 0) {
      const movement = Number.isFinite(margin.change.percent) ? Math.abs(margin.change.percent).toFixed(2) + '%' : amount(margin.change.delta) + '십억 달러';
      bad.push(`${shortMonth(margin.date)} 마진부채는 ${movement} 늘었습니다. 차입 확대와 함께 담보·증거금 여유를 점검해야 합니다.`);
    }
    return {
      id: 'current', label: '현재 관측', title,
      when: differentDates ? '관측월이 달라 전체 판단을 보류합니다. 각 지표는 표시된 기준기간의 사실로만 읽습니다.' : '같은 날짜라도 기간 집계와 전달경로가 확인돼야 전체 해석이 가능합니다.',
      good: good.length ? good.join('\n\n') : '이 다섯 관측만으로 제약 완화를 확인하기 어렵습니다.',
      bad: bad.length ? bad.join('\n\n') : '이 다섯 관측만으로 제약 악화를 확인하기 어렵습니다.',
      watch: '먼저 SOFR−IORB와 레포금리로 준비금 변화가 실제 조달 압박을 동반했는지 확인합니다. 이어서 최신 FINRA·TIC 발표, 재무부 일간 입출금·빌/쿠폰 결제, 주식 펀드·자사주 매입을 확인합니다. 이 추가 지표들은 여기서 판정하지 않았습니다.',
      assumption: '현재 스냅샷의 실제 직전 관측과 비교합니다. 주간 평균·월말 잔액·월간 흐름의 정의를 유지합니다.',
      breaks: '서로 다른 달의 변화를 현재의 한 상태로 합치거나, TGA와 준비금을 두 개의 독립 충격으로 세면 잘못된 결론이 됩니다.',
      sourceKeys: ['fiscal', 'rrp', 'acm', 'finra', 'tic']
    };
  }

  const current = currentNarrative();
  const scenarios = [current, ...INTERPRETATION_COPY.scenarios];
  const section = node('section', 'interpretation');
  section.id = 'interpretation';
  section.setAttribute('aria-labelledby', 'interpretation-heading');
  const head = node('div', 'interpretation-head'), heading = node('div');
  heading.append(node('div', 'eyebrow', 'READING THE OBSERVATIONS'));
  const h2 = node('h2', '', '종합 해석');h2.id = 'interpretation-heading';heading.append(h2);
  head.append(heading, node('span', 'interpretation-badge', '관측 해석 · 방향 예측 아님'));section.append(head);
  section.append(node('p', 'interpretation-intro', '현재 자료를 먼저 읽고, 아래에서 조건이 달라졌을 때의 해석을 확인하세요. 유리·불리는 유동성 제약에 관한 설명입니다.'));
  const nav = node('nav', 'scenario-nav');nav.setAttribute('aria-label', '해석 시나리오 선택');
  const panel = node('div', 'scenario-panel');panel.id = 'scenario-panel';panel.setAttribute('aria-live', 'polite');panel.setAttribute('aria-atomic', 'true');
  const buttons = new Map();
  for (const item of scenarios) {
    const button = node('button', 'scenario-button', item.label);
    button.type = 'button';button.dataset.scenario = item.id;button.setAttribute('aria-controls', 'scenario-panel');
    button.addEventListener('click', () => render(item.id));buttons.set(item.id, button);nav.append(button);
  }
  section.append(nav, panel);
  section.append(node('p', 'interpretation-note', '시나리오는 가정별 설명이며, 현재 상황으로 자동 분류하지 않습니다. 유동성 증가만으로 가격 상승을 말할 수 없고, 방향 예측에는 유동성 자체의 예측이 먼저 필요합니다.'));
  grid.after(section);

  function render(id) {
    const scenario = scenarios.find(s => s.id === id);
    if (!scenario) throw Error('Unknown narrative scenario');
    for (const [key, button] of buttons) button.setAttribute('aria-pressed', String(key === id));
    panel.replaceChildren();
    panel.dataset.scenario = id;
    const tag = id === 'current'
      ? (complete ? (differentDates ? '스냅샷 관측 · 전체 판단 보류' : '스냅샷 관측 · 전달경로 확인 필요') : '관측 부족 · 판단 보류')
      : '가정 시나리오 · 현재 판정 아님';
    panel.append(node('div', 'scenario-tag', tag), node('h3', '', scenario.title), node('p', 'scenario-condition', scenario.when));
    if (id === 'current') {
      const dates = node('div', 'observation-dates');
      for (const f of facts) dates.append(node('span', '', `${f.title} ${f.date ? shortDate(f.date) : '미확인'}`));
      panel.append(dates);
    }
    const columns = node('div', 'interpretation-columns');
    for (const [label, text, cls] of [
      ['유리한 점', scenario.good, 'support'],
      ['부담·불확실성', scenario.bad, 'pressure'],
      ['다음 확인', scenario.watch, 'watch']
    ]) {
      const column = node('section', 'interpretation-column ' + cls);column.append(node('h4', '', label));
      for (const paragraph of text.split('\n\n')) column.append(node('p', '', paragraph));
      columns.append(column);
    }
    panel.append(columns);
    const caveats = node('div', 'scenario-caveats');
    for (const [title, text] of [['가정', scenario.assumption], ['해석이 깨지는 곳', scenario.breaks]]) {
      const item = node('p');item.append(node('strong', '', title + '  '), document.createTextNode(text));caveats.append(item);
    }
    panel.append(caveats);
    const details = node('details', 'interpretation-sources');details.append(node('summary', '', '해석 기준과 출처'));
    details.append(node('p', '', '아래 공식 계열 정의와 회계 경로를 바탕으로 작성한 조건부 해석입니다. 다섯 관측을 합산한 점수나 추정 모형의 결과가 아닙니다.'));
    const links = node('div', 'source-links');
    for (const key of scenario.sourceKeys) {
      const source = sourceLibrary[key];
      if (!source) throw Error('Unknown source');
      const link = node('a', '', source.title + ' ↗');link.href = source.url;link.target = '_blank';link.rel = 'noopener noreferrer';links.append(link);
    }
    details.append(links);panel.append(details);
    window.INTERPRETATION_READY = {selected: id, scenarioCount: scenarios.length, autoClassification: false,
      datesDiffer: differentDates, complete, facts, columns: ['유리한 점', '부담·불확실성', '다음 확인']};
  }
  render('current');
}
