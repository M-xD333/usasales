from unittest.mock import Mock
from tools.upc_free_probe import run,BASE


def response(items,status=200):
    r=Mock();r.status_code=status;r.headers={'X-RateLimit-Remaining':'90'};r.json.return_value={'items':items};return r


def test_twenty_products_batch_and_spacing_no_package_guess():
    items=[{'upc':str(123456789000+i),'title':'Memory '+str(i),'weight':'1 lb'} for i in range(20)]
    s=Mock();s.get.side_effect=[response(items)]+[response(items[i:i+2]) for i in range(0,20,2)];sleep=Mock()
    result=run(s,sleep)
    assert result['summary']['attempted_products']==20 and result['summary']['weight_with_units']==20
    assert result['summary']['confirmed_pkg']==0 and result['summary']['confirmed_net']==0
    assert s.get.call_count==11 and all(c.args[0].startswith(BASE) for c in s.get.call_args_list)
    assert all(c.args[0]>=10 for c in sleep.call_args_list)
    assert all('user_key' not in str(c) and '/v1/' not in str(c) for c in s.get.call_args_list)


def test_429_stops_without_retry_or_paid_fallback():
    s=Mock();s.get.return_value=response([],429);result=run(s,Mock())
    assert result['stopped']=='HTTP 429' and not result['rows'];s.get.assert_called_once()
