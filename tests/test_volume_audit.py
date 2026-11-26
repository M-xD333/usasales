import json
from pathlib import Path
from tools.volume_audit import reported_price,analyze


def test_only_explicit_discount_and_primary_offer_price():
    assert reported_price('Amazon DDR5 6000 $99.99 80 PLUS, 24% APR')==(99.99,None,None)
    assert reported_price('Best Buy $100 was $200')==(100,200,50)
    assert reported_price('Amazon 30% off')==(None,None,30)
    assert reported_price('Newegg $Combo, 1,109.98 25% off')==(1109.98,None,25)
    assert reported_price('💰 $99.00  −25%\nOther store $200',True)==(99,None,25)


def test_frozen_cohorts_are_complete_and_historical_causes_unknown():
    report=analyze(json.loads(Path('docs/evidence/volume-input-2026-11-26.json').read_text()))
    for name,count in [('pcpartsdrop',192),('usasalessearch',48)]:
        for dimension in ('store','category','price_band','discount_band'):
            assert sum(report['summary'][name][dimension].values())==count
    assert report['summary']['pcpartsdrop']['store']=={'amazon.com':94,'bestbuy.com':96,'newegg.com':2}
    assert report['summary']['usasalessearch']['store']=={'corsair.com':34,'amazon.com':14}
    assert all(r['historical_miss_reason']=='unknown' for r in report['rows']['pcpartsdrop'])
    assert report['summary']['pcpartsdrop']['price_band']['n/a']==44
    assert report['opportunity_ceilings']['category']['posts']==10
    assert sum(bool(r['same_product_our_posts']) for r in report['rows']['pcpartsdrop'])==3
