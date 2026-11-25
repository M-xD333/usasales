"""Read-only public channel audit; getChat metadata, never send Telegram messages."""
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store_links
from tools.coverage import posts
from tools.newegg_gap import destination


def public_username(session=requests):
    token, chat = os.environ['TG_BOT_TOKEN'], os.environ['TG_CHAT_ID']
    try:
        response = session.post('https://api.telegram.org/bot'+token+'/getChat',
                                data={'chat_id':chat}, timeout=(5,15))
        if response.status_code != 200:
            raise RuntimeError('getChat failed: HTTP '+str(response.status_code))
        body = response.json()
    except requests.RequestException as ex:
        raise RuntimeError('getChat network failure: '+type(ex).__name__) from None
    if not body.get('ok'):
        raise RuntimeError('getChat did not confirm metadata')
    username = body.get('result',{}).get('username','')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        raise RuntimeError('No public channel username; owner export is required')
    return username


def fetch_window(channel, cutoff, end, session=requests, max_pages=80):
    url='https://t.me/s/'+channel; visited=[]; found={}; boundary=False
    for _ in range(max_pages):
        if url in visited: break
        visited.append(url)
        response=session.get(url,headers={'User-Agent':'usasales-public-audit/1.0 (+https://github.com/M-xD333/usasales)'},timeout=(5,25))
        if response.status_code != 200:
            raise RuntimeError('Public preview HTTP '+str(response.status_code))
        page=posts(response.text)
        if not page:
            raise RuntimeError('Public text preview unavailable; an owner export is required')
        for row in page: found[row['id']]=row
        times=[datetime.fromisoformat(r['at']) for r in page if r.get('at')]
        if times and min(times)<cutoff: boundary=True;break
        older=re.findall(r'href="(/s/'+re.escape(channel)+r'\?before=\d+)"',response.text)
        if not older:
            boundary=True # Visible public history exhausted, including a newly created channel.
            break
        url=urljoin(url,older[0]);time.sleep(.3)
    rows=[r for r in found.values() if r.get('at') and cutoff<=datetime.fromisoformat(r['at'])<=end]
    return rows,visited,boundary


def primary_newegg(rows):
    output=[]
    for row in rows:
        primary=next((u for raw in row['links'] if (u:=destination(raw)) and store_links.merchant_url(u)),None)
        if primary and store_links.on_domain(primary,('newegg.com',)):
            output.append(dict(post=row['id'],at=row['at'],title=row['text'][:100],url=primary,
                day_tashkent=datetime.fromisoformat(row['at']).astimezone(ZoneInfo('Asia/Tashkent')).date().isoformat()))
    return output


if __name__=='__main__':
    from collections import Counter
    sample=json.loads(Path('docs/evidence/newegg-week-2026-11-25.json').read_text())
    channel=public_username()
    rows,pages,complete=fetch_window(channel,datetime.fromisoformat(sample['cutoff']),datetime.fromisoformat(sample['now']))
    deals=primary_newegg(rows)
    result={'channel':channel,'window':[sample['cutoff'],sample['now']],'boundary_reached':complete,
            'public_text_posts':len(rows),'newegg_count':len(deals),
            'daily_tashkent':dict(Counter(d['day_tashkent'] for d in deals)), 'newegg_posts':deals,'public_pages':pages}
    Path('own-channel-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('public_pages','newegg_posts')},ensure_ascii=False))
