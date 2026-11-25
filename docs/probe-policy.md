# Endpoint probe policy (2026-11-25)

Only explicit workflow_dispatch mode=probe, DRY_RUN=1, no Telegram secrets, no state save/restore/chain.
At most 25 distinct URLs, one GET each, honest identifying UA, no redirects/retries/CAPTCHA bypass.
Bodies bounded to ~2 MB; only status 200 + parsed offer entries + complete body count as usable.
Newegg sitemap can be discovered from the robots response; the total request cap still applies.
A candidate URL may return 404/redirect: that is a measured rejection, not permission to guess variants.

DealNews was checked first: https://www.dealnews.com/pages/rss.html
Its stated feed conditions restrict content/link/referral-code modifications and require public attribution; browser extensions are restricted.
The one read-only probe is calibration only. A successful parse does NOT authorize adding DealNews to the rewritten Telegram publishing path.
No source is enabled solely because it returned HTTP 200; parser, topic and publication-use compatibility must also hold.

Endpoint results live in the run's endpoint-probe-<run_id> artifact. Current CLI candidates are in tools/probe.py.
