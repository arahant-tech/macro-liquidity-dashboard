"""S&P DJI's independently reported aggregate repurchases, including stale data.

The public press archive can trail licensed workbooks. Its latest verified
quarter remains visibly stale; it never fills missing quarters or substitutes
announced authorizations, forecasts, net issuance or shareholder total returns.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from model.live_api import _validate_output_root
from .pboc import _HTML
from .terminal_flows import plain

PROVIDER = "market_buybacks"
SERIES_ID = "SPDJI_SP500_REPORTED_BUYBACKS"
INDEX_URL = "https://press.spglobal.com/index.php?s=2429&keywords=buybacks"
USER_AGENT = "MacroLiquidityDashboard/1.2 (https://github.com/arahant-tech/macro-liquidity-dashboard)"
ASSUMPTIONS = [
    "The publisher's explicit quarterly stock-repurchase expenditure is an independent reported observation, not an authorization or net-issuance inversion.",
    "The S&P 500 observation covers the publisher's index reporting population, which changes over time; it is not the fixed five-company panel or the entire US equity market.",
    "The newest verifiable repurchase report in the public press archive is the newest observation this collector can certify, not necessarily the publisher's newest licensed dataset.",
]
LIMITATIONS = [
    "The accessible archive may lag other S&P DJI publications. Reference periods older than 190 days are explicitly stale and are never extended to newer quarters.",
    "Preliminary aggregate figures can be revised, and headline billion-dollar amounts are rounded. Raw issuer definitions, cash timing and the exact constituent observation date are not independently certified.",
    "This aggregate overlaps the individual SEC and issuer repurchase observations; do not add them. Z.1 net issuance already contains retirements and is not additive either.",
    "Actual capture is the certified knowability time; a press-release date is preserved separately, without backdating the current snapshot.",
    "A quarterly report is a delayed realized flow, not a real-time trading signal. No model performance, causality or latent-state validity is established by collection.",
]


class BuybackError(RuntimeError):
    pass


def now(clock):
    value=(clock or (lambda:datetime.now(timezone.utc)))()
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise ValueError("clock_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00","Z")


def allowed_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!="https" or parsed.netloc!="press.spglobal.com" or parsed.username or parsed.password or parsed.fragment:
        raise BuybackError("unapproved_source_url")
    if url==INDEX_URL:
        return url
    if parsed.query or not re.fullmatch(r"/20\d{2}-\d{2}-\d{2}-S-P-500-[A-Za-z0-9,%._-]+",parsed.path):
        raise BuybackError("unapproved_source_url")
    return url


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def request(url,root,transport,clock):
    allowed_url(url)
    if transport:
        body=transport(url)
    else:
        req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"text/html"})
        with urllib.request.build_opener(Redirect()).open(req,timeout=20) as response:
            allowed_url(response.geturl())
            body=response.read(2_000_001)
    if not isinstance(body,bytes) or not body or len(body)>2_000_000:
        raise BuybackError("invalid_source_size")
    digest=hashlib.sha256(body).hexdigest()
    relative=Path("raw/market_buybacks")/(digest+".html")
    target=root/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=body:raise BuybackError("immutable_source_conflict")
    else:
        with target.open("xb") as handle:handle.write(body)
    captured=stamp(now(clock))
    return body.decode("utf-8-sig"),{"source_url":url,"raw_path":str(relative),"raw_sha256":digest,
            "known_by":captured,"retrieved_at":captured,"original_release_at":None,"release_timestamp_verified":False}


def quarter(year,q):
    end_month=q*3
    return date(year,end_month-2,1),date(year,end_month,calendar.monthrange(year,end_month)[1])


def select_latest(text,today):
    candidates=[]
    unrecognized=[]
    for link in _HTML(text).links:
        if "S&P 500" not in link["text"] or "buyback" not in link["text"].lower():continue
        url=allowed_url(urllib.parse.urljoin(INDEX_URL,link["href"]))
        published=date.fromisoformat(urllib.parse.urlsplit(url).path[1:11])
        if published>today:raise BuybackError("future_publication_date")
        match=re.match(r"S&P 500 Q([1-4]) (20\d{2}) Buybacks\b",link["text"])
        if not match:
            unrecognized.append(published)
            continue
        q,year=int(match[1]),int(match[2])
        start,end=quarter(year,q)
        if end>today or published<end:raise BuybackError("future_or_unfinished_reference_quarter")
        candidates.append((end,published,url,start,q,year))
    if not candidates:raise BuybackError("public_quarterly_buyback_report_not_found")
    selected_key=max((item[0],item[1]) for item in candidates)
    selected=set(x for x in candidates if x[:2]==selected_key)
    if len(selected)!=1:raise BuybackError("ambiguous_latest_buyback_report")
    item=selected.pop()
    if any(d>=item[1] for d in unrecognized):raise BuybackError("newer_unrecognized_buyback_report")
    return item


def parse_report(text,selected,today):
    end,published,url,start,q,year=selected
    content=plain(text)
    release=re.search(r"NEW YORK\s*,\s*([A-Za-z]+)\.?\s+(\d{1,2}),\s*(20\d{2})\s*/",content)
    if not release:raise BuybackError("document_release_date_unverified")
    month_name=release[1].lower()
    months={name.lower():n for n,name in enumerate(calendar.month_name) if name}
    months.update({name.lower():n for n,name in enumerate(calendar.month_abbr) if name})
    if month_name not in months:raise BuybackError("document_release_month_unverified")
    document_date=date(int(release[3]),months[month_name],int(release[2]))
    if document_date!=published or not end<=document_date<=today:
        raise BuybackError("document_release_date_mismatch")
    declaration=re.search(r"S&P Dow Jones Indices \(S&P DJI\) today announced the (preliminary|final) S&P 500\s*(?:®\s*)?stock buybacks or share repurchases data for Q([1-4]) (20\d{2})\.",content)
    if not declaration or int(declaration[2])!=q or int(declaration[3])!=year:
        raise BuybackError("actual_repurchase_quarter_unverified")
    # Require two explicit actual-expenditure sentences to agree. Forecasts and
    # trailing-twelve-month totals do not match either grammar.
    expressions=(rf"S&P 500 Q{q} {year} buybacks were \$([\d,.]+) (billion|trillion)\b",
                 rf"Q{q} {year} share repurchases were \$([\d,.]+) (billion|trillion)\b")
    extracted=[]
    precisions=[]
    for expression in expressions:
        found=re.findall(expression,content)
        if not found:raise BuybackError("explicit_quarterly_expenditure_missing")
        values=set()
        for value,unit in found:
            if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?",value):
                raise BuybackError("invalid_reported_amount")
            amount=Decimal(value.replace(',',''))
            multiplier=Decimal(1000) if unit=='trillion' else Decimal(1)
            amount*=multiplier
            if not Decimal(0)<=amount<=Decimal(2000):raise BuybackError("implausible_quarterly_buybacks")
            values.add(amount)
            decimals=len(value.split('.')[1]) if '.' in value else 0
            precisions.append(float(multiplier*Decimal(10)**(-decimals)))
        if len(values)!=1:raise BuybackError("conflicting_actual_expenditure_values")
        extracted.append(values.pop())
    if extracted[0]!=extracted[1]:raise BuybackError("quarterly_headline_body_mismatch")
    age=(today-end).days
    return {"provider":PROVIDER,"series_id":SERIES_ID,"label":"S&P DJI · S&P 500 자사주 매입 집계",
            "layer":4,"track":"equity","role":"flow","block":"reported_market_buybacks",
            "value":float(extracted[0]),"unit":"billion USD","units":"billion USD",
            "period_start":start.isoformat(),"period_end":end.isoformat(),"observation_date":end.isoformat(),
            "frequency":"quarterly","duration_months":3,"native_period_basis":"reported_quarter_not_ttm",
            "original_release_date":published.isoformat(),"reported_release_lag_days":(published-end).days,
            "source_publisher":"S&P Dow Jones Indices","source_universe":"S&P 500 reporting constituents; exact observation-date membership unverified",
            "constituent_date_verified":False,"method":"publisher_reported_aggregate_repurchase_expenditure",
            "measurement_kind":"independent_aggregate_buyback_observation","source_quality":"publisher_reported_"+declaration[1],
            "rounding_unit_billion_usd":max(precisions),"aggregation_allowed":False,
            "not_additive_with":["issuer_buybacks","buybacks","Z1_NFC_EQUITY_NET_ISSUANCE"],
            "status":"stale" if age>190 else "ok","age_calendar_days":age,"stale_after_days":190,
            "research_eligible":False,"market_aggregate":True,
            "discovery_status":"public_archive_only_current_licensed_data_unverified"}


def collect(output_root,transport=None,clock=None):
    root=_validate_output_root(output_root)
    today=now(clock).date()
    result={"provider":PROVIDER,"total":1,"success":0,"observations":[],"errors":[],"sources":[],
            "assumptions":ASSUMPTIONS,"limitations":LIMITATIONS,"research_eligible":False}
    try:
        index,evidence=request(INDEX_URL,root,transport,clock)
        result['sources'].append(evidence)
        selected=select_latest(index,today)
        body,source=request(selected[2],root,transport,clock)
        result['sources'].append(source)
        item=parse_report(body,selected,today)
        result['observations'].append({**item,**source})
        result['success']=1
        if item['status']=='stale':
            result['errors'].append({"series_id":SERIES_ID,"code":"latest_public_quarter_stale"})
            result['status']='partial'
        else:result['status']='ok'
    except Exception as exc:
        code=str(exc) if isinstance(exc,BuybackError) else 'http_'+str(exc.code) if isinstance(exc,urllib.error.HTTPError) else 'source_'+type(exc).__name__
        result['errors'].append({"series_id":SERIES_ID,"code":code})
        result['status']='error'
    result['fetched_at']=stamp(now(clock))
    return result
