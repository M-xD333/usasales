import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import bot
import checkpoints as C
import publication_history as P
import state_merge
from test_regressions import approved, state, cfg, NOW
from test_market_search import hardware


class DailyDuplicateRegression(unittest.TestCase):
    def run_offer(self, st, price, day=0, **kwargs):
        d=approved(f'SSD ${price} (was $200)')
        d['fetched_ts']=d['published_ts']=NOW+day*86400
        d.update(kwargs);send=Mock()
        bot.process([d],st,{},NOW+day*86400,cfg(),send)
        return send,d

    def test_unchanged_deal_not_republished_on_following_days(self):
        st=state()
        self.assertEqual(self.run_offer(st,80)[0].call_count,1)
        for day in [1,2,3,7,30]:
            bot.trim_state(st,NOW+day*86400,cfg())
            self.assertEqual(self.run_offer(st,80,day)[0].call_count,0)

    def test_lower_price_publishes_but_higher_or_rebound_does_not(self):
        st=state()
        for day,price,count in [(0,80,1),(1,90,0),(2,80,0),(3,70,1),(4,80,0),(5,70,0),(6,69.99,1)]:
            self.assertEqual(self.run_offer(st,price,day)[0].call_count,count)

    def test_new_source_title_id_and_tracking_tags_do_not_reopen_product(self):
        st=state();self.run_offer(st,80)
        send,_=self.run_offer(st,80,2,source_id='other-feed',id='new-id',ident='n:renamed-product',
            name='SSD renamed',url='https://www.amazon.com/dp/B012345678?tag=affiliate&utm_source=feed')
        send.assert_not_called()

    def test_second_item_in_same_batch_is_suppressed_after_resolution(self):
        first=approved();second=approved();second.update(id='other-id',ident='n:other-title',source_id='other-feed')
        first['url']='https://slickdeals.net/f/first';second['url']='https://slickdeals.net/f/second'
        send=Mock()
        with patch('store_links.resolve_store_url',return_value='https://www.amazon.com/dp/B012345678'):
            bot.process([first,second],state(),{},NOW,cfg(),send)
        self.assertEqual(send.call_count,1)

    def test_legacy_history_migrates_without_allowing_higher_price(self):
        st=state();old=approved();st['posted'][bot.dup_key(old)]=NOW-4*86400
        self.run_offer(st,90)[0].assert_not_called()
        self.assertTrue(st['publication_history'])
        self.assertEqual(min(r['lowest_cents'] for r in st['publication_history'].values()),8000)
        self.run_offer(st,70,1)[0].assert_called_once()

    def test_legacy_asin_survives_feed_title_identity_change(self):
        st=state();st['posted']['asin:B012345678|New|80.0']=NOW-3*86400
        self.run_offer(st,80,ident='n:changed-title')[0].assert_not_called()

    def test_different_condition_and_variant_are_not_collapsed(self):
        st=state();d=hardware('corsair.com',80)
        P.remember(st,d,NOW)
        other=copy.deepcopy(d);other['product_meta']['mpn']='DIFFERENT-SKU'
        self.assertIsNone(P.seen(st,other,'unused'))
        other=copy.deepcopy(d);other['cond']='Refurbished'
        self.assertIsNone(P.seen(st,other,'unused'))

    def test_delivery_failure_does_not_create_permanent_block(self):
        st=state()
        bot.process([approved()],st,{},NOW,cfg(),Mock(side_effect=bot.TelegramError('offline')))
        self.assertFalse(st.get('publication_history'))
        send=Mock();bot.process([],st,{},NOW+300,cfg(),send)
        send.assert_called_once()

    def test_restart_preserves_floor(self):
        st=state();self.run_offer(st,80)
        with tempfile.TemporaryDirectory() as root:
            C.Store(root).save(st,{})
            restored,_=C.Store(root).load()
            self.run_offer(restored,80,7)[0].assert_not_called()

    def test_git_merge_preserves_lowest_price_from_both_writers(self):
        a,b=state(),state();d=approved()
        P.remember(a,d,NOW);d['price']=70;P.remember(b,d,NOW+1)
        merged=state_merge.merge_state(b,a)
        d['price']=75
        self.assertEqual(P.seen(merged,d,'unused')['lowest_cents'],7000)

    def test_validation_rejects_broken_ledger(self):
        with self.assertRaises(C.CheckpointError):C.validate_state({'publication_history':{'bad':{'lowest_cents':None}}})


class CanonicalProductLinks(unittest.TestCase):
    def test_amazon_asin_same_across_link_formats(self):
        st=state();d=approved();P.remember(st,d,NOW)
        d.update(url='https://www.amazon.com/gp/product/B012345678?tag=new',ident='n:changed-title')
        self.assertIsNotNone(P.seen(st,d,'unused'))

    def test_newegg_item_same_across_link_formats(self):
        st=state();d=approved();d['url']='https://www.newegg.com/p/N82E16800000001'
        P.remember(st,d,NOW)
        d['url']='https://www.newegg.com/Product/Product.aspx?Item=N82E16800000001'
        self.assertIsNotNone(P.seen(st,d,'unused'))
