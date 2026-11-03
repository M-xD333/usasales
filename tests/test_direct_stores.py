import json
import time
import unittest
from unittest import mock

import bot
import direct_stores as D
import sources as S


BASE = "https://www.corsair.com/us/en/c/memory"
URL = "https://www.corsair.com/us/en/p/memory/sku123/vengeance-ddr5-sku123"


def schema(name="VENGEANCE 32GB DDR5 RAM", price="100", currency="USD", **offer):
    return {"@type": "Product", "name": name, "url": URL,
            "offers": {"@type": "Offer", "price": price, "priceCurrency": currency, **offer}}


def markup(data):
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


class DirectParsing(unittest.TestCase):
    def test_product_list_exact_structured_price(self):
        data = {"@type": "ItemList", "itemListElement": [{"item": schema()}]}
        e = D.parse_catalog(markup(data), BASE)[0]
        self.assertEqual(e["store_price"], 100)
        self.assertEqual(e["url"], URL)
        self.assertIsNone(e["published_ts"])
        self.assertEqual(e["discuss_url"], "")

    def test_skip_wrong_currency_ranges_unavailable_and_external_store(self):
        offers = [schema(currency="EUR"), schema(price="100 - 150"),
                  schema(availability="https://schema.org/OutOfStock"),
                  schema(availability="https://schema.org/PreOrder"),
                  {**schema(), "url": "https://www.amazon.com/dp/B0ABCDE123"}]
        self.assertEqual(D.parse_catalog(markup(offers), BASE), [])

    def test_conflicting_variant_prices_not_collapsed_to_lowest(self):
        self.assertEqual(D.parse_catalog(markup([schema(price="100"), schema(price="120")]), BASE), [])

    def test_no_installment_or_aggregate_low_price(self):
        product = schema()
        product["offers"] = {"@type": "AggregateOffer", "lowPrice": "10", "priceCurrency": "USD"}
        self.assertEqual(D.parse_catalog(markup(product), BASE), [])
        self.assertIsNone(D.money("$10/mo"))
        self.assertIsNone(D.money("NaN"))

    def test_amazon_card_price_does_not_include_shipping(self):
        html = '''<div data-component-type="s-search-result" data-asin="B0ABCDE123">
          <h2>Google Pixel 10 Pro 256GB Unlocked Phone</h2>
          <span class="a-price"><span class="a-offscreen">$599.99</span></span>
          <span class="a-price a-text-price"><span class="a-offscreen">$999.99</span></span>
          <span>$20 delivery</span></div>'''
        e = D.parse_catalog(html, "https://www.amazon.com/s?k=google+pixel")[0]
        self.assertEqual(e["store_price"], 599.99)
        self.assertEqual(e["store_original"], 999.99)
        self.assertEqual(e["url"], "https://www.amazon.com/dp/B0ABCDE123")

    def test_newegg_price_integer_and_cents(self):
        html = '''<div class="item-cell"><a class="item-title" href="https://www.newegg.com/p/N82E1681">MSI RTX GPU</a>
          <li class="price-current"><strong>299</strong><sup>.99</sup> $25/mo</li>
          <span class="price-was-data">$399.99</span></div>'''
        e = D.parse_catalog(html, "https://www.newegg.com/todays-deals")[0]
        self.assertEqual((e["store_price"], e["store_original"]), (299.99, 399.99))

    def test_corsair_native_stock_and_regular_price(self):
        prices = {"final_price": {"value": 100, "currency": "USD"},
                  "regular_price": {"value": 160, "currency": "USD"}}
        item = {"name": "VENGEANCE 32GB DDR5 RAM", "sku": "SKU123", "stock_status": "IN_STOCK",
                "price_range": {"minimum_price": prices, "maximum_price": prices}}
        def page():
            data = {"props": {"pageProps": {"categoryData": {"products": {"items": [item]}}}}}
            return markup(schema()) + '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        e = D.parse_catalog(page(), BASE)[0]
        self.assertEqual(e["store_original"], 160)
        item["stock_status"] = "OUT_OF_STOCK"
        self.assertEqual(D.parse_catalog(page(), BASE), [])

    def test_google_discovery_includes_phones_only(self):
        html = ''.join('<a href="/us/product/' + x + '?hl=en-US">x</a>' for x in
                       ['pixel_11', 'pixel_11_pro', 'pixel_10a', 'pixel_watch_5', 'pixel_buds_2'])
        links = D.product_links(html, "https://store.google.com/us/collection/offers?hl=en-US")
        self.assertEqual(len(links), 3)
        self.assertFalse(any('watch' in u or 'buds' in u for u in links))


class DirectFetching(unittest.TestCase):
    def setUp(self):
        self.source = S.Source("test-store", "Corsair", BASE, type="store", trust="merchant", allow_undated=True)

    def test_throttle_success_and_failure_without_network(self):
        session = mock.Mock()
        for state in ({"last_attempt": time.time() - 60},
                      {"last_attempt": time.time() - 1200, "errors": 2}):
            r = S.fetch_store(self.source, state, session=session)
            self.assertTrue(r.not_modified)
        session.get.assert_not_called()

    def test_403_is_error_not_empty_success(self):
        session = mock.Mock()
        session.get.return_value.status_code = 403
        r = S.fetch_store(self.source, session=session)
        self.assertIn("403", r.error)
        self.assertEqual(r.entries, [])

    def test_200_js_shell_is_reported_as_unparsed(self):
        session = mock.Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.url = BASE
        session.get.return_value.text = '<html><body>Loading...</body></html>'
        r = S.fetch_store(self.source, session=session)
        self.assertTrue(r.error)

    def test_native_price_enters_independent_history_without_title_price(self):
        e = D.parse_catalog(markup(schema()), BASE)[0]
        d = bot.make_deal(e, self.source, 10000)
        self.assertEqual(d["price"], 100)
        self.assertEqual(d["cat"], "ram")
        self.assertEqual(d["published_ts"], 10000)
        self.assertTrue(bot.hist_key(d).startswith("store:"))
        ok, info = bot.assess(d, {"n": 0, "median": None, "min": None}, bot.get_cfg(), 10000)
        self.assertFalse(ok)  # an ordinary catalog listing is not automatically a deal

    def test_same_sku_collects_spaced_history_and_detects_price_drop(self):
        e = D.parse_catalog(markup(schema(price="200")), BASE)[0]
        start = 1_800_000_000
        d = bot.make_deal(e, self.source, start)
        samples = []
        for dt in (0, 900, 43201, 86402):
            bot.add_sample(samples, d, start + dt)
        self.assertEqual(len(samples), 3)
        e["store_price"] = 100
        drop = bot.make_deal(e, self.source, start + 87000)
        hist = {bot.hist_key(drop): samples}
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        send = mock.Mock()
        bot.process([drop], state, hist, start + 87000, bot.get_cfg(), send)
        send.assert_called_once()
        self.assertIn("медианы", send.call_args.args[0])


class PrioritiesAndCategories(unittest.TestCase):
    def test_pixel_and_peripheral_categories(self):
        for title in ["Google Pixel 10 Pro 256GB Unlocked $599", "Pixel 9a 128GB Smartphone"]:
            self.assertEqual(bot.classify(title), "pixel")
        for title in ["Case for Google Pixel 10", "Google Pixel 10 screen protector", "Pixel Watch 5"]:
            self.assertNotEqual(bot.classify(title), "pixel")
        for title, category in [("Elgato Wave 3 USB Microphone", "microphone"),
                                ("Corsair TITAN 360 Liquid CPU Cooler", "cooler"),
                                ("Corsair K70 Mechanical Keyboard", "keyboard"),
                                ("Corsair M65 Mouse", "mouse"), ("Corsair HS80 Headset", "headset")]:
            self.assertEqual(bot.classify(title), category)

    def test_conditional_pixel_price_not_published(self):
        source = S.Source("sd", "Slickdeals", "https://slickdeals.net", trust="curated")
        e = {"title": "Google Pixel 10 $99 with trade-in", "url": "https://www.amazon.com/dp/B0ABCDE123",
             "discuss_url": "", "item_id": "pixel", "expired": False, "published_ts": 1000}
        d = bot.make_deal(e, source, 1001)
        self.assertFalse(bot.assess(d, {"n": 0, "median": None}, bot.get_cfg(), 1001)[0])

    def test_new_sources_are_enabled_without_editing_variables(self):
        active = {s.id for s in S.active_sources({"ENABLE_SOURCES": "techbargains,bensbargains"})}
        self.assertTrue({"corsair-mice", "msi-direct", "amazon-pixel", "sd-q-google-pixel"} <= active)

    @mock.patch("store_links.resolve_store_url", side_effect=lambda url, *a, **kw: url)
    def test_components_publish_before_newer_laptop(self, _):
        now = 1_800_000_000
        source = S.Source("direct", "Corsair", BASE, type="store", trust="merchant", allow_undated=True)
        def deal(name, url, ts):
            e = D.entry(name, url, 100, 250)
            e["published_ts"] = ts
            return bot.make_deal(e, source, now)
        laptop = deal("Gaming Laptop Ryzen 7", URL + "-laptop", now)
        ram = deal("Corsair DDR5 RAM", URL, now - 600)
        state = {"processed": {}, "posted": {}, "pending": {}, "sources": {}, "review": []}
        config = bot.get_cfg(); config["max_posts"] = 1
        send = mock.Mock()
        bot.process([laptop, ram], state, {}, now, config, send)
        self.assertIn("DDR5", send.call_args.args[0])
        self.assertEqual(len(state["pending"]), 1)


if __name__ == "__main__":
    unittest.main()
