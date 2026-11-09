"""Semantic checkpoint merge when another writer changed data on the remote."""
import copy
import json
import checkpoints as C
import offer_queue as Q
import publication_history as P


def merge_state(remote, local):
    out = {**copy.deepcopy(remote), **copy.deepcopy(local)}
    for name in ('posted', 'processed'):
        out[name] = dict(remote.get(name, {}))
        for key, stamp in local.get(name, {}).items():
            out[name][key] = max(stamp, out[name].get(key, stamp))
    for name, stamp in (('assessment_cache', 'at'), ('price_events', 'observed_at'), ('sources', 'last_attempt')):
        out[name] = dict(remote.get(name, {}))
        for key, value in local.get(name, {}).items():
            if value.get(stamp, 0) >= out[name].get(key, {}).get(stamp, 0):
                out[name][key] = value
    out['publication_history'] = P.merge(remote.get('publication_history', {}), local.get('publication_history', {}))
    candidates = Q.merge_latest(list(local.get('pending', {}).values()), remote.get('pending', {}))
    out['pending'] = {}
    for d in candidates:
        delivered = out.get('posted', {}).get(f"{d['ident']}|{d['cond']}|{d['price']}", 0)
        if not delivered or delivered < d.get('fetched_ts', 0):
            out['pending'][Q.pkey(d)] = d
    out['review'] = list({json.dumps(v, sort_keys=True): v for v in remote.get('review', []) + local.get('review', [])}.values())[-100:]
    market = {}
    for field, stamp in (('quotes','observed_at'),('products','seen_at'),('searched','at')):
        market[field] = {}
        for origin in (remote, local):
            for key, value in origin.get('market_prices', {}).get(field, {}).items():
                if value.get(stamp,0) >= market[field].get(key,{}).get(stamp,0):
                    market[field][key]=value
    market['checked'] = dict(remote.get('market_prices',{}).get('checked',{}))
    for key, value in local.get('market_prices',{}).get('checked',{}).items():
        market['checked'][key] = max(value,market['checked'].get(key,value))
    market['brand_searched'] = dict(remote.get('market_prices', {}).get('brand_searched', {}))
    for key, value in local.get('market_prices', {}).get('brand_searched', {}).items():
        market['brand_searched'][key] = max(value, market['brand_searched'].get(key, value))
    if remote.get('market_prices') or local.get('market_prices'):
        out['market_prices'] = market
    return C.validate_state(out)


def merge_history(remote, local):
    out = {}
    for key in remote.keys() | local.keys():
        values = {json.dumps(v):v for v in remote.get(key, []) + local.get(key, [])}
        out[key] = sorted(values.values(),key=lambda v:v[0])
    return C.validate_history(out)
