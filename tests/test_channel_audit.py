from unittest.mock import Mock,patch
import pytest
from tools.channel_audit import public_username,primary_newegg,fetch_window


def test_metadata_only_and_private_channel_fails_without_chat_details():
    session=Mock();session.post.return_value=Mock(status_code=200)
    session.post.return_value.json.return_value={'ok':True,'result':{'username':'public_deals'}}
    with patch.dict('os.environ',{'TG_BOT_TOKEN':'secret','TG_CHAT_ID':'private-id'}):
        assert public_username(session)=='public_deals'
        assert session.post.call_args.args[0].endswith('/getChat')
        session.post.return_value.json.return_value={'ok':True,'result':{'id':-123}}
        with pytest.raises(RuntimeError,match='No public channel username'):
            public_username(session)


def test_primary_store_not_comparison_reference_is_counted():
    amazon='https://www.amazon.com/dp/B012345678';newegg='https://www.newegg.com/p/N82E16800000001'
    base={'text':'Offer','at':'2026-11-19T12:00:00+00:00'}
    rows=[dict(base,id='ours/1',links=[amazon,newegg]),dict(base,id='ours/2',links=[newegg,amazon])]
    assert [r['post'] for r in primary_newegg(rows)]==['ours/2']


def test_window_reaches_older_boundary_without_counting_older_posts():
    from datetime import datetime
    session=Mock()
    def page(pid,at,older):
        return f'<div data-post="public_deals/{pid}"><div class="tgme_widget_message_text">Deal</div><time datetime="{at}"></time></div><a href="/s/public_deals?before={older}">old</a>'
    session.get.side_effect=[Mock(status_code=200,text=page(2,'2026-11-19T12:00:00+00:00',2)),
                             Mock(status_code=200,text=page(1,'2026-11-17T12:00:00+00:00',1))]
    with patch('tools.channel_audit.time.sleep'):
        rows,pages,complete=fetch_window('public_deals',datetime.fromisoformat('2026-11-18T12:00:00+00:00'),
                                       datetime.fromisoformat('2026-11-25T12:00:00+00:00'),session)
    assert complete and len(pages)==2
    assert [r['id'] for r in rows]==['public_deals/2']
