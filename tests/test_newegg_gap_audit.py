from tools.newegg_gap import analyze, destination


def test_newegg_business_name_on_amazon_is_not_counted_as_newegg():
    sample = {'cutoff':'2026-11-18T12:00:00+00:00','now':'2026-11-25T12:00:00+00:00',
              'boundary_reached':True,'all_text_posts':3,'rows':[
        {'id':'sample/1','text':'Amazon [Newegg Business] $100','at':'2026-11-19T12:00:00+00:00',
         'links':['https://r.amzlink.to/?btn_url=https%3A%2F%2Fwww.amazon.com%2Fdp%2FB012345678']},
        {'id':'sample/2','text':'Newegg Combo $1109.98','at':'2026-11-18T21:37:18+00:00',
         'links':['https://click.linksynergy.com/deeplink?id=test&mid=44583&murl=https://www.newegg.com/Product/ComboDealDetails?ItemList=Combo.4884342']},
        {'id':'sample/3','text':'Newegg Combo $1639.98','at':'2026-11-19T14:59:19+00:00',
         'links':['https://click.linksynergy.com/deeplink?id=test&mid=44583&murl=https://www.newegg.com/Product/ComboDealDetails?ItemList=Combo.4884345']} ]}
    result = analyze(sample, {'state':{}})
    assert result['newegg_count'] == 2
    assert result['competitor_daily_tashkent'] == {'2026-11-19':2}
    assert result['our_daily_total'] is None
    assert all(r['target_params'] == ['ItemList'] for r in result['rows'])
    assert all(r['tracking_params'] == ['id','mid','murl'] for r in result['rows'])
