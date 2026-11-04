"""Execute the actual chain script with a local gh stub, without GitHub requests."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


class ChainControl(unittest.TestCase):
    def run_chain(self, status, attempt, gh_failure=False):
        workflow = (Path(__file__).parent.parent / '.github/workflows/deals.yml').read_text()
        script = textwrap.dedent(workflow.split('      - name: Chain next run', 1)[1].split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gh = root / 'gh'
            gh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit ' + ('1' if gh_failure else '0') + '\n')
            gh.chmod(0o755)
            sleep = root / 'sleep'
            sleep.write_text('#!/bin/sh\nexit 0\n')
            sleep.chmod(0o755)
            log = root / 'calls'
            result = subprocess.run(['bash', '-c', script], env={**os.environ, 'PATH': temp + ':' + os.environ['PATH'],
                                    'CALLS': str(log), 'PREVIOUS_STATUS': status,
                                    'RECOVERY_ATTEMPT': str(attempt), 'REF': 'main'}, capture_output=True, text=True)
            return result, log.read_text() if log.exists() else ''

    def test_failed_run_dispatches_next_recovery_attempt(self):
        result, calls = self.run_chain('failure', 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('recovery_attempt=1', calls)
        self.assertIn('dry_run=false', calls)

    def test_repeated_failure_stops_after_two_restarts(self):
        result, calls = self.run_chain('failure', 2)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')

    def test_success_resets_recovery_counter(self):
        result, calls = self.run_chain('success', 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('recovery_attempt=0', calls)

    def test_dispatch_failure_is_retried_then_reported(self):
        result, calls = self.run_chain('failure', 0, True)
        self.assertEqual(len(calls.splitlines()), 3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Failed to dispatch', result.stdout)
