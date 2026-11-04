import copy
import time
import unittest
from unittest.mock import Mock

import market_prices as M
import market_search as D
import direct_stores
from test_market_prices import offer, NOW

MPN = 'CMK32GX5M2B6000C30'


def hardware(domain, price):
    d = offer(domain, price, title='Corsair 32GB DDR5 Memory', brand='Corsair', mpn=MPN)
    if domain == 'corsair.com':
        d['url'] = 'https://www.corsair.com/us/en/p/memory/' + MPN
    return d


class Discovery(unittest.TestCase):
    def test_manufacturer_sku_can_supply_mpn_but_retailer_sku_cannot(self):
        product = {'brand': {'name': 'Corsair'}, 'sku': MPN}
        self.assertEqual(direct_stores.offer_meta(product, {}, 'https://www.corsair.com/us/en/p/x')['mpn'], MPN)
        self.assertEqual(direct_stores.offer_meta(product, {}, 'https://www.bestbuy.com/site/x/123.p')['mpn'], '')
    def test_only_exact_part_product_links(self):
        spec = D.terms(hardware('amazon.com', 100))
        body = f'''<a href="/us/en/p/memory/{MPN}">Memory</a>
                   <a href="/us/en/p/memory/WRONG">Other memory</a>
                   <a href="/us/en/p/memory/{MPN}W">White memory</a>
                   <a href="/us/en/search?q={MPN}">{MPN}</a>
                   <a href="https://evil.example/{MPN}">{MPN}</a>'''
        urls = D.links_from_html(body, 'https://www.corsair.com/us/en/search', 'corsair.com', spec)
        self.assertEqual(urls, ['https://www.corsair.com/us/en/p/memory/' + MPN])

    def test_neighboring_skus_not_accepted_from_whole_page(self):
        spec = D.terms(hardware('amazon.com', 100))
        body = f'<main>{"x" * 2500}<a href="/us/en/p/memory/WRONG">Other memory</a>{MPN}</main>'
        self.assertEqual(D.links_from_html(body, 'https://www.corsair.com/', 'corsair.com', spec), [])

    def test_search_prices_never_returned_as_quotes(self):
        session = Mock()
        response = Mock(status_code=200, encoding='utf-8')
        response.iter_content.return_value = [f'<a href="/us/en/p/memory/{MPN}">{MPN} $9999 MSRP</a>'.encode()]
        session.get.return_value.__enter__ = Mock(return_value=response)
        session.get.return_value.__exit__ = Mock(return_value=False)
        result = D.discover(hardware('amazon.com', 100), limit=1, session=session)
        self.assertEqual(result['requests'], 1)
        self.assertEqual(result['urls'], ['https://www.corsair.com/us/en/p/memory/' + MPN])
        self.assertNotIn('price', result)
        self.assertIn(MPN, session.get.call_args.args[0])

    def test_blocked_search_is_explicit_and_bounded(self):
        session = Mock()
        response = Mock(status_code=403)
        session.get.return_value.__enter__ = Mock(return_value=response)
        session.get.return_value.__exit__ = Mock(return_value=False)
        result = D.discover(hardware('corsair.com', 100), limit=2, session=session)
        self.assertEqual(session.get.call_count, 2)
        self.assertFalse(result['urls'])
        self.assertTrue(all('HTTP 403' in note for note in result['notes']))

    def test_missing_identifier_never_guessed(self):
        d = hardware('corsair.com', 100)
        d['product_meta'] = {}
        self.assertIsNone(D.terms(d))
        session = Mock()
        self.assertEqual(D.discover(d, session=session)['requests'], 0)
        session.get.assert_not_called()

    def test_pixel_full_variant_search_without_offer_price(self):
        d = offer('amazon.com', 500)
        d['title'] += ' $500'
        spec = D.terms(d)
        self.assertNotIn('$500', spec['query'])
        self.assertTrue(D.matching('Google Pixel 10 Pro 256GB Obsidian Unlocked', spec))
        self.assertFalse(D.matching('Google Pixel 10 Pro 128GB Obsidian Unlocked', spec))


class EngineDiscovery(unittest.TestCase):
    def test_first_seen_hardware_discovers_competitors_and_matches_mpn_alias(self):
        current = hardware('corsair.com', 100)
        refs = [hardware('amazon.com', 180), hardware('newegg.com', 200)]
        # A retailer may also expose a GTIN. It still matches the exact manufacturer MPN.
        refs[0]['product_meta']['gtins'] = ['012345678905']
        verified = {M.canonical_url(d['url']): d for d in [current, *refs]}
        search = Mock(return_value={'urls': list(verified)[1:], 'notes': ['found exact parts']})
        engine = M.MarketPrices({}, NOW, verifier=verified.get, discoverer=search)
        engine.add(current)
        result, reason = engine.compare(current, 25, 20)
        self.assertIsNotNone(result, reason)
        self.assertEqual(result['baseline'], 180)
        self.assertEqual(result['saved'], 80)
        self.assertEqual(len(result['references']), 2)

    def test_wrong_part_or_unverified_card_never_supplies_price(self):
        current = hardware('corsair.com', 100)
        wrong = hardware('amazon.com', 200)
        wrong['product_meta']['mpn'] = 'CMK64GX5M2B6000C30'
        search = Mock(return_value={'urls': [wrong['url']], 'notes': ['found one link']})
        engine = M.MarketPrices({}, NOW, verifier=lambda url: wrong, discoverer=search)
        engine.add(current)
        self.assertIsNone(engine.compare(current, 25, 20)[0])

    def test_search_results_cooldown_quota_and_persistence(self):
        state = {}
        search = Mock(return_value={'urls': [], 'notes': ['newegg.com: HTTP 403']})
        engine = M.MarketPrices(state, NOW, discoverer=search)
        current = hardware('corsair.com', 100)
        engine.add(current)
        self.assertIn('HTTP 403', engine.compare(current, 25, 20)[1])
        engine.compare(current, 25, 20)
        self.assertEqual(search.call_count, 1)
        later = M.MarketPrices(state, NOW + 100, discoverer=search)
        later.compare(current, 25, 20)
        self.assertEqual(search.call_count, 1)
        ready = M.MarketPrices(state, NOW + 1801, discoverer=search)
        ready.compare(current, 25, 20)
        self.assertEqual(search.call_count, 2)

    def test_pixel_variant_conflict_even_with_identical_gtin(self):
        current = offer('store.google.com', 500)
        refs = [offer('amazon.com', 800), offer('newegg.com', 800)]
        for d in [current, *refs]:
            d['product_meta']['gtins'] = ['012345678905']
        refs[0]['title'] = 'Google Pixel 10 Pro XL 256GB Obsidian Unlocked'
        engine = M.MarketPrices({}, NOW)
        for d in [current, *refs]: engine.add(d)
        self.assertIsNone(engine.compare(current, 15, 50)[0])

    def test_discovery_failure_is_diagnostic_not_process_crash(self):
        d = hardware('corsair.com', 100)
        engine = M.MarketPrices({}, NOW, discoverer=Mock(side_effect=RuntimeError('bad response')))
        engine.add(d)
        result, reason = engine.compare(d, 25, 20)
        self.assertIsNone(result)
        self.assertIn('RuntimeError', reason)

    def test_feed_without_mpn_can_learn_it_only_from_verified_store_card(self):
        d = hardware('corsair.com', 100)
        feed = copy.deepcopy(d)
        feed['product_meta'] = {}
        feed['price_origin'] = 'feed'
        search = Mock(return_value={'urls': [], 'notes': []})
        engine = M.MarketPrices({}, NOW, verifier=lambda url: d, discoverer=search)
        engine.compare(feed, 25, 20)
        self.assertEqual(M.identity(feed), M.identity(d))
        search.assert_called_once()

    def test_global_discovery_quota_does_not_expand_per_product(self):
        search = Mock(return_value={'urls': [], 'notes': []})
        engine = M.MarketPrices({}, NOW, discoverer=search, max_search_requests=4)
        first = hardware('corsair.com', 100)
        second = hardware('corsair.com', 120)
        second['product_meta']['mpn'] = 'CMK64GX5M2B6000C30'
        for d in (first, second):
            engine.add(d)
            engine.compare(d, 25, 20)
        search.assert_called_once()

    def test_expired_search_deadline_never_calls_network(self):
        d = hardware('corsair.com', 100)
        session = Mock()
        result = D.discover(d, deadline=time.monotonic() - 1, session=session)
        self.assertEqual(result['requests'], 0)
        session.get.assert_not_called()
