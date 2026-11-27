"""Disabled-without-key Woot feed adapter; not registered in the publishing loop."""
import math
import os
from datetime import datetime,timezone
import requests
import direct_stores
import store_links

FEEDS={'Computers','Electronics'}
MIN_INTERVAL_SECONDS=900  # Future scheduler: <=192 feed requests/day, below the documented 1000/day quota.


def exact_price(value):
    if not isinstance(value,dict):return None
    lo,hi=value.get('Minimum'),value.get('Maximum')
    return float(lo) if type(lo) in (int,float) and type(hi) in (int,float) and math.isfinite(lo) and lo>0 and lo==hi else None


def normalize(payload,now=None):
    now=datetime.now(timezone.utc) if now is None else now;entries=[];notes=[]
    if not isinstance(payload,dict) or not isinstance(payload.get('Items'),list):raise ValueError('invalid Woot FeedPage')
    for item in payload['Items']:
        if not isinstance(item,dict):continue
        if item.get('IsSoldOut') is True:notes.append('sold_out');continue
        try:expired=bool(item.get('EndDate') and datetime.fromisoformat(item['EndDate'])<now)
        except (ValueError,TypeError):expired=False
        if expired:notes.append('expired');continue
        url=store_links.merchant_url(item.get('Url'))
        if not url or not store_links.on_domain(url,('woot.com',)):notes.append('invalid_store_url');continue
        price=exact_price(item.get('SalePrice'));original=exact_price(item.get('ListPrice'))
        if item.get('IsAvailableOnMobileAppOnly'):price=None;notes.append('app_only_requires_check')
        if price is None:
            # Retain a candidate, never publish the minimum of a multi-variant range.
            entries.append({'item_id':'woot:'+str(item.get('OfferId','')),'url':url,'title':item.get('Title',''),
                            'store_price':None,'store_currency':'USD','store_issue':'ambiguous_woot_variant',
                            'store_original':original,'published_ts':None,'expired':False,'store_candidates':[url]})
            notes.append('price_or_variant_requires_check');continue
        entry=direct_stores.entry(item.get('Title'),url,price,original,currency='USD',base=url,image=item.get('Photo'),
                                 meta={'currency':'USD','market':'US','in_stock':True if item.get('IsSoldOut') is False else None,'condition':item.get('Condition') or None})
        if entry:
            entry['item_id']='woot:'+str(item.get('OfferId',''));entries.append(entry)
    return {'entries':entries,'notes':notes}


def fetch(feed,key=None,session=requests,now=None):
    key=key if key is not None else os.getenv('WOOT_API_KEY')
    if not key:return {'enabled':False,'entries':[],'notes':['WOOT_API_KEY missing; no request']}
    if feed not in FEEDS:raise ValueError('unsupported Woot feed')
    try:
        r=session.get('https://developer.woot.com/feed/'+feed,
                      headers={'x-api-key':key,'Accept':'application/json','User-Agent':'usasales-woot/1.0'},timeout=(3,7),allow_redirects=False)
        if r.status_code!=200:return {'enabled':True,'entries':[],'notes':['HTTP '+str(r.status_code)],'retry_after':r.headers.get('Retry-After')}
        return dict(normalize(r.json(),now),enabled=True)
    except (requests.RequestException,ValueError):return {'enabled':True,'entries':[],'notes':['Woot request/response unavailable']}
