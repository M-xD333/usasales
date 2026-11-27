from unittest.mock import patch
import offer_audit as A
import runtime_info
import weight_lookup


def test_runtime_receipt_and_nonblocking_budget_survive_audit_merge(tmp_path):
    with patch('runtime_info.subprocess.check_output',return_value='a'*40):r=runtime_info.receipt(5,7,100)
    assert 'PARSER_VERSION=5' in r['log_line'] and r['sha']=='a'*40
    one=A.Ledger(tmp_path/'one',100,7);one.doc['runtime']=r;one.flush()
    two=A.Ledger(tmp_path/'two',101,7);two.doc['runtime']=dict(r,at=101,sha='b'*40);two.flush()
    assert A.decode(A.merge_bytes(one.path.read_bytes(),two.path.read_bytes(),101))['runtime']['sha']=='b'*40
    assert weight_lookup.BUDGET==6


def test_real_runner_corsair_weight_is_not_shipping():
    from pathlib import Path
    import product_weight as W
    d=W.extract(Path('tests/fixtures/weight/corsair-runner-real.html').read_text(),'https://www.corsair.com/us/en/p/example')
    assert not d.get('weight_pkg_kg') and not d.get('weight_net_kg')
