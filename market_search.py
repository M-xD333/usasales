"""Discover exact-product merchant URLs. Search-page prices are never evidence."""
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urljoin

import requests
import direct_stores
import market_prices as M

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'
BRANDS = {M.normalize(d.split('.')[0]): d for d in M.FIRST_PARTY}
BRANDS.update(google='store.google.com', westerndigital='westerndigital.com')
RETAIL = {
    'amazon.com': 'https://www.amazon.com/s?k={query}',
    'newegg.com': 'https://www.newegg.com/p/pl?d={query}',
    'bhphotovideo.com': 'https://www.bhphotovideo.com/c/search?q={query}&sts=ma',
    'bestbuy.com': 'https://www.bestbuy.com/site/searchpage.jsp?st={query}',
}
OFFICIAL = {'corsair.com': 'https://www.corsair.com/us/en/search?q={query}'}


def terms(deal):
    """Use manufacturer metadata, never infer a part number from random title digits."""
    if not M.identity(deal):
        return None
    meta = deal.get('product_meta') or {}
    mpn = str(meta.get('mpn') or '').strip()
    brand = str(meta.get('brand') or '').strip()
    valid_mpn = M.mpn_key(meta)
    if valid_mpn:
        return {'query': mpn, 'tokens': [M.normalize(mpn)], 'exact_identifier': True,
                'brand': BRANDS.get(M.normalize(brand))}
    gtins = [str(g) for g in meta.get('gtins', []) if M.valid_gtin(g)]
    if gtins:
        return {'query': gtins[0], 'tokens': [M.normalize(gtins[0])], 'exact_identifier': True,
                'brand': BRANDS.get(M.normalize(brand))}
    if deal.get('cat') == 'pixel' and M.pixel_key(deal['title']):
        key = M.pixel_key(deal['title']).split(':')
        # A complete version is allowed for discovery; card verification remains mandatory.
        model = re.search(r'\bpixel\s+\d+a?(?:\s+pro)?(?:\s+xl|\s+fold)?\b', deal['title'], re.I).group()
        query = f'Google {model} {key[2]}GB {key[3]} unlocked'
        return {'query': query, 'tokens': [key[1], key[2], key[3], 'unlocked'],
                'brand': 'store.google.com'}
    return None


def product_url(url, domain):
    url = M.canonical_url(url)
    if not url or M.shop(url) != domain:
        return None
    # canonical_url rejects Amazon/Newegg search pages; other merchants need explicit product paths.
    patterns = {'bhphotovideo.com': r'/c/product/\d+', 'bestbuy.com': r'/site/.+/(?:\d+)\.p',
                'corsair.com': r'/us/en/p/', 'store.google.com': r'/product/'}
    if domain in patterns and not re.search(patterns[domain], url, re.I):
        return None
    if re.search(r'/(?:search|support|blog|news|articles?)(?:[/?]|$)', url, re.I):
        return None
    return url


def matching(text, spec):
    if spec.get('exact_identifier'):
        return all(re.search(r'(?<![a-z0-9])' + r'[\W_]*'.join(map(re.escape, token)) + r'(?![a-z0-9])',
                             text, re.I) for token in spec['tokens'])
    normalized = M.normalize(text)
    return all(token in normalized for token in spec['tokens'])


def links_from_html(body, base, domain, spec):
    urls = []
    for anchor in direct_stores.Document(body).root.find('a'):
        href = urljoin(base, anchor.attrs.get('href', ''))
        url = product_url(href, domain)
        if not url:
            continue
        node = anchor
        for _ in range(4):
            context = ' '.join((href, node.text, str(anchor.attrs.get('title', ''))))
            if len(context) < 2000 and matching(context, spec):
                urls.append(url)
                break
            if not node.parent or not node.parent.parent or node.parent.tag in ('html', 'body', 'main'):
                break
            node = node.parent
    return list(dict.fromkeys(urls))[:8]


def read_page(url, session, deadline):
    if time.monotonic() >= deadline:
        return None, 'лимит времени'
    timeout = max(.1, min(4, (deadline - time.monotonic()) / 2))
    with session.get(url, headers={'User-Agent': UA, 'Accept-Language': 'en-US'},
                     timeout=(min(2, timeout), timeout), stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            return None, f'HTTP {response.status_code}'
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 2_000_000 or time.monotonic() >= deadline:
                return None, 'лимит размера/времени'
            chunks.append(chunk)
        body = b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')
    if re.search(r'captcha|verify you are human|<title[^>]*>\s*Just a moment', body[:15000], re.I):
        return None, 'проверка браузера'
    return body, ''


def discover(deal, limit=4, deadline=None, session=requests):
    """Bounded native store searches, plus a manufacturer-only index lookup if necessary."""
    spec = terms(deal)
    if not spec:
        return {'urls': [], 'notes': ['нет точного артикула'], 'requests': 0}
    deadline = deadline or time.monotonic() + 20
    own = M.shop(deal.get('url'))
    plans = []
    brand = spec['brand']
    if brand and brand != own:
        template = OFFICIAL.get(brand)
        if template:
            plans.append((brand, template.format(query=quote_plus(spec['query'])), False))
        else:
            query = f'site:{brand} "{spec["query"]}"'
            plans.append((brand, 'https://www.bing.com/search?format=rss&q=' + quote_plus(query), True))
    plans += [(domain, template.format(query=quote_plus(spec['query'])), False)
              for domain, template in RETAIL.items() if domain != own]
    urls, notes, count = [], [], 0
    for domain, url, rss in plans[:max(0, min(limit, 4))]:
        if time.monotonic() >= deadline:
            break
        count += 1
        try:
            body, error = read_page(url, session, deadline)
            found = []
            if body:
                if rss:
                    for item in ET.fromstring(body).findall('./channel/item'):
                        link = item.findtext('link') or ''
                        direct = product_url(link, domain)
                        if direct and matching((item.findtext('title') or '') + ' ' + link, spec):
                            found.append(direct)
                else:
                    found = links_from_html(body, url, domain, spec)
            notes.append(domain + ': ' + (error or f'точных ссылок {len(found)}'))
            urls.extend(found)
        except (requests.RequestException, ET.ParseError, ValueError, TypeError, RecursionError) as ex:
            notes.append(domain + ': ' + type(ex).__name__)
    return {'urls': list(dict.fromkeys(urls))[:12], 'notes': notes, 'requests': count}
