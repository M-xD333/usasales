"""Реестр источников и сетевая часть (RSS / Reddit). Здесь нет логики решений о публикации."""
import calendar
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import feedparser
import requests
import store_links
import direct_stores
import product_images
import market_prices

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REDDIT_UA = "deals-bot/0.2 (personal project)"
TIMEOUT = (5, 15)           # connect, read
SD = "https://slickdeals.net/newsearch.php"
SEARCH_TERMS = ["ssd", "nvme", "rtx", "radeon", "ryzen", "ddr5",
                "graphics card", "motherboard", "monitor", "google pixel",
                "gaming mouse", "mechanical keyboard", "headset", "microphone",
                "drum machine", "sampler", "synthesizer", "Roland AIRA"]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    type: str = "rss"              # rss | reddit | store
    trust: str = "unvetted"        # curated | editorial | unvetted | community
    enabled: bool = True           # включён по умолчанию; остальные — через ENABLE_SOURCES
    publish: bool = True           # False = только наблюдение за ценами
    allow_undated: bool = False
    note: str = ""


def _sd_search(term):
    return Source(
        id="sd-q-" + term.lower().replace(" ", "-"), name=f"Slickdeals: {term}",
        url=SD + "?rss=1&q=" + quote_plus(term) + "&searcharea=deals&searchin=first&isUserSearch=1",
        note="поисковая лента Slickdeals (формат взят с форума Slickdeals, проверять --smoke)")


SOURCES = [
    Source("sd-frontpage", "Slickdeals Frontpage", SD + "?mode=frontpage&searcharea=deals&searchin=first&rss=1", trust="curated"),
    Source("sd-popular", "Slickdeals Popular", SD + "?mode=popdeals&searcharea=deals&searchin=first&rss=1", trust="curated"),
    Source("sd-hot", "Slickdeals Hot Deals", SD + "?searchin=first&forumchoice%5B%5D=9&rss=1"),
    *[_sd_search(t) for t in SEARCH_TERMS],
    # --- отключены до успешного smoke-теста (python bot.py --smoke) ---
    Source("techbargains", "TechBargains", "http://www.techbargains.com/rss.xml", enabled=False,
           note="smoke 2026-11-03: HTTP 200, 600 записей, в основном быт; без старой цены в заголовке почти ничего не публикует"),
    Source("bensbargains", "Ben's Bargains", "http://bensbargains.net/rss/", enabled=False,
           note="smoke 2026-11-03: HTTP 403 (блокирует GitHub); не включать"),
    Source("9to5toys-steals", "9to5Toys Steals", "https://9to5toys.com/steals/feed", trust="editorial", enabled=False),
    Source("pcworld", "PCWorld", "http://www.pcworld.com/index.rss", trust="editorial", enabled=False),
    Source("pcworld-deals", "PCWorld Deals", "https://www.pcworld.com/deals/feed", trust="editorial", enabled=False,
           note="smoke 2026-11-03: HTTP 200, но содержимое совпало с pcworld (дубль), статьи; не включать"),
    Source("tomshardware", "Tom's Hardware", "https://www.tomshardware.com/feeds.xml", trust="editorial",
           enabled=False, publish=False, note="вспомогательный сигнал, не публикуется"),
    Source("dealnews-computers", "DealNews Computers", "https://www.dealnews.com/c39/Computers/?rss=1",
           trust="curated", enabled=False,
           note="условия DealNews: не менять содержимое/ссылки, обязательная атрибуция. Включать только осознанно"),
    Source("reddit-buildapcsales", "Reddit r/buildapcsales", "reddit://buildapcsales", type="reddit", trust="community",
           note="нужны REDDIT_CLIENT_ID и REDDIT_CLIENT_SECRET (одобренный доступ)"),
    # Direct catalogs are enabled independently of the optional aggregator list.
    *[Source(sid, name, url, type="store", trust="merchant", allow_undated=True)
      for sid, name, url in [
          ("amazon-components", "Amazon Components", "https://www.amazon.com/s?k=computer+components"),
          ("amazon-peripherals", "Amazon Peripherals", "https://www.amazon.com/s?k=gaming+keyboard+mouse+headset+microphone"),
          ("amazon-pixel", "Amazon Google Pixel", "https://www.amazon.com/s?k=google+pixel+unlocked+phone"),
          ("newegg-direct", "Newegg Direct", "https://www.newegg.com/todays-deals"),
          ("corsair-memory", "Corsair Memory", "https://www.corsair.com/us/en/c/memory"),
          ("corsair-psu", "Corsair Power Supplies", "https://www.corsair.com/us/en/c/psu"),
          ("corsair-cooling", "Corsair Cooling", "https://www.corsair.com/us/en/c/cpu-coolers"),
          ("corsair-keyboards", "Corsair Keyboards", "https://www.corsair.com/us/en/c/keyboards"),
          ("corsair-mice", "Corsair Mice", "https://www.corsair.com/us/en/c/gaming-mouse"),
          ("corsair-headsets", "Corsair Headsets", "https://www.corsair.com/us/en/c/gaming-headsets"),
          ("msi-direct", "MSI Components", "https://us-store.msi.com/Components"),
          ("samsung-storage", "Samsung Storage", "https://www.samsung.com/us/computing/memory-storage/all-memory-storage/"),
          ("crucial-direct", "Crucial Memory & SSD", "https://www.crucial.com/promotions"),
          ("asus-gpu", "ASUS Graphics Cards", "https://eshop.asus.com/us/products/graphics-cards.html"),
          ("google-pixel-direct", "Google Store Pixel", "https://store.google.com/us/collection/offers?hl=en-US"),
      ]],
]


# ---------- конфигурация источников ----------
def _ids(value):
    return {x.strip() for x in (value or "").split(",") if x.strip()}


def reddit_credentials(env=None):
    env = os.environ if env is None else env
    cid, sec = env.get("REDDIT_CLIENT_ID", "").strip(), env.get("REDDIT_CLIENT_SECRET", "").strip()
    if env.get("ENABLE_REDDIT", "").lower() in ("0", "false", "no"):
        return None
    if cid and sec:
        return cid, sec
    if cid or sec:
        raise ConfigError("Reddit: задан только один из REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET — нужны оба")
    return None


def active_sources(env=None, log=print):
    env = os.environ if env is None else env
    enable, disable = _ids(env.get("ENABLE_SOURCES")), _ids(env.get("DISABLE_SOURCES"))
    out = []
    for s in SOURCES:
        if s.id in disable or not (s.enabled or s.id in enable):
            continue
        if s.type == "reddit":
            try:
                if reddit_credentials(env) is None:
                    continue
            except ConfigError as ex:
                log(f"[WARN] {ex}. Reddit отключён.")
                continue
        out.append(s)
    return out


# ---------- нормализация записей ----------
EXPIRED_RX = re.compile(r"\b(expired|sold out|out of stock|deal dead|no longer available)\b", re.I)


def entry_timestamp(e):
    """Unix UTC из published_parsed / updated_parsed (feedparser отдаёт UTC-кортежи)."""
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return calendar.timegm(t)
    return None


def normalize_entry(e):
    link = e.get("link", "") or ""
    title = (e.get("title") or "").strip()
    markup = (e.get("summary", "") or "") + "\n" + "\n".join(
        c.get("value", "") for c in e.get("content", []))
    candidates = store_links.extract_links(markup, link)
    summary = re.sub(r"<[^>]+>", " ", e.get("summary", "") or "")
    summary = re.sub(r"\s+", " ", summary).strip()[:600]
    return {"item_id": e.get("id") or link or title, "url": link, "discuss_url": link,
            "store_candidates": candidates,
            "image_url": product_images.from_feed(e, markup, link),
            "title": title, "summary": summary, "published_ts": entry_timestamp(e),
            "expired": bool(EXPIRED_RX.search(title)), "community_score": None, "flair": ""}


@dataclass
class FetchResult:
    source: Source
    status: int | None = None
    entries: list = field(default_factory=list)
    not_modified: bool = False
    error: str = ""
    seconds: float = 0.0
    content_type: str = ""
    etag: str = ""
    last_modified: str = ""
    bozo: bool = False


def _get(url, headers, session, attempts=2, **kw):
    last = None
    for i in range(attempts):
        try:
            r = session.get(url, headers=headers, timeout=TIMEOUT, **kw)
            if r.status_code >= 500 and i < attempts - 1:
                time.sleep(1.5 * (i + 1))
                continue
            return r
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ex:
            last = ex
            if i < attempts - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def fetch_rss(source, cond=None, session=requests):
    res, t0 = FetchResult(source), time.time()
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    cond = cond or {}
    if cond.get("etag"):
        headers["If-None-Match"] = cond["etag"]
    if cond.get("last_modified"):
        headers["If-Modified-Since"] = cond["last_modified"]
    try:
        r = _get(source.url, headers, session)
        res.status = r.status_code
        res.content_type = r.headers.get("Content-Type", "")
        res.etag = r.headers.get("ETag", "")
        res.last_modified = r.headers.get("Last-Modified", "")
        if r.status_code == 304:
            res.not_modified = True
        elif r.status_code != 200:
            res.error = f"HTTP {r.status_code}"
        else:
            feed = feedparser.parse(r.content)
            res.bozo = bool(feed.bozo)
            res.entries = [normalize_entry(e) for e in feed.entries]
            if not res.entries and feed.bozo:
                res.error = "ответ не похож на RSS/XML"
    except Exception as ex:  # сеть: не роняем остальные источники
        res.error = f"{type(ex).__name__}: {ex}"
    res.seconds = time.time() - t0
    return res


def fetch_reddit(source, env=None, session=requests):
    res, t0 = FetchResult(source), time.time()
    try:
        creds = reddit_credentials(env)
        if creds is None:
            res.error = "Reddit не настроен"
            return res
        sub = source.url.rsplit("/", 1)[-1]
        tok = session.post("https://www.reddit.com/api/v1/access_token", auth=creds,
                           data={"grant_type": "client_credentials"},
                           headers={"User-Agent": REDDIT_UA}, timeout=TIMEOUT)
        if tok.status_code != 200:
            res.status, res.error = tok.status_code, f"OAuth HTTP {tok.status_code}"
            return res
        token = tok.json().get("access_token")
        r = session.get(f"https://oauth.reddit.com/r/{sub}/new", params={"limit": 50, "raw_json": 1},
                        headers={"User-Agent": REDDIT_UA, "Authorization": "bearer " + str(token)}, timeout=TIMEOUT)
        res.status = r.status_code
        if r.status_code == 429:
            res.error = "HTTP 429 (лимит Reddit)"
        elif r.status_code in (401, 403):
            res.error = f"HTTP {r.status_code} (нет доступа к API)"
        elif r.status_code != 200:
            res.error = f"HTTP {r.status_code}"
        else:
            for c in r.json()["data"]["children"]:
                p = c["data"]
                res.entries.append({
                    "item_id": "reddit:" + p["id"], "url": p.get("url") or "",
                    "image_url": product_images.image_url(p.get("thumbnail")),
                    "discuss_url": "https://www.reddit.com" + p.get("permalink", ""),
                    "title": p.get("title", ""), "summary": "", "published_ts": p.get("created_utc"),
                    "expired": "expired" in (p.get("link_flair_text") or "").lower(),
                    "community_score": int(p.get("score") or 0), "flair": p.get("link_flair_text") or ""})
    except ConfigError as ex:
        res.error = str(ex)
    except Exception as ex:
        res.error = f"{type(ex).__name__}: {ex}"
    res.seconds = time.time() - t0
    return res


def fetch_store(source, cond=None, session=requests):
    res, t0 = FetchResult(source), time.monotonic()
    state = cond or {}
    interval = 1800 if state.get("errors", 0) else 900
    if time.time() - state.get("last_attempt", 0) < interval:
        res.status, res.not_modified = 304, True
        return res
    try:
        headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
        # US catalog only; never bypass CAPTCHA/access challenges.
        r = session.get(source.url, headers=headers, timeout=(4, 10))
        res.status, res.content_type = r.status_code, r.headers.get("Content-Type", "")
        if r.status_code != 200:
            res.error = f"прямой магазин: HTTP {r.status_code}"
            return res
        if re.search(r"<title[^>]*>[^<]*(?:captcha|robot check|just a moment)|verify you are human|/errors/validateCaptcha",
                     r.text[:15000], re.I):
            res.error = "магазин требует проверку браузера; данные не получены"
            return res
        res.entries = direct_stores.parse_catalog(r.text, r.url)
        if not res.entries:
            links = direct_stores.product_links(r.text, r.url)
            for url in links:
                if time.monotonic() - t0 > 20:
                    break
                p = session.get(url, headers=headers, timeout=(3, 5))
                if p.status_code == 200:
                    res.entries.extend(direct_stores.parse_catalog(p.text, p.url))
            if not res.entries:
                res.error = "нет однозначных USD-цен товаров: JS-каталог, блокировка или изменённая разметка"
    except (requests.RequestException, ValueError, TypeError, KeyError, RecursionError) as ex:
        res.error = f"{type(ex).__name__}: {ex}"
    finally:
        res.seconds = time.monotonic() - t0
    return res


def fetch_market_product(url, session=requests):
    """Read an exact merchant product page; never trust a feed's advertised price."""
    url = market_prices.canonical_url(url)
    if not url:
        return None
    try:
        with session.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US"},
                         timeout=(3, 5), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                return None
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 2_000_000:
                    return None
                chunks.append(chunk)
            body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        for entry in direct_stores.parse_catalog(body, url):
            if market_prices.canonical_url(entry["url"]) == url:
                return entry
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def fetch_one(source, cond=None):
    if source.type == "store":
        return fetch_store(source, cond)
    return fetch_reddit(source) if source.type == "reddit" else fetch_rss(source, cond)


def fetch_all(sources, state_sources=None, workers=3, deadline=120):
    """Параллельная загрузка с ограничением числа потоков и общим дедлайном."""
    state_sources = state_sources or {}
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, s, state_sources.get(s.id)): s for s in sources}
        try:
            for f in as_completed(futs, timeout=deadline):
                results.append(f.result())
        except Exception as e:  # TimeoutError по общему дедлайну
            done = {r.source.id for r in results}
            for s in sources:
                if s.id not in done:
                    results.append(FetchResult(s, error=f"не успел за {deadline} с ({type(e).__name__})"))
    order = {s.id: i for i, s in enumerate(sources)}
    return sorted(results, key=lambda r: order[r.source.id])
