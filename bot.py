#!/usr/bin/env python3
"""r/buildapcsales -> Telegram deals bot (runs from GitHub Actions)."""
import html, json, os, re, sys, time
from pathlib import Path
import requests
import feedparser

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
SEEN_F, HIST_F = DATA / "seen.json", DATA / "history.json"

UA = "deals-bot/0.1 (personal project)"
SUBREDDIT = os.environ.get("SUBREDDIT", "buildapcsales")
MIN_SCORE = int(os.environ.get("MIN_SCORE", "8"))       # голоса для сделок без подтверждения историей
MAX_POSTS = int(os.environ.get("MAX_POSTS", "8"))        # лимит публикаций за один запуск
GIVE_UP_AFTER = 6 * 3600                                 # через 6ч перестаём ждать голосов
HIST_DAYS = 90

# категория: (мин. скидка %, мин. экономия $)
RULES = {
    "gpu": (15, 40), "cpu": (20, 40), "mobo": (20, 30), "ram": (25, 20),
    "ssd": (30, 25), "hdd": (25, 25), "monitor": (25, 50), "psu": (25, 20),
    "case": (25, 20), "cooler": (25, 15), "prebuilt": (15, 100),
    "laptop": (15, 100), "peripheral": (30, 20), "combo": (0, 0),
}
DEFAULT_RULE = (30, 20)
CAT_KEYWORDS = [
    ("combo", ("combo", "bundle")), ("gpu", ("gpu", "graphics", "video card")),
    ("cpu", ("cpu", "processor")), ("mobo", ("motherboard", "mobo")),
    ("ram", ("ram", "memory")), ("ssd", ("ssd", "nvme")),
    ("hdd", ("hdd", "hard drive")), ("monitor", ("monitor",)),
    ("psu", ("psu", "power supply")), ("case", ("case",)),
    ("cooler", ("cooler", "cooling", "fan")), ("prebuilt", ("prebuilt", "desktop")),
    ("laptop", ("laptop",)),
    ("peripheral", ("keyboard", "mouse", "headset", "headphone", "controller")),
]

TITLE_HINTS = [
    ("gpu", r"\brtx\b|geforce|radeon|\brx ?\d{4}|arc b\d{3}|graphics card|video card"),
    ("cpu", r"ryzen|core (?:ultra|i[3579])|threadripper|\bcpu\b|processor"),
    ("mobo", r"motherboard"),
    ("ram", r"\bddr[345]\b|\bram\b|\bdram\b"),
    ("ssd", r"\bssd\b|nvme|\bm\.2\b|solid state"),
    ("hdd", r"hard drive|\bhdd\b|ironwolf|barracuda|exos"),
    ("monitor", r"monitor"),
    ("psu", r"power supply|\bpsu\b"),
    ("cooler", r"cpu cooler|\baio\b|liquid cooler"),
    ("case", r"pc case|computer case|mid tower|full tower"),
    ("prebuilt", r"gaming pc|gaming desktop"),
    ("laptop", r"laptop"),
    ("peripheral", r"keyboard|\bmouse\b|headset"),
]


def detect_cat_from_title(title):
    t = title.lower()
    for cat, rx in TITLE_HINTS:
        if re.search(rx, t):
            return cat
    return "other"


NUM = r"(\d[\d,]*(?:\.\d+)?)"
def num(s): return float(s.replace(",", ""))


def detect_cat(tag):
    t = tag.lower()
    for cat, kws in CAT_KEYWORDS:
        if any(k in t for k in kws):
            return cat
    return "other"


def detect_condition(text):
    t = text.lower()
    if re.search(r"refurb|renewed|recertified|reconditioned", t): return "refurbished"
    if "open box" in t or "open-box" in t: return "open box"
    if re.search(r"\bused\b|pre-owned", t): return "used"
    return "new"


def parse(post):
    """Разбор заголовка вида: [SSD] Samsung 990 Pro 2TB - $119.99 (was $199) @ Amazon"""
    title = html.unescape(post["title"]).strip()
    m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", title)
    tag, rest = (m.group(1), m.group(2)) if m else (post.get("link_flair_text") or "", title)
    cat = detect_cat(tag or post.get("link_flair_text") or "")
    if cat == "other":
        cat = detect_cat_from_title(title)
    pm = re.search(r"\$\s?" + NUM, rest)
    if not pm:
        return None
    price = num(pm.group(1))
    name = rest[:pm.start()].rstrip(" -–—|:(").strip() or rest
    name = re.sub(r"\s+(?:for|at|from|only|now|starting at)$", "", name, flags=re.I)
    tail = rest[pm.end():]

    was = None
    wm = re.search(r"(?:was|reg(?:ular)?\.?|msrp|list|orig(?:inal)?|retail)\s*:?\s*\$\s?" + NUM, tail, re.I)
    if wm:
        was = num(wm.group(1))
    else:
        om = re.search(r"\$\s?" + NUM + r"\s*off", tail, re.I)
        pc = re.search(r"(\d{1,2})%\s*off|-\s?(\d{1,2})%", tail, re.I)
        if om:
            was = price + num(om.group(1))
        elif pc:
            p = int(pc.group(1) or pc.group(2))
            if 0 < p < 95:
                was = price / (1 - p / 100)

    sm = re.search(r"(?:@|\bat\b|\bfrom\b|\bvia\b)\s+([A-Z][\w .&'-]{1,30}?)(?:\s*(?:\+|\(|\[|,|$))", tail)
    store = sm.group(1).strip() if sm else ""
    key = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    key = re.sub(r"\s+", " ", key).strip()
    return {
        "id": post["id"], "title": title, "tag": tag, "cat": cat, "name": name,
        "key": key, "price": price, "was": was, "store": store,
        "cond": detect_condition(title), "url": post.get("url") or "",
        "discuss": post.get("discuss_url") or "https://www.reddit.com" + post.get("permalink", ""),
        "score": int(post.get("score") or 0), "created": float(post.get("created_utc") or time.time()),
        "flair": (post.get("link_flair_text") or "").lower(),
    }


def evaluate(d, samples):
    """samples: [[ts, price, post_id], ...] история этого товара. Возвращает (ok, info)."""
    prices = [p for _, p, pid in samples if pid != d["id"]]
    confirmed = len(prices) >= 2
    price, claimed = d["price"], d["was"]
    ref, kind = None, None

    if claimed and claimed > price:
        ref, kind = claimed, "claimed"
        if confirmed:
            cap = max(prices) * 1.15
            if claimed > cap:                      # «раздутая» старая цена
                ref, kind = cap, "capped"
    elif confirmed:
        ref, kind = max(prices), "history"

    new_low = confirmed and price < min(prices) * 0.98
    info = {"ref": ref, "kind": kind, "pct": 0, "sav": 0, "new_low": new_low, "inflated": kind == "capped"}

    if d["cat"] == "combo":
        return d["score"] >= MIN_SCORE * 2, info
    if ref is None or ref <= price:
        return (d["score"] >= MIN_SCORE * 3) or new_low, info

    info["pct"] = (ref - price) / ref * 100
    info["sav"] = ref - price
    min_pct, min_sav = RULES.get(d["cat"], DEFAULT_RULE)
    passes = info["pct"] >= min_pct and info["sav"] >= min_sav
    return passes and (confirmed or d["score"] >= MIN_SCORE), info


def fmt_price(x): return f"${x:,.2f}".replace(".00", "")


def build_message(d, info):
    e = html.escape
    lines = [f"📦 <b>[{e(d['tag'] or d['cat'].upper())}] {e(d['name'])}</b>", ""]
    if info["ref"] and info["ref"] > d["price"]:
        lines.append(f"💰 Цена: <b>{fmt_price(d['price'])}</b> (было ~{fmt_price(info['ref'])}, −{info['pct']:.0f}%)")
    else:
        lines.append(f"💰 Цена: <b>{fmt_price(d['price'])}</b>")
    lines.append(f"⚠️ Условие: {d['cond']}")
    if d["store"]:
        lines.append(f"🏪 Магазин: {e(d['store'])}")
    if info["new_low"]:
        lines.append("🔥 Новый минимум по нашей истории цен")
    if info["inflated"]:
        lines.append("ℹ️ Заявленная «старая» цена завышена, скидка пересчитана")
    if d["score"]:
        lines.append(f"👍 Рейтинг на Reddit: {d['score']}")
    lines += ["", f"👉 <a href=\"{html.escape(d['url'], quote=True)}\">СМОТРЕТЬ В МАГАЗИНЕ</a>",
              f"💬 <a href=\"{html.escape(d['discuss'], quote=True)}\">Обсуждение</a>"]
    link = os.environ.get("CHANNEL_LINK", "")
    if link:
        lines += ["", "———————————————", f"📢 Ссылка для друзей: {html.escape(link)}"]
    return "\n".join(lines)


def tg_send(text):
    token, chat = os.environ["TG_BOT_TOKEN"], os.environ["TG_CHAT_ID"]
    for _ in range(3):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": False}, timeout=20)
        if r.status_code == 429:
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
            continue
        r.raise_for_status()
        return
    raise RuntimeError("Telegram: слишком много попыток")


BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# (имя, url, "оценка сообщества", которую присваиваем сделкам из этой ленты)
FEEDS = [
    ("slickdeals-frontpage", "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1", MIN_SCORE * 4),
    ("slickdeals-popular", "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1", MIN_SCORE * 4),
    ("slickdeals-hot", "https://slickdeals.net/newsearch.php?searchin=first&forumchoice%5B%5D=9&rss=1", MIN_SCORE),
]


def fetch_feed(name, url, score):
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20)
    r.raise_for_status()
    out = []
    for e in feedparser.parse(r.content).entries:
        ts = time.mktime(e.published_parsed) if getattr(e, "published_parsed", None) else time.time()
        link = e.get("link", "")
        out.append({"id": e.get("id") or link, "title": e.get("title", ""), "url": link,
                    "discuss_url": link, "score": score, "created_utc": ts,
                    "link_flair_text": ""})
    return out


def fetch_all():
    posts, ok = [], 0
    for name, url, score in FEEDS:
        try:
            got = fetch_feed(name, url, score)
            ok += 1
            print(f"[{name}] получено записей: {len(got)}")
            posts += got
        except Exception as ex:
            print(f"[{name}] ОШИБКА: {ex}")
    if os.environ.get("REDDIT_CLIENT_ID"):
        try:
            got = fetch_reddit()
            ok += 1
            print(f"[reddit] получено записей: {len(got)}")
            posts += got
        except Exception as ex:
            print(f"[reddit] ОШИБКА: {ex}")
    if ok == 0:
        raise SystemExit("Все источники недоступны")
    return posts


def fetch_reddit():
    cid, sec = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    headers = {"User-Agent": UA}
    if cid and sec:
        r = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, sec),
                          data={"grant_type": "client_credentials"}, headers=headers, timeout=20)
        r.raise_for_status()
        headers["Authorization"] = "bearer " + r.json()["access_token"]
        url = f"https://oauth.reddit.com/r/{SUBREDDIT}/new"
    else:
        url = f"https://www.reddit.com/r/{SUBREDDIT}/new.json"
    r = requests.get(url, params={"limit": 50, "raw_json": 1}, headers=headers, timeout=20)
    r.raise_for_status()
    return [c["data"] for c in r.json()["data"]["children"]]


def load(p, default):
    try: return json.loads(p.read_text())
    except Exception: return default


def main():
    dry = os.environ.get("DRY_RUN") == "1"
    now = time.time()
    seen, hist = load(SEEN_F, {}), load(HIST_F, {})
    posted = 0

    for post in sorted(fetch_all(), key=lambda p: p["created_utc"]):
        pid = post["id"]
        if pid in seen:
            continue
        d = parse(post)
        if d is None or d["cat"] == "other" or "expired" in d["flair"]:
            seen[pid] = now
            continue
        samples = hist.setdefault(d["key"], [])
        ok, info = evaluate(d, samples)
        if not any(s[2] == pid for s in samples):
            samples.append([now, d["price"], pid])

        if ok and posted < MAX_POSTS:
            msg = build_message(d, info)
            print(msg, "\n" + "=" * 40) if dry else tg_send(msg)
            seen[pid] = now
            posted += 1
        elif not ok and now - d["created"] > GIVE_UP_AFTER:
            seen[pid] = now

    cutoff = now - HIST_DAYS * 86400
    hist = {k: [s for s in v if s[0] > cutoff] for k, v in hist.items()}
    hist = {k: v for k, v in hist.items() if v}
    seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-3000:])
    if not dry:
        SEEN_F.write_text(json.dumps(seen))
        HIST_F.write_text(json.dumps(hist))
    print(f"Опубликовано: {posted}")


if __name__ == "__main__":
    sys.exit(main())
