import json
import unittest
from unittest.mock import Mock, patch

import bot
import direct_stores as D
import product_images as I
import product_weight as W
import sources as S
from test_regressions import approved, state, cfg, NOW
from test_direct_stores import schema, markup, URL


class WeightParsing(unittest.TestCase):
    def test_supported_units_are_converted_to_kg(self):
        for value,expected in [('850 g',.85),('2 kg',2),('2 lbs',.90718474),('16 ounces',.45359237)]:
            with self.subTest(value=value):self.assertAlmostEqual(W.kilograms(value),expected)
        self.assertAlmostEqual(W.kilograms({'value':500,'unitCode':'GRM'}),.5)
        self.assertEqual(W.kilograms({'value':'2.5','unitText':'kilograms'}),2.5)

    def test_no_guesses_for_missing_units_ranges_and_invalid_weights(self):
        for value in [123,'123','1-2 kg','2 lb 4 oz','NaN kg','-2 kg','0 kg',True,None]:
            with self.subTest(value=value):self.assertIsNone(W.kilograms(value))

    def test_schema_weight_survives_catalog_normalization(self):
        product={**schema(),'weight':{'value':850,'unitCode':'GRM'}}
        entry=D.parse_catalog(markup(product),URL)[0]
        deal=bot.make_deal(entry,S.Source('store','Store',URL,type='store',trust='merchant',allow_undated=True),NOW)
        self.assertEqual(deal['weight_kg'],.85)

    def test_spec_table_uses_item_weight_not_shipping_weight(self):
        page='<table><tr><th>Shipping Weight</th><td>5 pounds</td></tr><tr><th>Item Weight</th><td>2 pounds</td></tr></table>'
        self.assertAlmostEqual(W.from_markup(page,URL),.90718474)
        self.assertIsNone(W.from_markup('<table><th>Shipping Weight</th><td>5 pounds</td></table>',URL))

    def test_weight_with_unit_in_label(self):
        self.assertEqual(W.from_markup('<dl><dt>Weight (kg)</dt><dd>1.2</dd></dl>',URL),1.2)

    def test_conflicting_variants_or_recommendations_are_not_used(self):
        a={**schema(),'weight':'1 kg'};b={**schema(),'weight':'2 kg'}
        self.assertIsNone(W.from_markup(markup([a,b]),URL))
        b['url']=URL+'/other';self.assertEqual(W.from_markup(markup([a,b]),URL),1)
        self.assertIsNone(W.from_markup(markup([a,b]),URL+'/unknown'))

    def test_additional_product_property(self):
        self.assertAlmostEqual(W.from_product({'additionalProperty':[{'name':'Weight','value':'700 g'}]}),.7)
        self.assertIsNone(W.from_product({'additionalProperty':[{'name':'Shipping Weight','value':'2 kg'}]}))


class WeightDelivery(unittest.TestCase):
    def test_message_and_short_fallback_show_kg(self):
        d=approved();d['weight_kg']=.85
        info=bot.assess(d,{'n':0,'median':None},cfg(),NOW)[1]
        self.assertIn('⚖️ Вес: 0.85 кг',bot.build_message(d,info))
        with patch.dict('os.environ',{'CHANNEL_LINK':'x'*5000}):
            self.assertIn('⚖️ Вес: 0.85 кг',bot.build_message(d,info))
        caption=bot.photo_caption(bot.build_message(d,info),d['url'])
        self.assertIn('Вес: 0.85 кг',caption['caption'])

    def test_missing_weight_is_explicit(self):
        d=approved();info=bot.assess(d,{'n':0,'median':None},cfg(),NOW)[1]
        self.assertIn('Вес: не указан',bot.build_message(d,info))

    def test_process_enriches_weight_and_photo_together(self):
        d=approved();send=Mock();details=Mock(return_value={'weight_kg':1.25,'image_url':'https://images.example.com/product.jpg'})
        bot.process([d],state(),{},NOW,cfg(),send,details_resolver=details)
        details.assert_called_once_with(d['url'])
        self.assertIn('Вес: 1.25 кг',send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs['photo_url'],'https://images.example.com/product.jpg')

    def test_unknown_weight_does_not_block_publication(self):
        send=Mock();bot.process([approved()],state(),{},NOW,cfg(),send,details_resolver=lambda u:None)
        send.assert_called_once();self.assertIn('Вес: не указан',send.call_args.args[0])

    def test_same_page_fetch_returns_both_image_and_weight(self):
        session=Mock();response=session.get.return_value
        response.status_code=200;response.url=URL;response.encoding='utf-8'
        response.iter_content.return_value=[(markup({**schema(),'weight':'500 g'})+'<meta property="og:image" content="https://images.example.com/product.jpg">').encode()]
        details=I.from_product_details(URL,session=session)
        self.assertEqual(details['weight_kg'],.5)
        self.assertEqual(details['image_url'],'https://images.example.com/product.jpg')
        self.assertEqual(session.get.call_count,1)
