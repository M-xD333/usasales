"""Durable publication floor: elapsed time and price rebounds never re-open a sent deal."""
import hashlib
import re
from urllib.parse import parse_qs, urlsplit
import market_prices as M


def cents(price):
    return round(price * 100) if M.number(price) and price > 0 else None


def aliases(deal):
    condition = deal.get('cond', 'New')
    url = M.canonical_url(deal.get('url'))
    if not url:
        return []
    keys = ['url:' + url + '|' + condition]
    domain = M.shop(url)
    asin = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)', url, re.I)
    if domain == 'amazon.com' and asin:
        keys.append('asin:' + asin.group(1).upper() + '|' + condition)
    if domain == 'newegg.com':
        match = re.search(r'/p/([A-Z0-9-]+)(?:/|$)', urlsplit(url).path, re.I)
        item = parse_qs(urlsplit(url).query).get('Item', [None])[0] or (match.group(1) if match else None)
        if item:
            keys.append('newegg-item:' + item.upper() + '|' + condition)
    keys += ['product:' + domain + '|' + identity + '|' + condition for identity in sorted(M.identity_keys(deal))]
    # Hash URLs/identities to keep the permanent ledger small.
    return [hashlib.sha256(key.encode()).hexdigest() for key in keys]


def compatible(record, deal):
    old, new = set(record.get('identities', [])), M.identity_keys(deal)
    return not old or not new or bool(old & new)


def seen(state, deal, legacy_key):
    """Return a confirmed prior floor, including legacy posted keys with no TTL."""
    price = cents(deal.get('price'))
    if price is None:
        return None
    identities = {deal.get('ident')}
    asin = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)', deal.get('url', ''), re.I)
    if asin:
        identities.add('asin:' + asin.group(1).upper())
    prefixes = tuple(str(i) + '|' + deal.get('cond', 'New') + '|' for i in identities if i)
    legacy = []
    for key, at in state.get('posted', {}).items():
        if key == legacy_key or prefixes and key.startswith(prefixes):
            try:
                floor = cents(float(key.rsplit('|', 1)[1]))
            except (ValueError, IndexError):
                continue
            if floor is not None and price >= floor:
                legacy.append({'lowest_cents': floor, 'at': at, 'identities': sorted(M.identity_keys(deal))})
    if legacy:
        return min(legacy, key=lambda r:r['lowest_cents'])
    records = [r for key in aliases(deal)
               if (r := state.get('publication_history', {}).get(key)) and compatible(r, deal)]
    previous = min(records, key=lambda r:r['lowest_cents']) if records else None
    return previous if previous and price >= previous['lowest_cents'] else None


def remember(state, deal, at, lowest_cents=None):
    price = cents(deal.get('price'))
    if price is None:
        return
    if lowest_cents is not None:
        price = min(price, lowest_cents)
    ledger = state.setdefault('publication_history', {})
    identities = sorted(M.identity_keys(deal))
    for key in aliases(deal):
        old = ledger.get(key)
        floor = min(price, old['lowest_cents']) if old and compatible(old, deal) else price
        ledger[key] = {'lowest_cents': floor, 'at': max(at, (old or {}).get('at', 0)), 'identities': identities}


def merge(remote, local):
    out = dict(remote)
    for key, record in local.items():
        old = out.get(key)
        if old and (not old.get('identities') or not record.get('identities') or set(old['identities']) & set(record['identities'])):
            newest = max((old, record), key=lambda r:r.get('at', 0))
            out[key] = {**newest, 'lowest_cents': min(old['lowest_cents'], record['lowest_cents']),
                        'identities': sorted(set(old.get('identities', [])) | set(record.get('identities', [])))}
        elif not old or record.get('at', 0) > old.get('at', 0):
            out[key] = record
    return out
