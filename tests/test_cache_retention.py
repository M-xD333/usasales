import copy
import gzip
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cache_retention as R
import checkpoints as C
import state_merge


class CompactionTests(unittest.TestCase):
    def test_ttls_caps_and_delivery_facts_unchanged(self):
        now=time.time()
        st={'assessment_cache':{str(i):{'at':now-i,'reason':'x'*250} for i in range(60000)},
            'price_events':{str(i):{'observed_at':now-i,'price':100} for i in range(60000)},
            'posted':{'delivered':1},'publication_history':{'floor':{'lowest_cents':100,'at':1,'identities':[]}},
            'last_delivery':{'at':1,'kind':'photo','message_id':2},'processed':{'p':1},'pending':{'never-drop':{}}}
        facts=copy.deepcopy({k:v for k,v in st.items() if k not in ('assessment_cache','price_events')})
        assessments=st['assessment_cache']
        self.assertTrue(R.compact(st,now))
        self.assertIs(st['assessment_cache'],assessments)
        self.assertEqual(len(st['assessment_cache']),3000)
        self.assertEqual(len(st['price_events']),3000)
        self.assertEqual({k:st[k] for k in facts},facts)
        self.assertLess(len(json.dumps({'state':st,'history':{}}).encode()),8_000_000)
        self.assertFalse(R.compact(st,now))

    def test_independent_ttls_with_max_age_extension(self):
        now=time.time()
        st={'assessment_cache':{'49h':{'at':now-49*3600},'5d':{'at':now-5*86400}},
            'price_events':{'13d':{'observed_at':now-13*86400},'15d':{'observed_at':now-15*86400}}}
        R.compact(st,now,max_age_h=48)
        self.assertEqual(set(st['assessment_cache']),{'49h'})
        self.assertEqual(set(st['price_events']),{'13d'})

    def test_load_archives_original_and_is_idempotent_across_restart(self):
        now=time.time();original={'posted':{'sent':now},'publication_history':{'floor':{'lowest_cents':8000,'at':now,'identities':[]}},
            'assessment_cache':{'stale':{'at':now-3*86400}},'price_events':{},'pending':{}}
        with tempfile.TemporaryDirectory() as root:
            C.Store(root).save(original,{})
            store=C.Store(root);st,hist=store.load()
            self.assertFalse(st['assessment_cache'])
            archive=list(Path(root,'migrations').glob('*.json.gz'))
            self.assertEqual(len(archive),1)
            with gzip.open(archive[0],'rt') as f:old=json.load(f)
            self.assertEqual(old['state'],original)
            generation=store.generation
            again=C.Store(root);restored,_=again.load()
            self.assertEqual(again.generation,generation)
            self.assertEqual(restored['posted'],original['posted'])
            self.assertEqual(restored['publication_history'],original['publication_history'])

    def test_archive_failure_never_replaces_canonical_state(self):
        with tempfile.TemporaryDirectory() as root:
            C.Store(root).save({'assessment_cache':{'old':{'at':0}}},{})
            before=Path(root,C.FILE).read_bytes()
            with patch('checkpoints.gzip.GzipFile',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):C.Store(root).load()
            self.assertEqual(Path(root,C.FILE).read_bytes(),before)

    def test_merge_cannot_resurrect_stale_cache(self):
        now=time.time()
        remote={'posted':{'remote':1},'assessment_cache':{'old':{'at':now-5*86400}},'price_events':{}}
        local={'posted':{'local':2},'assessment_cache':{'fresh':{'at':now}},'price_events':{}}
        merged=state_merge.merge_state(remote,local)
        self.assertEqual(set(merged['assessment_cache']),{'fresh'})
        self.assertEqual(merged['posted'],{'remote':1,'local':2})
