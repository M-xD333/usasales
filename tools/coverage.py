#!/usr/bin/env python3
"""Public Telegram sample, offline classifier calibration, conservative source coverage.
Only requests the public channel and observed older-page links; never follows merchant links.
"""
import argparse
import collections
import datetime
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit, parse_qs
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bot
import sources


def posts(page):
    rows=[]
    for part in re.split(r'data-post="',page)[1:]:
        post_id=part.split('"',1)[0]
        text=re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>',part,re.S)
        when=re.search(r'<time datetime="([^"]+)"',part)
        if not text: continue
        raw=text.group(1); title=html.unescape(re.sub('<[^>]+>',' ',raw.split('<br')[0])).strip()
        links=[html.unescape(v) for v in re.findall(r'<a[^>]+href="([^"]+)"',raw)]
        rows.append({'id':post_id,'text':title,'at':when.group(1) if when else None,'links':links})
    return rows


def merchant_url(links):
    for link in links:
        # Decode advertised destination offline, without calling affiliate redirects.
        for _ in range(3):
            q=parse_qs(urlsplit(link).query)
            target=next((q[k][0] for k in ['u','url','btn_url','redirect'] if q.get(k)),None)
            if not target: break
            link=target
        if urlsplit(link).scheme=='https': return link
    return ''


def sample(channel, limit):
    url='https://t.me/s/'+channel; found={}; visited=[]
    for _ in range(10):
        if url in visited: break
        visited.append(url); response=requests.get(url,timeout=(5,25));response.raise_for_status()
        page=response.text
        for row in posts(page): found[row['id']]=row
        if len(found)>=limit: break
        older=re.findall(r'href="(/s/'+re.escape(channel)+r'\?before=\d+)"',page)
        if not older: break
        url=urljoin(url,html.unescape(older[0]))
    return sorted(found.values(),key=lambda d:int(d['id'].split('/')[-1]),reverse=True)[:limit],visited


def calibrate(rows, checkpoint):
    bundle=json.loads(Path(checkpoint).read_text());state=bundle['state']; now=bundle['saved_at']
    pending=list(state.get('pending',{}).values()); history=bundle.get('history',{})
    observed_urls={d.get('url') for d in pending}
    observed_titles={bot.name_key(d.get('name','')) for d in pending}
    cfg=bot.get_cfg(); src=sources.Source('competitor-calibration','Public sample','https://t.me/s/pcpartsdrop',trust='curated')
    output=[]
    for row in rows:
        url=merchant_url(row['links']);ts=datetime.datetime.fromisoformat(row['at']).timestamp() if row['at'] else now
        entry={'title':row['text'],'item_id':row['id'],'url':url,'discuss_url':'https://t.me/'+row['id'],'published_ts':ts,'expired':False}
        d=bot.make_deal(entry,src,now)
        ok,info=bot.assess(d,bot.hist_stats(history.get(bot.hist_key(d),[]),now,cfg['hist_days']),cfg,now)
        caught=bool(url and url in observed_urls) or bot.name_key(d.get('name','')) in observed_titles
        output.append({'post':row['id'],'title':row['text'],'store':urlsplit(url).hostname,'category':d['cat'],
                       'parsed_price':d['price'],'classifier_accept':ok,'reason':info['reason'],
                       'observed_in_pending':caught})
    return {'sample_count':len(output),'store_counts':dict(collections.Counter(r['store'] for r in output)),
            'category_counts':dict(collections.Counter(r['category'] for r in output)),
            'classifier_accept_count':sum(r['classifier_accept'] for r in output),
            'observed_pending_matches':sum(r['observed_in_pending'] for r in output),
            'limitations':['Classifier accept is not caught or delivered. Snapshot matching is conservative and misses removed/renamed candidates.',
                            'No merchant URLs fetched; affiliate destinations decoded offline. Price-less advertisements remain price-less.'],
            'rows':output}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--channel',default='pcpartsdrop');p.add_argument('--limit',type=int,default=100);p.add_argument('--checkpoint',default='data/checkpoint.json');p.add_argument('--output',default='docs/evidence/coverage-2026-11-25.json')
    a=p.parse_args(); rows,visited=sample(a.channel,a.limit);r=calibrate(rows,a.checkpoint);r['public_pages_requested']=visited
    Path(a.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ['rows','limitations']},ensure_ascii=False))
