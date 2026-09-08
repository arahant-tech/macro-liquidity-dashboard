/* Native observations only. No global index, imputation, or fitted trend. */
(function(root){
  'use strict';
  const DAY=86400000;
  const SPECS={
    WRESBAL:{region:'UNITED STATES',short:'미국 · Fed',color:'#09816b',unit:'Millions of U.S. Dollars',scale:1e6,display:'조 USD'},
    WALCL:{region:'UNITED STATES',short:'미국 · Fed',color:'#09816b',unit:'Millions of U.S. Dollars',scale:1e6,display:'조 USD'},
    ECBASSETSW:{region:'EURO AREA',short:'유로지역 · ECB',color:'#417bbb',unit:'Millions of Euros',scale:1e6,display:'조 EUR'},
    JPNASSETS:{region:'JAPAN',short:'일본 · BOJ',color:'#8a69b1',unit:'100 Million Yen',scale:1e4,display:'조 JPY'},
    RRPONTSYD:{region:'UNITED STATES',short:'미국 · Fed',color:'#3877a2',unit:'Billions of US Dollars',scale:1e3,display:'조 USD'},
    WTREGEN:{region:'UNITED STATES',short:'미 재무부',color:'#a0783d',unit:'Millions of U.S. Dollars',scale:1e6,display:'조 USD'},
    PBOC_TOTAL_ASSETS:{region:'CHINA',short:'중국 · PBoC',color:'#b88436',unit:'100 million CNY',scale:1e4,display:'조 CNY'},
    PBOC_DEPOSITS_OTHER_DEPOSITORY_CORPORATIONS:{region:'CHINA',short:'중국 · PBoC',color:'#b88436',unit:'100 million CNY',scale:1e4,display:'조 CNY'},
    PBOC_TSF_STOCK:{region:'CHINA / CREDIT',short:'중국 · 신용',color:'#a87735',unit:'trillion CNY',scale:1,display:'조 CNY'},
    PBOC_TSF_FLOW:{region:'CHINA / CREDIT',short:'중국 · 신용',color:'#a87735',unit:'100 million CNY',scale:1e4,display:'조 CNY'}
  };
  const OVERVIEW=['WRESBAL','ECBASSETSW','JPNASSETS','PBOC_TOTAL_ASSETS'];
  function timestamp(value){if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return NaN;const n=Date.parse(value+'T00:00:00Z');return Number.isFinite(n)&&new Date(n).toISOString().slice(0,10)===value?n:NaN;}
  function validPoints(points){
    if(!Array.isArray(points)||points.length>2000)throw new Error('Invalid chart points');
    const dates=new Set();return points.map(p=>{const t=timestamp(p.date);if(!Number.isFinite(t)||dates.has(p.date)||(p.value!==null&&(typeof p.value!=='number'||!Number.isFinite(p.value))))throw new Error('Invalid chart observation');dates.add(p.date);return {date:p.date,value:p.value,t};}).sort((a,b)=>a.t-b.t);
  }
  function selectPoints(series,days,asOf){
    const points=validPoints(series.points),end=Date.parse(asOf);if(!Number.isFinite(end))throw new Error('Invalid snapshot date');
    if(points.some(p=>p.t>end))throw new Error('Future chart date');
    return points.filter(p=>p.t>=end-days*DAY&&p.t<=end);
  }
  function unitFor(series){const spec=SPECS[series.series_id];return spec&&spec.unit===series.unit?{scale:spec.scale,label:spec.display}:{scale:1,label:series.unit||'단위 미확인'};}
  function geometry(points,width=1000,height=300,bars=false){
    const finite=points.filter(p=>p.value!==null);
    if(!finite.length)return null;
    const pad={left:12,right:72,top:18,bottom:34};
    let x0=points[0].t,x1=points[points.length-1].t;if(x0===x1){x0-=DAY;x1+=DAY;}
    let lo=Math.min(...finite.map(p=>p.value)),hi=Math.max(...finite.map(p=>p.value));
    if(bars){lo=Math.min(0,lo);hi=Math.max(0,hi);}
    const spread=hi-lo||Math.abs(hi)*.04||1;lo-=spread*.12;hi+=spread*.12;
    const x=t=>pad.left+(t-x0)/(x1-x0)*(width-pad.left-pad.right),y=v=>height-pad.bottom-(v-lo)/(hi-lo)*(height-pad.top-pad.bottom);
    // Stop a line at every explicit missing observation. Never interpolate.
    const segments=[];let segment=[];
    points.forEach(p=>{if(p.value===null){if(segment.length)segments.push(segment);segment=[];}else segment.push({...p,x:x(p.t),y:y(p.value)});});if(segment.length)segments.push(segment);
    return {width,height,pad,x0,x1,lo,hi,x,y,segments,finite,base:y(0)};
  }
  function chartStatus(row,health={},now=Date.now()){
    if(health.failed)return ['retained','이전 이력 · 갱신 실패'];
    if(health.generatedAt&&now-Date.parse(health.generatedAt)>3*3600000)return ['stale','이력 갱신 지연'];
    return row.status==='retained'?['retained','이전 이력']:row.status==='stale'?['stale','관측 지연']:['ok','원자료'];
  }
  const exports={timestamp,validPoints,selectPoints,unitFor,geometry,chartStatus,SPECS};
  if(typeof module!=='undefined'&&module.exports)module.exports=exports;
  if(!root.document)return;
  const doc=root.document,$=id=>doc.getElementById(id),SVG='http://www.w3.org/2000/svg';
  const state={data:null,selected:'WRESBAL',days:365,loading:false,fetchFailed:false};
  function node(tag,cls,text){const el=doc.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=String(text);return el;}
  function svg(tag,attrs,text){const el=doc.createElementNS(SVG,tag);Object.entries(attrs||{}).forEach(([k,v])=>el.setAttribute(k,String(v)));if(text!==undefined)el.textContent=String(text);return el;}
  function fmt(value,digits=3){return new Intl.NumberFormat('en-US',{maximumFractionDigits:digits}).format(value);}
  function shortDate(date){return date.replaceAll('-','.');}
  function knowledge(value){const d=new Date(value);return Number.isFinite(d.getTime())?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',dateStyle:'medium',timeStyle:'short'}).format(d):'미확인';}
  function linePath(segment){return segment.map((p,i)=>(i?'L':'M')+p.x.toFixed(2)+','+p.y.toFixed(2)).join(' ');}
  function status(row){return chartStatus(row,{failed:state.fetchFailed,generatedAt:state.data?.generated_at});}
  function setBadge(row){const [cls,text]=status(row);$('chart-status').className='badge '+cls;$('chart-status').textContent=text;}
  function mini(points,color){
    const el=svg('svg',{viewBox:'0 0 220 42',class:'mini-chart','aria-hidden':'true',preserveAspectRatio:'none'});
    const finite=points.filter(p=>p.value!==null);if(!finite.length)return el;
    const min=Math.min(...finite.map(p=>p.value)),max=Math.max(...finite.map(p=>p.value)),span=max-min||Math.abs(max)*.04||1;
    const first=points[0].t,last=points[points.length-1].t;
    let segment=[];const draw=()=>{if(!segment.length)return;if(segment.length===1)el.append(svg('circle',{cx:segment[0].x,cy:segment[0].y,r:2.4,fill:color}));else el.append(svg('path',{d:linePath(segment),fill:'none',stroke:color,'stroke-width':1.8,'vector-effect':'non-scaling-stroke'}));segment=[];};
    points.forEach(p=>{if(p.value===null)draw();else segment.push({x:first===last?110:3+(p.t-first)/(last-first)*214,y:35-(p.value-min)/span*28});});draw();return el;
  }
  function renderCards(){
    const available=state.data.series;
    $('overview-cards').replaceChildren(...OVERVIEW.map(id=>{
      const row=available.find(s=>s.series_id===id),spec=SPECS[id],button=node('button','overview-card');button.type='button';button.style.setProperty('--series-color',spec.color);button.setAttribute('aria-pressed',String(id===state.selected));button.setAttribute('aria-label',spec.short+' 그래프 보기');
      const top=node('div','overview-card-top'),key=node('span','series-key');key.append(node('i','series-dot'),node('span','',spec.short));top.append(key,node('span','','↗'));button.append(top,node('div','overview-card-label',row?.label||'이력 미확보'));
      const points=row?selectPoints(row,365,state.data.generated_at):[],values=points.filter(p=>p.value!==null),last=values.at(-1),unit=row?unitFor(row):null;
      if(last){const value=node('div','overview-card-value');value.append(node('strong','',fmt(last.value/unit.scale)),node('span','',unit.label));button.append(value,mini(points,spec.color));const dates=node('div','overview-card-date');dates.append(node('span','',shortDate(last.date)),node('span','',status(row)[0]==='ok'?(row.history_basis==='available_official_monthly_table'?'확보 '+values.length+'개월':'최근 1년'):status(row)[1]));button.append(dates);}
      else button.append(node('div','overview-card-missing','공식 이력을 아직 확보하지 못했습니다.'));
      button.addEventListener('click',()=>{state.selected=id;render();});return button;
    }));
  }
  function chartTable(row,points){
    const body=$('chart-data-table').querySelector('tbody');body.replaceChildren(...points.slice().reverse().map(p=>{const tr=node('tr');tr.append(node('td','',shortDate(p.date)),node('td','',p.value===null?'결측':fmt(p.value,8)+' '+row.unit));return tr;}));
    $('chart-provenance').textContent='이력 최초 확보: '+knowledge(row.known_by)+' KST · 최근 확인: '+knowledge(row.checked_at)+' KST. 과거 원발표시각을 인증한 자료가 아닙니다. 그래프는 원자료 수준이며 측정모형의 입력·진입 신호가 아닙니다.';
  }
  function drawChart(row,points){
    const container=$('chart-canvas');container.replaceChildren();
    const small=typeof root.matchMedia==='function'&&root.matchMedia('(max-width:760px)').matches;
    const width=small?520:1100,height=small?280:315,unit=unitFor(row),color=SPECS[row.series_id].color,bars=row.chart_type==='bar';
    const scaled=points.map(p=>({...p,value:p.value===null?null:p.value/unit.scale}));
    const g=geometry(scaled,width,height,bars);
    if(!g){container.append(node('p','chart-empty','이 기간에 확보한 관측이 없습니다. 기간을 넓혀 확인하세요.'));return;}
    const chart=svg('svg',{viewBox:`0 0 ${width} ${height}`,class:'main-chart',role:'img',tabindex:'0','aria-label':row.label+' '+unit.label+' 단독 축. 방향키로 각 원관측값 확인. 수치는 아래 표에서도 확인할 수 있습니다.'});
    chart.append(svg('title',{},row.label+' · 원자료의 현재 빈티지'));
    for(let i=0;i<5;i++){const v=g.lo+(g.hi-g.lo)*i/4,y=g.y(v);chart.append(svg('line',{x1:g.pad.left,x2:width-g.pad.right,y1:y,y2:y,class:'chart-grid'}),svg('text',{x:width-g.pad.right+12,y:y+4,class:'chart-axis'},fmt(v,Math.abs(g.hi-g.lo)<.1?4:Math.abs(g.hi-g.lo)<1?3:2)));}
    for(let i=0;i<4;i++){const t=g.x0+(g.x1-g.x0)*i/3,label=new Date(t).toISOString().slice(0,10);chart.append(svg('text',{x:g.x(t),y:height-6,class:'chart-axis','text-anchor':i===0?'start':i===3?'end':'middle'},(g.x1-g.x0>180*DAY?label.slice(0,7):label.slice(5)).replace('-','.')));}
    if(bars){chart.append(svg('line',{x1:g.pad.left,x2:width-g.pad.right,y1:g.base,y2:g.base,stroke:'#a7b5bf','stroke-width':1}));const gaps=scaled.slice(1).map((p,i)=>p.t-scaled[i].t).filter(x=>x>0),barWidth=Math.max(2,Math.min(32,(g.x(g.x0+Math.min(...gaps,30*DAY))-g.x(g.x0))*.65));g.segments.flat().forEach(p=>chart.append(svg('rect',{x:p.x-barWidth/2,y:Math.min(g.base,p.y),width:barWidth,height:Math.max(1,Math.abs(g.base-p.y)),fill:p.value<0?'#b0606d':color,rx:1,opacity:.8})));}
    else{g.segments.forEach(segment=>{if(segment.length>1){const bottom=height-g.pad.bottom;chart.append(svg('path',{d:linePath(segment)+` L${segment.at(-1).x},${bottom} L${segment[0].x},${bottom} Z`,fill:color,opacity:.055}),svg('path',{d:linePath(segment),fill:'none',stroke:color,'stroke-width':2.3,'stroke-linecap':'round','stroke-linejoin':'round','vector-effect':'non-scaling-stroke'}));}else chart.append(svg('circle',{cx:segment[0].x,cy:segment[0].y,r:3,fill:color}));});const last=g.segments.at(-1)?.at(-1);if(last)chart.append(svg('circle',{cx:last.x,cy:last.y,r:3.6,fill:color,stroke:'#fff','stroke-width':2}));}
    const guide=svg('line',{y1:g.pad.top,y2:height-g.pad.bottom,class:'chart-guide',visibility:'hidden'}),dot=svg('circle',{r:4,fill:color,stroke:'#fff','stroke-width':2,visibility:'hidden'});chart.append(guide,dot);
    const tooltip=node('div','chart-tooltip hidden');tooltip.setAttribute('aria-live','polite');let selected=points.length-1;
    const show=index=>{selected=Math.max(0,Math.min(points.length-1,index));const p=scaled[selected],x=g.x(p.t);guide.setAttribute('x1',x);guide.setAttribute('x2',x);guide.setAttribute('visibility','visible');dot.setAttribute('visibility',p.value===null?'hidden':'visible');if(p.value!==null){dot.setAttribute('cx',x);dot.setAttribute('cy',g.y(p.value));}tooltip.replaceChildren(node('div','',shortDate(p.date)),node('strong','',p.value===null?'결측':fmt(p.value,5)+' '+unit.label),node('span','',row.frequency));tooltip.classList.remove('hidden');const rect=container.getBoundingClientRect();tooltip.style.left=Math.max(0,Math.min(rect.width-190,x/width*rect.width+12))+'px';tooltip.style.top='12px';};
    chart.addEventListener('pointermove',event=>{const rect=chart.getBoundingClientRect(),x=(event.clientX-rect.left)/rect.width*width;let nearest=0;for(let i=1;i<scaled.length;i++)if(Math.abs(g.x(scaled[i].t)-x)<Math.abs(g.x(scaled[nearest].t)-x))nearest=i;show(nearest);});
    chart.addEventListener('pointerleave',()=>{guide.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');tooltip.classList.add('hidden');});chart.addEventListener('focus',()=>show(selected));chart.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();show(selected+(event.key==='ArrowLeft'?-1:1));}});chart.addEventListener('blur',()=>{guide.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');tooltip.classList.add('hidden');});
    container.append(chart,tooltip);
  }
  function render(){
    if(!state.data)return;
    renderCards();const row=state.data.series.find(s=>s.series_id===state.selected);
    const picker=$('chart-series');picker.replaceChildren(...state.data.series.map(s=>{const option=node('option','',s.label);option.value=s.series_id;return option;}));picker.value=state.selected;
    doc.querySelectorAll('[data-range]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.range)===state.days)));
    if(!row){$('chart-title').textContent='이력 미확보';$('chart-value').textContent='—';$('chart-unit').textContent='';$('chart-change').textContent='';$('chart-canvas').replaceChildren(node('p','chart-empty','이 원천의 관측 이력을 아직 확보하지 못했습니다. 다른 원천을 선택할 수 있습니다.'));$('chart-status').className='badge error';$('chart-status').textContent='미확보';$('chart-extent').textContent='연결 대기';$('chart-frequency').textContent='';$('chart-source').classList.add('hidden');$('chart-provenance').textContent='';$('chart-data-table').querySelector('tbody').replaceChildren();return;}
    const points=selectPoints(row,state.days,state.data.generated_at),finite=points.filter(p=>p.value!==null),last=finite.at(-1),first=finite[0],unit=unitFor(row);
    $('chart-region').textContent=SPECS[row.series_id].region;$('chart-title').textContent=row.label;setBadge(row);$('chart-value').textContent=last?fmt(last.value/unit.scale):'—';$('chart-unit').textContent=unit.label;
    $('chart-change').textContent=first&&last&&first!==last?row.chart_type==='bar'?'공시된 각 월의 흐름':first.value===0?'첫 관측이 0이므로 변화율 미표시':(last.value-first.value>=0?'+':'')+fmt((last.value/first.value-1)*100,2)+'% · 표시기간 첫 관측 대비':'이력을 축적하고 있습니다';
    $('chart-extent').textContent=first?shortDate(first.date)+' — '+shortDate(last.date)+' · '+finite.length+'개 관측':'이 기간의 관측 없음';
    $('chart-frequency').textContent=row.frequency.startsWith('Weekly')?'주간 · 원빈도':row.frequency.startsWith('Monthly')?'월간 · 원빈도':'일간 · 원빈도';
    const source=$('chart-source');try{const url=new URL(row.source_url);if(url.protocol!=='https:')throw Error();source.href=url.href;source.classList.remove('hidden');}catch{source.classList.add('hidden');}
    chartTable(row,points);drawChart(row,points);
  }
  async function refresh(){
    if(state.loading)return;state.loading=true;const controller=new AbortController(),timeout=root.setTimeout(()=>controller.abort(),15000);
    try{const response=await root.fetch('./chart-data.json?t='+Date.now(),{cache:'no-store',credentials:'omit',signal:controller.signal});if(!response.ok)throw Error('History unavailable');const data=await response.json();if(data.schema_version!==1||data.research_eligible!==false||data.vintage_policy!=='current_snapshot_not_historical_availability'||!Array.isArray(data.series)||data.series.length>Object.keys(SPECS).length)throw Error('History schema');const seen=new Set();data.series.forEach(s=>{if(!SPECS[s.series_id]||seen.has(s.series_id))throw Error('History identity');seen.add(s.series_id);selectPoints(s,1096,data.generated_at);});state.fetchFailed=false;state.data=data;render();}
    catch{state.fetchFailed=true;if(state.data){setBadge(state.data.series.find(s=>s.series_id===state.selected)||{});}else{$('chart-canvas').replaceChildren(node('p','chart-empty','관측 이력을 불러오지 못했습니다. 아래 최신 원자료는 별도로 확인할 수 있습니다.'));$('overview-cards').replaceChildren(node('p','chart-empty','그래프 연결을 확인하고 있습니다.'));$('chart-status').className='badge error';$('chart-status').textContent='연결 실패';}}
    finally{root.clearTimeout(timeout);state.loading=false;}
  }
  $('chart-series').addEventListener('change',event=>{state.selected=event.target.value;render();});
  doc.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>{state.days=Number(button.dataset.range);render();}));
  let resizeTimer;root.addEventListener('resize',()=>{root.clearTimeout(resizeTimer);resizeTimer=root.setTimeout(render,160);});
  root.LiquidityCharts={refresh};refresh();root.setInterval(refresh,5*60*1000);
})(typeof window==='undefined'?globalThis:window);
