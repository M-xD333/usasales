import copy
import gzip
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import Mock,patch
import pytest
import bot
import product_weight as W
import weight_knowledge as K
import weight_lookup as L
import checkpoints as C
import state_merge
import offer_audit as A
from tools.weight_coverage import coverage
from test_regressions import approved,state,cfg,NOW


def fields(pkg=None,net=None):
    props=[]
    if pkg:props.append({'name':'Package Weight','value':pkg})
    if net:props.append({'name':'Item Weight','value':net})
    return W.from_product_fields({'additionalProperty':props},'https://example.org/product',NOW)


@pytest.mark.parametrize('label,kind',[('Shipping Weight','pkg'),('Package Weight','pkg'),('Weight (with packaging)','pkg'),('Item Weight','net'),('Product Weight','net'),('Net Weight','net')])
def test_explicit_field_names(label,kind):
    result=W.extract('<table><tr><th>'+label+'</th><td>16 oz</td></tr></table>','https://example.org/product',NOW)
    assert result['weight_'+kind+'_kg']==pytest.approx(.45359237)
    assert result['weight_'+kind+'_provenance']=={'source':'https://example.org/product','field':label,'ts':NOW}


def test_amazon_package_dimensions_not_dimensions_and_bullets_both_kinds():
    page='<ul><li>Package Dimensions : 10 x 8 x 2 inches; 1.5 pounds</li><li>Item Weight : 16 ounces</li></ul>'
    result=W.extract(page,'https://www.amazon.com/dp/B012345678',NOW)
    assert result['weight_pkg_kg']==pytest.approx(.680388555)
    assert result['weight_net_kg']==pytest.approx(.45359237)
    for value in ('10 x 8 x 2 inches','10 x 8 x 2 inches; 5','10 x 8 x 2 inches; 1-2 pounds'):
        assert not W.extract('<table><th>Package Dimensions</th><td>'+value+'</td></table>','https://example.org/product').get('weight_pkg_kg')


def test_ambiguous_unitless_conflicting_and_variants_stay_unknown():
    for page in ('<table><th>Weight</th><td>1 kg</td></table>','<table><th>Shipping Weight</th><td>8</td></table>',
                 '<table><th>Shipping Weight</th><td>1 lb</td><th>Shipping Weight</th><td>2 lb</td></table>',
                 '<script type="application/ld+json">{"@type":"ProductGroup","hasVariant":[]}</script><table><th>Shipping Weight</th><td>1 lb</td></table>'):
        assert not W.extract(page,'https://example.org/product').get('weight_pkg_kg')
        assert not W.extract(page,'https://example.org/product').get('weight_net_kg')


@pytest.mark.parametrize('case,expected',[
 ('pkg','1 кг (с упаковкой)'),('net','0.5 кг (без упаковки)'),('none','не указан'),
 ('bundle_all','≈ 1.5 кг (сумма комплектующих)'),('bundle_partial','не указан')])
def test_dry_run_five_cases_and_photo_fallback(case,expected):
    d=approved()
    if case=='pkg':W.apply(d,fields('1 kg','500 g'))
    if case=='net':W.apply(d,fields(net='500 g'))
    if case.startswith('bundle'):
        d['cat']='combo';d['weight_components']=[fields(net='1 kg'),fields(net='500 g') if case=='bundle_all' else {}]
    before=copy.deepcopy(d);st=state();original=copy.deepcopy(st);logs=[];send=Mock()
    bot.process([d],st,{},NOW,cfg(),send,dry=True,log=logs.append)
    assert any('⚖️ Вес: '+expected in text for text in logs)
    assert st==original and d==before;send.assert_not_called()
    info=bot.assess(d,{'n':0,'median':None},cfg(),NOW)[1]
    with patch.dict('os.environ',{'CHANNEL_LINK':'x'*5000}):assert '⚖️ Вес: '+expected in bot.build_message(d,info)
    assert 'Вес: '+expected in bot.photo_caption(bot.build_message(d,info),d['url'])['caption']


def test_estimates_off_by_default_and_explicitly_labelled(monkeypatch):
    monkeypatch.delenv('WEIGHT_ESTIMATES',raising=False);d=dict(approved(),cat='mouse')
    assert W.display_deal(d)=='не указан'
    monkeypatch.setenv('WEIGHT_ESTIMATES','1');assert W.display_deal(d)=='≈ 0.3 кг (оценка)'
    assert not W.status(d)['found'] # Estimates never inflate measured coverage.
    monkeypatch.setenv('WEIGHT_ESTIMATES','0');assert W.display_deal(d)=='не указан'


def deal(title='Pixel 10 Pro 256GB Black',**changes):
    d=dict(approved(),title=title,cond='New',url='https://www.amazon.com/dp/B012345678',
           product_meta={'brand':'Google','mpn':'G-P10PRO-B256','gtins':['123456789012'],'model':'Pixel 10 Pro'})
    d.update(changes);return d


def test_knowledge_restart_cross_store_separate_kinds_and_variant_safety(tmp_path):
    path=tmp_path/K.FILE;k=K.Knowledge(path,NOW);d=deal();W.apply(d,fields('1 kg','500 g'));k.remember(d);k.save()
    restarted=K.Knowledge(path,NOW+1)
    other=deal(url='https://www.bestbuy.com/product/pixel/123.p');assert restarted.lookup(other)['weight_pkg_kg']==1
    assert restarted.lookup(other)['weight_net_kg']==.5
    for changed in (deal('Pixel 10 Pro 128GB Black'),deal('Pixel 10 Pro 256GB White'),deal(cond='Refurbished'),deal(cat='combo'),deal('Pixel 10 Pro 256GB Black bundle')):
        assert restarted.lookup(changed)=={}
    d2=deal();W.apply(d2,fields(net='600 g'));k.remember(d2);k.save()
    assert K.Knowledge(path,NOW+1).lookup(other)['weight_pkg_kg']==1


def test_knowledge_size_cap_and_merge(tmp_path):
    k=K.Knowledge(tmp_path/K.FILE,NOW);base=fields('1 kg','500 g');rows={}
    for i in range(5000):
        r=copy.deepcopy(base);r['at']=NOW+i/10000
        r['weight_pkg_provenance']['source']='https://example.org/'+hashlib.sha512(str(i).encode()).hexdigest()*3
        r['weight_net_provenance']['source']='https://example.org/'+hashlib.sha512(('net'+str(i)).encode()).hexdigest()*3
        rows[hashlib.sha256(str(i).encode()).hexdigest()]=r
    data=K.compact({'version':1,'rows':rows},NOW+1)
    assert len(data)<=500_000 and 0<len(K.decode(data)['rows'])<5000
    one=deal();W.apply(one,fields('1 kg'));k.remember(one);k.save()
    second=K.Knowledge(tmp_path/'second.gz',NOW+1);two=deal();W.apply(two,fields(net='500 g'));second.remember(two);second.save()
    merged=K.decode(K.merge_bytes(k.path.read_bytes(),second.path.read_bytes(),NOW+1))
    assert all(r['weight_pkg_kg']==1 and r['weight_net_kg']==.5 for r in merged['rows'].values())


def hung(url):time.sleep(5)


def test_lookup_hard_deadline_and_publication_never_waits():
    lookup=L.Lookup(resolver=hung,budget=.1);d=deal();lookup.request(d)
    start=time.monotonic();result=lookup.get(d)
    assert time.monotonic()-start<.5 and result['weight_missing_reason']
    time.sleep(.3);lookup.poll()
    assert lookup.results[d['url']]['weight_missing_reason']=='lookup_timeout'
    lookup.close()
    send=Mock();start=time.monotonic()
    bot.process([approved()],state(),{},NOW,cfg(),send,details_resolver=hung)
    assert time.monotonic()-start<1;send.assert_called_once()
    assert 'Вес: не указан' in send.call_args.args[0]


def test_state_validate_restart_merge_and_weight_audit(tmp_path):
    d=deal();W.apply(d,fields('1 kg','500 g'));st=state();st['pending']={'x':d}
    store=C.Store(tmp_path);store.save(st,{});loaded,_=C.Store(tmp_path).load()
    merged=state_merge.merge_state(loaded,copy.deepcopy(st))
    assert next(iter(merged['pending'].values()))['weight_pkg_provenance']['field']=='Package Weight'
    bad=copy.deepcopy(st);bad['pending']['x']['weight_pkg_provenance']['ts']='bad'
    with pytest.raises(C.CheckpointError):C.validate_state(bad)
    ledger=A.Ledger(tmp_path/A.FILE,NOW,7);ledger.record(d,'queued','evidence');ledger.flush(final=True)
    decoded=A.decode(ledger.path.read_bytes());r=decoded['rows'][0]
    assert r['weight_status']=={'found':True,'source':'https://example.org/product','kind':'pkg','missing_reason':''}
    assert coverage(decoded)['groups'][0]['pkg']==1
    r.pop('weight_status');assert coverage(decoded)['groups'][0]['unmeasured']==1


def test_real_html_fragments_and_compound_mass_are_not_mislabelled():
    fixtures=Path('tests/fixtures/weight')
    for name,reason in [('corsair-ambiguous-real.html','no_explicit_weight_with_units'),('amazon-challenge-real.html','challenge_page')]:
        result=W.extract((fixtures/name).read_text(),'https://example.org/product',NOW)
        assert result['weight_missing_reason']==reason
        assert not result.get('weight_pkg_kg') and not result.get('weight_net_kg')
    for page in ('<li>Item Weight: 2 lb 4 oz</li>','<li>Package Dimensions: 10 inches; 1-2 pounds</li>',
                 '<div id="variation_color_name"><table><th>Package Weight</th><td>1 kg</td></table></div>'):
        result=W.extract(page,'https://example.org/product',NOW)
        assert not result.get('weight_pkg_kg') and not result.get('weight_net_kg')


def test_model_fallback_requires_exact_title_variant(tmp_path):
    k=K.Knowledge(tmp_path/K.FILE,NOW)
    d=deal(product_meta={'brand':'Google'});W.apply(d,fields('1 kg'));k.remember(d)
    assert k.lookup(dict(d,url='https://www.bestbuy.com/product/pixel/123.p'))['weight_pkg_kg']==1
    assert k.lookup(dict(d,title='Pixel 10 Pro 128GB Black',url='https://www.bestbuy.com/product/pixel/123.p'))=={}


def test_audit_latest_decision_does_not_double_count_offer():
    base={'id':'one','listing':'same','price':50,'last_ts':1,'store':'amazon.com','category':'cpu'}
    known=dict(base,id='two',last_ts=2,weight_status={'found':True,'kind':'pkg','source':'page','missing_reason':''})
    report=coverage({'rows':[base,known]})
    assert report['rows']==2 and report['offers']==1
    assert all(r['seen']==1 and r['pkg']==1 for r in report['groups'])


def ready_package(url):return fields('1 kg')


def test_prefetched_result_enters_knowledge_without_send_time_lookup(tmp_path):
    k=K.Knowledge(tmp_path/K.FILE,NOW);lookup=L.Lookup(k,resolver=ready_package);d=deal();lookup.request(d)
    limit=time.monotonic()+1
    while not lookup.results and time.monotonic()<limit:
        lookup.poll();time.sleep(.005)
    assert lookup.results and k.lookup(d)['weight_pkg_kg']==1
    lookup.close()
    assert K.Knowledge(tmp_path/K.FILE,NOW+1).lookup(d)['weight_pkg_kg']==1


def test_corrupt_optional_knowledge_never_blocks_mock_publication_or_existing_floors(tmp_path):
    path=tmp_path/K.FILE;path.write_bytes(b'not gzip');k=K.Knowledge(path,NOW,log=lambda *_:None)
    lookup=L.Lookup(k,resolver=hung,budget=.1);st=state()
    protected={'lowest_cents':100,'at':1,'identities':[]};st['publication_history']={'unrelated':copy.deepcopy(protected)}
    send=Mock();bot.process([approved()],st,{},NOW,cfg(),send,weight_reader=lookup)
    lookup.close();send.assert_called_once()
    assert 'Вес: не указан' in send.call_args.args[0]
    assert st['publication_history']['unrelated']==protected and path.read_bytes()==b'not gzip'


@pytest.mark.parametrize('store_url',[None,'https://www.amazon.com/dp/B012345678'])
def test_truncated_photo_caption_always_preserves_weight(store_url):
    text='<b>'+('📦'*1200)+'</b>\n⚖️ Вес: 1 кг (с упаковкой)'
    caption=bot.photo_caption(text,store_url)['caption']
    assert caption.count('⚖️ Вес: 1 кг (с упаковкой)')==1
    import html,re
    assert len(html.unescape(re.sub('<[^>]*>','',caption)).encode('utf-16-le'))<=2048
