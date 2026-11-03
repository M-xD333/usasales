"""Public US store catalogs. No aggregator data or invented discount prices."""
import hashlib
import html
import json
import math
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import store_links


class Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children, self.parts = [], []

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())

    def find(self, tag=None, cls=None, **attrs):
        result = []
        for child in self.children:
            if ((tag is None or child.tag == tag)
                    and (cls is None or cls in (child.attrs.get("class") or "").split())
                    and all(child.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items())):
                result.append(child)
            result.extend(child.find(tag, cls, **attrs))
        return result


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, text):
        node = self.current
        while node:
            node.parts.append(text)
            node = node.parent


def money(value):
    # A range, installment, or multiple prices is not a single purchase price.
    s = str(value or "").strip().replace(",", "")
    if not re.fullmatch(r"\$?\s*\d+(?:\.\d{1,2})?", s):
        return None
    amount = float(s.replace("$", "").strip())
    return amount if math.isfinite(amount) and 0 < amount < 100000 else None


def entry(name, url, price, original=None, currency="USD", availability="", base=""):
    url = store_links.merchant_url(urljoin(base, url or ""))
    price = money(price)
    original = money(original)
    if not name or not url or price is None or currency != "USD":
        return None
    # Only accept a link back to the catalog's own store.
    if base and urlsplit(url).hostname != urlsplit(base).hostname:
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        if not (urlsplit(base).hostname or "").endswith(host):
            return None
    if any(x in availability.lower() for x in ("outofstock", "soldout", "discontinued", "preorder", "backorder")):
        return None
    title = html.unescape(str(name)).strip()
    return {"item_id": "store:" + hashlib.sha256(url.encode()).hexdigest()[:24],
            "title": title, "url": url, "discuss_url": "", "summary": "",
            "published_ts": None, "expired": False, "community_score": None, "flair": "",
            "store_price": price, "store_original": original if original and original > price else None,
            "store_currency": "USD", "store_candidates": [url]}


def walk_products(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from walk_products(x)
    elif isinstance(obj, dict):
        typ = obj.get("@type", "")
        if typ == "Product" or isinstance(typ, list) and "Product" in typ:
            yield obj
        else:
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    yield from walk_products(value)


def jsonld_entries(doc, base):
    out = []
    for script in doc.find("script", type="application/ld+json"):
        try:
            data = json.loads(script.text)
        except (ValueError, TypeError):
            continue
        for product in walk_products(data):
            offers = product.get("offers", [])
            offers = offers if isinstance(offers, list) else [offers]
            # AggregateOffer's lowPrice is not a verified price of a specific variant.
            for offer in offers:
                if not isinstance(offer, dict) or "price" not in offer:
                    continue
                e = entry(product.get("name"), product.get("url") or offer.get("url") or
                          (base if "/p/" in base or "/product/" in base else ""),
                          offer.get("price"), currency=offer.get("priceCurrency"),
                          availability=offer.get("availability", ""), base=base)
                if e:
                    out.append(e)
    return out


def first(nodes):
    return nodes[0] if nodes else Node()


def corsair_catalog(doc, base, schema_entries):
    """Use the catalog's own stock and per-SKU regular/final prices."""
    if not (urlsplit(base).hostname or "").endswith("corsair.com"):
        return None
    for script in doc.find("script", id="__NEXT_DATA__"):
        try:
            items = json.loads(script.text)["props"]["pageProps"]["categoryData"]["products"]["items"]
        except (ValueError, KeyError, TypeError):
            continue
        out = []
        for item in items:
            if item.get("stock_status") != "IN_STOCK" or item.get("not_sellable"):
                continue
            prices = item.get("price_range", {})
            lo, hi = prices.get("minimum_price", {}), prices.get("maximum_price", {})
            final, regular = lo.get("final_price", {}), lo.get("regular_price", {})
            if final.get("value") != hi.get("final_price", {}).get("value"):
                continue
            sku = item.get("sku", "").lower()
            url = next((e["url"] for e in schema_entries if sku and sku in e["url"].lower()), None)
            e = entry(item.get("name"), url, final.get("value"),
                      regular.get("value") if regular.get("currency") == "USD" else None,
                      currency=final.get("currency"), base=base) if url else None
            if e:
                out.append(e)
        return out
    return None


def retail_cards(doc, base):
    out = []
    host = urlsplit(base).hostname or ""
    if host.endswith("amazon.com"):
        for card in doc.find(data_component_type="s-search-result"):
            title = first(card.find("h2")).text
            asin = card.attrs.get("data-asin", "")
            if not re.fullmatch(r"[A-Z0-9]{10}", asin):
                continue
            current, original = None, None
            for price in card.find(cls="a-price"):
                value = money(first(price.find(cls="a-offscreen")).text)
                if "a-text-price" in price.attrs.get("class", "").split():
                    original = value
                elif current is None:
                    current = value
            e = entry(title, "https://www.amazon.com/dp/" + asin, current, original, base=base)
            if e:
                out.append(e)
    elif host.endswith("newegg.com"):
        for card in doc.find(cls="item-cell"):
            link = first(card.find("a", cls="item-title"))
            price = first(card.find(cls="price-current"))
            # Read integer/cents only; don't mistake monthly payments or shipping for price.
            amount = first(price.find("strong")).text + first(price.find("sup")).text
            original = first(card.find(cls="price-was-data")).text
            if re.search(r"out of stock|sold out", card.text, re.I):
                continue
            e = entry(link.text, link.attrs.get("href"), amount, original, base=base)
            if e:
                out.append(e)
    else:
        # Magento-style product cards used by a number of manufacturers.
        for card in doc.find(cls="product-item"):
            link = first(card.find("a", cls="product-item-link"))
            current = first(card.find(data_price_type="finalPrice")).attrs.get("data-price-amount")
            original = first(card.find(data_price_type="oldPrice")).attrs.get("data-price-amount")
            if re.search(r"out of stock|sold out", card.text, re.I):
                continue
            e = entry(link.text, link.attrs.get("href"), current, original, base=base)
            if e:
                out.append(e)
    return out


def parse_catalog(markup, base):
    doc = Document(markup).root
    schema = jsonld_entries(doc, base)
    native = corsair_catalog(doc, base, schema)
    entries = native if native is not None else schema + retail_cards(doc, base)
    unique = {}
    for e in entries:
        # Conflicting variant prices for the same URL are unsafe: skip the product.
        key = e["item_id"]
        if key in unique and (unique[key] is None or unique[key]["store_price"] != e["store_price"]):
            unique[key] = None
        else:
            unique[key] = e
    return [e for e in unique.values() if e][:100]


def product_links(markup, base):
    """Bounded discovery from the store itself, never via deal aggregators."""
    host = urlsplit(base).hostname or ""
    patterns = {"crucial.com": r"/(?:ssd|memory)/[^/]+/[^/?]+$",
                "store.google.com": r"/(?:us/)?product/pixel_\d+(?:a|_pro|_pro_fold)?$",
                "asus.com": r"/us/(?!products/)[^/]+\.html$"}
    pattern = next((p for h, p in patterns.items() if host.endswith(h)), r"/p/[^/]+/[^/]+")
    urls = []
    for a in Document(markup).root.find("a"):
        url = urljoin(base, a.attrs.get("href", ""))
        if urlsplit(url).hostname == host and re.search(pattern, urlsplit(url).path, re.I):
            if store_links.merchant_url(url):
                urls.append(url)
    return list(dict.fromkeys(urls))[:4]
