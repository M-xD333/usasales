from unittest.mock import Mock
import bot
import market_prices as M
from test_bot import cfg,NOW
from test_market_prices import offer


def candidate(trust='curated',score=None):
    d=offer('bestbuy.com',500)
    d.update(price_origin='feed',trust=trust,flags=['free_shipping'],original=800,community_score=score)
    return d


def run(d,verifier=lambda _:None):
    e=M.MarketPrices({},NOW,verifier=verifier);send=Mock()
    stats,*_=bot.process([d],{'posted':{},'pending':{},'processed':{},'review':[]},{},NOW,cfg(),send,market=e)
    return stats,send


def test_curated_403_unavailable_does_not_require_comparison():
    stats,send=run(candidate())
    assert stats['опубликовано']==1
    assert 'Цена из агрегатора, не проверена на странице Best Buy' in send.call_args.args[0]
    assert 'оценка по категории' in send.call_args.args[0]


def test_community_real_votes_and_explicit_contradiction_remain_required():
    assert run(candidate('community',100))[1].call_count==1
    assert run(candidate('community',None))[1].call_count==0
    assert run(candidate('community',0))[1].call_count==0
    assert run(candidate(),lambda _:offer('bestbuy.com',700))[1].call_count==0
    assert run(candidate('unvetted'))[1].call_count==0


def test_roundup_secondary_bestbuy_link_is_not_primary_offer():
    d=candidate();d.update(url='https://slickdeals.net/f/12345',store='Amazon',store_candidates=['https://www.bestbuy.com/product/widget/123.p'])
    assert M.aggregator_store_url(d) is None
    d['store']='Best Buy';assert M.aggregator_store_url(d)=='https://www.bestbuy.com/product/widget/123.p'
