import json
import os
import unittest
from unittest import mock

import requests
import bot
import direct_stores as D
import product_images as P
import sources as S


IMAGE = "https://images.example.com/ssd.jpg"
STORE = "https://www.amazon.com/dp/B0ABCDE123"


def response(code=200, data=None):
    r = mock.Mock(status_code=code)
    r.json.return_value = {"ok": True} if data is None else data
    return r


class ImageExtraction(unittest.TestCase):
    def test_rss_full_content_image_survives_normalization(self):
        e = S.normalize_entry({"title": "SSD $99", "link": "https://slickdeals.net/f/123",
                               "content": [{"value": '<img src="' + IMAGE + '" width="300">'}]})
        self.assertEqual(e["image_url"], IMAGE)
        d = bot.make_deal(e, S.Source("sd", "SD", "https://slickdeals.net"), 1000)
        self.assertEqual(d["image_url"], IMAGE)

    def test_skip_tracking_pixels_and_avatars(self):
        markup = '<img src="https://example.com/pixel.gif" width="1" height="1">'
        markup += '<img src="https://example.com/avatar.jpg"><img src="' + IMAGE + '">'
        self.assertEqual(P.extract_image(markup), IMAGE)

    def test_media_thumbnail_and_image_enclosure(self):
        self.assertEqual(P.from_feed({"media_thumbnail": [{"url": IMAGE}]}, "", STORE), IMAGE)
        self.assertEqual(P.from_feed({"enclosures": [{"type": "image/jpeg", "href": IMAGE}]}, "", STORE), IMAGE)
        self.assertIsNone(P.from_feed({"media_content": [{"type": "video/mp4", "url": IMAGE}]}, "", STORE))

    def test_metadata_priority_and_relative_urls(self):
        markup = '<img src="/sidebar.jpg"><meta property="og:image" content="/product.jpg?x=1&amp;y=2">'
        self.assertEqual(P.extract_image(markup, STORE, product_page=True),
                         "https://www.amazon.com/product.jpg?x=1&y=2")
        self.assertIsNone(P.extract_image('<img src="/sidebar.jpg">', STORE, product_page=True))

    def test_jsonld_image_variants_and_direct_catalog(self):
        product = {"@type": "Product", "name": "SSD", "url": STORE,
                   "image": [{"@type": "ImageObject", "url": IMAGE}],
                   "offers": {"price": "99", "priceCurrency": "USD"}}
        markup = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
        self.assertEqual(P.extract_image(markup, STORE, product_page=True), IMAGE)
        self.assertEqual(D.parse_catalog(markup, STORE)[0]["image_url"], IMAGE)

    def test_non_public_or_non_http_urls_not_sent(self):
        for value in ("data:image/png;base64,abc", "file:///tmp/x.jpg", "http://localhost/x.jpg",
                      "http://127.0.0.1/x.jpg", "http://169.254.169.254/x.jpg", "http://[::1]/x.jpg"):
            self.assertIsNone(P.image_url(value))

    def test_corsair_cdn_requests_jpeg_rendition(self):
        url = "https://assets.corsair.com/image/upload/c_pad,q_auto,f_auto/products/keyboard.webp"
        self.assertEqual(P.image_url(url),
                         "https://assets.corsair.com/image/upload/c_pad,q_auto,f_jpg/products/keyboard.jpg")

    def test_blocked_product_page_is_optional(self):
        session = mock.Mock()
        session.get.return_value.status_code = 403
        self.assertIsNone(P.from_product_page(STORE, session=session))
        session.get.return_value.close.assert_called_once()


@mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "test-token", "TG_CHAT_ID": "test-chat"})
class PhotoDelivery(unittest.TestCase):
    def test_send_photo_caption_and_store_button(self):
        session = mock.Mock()
        session.post.return_value = response()
        bot.tg_send("<b>SSD</b> $99", session=session, photo_url=IMAGE, store_url=STORE)
        call = session.post.call_args
        self.assertTrue(call.args[0].endswith("/sendPhoto"))
        data = call.kwargs["json"]
        self.assertEqual(data["photo"], IMAGE)
        self.assertEqual(data["caption"], "<b>SSD</b> $99")
        self.assertNotIn("text", data)
        self.assertEqual(data["reply_markup"]["inline_keyboard"][0], [{"text": "В магазин", "url": STORE}])

    def test_rejected_photo_falls_back_once_with_same_button(self):
        session = mock.Mock()
        session.post.side_effect = [response(400, {"description": "Bad Request: failed to get HTTP URL content"}), response()]
        bot.tg_send("SSD $99", session=session, photo_url=IMAGE, store_url=STORE)
        self.assertEqual(session.post.call_count, 2)
        self.assertTrue(session.post.call_args.args[0].endswith("/sendMessage"))
        self.assertEqual(session.post.call_args.kwargs["json"]["text"], "SSD $99")
        self.assertIn("reply_markup", session.post.call_args.kwargs["json"])

    def test_photo_timeout_never_falls_back_or_resends(self):
        session = mock.Mock()
        session.post.side_effect = requests.exceptions.ReadTimeout()
        with self.assertRaises(bot.TelegramError) as ctx:
            bot.tg_send("SSD", session=session, photo_url=IMAGE)
        self.assertTrue(ctx.exception.ambiguous)
        self.assertEqual(session.post.call_count, 1)

    def test_chat_permission_error_is_not_hidden_by_fallback(self):
        session = mock.Mock()
        session.post.return_value = response(403, {"description": "bot was blocked"})
        with self.assertRaises(bot.TelegramError) as ctx:
            bot.tg_send("SSD", session=session, photo_url=IMAGE)
        self.assertTrue(ctx.exception.fatal)
        self.assertEqual(session.post.call_count, 1)

    def test_429_retries_photo_not_text(self):
        session = mock.Mock()
        session.post.side_effect = [response(429, {"parameters": {"retry_after": 2}}), response()]
        bot.tg_send("SSD", session=session, photo_url=IMAGE, sleep=lambda _: None)
        self.assertTrue(all(c.args[0].endswith("/sendPhoto") for c in session.post.call_args_list))

    def test_caption_limit_does_not_break_entities_or_emoji(self):
        caption = bot.photo_caption("<b>" + "📦&amp;" * 600 + "</b>")
        self.assertNotIn("parse_mode", caption)
        self.assertLessEqual(len(caption["caption"].encode("utf-16-le")), 2048)
        self.assertNotIn("<b>", caption["caption"])
        self.assertTrue(caption["caption"].endswith("…"))

    def test_missing_photo_preserves_text_delivery(self):
        session = mock.Mock()
        session.post.return_value = response()
        bot.tg_send("SSD", session=session)
        self.assertTrue(session.post.call_args.args[0].endswith("/sendMessage"))

    def test_process_passes_image_to_sender(self):
        source = S.Source("sd", "SD", "https://slickdeals.net", trust="curated")
        now = 1_800_000_000
        e = {"item_id": "1", "title": "Samsung SSD $99 (was $200)", "url": STORE,
             "discuss_url": "", "image_url": IMAGE, "expired": False, "published_ts": now - 10}
        d = bot.make_deal(e, source, now)
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        send = mock.Mock()
        bot.process([d], state, {}, now, bot.get_cfg(), send)
        self.assertEqual(send.call_args.kwargs["photo_url"], IMAGE)
        self.assertEqual(len(state["posted"]), 1)


if __name__ == "__main__":
    unittest.main()
