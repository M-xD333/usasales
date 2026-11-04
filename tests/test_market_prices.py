import copy
import json
import unittest
from unittest.mock import Mock, patch

import bot
import direct_stores
import market_prices as M
import sources
from test_bot import NOW, cfg


def offer(domain, price, title='Google Pixel 10 Pro 256GB Obsidian Unlocked', **meta):
    path = '/p/N82E16800000001' if domain == 'newegg.com' else '/dp/B012345678' if domain == 'amazon.com' else '/product/pixel-10-pro'
    url = 'https://' + domain + path
    raw = dict(item_id=url, title=title, url=url, discuss_url='', summary='',
               published_ts=NOW, expired=False, store_price=price, store_currency='USD',
               product_meta=dict(currency='USD', market='US', condition='New', in_stock=True,
                                 shipping=0, seller=M.RETAIL_SELLERS.get(domain, {domain}).copy().pop(), **meta))
    return bot.make_deal(raw, sources.Source(domain, domain, url, type='store', trust='merchant'), NOW)


class Comparisons(unittest.TestCase):
    def setUp(self):
        self.current = offer('store.google.com', 500)
        self.refs = [offer('bestbuy.com', 750), offer('newegg.com', 800)]
        self.engine = M.MarketPrices({}, NOW)

    def compare(self):
        for d in [self.current, *self.refs]:
            self.engine.add(d)
        return self.engine.compare(self.current, 15, 50)[0]

    def test_first_observation_and_message(self):
        result = self.compare()
        self.assertEqual(result['baseline'], 750)
        self.assertEqual(result['saved'], 250)
        self.assertAlmostEqual(result['pct'], 100 / 3)
        self.assertEqual(len(result['references']), 2)
        state = bot.default_state() if hasattr(bot, 'default_state') else {'processed': {}, 'posted': {}, 'pending': {}, 'review': []}
        sent = Mock()
        with patch('store_links.resolve_store_url', return_value=self.current['url']):
            stats, _, _, _ = bot.process([self.current], state, {}, NOW, cfg(), sent, market=self.engine)
        self.assertEqual(stats['опубликовано'], 1)
        text = sent.call_args.args[0]
        self.assertIn('Другие магазины: от $750', text)
        self.assertIn('bestbuy.com', text)
        self.assertNotIn('MSRP', text)
        self.assertEqual(sent.call_args.kwargs['store_url'], self.current['url'])

    def test_variant_mismatch(self):
        for title in ('Google Pixel 10 Pro XL 256GB Obsidian Unlocked',
                      'Google Pixel 10 Pro 128GB Obsidian Unlocked',
                      'Google Pixel 10 Pro 256GB Jade Unlocked',
                      'Google Pixel 9 Pro 256GB Obsidian Unlocked',
                      'Google Pixel 10 Pro 256GB Obsidian Verizon',
                      'Google Pixel 10 Pro Unlocked',
                      'Google Pixel 10 Pro 256GB Obsidian Unlocked with trade-in'):
            with self.subTest(title=title):
                self.setUp()
                self.refs[0]['title'] = title
                self.assertIsNone(self.compare())

    def test_requires_two_independent_sellers(self):
        self.refs = [offer('newegg.com', 750), offer('newegg.com', 800)]
        self.assertIsNone(self.compare())

    def test_cheapest_competitor_controls_baseline(self):
        self.refs.append(offer('amazon.com', 550))
        self.assertIsNone(self.compare())

    def test_shipping_defeats_discount(self):
        self.current['product_meta']['shipping'] = 300
        self.assertIsNone(self.compare())

    def test_unknown_shipping(self):
        self.current['product_meta']['shipping'] = None
        self.assertIsNone(self.compare())

    def test_untrusted_or_incomplete_quotes(self):
        for field, value in [('in_stock', False), ('in_stock', None), ('currency', 'EUR'),
                             ('seller', 'Unknown Marketplace Seller'), ('condition', 'Used')]:
            with self.subTest(field=field):
                self.setUp()
                self.refs[0]['product_meta'][field] = value
                self.assertIsNone(self.compare())

    def test_feed_price_is_not_evidence(self):
        self.refs[0]['price_origin'] = 'feed'
        self.assertIsNone(self.compare())

    def test_stale_and_future(self):
        for delta in (-901, 1):
            self.setUp()
            self.refs[0]['fetched_ts'] = NOW + delta
            self.assertIsNone(self.compare())

    def test_strong_identifier_conflict(self):
        self.current['product_meta']['gtins'] = ['012345678905']
        self.refs[0]['product_meta']['gtins'] = ['036000291452']
        self.assertIsNone(self.compare())

    def test_current_price_changed(self):
        self.current['price_origin'] = 'feed'
        verified = copy.deepcopy(self.current)
        verified.update(price_origin='store', price=550)
        self.engine.verifier = lambda url: verified if url == self.current['url'] else None
        self.assertIsNone(self.compare())

    def test_verifier_failure_and_cooldown(self):
        fetch = Mock(side_effect=RuntimeError('HTTP 403'))
        self.engine.verifier = fetch
        self.engine.verify(self.current['url'])
        self.engine.verify(self.current['url'])
        self.assertEqual(fetch.call_count, 1)

    def test_budget(self):
        fetch = Mock(return_value=None)
        engine = M.MarketPrices({}, NOW, fetch, max_requests=1)
        for d in self.refs:
            engine.verify(d['url'])
        self.assertEqual(fetch.call_count, 1)

    def test_mpn_hardware_and_gtin_checksum(self):
        d = offer('corsair.com', 100, title='Corsair DDR5 Memory', brand='Corsair', mpn='CMK32GX5M2B6000C30')
        self.assertTrue(M.identity(d).startswith('mpn:corsair:'))
        d['product_meta'] = {}
        self.assertIsNone(M.identity(d))
        self.assertIsNone(M.valid_gtin('012345678906'))
        self.assertEqual(M.valid_gtin('012345678905'), '00012345678905')

    def test_pending_reevaluated_and_expired(self):
        state = {'processed': {}, 'posted': {}, 'pending': {}, 'review': []}
        bot.process([self.current], state, {}, NOW, cfg(), Mock(), market=self.engine)
        self.assertEqual(len(state['pending']), 1)
        bot.trim_state(state, NOW + 13 * 3600, cfg())
        self.assertFalse(state['pending'])

    def test_saved_cache_reused_then_expired(self):
        self.assertIsNotNone(self.compare())
        state = {'market_prices': self.engine.state}
        fresh = M.MarketPrices(state, NOW + 100)
        self.assertIsNotNone(fresh.compare(self.current, 15, 50)[0])
        expired = M.MarketPrices(state, NOW + 901)
        self.assertIsNone(expired.compare(self.current, 15, 50)[0])


class StructuredMetadata(unittest.TestCase):
    def test_product_offer(self):
        product = {'@type': 'Product', 'name': 'Corsair DDR5 memory', 'url': 'https://www.corsair.com/us/en/p/memory/test',
                   'mpn': 'CMK32GX5M2B6000C30', 'brand': {'name': 'Corsair'},
                   'offers': {'price': 100, 'priceCurrency': 'USD', 'availability': 'https://schema.org/InStock',
                              'shippingDetails': {'shippingDestination': {'addressCountry': 'US'},
                                                  'shippingRate': {'currency': 'USD', 'value': 0}}}}
        raw = direct_stores.parse_catalog('<script type="application/ld+json">' + json.dumps(product) + '</script>', product['url'])[0]
        self.assertEqual(raw['product_meta']['mpn'], product['mpn'])
        self.assertTrue(raw['product_meta']['in_stock'])
        self.assertEqual(raw['product_meta']['shipping'], 0)
        self.assertEqual(raw['product_meta']['condition'], 'New')
        self.assertIsNone(direct_stores.offer_meta({}, {}, 'https://bestbuy.com/product/x')['condition'])

    def test_conditional_shipping_unknown(self):
        offer = {'shippingDetails': {'shippingDestination': {'addressCountry': 'US'},
                                    'shippingRate': {'currency': 'USD', 'value': 0},
                                    'eligibleTransactionVolume': {'minValue': 1000}}}
        self.assertIsNone(direct_stores.offer_meta({}, offer, 'https://corsair.com/us/p/x')['shipping'])

    def test_product_fetch_exact_and_no_redirects(self):
        url = 'https://store.google.com/product/pixel-10-pro'
        product = {'@type': 'Product', 'name': 'Google Pixel 10 Pro 256GB Obsidian Unlocked',
                   'url': url, 'offers': {'price': 500, 'priceCurrency': 'USD',
                                         'availability': 'https://schema.org/InStock'}}
        response = Mock(status_code=200, encoding='utf-8')
        response.iter_content.return_value = [('<script type="application/ld+json">' + json.dumps(product) + '</script>').encode()]
        session = Mock()
        session.get.return_value.__enter__ = Mock(return_value=response)
        session.get.return_value.__exit__ = Mock(return_value=False)
        result = sources.fetch_market_product(url, session)
        self.assertEqual(result['store_price'], 500)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        response.status_code = 403
        self.assertIsNone(sources.fetch_market_product(url, session))


class Rechecks(unittest.TestCase):
    def test_remembered_urls_can_supply_first_history_discount(self):
        current = offer('store.google.com', 500)
        refs = [offer('bestbuy.com', 750), offer('newegg.com', 800)]
        state = {}
        old = M.MarketPrices(state, NOW - 1000)
        for d in refs:
            d['fetched_ts'] = NOW - 1000
            old.add(d)
        by_url = {M.canonical_url(d['url']): d for d in refs}
        engine = M.MarketPrices(state, NOW, verifier=by_url.get)
        engine.add(current)
        evidence, reason = engine.compare(current, 15, 50)
        self.assertIsNotNone(evidence, reason)
        self.assertEqual(evidence['baseline'], 750)

    def test_market_evidence_cannot_override_expired(self):
        current = offer('store.google.com', 500)
        current['expired'] = True
        ok, info = bot.assess(current, {'n': 0, 'median': None}, cfg(), NOW, {'baseline': 750})
        self.assertFalse(ok)
        self.assertTrue(info['final'])
