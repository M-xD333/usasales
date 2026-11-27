"""20-product free-tier measurement, <=25 requests, sequential and quota-aware; no paid endpoint."""
import json
from pathlib import Path
import time
import requests
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import product_weight as W

BASE='https://api.upcitemdb.com/prod/trial/'


def run(session=requests,sleep=time.sleep):
    trace=[];pool={};stopped=None
    def get(method,params,delay):
        nonlocal stopped
        if trace:sleep(delay)
        if len(trace)>=25:stopped='request_cap';return None
        try:
            r=session.get(BASE+method,params=params,headers={'Accept':'application/json','User-Agent':'usasales-upc-weight-probe/1.0'},timeout=(3,8),allow_redirects=False)
            trace.append({'method':method,'http':r.status_code,'limits':{k:v for k,v in r.headers.items() if k.lower().startswith('x-ratelimit') or k.lower()=='retry-after'}})
            if r.status_code!=200:stopped='HTTP '+str(r.status_code);return None
            if r.headers.get('X-RateLimit-Remaining')=='0':stopped='quota_exhausted'
            data=r.json()
            return data if isinstance(data,dict) else None
        except (requests.RequestException,ValueError):stopped='network_or_json';return None
    for term in ('Corsair','MSI','Google Pixel','Logitech','Crucial'):
        data=get('search',{'s':term},31)
        if data:
            for item in data.get('items',[]):
                upc=str(item.get('upc') or item.get('ean') or '')
                if upc.isdigit() and 8<=len(upc)<=14:pool.setdefault(upc,{'upc':upc,'title':str(item.get('title',''))[:180]})
        if stopped or len(pool)>=20:break
    rows=[];selected=list(pool)[:20]
    for i in range(0,len(selected),2):
        if stopped:break
        ids=selected[i:i+2];data=get('lookup',{'upc':','.join(ids)},10.2)
        if data:
            found={str(item.get('upc') or item.get('ean') or ''):item for item in data.get('items',[])}
            for upc in ids:
                item=found.get(upc,{})
                raw=item.get('weight');kg=W.kilograms(raw)
                rows.append(dict(pool[upc],found=bool(item),provider_weight=raw,unit_mass_kg=kg,
                                 kind='unknown',reason='provider weight does not identify package/net' if kg else 'missing weight or unit'))
    return {'rows':rows,'selected':len(selected),'trace':trace,'stopped':stopped,
            'summary':{'attempted_products':len(rows),'returned':sum(r['found'] for r in rows),
                       'weight_with_units':sum(r['unit_mass_kg'] is not None for r in rows),'confirmed_pkg':0,'confirmed_net':0},
            'limits_source':'https://www.upcitemdb.com/wp/docs/main/development/plan/'}


if __name__=='__main__':
    result=run();Path('/tmp/upc-free-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'summary':result['summary'],'requests':len(result['trace']),'stopped':result['stopped']}))
