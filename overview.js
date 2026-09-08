/* Native observations only. The hourly GitHub workflow supplies public JSON. */
(()=>{
'use strict';
const SOURCE_SPECS=[
 {id:'WRESBAL',slot:1,layer:1,provider:'fred',units:['Millions of U.S. Dollars'],frequency:'weekly',label:'주간 평균',native:'수요일 종료 주간평균',display:'조 USD',count:52},
 {id:'WTREGEN',slot:6,layer:1,provider:'fred',units:['Millions of U.S. Dollars'],frequency:'weekly',label:'주간 평균',native:'수요일 종료 주간평균',display:'십억 USD',count:52},
 {id:'NYFED_ACM_TP10_MONTHLY',slot:2,layer:2,provider:'funding_structure',units:['percent'],frequency:'monthly',label:'월말',native:'월말 관측',display:'% · 등락 bp',count:13},
 {id:'FINRA_MARGIN_DEBT',slot:3,layer:3,provider:'intermediary',units:['million USD'],frequency:'monthly',label:'월말',native:'최종 영업일 기준 월말 잔액',display:'조 USD',count:13},
 {id:'TIC_US_EQUITY_FOREIGN_NET_PURCHASES',slot:4,layer:4,provider:'terminal_flows',units:['million USD'],frequency:'monthly',label:'월간 흐름',native:'월간 순매수 흐름',display:'십억 USD',count:12}
];
const STATUS_LABELS={ok:'정상',retained:'이전 정상값',stale:'갱신 지연',mismatch:'최신값·이력 불일치',missing:'이력 미확보',partial:'일부 미확인'};
let busy=false,currentChartObjects=[],lastPayload=null,refreshTimer;
const root=typeof document==='undefined'?null:document.querySelector('.app');
function safeUrl(value){try{const u=new URL(value);return u.protocol==='https:'?u.href:null}catch{return null}}
function finite(value){return typeof value==='number'&&Number.isFinite(value)}
function validDate(value){return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString().slice(0,10)===value}
function kst(value){const d=new Date(value);if(!Number.isFinite(d.getTime()))return '시각 미확인';return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(d).replaceAll('-','.')+' KST'}
function originalNumber(value){if(typeof value==='string'&&/^[-+]?(?:\d+\.?\d*|\.\d+)$/.test(value))return Number(value);return finite(value)?value:null}
function makeData(live,history,now=Date.now()){
 if(!Array.isArray(live.observations)||!Array.isArray(history.series)||history.research_eligible!==false||history.vintage_policy!=='current_snapshot_not_historical_availability')throw Error('자료 형식을 확인할 수 없습니다.');
 const cutoff=Date.parse(history.generated_at),liveTime=Date.parse(live.generated_at);
 if(!Number.isFinite(cutoff)||!Number.isFinite(liveTime)||liveTime>cutoff||cutoff>now+300000)throw Error('자료의 수집시각을 확인할 수 없습니다.');
 if(live.github_run_url&&history.github_run_url&&live.github_run_url!==history.github_run_url)throw Error('서로 다른 실행의 자료가 응답했습니다. 다시 확인해 주세요.');
 const collectorDelayed=now-liveTime>3*3600000;
 const rows=SOURCE_SPECS.map(spec=>{
  const found=history.series.filter(s=>s.series_id===spec.id&&s.provider===spec.provider);
  const latest=live.observations.filter(s=>s.series_id===spec.id&&s.provider===spec.provider);
  const h=found.length===1?found[0]:null,r=latest.length===1?latest[0]:null;
  let points=[],status='missing',sourceUrl=null,knownBy=null,actualUnit=spec.units[0];
  const hTime=Date.parse(h?.known_by),rTime=Date.parse(r?.known_by);
  const shapeOk=h&&h.research_eligible===false&&spec.units.includes(h.unit)&&String(h.frequency).toLowerCase().includes(spec.frequency.slice(0,-2))&&Array.isArray(h.points)&&Number.isFinite(hTime)&&hTime<=cutoff;
  if(shapeOk){
   let previous='';let valid=true;
   for(const point of h.points){if(!validDate(point.date)||point.date<=previous||Date.parse(point.date)>cutoff||!(point.value===null||finite(point.value))){valid=false;break}previous=point.date;}
   if(valid){points=h.points.map(p=>({date:p.date,value:p.value}));while(points.length&&points.at(-1).value===null)points.pop();points=points.slice(-spec.count);sourceUrl=safeUrl(h.source_url);knownBy=h.known_by;actualUnit=h.unit;status=h.status==='ok'?'ok':h.status==='stale'?'stale':'retained';}
  }
  const latestDate=r?.period_end||r?.observation_date,latestValue=originalNumber(r?.value);
  const rowOk=r&&latestValue!==null&&validDate(latestDate)&&Date.parse(latestDate)<=cutoff&&Number.isFinite(rTime)&&rTime<=cutoff&&spec.units.includes(r.unit);
  if(!points.length&&rowOk){points=[{date:latestDate,value:latestValue}];sourceUrl=safeUrl(r.source_url);knownBy=r.known_by;actualUnit=r.unit;status='missing';}
  const last=points.at(-1);
  if(status==='ok'&&(!rowOk||!last||last.date!==latestDate||Math.abs(last.value-latestValue)>Math.max(1e-9,Math.abs(last.value)*1e-10)))status='mismatch';
  if(status==='ok'&&r.status!=='ok')status=r.status==='stale'?'stale':'retained';
  if(status==='ok'&&last&&(now-Date.parse(last.date))/86400000>(spec.frequency==='weekly'?21:100))status='stale';
  return {id:spec.id,slot:spec.slot,layer:spec.layer,provider:spec.provider,unit:actualUnit,frequency:spec.frequency,frequency_label:spec.label,points,source_url:sourceUrl,known_by:knownBy,checked_at:h?.checked_at,raw_sha256:h?.raw_sha256,source_status:status,status_label:STATUS_LABELS[status],interpretation_eligible:status==='ok'&&points.length>=2&&!collectorDelayed,research_eligible:false};
 });
 return {captured_at:history.generated_at,live_generated_at:live.generated_at,series:rows,research_eligible:false,collectorDelayed,run_url:safeUrl(live.github_run_url),model_readiness:live.model_readiness};
}
async function fetchJson(path,nonce){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);try{const response=await fetch(path+'?t='+nonce,{cache:'no-store',credentials:'omit',signal:controller.signal});if(!response.ok)throw Error('HTTP '+response.status);return await response.json()}finally{clearTimeout(timer)}}
function showError(message){let banner=document.getElementById('connection-banner');if(!banner){banner=document.createElement('div');banner.id='connection-banner';banner.className='connection-message';root.prepend(banner)}banner.hidden=false;banner.textContent=message;banner.setAttribute('role','status');}
async function load(){
 if(busy)return;busy=true;const button=document.querySelector('.refresh-data');if(button)button.disabled=true;
 try{
  const nonce=Date.now();const [live,history,copy]=await Promise.all([fetchJson('./live-data.json',nonce),fetchJson('./chart-data.json',nonce),fetchJson('./interpretation-copy.json',nonce)]);
  if(!Array.isArray(copy.scenarios)||copy.scenarios.length!==7)throw Error('시나리오 문구를 확인할 수 없습니다.');
  const DATA=makeData(live,history);
  const previousScenario=window.INTERPRETATION_READY?.selected||'current';
  currentChartObjects.forEach(c=>c.destroy());currentChartObjects=[];root.replaceChildren();
  render(DATA,copy);
  if(previousScenario!=='current')document.querySelector('.scenario-button[data-scenario="'+previousScenario+'"]')?.click();
  lastPayload=DATA;
  window.DASHBOARD_ERROR=false;
  window.DASHBOARD_READY={cards:5,charts:currentChartObjects.length,scenarios:copy.scenarios.length+1,liveGeneratedAt:live.generated_at,historyGeneratedAt:history.generated_at,collectorDelayed:DATA.collectorDelayed,sourceStates:DATA.series.map(s=>({id:s.id,status:s.source_status,points:s.points.length,latest:s.points.at(-1)})),autoRefreshMinutes:5};
 }catch(error){showError(lastPayload?'자료 확인에 실패해 이전 화면을 유지합니다. 마지막 수집 '+kst(lastPayload.live_generated_at)+'. 다시 확인하거나 전체 자료의 실행 기록을 확인하세요.':'자료를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요. '+(error.name==='AbortError'?'요청 시간이 초과되었습니다.':error.message));window.DASHBOARD_ERROR=true;
 }finally{busy=false;document.querySelectorAll('.refresh-data').forEach(b=>b.disabled=false)}
}
function render(DATA,INTERPRETATION_COPY){
 const FINAL_METADATA={captured_kst:kst(DATA.live_generated_at),labels:Object.fromEntries(SOURCE_SPECS.map(s=>[s.id,{frequency:s.native,display_unit:s.display}]))};
/* Render the current published observations without fitting a model. */
const configs={
 a:{eyebrow:'FIVE LAYERS / ONE VIEW',title:'다섯 축으로 보는 유동성',description:'창출부터 크립토까지, 각 층의 대표 관측을 한 화면에.',name:'A · 균등 카드형',order:[1,2,3,4,5]},
 b:{eyebrow:'LIQUIDITY / OBSERVATORY',title:'창출과 전환을 중심으로',description:'위쪽에서 원천과 전환 관측을, 아래쪽에서 분포와 플로우를.',name:'B · 2 + 3 집중형',order:[1,3,2,4,5]},
 c:{eyebrow:'THE LIQUIDITY NOTE',title:'흐름을 읽는 다섯 줄',description:'대표 관측의 궤적을 길게 펼쳐 봅니다.',name:'C · 가로 스트립형',order:[1,2,3,4,5]}
};
const variant=document.body.dataset.variant||'a',config=configs[variant];
const treasuryOverview=document.body.dataset.overview==='treasury';
if(treasuryOverview){Object.assign(config,{title:'유동성의 다섯 관측면',description:'Fed와 재무부의 현금, 분포·전환·플로우를 한 화면에서.',name:'B · 재무부 수정안',order:[1,6,2,3,4]})}
const design={
 1:{layer:'창출 · 스톡',title:'Fed 지급준비금',subtitle:'미국 연방준비제도',divisor:1e6,unit:'조 USD',digits:3,type:'line'},
 2:{layer:'분포',title:'ACM 기간 프리미엄',subtitle:'미국 국채 10년물',divisor:1,unit:'%',digits:3,type:'line'},
 3:{layer:'전환 관측',title:'FINRA 마진부채',subtitle:'보고 회원사 고객 계좌',divisor:1e6,unit:'조 USD',digits:3,type:'line'},
 4:{layer:'종단 플로우',title:'외국인 주식 순매수',subtitle:'TIC · 미국 주식',divisor:1e3,unit:'십억 USD',digits:1,type:'bar'},
 5:{layer:'크립토',title:'BTC ETF 순유입',subtitle:'미국 현물 ETF · Farside',divisor:1,unit:'백만 USD',digits:1,type:'bar'},
 6:{layer:'창출 · 재무부',title:'TGA 잔액',subtitle:'미국 재무부 · 주간 평균',divisor:1e3,unit:'십억 USD',digits:1,type:'line',layerNumber:1}
};
const colors=variant==='b'?['#77c6af','#9fafe1','#bbace0','#c4cab5','#d4b77d']:variant==='c'?['#507563','#737f96','#8b7e91','#627f8c','#af966e']:['#258978','#6d85b7','#937cad','#628c9e','#ba975b'];
const series=new Map(DATA.series.map(s=>[s.slot||s.layer,s]));
const seriesColor=slot=>colors[slot===6?0:slot-1];
const featuredSlots=treasuryOverview?[1,6]:[1,3];
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function month(date){return date.slice(0,7).replace('-','.')}
function shortDate(date){return date.replaceAll('-','.')}
function valueText(x,spec){return (spec.type==='bar'&&x>0?'+':'')+x.toLocaleString('en-US',{minimumFractionDigits:spec.digits,maximumFractionDigits:spec.digits})}
function observedChange(s,spec,points,index){
 const current=points[index],previous=points[index-1];
 const validDate=date=>typeof date==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(date)&&Number.isFinite(Date.parse(date))&&new Date(date).toISOString().slice(0,10)===date;
 if(!current||!previous||!Number.isFinite(current.value)||!Number.isFinite(previous.value)||!validDate(current.date)||!validDate(previous.date)||Date.parse(previous.date)>=Date.parse(current.date))return {direction:'flat',label:'비교 관측 확인 필요',text:'—',detail:'비교 가능한 직전 관측값과 날짜를 확인할 수 없어 증감을 계산하지 않았습니다.'};
 const rawDelta=current.value-previous.value,delta=spec.unit==='%'?rawDelta*100:rawDelta/1e3;
 const unit=spec.unit==='%'?'bp':'십억 USD',direction=rawDelta>0?'up':rawDelta<0?'down':'flat';
 const signed=(value,digits)=>(value>0?'+':value<0?'−':'')+Math.abs(value).toLocaleString('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits});
 const percent=spec.type==='line'&&spec.unit!=='%'&&previous.value>0?rawDelta/previous.value*100:null;
 const months=(Number(current.date.slice(0,4))-Number(previous.date.slice(0,4)))*12+Number(current.date.slice(5,7))-Number(previous.date.slice(5,7));
 const days=(Date.parse(current.date)-Date.parse(previous.date))/86400000;
 const frequency=s.frequency.toLowerCase();
 const label=frequency.includes('week')&&days===7?'전주 대비':frequency.includes('month')&&months===1?'전월 대비':'직전 관측 대비';
 const text=(direction==='up'?'▲ ':direction==='down'?'▼ ':'— ')+signed(delta,1)+' '+unit+(percent===null?'':' ('+signed(percent,2)+'%)');
 return {direction,label,text,delta,unit,percent,from:previous.date,to:current.date,detail:`${shortDate(previous.date)} → ${shortDate(current.date)} · ${label} ${text}. 관측값 자체의 증감입니다.`};
}
const main=document.querySelector('.app');
const header=node('header','topbar');const brand=node('a','brand');brand.href='#top';brand.setAttribute('aria-label','유동성 관측실 맨 위로');const mark=node('span','brand-mark');mark.setAttribute('aria-hidden','true');for(let i=0;i<3;i++)mark.append(node('i'));brand.append(mark,node('span','','유동성 관측실'));
const nav=node('nav','topnav');nav.setAttribute('aria-label','페이지 탐색');for(const [href,label,cls] of [['#overview','관측',''],['#interpretation','종합 해석',''],['#data-guide','자료 안내',''],['./details.html','전체 자료 ↗','detail-link']]){const a=node('a',cls,label);a.href=href;nav.append(a)}nav.append(node('span','draft','매시간 수집'));header.append(brand,nav);main.append(header);
const hero=node('section','hero');const copy=node('div');copy.append(node('div','eyebrow',config.eyebrow),node('h1','',config.title),node('p','',config.description));const snapshot=node('div','snapshot');snapshot.append(node('strong','','클라우드 수집 자료'),node('span','',FINAL_METADATA.captured_kst));const refresh=node('button','refresh-data','↻ 새로 확인');refresh.type='button';refresh.addEventListener('click',load);snapshot.append(node('br'),refresh,node('span','refresh-time','화면 확인 '+kst(Date.now())));if(DATA.run_url){const run=node('a','run-evidence','실행 기록 ↗');run.href=DATA.run_url;run.target='_blank';run.rel='noopener noreferrer';snapshot.append(run)};hero.append(copy,snapshot);main.append(hero);const banner=node('div','connection-message');banner.id='connection-banner';banner.setAttribute('role','status');const problems=DATA.series.filter(s=>s.source_status!=='ok');banner.hidden=!problems.length&&!DATA.collectorDelayed;banner.textContent=(DATA.collectorDelayed?'클라우드 수집이 3시간 이상 지연되었습니다. ':'')+problems.map(s=>s.id+' · '+s.status_label).join(' / ')+(problems.length?' — 기준일과 원 수집시각을 유지하며 현재 종합 해석을 보류합니다.':'');main.append(banner);
const grid=node('section','axis-grid');grid.setAttribute('aria-label',treasuryOverview?'Fed, 재무부, 분포, 전환과 종단 플로우 대표 관측 그래프':'5개 층의 대표 관측 그래프');grid.id='overview';main.append(grid);
const charts=[];
for(const layer of config.order){
 const s=series.get(layer),spec=design[layer],points=s.points;

 const last=points.at(-1)||{date:null,value:null},vals=points.map(p=>finite(p.value)?p.value/spec.divisor:null),latest=vals.at(-1),card=node('article','axis-card'+(layer===5?' crypto':'')+(layer===6?' treasury':'')+(variant==='b'&&featuredSlots.includes(layer)?' featured':''));card.style.setProperty('--series',seriesColor(layer));
 const top=node('div','axis-top');const tag=node('span','axis-label');tag.append(node('span','layer-number','0'+(spec.layerNumber||layer)),node('span','',spec.layer));top.append(tag,node('span','frequency',s.frequency_label||s.frequency));
 const intro=node('div','card-intro'),identity=node('div','identity');identity.append(top,node('h2','',spec.title),node('p','sub',spec.subtitle));const readingWrap=node('div','reading-wrap');const reading=node('div','reading');reading.append(node('span','value',finite(latest)?valueText(latest,spec):'—'),node('span','unit',spec.unit));readingWrap.append(reading,node('div','reference',last.date?shortDate(last.date):'기준일 미확인'));
 const change=observedChange(s,spec,points,points.length-1),changeRow=node('div','change-row');
 changeRow.append(node('span','change-period',change.label),node('span','change-value '+change.direction,change.text));
 changeRow.title=change.detail;changeRow.setAttribute('aria-label',change.detail);changeRow.dataset.series=s.id;
 const plot=node('div','plot'),canvas=node('canvas');canvas.setAttribute('role','img');canvas.setAttribute('aria-label',`${spec.title}. ${s.frequency_label||s.frequency}, ${points[0]?.date||'미확인'}부터 ${last.date||'미확인'}까지 ${points.length}개 실제 관측. 단위 ${spec.unit}.`);plot.append(canvas);
 const bottom=node('div','axis-bottom');bottom.append(node('span','period',points.length?month(points[0].date)+' — '+month(last.date):'관측 미확보'),node('span',layer===5?'crypto-tag':'',layer===5?'별도 트랙':points.length+'개 관측'));
 if(variant==='c'){intro.append(identity);const aside=node('div','strip-aside');aside.append(readingWrap,bottom);card.append(intro,plot,aside)}
 else if(variant==='b'){intro.append(identity,readingWrap);card.append(intro,plot,bottom)}
 else{intro.append(identity,readingWrap);card.append(intro,plot,bottom)}
 if(treasuryOverview)card.insertBefore(changeRow,plot);
 const sourceState=node('span','source-state '+s.source_status,s.status_label);top.append(sourceState);grid.append(card);if(points.length<2){plot.replaceChildren(node('div','plot-unavailable',points.length?'관측 이력을 확인하고 있습니다.':'원자료를 확보하지 못했습니다.'));continue;}
 const dark=variant==='b',strip=variant==='c';
 const chart=new Chart(canvas,{type:spec.type,data:{labels:points.map(p=>p.date),datasets:[{label:spec.title,data:vals,borderColor:seriesColor(layer),backgroundColor:spec.type==='bar'?vals.map(v=>v<0?(dark?'#52616c':'#b9c2c7'):seriesColor(layer)+'cb'):seriesColor(layer)+(dark?'0e':'08'),borderWidth:spec.type==='bar'?0:1.8,borderRadius:spec.type==='bar'?2:0,fill:spec.type==='line'&&!strip,tension:0,pointRadius:context=>spec.type==='line'&&context.dataIndex===points.length-1?2.2:0,pointHitRadius:12,pointHoverRadius:3,spanGaps:false,barPercentage:.68,categoryPercentage:.9}]},options:{responsive:true,maintainAspectRatio:false,animation:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{displayColors:false,backgroundColor:dark?'#34434d':'#263c45',padding:10,titleFont:{size:11},bodyFont:{size:12},callbacks:{title:items=>items[0].label,label:context=>context.parsed.y.toFixed(spec.digits)+' '+spec.unit,afterLabel:context=>treasuryOverview&&context.dataIndex>0?(()=>{const c=observedChange(s,spec,points,context.dataIndex);return c.label+' '+c.text})():''}}},layout:{padding:{top:6,right:strip?5:0,bottom:0,left:0}},scales:{x:{display:false,grid:{display:false}},y:{display:true,position:'right',beginAtZero:spec.type==='bar',border:{display:false},ticks:{display:!strip,maxTicksLimit:3,color:dark?'#758998':'#a0aab1',font:{size:9},padding:4,callback:v=>Math.abs(v)>=100?Number(v).toFixed(0):Number(v).toFixed(spec.digits===3?1:0)},grid:{display:true,color:context=>context.tick.value===0?(dark?'#52616c':'#cbd3d7'):(dark?'#26343f':'#e8ecee'),drawTicks:false,lineWidth:context=>context.tick.value===0?1:0.6}}}}});charts.push(chart);
}
const context=node('section','context-strip');for(const pair of [['대표 관측 5개','같은 크기, 각자의 단위와 관측기간'],['공통 점수로 합산하지 않음',treasuryOverview?'재무부는 1층의 별도 관측':'층별 구조와 크립토 트랙 유지'],['상세 관측으로 이어집니다','아래에서 전체 계열·연결 상태 확인']]){const n=node('div');n.append(node('strong','',pair[0]),node('span','',pair[1]));context.append(n)}main.append(context);
const footer=node('footer','footer');footer.append(node('div','notice',treasuryOverview?'가정: 현재 빈티지의 원자료이며 단위·기간은 서로 다릅니다. 등락은 직전 관측 대비 변화입니다. 한계: TGA는 재무부의 일부이며, Lₜ·Aₜ는 미추정입니다.':'가정: 각 층의 대표 관측 하나를 선택한 시안입니다. 한계: 전체 층을 대변하지 않으며, Lₜ·Aₜ는 미추정입니다. 단위·기간은 그래프마다 다릅니다.'),node('span','concept-label','LIQUIDITY OBSERVATORY'));main.append(footer);
window.PREVIEW_READY={variant,overview:treasuryOverview?'treasury':'five-layers',chartCount:charts.length,slots:config.order,layers:config.order.map(slot=>series.get(slot).layer),points:DATA.series.map(s=>({id:s.id,slot:s.slot||s.layer,layer:s.layer,count:s.points.length,latest:s.points.at(-1)||null}))};

currentChartObjects=charts;window.mountLiquidityInterpretation({treasuryOverview,DATA,config,series,design,observedChange,shortDate,node,grid,INTERPRETATION_COPY});
/* Provenance and navigation for the self-contained final design artifact. */
const guide=node('section','data-guide');guide.id='data-guide';guide.setAttribute('aria-labelledby','data-guide-heading');
const guideHead=node('div','data-guide-head'),guideTitle=node('h2','','자료 안내');guideTitle.id='data-guide-heading';
guideHead.append(guideTitle,node('span','data-guide-status','저장 시각 '+FINAL_METADATA.captured_kst));guide.append(guideHead);
guide.append(node('p','','각 지표는 고유한 관측기간과 발표주기를 갖습니다. GitHub에서 매시간 API를 확인하며 이 화면은 5분마다 새 자료를 확인합니다. 원천 발표가 있어야 관측값이 바뀝니다.'));
const tableWrap=node('div','data-table-wrap'),table=node('table'),thead=node('thead'),headerRow=node('tr');
for(const label of ['대표 관측','기준일','관측 방식','표시 단위','원자료']){const cell=node('th','',label);cell.scope='col';headerRow.append(cell)}thead.append(headerRow);table.append(thead);
const tbody=node('tbody');
for(const slot of config.order){
 const s=series.get(slot),meta=FINAL_METADATA.labels[s.id],row=node('tr'),name=node('th','',design[slot].title);name.scope='row';if(s.source_status!=='ok')name.append(node('span','source-state '+s.source_status,s.status_label));row.append(name);
 for(const [label,value] of [['기준일',s.points.length?shortDate(s.points.at(-1).date):'미확보'],['관측 방식',meta.frequency],['표시 단위',meta.display_unit]]){const td=node('td','',value);td.dataset.label=label;row.append(td)}
 const sourceCell=node('td');sourceCell.dataset.label='원자료';const source=node('a','','원천 확인 ↗');if(s.source_url)source.href=s.source_url;else source.textContent='원천 미확인';source.target='_blank';source.rel='noopener noreferrer';sourceCell.append(source);row.append(sourceCell);tbody.append(row);
}
table.append(tbody);tableWrap.append(table);guide.append(tableWrap);
const links=node('div','deployment-links');for(const [href,label] of [['./details.html','전체 관측·연결 상태 ↗'],['./docs/OVERVIEW_SOURCE.md','화면의 가정과 한계 ↗']]){const a=node('a','',label);a.href=href;links.append(a)}guide.append(links);const guideNote=node('div','data-guide-note');guideNote.append(node('span','','단위 환산만 적용 · 원빈도 유지'),node('span','','현재 빈티지 · 과거 실시간 검증에 사용 불가'),node('span','','Lₜ·Aₜ 미추정'));guide.append(guideNote);
document.querySelector('.footer').before(guide);
const anchors=[...document.querySelectorAll('.topnav a')];
for(const anchor of anchors){anchor.addEventListener('click',()=>{for(const a of anchors)a.removeAttribute('aria-current');anchor.setAttribute('aria-current','location')})}
anchors[0]?.setAttribute('aria-current','location');

}
if(typeof module==='object'&&module.exports){module.exports={makeData,validDate,originalNumber};return;}
window.LIQUIDITY_UI_CONTRACT={makeData};
load();refreshTimer=setInterval(load,5*60*1000);
})();
