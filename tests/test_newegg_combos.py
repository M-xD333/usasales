from urllib.parse import quote
from unittest.mock import Mock
import pytest
import bot
import sources
import store_links as L
import publication_history as P
from test_bot import NOW, cfg, entry
from test_newegg_aggregator import engine, publish

COMBO = 'https://www.newegg.com/Product/ComboDealDetails?ItemList=Combo.4884345'
WRAPPED = 'https://click.linksynergy.com/deeplink?id=test&mid=44583&murl=' + quote(COMBO, safe='')


def deal(trust='curated'):
    raw = entry('ZOTAC RTX 5070 Ti + Ryzen Bundle $1639.98 at Newegg', url='https://slickdeals.net/f/123')
    raw['store_candidates'] = [L.unwrap(WRAPPED)]
    return bot.make_deal(raw, sources.Source('test', 'Test', 'https://slickdeals.net/rss', trust=trust), NOW)


def test_real_competitor_combo_links_unwrap_without_newegg_request():
    for combo in [COMBO, COMBO.replace('4884345', '4884342')]:
        wrapped = WRAPPED.replace(quote(COMBO, safe=''), quote(combo, safe=''))
        assert L.unwrap(wrapped) == combo
        assert L.resolve_store_url('https://slickdeals.net/f/123', [wrapped], budget=0) == combo


@pytest.mark.parametrize('url', [COMBO.replace('Combo.4884345', 'anything'),
    COMBO + '&ItemList=Combo.4884342', COMBO.replace('newegg.com', 'newegg.com.evil.test'),
    'https://www.newegg.com/tools/combo-builder/1740', 'https://www.newegg.com/Product/ComboDealDetails'])
def test_generic_or_ambiguous_combo_routes_rejected(url):
    assert L.merchant_url(url) is None


def test_combo_price_is_for_entire_kit_and_curated_can_publish_without_page():
    d = deal(); assert d['cat'] == 'combo'; assert d['price'] == 1639.98
    assert L.newegg_combo_id(d['store_candidates'][0]) == '4884345'
    stats, sender = publish(d, engine(0))
    assert stats['опубликовано'] == 1
    text = sender.call_args.args[0]
    assert 'Цена комплекта' in text and 'Цена из агрегатора' in text
    assert sender.call_args.kwargs['store_url'] == COMBO


def test_unvetted_combo_not_compared_as_single_gpu():
    d = deal('unvetted')
    d['product_meta'] = {'brand': 'Zotac', 'mpn': 'example-gpu'}
    assert engine().compare(d, 0, 0)[0] is None
    assert publish(d, engine())[0]['опубликовано'] == 0


def test_same_combo_tracking_parameters_never_repost():
    d = deal(); d['url'] = COMBO
    s = {}; P.remember(s, d, NOW)
    d.update(id='another-feed', ident='renamed-kit', url=COMBO+'&nm_mc=affiliate&cm_mmc=test&ranMID=44583&u1=other')
    assert P.seen(s, d, bot.dup_key(d))
    d['url'] = COMBO.replace('4884345', '4884342')
    assert not P.seen(s, d, bot.dup_key(d))


def test_combo_revealed_by_opaque_redirect_is_not_advertised_as_one_gpu():
    from unittest.mock import patch
    d = deal(); d.update(cat='gpu', tag='GPU', store_candidates=['https://slickdeals.net/click?u3=opaque'])
    state = {'processed': {}, 'posted': {}, 'pending': {}, 'review': []}
    sender = Mock()
    with patch('store_links.resolve_store_url', return_value=COMBO):
        stats, *_ = bot.process([d], state, {}, NOW, cfg(), sender, market=engine(0))
    assert stats['опубликовано'] == 1
    assert 'Цена комплекта' in sender.call_args.args[0]
    assert '[COMBO]' in sender.call_args.args[0]
    assert 'Цена из агрегатора' in sender.call_args.args[0]


def test_new_combo_feed_is_unvetted():
    assert sources._sd_search('newegg combo') in sources.active_sources({})


def test_two_distinct_kits_with_same_component_mpn_remain_distinct():
    d = deal(); d['url'] = COMBO
    d['product_meta'] = {'brand': 'Zotac', 'mpn': 'shared-gpu'}
    s = {}; P.remember(s, d, NOW)
    other = dict(d, url=COMBO.replace('4884345','4884342'), ident='newegg-combo:4884342')
    assert not P.seen(s, other, bot.dup_key(other))


def test_community_combo_needs_real_votes():
    d = deal('community'); d['community_score'] = cfg()['min_score'] * 2 - 1
    assert publish(d, engine(0))[0]['опубликовано'] == 0
    d['community_score'] += 1
    assert publish(d, engine(0))[0]['опубликовано'] == 1


def test_curated_code_combo_can_be_published_with_unverified_code_notice():
    raw = entry('Ryzen CPU + DDR5 Bundle $500 with code NEGG2026 at Newegg', url='https://slickdeals.net/f/123')
    raw['store_candidates'] = [COMBO]
    d = bot.make_deal(raw, sources.Source('test','Test','https://slickdeals.net/rss',trust='curated'), NOW)
    stats, sender = publish(d, engine(0))
    assert stats['опубликовано'] == 1
    assert 'NEGG2026' in sender.call_args.args[0]
    assert 'проверьте его применение' in sender.call_args.args[0]
    d['code'] = None
    assert publish(d, engine(0))[0]['опубликовано'] == 0


def test_curated_coupon_combo_requires_exact_unambiguous_arithmetic():
    raw = entry('Ryzen CPU + DDR5 Bundle $500 with $20 coupon at Newegg', url='https://slickdeals.net/f/123')
    raw['store_candidates'] = [COMBO]
    d = bot.make_deal(raw, sources.Source('test','Test','https://slickdeals.net/rss',trust='curated'), NOW)
    assert d['price'] == 480
    stats, sender = publish(d, engine(0))
    assert stats['опубликовано'] == 1
    assert '$480' in sender.call_args.args[0]
    d['price_components']['coupon_amount'] = None
    assert publish(d, engine(0))[0]['опубликовано'] == 0


def test_redirect_reclassification_does_not_pollute_single_component_history():
    from unittest.mock import patch
    d = deal(); d.update(cat='gpu', ident='n:gpu-only', store_candidates=['https://slickdeals.net/click?u3=opaque'])
    state = {'processed': {}, 'posted': {}, 'pending': {}, 'review': []}
    history = {}; sender = Mock()
    with patch('store_links.resolve_store_url', return_value=COMBO):
        stats, *_ = bot.process([d], state, history, NOW, cfg(), sender, market=engine(0))
    assert stats['опубликовано'] == 1
    assert not history['n:gpu-only|New']
    assert history['newegg-combo:4884345|New'][0][1] == d['price']
