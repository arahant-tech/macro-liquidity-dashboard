"""Daily official-source measurement update; no credentials, prices or fitted rules.

Each source is a separate transaction. Failed sources retain their verified snapshot.
The complete candidate must pass accounting/date/coverage checks before publication.
Raw HTTP bodies are archived as a workflow artifact for independent replay.
"""
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from urllib.parse import urlencode
import argparse, calendar, copy, csv, hashlib, html, io, json, math, os, re, subprocess, zipfile
from measurement import core_change, fund_window, LEAVES
from ici_contract import URLS, decode_report, validate_bundle

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc).isoformat()
TODAY = date.fromisoformat(NOW[:10])
RAW = ROOT / '.run/raw'
OFR = {k: 'MMF-MMF_' + v + '-M' for k,v in {
    'total':'TOT','treasury':'T_TOT','agency':'AG_TOT','bank':'BRA_TOT','assets':'OA_TOT',
    'repo':'RP_TOT','fed_repo':'RP_wFR','domestic':'RP_wDFI','foreign':'RP_wFFI','ficc':'RP_wFICC','counterparty':'RP_wOCP'}.items()}
CHANNELS = {'fed_repo':'연준 상대 레포','private_repo':'비연준 레포','treasury':'국채 직접보유','other':'기타'}
PD_KEYS = 'PDPOSGS-B_PDPOSGS-BFRN_PDPOSGSC-G11_PDPOSGSC-G11L21_PDPOSGSC-G21_PDPOSGSC-G2L3_PDPOSGSC-G3L6_PDPOSGSC-G6L7_PDPOSGSC-G7L11_PDPOSGSC-L2_PDPOSGST-TOT_PDPOSTIPS-G11_PDPOSTIPS-G2_PDPOSTIPS-G6L11_PDPOSTIPS-L2'

def require(ok, message):
    if not ok: raise ValueError(message)
def encoded(obj): return (json.dumps(obj,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode()
def digest(body): return hashlib.sha256(body).hexdigest()
def load(path): return json.loads(Path(path).read_text())
def write(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.new');temp.write_bytes(encoded(obj));temp.replace(path)
def monthend(day):
    d=date.fromisoformat(day[:10]);return d.replace(day=calendar.monthrange(d.year,d.month)[1]).isoformat()
def decimal(value):
    require(value is not None and str(value).strip() not in ('','.', 'ND', 'NA', 'null'), 'Missing official numeric observation')
    result=D(str(value));require(result.is_finite(),'Nonfinite observation');return result

class Source:
    def __init__(self, name): self.name=name;self.refs={}
    def get(self,url):
        require(url.startswith('https://'), 'HTTPS source required')
        result=subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time','45','--retry','1','--retry-delay','2',url],capture_output=True)
        require(result.returncode==0, f'공식 자료 수집 실패 (HTTP/연결 오류; {self.name})')
        body=result.stdout;require(len(body)>5,'Empty source response')
        sha=digest(body);RAW.mkdir(parents=True,exist_ok=True)
        (RAW/sha).write_bytes(body)
        self.refs[url]=sha
        return body
    def json(self,url): return json.loads(self.get(url),parse_float=D)
    def fred(self,series,start='2015-01-01',end=None):
        body=self.get('https://fred.stlouisfed.org/graph/fredgraph.csv?'+urlencode({'id':series,'cosd':start,'coed':end or TODAY.isoformat()}))
        reader=csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
        require(reader.fieldnames in [['DATE',series],['observation_date',series]],'FRED series/header mismatch')
        rows={}
        for row in reader:
            day=row[reader.fieldnames[0]];date.fromisoformat(day)
            if row[series] in ('','.'): continue
            require(day not in rows,'Duplicate FRED observation')
            if start<=day<=(end or TODAY.isoformat()): rows[day]=decimal(row[series])
        require(bool(rows),'FRED coverage empty')
        return rows

def core_rows(reserves,rrp):
    require(set(reserves)==set(rrp),'Reserve and RRP Wednesday calendars differ')
    rows=[]
    for day in sorted(reserves):
        require(date.fromisoformat(day).weekday()==2,'Expected Wednesday balance')
        r,p=reserves[day]/1000,rrp[day]/1000;s=r+p
        require(r>0 and p>=0 and s>0,'Invalid reserve pool')
        rows.append({'date':day,'R_bn':float(r),'P_bn':float(p),'S_bn':float(s),'p_bank':float(r/s),'p_rrp':float(p/s)})
    return rows

def collect_core(src, model):
    rows=core_rows(src.fred('WRBWFRBL'),src.fred('WLRRAOL'))
    require({r['date'] for r in model['core_weekly']} <= {r['date'] for r in rows},'Core history regressed')
    return {'core_weekly':rows}

def collect_funds(src,model):
    reports={k:decode_report(src.get(url),k,'text/html',TODAY.isoformat()) for k,url in URLS.items()}
    validate_bundle(reports)
    # Product pages may be published on different days for the same observation week.
    # Each release is validated separately; the accounting bundle must share week ends.
    require(reports['combined']['dates'][-1]>=model['fund_weekly'][-1]['date'],'ICI observation regressed')
    previous=model['sources'][1].get('release_date')
    require(not previous or reports['combined']['release_date']>=previous,'ICI release regressed')
    rows={r['date']:copy.deepcopy(r) for r in model['fund_weekly']}
    labels={'domestic_equity':'Domestic','world_equity':'World','hybrid':'Hybrid','bond':'Bond','commodity':'Commodity'}
    for day in reports['combined']['dates']:
        c,m,e=(reports[k]['bydate'][day] for k in URLS)
        rows[day]={'date':day,**{k+'_bn':c[v]/1000 for k,v in labels.items()},
                   'mutual_domestic_equity_bn':m['Domestic']/1000,'etf_domestic_equity_bn':e['Domestic']/1000,
                   'reported_total_bn':c['Total']/1000,'reported_equity_bn':c['Equity']/1000,
                   'source_snapshot_id':reports['combined']['release_date']+'-'+digest(encoded(src.refs))[:24],
                   'source_release_date':reports['combined']['release_date']}
    return {'fund_weekly':[rows[d] for d in sorted(rows)],'release_date':reports['combined']['release_date'],
            'product_release_dates':{k:r['release_date'] for k,r in reports.items()}}

def ofr_panel(body):
    series={}
    for name,mnemonic in OFR.items():
        item=body[mnemonic];meta=item['metadata']
        require(meta['unit']['name']=='USD' and meta['unit']['magnitude']==0,'OFR unit changed')
        require(meta['schedule']['observation_frequency']=='Monthly' and meta['schedule']['observation_period']=='Single Day','OFR frequency changed')
        obs=item['timeseries']['aggregation'];require([r[0] for r in obs]==sorted({r[0] for r in obs}),'OFR dates duplicate/unsorted')
        series[name]={d:None if v is None else decimal(v) for d,v in obs}
    panel={}
    for day,total in series['total'].items():
        if day<'2013-09-01':continue
        require(monthend(day)==day and day<=TODAY.isoformat(),'Invalid OFR month')
        v={k:values.get(day) for k,values in series.items()}
        require(all(v[k] is not None and v[k]>=0 for k in ['total','treasury','agency','bank','assets','repo']),'Missing OFR asset total')
        require(total==sum(v[k] for k in ['treasury','agency','bank','assets','repo']),'OFR asset identity failed')
        if v['fed_repo'] is None: continue
        require(0<=v['fed_repo']<=v['repo'],'OFR Fed repo invalid')
        cp=['fed_repo','domestic','foreign','ficc','counterparty']
        if all(v[k] is not None for k in cp):require(sum(v[k] for k in cp)==v['repo'],'OFR counterparties do not reconcile')
        panel[day]={'total':float(total/D(1e9)),'fed_repo':float(v['fed_repo']/D(1e9)),
                    'private_repo':float((v['repo']-v['fed_repo'])/D(1e9)),
                    'treasury':float(v['treasury']/D(1e9)),
                    'other':float((total-v['repo']-v['treasury'])/D(1e9))}
    return panel

def treasury_months(src,end):
    base='https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/public_debt_transactions'
    fields='record_date,transaction_type,security_market,security_type,security_type_desc,transaction_today_amt,transaction_mtd_amt,src_line_nbr'
    allrows=[];page=1
    while True:
        obj=src.json(base+'?'+urlencode({'filter':f'record_date:gte:2013-09-01,record_date:lte:{end},security_market:eq:Marketable','fields':fields,'sort':'record_date,src_line_nbr','page[size]':'10000','page[number]':str(page)}))
        require(obj['meta']['dataFormats']['transaction_today_amt']==obj['meta']['dataFormats']['transaction_mtd_amt']=='$1,000,000','DTS units changed')
        allrows.extend(obj['data'])
        require(page<=100,'DTS pagination excessive')
        if page>=obj['meta']['total-pages']:break
        page+=1
    require(len(allrows)==obj['meta']['total-count'],'Incomplete DTS pagination')
    categories=[('Issues','Bills','Regular Series'),('Issues','Bills','Cash Management Series'),('Issues','Notes','null'),('Issues','Bonds','null'),('Redemptions','Bills','null'),('Redemptions','Notes','null'),('Redemptions','Bonds','null')]
    keyed={};months=defaultdict(set)
    for row in allrows:
        key=(row['record_date'],row['transaction_type'],row['security_type'],row['security_type_desc'])
        require(key not in keyed and row['security_market']=='Marketable','Duplicate/unexpected DTS category')
        keyed[key]=row;months[key[0][:7]].add(key[0])
    result={}
    for month,dates in sorted(months.items()):
        last=max(dates);endday=monthend(last)
        require(len(dates)>=10 and (date.fromisoformat(endday)-date.fromisoformat(last)).days<=6,'Incomplete DTS month')
        amounts=[]
        for cat in categories:
            require(all((day,*cat) in keyed for day in dates),'DTS category missing')
            mtd=decimal(keyed[(last,*cat)]['transaction_mtd_amt'])
            total=sum(decimal(keyed[(day,*cat)]['transaction_today_amt']) for day in dates)
            require(abs(mtd-total)<=D(len(dates)+1)/2,'DTS monthly total exceeds rounding tolerance')
            amounts.append(mtd)
        result[endday]={'bill_net_bn':float((amounts[0]+amounts[1]-amounts[4])/1000),
                        'coupon_net_bn':float((amounts[2]+amounts[3]-amounts[5]-amounts[6])/1000)}
    return result

def dealers(src,end):
    rows=src.json('https://markets.newyorkfed.org/api/pd/get/'+PD_KEYS+'.json')['pd']['timeseries']
    bydate=defaultdict(dict)
    for row in rows:
        d,k=row['asofdate'],row['keyid']
        require(k not in bydate[d],'Duplicate dealer observation')
        bydate[d][k]=None if row['value'] in (None,'','*','NA') else decimal(row['value'])
    result={}
    for day,v in sorted(bydate.items()):
        if not '2013-09-01'<=day<=end:continue
        keys=['PDPOSGS-B','PDPOSGSC-L2','PDPOSGSC-G2L3','PDPOSGSC-G3L6','PDPOSGSC-G6L7','PDPOSGSC-G7L11','PDPOSTIPS-L2','PDPOSTIPS-G2','PDPOSTIPS-G6L11','PDPOSTIPS-G11']
        keys+=['PDPOSGSC-G11'] if day<'2022-01-05' else ['PDPOSGSC-G11L21','PDPOSGSC-G21']
        if day>='2015-01-07':keys+=['PDPOSGS-BFRN']
        require(all(v.get(k) is not None for k in keys+['PDPOSGST-TOT']),'Missing dealer component')
        require(abs(v['PDPOSGST-TOT']-sum(v[k] for k in keys))<=D(len(keys)+1)/2,'Dealer composition failed')
        result[day]=float(v['PDPOSGST-TOT']/1000)
    return result

def rate_spreads(src,end):
    tgcr=src.json('https://markets.newyorkfed.org/api/rates/secured/tgcr/search.json?'+urlencode({'startDate':'2018-04-02','endDate':end}))['refRates']
    ioer=src.fred('IOER','2018-04-02',end);iorb=src.fred('IORB','2021-07-29',end)
    months=defaultdict(list);seen=set()
    for row in tgcr:
        day=row['effectiveDate'];require(day not in seen and row['type']=='TGCR','Duplicate/unexpected TGCR observation');seen.add(day)
        if day>end:continue
        admin=(ioer if day<'2021-07-29' else iorb).get(day)
        if admin is None:continue
        months[monthend(day)].append(float(100*(decimal(row['percentRate'])-admin)))
    return {m:sum(v)/len(v) for m,v in months.items() if len(v)>=10}

def collect_monthly(src,model):
    body=src.json('https://data.financialresearch.gov/v1/series/multifull?'+urlencode({'mnemonics':','.join(OFR.values()),'start_date':'2013-09-01','end_date':TODAY.isoformat()}))
    panel=ofr_panel(body);require(bool(panel),'No complete MMF months')
    end=max(panel);require(end>=model['transmission']['end'],'MMF history regressed')
    # All three demand components must be valid; do not publish a mixed monthly bundle.
    treasury=treasury_months(src,end);pd=dealers(src,end);spreads=rate_spreads(src,end)
    positions={}
    for month in panel:
        ds=[d for d in pd if d<=month]
        require(bool(ds),'No preceding dealer observation')
        day=max(ds);require((date.fromisoformat(month)-date.fromisoformat(day)).days<=7,'Dealer month-end too old')
        positions[month]=(day,pd[day])
    rows=[]
    for end in sorted(panel):
        start=(date.fromisoformat(end).replace(day=1)-timedelta(days=1)).isoformat()
        if start not in panel:continue
        require(end in treasury,'Missing Treasury month')
        a,b=panel[start],panel[end];channels=[]
        for key,label in CHANNELS.items():
            w0,w1=a[key]/a['total'],b[key]/b['total']
            size=(w0+w1)/2*(b['total']-a['total']);allocation=(a['total']+b['total'])/2*(w1-w0)
            channels.append({'id':key,'label':label,'holding_bn':b[key],'share_pct':100*w1,'change_bn':b[key]-a[key],'size_effect_bn':size,'allocation_effect_bn':allocation,'share_change_pp':100*(w1-w0)})
        spread,prior=spreads.get(end),spreads.get(start)
        rows.append({'start':start,'end':end,'total_mmf_investments_bn':b['total'],'mmf_channels':channels,
                     'demand':{**treasury[end],'dealer_change_bn':positions[end][1]-positions[start][1],
                               'dealer_start_observation':positions[start][0],'dealer_end_observation':positions[end][0],
                               'spread_bp':spread,'spread_change_bp':spread-prior if spread is not None and prior is not None else None},'same_date_as_main':False})
    require({r['end'] for r in model['transmission_monthly']}<={r['end'] for r in rows},'MMF complete history shrank')
    return {'transmission_monthly':rows,'transmission':rows[-1]}

def z1_rows(zipbytes,contract,release,reference):
    with zipfile.ZipFile(io.BytesIO(zipbytes)) as bundle:
        # A changed table/sector definition requires review, never a silent remap.
        table=list(csv.reader(io.StringIO(bundle.read('csv/F51_1_t_tu.csv').decode())))
        dictionary=list(csv.reader(io.StringIO(bundle.read('data_dictionary/F51_1_t_tu.txt').decode()),delimiter='\t'))
    require([r[0] for r in dictionary]==table[0][1:],'Z.1 dictionary/header mismatch')
    expected=contract['series'];require(len(dictionary)==len(expected),'Z.1 sector taxonomy changed; review required')
    for actual,old in zip(dictionary,expected):
        require(actual[0]==old['fu_code'] and actual[1]==old['description'] and actual[4]==old['unit'],'Z.1 series definition changed; review required')
    out=[]
    for raw in table[1:]:
        if raw[0]<'1952:Q1':continue
        if all(v in ('','ND','NA','.') for v in raw[1:]):continue
        require(raw[0]<=reference,'Unreleased Z.1 quarter')
        year,q=map(int,raw[0].replace(':Q','-').split('-'));day=monthend(f'{year}-{q*3:02d}-01')
        v=[D(0)]+[decimal(x) for x in raw[1:]]
        require(v[1]==v[15],'Z.1 repeated total differs')
        for target,parts in [(1,[2,3,14]),(3,list(range(4,14))),(1,list(range(16,34)))]:
            require(abs(v[target]-sum(v[k] for k in parts))<=D(len(parts)+1)/2,'Z.1 accounting failed')
        acq={m['sector_key']:float(v[m['line']]/1000) for m in expected if m['role']=='acquirer'}
        issue={m['sector_key']:float(v[m['line']]/1000) for m in expected if m['role']=='issuer'}
        groups={'households':acq['households_nonprofits'],'foreign':acq['rest_of_world'],
                'pensions':sum(acq[k] for k in ['private_pensions','federal_pensions','state_local_pensions']),
                'insurers':acq['property_casualty_insurance']+acq['life_insurance'],
                'funds':sum(acq[k] for k in ['mutual_funds','closed_end_funds','exchange_traded_funds'])}
        groups['other']=sum(acq.values())-sum(groups.values())
        expressions={'all_sector_net_issuance_bn':v[1],'all_holder_net_acquisition_sum_bn':sum(v[16:34]),'domestic_net_issuance_bn':v[2]+v[3],
            'domestic_nonfund_net_issuance_bn':v[1]-v[14]-v[7]-v[8],'domestic_nonfund_net_issuance_alternative_bn':v[2]+v[3]-v[7]-v[8],
            'nonfinancial_corporate_net_issuance_bn':v[2],'financial_issuer_net_issuance_bn':v[3],'etf_share_net_issuance_bn':v[8],'cef_share_net_issuance_bn':v[7],
            'foreign_acquisition_us_equity_bn':v[33],'us_acquisition_foreign_equity_bn':v[14],'cross_border_portfolio_net_toward_us_bn':v[33]-v[14],
            'pension_acquisition_bn':sum(v[25:28]),'insurance_acquisition_bn':v[23]+v[24],'fund_acquisition_bn':sum(v[28:31]),'accounting_discrepancy_bn':sum(v[16:34])-v[1]}
        out.append({'date':day,'quarter':f'{year}Q{q}','grouped_acquisition_bn':groups,'acquisition_by_sector_bn':acq,'issuance_by_sector_bn':issue,'derived':{k:float(x/1000) for k,x in expressions.items()}})
    require(out and out[-1]['quarter']==reference.replace(':',''),'Z.1 current release quarter missing')
    return out

def collect_z1(src,model):
    text=html.unescape(re.sub('<[^>]*>',' ',src.get('https://www.federalreserve.gov/releases/z1/default.htm').decode()))
    match=re.search(r'Release Date:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})\s+(\d{4}:Q[1-4])\s+Release',text)
    require(match is not None,'Official actual release date unavailable')
    release=datetime.strptime(re.sub(r'\s+',' ',match[1]),'%B %d, %Y').date().isoformat()
    require(model['equity_sectors']['release_date']<=release<=TODAY.isoformat(),'Future/regressed Z.1 release')
    rows=z1_rows(src.get('https://www.federalreserve.gov/releases/z1/current/z1_csv_files.zip'),load(ROOT/'automation/z1-contract.json'),release,match[2])
    require({r['quarter'] for r in model['equity_sectors']['rows']}<={r['quarter'] for r in rows},'Z.1 history shrank')
    return {'equity_sectors':{**model['equity_sectors'],'release_date':release,'rows':rows},'release_date':release}

def rebuild_windows(model):
    funds={r['date']:r for r in model['fund_weekly']};core={r['date']:r for r in model['core_weekly']}
    model['fund_windows']={str(n):[fund_window(funds,core,n,d) for d in sorted(funds)] for n in (1,4)}
    eligible=[r for r in model['fund_windows']['4'] if r['available']]
    require(bool(eligible),'No complete 4-week fund/core window')
    model['primary']=eligible[-1];model['default_end']=eligible[-1]['end']

def validate_model(model,previous=None):
    encoded(model)  # Reject NaN/Infinity anywhere, including unused metadata.
    for key,daykey in [('core_weekly','date'),('fund_weekly','date'),('transmission_monthly','end')]:
        dates=[r[daykey] for r in model[key]]
        require(dates and dates==sorted(set(dates)) and dates[-1]<=TODAY.isoformat(),f'{key}: invalid dates')
        if previous:require({r[daykey] for r in previous[key]}<=set(dates),f'{key}: lost historical observations')
    for r in model['core_weekly']:
        require(r['R_bn']>0 and r['P_bn']>=0 and math.isclose(r['S_bn'],r['R_bn']+r['P_bn'],abs_tol=1e-8),'Core identity failed')
        require(math.isclose(r['p_bank'],r['R_bn']/r['S_bn'],abs_tol=1e-12) and math.isclose(r['p_bank']+r['p_rrp'],1,abs_tol=1e-12),'Core allocation failed')
    for r in model['fund_weekly']:
        require(date.fromisoformat(r['date']).weekday()==2 and all(math.isfinite(r[k+'_bn']) for k in LEAVES),'Invalid fund observation')
        require(abs(r['domestic_equity_bn']-r['mutual_domestic_equity_bn']-r['etf_domestic_equity_bn'])<=.001500001,'Fund product reconciliation failed')
    rebuilt=copy.deepcopy(model);rebuild_windows(rebuilt)
    require(rebuilt['fund_windows']==model['fund_windows'] and rebuilt['primary']==model['primary'],'Fund windows stale')
    for r in model['transmission_monthly']:
        require((date.fromisoformat(r['end']).replace(day=1)-timedelta(days=1)).isoformat()==r['start'],'Monthly window not adjacent')
        require({c['id'] for c in r['mmf_channels']}==set(CHANNELS),'Monthly taxonomy changed')
        require(math.isclose(sum(c['holding_bn'] for c in r['mmf_channels']),r['total_mmf_investments_bn'],abs_tol=1e-6),'MMF holdings do not sum')
        for c in r['mmf_channels']:
            require(c['holding_bn']>=0 and math.isclose(c['size_effect_bn']+c['allocation_effect_bn'],c['change_bn'],abs_tol=1e-7),'MMF decomposition failed')
        require(r['demand']['dealer_start_observation']<=r['start'] and r['demand']['dealer_end_observation']<=r['end'],'Future dealer observation')
    require(model['transmission']==model['transmission_monthly'][-1],'Latest monthly pointer stale')
    quarters=model['equity_sectors']['rows'];require(bool(quarters),'No Z.1 history')
    require(model['equity_sectors']['release_date']<=TODAY.isoformat(),'Z.1 release is in future')
    for i,r in enumerate(quarters):
        require(r['date']<=TODAY.isoformat() and abs(sum(r['acquisition_by_sector_bn'].values())-sum(r['issuance_by_sector_bn'].values()))<=.02,'Z.1 identity failed')
        require(abs(sum(r['grouped_acquisition_bn'].values())-sum(r['acquisition_by_sector_bn'].values()))<1e-7,'Z.1 grouped totals failed')
        if i:
            a=quarters[i-1]['quarter'];b=r['quarter']
            require(4*int(b[:4])+int(b[-1])-4*int(a[:4])-int(a[-1])==1,'Missing Z.1 quarter')
    return True

LAYERS=[('core',collect_core,0),('funds',collect_funds,1),('monthly',collect_monthly,2),('z1',collect_z1,3)]
def observation(model,key):
    return {'core':model['core_weekly'][-1]['date'],'funds':model['fund_weekly'][-1]['date'],'monthly':model['transmission']['end'],'z1':model['equity_sectors']['rows'][-1]['date']}[key]

def measurement_equal(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(measurement_equal(x,y) for x,y in zip(a,b))
    if isinstance(a,dict) and isinstance(b,dict):
        keys={k for k,v in a.items() if not k.startswith('source_') and isinstance(v,(int,float,dict,list))}
        return all(k in b and measurement_equal(a[k],b[k]) for k in keys)
    return a==b

def update(collectors=None):
    previous=load(ROOT/'live-model.json');validate_model(previous)
    prior=load(ROOT/'update-status.json') if (ROOT/'update-status.json').exists() else {}
    model=copy.deepcopy(previous);statuses=[]
    def collect(layer):
        key,fn,index=layer;src=Source(key)
        try: return layer,src,(collectors or {}).get(key,fn)(src,previous),None
        except Exception as exc:return layer,src,None,str(exc)[:200]
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(collect,LAYERS))
    for (key,fn,index),src,patch,error in results:
        oldcheck=next((s for s in prior.get('sources',[]) if s['id']==key),{})
        status={'id':key,'label':previous['sources'][index]['label'],'checked_at_utc':NOW,'state':'failed' if error else 'checked',
                'message':error,'last_successful_check_at_utc':oldcheck.get('last_successful_check_at_utc') if error else NOW,'raw_sha256':src.refs}
        if patch:
            release=patch.pop('release_date',None);product_releases=patch.pop('product_release_dates',None);model.update(patch)
            source=model['sources'][index]
            source.update(observation_date=observation(model,key),period_end=observation(model,key),retrieved_at=NOW,snapshot_generated_at=NOW,source_sha256=src.refs,
                          retrieval_note='Official HTTPS source checked by the daily workflow; current revised snapshot, not historical release vintages.')
            if release:source['release_date']=release
            if product_releases:source['product_release_dates']=product_releases
            if key=='core':source['period_start']=source['period_end']
            if key=='funds':source['period_start']=(date.fromisoformat(source['period_end'])-timedelta(days=6)).isoformat()
            if key=='monthly':source['period_start']=model['transmission']['start']
            if key=='z1':source['period_start']=source['period_end'][:4]+f'-{int(source["period_end"][5:7])-2:02d}-01'
            status['state']='updated' if observation(model,key)>observation(previous,key) or any(not measurement_equal(model[k],previous.get(k)) for k in patch) else 'unchanged'
        status['observation_date']=observation(model,key);statuses.append(status)
    failure=None
    try:
        require(statuses[0]['state']!='failed','코어 자료 확인 실패: 이전 검증 모델을 유지합니다.')
        rebuild_windows(model);validate_model(model,previous)
        model['generated_at_utc']=NOW;model['analysis_as_of_utc']=TODAY.isoformat()
        model['source_hashes']={f'{key}:{url}':sha for key in [s['id'] for s in statuses] for s in statuses if s['id']==key for url,sha in model['sources'][[x[0] for x in LAYERS].index(key)]['source_sha256'].items()}
        model['quality'].update(raw_core_rows_verified=len(model['core_weekly']),reported_fund_weeks=len(model['fund_weekly']),z1_quarters=len(model['equity_sectors']['rows']),
                                weekly_capture_scope=f"Accumulated verified weekly snapshots: {model['fund_weekly'][0]['date']} through {model['fund_weekly'][-1]['date']}; exact 1/4-week completeness required.")
        model['daily_collection']={'version':1,'source_states':{s['id']:s['state'] for s in statuses},'raw_artifact':'official-source-snapshots','retained_sources':[s['id'] for s in statuses if s['state']=='failed']}
        validate_model(model,previous)
    except Exception as exc:failure=str(exc)[:200];model=previous
    if not failure:write(ROOT/'live-model.json',model)
    else:
        for s in statuses:
            s['observation_date']=observation(previous,s['id'])
            if s['state']!='failed':
                s['checked_state']=s['state'];s['state']='retained';s['message']='전체 모델 검증이 끝나지 않아 이전 게시 자료를 유지합니다.'
    status={'schema_version':1,'schedule':'매일 오전 9시 (한국시간)','timezone':'Asia/Seoul','state':'failed' if failure else 'partial' if any(s['state']=='failed' for s in statuses) else 'success',
            'last_attempt_at_utc':NOW,'last_success_at_utc':prior.get('last_success_at_utc') if failure else NOW,
            'model_generated_at_utc':model['generated_at_utc'],'model_sha256':digest((ROOT/'live-model.json').read_bytes()),'message':failure,
            'workflow_url':'https://github.com/arahant-tech/macro-liquidity-dashboard/actions/workflows/daily.yml',
            'run_url':os.getenv('GITHUB_SERVER_URL','https://github.com')+'/'+os.getenv('GITHUB_REPOSITORY','arahant-tech/macro-liquidity-dashboard')+'/actions/runs/'+os.getenv('GITHUB_RUN_ID',''),
            'sources':statuses,'schedule_note':'GitHub 실행 대기 시간에 따라 지연될 수 있습니다. 자료는 각 출처의 주간·월간·분기 발표 때 바뀝니다.'}
    write(ROOT/'update-status.json',status)
    write(ROOT/'.run/source-manifest.json',{'checked_at':NOW,'sources':statuses,'candidate_error':failure})
    print(json.dumps({'state':status['state'],'sources':[{k:s[k] for k in ['id','state','observation_date','message']} for s in statuses]},ensure_ascii=False),flush=True)
    return status

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--validate-only',action='store_true');args=parser.parse_args()
    if args.validate_only:validate_model(load(ROOT/'live-model.json'));print('Model invariants passed.')
    else:update()
