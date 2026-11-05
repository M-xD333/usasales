import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from workflow_git import RunnerGit


def git(root, *args, check=True):
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=check)


class RunnerSyncTests(unittest.TestCase):
    def setUp(self):
        sleep_patch = mock.patch("workflow_git.time.sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="deals-git-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote, self.editor, self.runner = root / "remote.git", root / "editor", root / "runner"
        git(root, "init", "--bare", "--initial-branch=main", str(self.remote))
        git(root, "clone", str(self.remote), str(self.editor))
        for name, value in (("user.name", "Test"), ("user.email", "test@example.test")):
            git(self.editor, "config", name, value)
        (self.editor / "data").mkdir()
        (self.editor / "data/state.json").write_text('{"posted": {"first": 1}}')
        (self.editor / "bot.py").write_text("# old code\n")
        git(self.editor, "add", ".")
        git(self.editor, "commit", "-m", "initial")
        git(self.editor, "push", "origin", "main")
        git(root, "clone", str(self.remote), str(self.runner))
        for name, value in (("user.name", "Test"), ("user.email", "test@example.test")):
            git(self.runner, "config", name, value)
        self.sync = RunnerGit(self.runner, "main")

    def edit_remote(self, name="bot.py", content="# new code\n"):
        git(self.editor, "pull", "--ff-only")
        (self.editor / name).write_text(content)
        git(self.editor, "add", name)
        git(self.editor, "commit", "-m", "remote update")
        git(self.editor, "push", "origin", "main")

    def remote_file(self, name):
        return git(self.remote, "show", "main:" + name).stdout

    def test_state_push_preserves_new_code_and_sync_preserves_local_state(self):
        state = '{"posted": {"first": 1, "second": 2}}'
        (self.runner / "data/state.json").write_text(state)
        self.edit_remote()
        self.sync.push()
        self.assertEqual(self.remote_file("bot.py"), "# new code\n")
        self.assertEqual(self.remote_file("data/state.json"), state)
        self.sync.sync()
        self.assertEqual((self.runner / "bot.py").read_text(), "# new code\n")
        self.assertEqual((self.runner / "data/state.json").read_text(), state)
        self.assertFalse((self.runner / ".git/rebase-merge").exists())
        head = git(self.remote, "rev-parse", "main").stdout
        self.sync.push()
        self.assertEqual(git(self.remote, "rev-parse", "main").stdout, head)

    def test_push_race_retries_on_latest_remote_tree(self):
        (self.runner / "data/state.json").write_text('{"posted": {"new": 2}}')
        real_git, raced = self.sync.git, []
        def racing_git(*args, **kw):
            if args[0] == "push" and not raced:
                raced.append(True)
                self.edit_remote(content="racing update\n")
            return real_git(*args, **kw)
        with mock.patch.object(self.sync, "git", side_effect=racing_git):
            self.sync.push()
        self.assertEqual(self.remote_file("bot.py"), "racing update\n")
        self.assertIn('"new"', self.remote_file("data/state.json"))

    def test_unfinished_rebase_is_aborted_without_losing_data(self):
        (self.runner / "data/state.json").write_text('{"posted": {"local": 2}}')
        git(self.runner, "add", "data/")
        git(self.runner, "commit", "-m", "old state commit")
        self.edit_remote("data/state.json", '{"posted": {"remote": 3}}')
        git(self.runner, "fetch", "origin")
        result = git(self.runner, "rebase", "origin/main", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.runner / ".git/rebase-merge").exists())
        # A valid most-recent bot checkpoint exists despite the interrupted rebase.
        checkpoint = '{"posted": {"local": 2, "latest": 4}}'
        (self.runner / "data/state.json").write_text(checkpoint)
        self.sync.sync()
        self.assertFalse((self.runner / ".git/rebase-merge").exists())
        merged = json.loads((self.runner / "data/state.json").read_text())
        self.assertEqual(merged['posted'], {'remote': 3, 'local': 2, 'latest': 4})
        self.sync.push()
        self.assertEqual(json.loads(self.remote_file("data/state.json")), merged)

    def test_invalid_json_is_never_pushed_or_reset(self):
        before = git(self.remote, "rev-parse", "main").stdout
        (self.runner / "data/state.json").write_text("<<<<<<< conflict")
        with self.assertRaises(json.JSONDecodeError):
            self.sync.push()
        with self.assertRaises(json.JSONDecodeError):
            self.sync.sync()
        self.assertEqual(git(self.remote, "rev-parse", "main").stdout, before)
        self.assertEqual((self.runner / "data/state.json").read_text(), "<<<<<<< conflict")

    def test_persistent_push_failure_does_not_leave_rebase(self):
        (self.runner / "data/state.json").write_text('{"posted": {"new": 2}}')
        real_git = self.sync.git
        def failing_git(*args, **kwargs):
            if args[0] == "push":
                return subprocess.CompletedProcess(args, 1, "", "denied")
            return real_git(*args, **kwargs)
        with mock.patch.object(self.sync, "git", side_effect=failing_git):
            with self.assertRaises(RuntimeError):
                self.sync.push()
        self.assertFalse((self.runner / ".git/rebase-merge").exists())
        self.assertIn('"new"', (self.runner / "data/state.json").read_text())

    def test_push_logs_reason_without_credentials_and_recovers(self):
        (self.runner / "data/state.json").write_text('{"posted": {"new": 2}}')
        real_git, attempts = self.sync.git, []
        def transient_git(*args, **kwargs):
            if args[0] == "push" and not attempts:
                attempts.append(1)
                return subprocess.CompletedProcess(args, 1, "", "remote: HTTP 503 https://user:secret@github.com/x")
            return real_git(*args, **kwargs)
        with mock.patch.object(self.sync, "git", side_effect=transient_git), mock.patch("builtins.print") as log:
            self.sync.push()
        text = str(log.call_args_list)
        self.assertIn("HTTP 503", text)
        self.assertNotIn("user:secret", text)
        self.assertIn('"new"', self.remote_file("data/state.json"))

    def test_recovery_restores_only_newer_valid_checkpoint(self):
        artifact = {"id": 1, "name": "deals-state-99", "created_at": "2026-11-04T12:00:00Z",
                    "workflow_run": {"id": 99, "head_branch": "main"}}
        commits = [{"commit": {"committer": {"date": "2026-11-04T11:59:00Z"}}}]
        checkpoint = {"posted": {"delivered": 1}, "processed": {}, "pending": {}}
        def gh(args, **kwargs):
            if "download" in args:
                target = Path(args[args.index("--dir") + 1])
                (target / "state.json").write_text(json.dumps(checkpoint))
                (target / "history.json").write_text('{}')
                return ""
            return json.dumps({"artifacts": [artifact]} if "artifacts" in args[-1] else commits)
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "test/repo"}), mock.patch("workflow_git.subprocess.check_output", side_effect=gh):
            self.sync.restore_checkpoint()
        self.assertEqual(json.loads((self.runner / "data/state.json").read_text())["posted"], {"first": 1, "delivered": 1})
        # A later successful save must not be replaced by the old failed-run artifact.
        commits[0]["commit"]["committer"]["date"] = "2026-11-04T12:01:00Z"
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "test/repo"}), mock.patch("workflow_git.subprocess.check_output", side_effect=gh) as calls:
            self.sync.restore_checkpoint()
        self.assertEqual(calls.call_count, 2)

    def test_incomplete_checkpoint_does_not_overwrite_state(self):
        original = (self.runner / "data/state.json").read_text()
        artifact = {"name": "deals-state-99", "created_at": "2026-11-04T12:00:00Z",
                    "workflow_run": {"id": 99, "head_branch": "main"}}
        def gh(args, **kwargs):
            if "download" in args:
                return ""
            return json.dumps({"artifacts": [artifact]} if "artifacts" in args[-1] else [])
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "test/repo"}), mock.patch("workflow_git.subprocess.check_output", side_effect=gh):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                self.sync.restore_checkpoint()
        self.assertEqual((self.runner / "data/state.json").read_text(), original)

    def test_concurrent_delivery_records_survive_push_race(self):
        (self.runner / "data/state.json").write_text('{"posted":{"local":2}}')
        real_git, raced = self.sync.git, []
        def racing_git(*args, **kwargs):
            if args[0] == 'push' and not raced:
                raced.append(True)
                self.edit_remote('data/state.json', '{"posted":{"remote":3}}')
            return real_git(*args, **kwargs)
        with mock.patch.object(self.sync, 'git', side_effect=racing_git):
            self.sync.push()
        self.assertEqual(json.loads(self.remote_file('data/state.json'))['posted'], {'remote':3, 'local':2})

    def test_concurrent_history_only_update_is_merged(self):
        (self.runner / 'data/history.json').write_text('{"p":[[1,100,"a","s"]]}')
        self.edit_remote('data/history.json', '{"p":[[2,80,"b","s"]]}')
        self.sync.push()
        self.assertEqual(len(json.loads(self.remote_file('data/history.json'))['p']), 2)

    def test_canonical_snapshot_repairs_interrupted_exports(self):
        import checkpoints as C
        store=C.Store(self.runner / 'data')
        store.save({'posted':{'delivered':5}}, {'p':[[1,80,'a','s']]})
        (self.runner / 'data/state.json').write_text('null')
        (self.runner / 'data/history.json').write_text('broken')
        self.sync.push()
        bundle=json.loads(self.remote_file('data/checkpoint.json'))
        self.assertEqual(json.loads(self.remote_file('data/state.json')), bundle['state'])
        self.assertEqual(json.loads(self.remote_file('data/history.json')), bundle['history'])

    def test_restore_artifact_with_only_atomic_bundle(self):
        artifact={'name':'deals-state-99','created_at':'2026-11-04T12:00:00Z',
                  'workflow_run':{'id':99,'head_branch':'main'}}
        bundle={'generation_id':10,'state':{'posted':{'delivered':5}},'history':{}}
        def gh(args, **kwargs):
            if 'download' in args:
                target=Path(args[args.index('--dir')+1])
                (target/'checkpoint.json').write_text(json.dumps(bundle))
                return ''
            return json.dumps({'artifacts':[artifact]} if 'artifacts' in args[-1] else [])
        with mock.patch.dict('os.environ',{'GITHUB_REPOSITORY':'test/repo'}), mock.patch('workflow_git.subprocess.check_output',side_effect=gh):
            self.sync.restore_checkpoint()
        import checkpoints as C
        restored=C.Store(self.runner/'data');st,hist=restored.load()
        self.assertEqual(st['posted'],{'first':1,'delivered':5})
        self.assertGreater(restored.generation,10)

    def test_broken_remote_code_does_not_replace_working_code_or_state(self):
        before = git(self.runner, "rev-parse", "HEAD").stdout
        checkpoint = '{"posted": {"latest": 4}}'
        (self.runner / "data/state.json").write_text(checkpoint)
        self.edit_remote("sources.py", 'url = "https://store.example/… tokens truncated…')
        with self.assertRaises(SyntaxError):
            self.sync.sync()
        self.assertEqual(git(self.runner, "rev-parse", "HEAD").stdout, before)
        self.assertEqual((self.runner / "bot.py").read_text(), "# old code\n")
        self.assertEqual((self.runner / "data/state.json").read_text(), checkpoint)


if __name__ == "__main__":
    unittest.main()
