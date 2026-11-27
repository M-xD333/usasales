import copy,json
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import Mock
import woot_api as W


def payload():return json.loads(Path('tests/fixtures/woot/feed-official-example.json').read_text())


def test_disabled_without_key_and_one_header_only_request(monkeypatch):
    monkeypatch.delenv('WOOT_API_KEY',raising=False);s=Mock()
    assert not W.fetch('Computers',session=s)['enabled'];s.get.assert_not_called()
    s.get.return_value.status_code=429;s.get.return_value.headers={'Retry-After':'60'}
    r=W.fetch('Computers',key='secret',session=s);s.get.assert_called_once()
    assert s.get.call_args.kwargs['headers']['x-api-key']=='secret'
    assert 'secret' not in str(r) and 'secret' not in s.get.call_args.args[0]
    assert r['retry_after']=='60'


def test_official_example_soldout_not_live_and_exact_vs_range():
    p=payload();now=datetime(2018,10,9,12,tzinfo=timezone.utc)
    assert W.normalize(p,now)['entries']==[]
    p['Items'][0]['IsSoldOut']=False;p['Items'][0]['IsAvailableOnMobileAppOnly']=False
    e=W.normalize(p,now)['entries'][0];assert e['store_price']==39.99 and e['store_original']==49.99
    p['Items'][0]['SalePrice']['Maximum']=59.99
    e=W.normalize(p,now)['entries'][0];assert e['store_price'] is None and e['store_issue']=='ambiguous_woot_variant'
    p['Items'][0]['Url']='https://evil.example/product';assert W.normalize(p,now)['entries']==[]
