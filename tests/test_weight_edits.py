import copy
import hashlib
from unittest.mock import Mock,patch
import pytest
import weight_edits as E
from test_package_weight import deal,fields,NOW

TEXT='📦 <b>Pixel 10 Pro</b>\n💰 Цена: <b>$500</b>\n⚖️ Вес: ≈ 0.3-0.8 кг (оценка по категории)\n🛒 <a href="https://www.amazon.com/dp/B012345678">Купить</a>'


def registry(tmp_path,photo=False):
    r=E.Registry(tmp_path/E.FILE,NOW)
    r.observe(deal(),TEXT,{'result':{'message_id':123,'chat':{'id':-1001},**({'photo':[{}]} if photo else {})}})
    return r


def test_exact_weight_only_replacement_and_dry_no_requests(tmp_path):
    r=registry(tmp_path);row=r.pending()[0];new=E.replacement(row,fields('1 kg'))
    assert new.replace('⚖️ Вес: 1 кг (с упаковкой)',E.weight_line(TEXT))==TEXT
    session=Mock();lookup=Mock();lookup.get.return_value=fields('1 kg')
    changes=r.apply_ready(lookup,'secret',session,selected='canary',dry=True)
    assert len(changes)==1;session.post.assert_not_called();session.get.assert_not_called()
    assert r.pending()[0]['state']=='pending'


@pytest.mark.parametrize('photo,method',[(False,'editMessageText'),(True,'editMessageCaption')])
def test_one_real_mock_attempt_canary_and_restart_no_retry(tmp_path,photo,method):
    r=registry(tmp_path,photo);lookup=Mock();lookup.get.return_value=fields('1 kg')
    session=Mock();session.post.return_value.status_code=200;session.post.return_value.json.return_value={'ok':True}
    r.apply_ready(lookup,'secret',session,selected='canary',preflight=lambda _:True)
    assert session.post.call_args.args[0].endswith(method)
    payload=session.post.call_args.kwargs['json'];assert '$500' in str(payload) and 'amazon.com/dp/B012345678' in str(payload)
    r.apply_ready(lookup,'secret',session,selected='canary',preflight=lambda _:True)
    restored=E.Registry(tmp_path/E.FILE,NOW+1);restored.apply_ready(lookup,'secret',session,selected='canary',preflight=lambda _:True)
    assert session.post.call_count==1 and restored.doc['canary_done']


def test_timeout_never_retries_and_off_or_expired_no_edit(tmp_path):
    r=registry(tmp_path);lookup=Mock();lookup.get.return_value=fields('1 kg');session=Mock()
    r.apply_ready(lookup,'secret',session,selected='off',preflight=lambda _:True);session.post.assert_not_called()
    session.post.side_effect=E.requests.ReadTimeout()
    r.apply_ready(lookup,'secret',session,selected='canary',preflight=lambda _:True)
    restored=E.Registry(tmp_path/E.FILE,NOW+1);restored.apply_ready(lookup,'secret',session,selected='on',preflight=lambda _:True)
    assert session.post.call_count==1 and next(iter(restored.doc['rows'].values()))['state']=='edit_unknown'
    expired=E.Registry(tmp_path/E.FILE,NOW+E.WINDOW+1);assert expired.pending()==[]


def test_invalid_registry_no_reset_and_merge_monotonic(tmp_path):
    r=registry(tmp_path);a=r.path.read_bytes();row=next(iter(r.doc['rows'].values()));row['state']='editing';r.save();b=r.path.read_bytes()
    merged=E.decode(E.merge_bytes(a,b,NOW));assert next(iter(merged['rows'].values()))['state']=='editing'
    r.path.write_bytes(b'broken');bad=E.Registry(r.path,NOW);assert bad.disabled and not bad.save();assert r.path.read_bytes()==b'broken'


def test_public_manual_price_or_link_change_blocks(tmp_path):
    r=registry(tmp_path);row=r.pending()[0];session=Mock();session.get.return_value.status_code=200;session.get.return_value.content=b'page'
    for changed,expected in [(TEXT,True),(TEXT.replace('$500','$600'),False),(TEXT.replace('B012345678','B099999999'),False)]:
        session.get.return_value.text='data-post="usasalessearch/123"><div class="tgme_widget_message_text">'+changed+'</div>'
        assert E.public_unchanged(row,session)==expected
    lookup=Mock();lookup.get.return_value=fields('1 kg')
    r.apply_ready(lookup,'secret',session,selected='canary',preflight=lambda _:False);session.post.assert_not_called()


def test_estimate_alone_and_already_package_never_edit(tmp_path):
    r=registry(tmp_path);row=r.pending()[0]
    assert E.replacement(row,{'weight_estimate_range_kg':[1,2]}) is None
    row['payload']=TEXT.replace(E.weight_line(TEXT),'⚖️ Вес: 1 кг (с упаковкой)')
    assert E.replacement(row,fields('2 kg')) is None
    with patch('weight_edits.os.getenv',return_value='on'):
        assert E.mode()=='canary'


def test_registry_size_cap_and_optional_capture_failure(tmp_path):
    import random
    r=registry(tmp_path);template=copy.deepcopy(r.pending()[0]);rows={};rng=random.Random(3)
    for i in range(1000):
        item=copy.deepcopy(template);item['payload']=rng.randbytes(800).hex();item['hash']=hashlib.sha256(item['payload'].encode()).hexdigest();item['message_id']=i
        rows[str(i)]=item
    data=E.compact({'version':1,'canary_done':False,'rows':rows},NOW)
    assert len(data)<=E.MAX_BYTES and 0<len(E.decode(data)['rows'])<1000
    import bot
    from test_regressions import approved,state,cfg
    send=Mock(return_value={'result':{'message_id':5}});observer=Mock(side_effect=RuntimeError('optional'))
    st=state();bot.process([approved()],st,{},NOW,cfg(),send,post_observer=observer,log=lambda *_:None)
    send.assert_called_once();assert st['posted'] and observer.called
