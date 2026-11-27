"""Read-only GH feed measurement, one request per public endpoint, no bypass."""
import json
from pathlib import Path
import re
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import feedparser
import requests
from sources import _sd_search

URLS=[('tom-us','https://www.tomshardware.com/feeds/articletype/deals'),
      ('tom-uk','https://www.tomshardware.com/uk/feeds/articletype/deals'),
      ('tom-news','https://www.tomshardware.com/feeds.xml'),
      ('sd-parts',_sd_search('computer parts').url),('sd-components',_sd_search('pc components').url),
      ('sd-frontpage','https://slickdeals.net/newsearch.php?mode=frontpage&rss=1&searcharea=deals&searchin=first&q=computer+parts')]


def inspect(name,url,session=requests):
    out={'name':name,'url':url};start=time.monotonic()
    try:
        r=session.get(url,headers={'User-Agent':'usasales-feed-audit/1.0 (+https://github.com/M-xD333/usasales)'},timeout=(3,8),allow_redirects=False)
        out.update(http=r.status_code,content_type=r.headers.get('Content-Type',''),bytes=len(r.content))
        if r.status_code!=200:return out
        parsed=feedparser.parse(r.content);out['entries']=len(parsed.entries)
        out['bozo']=bool(parsed.get('bozo'));out['entry_keys']=sorted({k for e in parsed.entries for k in e.keys()})
        out['score_fields']=[{k:str(v)[:80] for k,v in e.items() if re.search(r'thumb|vote|score|rating',k,re.I)} for e in parsed.entries if any(re.search(r'thumb|vote|score|rating',k,re.I) for k in e)]
        out['sample']=[{'title':e.get('title','')[:280],'link':e.get('link',''),'published':e.get('published','')} for e in parsed.entries[:8]]
    except requests.RequestException as ex:out['error']=type(ex).__name__
    finally:out['elapsed_s']=round(time.monotonic()-start,3)
    return out


if __name__=='__main__':
    rows=[inspect(name,url) for name,url in URLS]
    Path('/tmp/feed-probe.json').write_text(json.dumps({'rows':rows},ensure_ascii=False,indent=2)+'\n')
    for r in rows:print(json.dumps({k:r.get(k) for k in ('name','http','entries','error')},ensure_ascii=False))
