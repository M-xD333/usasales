"""Offline Newegg route/known-publication audit of the saved public weekly sample."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store_links
import publication_history as P


def destination(url):
    """Read advertised destinations only: no requests to shorteners or trackers."""
    for _ in range(6):
        params = parse_qs(urlsplit(url).query)
        nested = next((params[k][0] for k in ('murl','btn_url','url','u','u1','u2','redirect')
                       if params.get(k) and urlsplit(params[k][0]).scheme in ('http','https')), None)
        if not nested:
            break
        url = nested
    return url


def analyze(sample, checkpoint):
    rows = []
    for post in sample['rows']:
        for advertised in post['links']:
            target = destination(advertised)
            if store_links.on_domain(target, ('newegg.com',)):
                deal = {'url': target, 'cat':'combo' if store_links.newegg_combo_id(target) else 'other',
                        'cond':'New','title':post['text']}
                matches = [checkpoint['state'].get('publication_history',{}).get(k) for k in P.aliases(deal)]
                rows.append(dict(post=post['id'], at=post['at'], title=post['text'][:80],
                    tracking_domain=urlsplit(advertised).hostname,
                    tracking_params=sorted(parse_qs(urlsplit(advertised).query)),
                    target=target, target_params=sorted(parse_qs(urlsplit(target).query)),
                    day_tashkent=datetime.fromisoformat(post['at']).astimezone(ZoneInfo('Asia/Tashkent')).date().isoformat(),
                    known_publication_match=any(matches),
                    blocker='link unresolved: unsupported ComboDealDetails before this fix' if store_links.newegg_combo_id(target) else 'unknown',
                    historical_ingestion='unknown; current pending/RSS is not an arrival archive'))
                break
    return {'window':[sample['cutoff'],sample['now']], 'checkpoint_saved_at':checkpoint.get('saved_at'), 'boundary_reached':sample['boundary_reached'],
            'public_text_posts':sample['all_text_posts'],'newegg_count':len(rows),
            'competitor_daily_tashkent':dict(Counter(r['day_tashkent'] for r in rows)),
            'rows':rows,'our_daily_total':None,
            'limitation':'Publication ledger aliases are hashed; total merchant/day counts require the owner channel or an event log.'}

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--sample',default='docs/evidence/newegg-week-2026-11-25.json');p.add_argument('--checkpoint',default='data/checkpoint.json');p.add_argument('--output',default='docs/evidence/newegg-gap-2026-11-25.json');a=p.parse_args()
    result=analyze(json.loads(Path(a.sample).read_text()),json.loads(Path(a.checkpoint).read_text()))
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','limitation')},ensure_ascii=False))
    for row in result['rows']: print(json.dumps(row,ensure_ascii=False))
