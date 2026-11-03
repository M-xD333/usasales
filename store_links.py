"""Extract merchant links without publishing an aggregator or tracking URL."""
import html
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import requests

MERCHANTS = (
    "amazon.com", "newegg.com", "bestbuy.com", "walmart.com", "microcenter.com",
    "bhphotovideo.com", "woot.com", "ebay.com", "costco.com", "target.com",
    "adorama.com", "antonline.com", "staples.com", "officedepot.com", "dell.com",
    "lenovo.com", "hp.com", "samsung.com", "lg.com", "asus.com", "acer.com",
    "westerndigital.com", "crucial.com", "corsair.com", "logitech.com", "monoprice.com",
)
AGGREGATORS = ("slickdeals.net", "techbargains.com", "bensbargains.net", "9to5toys.com",
               "pcworld.com", "tomshardware.com", "dealnews.com", "reddit.com")
TRACKERS = ("anrdoezrs.net", "jdoqocy.com", "dpbolvw.net", "tkqlhce.com", "kqzyfj.com",
            "click.linksynergy.com", "go.skimresources.com", "redirect.viglink.com",
            "awin1.com", "shareasale.com", "zdcs.link", "amzn.to", "newegg.io")
URL_KEYS = ("url", "u", "u1", "u2", "murl", "p", "target", "dest", "destination",
            "redirect", "redirect_url", "redirect_uri", "uddg")


def web_url(value):
    value = html.unescape(str(value or "")).strip()
    try:
        p = urlsplit(value)
        if (p.scheme not in ("http", "https") or not p.hostname or p.username or p.password
                or p.port not in (None, 80, 443) or re.search(r'[\s<>"\\\x00-\x1f]', value)):
            return None
        return value
    except ValueError:
        return None


def on_domain(url, domains):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def merchant_url(value):
    url = web_url(value)
    if not url or not on_domain(url, MERCHANTS):
        return None
    p = urlsplit(url)
    if on_domain(url, ("amazon.com",)):
        m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", p.path + "/", re.I)
        return "https://www.amazon.com/dp/" + m.group(1).upper() if m else None
    if on_domain(url, ("newegg.com",)):
        if not re.search(r"/p/[A-Z0-9]+(?:/|$)", p.path, re.I) and not parse_qs(p.query).get("Item"):
            return None
    if p.path.rstrip("/") == "" or re.search(r"/(?:search|cart|login|signin|stores?)(?:[/.]|$)", p.path, re.I):
        return None
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def unwrap(value, depth=0):
    """Decode only explicit URL parameters, never infer a product from a title."""
    url = web_url(value)
    if not url or depth > 5:
        return None
    direct = merchant_url(url)
    if direct:
        return direct
    if not on_domain(url, AGGREGATORS + TRACKERS):
        return None
    params = parse_qs(urlsplit(url).query)
    for key in URL_KEYS:
        for nested in params.get(key, []):
            for _ in range(3):
                found = unwrap(nested, depth + 1)
                if found:
                    return found
                decoded = unquote(nested)
                if decoded == nested:
                    break
                nested = decoded
    return None


class Links(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.urls, self.current = base, [], None
        self.script, self.script_text = False, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self.current = (a, [])
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.script, self.script_text = True, []

    def handle_data(self, text):
        if self.current:
            self.current[1].append(text)
        if self.script:
            self.script_text.append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            a, text = self.current
            label = " ".join(text) + " " + " ".join(a.get(k, "") for k in ("class", "id", "aria-label", "title"))
            cta = bool(re.search(r"buy|shop|see deal|view deal|go to deal|get deal|merchant|shopnow|deal.?button", label, re.I))
            for key in ("href", "data-href", "data-url", "data-destination"):
                if a.get(key):
                    url = urljoin(self.base, a[key])
                    direct = unwrap(url)
                    if direct:
                        self.urls.append((0 if cta else 1, direct))
                    elif web_url(url) and on_domain(url, AGGREGATORS + TRACKERS):
                        if cta or urlsplit(url).path.startswith(("/click", "/go/", "/redirect")):
                            self.urls.append((0 if cta else 1, url))
            self.current = None
        if tag == "script" and self.script:
            try:
                self.product_data(json.loads("".join(self.script_text)))
            except (ValueError, TypeError):
                pass
            self.script = False

    def product_data(self, obj):
        if isinstance(obj, list):
            for item in obj:
                self.product_data(item)
        elif isinstance(obj, dict):
            if obj.get("@type") in ("Product", "Offer", "AggregateOffer"):
                direct = unwrap(urljoin(self.base, str(obj.get("url", ""))))
                if direct:
                    self.urls.append((0, direct))
            for key in ("@graph", "offers", "itemListElement", "item"):
                if key in obj:
                    self.product_data(obj[key])


def extract_links(markup, base="", product_page=False):
    parser = Links(base)
    parser.feed(markup or "")
    return list(dict.fromkeys(u for priority, u in sorted(parser.urls, key=lambda x: x[0])
                              if not product_page or priority == 0))[:20]


def follow(url, session, deadline):
    """Follow bounded, known redirects; do not fetch arbitrary hosts from feed HTML."""
    for _ in range(6):
        if time.monotonic() >= deadline or not web_url(url):
            return None
        direct = unwrap(url)
        if direct:
            return direct
        if not on_domain(url, AGGREGATORS + TRACKERS):
            return None
        r = session.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; deals-bot/0.2)"},
                        timeout=(3, min(6, max(0.1, deadline - time.monotonic()))),
                        allow_redirects=False, stream=True)
        try:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            if r.status_code != 200:
                return None
            # Limit HTML size even if a feed links a large non-HTML resource.
            body = bytearray()
            for chunk in r.iter_content(16384):
                body.extend(chunk)
                if len(body) >= 1_000_000 or time.monotonic() >= deadline:
                    break
            markup = bytes(body[:1_000_000]).decode(r.encoding or "utf-8", errors="replace")
            links = extract_links(markup, url, product_page=True)
            for candidate in links:
                direct = unwrap(candidate)
                if direct:
                    return direct
            return links[:3]
        finally:
            r.close()
    return None


def resolve_store_url(url, candidates=(), discuss_url="", session=requests, budget=15):
    deadline = time.monotonic() + budget
    queue = list(dict.fromkeys([*candidates, url, discuss_url]))
    # Prefer links already in the RSS item over fetching its discussion page.
    for value in queue:
        direct = unwrap(value)
        if direct:
            return direct
    seen = set()
    while queue and len(seen) < 4 and time.monotonic() < deadline:
        value = queue.pop(0)
        if not value or value in seen:
            continue
        seen.add(value)
        try:
            result = follow(value, session, deadline)
        except (requests.RequestException, ValueError):
            continue
        if isinstance(result, str):
            return result
        if result:
            queue = result + queue
    return None
