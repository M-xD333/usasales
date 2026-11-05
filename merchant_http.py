"""Bounded HTTPS redirects between explicit merchant domains."""
import time
from urllib.parse import urljoin, urlsplit
import requests
import store_links


def get(url, session=requests, deadline=None, **kwargs):
    deadline = deadline or time.monotonic() + 20
    for hop in range(4):
        if (urlsplit(url).scheme != 'https' or not store_links.web_url(url)
                or not store_links.on_domain(url, store_links.MERCHANTS)):
            return None
        if time.monotonic() >= deadline:
            return None
        response = session.get(url, allow_redirects=False, **kwargs)
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get('Location')
        response.close()
        if not location or hop == 3:
            return None
        url = urljoin(url, location)
    return None
