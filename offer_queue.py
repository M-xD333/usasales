"""Queue identity, revisions and price events. No I/O or publication policy."""
import hashlib
import json
import market_prices as M


def listing_key(deal):
    url = M.canonical_url(deal.get('url'))
    if url and deal.get('trust') == 'merchant':
        return 'url:' + url
    return str(deal.get('source_id', '')) + ':' + str(deal['id'])


def pkey(deal):
    return f"{deal['id']}@{deal['price']}"


def merge_latest(deals, pending):
    latest = {}
    urls = {}
    ids = {}
    # Incoming entries win ties against last cycle's queue.
    for d in [*deals, *pending.values()]:
        item = (d.get('source_id'), d['id'])
        url = M.canonical_url(d.get('url'))
        key = ids.get(item) or (urls.get(url) if url else None) or listing_key(d)
        ids[item] = key
        if url:
            urls[url] = key
        old = latest.get(key)
        if old is None or d.get('fetched_ts', 0) > old.get('fetched_ts', 0):
            if old and d['price'] == old['price']:
                d.setdefault('_queue', old.get('_queue', {}).copy())
            latest[key] = d
        elif d['price'] == old['price']:
            old.setdefault('_queue', d.get('_queue', {}).copy())
    return list(latest.values())


def revision(deal, cfg, rules_version, parser_version):
    fields = {key: deal.get(key) for key in ('title', 'price', 'original', 'price_kind', 'flags',
              'cond', 'cat', 'expired', 'community_score', 'product_meta', 'publish')}
    fields.update(rules_version=rules_version, parser_version=parser_version, rules=cfg['rules'])
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def price_event(deal, state, now):
    key = listing_key(deal)
    old = state.setdefault('price_events', {}).get(key)
    observed = deal.get('fetched_ts', now)
    if old and observed < old.get('observed_at', 0):
        deal['_rebound_at'] = old.get('rebound_at', 0)
        return
    price = deal.get('price')
    rebound = old.get('rebound_at', 0) if old else 0
    rising = old.get('rising', False) if old else False
    if old and price is not None and old.get('price') is not None:
        if price > old['price']:
            rising = True
        elif price < old['price'] and rising:
            rebound, rising = now, False
    state['price_events'][key] = {'price': price, 'observed_at': observed,
                                 'rising': rising, 'rebound_at': rebound}
    deal['_rebound_at'] = rebound


def history_eligible(deal, now, max_age):
    return (deal.get('price') is not None and deal.get('cat') not in ('other', 'review')
            and not deal.get('expired') and deal.get('published_ts') is not None
            and 0 <= now - deal['published_ts'] <= max_age * 3600
            and deal.get('price_kind', 'regular') == 'regular'
            and not set(deal.get('flags', [])) - {'free_shipping'})
