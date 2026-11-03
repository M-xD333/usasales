import json
import os
import unittest
from unittest import mock
from urllib.parse import quote

import bot
import sources
import store_links as L


AMAZON = "https://www.amazon.com/dp/B0ABCDE123"
NEWEGG = "https://www.newegg.com/p/N82E16820147875"
DISCUSS = "https://slickdeals.net/f/12345678-ssd"


def response(code=200, body="", location=None):
    r = mock.Mock(status_code=code, encoding="utf-8")
    r.headers = {"Location": location} if location else {}
    r.iter_content.return_value = [body.encode()]
    return r


class StoreLinks(unittest.TestCase):
    def test_rss_keeps_store_link_before_stripping_html(self):
        e = sources.normalize_entry({"link": DISCUSS, "title": "SSD $99",
                                     "summary": f'<a href="{AMAZON}?tag=affiliate">Buy</a>'})
        self.assertEqual(e["discuss_url"], DISCUSS)
        self.assertEqual(e["store_candidates"], [AMAZON])
        self.assertEqual(L.resolve_store_url(e["url"], e["store_candidates"]), AMAZON)

    def test_nested_encoded_redirect(self):
        wrapped = "https://slickdeals.net/click?u2=" + quote(quote(NEWEGG, safe=""), safe="")
        self.assertEqual(L.unwrap(wrapped), NEWEGG)

    def test_slickdeals_full_content_click_precedes_blocked_discussion(self):
        click = "https://slickdeals.net/click?lno=1&u3=opaque"
        e = sources.normalize_entry({"link": DISCUSS, "title": "SSD $99",
                                     "summary": "SSD on sale",
                                     "content": [{"value": '<a href="' + click + '">Amazon</a>'}]})
        self.assertEqual(e["store_candidates"], [click])
        session = mock.Mock()
        session.get.return_value = response(302, location=AMAZON)
        self.assertEqual(L.resolve_store_url(e["url"], e["store_candidates"], session=session), AMAZON)
        self.assertEqual(session.get.call_args.args[0], click)

    def test_reject_aggregators_search_and_lookalike_domains(self):
        for url in (DISCUSS, "https://amazon.com.evil.test/dp/B0ABCDE123",
                    "https://www.amazon.com/s?k=ssd", "https://www.newegg.com/",
                    "https://www.amazon.com@evil.test/dp/B0ABCDE123", "javascript:alert(1)"):
            self.assertIsNone(L.merchant_url(url))

    def test_page_buy_button_and_redirect_chain(self):
        session = mock.Mock()
        session.get.side_effect = [response(body='<a href="/click?id=42" class="buy-button">Buy now</a>'),
                                   response(302, location="https://go.skimresources.com/?id=42"),
                                   response(302, location=NEWEGG)]
        self.assertEqual(L.resolve_store_url(DISCUSS, session=session), NEWEGG)
        self.assertEqual(session.get.call_count, 3)
        self.assertTrue(all(c.kwargs["allow_redirects"] is False for c in session.get.call_args_list))

    def test_product_jsonld_and_no_sidebar_fallback(self):
        markup = '<a href="' + NEWEGG + '">Related article</a>'
        self.assertEqual(L.extract_links(markup, DISCUSS, product_page=True), [])
        markup += '<script type="application/ld+json">' + json.dumps({
            "@type": "Product", "offers": {"@type": "Offer", "url": AMAZON}}) + '</script>'
        self.assertEqual(L.extract_links(markup, DISCUSS, product_page=True), [AMAZON])

    def test_blocked_page_never_becomes_store_link(self):
        session = mock.Mock()
        session.get.return_value = response(403)
        self.assertIsNone(L.resolve_store_url(DISCUSS, session=session))

    def test_redirect_loop_is_bounded(self):
        session = mock.Mock()
        session.get.return_value = response(302, location=DISCUSS)
        self.assertIsNone(L.resolve_store_url(DISCUSS, session=session))
        self.assertEqual(session.get.call_count, 6)

    @mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "test", "TG_CHAT_ID": "test"})
    def test_telegram_button_points_to_product(self):
        session = mock.Mock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {"ok": True}
        bot.tg_send("SSD", session=session, store_url=AMAZON)
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["reply_markup"], {
            "inline_keyboard": [[{"text": "В магазин", "url": AMAZON}]]})
        self.assertNotIn("Обсуждение", json.dumps(payload, ensure_ascii=False))

    @mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "test", "TG_CHAT_ID": "test"})
    def test_telegram_rejects_aggregator_button(self):
        session = mock.Mock()
        with self.assertRaises(bot.TelegramError):
            bot.tg_send("SSD", session=session, store_url=DISCUSS)
        session.post.assert_not_called()

    @mock.patch("store_links.resolve_store_url", return_value=None)
    def test_missing_product_link_is_retried_even_after_empty_feed(self, resolve):
        now = 1_800_000_000
        source = sources.Source("sd-frontpage", "Slickdeals", DISCUSS, trust="curated")
        e = {"item_id": "deal", "title": "Samsung SSD $99 (was $200)", "url": DISCUSS,
             "discuss_url": DISCUSS, "summary": "", "published_ts": now - 10, "expired": False}
        d = bot.make_deal(e, source, now)
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        send = mock.Mock()
        bot.process([d], state, {}, now, bot.get_cfg(), send)
        send.assert_not_called()
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(state["processed"], {})
        resolve.return_value = AMAZON
        bot.process([], state, {}, now + 300, bot.get_cfg(), send)
        self.assertEqual(send.call_args.kwargs["store_url"], AMAZON)
        self.assertEqual(state["pending"], {})


if __name__ == "__main__":
    unittest.main()
