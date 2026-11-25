"""Bound disposable caches only. Publication/delivery facts are never pruned here."""
import os
import time

CACHE_LIMIT = 3000
PRICE_EVENTS_TTL = 14 * 86400
MIGRATION_VERSION = 1


def compact(state, now=None, max_age_h=None):
    now = time.time() if now is None else now
    if max_age_h is None:
        try:
            max_age_h = int(os.environ.get('MAX_DEAL_AGE_HOURS', '12'))
        except ValueError:
            max_age_h = 12
    changed = False
    for field, stamp, ttl in (('assessment_cache', 'at', max(48, 2 * max_age_h) * 3600),
                              ('price_events', 'observed_at', PRICE_EVENTS_TTL)):
        values = state.get(field)
        if values is None:
            continue
        fresh = [(key, value) for key, value in values.items()
                 if isinstance(value, dict) and isinstance(value.get(stamp), (int, float))
                 and now - value[stamp] < ttl]
        keep = dict(sorted(fresh, key=lambda pair: (pair[1][stamp], pair[0]))[-CACHE_LIMIT:])
        if values != keep:
            values.clear()  # Preserve live references held by process().
            values.update(keep)
            changed = True
    return changed
