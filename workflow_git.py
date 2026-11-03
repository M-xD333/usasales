"""Git synchronization for the disposable GitHub Actions checkout only."""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile


class RunnerGit:
    def __init__(self, root, branch):
        self.root, self.branch = Path(root), branch
        self.git("check-ref-format", "refs/heads/" + branch)
        self.remote = "refs/remotes/origin/" + branch

    def git(self, *args, env=None, check=True):
        return subprocess.run(["git", *args], cwd=self.root,
                              env={**os.environ, **(env or {})}, check=check,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def fetch(self):
        self.git("fetch", "--no-tags", "origin", "+refs/heads/" + self.branch + ":" + self.remote)
        return self.git("rev-parse", self.remote).stdout.strip()

    def snapshot(self):
        data = self.root / "data"
        files = {}
        for path in sorted(data.rglob("*")) if data.exists() else []:
            if not path.is_file() or path.is_symlink() or path.suffix == ".tmp":
                continue
            value = path.read_bytes()
            if path.suffix == ".json":
                # Never persist conflict markers or replace broken state with empty defaults.
                json.loads(value)
            files[path.relative_to(self.root).as_posix()] = value
        return files

    def restore(self, files):
        for name, value in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)

    def recover(self):
        for name in ("rebase-merge", "rebase-apply"):
            path = Path(self.git("rev-parse", "--git-path", name).stdout.strip())
            if not path.is_absolute():
                path = self.root / path
            if path.exists():
                self.git("rebase", "--abort")
                break
        if self.git("rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
            self.git("merge", "--abort")

    def push(self):
        files = self.snapshot()
        if not files:
            return
        # A temporary index builds a state commit on the current remote code tree.
        # No rebase/merge is started and the working checkout is never switched here.
        for _ in range(3):
            parent = self.fetch()
            with tempfile.TemporaryDirectory(prefix="deals-git-index-") as tmp:
                env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
                self.git("read-tree", parent, env=env)
                for name, value in files.items():
                    # Hash the captured snapshot, not files that another process could modify.
                    local = Path(tmp) / "blob"
                    local.write_bytes(value)
                    blob = self.git("hash-object", "-w", str(local)).stdout.strip()
                    self.git("update-index", "--add", "--cacheinfo", "100644", blob, name, env=env)
                tree = self.git("write-tree", env=env).stdout.strip()
            if tree == self.git("rev-parse", parent + "^{tree}").stdout.strip():
                return
            commit = self.git("commit-tree", tree, "-p", parent, "-m", "chore: persist deals-bot state").stdout.strip()
            if self.git("push", "origin", commit + ":refs/heads/" + self.branch, check=False).returncode == 0:
                print("State saved on latest remote commit.")
                return
        raise RuntimeError("state push failed after 3 attempts; stopping before further publications")

    def sync(self):
        files = self.snapshot()
        # Keep a recovery copy outside the checkout before abort/reset.
        backup = Path(tempfile.mkdtemp(prefix="deals-state-backup-"))
        for name, value in files.items():
            path = backup / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        try:
            self.recover()
            head = self.fetch()
            # This reset is restricted by main() to an ephemeral Actions runner.
            self.git("reset", "--hard", head)
        finally:
            self.restore(files)
        shutil.rmtree(backup)
        print("Code synchronized; local data preserved.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("push", "sync"))
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("this helper may only run in the disposable GitHub Actions checkout")
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    if root.resolve() != workspace:
        parser.error("repository does not match GITHUB_WORKSPACE")
    runner = RunnerGit(root, os.environ["GITHUB_REF_NAME"])
    runner.git("config", "user.name", "deals-bot")
    runner.git("config", "user.email", "deals-bot@users.noreply.github.com")
    getattr(runner, args.action)()


if __name__ == "__main__":
    main()
