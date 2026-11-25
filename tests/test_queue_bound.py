import copy
import bot
import offer_queue as Q
from test_bot import deal, NOW, cfg


def make(i, source='corsair', cat='ram', **kw):
    d = deal('Corsair DDR5 RAM $50 (was $100)', item_id=str(i))
    d.update(source_id=source, cat=cat, fetched_ts=NOW, published_ts=NOW-i, **kw)
    return d


def test_cap_and_source_brand_fairness():
    ds = [make(i) for i in range(600)] + [make(700, 'msi'), make(701, 'samsung')]
    pending = {Q.pkey(d): d for d in ds}
    assert Q.bound_pending(pending) == 102
    assert len(pending) == 500
    assert {'corsair', 'msi', 'samsung'} <= {d['source_id'] for d in pending.values()}


def test_unknown_category_does_not_displace_components_or_hot_price():
    ds = [make(i, cat='other') for i in range(600)] + [make(800, 'msi'), make(801, 'hot', price=1, original=100)]
    pending = {Q.pkey(d): d for d in ds}
    Q.bound_pending(pending)
    assert {'msi', 'hot'} <= {d['source_id'] for d in pending.values()}


def test_uncertain_deliveries_survive_cap_and_expiration():
    ds = [make(i, _queue={'status': 'delivery_unknown'}) for i in range(501)]
    pending = {Q.pkey(d): d for d in ds}
    before = copy.deepcopy(pending)
    assert Q.bound_pending(pending) == 0
    assert pending == before
    state = {'pending': pending, 'processed': {}, 'review': []}
    bot.trim_state(state, NOW+86400, cfg())
    assert state['pending'] == before


def test_bound_does_not_write_dedup_or_delivery_facts():
    state = {'pending': {Q.pkey(d): d for d in [make(i) for i in range(510)]},
             'posted': {'fixed': 1}, 'publication_history': {'fixed': 2}, 'last_delivery': {'message_id': 3}}
    before = copy.deepcopy(state)
    Q.bound_pending(state['pending'])
    for key in ('posted', 'publication_history', 'last_delivery'):
        assert state[key] == before[key]


def test_remote_merge_does_not_resurrect_evicted_queue():
    import state_merge
    remote_deals = [make(i) for i in range(600)]
    unknown = make(900, 'uncertain', _queue={'status': 'delivery_unknown'})
    remote = {'pending': {Q.pkey(d): d for d in remote_deals + [unknown]},
              'posted': {'confirmed': NOW},
              'publication_history': {'floor': {'lowest_cents': 5000, 'at': NOW, 'identities': []}},
              'last_delivery': {'message_id': 123}}
    local = copy.deepcopy(remote)
    Q.bound_pending(local['pending'])
    merged = state_merge.merge_state(remote, local)
    assert len(merged['pending']) == 500
    assert Q.pkey(unknown) in merged['pending']
    for key in ('posted', 'publication_history', 'last_delivery'):
        assert merged[key] == remote[key]
    assert state_merge.merge_state(remote, merged)['pending'] == merged['pending']
