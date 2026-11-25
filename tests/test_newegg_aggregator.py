import copy
from unittest.mock import Mock, patch
import bot
import market_prices as M
from test_bot import NOW, cfg
from test_market_prices import offer


def candidate(trust='unvetted'):
    d = offer('newegg.com', 500)
    d.update(price_origin='feed', trust=trust, flags=['free_shipping'], original=800)
    return d


def engine(refs=2):
    e = M.MarketPrices({}, NOW, verifier=lambda _: None)
    for domain in ['amazon.com', 'bestbuy.com'][:refs]:
        e.add(offer(domain, 800))
    return e


def test_comparison_does_not_need_newegg_page():
    e = engine()
    result, reason = e.compare(candidate(), 15, 50)
    assert result, reason
    assert result['candidate_unverified']
    assert result['baseline'] == 800
    assert not any(q['shop'] == 'newegg.com' for q in e.state['quotes'].values())


def test_one_other_shop_is_insufficient():
    assert engine(1).compare(candidate(), 15, 50)[0] is None


def test_explicit_store_contradiction_prevents_fallback():
    e = engine()
    e.verifier = lambda _: offer('newegg.com', 700)
    assert e.compare(candidate(), 15, 50)[0] is None


def test_unknown_shipping_and_variant_are_not_invented():
    d = candidate(); d['flags'] = []; d['product_meta']['shipping'] = None
    assert engine().compare(d, 15, 50)[0] is None
    d = candidate(); d['title'] = 'Google Pixel 10 Pro 128GB Obsidian Unlocked'
    assert engine().compare(d, 15, 50)[0] is None


def publish(d, e):
    state = {'processed': {}, 'posted': {}, 'pending': {}, 'review': []}
    sender = Mock()
    with patch('store_links.resolve_store_url', return_value=d['url']):
        stats, *_ = bot.process([d], state, {}, NOW, cfg(), sender, market=e)
    return stats, sender


def test_curated_unreadable_card_is_attributed():
    stats, sender = publish(candidate('curated'), engine(0))
    assert stats['опубликовано'] == 1
    assert 'Цена из агрегатора, не проверена на странице Newegg' in sender.call_args.args[0]


def test_unvetted_requires_comparison_even_with_claimed_discount():
    stats, sender = publish(candidate(), engine(0))
    assert stats['опубликовано'] == 0
    sender.assert_not_called()


def test_community_votes_still_required_and_extreme_curated_allowed():
    d = candidate('community'); d['community_score'] = 0
    assert publish(d, engine(2))[0]['опубликовано'] == 0
    d = candidate('curated'); d['price'] = 50
    assert publish(d, engine(0))[0]['опубликовано'] == 1


def test_roundup_newegg_link_does_not_change_store():
    d = candidate(); d.update(url='https://www.tomshardware.com/news/example', store='Amazon',
                            store_candidates=['https://www.newegg.com/p/N82E16800000001'])
    assert M.newegg_aggregator_url(d) is None


def test_probed_newegg_feed_is_unvetted_and_enabled():
    import sources
    source = next(s for s in sources.active_sources({}) if s.id == 'sd-q-newegg')
    assert source.trust == 'unvetted'
    assert source.url == sources._sd_search('newegg').url


def test_aggregator_secondary_link_does_not_exclude_amazon_reference():
    d = candidate()
    d.update(url='https://slickdeals.net/f/123-newegg-deal', store='Newegg',
             store_candidates=['https://www.amazon.com/dp/B012345678',
                               'https://www.newegg.com/p/N82E16800000001'])
    result, reason = engine().compare(d, 15, 50)
    assert result, reason
    assert {q['shop'] for q in result['references']} == {'amazon.com', 'bestbuy.com'}


def test_newegg_identity_not_borrowed_from_secondary_accessory():
    d = candidate()
    d.update(title='DDR5 RAM at Newegg', cat='ram', product_meta={},
             url='https://slickdeals.net/f/123-deal', store='Newegg',
             store_candidates=['https://www.amazon.com/dp/B012345678',
                               'https://www.newegg.com/p/N82E16800000001'])
    e = engine(); calls = []
    def verifier(url):
        calls.append(url)
        return offer('amazon.com', 500) if 'amazon.com' in url else None
    e.verifier = verifier
    e.discoverer = Mock()
    assert e.compare(d, 15, 50)[0] is None
    assert not any('amazon.com' in url for url in calls)
    assert d['product_meta'] == {}
