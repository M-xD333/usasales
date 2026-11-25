#!/usr/bin/env python3
"""Read-only checkpoint funnel. Counts snapshots, never claims historical event totals."""
import argparse
import collections
import json
from pathlib import Path


def report(path, days=7):
    bundle = json.loads(Path(path).read_text())
    state = bundle['state']; end = bundle['saved_at']; start = end - days * 86400
    pending = [d for d in state.get('pending', {}).values() if start <= d.get('fetched_ts', 0) <= end]
    assessments = [v for v in state.get('assessment_cache', {}).values() if start <= v.get('at', 0) <= end]
    def count(rows, key):
        return dict(collections.Counter(str(key(d) or 'unknown') for d in rows).most_common())
    return {'window': {'start': start, 'end': end, 'days': days, 'anchor': 'checkpoint.saved_at'},
            'pending_snapshot': len(pending), 'latest_assessments': len(assessments),
            'pending_by_source': count(pending, lambda d:d.get('source_id')),
            'pending_by_category': count(pending, lambda d:d.get('cat')),
            'pending_by_store': count(pending, lambda d:d.get('store')),
            'pending_by_reason': count(pending, lambda d:d.get('_queue', {}).get('reason', d.get('issue'))),
            'assessment_by_status': count(assessments, lambda d:d.get('status')),
            'assessment_by_reason': count(assessments, lambda d:d.get('reason')),
            'source_status': count(state.get('sources', {}).values(), lambda d: 'error' if d.get('errors') else 'no_recorded_error'),
            'publication_records_in_window': sum(start <= v.get('at', 0) <= end for v in state.get('publication_history', {}).values()),
            'limitations': ['Latest snapshots only: not total arrivals, decisions or sends.',
                            'Assessment records lack source/category/store, so those breakdowns apply only to surviving pending.',
                            'Removed terminal candidates and original observation times cannot be reconstructed.']}

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',default='data/checkpoint.json');p.add_argument('--days',type=int,default=7);p.add_argument('--output')
    a=p.parse_args();r=report(a.checkpoint,a.days);text=json.dumps(r,ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(text+'\n')
    print(json.dumps({k:r[k] for k in ['window','pending_snapshot','latest_assessments','publication_records_in_window','assessment_by_status']},ensure_ascii=False))
