import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock

spec=importlib.util.spec_from_file_location('probe',Path(__file__).parents[1]/'tools/probe.py');P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)

class ProbeTests(unittest.TestCase):
    def session(self,status=200,body=b'<rss><channel><item><title>SSD $80</title><link>https://example.com/p</link></item></channel></rss>'):
        session=MagicMock();r=session.get.return_value.__enter__.return_value
        r.status_code=status;r.encoding='utf-8';r.headers={'Content-Type':'application/rss+xml'};r.iter_content.side_effect=lambda n:[body]
        return session
    def test_one_get_per_url_and_at_most_25(self):
        session=self.session();rows=[(str(i),'https://example.com/'+str(i)) for i in range(30)]
        out=P.run(rows+rows,session=session)
        self.assertEqual(len(out),25);self.assertEqual(session.get.call_count,25)
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in session.get.call_args_list))
        self.assertTrue(all(r['usable'] for r in out))
    def test_403_is_not_a_feed_and_never_retried(self):
        session=self.session(403);out=P.run([('blocked','https://example.com')],session=session)
        self.assertFalse(out[0]['usable']);self.assertEqual(session.get.call_count,1)
    def test_mode_probe_is_isolated_from_publishing(self):
        workflow=(Path(__file__).parents[1]/'.github/workflows/deals.yml').read_text()
        self.assertIn("if: inputs.mode != 'probe'",workflow)
        self.assertIn("if: inputs.mode == 'probe'",workflow)
        self.assertIn('DRY_RUN: "1"',workflow)
