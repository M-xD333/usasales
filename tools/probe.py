"""Read-only honest endpoint probe: one GET/URL, <=25, no redirects/retries/Telegram."""
import argparse
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote_plus, urlsplit
import feedparser
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import direct_stores
import sources
import store_links

UA='usasales-endpoint-probe/1.0 (+https://github.com/M-xD333/usasales; read-only)'


def endpoints(checkpoint):
    state=json.loads(Path(checkpoint).read_text())['state']
    candidates=list(state.get('market_prices',{}).get('products',{}))
    for d in state.get('pending',{}).values():candidates += [d.get('url',''),*d.get('store_candidates',[])]
    product=next((u for u in candidates if 'newegg.com' in u and store_links.merchant_url(u)),None)
    rows=[('newegg-today','https://www.newegg.com/todays-deals'),
          ('newegg-combo','https://www.newegg.com/tools/combo-builder/1740'),
          ('newegg-category','https://www.newegg.com/Computer-Parts/Store/ID-7'),
          ('newegg-robots','https://www.newegg.com/robots.txt'),
          ('newegg-rss','https://www.newegg.com/RSS/Index.aspx'),
          ('reddit-rss','https://www.reddit.com/r/buildapcsales/.rss'),
          ('reddit-old-rss','https://old.reddit.com/r/buildapcsales/.rss'),
          ('sd-category','https://slickdeals.net/category/computer-parts/')]
    if product:rows.insert(3,('newegg-product',product))
    for name in ['newegg','amazon','micro center','best buy','b&h','dell','lenovo']:
        rows.append(('sd-'+name.replace(' ','-'),sources._sd_search(name).url))
    rows += [('woot','https://www.woot.com/category/computers'),
             ('techbargains','http://www.techbargains.com/rss.xml'),
             ('dealnews','https://www.dealnews.com/c39/Computers/?rss=1'),
             ('micro-center','https://www.microcenter.com/category/4294966996/computer-parts'),
             ('best-buy','https://www.bestbuy.com/site/top-deals/computer-tablet-deals/pcmcat1563302966557.c'),
             ('bh-deals','https://www.bhphotovideo.com/c/browse/DealZone/ci/48367'),
             ('dell-outlet','https://www.dell.com/en-us/dfh/lp/outlet'),
             ('lenovo-outlet','https://www.lenovo.com/us/outletus/en/'),
             ('hp-outlet','https://www.hp.com/us-en/shop/cv/refurbished-computers')]
    return rows


def run(rows, session=requests, max_gets=25):
    queue=list(rows);results=[];visited=set()
    while queue and len(visited)<min(25,max_gets):
        name,url=queue.pop(0)
        if url in visited:continue
        visited.add(url);start=time.monotonic()
        row={'id':name,'url':url,'status':None,'content_type':'','bytes':0,'parsed_entries':0,'truncated':False}
        try:
            with session.get(url,headers={'User-Agent':UA,'Accept-Language':'en-US'},timeout=(4,8),stream=True,allow_redirects=False) as response:
                row.update(status=response.status_code,content_type=response.headers.get('Content-Type',''))
                chunks=[]
                for chunk in response.iter_content(65536):
                    chunks.append(chunk);row['bytes']+=len(chunk)
                    if row['bytes']>=2_000_000:row['truncated']=True;break
                body=b''.join(chunks).decode(response.encoding or 'utf-8',errors='replace')
                if response.status_code==200:
                    if name=='newegg-robots':
                        maps=re.findall(r'^Sitemap:\s*(\S+)',body,re.M|re.I)
                        safe=next((u for u in maps if urlsplit(u).scheme=='https' and (urlsplit(u).hostname or '').endswith('.newegg.com')),None)
                        if safe:queue.insert(0,('newegg-sitemap',safe))
                    elif name!='newegg-sitemap':
                        if 'xml' in row['content_type'] or '<rss' in body[:1000] or '<feed' in body[:1000]:
                            entries=feedparser.parse(body).entries
                        else:entries=direct_stores.parse_catalog(body,url)
                        row['parsed_entries']=len(entries)
        except requests.RequestException as ex:row['error']=type(ex).__name__
        row['seconds']=round(time.monotonic()-start,2)
        row['usable']=row['status']==200 and row['parsed_entries']>0 and not row['truncated']
        results.append(row)
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',default='data/checkpoint.json');p.add_argument('--output',default='probe-results.json');a=p.parse_args()
    result=run(endpoints(a.checkpoint));Path(a.output).write_text(json.dumps(result,indent=2))
    for r in result:print(json.dumps({k:r[k] for k in ('id','status','content_type','bytes','parsed_entries','usable')}))
