import copy
import pytest
import product_weight as W
import weight_estimates as E
import weight_knowledge as K
import checkpoints as C
from test_package_weight import fields,deal,NOW


def test_category_range_and_off_switch_and_unknown(monkeypatch):
    monkeypatch.delenv('WEIGHT_ESTIMATES',raising=False)
    d={'cat':'case'};assert W.display_deal(d)=='≈ 6-12 кг (оценка по категории)'
    assert not W.status(d)['found'] and not d.get('weight_pkg_kg')
    assert W.display_deal({'cat':'unknown'})=='не указан'
    monkeypatch.setenv('WEIGHT_ESTIMATES','0');assert W.display_deal(d)=='не указан'
    assert 'weight_estimate_range_kg' not in d


def test_bundle_mixed_measured_estimated_complete_only(monkeypatch):
    monkeypatch.setenv('WEIGHT_ESTIMATES','1')
    d={'cat':'combo','weight_components':[fields(net='1 kg'),{'cat':'case'}]}
    assert W.display_deal(d)=='≈ сумма: 7-13 кг (оценка по комплектующим)'
    assert not d.get('weight_pkg_kg') and not d.get('weight_net_kg')
    assert W.display_deal({'cat':'combo','weight_components':[fields(net='1 kg'),{'cat':'other'}]})=='не указан'
    monkeypatch.setenv('WEIGHT_ESTIMATES','0');assert W.display_deal(d)=='не указан'


def test_refinement_uses_package_only_and_deduplicates_aliases(tmp_path):
    k=K.Knowledge(tmp_path/K.FILE,NOW)
    for i,kg in enumerate([10,12,20]):
        d=deal(title='Case '+str(i),cat='case');W.apply(d,fields(str(kg)+' kg','999 kg'))
        d['weight_pkg_provenance']['source']='https://example.org/'+str(i);k.remember(d)
    result=k.estimate({'cat':'case'})
    assert result['weight_estimate_range_kg']==[6,25] and result['weight_estimate_basis']['samples']==3
    k.save();assert K.Knowledge(tmp_path/K.FILE,NOW+1).estimate({'cat':'case'})==result
    assert E.estimate('case',[dict(fields(net='999 kg'),category='case')])['weight_estimate_range_kg']==[6,12]


def test_range_payload_validate_restart_and_measured_priority(tmp_path):
    d=deal(cat='case');W.prepare(d);W.validate(d)
    st={'pending':{'item':d}};store=C.Store(tmp_path);store.save(st,{})
    loaded,_=C.Store(tmp_path).load();assert loaded['pending']['item']['weight_estimate_basis']['version']=='estimate_v1'
    W.apply(d,fields('2 kg'));assert W.display_deal(d)=='2 кг (с упаковкой)'
    bad=dict(d,weight_estimate_range_kg=[12,6],weight_estimate_basis={'version':'estimate_v1','kind':'category','samples':0})
    with pytest.raises(ValueError):W.validate(bad)


def test_derived_sum_is_not_measured_net_or_package():
    from tools.weight_coverage import coverage
    d={'cat':'combo','weight_components':[fields('1 kg'),fields('2 kg')]}
    assert W.display_deal(d)=='≈ сумма: 3 кг (комплектующие)'
    s=W.status(d);assert s['found'] is False and s['kind']=='unknown'
    row={'id':'x','listing':'x','price':50,'store':'example','category':'combo','weight_status':s}
    report=coverage({'rows':[row]});assert all(g['derived_sum']==1 and 'net' not in g and 'pkg' not in g for g in report['groups'])
    row['weight_status']=dict(s,found=True,kind='net') # Correct metrics for the earlier optional audit shape, without rewriting it.
    assert coverage({'rows':[row]})['groups'][0]['derived_sum']==1
