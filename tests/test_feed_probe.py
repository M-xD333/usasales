from unittest.mock import Mock
from tools.feed_probe import inspect,URLS
import bot


def test_probe_single_request_and_comments_are_not_votes():
    session=Mock();r=session.get.return_value;r.status_code=200;r.headers={};r.content=b'<rss version="2.0"><channel><item><title>RTX 5070 was $699 now $499 at Amazon</title><link>https://example.org/deal</link><comments>https://example.org/comments</comments></item></channel></rss>'
    result=inspect('test','https://example.org/rss',session)
    session.get.assert_called_once();assert result['entries']==1 and result['score_fields']==[]
    assert session.get.call_args.kwargs['allow_redirects'] is False
    parsed=bot.parse_prices(result['sample'][0]['title']);assert parsed['price']==499 and parsed['original']==699
    assert len(URLS)<=25


def test_403_not_retried_or_followed():
    session=Mock();session.get.return_value.status_code=403;session.get.return_value.headers={};session.get.return_value.content=b'blocked'
    result=inspect('test','https://example.org/rss',session)
    assert result['http']==403 and 'entries' not in result;session.get.assert_called_once()


def test_thumbnails_are_not_real_community_votes():
    session=Mock();r=session.get.return_value;r.status_code=200;r.headers={}
    r.content=b'<rss xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel><item><title>GPU $500</title><media:thumbnail url="https://example.org/a.jpg"/></item></channel></rss>'
    assert inspect('test','https://example.org/rss',session)['score_fields']==[]


def test_selected_us_editorial_feed_and_real_price_headline():
    import sources,feedparser
    from pathlib import Path
    source=next(s for s in sources.SOURCES if s.id=='tomshardware')
    assert source.url=='https://www.tomshardware.com/feeds/articletype/deals' and source.trust=='editorial' and source.publish
    entry=sources.normalize_entry(feedparser.parse(Path('tests/fixtures/feeds/toms-real.xml').read_bytes()).entries[0])
    assert bot.parse_prices(entry['title'])['price']==1141
    assert bot.parse_prices('Save 21% on this 2TB SSD, now just 17.5 cents per GB')['price'] is None
    for title,current,old in [('GPU was $699 now $499 at Amazon',499,699),('SSD was $199 now $129 at Best Buy',129,199)]:
        result=bot.parse_prices(title);assert result['price']==current and result['original']==old
