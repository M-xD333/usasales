"""Image URLs supplied by the same feed/product, with optional product-page metadata."""
import html
import ipaddress
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import store_links
import merchant_http
import product_weight


def image_url(value, base=""):
    if isinstance(value, list):
        return next((u for x in value if (u := image_url(x, base))), None)
    if isinstance(value, dict):
        return image_url(value.get("url") or value.get("contentUrl"), base)
    if not isinstance(value, str) or not value.strip():
        return None
    url = store_links.web_url(urljoin(base, html.unescape(value)))
    if not url:
        return None
    host = urlsplit(url).hostname or ""
    if host == "localhost" or host.endswith((".local", ".localhost")):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    if re.search(r"(?:logo|avatar|tracking[-_]?pixel|spacer)\b|\.svg(?:\?|$)", urlsplit(url).path, re.I):
        return None
    if host == "assets.corsair.com" and "/image/upload/" in url and "f_auto" in urlsplit(url).path:
        # The store serves WebP by default; request its documented JPEG rendition for Telegram.
        parts = urlsplit(url)
        path = re.sub(r"\bf_auto\b", "f_jpg", parts.path)
        path = re.sub(r"\.(?:webp|avif)$", ".jpg", path, flags=re.I)
        url = urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
    return url


class Images(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.meta, self.images, self.structured = base, [], [], []
        self.script, self.script_text = False, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")) in ("og:image", "og:image:secure_url", "twitter:image"):
            self.meta.append(a.get("content"))
        if tag == "img":
            if any((a.get(k) or "").isdigit() and int(a[k]) < 40 for k in ("width", "height")):
                return
            self.images.append(a.get("data-src") or a.get("src"))
        if tag == "script" and a.get("type") == "application/ld+json":
            self.script, self.script_text = True, []

    def handle_data(self, text):
        if self.script:
            self.script_text.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.script:
            try:
                self.collect(json.loads("".join(self.script_text)))
            except (ValueError, TypeError):
                pass
            self.script = False

    def collect(self, obj):
        if isinstance(obj, list):
            for x in obj:
                self.collect(x)
        elif isinstance(obj, dict):
            if obj.get("@type") == "Product":
                self.structured.append(obj.get("image"))
            for key in ("@graph", "item", "itemListElement"):
                if key in obj:
                    self.collect(obj[key])


def extract_image(markup, base="", product_page=False):
    parser = Images(base)
    parser.feed(markup or "")
    values = parser.meta + parser.structured
    if not product_page:
        values += parser.images
    return image_url(values, base)


def from_feed(entry, markup, base):
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []):
            if media.get("medium") not in (None, "image"):
                continue
            if media.get("type") and not media["type"].startswith("image/"):
                continue
            url = image_url(media.get("url"), base)
            if url:
                return url
    for enclosure in entry.get("enclosures", []):
        if str(enclosure.get("type", "")).startswith("image/"):
            url = image_url(enclosure.get("href") or enclosure.get("url"), base)
            if url:
                return url
    return extract_image(markup, base)


def from_product_details(url, session=requests):
    if not store_links.merchant_url(url):
        return None
    # Do not follow redirects to arbitrary hosts; this is a best-effort metadata lookup.
    try:
        r = merchant_http.get(url, session=session, headers={"User-Agent": "Mozilla/5.0"}, timeout=(3, 5), stream=True)
        if r is None:
            return None
        try:
            if r.status_code != 200:
                return None
            chunks, size = [], 0
            for chunk in r.iter_content(16384):
                chunks.append(chunk)
                size += len(chunk)
                if size >= 1_000_000:
                    break
            markup = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
            final_url = r.url if isinstance(r.url, str) else url
            return {'image_url': extract_image(markup, final_url, product_page=True),
                    'weight_kg': product_weight.from_markup(markup, final_url)}
        finally:
            r.close()
    except (requests.RequestException, ValueError):
        return None


def from_product_page(url, session=requests):
    details = from_product_details(url, session=session)
    return details.get('image_url') if details else None
