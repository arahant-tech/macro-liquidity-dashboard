"""Collector parser; independent-audit.py deliberately uses a separate parser."""
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import re

URLS={'combined':'https://www.ici.org/research/stats/combined_flows','mutual':'https://www.ici.org/research/stats/flows','etf':'https://www.ici.org/research/stats/etf_flows'}
LEAVES={'domestic_equity':'Domestic','world_equity':'World','hybrid':'Hybrid','bond':'Bond','commodity':'Commodity'}
NORMAL={'Total equity':'Equity','Total bond':'Bond'}
BASE_LABELS={'Equity','Domestic','World','Hybrid','Bond','Taxable','Municipal','Total'}
DATE_CELL=re.compile(r'\d{1,2}/\d{1,2}/\d{4}')
MONTH_DATE=r'([A-Za-z]+\s+\d{1,2},\s+\d{4})'

def require(condition,message):
    if not condition: raise ValueError(message)

def digest(data): return hashlib.sha256(data).hexdigest()

def local_path(root,relative):
    p=(Path(root)/relative).resolve()
    require(p.is_relative_to(Path(root).resolve()),'Source path escapes funds root')
    return p

class ReportHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables=[]; self.table=None; self.row=None; self.cell=None
        self.text=[]; self.published=[]; self.times=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and a.get('property',a.get('name')) in ('article:published_time','datePublished'): self.published.append(a.get('content','')[:10])
        if tag=='time' and a.get('datetime'): self.times.append(a['datetime'][:10])
        if tag=='table':
            require(self.table is None,'Nested report tables unsupported'); self.table=[]
        elif tag=='tr' and self.table is not None: self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
    def handle_data(self,data):
        self.text.append(data)
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split())); self.cell=None
        elif tag=='tr' and self.row is not None: self.table.append(self.row); self.row=None
        elif tag=='table' and self.table is not None: self.tables.append(self.table); self.table=None

def textual_date(text): return datetime.strptime(re.sub(r'\s+',' ',text),'%B %d, %Y').date().isoformat()

def decode_report(body,name,content_type,as_of=None):
    require(name in URLS,'Unknown report role')
    text=body.decode('utf-8-sig') if isinstance(body,bytes) else body
    if content_type=='text/html':
        page=ReportHTML(); page.feed(text)
        candidates=[t for t in page.tables if any(r and r[0]=='Domestic' for r in t)]
        require(len(candidates)==1,f'{name}: expected one investment-objective table')
        table=candidates[0]; prose=' '.join(page.text)
        narrative=re.findall(r'Washington,\s*D\.?C\.?;\s*'+MONTH_DATE,prose,re.I)
        explicit=set(page.published)|{textual_date(x) for x in narrative}
        choices=explicit or set(page.times)
        require(len(choices)==1,f'{name}: absent or ambiguous publication date')
        release=next(iter(choices))
    elif content_type=='text/x-web-capture':
        prose=text; table=[]
        found=re.findall(r'^L\d+:\s*'+MONTH_DATE+r'\s*\|',text,re.M)
        require(len(found)==1,f'{name}: absent or ambiguous captured release date')
        release=textual_date(found[0])
        request=re.search(r'Source: open\((\{.*?\})\);',text)
        import json
        require(request is not None and json.loads(request.group(1))['ref_id']==URLS[name],f'{name}: capture source URL differs')
        for line in text.splitlines():
            line=re.sub(r'^L\d+:\s*','',line).strip()
            if '|' not in line: continue
            cells=[v.strip() for v in line.split('|')]
            if any(DATE_CELL.fullmatch(v) for v in cells) or NORMAL.get(cells[0],cells[0]) in BASE_LABELS|{'Commodity'}: table.append(cells)
    else: raise ValueError('Unsupported source content_type')
    date.fromisoformat(release)
    require('Millions of dollars' in prose,f'{name}: expected USD millions')
    headers=[r for r in table if len(r)>1 and all(DATE_CELL.fullmatch(v) for v in r[1:])]
    require(len(headers)==1,f'{name}: missing or duplicate reporting-date header')
    dates=[datetime.strptime(x,'%m/%d/%Y').date().isoformat() for x in headers[0][1:]]
    require(dates and len(dates)==len(set(dates)),f'{name}: duplicate or empty dates')
    require(dates==sorted(dates,reverse=True),f'{name}: dates must descend')
    require(all(date.fromisoformat(x).weekday()==2 for x in dates),f'{name}: expected Wednesday week ends')
    require(all((date.fromisoformat(a)-date.fromisoformat(b)).days==7 for a,b in zip(dates,dates[1:])),f'{name}: weekly gap')
    require(max(dates)<release,f'{name}: observation must precede release')
    if as_of is not None: require(release<=date.fromisoformat(as_of).isoformat(),f'{name}: future release')
    values={}; allowed=BASE_LABELS|(set() if name=='mutual' else {'Commodity'})
    for cells in table:
        if not cells: continue
        label=NORMAL.get(cells[0],cells[0])
        if label not in BASE_LABELS|{'Commodity'}: continue
        require(label in allowed and label not in values,f'{name}: duplicate/unexpected {label}')
        require(len(cells)==len(dates)+1,f'{name}: row width mismatch')
        nums=[]
        for value in cells[1:]:
            value=value.replace('−','-')
            require(re.fullmatch(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)',value),f'{name}: absent/non-integer monetary cell')
            nums.append(int(value.replace(',','')))
        values[label]=nums
    require(set(values)==allowed,f'{name}: missing required categories')
    return {'release_date':release,'dates':sorted(dates),'bydate':{d:{k:v[i] for k,v in values.items()} for i,d in enumerate(dates)},'raw_table':table,'content_type':content_type,'text':prose}

def validate_bundle(reports):
    require(set(reports)==set(URLS),'Expected three product reports')
    dates=reports['combined']['dates']
    require(all(r['dates']==dates for r in reports.values()),'Product report periods differ')
    checks=[]
    for name,report in reports.items():
        for day,values in report['bydate'].items():
            leaves=['Domestic','World','Hybrid','Bond']+([] if name=='mutual' else ['Commodity'])
            for label,parts,total in [('equity',['Domestic','World'],'Equity'),('bond',['Taxable','Municipal'],'Bond'),('total',leaves,'Total')]:
                error=sum(values[k] for k in parts)-values[total]
                require(abs(error)<=(len(parts)+1)/2,f'{name}:{day}: {label} exceeds rounding bound')
                checks.append({'source':name,'date':day,'test':label,'residual_million':error})
    for day in dates:
        c,m,e=(reports[k]['bydate'][day] for k in URLS)
        for label in BASE_LABELS: require(abs(c[label]-m[label]-e[label])<=1.5,f'{day}:{label}: combined differs from products')
        require(c['Commodity']==e['Commodity'],'Commodity must remain ETF only')
    return checks
