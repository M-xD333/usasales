"""Conservative cross-store comparisons using fresh, attributable product offers."""
import math
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import store_links

TTL = 900
FIRST_PARTY = ("corsair.com", "samsung.com", "crucial.com", "asus.com", "msi.com",
               "store.google.com", "dell.com", "lenovo.com", "hp.com", "kingston.com",
               "westerndigital.com", "sandisk.com", "logitech.com", "razer.com", "elgato.com",
               "steelseries.com", "hyperx.com", "shure.com", "audio-technica.com")
RETAIL_SELLERS = {"amazon.com": {"amazon.com", "amazon"},
                  "newegg.com": {"newegg", "newegg.com"},
                  "bestbuy.com": {"best buy", "bestbuy.com"},
                  "walmart.com": {"walmart", "walmart.com"},
                  "bhphotovideo.com": {"b&h", "b&h photo", "b&h photo video"}}
CONDITIONAL = re.compile(
    r"trade[ -]?in|bill credits?|/mo\b|per month|activation|new line|port[ -]?in|"
    r"with .{0,30}plan|members? only|membership|prime members?|cashback|rebate|"
    r"gift card|open[ -]?box|refurbished|renewed|\bused\b|bundle", re.I)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def canonical_url(url):
    url = store_links.merchant_url(url)
    if not url:
        return None
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in
             {"tag", "ref", "ref_", "affid", "affiliate", "clickid", "irgwc", "cm_sp", "cm_mmc", "queryid", "position"}]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def shop(url):
    host = (urlsplit(url or "").hostname or "").lower()
    for domain in FIRST_PARTY + tuple(RETAIL_SELLERS) + store_links.MERCHANTS:
        if host == domain or host.endswith("." + domain):
            # ASUS shop domains and brand storefronts count as one seller.
            return domain
    return None


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def valid_gtin(value):
    s = str(value or "")
    if not s.isdigit() or len(s) not in (8, 12, 13, 14) or set(s) == {"0"}:
        return None
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(s[:-1])))
    return s.zfill(14) if (10 - total % 10) % 10 == int(s[-1]) else None


def pixel_key(text):
    model = re.search(r"\bpixel\s+(\d+a?)(\s+pro)?(\s+xl|\s+fold)?\b", text, re.I)
    sizes = re.findall(r"\b(128|256|512)\s*gb\b|\b(1)\s*tb\b", text, re.I)
    capacities = {int(gb) if gb else 1024 for gb, tb in sizes}
    colors = [c for c in ("obsidian", "porcelain", "moonstone", "jade", "frost", "indigo",
                          "peony", "hazel", "rose quartz", "limoncello", "lemongrass", "snow", "chalk")
              if re.search(r"\b" + c + r"\b", text, re.I)]
    if not model or len(capacities) != 1 or len(colors) != 1:
        return None
    return "pixel:" + normalize(model.group()) + ":" + str(capacities.pop()) + ":" + normalize(colors[0]) + ":unlocked:us"


def mpn_key(meta):
    brand, mpn = normalize(meta.get("brand")), normalize(meta.get("mpn"))
    if brand and len(mpn) >= 5 and re.search(r"[a-z]", mpn) and re.search(r"\d", mpn):
        return "mpn:" + brand + ":" + mpn
    return None


def identity(deal):
    """No fuzzy title similarity. Pixel fallback requires the full retail variant."""
    meta = deal.get("product_meta") or {}
    if deal.get("cond") != "New" or CONDITIONAL.search(deal.get("title", "")):
        return None
    if deal.get("flags") or deal.get("price_kind", "regular") != "regular":
        # Free shipping is a public property, not an eligibility condition.
        if set(deal.get("flags", [])) - {"free_shipping"} or deal.get("price_kind", "regular") != "regular":
            return None
    text = deal.get("title", "")
    if deal.get("cat") == "pixel":
        # A phone with an unspecified carrier must not be compared with unlocked stock.
        if not re.search(r"\b(?:factory[ -]?)?unlocked\b", text, re.I):
            return None
        if re.search(r"\b(?:international|global version|japan|japanese|renewed|dual sim import)\b", text, re.I):
            return None
        full_variant = pixel_key(text)
        if full_variant:
            return full_variant
    for value in meta.get("gtins", []):
        gtin = valid_gtin(value)
        if gtin:
            return "gtin:" + gtin
    return mpn_key(meta)


def identity_keys(deal):
    key = identity(deal)
    if not key:
        return set()
    meta = deal.get("product_meta") or {}
    keys = {key}
    keys.update("gtin:" + g for x in meta.get("gtins", []) if (g := valid_gtin(x)))
    if (mpn := mpn_key(meta)):
        keys.add(mpn)
    return keys


def quote(deal, observed_at):
    meta = deal.get("product_meta") or {}
    url = canonical_url(deal.get("url"))
    key = identity(deal)
    domain = shop(url)
    price = deal.get("price")
    if (not key or not domain or deal.get("price_origin") != "store" or not number(price)
            or price <= 0 or meta.get("currency") != "USD" or meta.get("in_stock") is not True
            or meta.get("market") != "US" or meta.get("condition") != "New"):
        return None
    # Marketplace pages need a verified first-party seller, not merely the platform's domain.
    if domain not in FIRST_PARTY and str(meta.get("seller") or "").strip().lower() not in RETAIL_SELLERS.get(domain, set()):
        return None
    shipping = meta.get("shipping")
    if shipping is not None and (not number(shipping) or shipping < 0):
        return None
    return {"key": key, "keys": sorted(identity_keys(deal)),
            "variant": pixel_key(deal["title"]) if deal.get("cat") == "pixel" else None,
            "url": url, "shop": domain, "price": price,
            "shipping": shipping, "observed_at": observed_at, "title": deal["title"],
            "gtins": [g for x in meta.get("gtins", []) if (g := valid_gtin(x))],
            "mpn": normalize(meta.get("mpn")), "brand": normalize(meta.get("brand"))}


class MarketPrices:
    def __init__(self, state, now, verifier=None, max_requests=12, budget=60,
                 discoverer=None, max_search_requests=4):
        self.now, self.verifier = now, verifier
        self.remaining, self.deadline = max_requests, time.monotonic() + budget
        self.discoverer, self.search_remaining = discoverer, max_search_requests
        self.state = state.setdefault("market_prices", {"quotes": {}, "checked": {}})
        self.state["quotes"] = {u: q for u, q in self.state.get("quotes", {}).items()
                                if number(q.get("observed_at")) and 0 <= now - q["observed_at"] <= TTL}
        self.state["checked"] = {u: t for u, t in self.state.get("checked", {}).items()
                                 if number(t) and 0 <= now - t < 600}
        self.state["products"] = {u: p for u, p in self.state.get("products", {}).items()
                                  if number(p.get("seen_at")) and 0 <= now - p["seen_at"] < 30 * 86400}
        self.seeds = {}
        self.state["searched"] = {k: v for k, v in self.state.get("searched", {}).items()
                                  if 0 <= now - v.get("at", 0) < 86400}
        for url, product in self.state["products"].items():
            for key in product.get("keys", [product["key"]]):
                self.seeds.setdefault(key, []).append(url)

    def add(self, deal):
        keys = identity_keys(deal)
        for key in keys:
            seeds = self.seeds.setdefault(key, [])
            for url in [deal.get("url"), *deal.get("store_candidates", [])]:
                url = canonical_url(url)
                if url and url not in seeds:
                    seeds.append(url)
                if url:
                    self.state["products"][url] = {"key": identity(deal), "keys": sorted(keys), "seen_at": self.now}
        q = quote(deal, deal.get("fetched_ts", 0))
        if q and 0 <= self.now - q["observed_at"] <= TTL:
            self.state["quotes"][q["url"]] = q
        else:
            # An explicit new observation that is unavailable/conditional invalidates old evidence.
            url = canonical_url(deal.get("url"))
            if deal.get("price_origin") == "store" and url:
                self.state["quotes"].pop(url, None)
        self.prune()

    def prune(self):
        for field, limit in (("quotes", 1000), ("checked", 500), ("products", 2000), ("searched", 1000)):
            values = self.state[field]
            if len(values) > limit:
                def stamp(item):
                    return item[1]["observed_at"] if field == "quotes" else item[1]["seen_at"] if field == "products" else item[1]["at"] if field == "searched" else item[1]
                self.state[field] = dict(sorted(values.items(), key=stamp)[-limit:])

    def verify(self, url):
        url = canonical_url(url)
        if not url:
            return None
        cached = self.state["quotes"].get(url)
        if cached and self.now - cached["observed_at"] <= 900:
            return cached
        if url in self.state["checked"] or not self.verifier or self.remaining <= 0 or time.monotonic() >= self.deadline:
            return None
        self.remaining -= 1
        self.state["checked"][url] = self.now
        try:
            deal = self.verifier(url)
        except Exception:
            deal = None
        # Remove stale evidence even when the recheck returned 403 or unknown stock.
        self.state["quotes"].pop(url, None)
        q = quote(deal, self.now) if deal else None
        if q and q["url"] == url:
            self.state["quotes"][url] = q
        self.prune()
        return q

    def compare(self, deal, min_pct, min_sav):
        key = identity(deal)
        if not key and self.discoverer:
            # A feed may omit the manufacturer's part number; only the store may supply it.
            for raw in [deal.get("url"), *deal.get("store_candidates", [])]:
                current = self.verify(raw)
                if current:
                    deal["product_meta"] = {"brand": current["brand"], "mpn": current["mpn"], "gtins": current["gtins"]}
                    key = identity(deal)
                    break
        if not key:
            return None, "не хватает точного GTIN/MPN или полной версии товара для сравнения"
        keys = identity_keys(deal)
        own_urls = [deal.get("url"), *deal.get("store_candidates", [])]
        own_urls = [u for x in own_urls if (u := canonical_url(x))]
        if not own_urls:
            return None, "нет прямой ссылки для проверки текущей цены"
        candidate_shop = shop(own_urls[0])
        search = self.state["searched"].get(key)
        if (self.discoverer and self.search_remaining > 0 and time.monotonic() < self.deadline
                and (not search or self.now - search["at"] >= 1800)):
            limit = min(4, self.search_remaining)
            # Reserve the quota even if the discovery adapter fails unexpectedly.
            self.search_remaining -= limit
            try:
                result = self.discoverer(deal, limit=limit, deadline=min(self.deadline, time.monotonic() + 20))
                search = {"at": self.now, "notes": result.get("notes", [])[:4]}
                for raw in result.get("urls", [])[:12]:
                    url = canonical_url(raw)
                    if url and shop(url) != candidate_shop:
                        for alias in keys:
                            if url not in self.seeds.setdefault(alias, []):
                                self.seeds[alias].append(url)
                        self.state["products"][url] = {"key": key, "keys": sorted(keys), "seen_at": self.now}
            except Exception as ex:
                search = {"at": self.now, "notes": ["ошибка поиска: " + type(ex).__name__]}
            self.state["searched"][key] = search
            self.prune()
        # Verify plausible competing URLs seen in feeds; their headline price is never evidence.
        for url in dict.fromkeys(u for alias in keys for u in self.seeds.get(alias, [])):
            if shop(url) != candidate_shop:
                self.verify(url)
        refs = [q for q in self.state["quotes"].values()
                if keys.intersection(q.get("keys", [q["key"]])) and q["shop"] != candidate_shop
                and not (deal.get("cat") == "pixel" and pixel_key(deal["title"]) and q.get("variant")
                         and pixel_key(deal["title"]) != q["variant"])
                and 0 <= self.now - q["observed_at"] <= TTL]
        by_shop = {}
        for q in refs:
            if q["shop"] not in by_shop or q["price"] < by_shop[q["shop"]]["price"]:
                by_shop[q["shop"]] = q
        if len(by_shop) < 2:
            detail = "; поиск: " + "; ".join(search["notes"]) if search else "; поиск ожидает лимита запросов" if self.discoverer else ""
            return None, f"подтверждено других независимых магазинов: {len(by_shop)}/2" + detail
        current = self.verify(own_urls[0])
        if not current or not keys.intersection(current.get("keys", [current["key"]])):
            return None, "текущая цена, наличие или точная версия не подтверждены магазином"
        if abs(current["price"] - deal["price"]) > 0.01:
            return None, "цена магазина изменилась относительно предложения"
        if current["shipping"] is None:
            return None, "неизвестна стоимость доставки текущего предложения"
        # Strong identifiers must not contradict a title-only Pixel match.
        by_shop = {s: q for s, q in by_shop.items()
                   if not (current["gtins"] and q["gtins"] and set(current["gtins"]).isdisjoint(q["gtins"]))
                   and not (current["mpn"] and q["mpn"] and current["brand"] == q["brand"]
                            and current["mpn"] != q["mpn"])}
        if len(by_shop) < 2:
            return None, "идентификаторы версии товара противоречат сопоставлению по названию"
        total = current["price"] + current["shipping"]
        # Reference item prices are a conservative lower bound when their shipping is unknown.
        baseline = min(q["price"] for q in by_shop.values())
        saved = baseline - total
        pct = saved / baseline * 100
        if pct < min_pct or saved < min_sav:
            return None, f"выгода к минимальной цене других магазинов {pct:.0f}% / ${saved:.0f} ниже порога"
        return {"baseline": baseline, "total": total, "pct": pct, "saved": saved,
                "references": sorted(by_shop.values(), key=lambda q: q["price"]),
                "checked_at": self.now, "candidate_url": current["url"]}, ""
