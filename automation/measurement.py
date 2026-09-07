"""Unchanged measurement functions from the audited v8 builder."""
from datetime import date,timedelta
import math
LEAVES=['domestic_equity','world_equity','hybrid','bond','commodity']

def direction(values):
 if any(v is None for v in values.values()):return {'q_bn':None,'u':None,'available':False}
 assert all(math.isfinite(v) for v in values.values())
 q=sum(abs(v) for v in values.values())
 return {'q_bn':q,'u':{k:v/q for k,v in values.items()} if q>0 else None,'available':True}

def core_change(a,b):
 ds=b['S_bn']-a['S_bn'];dp=b['p_bank']-a['p_bank'];dr=b['R_bn']-a['R_bn']
 scalar=(a['p_bank']+b['p_bank'])/2*ds;allocation=(a['S_bn']+b['S_bn'])/2*dp
 assert math.isclose(scalar+allocation,dr,abs_tol=1e-8)
 return {'start':a['date'],'end':b['date'],'S_bn':b['S_bn'],'R_bn':b['R_bn'],'P_bn':b['P_bn'],'p_bank':b['p_bank'],'delta_S_bn':ds,'delta_R_bn':dr,'delta_p_pp':100*dp,'scalar_effect_bn':scalar,'allocation_effect_bn':allocation}

def observed_state(ds,flow):
 a='총량 증가' if ds>0 else '총량 감소' if ds<0 else '총량 유지'
 b='미국 주식형 순설정' if flow>0 else '미국 주식형 순환매' if flow<0 else '미국 주식형 변화 없음'
 return a+' · '+b

def fund_window(week_rows,core,weeks,end):
 assert weeks in [1,4]
 endday=date.fromisoformat(end);start=(endday-timedelta(weeks=weeks)).isoformat()
 expected=[(endday-timedelta(weeks=i)).isoformat() for i in reversed(range(weeks))]
 missing=[d for d in expected if d not in week_rows]
 if missing or start not in core or end not in core:
  return {'available':False,'weeks':weeks,'start':start,'end':end,'missing_weeks':missing,'core_endpoints_available':start in core and end in core}
 rows=[week_rows[d] for d in expected]
 if any(r.get(k+'_bn') is None for r in rows for k in LEAVES):
  return {'available':False,'weeks':weeks,'start':start,'end':end,'reason':'missing fund leaf'}
 F={k:sum(r[k+'_bn'] for r in rows) for k in LEAVES};c=core_change(core[start],core[end])
 mf=[r.get('mutual_domestic_equity_bn') for r in rows];etf=[r.get('etf_domestic_equity_bn') for r in rows]
 return {'available':True,'weeks':weeks,'start':start,'end':end,'flow_first_week_end':expected[0],'week_dates':expected,'core':c,'F_bn':F,'F_US_bn':F['domestic_equity'],'F_equity_bn':F['domestic_equity']+F['world_equity'],'product_split':{'mutual_domestic_bn':sum(mf) if all(v is not None for v in mf) else None,'etf_domestic_bn':sum(etf) if all(v is not None for v in etf) else None},'normalization':direction(F),'state':observed_state(c['delta_S_bn'],F['domestic_equity'])}
