#!/usr/bin/env python3
"""US deals -> Telegram (текстовые публикации, запуск из GitHub Actions)."""
import html
import json
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

import sources as S
import store_links
import product_images
import market_prices

DATA = Path(__file__).parent / "data"
STATE_F, HIST_F, LEGACY_SEEN_F = "state.json", "history.json", "seen.json"

# ============================== конфигурация ==============================
# категория: (мин. скидка %, мин. экономия $). Переопределяется env RULES_JSON='{"ssd":[30,25]}'
RULES = {
    "gpu": (15, 40), "cpu": (20, 40), "mobo": (20, 30), "ram": (25, 20), "ssd": (30, 25),
    "hdd": (25, 25), "ext_storage": (25, 20), "nas": (15, 40), "monitor": (25, 50),
    "psu": (25, 20), "case": (25, 20), "cooler": (25, 15), "prebuilt": (15, 100),
    "laptop": (15, 100), "minipc": (15, 50), "keyboard": (30, 15), "mouse": (30, 15),
    "headset": (30, 15), "microphone": (25, 20), "pixel": (15, 50),
    "music_gear": (20, 30),
    "controller": (30, 15), "webcam": (30, 15), "networking": (25, 20),
    "dock": (25, 15), "capture": (25, 25), "printer": (25, 30), "combo": (0, 0),
}
DEFAULT_RULE = (30, 20)
INFLATE_FACTOR = 3.0    # заявленная «старая» цена > 3× медианы наших наблюдений = подозрительна
MAX_SANE_DISCOUNT = 85  # скидка больше — скорее ошибка разбора


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def is_dry():
    return os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def get_cfg():
    rules = dict(RULES)
    try:
        for k, v in json.loads(os.environ.get("RULES_JSON", "{}") or "{}").items():
            rules[k] = (float(v[0]), float(v[1]))
    except Exception:
        print("[WARN] RULES_JSON не разобран, используются правила по умолчанию")
    return {
        "max_posts": env_int("MAX_POSTS_PER_RUN", env_int("MAX_POSTS", 8)),
        "min_score": env_int("MIN_SCORE", 8),                 # ТОЛЬКО реальные голоса сообщества
        "max_age_h": env_int("MAX_DEAL_AGE_HOURS", 12),
        "hist_days": env_int("HIST_DAYS", 90),
        "dup_hours": env_int("DUP_WINDOW_HOURS", 24),
        "rules": rules,
    }


# ============================== разбор заголовков ==============================
AMT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
CURRENCY_OTHER = re.compile(r"£|€|¥|₹|\bCAD\b|C\$|CA\$|\bAUD\b|A\$|\bGBP\b|\bEUR\b", re.I)
ORIG_MARK = re.compile(r"(was|reg(?:ular)?\.?|msrp|list(?: price)?|orig(?:inal)?\.?|retail|normally|usually)\s*:?\s*$", re.I)
QUALIFIER = re.compile(r"(from|starting at|starts at|as low as|up to)\s*$", re.I)
COUPON_RX = re.compile(r"(?:after|w/|with|using)\s+(?:a\s+)?(?:clipped\s+)?coupon|clip(?:ping)?\s+(?:the\s+)?coupon|\bcoupon\b", re.I)
PROMO_RX = re.compile(r"promo(?:tion)?\s+code|(?:w/|with|using|after)\s+code|\bcode[:\s]+[A-Z0-9]{4,20}\b", re.I)
CODE_VAL = re.compile(r"\b[Cc]ode[:\s]+([A-Z0-9]{4,20})\b")
PCT_OFF = re.compile(r"(\d{1,2})\s*%\s*off|-\s?(\d{1,2})\s*%", re.I)
MEMBERSHIP_RX = re.compile(r"prime members?|members? only|membership|w/ prime|with prime|plus members?|rewards members?|student", re.I)
CASHBACK_RX = re.compile(r"cash ?back|gift card|rebate|mail-in|store credit|kohl'?s cash|amazon credit", re.I)
FREESHIP_RX = re.compile(r"free shipping|free s/h", re.I)


def parse_prices(text):
    """Разбор цен из заголовка. Если цену нельзя определить уверенно — price=None и issue."""
    r = {"price": None, "original": None, "original_kind": None, "price_kind": "regular",
         "code": None, "flags": set(), "issue": None}
    if CURRENCY_OTHER.search(text):
        r["issue"] = "non_usd"
        return r
    amts = [(float(m.group(1).replace(",", "")), m.start(), m.end()) for m in AMT.finditer(text)]
    if not amts:
        r["issue"] = "no_price"
        return r
    if re.search(AMT.pattern + r"\s*(?:-|–|—|to)\s*" + AMT.pattern + r"(?!\d)(?!\s*off)", text, re.I):
        r["issue"] = "price_range"
        return r

    cands, origs, offs = [], [], []
    for v, s, e in amts:
        before, after = text[max(0, s - 16):s], text[e:e + 8]
        m = ORIG_MARK.search(before)
        if m:
            kind = "MSRP" if m.group(1).lower().startswith(("msrp", "list")) else "was"
            origs.append((v, kind))
        elif re.match(r"^\s*off\b", after, re.I):
            offs.append(v)
        else:
            cands.append((v, s))
    if not cands:
        r["issue"] = "no_price"
        return r
    if QUALIFIER.search(text[max(0, cands[0][1] - 16):cands[0][1]]):
        r["issue"] = "price_from"
        return r

    has_coupon, has_code = bool(COUPON_RX.search(text)), bool(PROMO_RX.search(text))
    if len(cands) > 1 and not (has_coupon or has_code):
        r["issue"] = "multiple_prices"
        return r
    price = min(c[0] for c in cands)
    r["price"] = price
    if has_coupon:
        r["price_kind"] = "coupon"
    if has_code:
        r["price_kind"] = "code"
        m = CODE_VAL.search(text)
        r["code"] = m.group(1) if m else None

    if origs and origs[0][0] > price:
        r["original"], r["original_kind"] = origs[0]
    elif offs:
        r["original"], r["original_kind"] = price + offs[0], "derived"
    else:
        pm = PCT_OFF.search(text)
        if pm:
            p = int(pm.group(1) or pm.group(2))
            if 0 < p < 95:
                r["original"], r["original_kind"] = price / (1 - p / 100), "derived"
    if MEMBERSHIP_RX.search(text):
        r["flags"].add("membership")
    if CASHBACK_RX.search(text):
        r["flags"].add("cashback")
    if FREESHIP_RX.search(text):
        r["flags"].add("free_shipping")
    r["first_price_pos"] = cands[0][1]
    return r


def split_tag(title):
    m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", title)
    return (m.group(1).strip(), m.group(2)) if m else ("", title)


def clean_name(text, pos=None):
    name = text[:pos] if pos else text
    name = name.rstrip(" -–—|:(+,")
    name = re.sub(r"\s+(?:for|at|from|only|now|starting at|just)$", "", name, flags=re.I)
    return name.strip() or text.strip()


def extract_name(text, price_pos):
    t = re.sub(r"^(?:\s*\[[^\]]*\])+\s*", "", text)           # ведущие [Магазин] / [Пометки]
    m = re.match(r"^\s*\$\s?[\d,]+(?:\.\d+)?\*?\s*[|\-–—]\s*", t)   # «$1699 | Название ...»
    if m:
        name = t[m.end():]
        name = re.sub(r"\s+(?:at|from|via)\s+[A-Z][\w.&' !-]{1,30}$", "", name).strip()
        return name or t.strip()
    pos = None
    if price_pos is not None:
        pos = price_pos - (len(text) - len(t))
    return clean_name(t, pos if pos and pos > 0 else None)


# ============================== категории / состояние / магазины ==============================
TAG_MAP = {"gpu": "gpu", "cpu": "cpu", "motherboard": "mobo", "mobo": "mobo", "ram": "ram", "ssd": "ssd",
           "hdd": "hdd", "monitor": "monitor", "psu": "psu", "case": "case", "cooler": "cooler",
           "prebuilt": "prebuilt", "laptop": "laptop", "combo": "combo", "nas": "nas", "mouse": "mouse",
           "keyboard": "keyboard", "headset": "headset", "controller": "controller", "webcam": "webcam",
           "networking": "networking", "router": "networking", "fans": "cooler"}
# порядок важен: специфичное раньше общего (ноутбук с RTX — это laptop, а не gpu)
CLASSIFY = [
    # Musical keyboards must be recognized before computer peripherals.
    ("music_gear", r"^(?!.*\b(?:sample packs?|sound packs?|plugins?|vst|software|carrying case|dust cover|power adapter|coffee|tea|perfume|cologne|whiskey|bourbon|wine|cigars?|chocolate|skincare|fragrance)\b).*?\b(?:drum[ -]?machines?|samplers?|synthesizers?|synthesisers?|roland\s+aira)\b"),
    # Only Pixel phones; exclude their cases, watches, earbuds, chargers and accessories.
    ("pixel", r"^(?!.*\b(?:case|cover|protector|charger|cable|watch|buds|screen replacement)\b).*?\b(?:google\s+)?pixel\s+\d+(?:a|\s+pro)?\b"),
    ("laptop", r"\blaptop|notebook|chromebook|macbook|ultrabook"),
    ("minipc", r"mini[- ]pc|\bnuc\b|mini desktop"),
    ("prebuilt", r"gaming pc|gaming desktop|prebuilt|all-in-one pc|desktop pc|desktop computer"),
    ("mobo", r"motherboard"),
    ("nas", r"\bnas\b|network attached"),
    ("ext_storage", r"external (?:ssd|hard drive|hdd|drive|storage)|portable ssd|flash drive|usb drive|micro ?sd|sd card|memory card"),
    ("networking", r"router|wi-?fi|mesh|network switch|ethernet switch|\bnic\b|network card|access point|\bmodem\b|powerline"),
    ("capture", r"capture card"),
    ("dock", r"docking station|\bdock\b|usb[- ]?c? hub|\busb hub|thunderbolt dock|\badapter\b"),
    ("webcam", r"webcam"),
    ("cooler", r"cpu cooler|liquid cpu cooler|liquid cooler|\baio\b|case fans?|cooling fan|\bradiator\b|hydro x"),
    ("gpu", r"\brtx\b|geforce|radeon|\brx ?\d{4}|arc [ab]\d{3}|graphics card|video card|\bgpu\b"),
    ("cpu", r"ryzen|core (?:ultra|i[3579])|threadripper|\bcpu\b|(?<!food )processor"),
    ("mobo", r"motherboard"),
    ("ram", r"\bddr[345]\b|\bram\b|\bdram\b|memory kit"),
    ("ssd", r"\bssd\b|nvme|\bm\.2\b|solid state"),
    ("hdd", r"hard drive|\bhdd\b|ironwolf|barracuda|\bexos\b"),
    ("monitor", r"monitor"),
    ("psu", r"power supply|\bpsu\b"),
    ("cooler", r"cpu cooler|\baio\b|liquid cooler|case fans?"),
    ("case", r"pc case|computer case|mid tower|full tower"),
    ("keyboard", r"keyboard"), ("mouse", r"\bmouse\b|\bmice\b"),
    ("microphone", r"microphone|\bmic\b|wave[: ]?[13]|quadcast|seiren"),
    ("headset", r"headset|headphones|earbuds|airpods"), ("controller", r"gamepad|controller"),
    ("printer", r"printer"),
]
CLASSIFY_RX = [(c, re.compile(rx, re.I)) for c, rx in CLASSIFY]
_SPEC_CPU = re.compile(r"ryzen|core (?:ultra |i[3579])|intel ultra|snapdragon|celeron|pentium|\bapple m\d", re.I)
_SPEC_RAM = re.compile(r"\d+ ?gb (?:ddr\d|lpddr\d|ram)|\bram\b", re.I)
_SPEC_STO = re.compile(r"\d+ ?(?:gb|tb) (?:pcie|nvme|ssd)|\bssd\b", re.I)
_SPEC_SCR = re.compile(r"\b\d{2}(?:\.\d)?\s?[\"″”]|\bfhd\b|touch|\bips\b|\boled\b", re.I)


def looks_like_system(text):
    """CPU+RAM+накопитель+экран в одном заголовке = ноутбук/готовый ПК, а не отдельная деталь."""
    hits = sum(bool(rx.search(text)) for rx in (_SPEC_CPU, _SPEC_RAM, _SPEC_STO, _SPEC_SCR))
    return hits >= 3
REVIEW_HINT = re.compile(r"\btv\b|television|camera|tablet|ipad|phone|earbuds|airpods|speaker|soundbar|smartwatch|drone|kindle|usb|charger|power bank", re.I)

KNOWN_STORES = ["Amazon", "Newegg", "Best Buy", "Walmart", "Micro Center", "B&H", "Woot", "eBay", "Costco",
                "Target", "Adorama", "Antonline", "Staples", "Office Depot"]
STORE_RX = [(s, re.compile(r"(?:@|\bat\b|\bfrom\b|\bvia\b|\[)\s*" + re.escape(s) + r"(?![A-Za-z])", re.I))
            for s in KNOWN_STORES]
STORE_FALLBACK = re.compile(r"(?:@|\bat\b|\bfrom\b|\bvia\b)\s+([A-Z][\w.&' -]{1,30}?)(?=\s*(?:\+|\(|\[|,|$|w/|with|after|\bfor\b))")
ASIN_RX = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})\b")
STOPWORDS = {"new", "free", "shipping", "w", "with", "after", "coupon", "code", "prime", "member", "members",
             "at", "for", "from", "the", "and", "a", "an", "of", "deal", "sale"}


def classify(text, tag=""):
    t = (tag or "").strip().lower()
    if t in TAG_MAP:
        return TAG_MAP[t]
    for cat, rx in CLASSIFY_RX:
        if rx.search(text):
            return cat
        if cat == "prebuilt" and looks_like_system(text):
            return "laptop"
    return "review" if REVIEW_HINT.search(text) else "other"


def detect_condition(text):
    t = text.lower()
    if re.search(r"refurb|renewed|recertified|reconditioned", t):
        return "Refurbished"
    if "open box" in t or "open-box" in t:
        return "Open Box"
    if re.search(r"\bused\b|pre-owned", t):
        return "Used"
    return "New"


def detect_store(text):
    for name, rx in STORE_RX:
        if rx.search(text):
            return name
    m = STORE_FALLBACK.search(text)
    return m.group(1).strip() if m else ""


def name_key(name):
    s = re.sub(r"[^a-z0-9. ]", " ", name.lower())
    return " ".join(t for t in s.split() if t not in STOPWORDS)


def make_deal(entry, source, now):
    """entry (нормализованная запись) -> словарь сделки (JSON-совместимый)."""
    title = html.unescape(entry["title"]).strip()
    tag, rest = split_tag(title)
    if tag.lower() not in TAG_MAP:       # «[Micro Center]», «[Open-Box YMMV]» и т.п. — не категория
        tag, rest = "", title
    pi = parse_prices(rest)
    if source.type == "store":
        # Structured catalog prices, independent of punctuation/numbers in product names.
        price = direct_price = entry.get("store_price")
        if (entry.get("store_currency") == "USD" and isinstance(price, (int, float))
                and 0 < price < 100000):
            pi.update(price=direct_price, issue=None, original=entry.get("store_original"),
                      original_kind="MSRP" if entry.get("store_original") else None)
        else:
            pi.update(price=None, issue="invalid_store_price", original=None)
    cat = classify(rest, tag or entry.get("flair", ""))
    name = extract_name(rest, pi.get("first_price_pos"))
    asin_m = ASIN_RX.search(entry["url"] or "") or ASIN_RX.search(entry.get("summary", ""))
    asin = asin_m.group(1) if asin_m else None
    cond = detect_condition(title)
    if cat == "pixel" and re.search(
            r"trade[ -]?in|bill credits?|/mo\b|per month|activation|new line|port[ -]?in|with .{0,30}plan",
            title + " " + entry.get("summary", ""), re.I):
        pi["flags"].add("conditional_phone_price")
    ts = entry.get("published_ts")
    undated = ts is None
    if undated and source.allow_undated:
        ts = now
    return {
        "id": entry["item_id"], "source_id": source.id, "source": source.name, "trust": source.trust,
        "publish": source.publish, "title": title, "tag": tag, "name": name, "cat": cat,
        "price": pi["price"], "issue": pi["issue"], "original": pi["original"],
        "original_kind": pi["original_kind"], "price_kind": pi["price_kind"], "code": pi["code"],
        "flags": sorted(pi["flags"]), "cond": cond,
        "store": source.name if source.type == "store" else detect_store(rest),
        "url": entry["url"], "discuss": entry["discuss_url"], "published_ts": ts, "undated": undated,
        "store_candidates": entry.get("store_candidates", []),
        "product_meta": entry.get("product_meta", {}) if source.type == "store" else {},
        "price_origin": "store" if source.type == "store" else "feed",
        "image_url": product_images.image_url(entry.get("image_url")),
        "fetched_ts": now, "community_score": entry.get("community_score"), "expired": entry["expired"],
        "asin": asin, "ident": f"asin:{asin}" if asin else "n:" + name_key(name),
    }


def hist_key(d):
    if d["trust"] == "merchant":
        # Compare a store's exact SKU/variant against its own observed prices.
        return f"store:{d['url']}|{d['cond']}"
    return f"{d['ident']}|{d['cond']}"


def dup_key(d):
    return f"{d['ident']}|{d['cond']}|{d['price']}"


# ============================== история цен и оценка ==============================
def hist_stats(samples, now, hist_days, exclude_pid=None):
    cutoff = now - hist_days * 86400
    prices = [s[1] for s in samples if s[0] >= cutoff and s[2] != exclude_pid]
    n = len(prices)
    return {"n": n, "median": statistics.median(prices) if n else None,
            "min": min(prices) if n else None, "max": max(prices) if n else None}


def add_sample(samples, d, now):
    # A store SKU keeps the same item ID for years; separate spaced observations.
    pid = (f"{d['id']}@observation:{int(now // (12 * 3600))}"
           if d["trust"] == "merchant" else d["id"])
    if any(s[2] == pid and s[1] == d["price"] for s in samples):
        return
    if any(s[1] == d["price"] and now - s[0] < 12 * 3600 for s in samples):  # та же цена из другой ленты
        return
    samples.append([now, d["price"], pid, d["source_id"]])


def assess(d, stats, cfg, now, market_evidence=None):
    """Решение о публикации. Возвращает (ok, info). info['reason'] — причина отказа/ожидания."""
    info = {"claimed": None, "pct": 0.0, "sav": 0.0, "claim_bad": False, "median_pct": None,
            "new_low_obs": False, "reason": "", "final": False}

    def no(reason, final=False):
        info["reason"], info["final"] = reason, final
        return False, info

    if not d["publish"]:
        return no("источник только для наблюдений", True)
    if d["cat"] == "pixel" and "conditional_phone_price" in d["flags"]:
        return no("цена Pixel зависит от trade-in, тарифа или рассрочки", True)
    if d["price"] is None:
        return no(f"цена не определена ({d['issue']})", True)
    if d["cat"] in ("other", "review"):
        return no("категория не распознана", True)
    if d["expired"]:
        return no("сделка помечена как expired/sold out", True)
    if d["published_ts"] is None:
        return no("нет даты публикации", True)
    age_h = (now - d["published_ts"]) / 3600
    if age_h > cfg["max_age_h"]:
        return no(f"старше {cfg['max_age_h']} ч", True)

    min_pct, min_sav = cfg["rules"].get(d["cat"], DEFAULT_RULE)
    if market_evidence:
        info["market"] = market_evidence
        return True, info
    price, orig = d["price"], d["original"]
    n, med = stats["n"], stats["median"]

    if orig and orig > price:
        pct, sav = (orig - price) / orig * 100, orig - price
        suspicious = pct > MAX_SANE_DISCOUNT or (n >= 3 and med and orig > INFLATE_FACTOR * med)
        if suspicious:
            info["claim_bad"] = True
        else:
            info.update(claimed=orig, pct=pct, sav=sav)
    strong_claim = info["claimed"] is not None and info["pct"] >= min_pct and info["sav"] >= min_sav

    strong_obs = False
    if n >= 3 and med:
        info["median_pct"] = (med - price) / med * 100
        info["new_low_obs"] = price < stats["min"]
        strong_obs = info["median_pct"] >= min_pct and (med - price) >= min_sav

    trust, score = d["trust"], d["community_score"]
    if trust == "community":
        if score is None:
            return no("нет реальных голосов сообщества")
        need = cfg["min_score"] * (2 if d["cat"] == "combo" else 1)
        if score < need:
            return no(f"голоса {score}/{need}")
        if d["cat"] == "combo" or strong_claim or strong_obs or score >= 3 * cfg["min_score"]:
            return True, info
        return no(f"нет подтверждённой скидки (голоса {score})")
    if trust == "curated":
        if info["claimed"] is not None and not strong_claim:
            return no(f"скидка {info['pct']:.0f}% / ${info['sav']:.0f} ниже порога {min_pct:g}% / ${min_sav:g}")
        return True, info
    # editorial / unvetted: нужна подтверждённая скидка
    if strong_claim or strong_obs:
        return True, info
    if info["claim_bad"]:
        return no("заявленная старая цена выглядит завышенной")
    if info["claimed"] is not None:
        return no(f"скидка {info['pct']:.0f}% / ${info['sav']:.0f} ниже порога {min_pct:g}% / ${min_sav:g}")
    return no(f"нет подтверждения скидки (наблюдений цены: {n})")


# ============================== сообщение ==============================
def fmt_price(x):
    return f"${x:,.2f}".replace(".00", "")


def safe_url(u):
    u = (u or "").strip()
    return u if re.match(r"^https?://[^\s<>\"]+$", u) else None


def build_message(d, info):
    e = html.escape
    name = d["name"] if len(d["name"]) <= 180 else d["name"][:177] + "…"
    lines = [f"📦 <b>[{e(d['tag'] or d['cat'].upper())}] {e(name)}</b>", ""]
    price_line = f"💰 Цена: <b>{fmt_price(d['price'])}</b>"
    if d["price_kind"] == "coupon":
        price_line += " (с купоном)"
    elif d["price_kind"] == "code":
        price_line += f" (по промокоду{' ' + e(d['code']) if d['code'] else ''})"
    if info["claimed"]:
        label = "MSRP" if d["original_kind"] == "MSRP" else "было"
        price_line += f" ({label} {fmt_price(info['claimed'])}, −{info['pct']:.0f}%)"
    lines.append(price_line)
    direct = store_links.merchant_url(d.get("url"))
    if direct:
        lines.append(f'🛒 <a href="{e(direct, quote=True)}">Открыть товар в магазине</a>')
    if info.get("market"):
        m = info["market"]
        refs = ", ".join(f'<a href="{e(q["url"], quote=True)}">{e(q["shop"])}</a>' for q in m["references"])
        lines.append(f"📊 Другие магазины: от {fmt_price(m['baseline'])}; экономия с доставкой {fmt_price(m['saved'])} (−{m['pct']:.0f}%)")
        lines.append("Проверены: " + refs)
    lines.append(f"⚠️ Условие: {d['cond']}")
    if d["store"]:
        lines.append(f"🏪 Магазин: {e(d['store'])}")
    if info["median_pct"] is not None and info["median_pct"] >= 10:
        lines.append(f"📉 Ниже медианы наших наблюдений на {info['median_pct']:.0f}%")
    if info["new_low_obs"]:
        lines.append("🔥 Минимум среди наших наблюдений")
    if "membership" in d["flags"]:
        lines.append("ℹ️ Нужна подписка/членство")
    if "cashback" in d["flags"]:
        lines.append("ℹ️ Часть выгоды — кэшбэк/подарочная карта/ребейт")
    if d["community_score"] is not None:
        lines.append(f"👍 Голоса сообщества: {d['community_score']}")
    lines.append(f"🔎 Источник: {e(d['source'])}")
    link = os.environ.get("CHANNEL_LINK", "")
    if link:
        lines += ["", "———————————————", f"📢 Ссылка для друзей: {e(link)}"]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "…"


# ============================== Telegram ==============================
class TelegramError(Exception):
    def __init__(self, msg, fatal=False, permanent=False, ambiguous=False):
        super().__init__(msg)
        self.fatal, self.permanent, self.ambiguous = fatal, permanent, ambiguous


def redact(text):
    tok = os.environ.get("TG_BOT_TOKEN", "")
    return str(text).replace(tok, "***") if tok else str(text)


def photo_caption(text):
    plain = html.unescape(re.sub(r"<[^>]*>", "", text))
    if len(plain.encode("utf-16-le")) <= 1024 * 2:
        return {"caption": text, "parse_mode": "HTML"}
    # Rare long captions: preserve essential leading information without cutting HTML tags.
    caption = plain.encode("utf-16-le")[:1023 * 2].decode("utf-16-le", errors="ignore") + "…"
    return {"caption": caption}


def tg_send(text, session=requests, sleep=time.sleep, store_url=None, photo_url=None):
    """Отправка с retry. Остаточный риск: если ответ потерян после доставки (read timeout),
    сообщение помечается ambiguous и НЕ отправляется повторно (лучше пропуск, чем дубль)."""
    token, chat = os.environ["TG_BOT_TOKEN"], os.environ["TG_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    preview = {"is_disabled": False}
    if store_url:
        direct = store_links.merchant_url(store_url)
        if not direct:
            raise TelegramError("нет прямой ссылки магазина", permanent=True)
        escaped = html.escape(direct, quote=True)
        if f'href="{escaped}"' not in text and direct not in text:
            text += f'\n🛒 <a href="{escaped}">Открыть товар в магазине</a>'
        # Select the deal, even when comparison references or channel links occur first.
        preview.update(url=direct, prefer_large_media=True, show_above_text=False)
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "link_preview_options": preview}
    last = "неизвестная ошибка"
    for attempt in range(4):
        try:
            r = session.post(url, json=payload, timeout=(5, 20))
        except requests.exceptions.ReadTimeout:
            raise TelegramError("таймаут чтения: сообщение могло быть доставлено", ambiguous=True)
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout) as ex:
            last = redact(f"сетевая ошибка: {type(ex).__name__}")
            sleep(2 ** attempt)
            continue
        except requests.exceptions.RequestException as ex:
            raise TelegramError(redact(f"ошибка запроса: {type(ex).__name__}"), permanent=True)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("ok"):
            return data
        desc = str(data.get("description", ""))[:200]
        if r.status_code == 429:
            wait = min(int(data.get("parameters", {}).get("retry_after", 5)), 60) + 1
            last = f"429, ждём {wait} с"
            sleep(wait)
            continue
        if r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            sleep(2 ** attempt)
            continue
        fatal = r.status_code in (401, 403, 404) or "chat not found" in desc.lower()
        raise TelegramError(f"Telegram HTTP {r.status_code}: {desc}", fatal=fatal, permanent=True)
    raise TelegramError(f"Telegram недоступен после повторов: {last}")


# ============================== состояние (атомарная запись) ==============================
def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def load_state():
    st = _load(DATA / STATE_F, None)
    if st is None:  # миграция со старой версии (seen.json)
        old = _load(DATA / LEGACY_SEEN_F, {})
        st = {"version": 2, "processed": {k: v for k, v in old.items() if not k.startswith("k:")},
              "posted": {}, "pending": {}, "sources": {}, "review": []}
    for k, default in (("processed", {}), ("posted", {}), ("pending", {}), ("sources", {}), ("review", [])):
        st.setdefault(k, default)
    return st


def trim_state(st, now, cfg):
    st["pending"] = dict(sorted(((k, d) for k, d in st["pending"].items()
                                if d.get("published_ts") and now - d["published_ts"] <= cfg["max_age_h"] * 3600),
                               key=lambda kv: kv[1].get("published_ts", 0))[-500:])
    st["posted"] = {k: v for k, v in st["posted"].items() if now - v < max(cfg["dup_hours"], 24) * 3600 * 2}
    st["processed"] = dict(sorted(st["processed"].items(), key=lambda kv: kv[1])[-5000:])
    st["review"] = st["review"][-100:]


def prune_history(hist, now, days):
    cutoff = now - days * 86400
    out = {k: [s for s in v if s[0] >= cutoff] for k, v in hist.items()}
    return {k: v for k, v in out.items() if v}


# ============================== основной цикл ==============================
def process(deals, state, hist, now, cfg, send, dry=False, save_state=None, save_hist=None, log=print,
            image_resolver=None, market=None):
    """Обработка сделок. Возвращает (stats, per_source, waiting, fatal)."""
    stats, per_src, waiting, fatal = Counter(), defaultdict(Counter), [], False
    save_state = save_state or (lambda s: None)
    save_hist = save_hist or (lambda h: None)

    pending = state["pending"]
    state["pending"] = {}
    known = {(d["id"], d["price"]) for d in deals}
    for d in pending.values():          # отложенные лимитом в прошлый запуск (новый запуск мог получить 304)
        if (d["id"], d["price"]) not in known:
            deals.append(d)
    if market:
        for d in deals:
            market.add(d)

    # Components, peripherals and Pixel first; laptops/prebuilts use the remaining slots.
    deals.sort(key=lambda d: (d["cat"] in ("laptop", "prebuilt"), -(d["published_ts"] or 0)))
    transient_fails = 0
    link_deadline = time.monotonic() + 60

    for d in deals:
        sid = d["source_id"]
        pkey = f"{d['id']}@{d['price']}"           # смена цены у той же записи = новая оценка
        if pkey in state["processed"]:
            if d["trust"] == "merchant" and d["price"] is not None and d["cat"] not in ("other", "review"):
                add_sample(hist.setdefault(hist_key(d), []), d, now)
            stats["уже обработаны"] += 1
            continue
        if d["cat"] == "review":
            state["review"].append({"title": d["title"], "url": d["url"], "source": d["source"]})
            state["processed"][pkey] = now
            stats["неизвестная категория (в очередь проверки)"] += 1
            per_src[sid]["очередь проверки"] += 1
            continue
        if d["cat"] == "other":
            state["processed"][pkey] = now
            stats["не комплектующие"] += 1
            per_src[sid]["не комплектующие"] += 1
            continue
        if d["price"] is None:
            state["processed"][pkey] = now
            stats["цена не определена"] += 1
            per_src[sid][f"цена: {d['issue']}"] += 1
            continue

        samples = hist.setdefault(hist_key(d), [])
        stats_h = hist_stats(samples, now, cfg["hist_days"],
                             exclude_pid=None if d["trust"] == "merchant" else d["id"])
        ok, info = assess(d, stats_h, cfg, now)
        if market and not ok and not info["final"]:
            evidence, reason = market.compare(d, *cfg["rules"].get(d["cat"], DEFAULT_RULE))
            if evidence:
                ok, info = assess(d, stats_h, cfg, now, evidence)
                d["url"] = evidence["candidate_url"]
            else:
                info["reason"] += "; сравнение магазинов: " + reason
        add_sample(samples, d, now)
        per_src[sid]["оценено"] += 1

        dk = dup_key(d)
        if ok and now - state["posted"].get(dk, 0) < cfg["dup_hours"] * 3600:
            state["processed"][pkey] = now
            stats["дубль за окно"] += 1
            per_src[sid]["дубли"] += 1
            continue

        if not ok:
            if info["final"]:
                state["processed"][pkey] = now
                stats["отклонено окончательно"] += 1
                per_src[sid][f"откл.: {info['reason']}"] += 1
            else:
                if market and market_prices.identity(d):
                    state["pending"][pkey] = d
                stats["ждут"] += 1
                per_src[sid]["ждут"] += 1
                waiting.append(f"  • [{d['cat']}] {d['name'][:55]} — {fmt_price(d['price'])} ({d['source']}): {info['reason']}")
            continue

        if stats["опубликовано"] >= cfg["max_posts"]:
            state["pending"][pkey] = d             # не теряем: уйдёт в следующем запуске, пока свежее
            stats["отложено лимитом"] += 1
            continue
        if not safe_url(d["url"]):
            state["processed"][pkey] = now
            stats["нет корректной ссылки"] += 1
            continue

        direct = store_links.resolve_store_url(
            d["url"], d.get("store_candidates", []), d.get("discuss", ""),
            budget=max(0, min(15, link_deadline - time.monotonic())))
        if not direct:
            # Retry on the next run, including when the feed returns 304.
            state["pending"][pkey] = d
            stats["ждут прямой ссылки магазина"] += 1
            per_src[sid]["нет прямой ссылки магазина"] += 1
            continue
        d["url"] = direct

        photo = product_images.image_url(d.get("image_url"))
        d["image_url"] = photo

        msg = build_message(d, info)
        if dry:
            log(msg + "\nВ магазин: " + direct + ("\nФото: " + photo if photo else "") + "\n" + "=" * 40)
            stats["опубликовано"] += 1
            continue
        try:
            send(msg, store_url=direct, photo_url=photo)
        except TelegramError as ex:
            if ex.ambiguous:      # возможно доставлено: помечаем, чтобы не плодить дубли
                log(f"[WARN] {ex}; помечаю как отправленное (риск пропуска вместо дубля)")
                state["processed"][pkey], state["posted"][dk] = now, now
                save_state(state)
                stats["неоднозначно (возможно доставлено)"] += 1
                continue
            log(f"[ERROR] отправка не удалась: {redact(ex)}")
            stats["ошибки отправки"] += 1
            if ex.fatal:
                fatal = True
                break
            if ex.permanent:  # ошибка именно этого сообщения: не зацикливаемся
                state["processed"][pkey] = now
                save_state(state)
                continue
            transient_fails += 1
            if transient_fails >= 2:
                log("[ERROR] Telegram недоступен, прерываю отправку до следующего запуска")
                break
            continue
        # подтверждённый успех -> checkpoint сразу
        state["processed"][pkey], state["posted"][dk] = now, now
        save_state(state)
        stats["опубликовано"] += 1
        per_src[sid]["опубликовано"] += 1

    save_hist(hist)
    return stats, per_src, waiting, fatal


def print_report(results, per_src, stats, waiting, hist):
    print("\n===== ИСТОЧНИКИ =====")
    for r in results:
        s = r.source
        newest = max((e["published_ts"] for e in r.entries if e["published_ts"]), default=None)
        age = f"{(time.time() - newest) / 3600:.1f} ч" if newest else "—"
        status = "304 (без изменений)" if r.not_modified else (f"HTTP {r.status}" if r.status else "нет ответа")
        err = f" | ОШИБКА: {redact(r.error)}" if r.error else ""
        c = per_src.get(s.id, Counter())
        details = ", ".join(f"{k} {v}" for k, v in c.items())
        print(f"[{s.id}] {status} | записей {len(r.entries)} | свежайшая {age} | {r.seconds:.1f} с{err}")
        if details:
            print(f"     {details}")
    print("\n===== ИТОГ ЗАПУСКА =====")
    for k, v in stats.most_common():
        print(f"{k}: {v}")
    print(f"товаров с историей цен: {len(hist)}")
    if waiting:
        print("\nНе опубликованы — причины автоматической проверки (первые 15):")
        print("\n".join(waiting[:15]))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--smoke" in argv:
        return smoke()
    dry, cfg, now = is_dry(), get_cfg(), time.time()
    if not dry and not (os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID")):
        print("[ERROR] не заданы TG_BOT_TOKEN / TG_CHAT_ID (GitHub Secrets)")
        return 2
    print(f"Режим: {'DRY RUN (без отправки и сохранения)' if dry else 'боевой'}")

    state, hist = load_state(), _load(DATA / HIST_F, {})
    hist = prune_history(hist, now, cfg["hist_days"])
    srcs = S.active_sources()
    print(f"Активных источников: {len(srcs)}")
    results = S.fetch_all(srcs, {} if dry else state["sources"])

    deals = []
    for r in results:
        st = state["sources"].setdefault(r.source.id, {})
        if r.source.type == "store" and r.not_modified:
            # Preserve the previous error/backoff state during the cache interval.
            continue
        if r.source.type == "store" and not r.not_modified:
            st["last_attempt"] = now
        if r.error:
            st["errors"] = st.get("errors", 0) + 1
        else:
            st.update(errors=0, last_ok=now)
            if r.etag:
                st["etag"] = r.etag
            if r.last_modified:
                st["last_modified"] = r.last_modified
        for e in r.entries:
            deals.append(make_deal(e, r.source, now))
    if results and all(r.error for r in results):
        print_report(results, {}, Counter(), [], hist)
        print("[ERROR] все источники недоступны")
        return 1

    # дубли одной и той же записи из разных лент/запросов одного источника
    uniq = {}
    for d in deals:
        uniq.setdefault((d["id"], d["price"]), d)
    deals = list(uniq.values())

    ss = (lambda s: None) if dry else (lambda s: (trim_state(s, now, cfg), atomic_write_json(DATA / STATE_F, s)))
    sh = (lambda h: None) if dry else (lambda h: atomic_write_json(DATA / HIST_F, h))
    stats, per_src, waiting, fatal = Counter(), {}, [], False
    def verify_product(url):
        entry = S.fetch_market_product(url)
        if entry:
            source = S.Source("market-check", market_prices.shop(url), url,
                              type="store", trust="merchant", allow_undated=True)
            return make_deal(entry, source, now)
        return None
    market = market_prices.MarketPrices(state, now, verifier=verify_product)
    try:
        stats, per_src, waiting, fatal = process(deals, state, hist, now, cfg, tg_send, dry, ss, sh,
                                               market=market)
    finally:
        ss(state)
        sh(hist)
    print_report(results, per_src, stats, waiting, hist)
    return 1 if fatal else 0


def smoke():
    """Проверка всех источников (включая отключённые): статус, тип, число записей, свежесть."""
    print("===== SMOKE TEST ИСТОЧНИКОВ (состояние и Telegram не затрагиваются) =====")
    print("Время теста (UTC):", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
    for s in S.SOURCES:
        if s.type == "reddit":
            try:
                creds = S.reddit_credentials()
            except S.ConfigError as ex:
                print(f"[{s.id}] КОНФИГ: {ex}")
                continue
            if creds is None:
                print(f"[{s.id}] пропущен: Reddit не настроен")
                continue
        r = S.fetch_one(s)
        newest = max((e["published_ts"] for e in r.entries if e["published_ts"]), default=None)
        age = f"{(time.time() - newest) / 3600:.1f} ч назад" if newest else "нет дат"
        flag = "OK " if (r.status == 200 and r.entries and not r.error) else "FAIL"
        print(f"{flag} [{s.id}] enabled={s.enabled} HTTP {r.status} | {r.content_type[:40]} | записей {len(r.entries)}"
              f" | свежайшая {age} | {r.seconds:.1f} с" + (f" | {redact(r.error)}" if r.error else ""))
        for e in r.entries[:2]:
            print("      ·", e["title"][:100])
    return 0


if __name__ == "__main__":
    sys.exit(main())
