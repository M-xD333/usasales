import calendar
import feedparser
import pytest
import sources


@pytest.mark.parametrize('term', ['newegg', 'amazon', 'micro center', 'best buy', 'b&h', 'dell', 'lenovo'])
def test_probed_feed_normalizes_exact_product_link(term):
    source = sources._sd_search(term)
    assert source in sources.active_sources({})
    xml = '''<rss version="2.0"><channel><title>Deals</title><item>
    <title>DDR5 RAM $50 (was $100)</title><link>https://slickdeals.net/f/123-deal</link>
    <guid>123</guid><pubDate>Wed, 25 Nov 2026 12:00:00 GMT</pubDate>
    <description><![CDATA[<a href="https://www.newegg.com/p/N82E16800000001">Buy</a>]]></description>
    </item></channel></rss>'''
    row = sources.normalize_entry(feedparser.parse(xml).entries[0])
    assert row['store_candidates'] == ['https://www.newegg.com/p/N82E16800000001']
    assert row['published_ts'] == calendar.timegm((2026,11,25,12,0,0))
    assert source.trust == 'unvetted'
