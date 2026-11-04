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
        self.assertEqual((self.runner / "data/state.json").read_text(), checkpoint)
        self.sync.push()
        self.assertEqual(self.remote_file("data/state.json"), checkpoint)

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
