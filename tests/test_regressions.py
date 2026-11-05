"""Acceptance regressions from the November audit. No live services are called."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import bot
import checkpoints as C
import direct_stores as D
import market_prices as M
import merchant_http
import offer_queue as Q
import sources as S
import store_links as L
from test_bot import deal, cfg, NOW
from test_market_prices import offer
from test_market_search import hardware


def state():
    return {'processed': {}, 'posted': {}, 'pending': {}, 'review': [], 'sources': {}}


def approved(title='SSD $80 (was $160)', **kw):
    return deal(title, trust='curated', url='https://www.amazon.com/dp/B012345678', **kw)


class QueueRegression(unittest.TestCase):
    def test_fresh_400_replaces_pending_500_and_is_published(self):
        old = offer('store.google.com', 500)
        old.update(fetched_ts=NOW-300, published_ts=NOW-300)
        fresh = offer('store.google.com', 400)
        st = state(); st['pending'][Q.pkey(old)] = old
        market = M.MarketPrices({}, NOW)
        for d in [offer('bestbuy.com',750),offer('newegg.com',800)]: market.add(d)
        send = Mock()
        bot.process([fresh], st, {}, NOW, cfg(), send, market=market)
        send.assert_called_once()
        self.assertIn('Цена: <b>$400</b>', send.call_args.args[0])
        self.assertNotIn('$500', send.call_args.args[0])
        self.assertFalse(st['pending'])

    def test_old_quote_cannot_replace_new(self):
        market = M.MarketPrices({}, NOW)
        fresh = offer('store.google.com',400)
        old = offer('store.google.com',500); old['fetched_ts']=NOW-100
        market.add(fresh); market.add(old)
        self.assertEqual(next(iter(market.state['quotes'].values()))['price'],400)

    def test_duplicate_window_expires_despite_legacy_processed(self):
        d=approved(); st=state()
        st['processed'][Q.pkey(d)] = NOW-3*86400
        st['posted'][bot.dup_key(d)] = NOW-3*86400
        send=Mock();bot.process([d],st,{},NOW,cfg(),send)
        send.assert_called_once()

    def test_rise_then_drop_is_a_new_event_inside_duplicate_window(self):
        st=state();send=Mock()
        for dt,price in [(0,80),(120,150),(240,80)]:
            d=approved(f'SSD ${price} (was $160)');d['fetched_ts']=NOW+dt
            bot.process([d],st,{},NOW+dt,cfg(),send)
        self.assertEqual(send.call_count,2)

    def test_corrected_category_reassessed_at_same_price(self):
        st=state();send=Mock()
        first=approved('Unknown product $80');second=approved()
        bot.process([first],st,{},NOW,cfg(),send)
        second['fetched_ts']=NOW+1
        bot.process([second],st,{},NOW+1,cfg(),send)
        send.assert_called_once()
        record=next(iter(st['assessment_cache'].values()))
        self.assertEqual(record['rules_version'],bot.RULES_VERSION)
        self.assertEqual(record['parser_version'],bot.PARSER_VERSION)

    def test_temporary_delivery_survives_empty_next_feed(self):
        d=approved();st=state();st['pending'][Q.pkey(d)]=d
        bot.process([],st,{},NOW,cfg(),Mock(side_effect=bot.TelegramError('network')))
        self.assertEqual(len(st['pending']),1)
        q=next(iter(st['pending'].values()))['_queue']
        self.assertEqual(q['status'],'queued');self.assertEqual(q['attempts'],1)
        send=Mock();bot.process([],st,{},NOW+300,cfg(),send)
        send.assert_called_once();self.assertFalse(st['pending'])

    def test_entire_tail_saved_before_first_delivery_and_fatal_error(self):
        ds=[deal('SSD $80 (was $160)',trust='curated',url=f'https://www.amazon.com/dp/B01234567{i}') for i in range(3)]
        st=state();st['pending']={Q.pkey(d):d for d in ds};saves=[]
        bot.process([],st,{},NOW,cfg(),Mock(side_effect=bot.TelegramError('blocked',fatal=True)),
                    save_state=lambda s:saves.append(copy.deepcopy(s)))
        self.assertEqual(len(saves[0]['pending']),3)
        self.assertEqual(len(st['pending']),3)
        self.assertFalse(st['posted'])

    def test_sending_saved_before_request_and_recovered_after_crash(self):
        st=state();snapshots=[]
        with self.assertRaises(RuntimeError):
            bot.process([approved()],st,{},NOW,cfg(),Mock(side_effect=RuntimeError('killed')),
                        save_state=lambda s:snapshots.append(copy.deepcopy(s)))
        self.assertEqual(next(iter(snapshots[-1]['pending'].values()))['_queue']['status'],'sending')
        bot.process([],st,{},NOW+1,cfg(),Mock())
        self.assertEqual(next(iter(st['pending'].values()))['_queue']['status'],'delivery_unknown')

    def test_ambiguous_delivery_is_not_falsely_marked_posted(self):
        st=state()
        bot.process([approved()],st,{},NOW,cfg(),Mock(side_effect=bot.TelegramError('timeout',ambiguous=True)))
        self.assertFalse(st['posted'])
        self.assertEqual(next(iter(st['pending'].values()))['_queue']['status'],'delivery_unknown')

    def test_expired_observation_never_contaminates_baseline(self):
        hist={}
        bot.process([approved(expired=True,ts=NOW-30*86400)],state(),hist,NOW,cfg(),Mock())
        self.assertFalse(any(hist.values()))

    def test_primary_product_link_wins_over_secondary_link(self):
        a='https://www.amazon.com/dp/B012345678'; b='https://www.amazon.com/dp/B087654321'
        self.assertEqual(L.resolve_store_url(a,[b]),a)


class PriceRegression(unittest.TestCase):
    def test_coupon_arithmetic_and_already_final_price(self):
        for title,expected in [('SSD $100 with $20 coupon (was $150)',80),
                               ('SSD $80 after coupon',80),('SSD $100 with $20 off coupon',80),('SSD $150 with $30 coupon (was $200)',120)]:
            with self.subTest(title=title):self.assertEqual(bot.parse_prices(title)['price'],expected)
        self.assertIsNone(bot.parse_prices('SSD $100 and $20 coupon plus $5 accessories')['price'])

    def test_negations_and_actual_condition(self):
        for text in ['no trade-in required','without activation','trade-in is not required']:
            d=approved('Google Pixel 10 Pro 256GB Obsidian Unlocked $500 '+text)
            self.assertNotIn('conditional_phone_price',d['flags'])
        d=approved('Google Pixel 10 Pro $100 with trade-in')
        self.assertIn('conditional_phone_price',d['flags'])

    def test_free_accessories_do_not_replace_primary_category(self):
        self.assertEqual(bot.classify('Google Pixel 10 Pro 256GB Unlocked with free case $500'),'pixel')
        self.assertEqual(bot.classify('Roland AIRA synthesizer with free software $150'),'music_gear')
        self.assertNotEqual(bot.classify('Case for Google Pixel 10'),'pixel')

    def test_short_and_numeric_structured_mpn(self):
        for mpn in ['SM7B','TR-8S','910-005469']:
            self.assertIsNotNone(M.mpn_key({'brand':'Manufacturer','mpn':mpn}))

    def test_coupon_identity_is_independent_of_eligibility(self):
        d=hardware('corsair.com',80);d['price_kind']='coupon'
        self.assertIsNotNone(M.product_identity(d))
        self.assertFalse(M.purchase_conditions(d)['eligible'])
        d['product_meta']['coupon']={'verified':True,'public':True,'final_price':80}
        self.assertTrue(M.purchase_conditions(d)['eligible'])

    def test_confirmed_coupon_publishes_correct_final_price(self):
        d=hardware('corsair.com',80);d.update(price_kind='coupon',original=150)
        verified=hardware('corsair.com',100)
        verified['product_meta']['coupon']={'verified':True,'public':True,'amount':20,'final_price':80}
        market=M.MarketPrices({},NOW,verifier=lambda u:verified)
        send=Mock();bot.process([d],state(),{},NOW,cfg(),send,market=market)
        send.assert_called_once();self.assertIn('Цена: <b>$80</b>',send.call_args.args[0])

    def test_extreme_discount_prioritised_then_published_after_verification(self):
        d=hardware('corsair.com',10);d['original']=100
        ok,info=bot.assess(d,{'n':0,'median':None},cfg(),NOW)
        self.assertFalse(ok);self.assertTrue(info['priority_verification']);self.assertFalse(info['claim_bad'])
        market=M.MarketPrices({},NOW,verifier=lambda u:d);send=Mock()
        bot.process([d],state(),{},NOW,cfg(),send,market=market)
        send.assert_called_once();self.assertIn('$10',send.call_args.args[0])

    def test_unverified_extreme_stays_queued_not_final_fake(self):
        d=approved('SSD $10 (was $100)');st=state();send=Mock()
        bot.process([d],st,{},NOW,cfg(),send,market=M.MarketPrices({},NOW))
        send.assert_not_called()
        self.assertEqual(next(iter(st['pending'].values()))['_queue']['status'],'priority_verification')

    def test_changed_price_forces_refresh(self):
        current=offer('store.google.com',500);current['fetched_ts']=NOW-300
        fresh=offer('store.google.com',450)
        market=M.MarketPrices({},NOW,verifier=lambda u:fresh)
        for d in [current,offer('bestbuy.com',750),offer('newegg.com',800)]:market.add(d)
        with patch.object(market,'verify',wraps=market.verify) as verify:
            evidence,reason=market.compare(fresh,15,50)
        self.assertIsNotNone(evidence,reason)
        self.assertTrue(any(c.kwargs.get('force_refresh') for c in verify.call_args_list))

    def test_twenty_products_all_searched_in_five_cycles(self):
        st={};seen=[]
        def search(d,**kw):
            seen.append(M.identity(d));return {'urls':[],'notes':[],'requests':1}
        deals=[]
        for i in range(20):
            d=hardware('corsair.com',100);d['product_meta']['mpn']=f'MEMORY{i:05d}';deals.append(d)
        for cycle in range(5):
            M.MarketPrices(st,NOW+cycle*120,discoverer=search).prepare(deals)
        self.assertEqual(len(seen),20);self.assertEqual(len(set(seen)),20)

    def test_zero_actual_search_requests_do_not_spend_budget(self):
        market=M.MarketPrices({},NOW,discoverer=lambda *a,**k:{'requests':0,'notes':[],'urls':[]})
        market.prepare([hardware('corsair.com',100)])
        self.assertEqual(market.search_remaining,4)

    def test_html_truncation_keeps_valid_tags_and_core_link(self):
        d=approved();info=bot.assess(d,{'n':0,'median':None},cfg(),NOW)[1]
        with patch.dict(os.environ,{'CHANNEL_LINK':'x'*5000}):text=bot.build_message(d,info)
        self.assertLess(len(text),4096);self.assertEqual(text.count('<a '),text.count('</a>'))
        self.assertEqual(text.count('<b>'),text.count('</b>'));self.assertIn(d['url'],text)

    def test_structured_condition_and_stock_take_precedence(self):
        d=hardware('corsair.com',100);d['product_meta']['in_stock']=False;d['original']=500
        self.assertFalse(bot.assess(d,{'n':0,'median':None},cfg(),NOW)[0])
        e=D.entry('SSD 1TB','https://www.corsair.com/us/en/p/memory/test',100,
                  meta={'condition':'Refurbished'})
        d=bot.make_deal(e,S.Source('test','test',e['url'],type='store',trust='merchant',allow_undated=True),NOW)
        self.assertEqual(d['cond'],'Refurbished')


class CheckpointRegression(unittest.TestCase):
    def test_null_legacy_state_raises_instead_of_reset(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'state.json').write_text('null')
            with self.assertRaises(C.CheckpointError):C.Store(root).load()

    def test_atomic_bundle_survives_compatibility_export_failure(self):
        with tempfile.TemporaryDirectory() as root:
            store=C.Store(root);st=state();hist={'item':[[NOW,100,'id','source']]}
            real=C.atomic_json
            def fail_export(path,value):
                if Path(path).name=='state.json':raise OSError('interrupted export')
                real(path,value)
            with patch.object(C,'atomic_json',side_effect=fail_export):
                with self.assertRaises(OSError):store.save(st,hist)
            restored_state,restored_history=C.Store(root).load()
            self.assertEqual(restored_state,st);self.assertEqual(restored_history,hist)

    def test_corrupt_primary_recovers_valid_previous_generation(self):
        with tempfile.TemporaryDirectory() as root:
            store=C.Store(root);st=state();store.save(st,{})
            st['posted']['second']=NOW;store.save(st,{})
            Path(root,C.FILE).write_text('null')
            recovered,_=C.Store(root).load()
            self.assertNotIn('second',recovered['posted'])

    def test_inconsistent_exports_do_not_replace_canonical_generation(self):
        with tempfile.TemporaryDirectory() as root:
            store=C.Store(root);st=state();st['posted']['delivered']=NOW;store.save(st,{})
            Path(root,'state.json').write_text('null')
            loaded,_=C.Store(root).load()
            self.assertIn('delivered',loaded['posted'])


class SafeRedirectRegression(unittest.TestCase):
    def response(self,status,url,location=None):
        response=Mock(status_code=status,url=url,headers={'Location':location} if location else {})
        return response

    def test_same_store_canonical_redirect_followed(self):
        a='https://www.corsair.com/us/en/p/memory/test';b=a+'/canonical'
        session=Mock();first=self.response(301,a,b);last=self.response(200,b)
        session.get.side_effect=[first,last]
        self.assertIs(merchant_http.get(a,session=session),last)
        self.assertEqual(session.get.call_count,2);first.close.assert_called_once()

    def test_external_or_insecure_redirect_never_fetched(self):
        a='https://www.corsair.com/us/en/p/memory/test'
        for location in ['https://evil.example/product','http://www.corsair.com/product']:
            session=Mock();session.get.return_value=self.response(302,a,location)
            self.assertIsNone(merchant_http.get(a,session=session));self.assertEqual(session.get.call_count,1)

    def test_redirect_chain_is_bounded(self):
        a='https://www.corsair.com/us/en/p/memory/test';session=Mock()
        session.get.side_effect=[self.response(302,a,a+f'?hop={i}') for i in range(5)]
        self.assertIsNone(merchant_http.get(a,session=session));self.assertEqual(session.get.call_count,4)

    def test_music_links_allowed_but_search_and_cart_rejected(self):
        self.assertIsNotNone(L.merchant_url('https://www.sweetwater.com/store/detail/SM7B--shure'))
        self.assertIsNone(L.merchant_url('https://www.sweetwater.com/store/search?s=shure'))
        self.assertIsNone(L.merchant_url('https://www.sweetwater.com/cart'))

    @unittest.skipUnless('fork' in __import__('multiprocessing').get_all_start_methods(),'fork fixture')
    def test_hung_source_cannot_hold_whole_cycle(self):
        s=S.Source('test','test','https://example.com');start=time.monotonic()
        with patch.object(S,'fetch_one',side_effect=lambda *a:(time.sleep(2),S.FetchResult(s))[1]):
            result=S.fetch_all([s],workers=1,deadline=.05)
        self.assertLess(time.monotonic()-start,.5);self.assertIn('TimeoutError',result[0].error)


class AdditionalAuditRegression(unittest.TestCase):
    def test_conflicting_catalog_prices_retained_for_verification(self):
        from test_direct_stores import markup, schema, BASE
        entries=D.parse_catalog(markup([schema(price='100'),schema(price='120')]),BASE,include_conflicts=True)
        self.assertEqual(len(entries),1);self.assertIsNone(entries[0]['store_price'])
        source=S.Source('test','test',BASE,type='store',trust='merchant',allow_undated=True)
        d=bot.make_deal(entries[0],source,NOW);st=state();send=Mock()
        bot.process([d],st,{},NOW,cfg(),send)
        send.assert_not_called();self.assertEqual(len(st['pending']),1)

    def test_ambiguous_coupon_resolves_from_verified_store_final_price(self):
        d=hardware('corsair.com',None);d.update(price_kind='coupon',original=150)
        current=hardware('corsair.com',80)
        current['product_meta']['coupon']={'verified':True,'public':True,'amount':20,'final_price':80}
        market=M.MarketPrices({},NOW,verifier=lambda u:current);send=Mock()
        bot.process([d],state(),{},NOW,cfg(),send,market=market)
        send.assert_called_once();self.assertIn('Цена: <b>$80</b>',send.call_args.args[0])

    def test_unknown_shipping_is_pending_not_final_rejection(self):
        d=offer('store.google.com',500);d['product_meta']['shipping']=None
        market=M.MarketPrices({},NOW,verifier=lambda u:d)
        for other in [offer('bestbuy.com',750),offer('newegg.com',800)]:market.add(other)
        st=state();send=Mock();bot.process([d],st,{},NOW,cfg(),send,market=market)
        send.assert_not_called();self.assertEqual(len(st['pending']),1)

    def test_legacy_processed_rejection_cannot_erase_pending_during_merge(self):
        import state_merge
        d=approved();merged=state_merge.merge_state({'processed':{Q.pkey(d):NOW}},
            {'pending':{Q.pkey(d):d}})
        self.assertEqual(len(merged['pending']),1)

    def test_coupon_widget_extracts_public_final_amount(self):
        from test_direct_stores import markup,schema,URL
        entries=D.parse_catalog(markup(schema())+'<span id="couponText">Apply $20 coupon</span>',URL)
        self.assertEqual(entries[0]['product_meta']['coupon']['final_price'],80)

    def test_structured_parser_keeps_short_mpn(self):
        from test_direct_stores import markup,schema,URL
        for mpn in ('SM7B','TR-8S','910-005469'):
            entry=D.parse_catalog(markup({**schema(),'brand':'Manufacturer','mpn':mpn}),URL)[0]
            self.assertIsNotNone(M.mpn_key(entry['product_meta']))


class LastMileRegression(unittest.TestCase):
    def test_process_refreshes_changed_price_after_queue_cache_add(self):
        st=state();previous=offer('store.google.com',500);previous['fetched_ts']=NOW-300
        market=M.MarketPrices(st,NOW,verifier=lambda u:offer('store.google.com',450))
        for d in [previous,offer('bestbuy.com',750),offer('newegg.com',800)]:market.add(d)
        send=Mock()
        with patch.object(market,'verify',wraps=market.verify) as verify:
            bot.process([offer('store.google.com',450)],st,{},NOW,cfg(),send,market=market)
        send.assert_called_once()
        self.assertTrue(any(c.kwargs.get('force_refresh') for c in verify.call_args_list))

    def test_long_rate_limit_is_durable_not_an_early_retry(self):
        st=state();send=Mock(side_effect=bot.TelegramError('429',retry_after=900))
        bot.process([approved()],st,{},NOW,cfg(),send)
        self.assertGreaterEqual(next(iter(st['pending'].values()))['_queue']['retry_at'],NOW+900)
        bot.process([],st,{},NOW+300,cfg(),send)
        self.assertEqual(send.call_count,1)

    def test_changed_rules_reset_deferred_assessment(self):
        st=state();d=approved();send=Mock()
        bot.process([d],st,{},NOW,cfg(),Mock(side_effect=bot.TelegramError('retry',retry_after=900)))
        with patch.object(bot,'RULES_VERSION',bot.RULES_VERSION+1):
            bot.process([],st,{},NOW+1,cfg(),send)
        send.assert_called_once()


class LegacyMigrationRegression(unittest.TestCase):
    def test_three_field_legacy_observations_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'state.json').write_text(json.dumps(state()))
            Path(root,'history.json').write_text(json.dumps({'old':[[NOW,80,'thread-id']]}))
            store=C.Store(root);st,hist=store.load()
            self.assertEqual(hist['old'],[[NOW,80,'thread-id','legacy']])
            store.save(st,hist)
            self.assertEqual(C.Store(root).load()[1],hist)


class ProductionCheckpointRegression(unittest.TestCase):
    def test_trim_callback_keeps_live_outbox_and_assessment_references(self):
        with tempfile.TemporaryDirectory() as root:
            store=C.Store(root);st=state();hist={};send=Mock()
            def save(s):
                bot.trim_state(s,NOW,cfg());store.save(s,hist)
            bot.process([approved()],st,hist,NOW,cfg(),send,save_state=save)
            restored,_=C.Store(root).load()
            send.assert_called_once();self.assertFalse(restored['pending'])
            self.assertEqual(len(restored['assessment_cache']),1)
            self.assertEqual(len(restored['posted']),1)
