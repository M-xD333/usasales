import calendar, json, os, sys, tempfile, time, unittest
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
import bot
import sources as S
import requests

NOW = 1_800_000_000.0


def cfg(**kw):
    c = bot.get_cfg()
    c.update(kw)
    return c


def src(trust="unvetted", **kw):
    return S.Source(id=kw.pop("id", "t"), name=kw.pop("name", "Test"), url="http://x", trust=trust, **kw)


def entry(title, url="https://example.com/d/1", item_id=None, ts=NOW - 600, score=None, **kw):
    e = {"item_id": item_id or url, "url": url, "discuss_url": url, "title": title, "summary": "",
         "published_ts": ts, "expired": False, "community_score": score, "flair": ""}
    e.update(kw)
    return e


def deal(title, trust="unvetted", **kw):
    es = entry(title, **{k: kw.pop(k) for k in list(kw) if k in ("url", "item_id", "ts", "score", "expired")})
    return bot.make_deal(es, src(trust, **kw), NOW)


class FakeResp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d = code, data if data is not None else {"ok": True}

    def json(self):
        return self._d


class PriceParsing(unittest.TestCase):
    def test_basic_and_thousands(self):
        self.assertEqual(bot.parse_prices("Samsung 990 Pro $119.99")["price"], 119.99)
        self.assertEqual(bot.parse_prices("Gaming PC $1,299.00")["price"], 1299.0)

    def test_was_msrp_off(self):
        r = bot.parse_prices("SSD $100 (was $200)")
        self.assertEqual((r["original"], r["original_kind"]), (200.0, "was"))
        r = bot.parse_prices("GPU $400 (MSRP $600)")
        self.assertEqual(r["original_kind"], "MSRP")
        r = bot.parse_prices("RAM $80 - $20 off")
        self.assertEqual((r["price"], r["original"], r["original_kind"]), (80.0, 100.0, "derived"))
        r = bot.parse_prices("CPU $240 (20% off)")
        self.assertAlmostEqual(r["original"], 300.0)

    def test_coupon_and_code(self):
        r = bot.parse_prices("Mouse $25 w/ clipped coupon")
        self.assertEqual(r["price_kind"], "coupon")
        r = bot.parse_prices("Headset $40 with promo code SAVE20")
        self.assertEqual((r["price_kind"], r["code"]), ("code", "SAVE20"))

    def test_ambiguous_not_invented(self):
        self.assertEqual(bot.parse_prices("Monitors $199 - $399")["issue"], "price_range")
        self.assertEqual(bot.parse_prices("Laptops starting at $499")["issue"], "price_from")
        self.assertEqual(bot.parse_prices("Bundle SSD $50 plus mouse $30")["issue"], "multiple_prices")
        self.assertEqual(bot.parse_prices("Great deal on SSD")["issue"], "no_price")
        self.assertEqual(bot.parse_prices("Router £49.99")["issue"], "non_usd")
        self.assertEqual(bot.parse_prices("SSD C$99.99")["issue"], "non_usd")

    def test_flags(self):
        r = bot.parse_prices("SSD $50 + free shipping for Prime members")
        self.assertIn("membership", r["flags"])
        self.assertIn("free_shipping", r["flags"])


class Categories(unittest.TestCase):
    def test_categories(self):
        C = bot.classify
        self.assertEqual(C("ASUS RTX 4070 Super Graphics Card"), "gpu")
        self.assertEqual(C("AMD Ryzen 7 7800X3D CPU"), "cpu")
        self.assertEqual(C("MSI Gaming Laptop RTX 4070 16GB DDR5"), "laptop")   # не gpu
        self.assertEqual(C("Corsair 32GB DDR5-6000 Memory Kit"), "ram")
        self.assertEqual(C("Samsung 990 PRO 2TB NVMe SSD"), "ssd")
        self.assertEqual(C("Samsung T7 Portable SSD 1TB"), "ext_storage")
        self.assertEqual(C("LG 27 inch 4K Monitor"), "monitor")
        self.assertEqual(C("TP-Link WiFi 6 Router"), "networking")
        self.assertEqual(C("Logitech G502 Gaming Mouse"), "mouse")
        self.assertEqual(C("CyberPowerPC Gaming PC RTX 4060"), "prebuilt")
        self.assertEqual(C("Beelink Mini PC Ryzen 7"), "minipc")
        self.assertEqual(C("Cuisinart Food Processor"), "other")
        self.assertEqual(C("Whatever", tag="GPU"), "gpu")
        self.assertEqual(C("Sony TV 55 inch"), "review")

    def test_condition(self):
        D = bot.detect_condition
        self.assertEqual(D("SSD Renewed"), "Refurbished")
        self.assertEqual(D("GPU open box"), "Open Box")
        self.assertEqual(D("CPU used"), "Used")
        self.assertEqual(D("CPU"), "New")


class TimeParsing(unittest.TestCase):
    def test_utc(self):
        t = time.struct_time((2026, 11, 3, 12, 0, 0, 1, 307, 0))
        self.assertEqual(S.entry_timestamp({"published_parsed": t}), calendar.timegm(t))
        self.assertEqual(S.entry_timestamp({"published_parsed": t}), 1793707200)   # 2026-11-03 12:00:00 UTC
        self.assertEqual(S.entry_timestamp({"updated_parsed": t}), 1793707200)
        self.assertIsNone(S.entry_timestamp({}))

    def test_independent_of_runner_timezone(self):
        t = time.struct_time((2026, 11, 3, 12, 0, 0, 1, 307, 0))
        old_tz = os.environ.get("TZ")
        try:
            for tz in ("UTC", "America/New_York", "Asia/Tashkent"):
                os.environ["TZ"] = tz
                time.tzset()
                self.assertEqual(S.entry_timestamp({"published_parsed": t}), 1793707200)
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()

    def test_no_fake_freshness(self):
        d = deal("Samsung SSD $50", ts=None)
        self.assertIsNone(d["published_ts"])
        ok, info = bot.assess(d, {"n": 0, "median": None, "min": None, "max": None}, cfg(), NOW)
        self.assertFalse(ok)


class Dedup(unittest.TestCase):
    def test_same_product_different_feeds(self):
        a = deal("Samsung 990 PRO 2TB NVMe SSD $119.99", item_id="a", url="https://a/1")
        b = deal("Samsung 990 PRO 2TB NVMe SSD $119.99 + Free Shipping", item_id="b", url="https://b/2")
        self.assertEqual(bot.dup_key(a), bot.dup_key(b))

    def test_variants_not_merged(self):
        a = deal("Samsung 990 PRO 1TB NVMe SSD $79.99")
        b = deal("Samsung 990 PRO 2TB NVMe SSD $79.99")
        self.assertNotEqual(bot.dup_key(a), bot.dup_key(b))
        c = deal("Samsung 990 PRO 2TB NVMe SSD refurbished $79.99")
        self.assertNotEqual(bot.dup_key(b), bot.dup_key(c))

    def test_asin_identity(self):
        a = deal("SSD $50", url="https://www.amazon.com/dp/B0DLFMTX12?ref=x")
        self.assertEqual(a["ident"], "asin:B0DLFMTX12")


class Assessment(unittest.TestCase):
    EMPTY = {"n": 0, "median": None, "min": None, "max": None}

    def test_no_new_low_without_history(self):
        d = deal("Samsung 990 PRO 2TB NVMe SSD $60", trust="curated")
        ok, info = bot.assess(d, self.EMPTY, cfg(), NOW)
        self.assertTrue(ok)
        self.assertFalse(info["new_low_obs"])
        self.assertIsNone(info["median_pct"])
        msg = bot.build_message(d, info)
        self.assertNotIn("Минимум", msg)
        self.assertNotIn("−", msg)

    def test_fake_inflated_claim_rejected(self):
        d = deal("Fake Drive 1TB NVMe SSD $70 (was $1000)")
        stats = {"n": 4, "median": 75.0, "min": 60.0, "max": 90.0}
        ok, info = bot.assess(d, stats, cfg(), NOW)
        self.assertFalse(ok)
        self.assertTrue(info["claim_bad"])

    def test_real_discount_unvetted_ok(self):
        d = deal("Samsung 990 PRO 2TB NVMe SSD $100 (was $200)")
        ok, info = bot.assess(d, self.EMPTY, cfg(), NOW)
        self.assertTrue(ok)
        self.assertEqual(round(info["pct"]), 50)

    def test_unvetted_without_evidence_waits(self):
        d = deal("Samsung 990 PRO 2TB NVMe SSD $100")
        ok, info = bot.assess(d, self.EMPTY, cfg(), NOW)
        self.assertFalse(ok)
        self.assertFalse(info["final"])

    def test_below_median(self):
        d = deal("Samsung 990 PRO 2TB NVMe SSD $100")
        ok, info = bot.assess(d, {"n": 4, "median": 150.0, "min": 120.0, "max": 170.0}, cfg(), NOW)
        self.assertTrue(ok)
        self.assertTrue(info["new_low_obs"])

    def test_reddit_needs_real_votes(self):
        d = deal("[SSD] Samsung 990 PRO 2TB $100 (was $200) @ Amazon", trust="community", score=None)
        self.assertFalse(bot.assess(d, self.EMPTY, cfg(), NOW)[0])
        d = deal("[SSD] Samsung 990 PRO 2TB $100 (was $200) @ Amazon", trust="community", score=50)
        self.assertTrue(bot.assess(d, self.EMPTY, cfg(), NOW)[0])

    def test_no_fake_score_in_rss_message(self):
        d = deal("Samsung 990 PRO 2TB NVMe SSD $100 (was $200)")
        msg = bot.build_message(d, bot.assess(d, self.EMPTY, cfg(), NOW)[1])
        self.assertNotIn("голоса", msg.lower())
        self.assertIsNone(d["community_score"])

    def test_old_and_expired(self):
        old = deal("Samsung SSD $100 (was $200)", ts=NOW - 30 * 3600)
        self.assertFalse(bot.assess(old, self.EMPTY, cfg(), NOW)[0])
        ex = deal("Samsung SSD $100 (was $200)", expired=True)
        self.assertFalse(bot.assess(ex, self.EMPTY, cfg(), NOW)[0])


class TelegramTests(unittest.TestCase):
    def setUp(self):
        os.environ["TG_BOT_TOKEN"], os.environ["TG_CHAT_ID"] = "123:SECRET", "@chan"

    def test_html_escape(self):
        d = deal("Corsair <b>RAM</b> & Co 32GB DDR5 $80 (was $160)", url="https://www.newegg.com/p/N82E1681?b=1&c=2")
        msg = bot.build_message(d, bot.assess(d, Assessment.EMPTY, cfg(), NOW)[1])
        self.assertIn("&lt;b&gt;", msg)
        self.assertNotIn("Обсуждение", msg)
        self.assertNotIn("href=", msg)
        self.assertLessEqual(len(msg), 4096)

    def test_429_then_ok(self):
        sess = mock.Mock()
        sess.post.side_effect = [FakeResp(429, {"ok": False, "parameters": {"retry_after": 2}}), FakeResp(200)]
        sleeps = []
        bot.tg_send("hi", session=sess, sleep=sleeps.append)
        self.assertEqual(sess.post.call_count, 2)
        self.assertEqual(sleeps, [3])

    def test_5xx_retry_then_fail(self):
        sess = mock.Mock()
        sess.post.return_value = FakeResp(502, {})
        with self.assertRaises(bot.TelegramError) as cm:
            bot.tg_send("hi", session=sess, sleep=lambda s: None)
        self.assertFalse(cm.exception.permanent)
        self.assertEqual(sess.post.call_count, 4)

    def test_4xx_permanent_no_retry(self):
        sess = mock.Mock()
        sess.post.return_value = FakeResp(400, {"ok": False, "description": "Bad Request: can't parse entities"})
        with self.assertRaises(bot.TelegramError) as cm:
            bot.tg_send("hi", session=sess, sleep=lambda s: None)
        self.assertTrue(cm.exception.permanent and not cm.exception.fatal)
        self.assertEqual(sess.post.call_count, 1)

    def test_chat_not_found_fatal(self):
        sess = mock.Mock()
        sess.post.return_value = FakeResp(400, {"ok": False, "description": "Bad Request: chat not found"})
        with self.assertRaises(bot.TelegramError) as cm:
            bot.tg_send("hi", session=sess)
        self.assertTrue(cm.exception.fatal)

    def test_read_timeout_ambiguous(self):
        sess = mock.Mock()
        sess.post.side_effect = requests.exceptions.ReadTimeout("x")
        with self.assertRaises(bot.TelegramError) as cm:
            bot.tg_send("hi", session=sess)
        self.assertTrue(cm.exception.ambiguous)
        self.assertEqual(sess.post.call_count, 1)

    def test_token_redacted(self):
        self.assertNotIn("SECRET", bot.redact("https://api.telegram.org/bot123:SECRET/sendMessage failed"))


class Checkpoint(unittest.TestCase):
    def _deals(self):
        return [deal(f"Samsung 990 PRO {i}TB NVMe SSD $100 (was $200)", item_id=f"id{i}", url=f"https://www.ebay.com/itm/{i}",
                     ts=NOW - 60 * i) for i in (1, 2)]

    def test_first_saved_when_second_fails(self):
        saved = []
        calls = {"n": 0}

        def send(msg, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise bot.TelegramError("boom", permanent=False)

        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        bot.process(self._deals(), state, {}, NOW, cfg(), send,
                    save_state=lambda s: saved.append(json.loads(json.dumps(s))))
        self.assertEqual(len(state["processed"]), 1)
        self.assertTrue(saved and len(saved[-1]["processed"]) == 1)
        # следующий запуск: первое не повторяется, второе отправляется
        sent = []
        bot.process(self._deals(), state, {}, NOW + 300, cfg(), lambda text, **kw: sent.append(text))
        self.assertEqual(len(sent), 1)

    def test_newest_first_and_limit_defers(self):
        sent = []
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        ds = [deal(f"Samsung 990 PRO {i}TB NVMe SSD $100 (was $200)", item_id=f"id{i}", url=f"https://www.ebay.com/itm/{i}",
                   ts=NOW - 600 * i) for i in (1, 2, 3)]
        bot.process(ds, state, {}, NOW, cfg(max_posts=1), lambda text, **kw: sent.append(text))
        self.assertEqual(len(sent), 1)
        self.assertIn("1TB", sent[0])                 # самый свежий
        self.assertEqual(len(state["pending"]), 2)    # остальные не потеряны
        bot.process([], state, {}, NOW + 300, cfg(max_posts=1), lambda text, **kw: sent.append(text))   # даже если лента отдала 304/пусто
        self.assertEqual(len(sent), 2)

    def test_dry_run_leaves_state_untouched(self):
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        sent = []
        bot.process(self._deals(), state, {}, NOW, cfg(), lambda text, **kw: sent.append(text), dry=True, log=lambda *_: None)
        self.assertEqual(sent, [])
        self.assertEqual(state["processed"], {})
        self.assertEqual(state["posted"], {})

    def test_price_change_reevaluated(self):
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        sent = []
        a = deal("Samsung 990 PRO 2TB NVMe SSD $100 (was $200)", item_id="same", url="https://www.ebay.com/itm/s")
        bot.process([a], state, {}, NOW, cfg(), lambda text, **kw: sent.append(text))
        b = deal("Samsung 990 PRO 2TB NVMe SSD $80 (was $200)", item_id="same", url="https://www.ebay.com/itm/s")
        bot.process([b], state, {}, NOW + 300, cfg(), lambda text, **kw: sent.append(text))
        self.assertEqual(len(sent), 2)

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            bot.atomic_write_json(p, {"a": 1})
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            self.assertEqual([f.name for f in Path(d).iterdir()], ["s.json"])


class SourceConfig(unittest.TestCase):
    def test_reddit_requires_both(self):
        with self.assertRaises(S.ConfigError):
            S.reddit_credentials({"REDDIT_CLIENT_ID": "x"})
        self.assertIsNone(S.reddit_credentials({}))
        self.assertEqual(S.reddit_credentials({"REDDIT_CLIENT_ID": "a", "REDDIT_CLIENT_SECRET": "b"}), ("a", "b"))
        self.assertIsNone(S.reddit_credentials({"REDDIT_CLIENT_ID": "a", "REDDIT_CLIENT_SECRET": "b", "ENABLE_REDDIT": "0"}))

    def test_enable_disable(self):
        ids = {s.id for s in S.active_sources({"ENABLE_SOURCES": "techbargains", "DISABLE_SOURCES": "sd-hot"}, log=lambda *_: None)}
        self.assertIn("techbargains", ids)
        self.assertNotIn("sd-hot", ids)
        self.assertNotIn("dealnews-computers", ids)
        self.assertNotIn("reddit-buildapcsales", ids)

    def test_dry_flag_parsing(self):
        for v, exp in (("1", True), ("true", True), ("YES", True), ("0", False), ("", False)):
            with mock.patch.dict(os.environ, {"DRY_RUN": v}):
                self.assertEqual(bot.is_dry(), exp)

    def test_304_handling(self):
        sess = mock.Mock()
        sess.get.return_value = mock.Mock(status_code=304, headers={})
        r = S.fetch_rss(S.SOURCES[0], {"etag": "abc"}, session=sess)
        self.assertTrue(r.not_modified)
        self.assertEqual(sess.get.call_args.kwargs["headers"]["If-None-Match"], "abc")


class RealTitles(unittest.TestCase):
    """Заголовки из реального smoke-теста (Slickdeals, 2026-11-03)."""

    def D(self, title):
        return bot.make_deal(entry(title), src("curated"), NOW)

    def test_store_in_brackets_is_not_category(self):
        d = self.D("[Micro Center] PowerSpec G536 Gaming PC: Ryzen 5 7500X3D, RTX 5070 12GB, 32GB DDR5, 1TB SSD $1600 + Free Shipping")
        self.assertEqual((d["cat"], d["tag"], d["store"], d["price"]), ("prebuilt", "", "Micro Center", 1600.0))
        self.assertTrue(d["name"].startswith("PowerSpec"))

    def test_laptop_without_word_laptop(self):
        d = self.D('(Refurb) Dell Latitude 5450: Intel Ultra 7 165U 32GB RAM 256GB SSD 14" Touch W11P $518.39')
        self.assertEqual((d["cat"], d["cond"], d["store"]), ("laptop", "Refurbished", ""))   # Dell — бренд, не магазин
        d = self.D('$1699 | Acer Nitro Slim 16" AMD Ryzen AI 7 350, RTX 5070 Ti, 16GB RAM 512GB SSD')
        self.assertEqual((d["cat"], d["price"]), ("laptop", 1699.0))
        self.assertTrue(d["name"].startswith("Acer Nitro"))

    def test_motherboard_with_wifi(self):
        d = self.D("MSI MAG X870E Gaming Max WiFi AM5 ATX Motherboard $169.99 at Walmart")
        self.assertEqual((d["cat"], d["store"]), ("mobo", "Walmart"))

    def test_price_first_format(self):
        d = self.D("$99* | 15.6″ ASUS ZenScreen 1080p USB-C Portable Monitor w/ Kickstand & Tripod Socket at Amazon")
        self.assertEqual((d["cat"], d["price"], d["store"]), ("monitor", 99.0, "Amazon"))
        self.assertNotIn("$", d["name"])

    def test_from_price_and_non_tech(self):
        d = self.D('[Open-Box YMMV] Lenovo Legion Go 8.8" Handheld PC Console w/ Ryzen Z1 Extreme: 512GB from $515; 1TB')
        self.assertIsNone(d["price"])
        self.assertEqual(d["issue"], "price_from")
        self.assertEqual(self.D("150' Outdoor Basic 1080-LED Rope Lights (Various Colors) $30")["cat"], "other")

    def test_plain_parts_unaffected(self):
        self.assertEqual(self.D("Samsung 990 PRO 2TB NVMe M.2 SSD $119.99")["cat"], "ssd")
        self.assertEqual(self.D("TP-Link AXE5400 Tri-Band WiFi 6E Router for $99 + free shipping")["cat"], "networking")


if __name__ == "__main__":
    unittest.main()
