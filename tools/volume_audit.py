"""Frozen public cohorts: descriptive metrics, never inferred historical rejection causes."""
import json
import re
from collections import Counter,defaultdict
from pathlib import Path
import sys
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bot
import market_prices as M
import store_links
from tools.newegg_gap import destination

MANUAL = {'27301':'ext_storage','27261':'ram','27146':'kvm','27253':'optical',
          '27255':'cooler','27133':'cooler','27272':'combo','27214':'case','27229':'case','27182':'case','27130':'case'}


def reported_price(text,own=False):
    line=next((s for s in text.splitlines() if '💰' in s),'') if own else text.splitlines()[0]
    m=re.search(r'\$\s*(?:Combo,\s*)?([\d,]+(?:\.\d{1,2})?)',line,re.I)
    price=float(m.group(1).replace(',','')) if m else None
    was_match=re.search(r'(?:was|MSRP|было|reg(?:ular)?\.?|list price)\s*\$([\d,]+(?:\.\d+)?)',line,re.I)
    was=float(was_match.group(1).replace(',','')) if was_match else None
    pct_match=re.search(r'(\d+(?:\.\d+)?)\s*%\s*off|[−-]\s*(\d+(?:\.\d+)?)\s*%',line,re.I)
    pct=float(pct_match.group(1) or pct_match.group(2)) if pct_match else (
        (was-price)/was*100 if was and price is not None and was>0 else None)
    return price,was,pct


def category(row,target,own=False):
    text=row['text'];tag,rest=bot.split_tag(text.replace('📦','').strip())
    old=bot.classify(rest,tag) if own else row.get('classifier_before',bot.classify(text))
    if own:return old,old
    pid=row['id'].split('/')[-1]
    if store_links.newegg_combo_id(target):return 'combo','combo'
    if pid in MANUAL:return MANUAL[pid],old
    case=re.search(r'\b(?:pc|computer)\s+case\b|\b(?:mid|full|super|micro)[\s‐‑–-]*tower\b',text,re.I)
    gpu=re.search(r'\b(?:RTX|GeForce|Radeon|graphics card)\b',text,re.I)
    if case and (not gpu or case.start()<gpu.start()):return 'case',old
    if re.search(r'\bdesktop processor\b',text,re.I):return 'cpu',old
    if re.search(r'\b(?:PWM\s+fans?|UNI\s+FAN|triple fans)\b',text,re.I):return 'cooler',old
    return old,old


def band(value,bounds,labels):
    if value is None:return 'n/a'
    for cutoff,label in zip(bounds,labels):
        if value<cutoff:return label
    return labels[-1]


def analyze(data):
    result={};postrows={};pairs={}
    for channel in ('pcpartsdrop','usasalessearch'):
        own=channel=='usasalessearch';rows=[]
        for row in data[channel]['rows']:
            targets=[destination(u) for u in row['links']]
            target=next((u for u in targets if store_links.merchant_url(u)), '')
            store=M.shop(target)
            full=row.get('full_text',row['text'])
            if not store:
                label=re.search(r'Магазин:\s*([^\n]+)',full) if own else re.search(r'^(?:New release \[)?(Amazon|Best Buy|NewEgg)\b',row['text'],re.I)
                name=label.group(1) if label else ''
                store='corsair.com' if 'corsair' in name.lower() else {'amazon':'amazon.com','best buy':'bestbuy.com','newegg':'newegg.com'}.get(name.lower(),'unknown')
            price,was,pct=reported_price(full,own)
            cat,old=category(row,target,own)
            price_band=band(price,[50,100,200,500,1000],['<50','50–99.99','100–199.99','200–499.99','500–999.99','≥1000'])
            discount_band=band(pct,[15,25,50],['<15%','15–24.99%','25–49.99%','≥50%'])
            minpct,minsav=bot.RULES.get(cat,bot.DEFAULT_RULE)
            labels=[]
            if cat!=old:labels.append('wrong category (reproducible classifier, not historical cause)')
            if price is not None and pct is not None and (pct<minpct or (pct<100 and price*pct/(100-pct)<minsav)):
                labels.append('threshold (counterfactual claimed discount, not historical cause)')
            if store=='newegg.com' and store_links.newegg_combo_id(target):labels.append('link unresolved before combo fix (already fixed; not historical ingestion proof)')
            key=M.canonical_url(target)
            asin=re.search(r'/dp/([A-Z0-9]{10})',key or '',re.I)
            if asin:key='asin:'+asin.group(1).upper()
            rows.append(dict(post=row['id'],title=row['text'],store=store,category=cat,classifier_category=old,
                             price=price,was=was,reported_discount_pct=pct,price_band=price_band,discount_band=discount_band,
                             product_key=key,condition=bot.detect_condition(row['text']),
                             provable_checks=labels,historical_miss_reason='not_applicable' if own else 'unknown'))
        result[channel]={field:dict(Counter(r[field] for r in rows)) for field in ('store','category','price_band','discount_band')}
        result[channel]['posts']=len(rows)
        repeat=defaultdict(list)
        for r in rows:
            if r['product_key'] and r['price'] is not None:repeat[(r['product_key'],r['price'],r['condition'])].append(r['post'])
        result[channel]['confirmed_same_product_price_reposts']=sum(len(v)-1 for v in repeat.values())
        postrows[channel]=rows;pairs[channel]=Counter((r['store'],r['category']) for r in rows)
    ours=defaultdict(list)
    for r in postrows['usasalessearch']:
        if r['product_key']:ours[r['product_key']].append(r['post'])
    for r in postrows['pcpartsdrop']:
        r['same_product_our_posts']=ours.get(r['product_key'],[])
        r['coverage_check']='same_product_published_price_unknown' if r['same_product_our_posts'] else 'no_exact_product_key_match'
    gaps=sorted(((n-pairs['usasalessearch'][key],*key,n,pairs['usasalessearch'][key]) for key,n in pairs['pcpartsdrop'].items()),reverse=True)[:5]
    opportunities={k:set() for k in ('bestbuy','threshold','category')};corrected=[]
    for r in postrows['pcpartsdrop']:
        price,pct=r['price'],r['reported_discount_pct'];cat=r['category']
        minimum,saving=bot.RULES.get(cat,bot.DEFAULT_RULE)
        ok=price is not None and pct is not None and pct<100 and pct>=minimum and price*pct/(100-pct)>=saving
        key=(r['product_key'] or r['post'],price,r['condition'])
        if r['store']=='bestbuy.com' and ok:opportunities['bestbuy'].add(key)
        if cat in ('case','cooler') and price is not None and pct is not None and 20<=pct<minimum and price*pct/(100-pct)>=saving:
            opportunities['threshold'].add(key)
        if r['classifier_category']!=cat and bot.classify(r['title'])==cat:
            corrected.append(r['post'])
            if ok:opportunities['category'].add(key)
    ceilings={k:dict(posts=len(v),per_day=round(len(v)/7,2)) for k,v in opportunities.items()}
    return {'window':data['window'],'summary':result,'largest_store_category_gaps':gaps,'rows':postrows,'opportunity_ceilings':ceilings,'category_fixed_posts':corrected,
            'limits':['Post counts include repeated identical offers, not unique new opportunities.',
                      'Original 192 competitor posts retained; 24 headlines were not returned in the current preview.',
                      'Labels are reproducible checks; historical arrivals and actual rejection causes remain unknown.']}

if __name__=='__main__':
    p=analyze(json.loads(Path('docs/evidence/volume-input-2026-11-26.json').read_text()))
    Path('docs/evidence/volume-breakdown-2026-11-26.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
    for name,summary in p['summary'].items():print(name,json.dumps(summary,ensure_ascii=False))
    print('top5',json.dumps(p['largest_store_category_gaps'],ensure_ascii=False))
    print('checks',dict(Counter(label.split(' (')[0] for r in p['rows']['pcpartsdrop'] for label in r['provable_checks'])))
