import copy
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
    import product_weight as W
    import weight_knowledge as K
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
            if old and set(K.identity(old)) & set(K.identity(d)):
                for field in W.WEIGHT_FIELDS:
                    if field not in d and field in old: d[field]=copy.deepcopy(old[field])
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


def delivery_protected(deal):
    return deal.get('_queue', {}).get('status') in ('sending', 'delivery_unknown')


def prioritize(deals):
    """Round-robin sources/brands within quality tiers, newest first per group."""
    from collections import defaultdict, deque
    tiers = defaultdict(lambda: defaultdict(list))
    for d in deals:
        extreme = (M.number(d.get('price')) and M.number(d.get('original'))
                   and 0 < d['price'] < .15 * d['original'])
        tier = (0 if delivery_protected(d) else 1 if extreme else
                4 if d.get('cat') in ('other', 'review') else
                3 if d.get('cat') in ('laptop', 'prebuilt') else 2)
        brand = M.normalize((d.get('product_meta') or {}).get('brand')) or M.shop(d.get('url')) or ''
        tiers[tier][(d.get('source_id', ''), brand)].append(d)
    out = []
    for tier in sorted(tiers):
        groups = {k: deque(sorted(v, key=lambda d: -(d.get('published_ts') or d.get('fetched_ts', 0))))
                  for k, v in sorted(tiers[tier].items())}
        while groups:
            for key in list(groups):
                out.append(groups[key].popleft())
                if not groups[key]:
                    del groups[key]
    return out


def bound_pending(pending, limit=500):
    """Eviction is not a rejection: no processed/floor/delivery markers are written.

    Uncertain deliveries are protected even if they alone exceed the soft cap.
    Incoming deals are still assessed before this end-of-cycle retention bound.
    """
    ordered = prioritize(list(pending.values()))
    protected = [d for d in ordered if delivery_protected(d)]
    rest = [d for d in ordered if not delivery_protected(d)]
    keep = protected + rest[:max(0, limit - len(protected))]
    removed = len(pending) - len(keep)
    pending.clear()
    pending.update((pkey(d), d) for d in keep)
    return removed
