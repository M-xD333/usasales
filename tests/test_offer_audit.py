import copy
import hashlib
import random
from unittest.mock import patch,Mock
import pytest
import offer_audit as A
import bot
from test_bot import NOW,cfg
from test_newegg_aggregator import candidate,engine


def test_compressed_size_cap_newest_rows_and_visible_truncation(tmp_path):
    ledger=A.Ledger(tmp_path/A.FILE,NOW,7)
    ledger.record(candidate('curated'),'queued','unconfirmed')
    template=next(iter(ledger.pending.values()))
    rng=random.Random(9);rows=[]
    for i in range(12000):
        noise=rng.randbytes(200).hex()
        rows.append(dict(template,id=hashlib.sha256(str(i).encode()).hexdigest(),
                         listing=hashlib.sha256(('listing'+str(i)).encode()).hexdigest(),
                         ts=NOW-i,last_ts=NOW-i,title=noise[:160],reason=noise[160:]))
    doc,data=A.compact(dict(ledger.doc,rows=rows),NOW)
    assert len(data)<=1_500_000
    decoded=A.decode(data)
    assert 0<len(decoded['rows'])<12000
    assert decoded['dropped_capacity']>0 and decoded['truncated_before']>0
    assert template['ts']==max(r['last_ts'] for r in decoded['rows'])


def test_restart_ttl_coalesces_repeats_but_retains_publication(tmp_path):
    path=tmp_path/A.FILE;d=candidate('curated')
    for t,decision in [(NOW,'queued'),(NOW+60,'queued'),(NOW+120,'published')]:
        log=A.Ledger(path,t,7);d['fetched_ts']=t;log.record(d,decision,'same');log.flush(final=True)
    rows=A.decode(path.read_bytes())['rows']
    assert len(rows)==2 and rows[0]['last_ts']==NOW+60
    log=A.Ledger(path,NOW+A.TTL+70,7);log.flush(final=True)
    assert [r['decision'] for r in A.decode(path.read_bytes())['rows']]==['published']


def test_atomic_failure_never_replaces_existing_log_or_touches_publications(tmp_path):
    path=tmp_path/A.FILE;l=A.Ledger(path,NOW,7);d=candidate('curated')
    l.record(d,'queued','old');l.flush(final=True);before=path.read_bytes()
    l.record(dict(d,id='different'),'rejected','new')
    with patch('offer_audit.os.replace',side_effect=OSError('disk full')):l.flush(final=True)
    assert l.disabled and path.read_bytes()==before


def test_merge_preserves_both_writers_and_newer_decision(tmp_path):
    d=candidate('curated');a=A.Ledger(tmp_path/'a.gz',NOW,7);b=A.Ledger(tmp_path/'b.gz',NOW,7)
    a.record(d,'queued','wait');a.flush()
    b.record(d,'queued','wait');b.record(d,'published','ok');b.flush()
    b.record(dict(d,id='second'),'queued','wait');b.flush()
    merged=A.decode(A.merge_bytes((tmp_path/'a.gz').read_bytes(),(tmp_path/'b.gz').read_bytes(),NOW))
    assert len(merged['rows'])==2
    assert sorted(r['decision'] for r in merged['rows'])==['published','queued']


def test_real_process_decisions_and_dry_run_has_no_audit_side_effect(tmp_path):
    d=candidate('curated');l=A.Ledger(tmp_path/A.FILE,NOW,7);state={'pending':{},'posted':{},'processed':{},'review':[]}
    sender=Mock();bot.process([d],state,{},NOW,cfg(),sender,market=engine(0),audit=l.record)
    l.flush(final=True)
    assert A.decode((tmp_path/A.FILE).read_bytes())['rows'][-1]['decision']=='published'
    spy=Mock();bot.process([candidate('curated')],{'pending':{},'posted':{},'processed':{},'review':[]},{},NOW,cfg(),Mock(),dry=True,audit=spy)
    spy.assert_not_called()


def test_corrupt_file_is_not_reset_and_logging_failure_does_not_stop_send(tmp_path):
    path=tmp_path/A.FILE;path.write_bytes(b'broken');l=A.Ledger(path,NOW,7)
    d=candidate('curated');send=Mock()
    bot.process([d],{'pending':{},'posted':{},'processed':{},'review':[]},{},NOW,cfg(),send,market=engine(0),audit=l.record)
    l.flush(final=True)
    assert send.called and path.read_bytes()==b'broken'


def test_same_listing_distinct_prices_and_advertised_merchant(tmp_path):
    log=A.Ledger(tmp_path/A.FILE,NOW,7)
    one=candidate('curated');one.update(url='https://slickdeals.net/f/12345',store_candidates=['https://www.newegg.com/p/N82E16812345678'],price=90)
    two=dict(one,price=80)
    log.record(one,'rejected','threshold');log.record(two,'queued','evidence')
    two['price']=79;log.record(two,'published','sent');log.flush(final=True)
    rows=A.decode(log.path.read_bytes())['rows']
    assert sorted(r['price'] for r in rows)==[79,90]
    assert all(r['store']=='newegg.com' and r['store_basis']=='advertised' and r['candidate_key'].startswith('url:') for r in rows)
