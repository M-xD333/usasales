"""Read-only ledger coverage and bounded public-page sample. No Telegram or challenge bypass."""
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import product_weight as W
import merchant_http
import requests
import weight_lookup


def coverage(doc):
    groups=defaultdict(Counter);latest={}
    for row in doc['rows']:
        key=(row.get('listing',row.get('id')),row.get('price'))
        old=latest.get(key)
        if old is None or (row.get('last_ts',0),row.get('seq',0)) >= (old.get('last_ts',0),old.get('seq',0)): latest[key]=row
    for row in latest.values():
        status=row.get('weight_status')
        for dim,key in [('store',row.get('store','unknown')),('category',row.get('category','unknown'))]:
            c=groups[(dim,key)];c['seen']+=1
            if status is None:c['unmeasured']+=1
            else:
                c['measured']+=1;c[status.get('kind','unknown') if status.get('found') else 'missing']+=1
    return {'rows':len(doc['rows']),'offers':len(latest),'started_at':doc.get('started_at'),'truncated_before':doc.get('truncated_before'),
            'dropped_capacity':doc.get('dropped_capacity'),
            'groups':[dict(dimension=d,key=k,**v) for (d,k),v in sorted(groups.items())]}


BEFORE_PARSER=None

def sample_page(url):
    at=time.time();response=None
    try:
        response=merchant_http.get(url,session=requests,headers={'User-Agent':'usasales-weight-audit/1.0'},timeout=(1,1),stream=True)
        if response is None:return {'_sample':{'http':None,'reason':'unsafe_or_unavailable'}}
        meta={'http':response.status_code,'source_url':url,'at':at}
        if response.status_code!=200:return {'_sample':meta,'weight_missing_reason':'HTTP '+str(response.status_code)}
        chunks=[];size=0
        for chunk in response.iter_content(16384):
            chunks.append(chunk);size+=len(chunk)
            if size>=1_000_000:break
        raw=b''.join(chunks);page=raw.decode(response.encoding or 'utf-8',errors='replace')
        meta.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        fragments=[]
        for match in re.finditer(r'<(?:tr|li|div)\b[^>]*>[^<>]{0,100}(?:<[^>]+>[^<>]{0,100}){0,4}(?:Shipping Weight|Package Weight|Package Dimensions|Item Weight|Product Weight|Net Weight)[\s\S]{0,700}?</(?:tr|li|div)>',page,re.I):
            fragments.append(match[0])
            if len(fragments)>=4:break
        meta['fragments']=fragments
        # Legacy baseline is supplied only for this read-only sample, never production.
        old_path=Path(BEFORE_PARSER) if BEFORE_PARSER else Path('/nonexistent/before-parser')
        if old_path.exists():
            spec=importlib.util.spec_from_file_location('weight_before',old_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
            meta['before_net_kg']=old.from_markup(page,url)
        return {**W.extract(page,url,at),'_sample':meta}
    except Exception as ex:return {'_sample':{'http':None,'reason':type(ex).__name__},'weight_missing_reason':'network_error'}
    finally:
        if response is not None:response.close()


def collect(offers):
    lookup=weight_lookup.Lookup(resolver=sample_page,budget=3,limit=30)
    for d in offers:lookup.request(d)
    deadline=time.monotonic()+55
    while (lookup.waiting or lookup.active) and time.monotonic()<deadline:
        lookup.poll();time.sleep(.02)
    lookup.closed=True
    rows=[]
    import store_links
    for d in offers:
        result=lookup.results.get(store_links.merchant_url(d['url']),{'weight_missing_reason':'sample_deadline'})
        row={**d,**result};row['weight_status']=W.status(row);rows.append(row)
    lookup.close()
    return {'sampled_at':time.time(),'rows':rows}


def main():
    p=argparse.ArgumentParser();p.add_argument('--ledger',default='data/offer_audit.json.gz');p.add_argument('--sample-input');p.add_argument('--sample-output');p.add_argument('--output');p.add_argument('--before-parser');a=p.parse_args()
    global BEFORE_PARSER
    BEFORE_PARSER=a.before_parser
    report=coverage(json.loads(gzip.decompress(Path(a.ledger).read_bytes())))
    if a.sample_input:
        sample=collect(json.loads(Path(a.sample_input).read_text())['rows'][:30])
        if a.sample_output:Path(a.sample_output).write_text(json.dumps(sample,ensure_ascii=False,indent=2)+'\n')
        report['sample']={'offers':len(sample['rows']),'pkg':sum(bool(r.get('weight_pkg_kg')) for r in sample['rows']),
                          'net':sum(bool(r.get('weight_net_kg')) for r in sample['rows']),
                          'before_html_evaluated':sum('before_net_kg' in r.get('_sample',{}) for r in sample['rows']),
                          'before_net':sum(bool(r.get('_sample',{}).get('before_net_kg')) for r in sample['rows']) if a.before_parser else None}
    if a.output:Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'rows':report['rows'],'groups':len(report['groups']),'sample':report.get('sample')},ensure_ascii=False))

if __name__=='__main__':main()
