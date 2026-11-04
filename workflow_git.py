"""Git synchronization for the disposable GitHub Actions checkout only."""
import argparse
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import tempfile
import time


def safe_error(value):
    value = re.sub(r"https?://[^\s]+", "[remote URL]", str(value))
    value = re.sub(r"(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)", "***", value)
    return value[-2000:]


class RunnerGit:
    def __init__(self, root, branch):
        self.root, self.branch = Path(root), branch
        self.git("check-ref-format", "refs/heads/" + branch)
        self.remote = "refs/remotes/origin/" + branch

    def git(self, *args, env=None, check=True):
        return subprocess.run(["git", *args], cwd=self.root,
                              env={**os.environ, **(env or {})}, check=check,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)

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
        last_error = "unknown push failure"
        for attempt in range(6):
            try:
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
                result = self.git("push", "origin", commit + ":refs/heads/" + self.branch, check=False)
                if result.returncode == 0:
                    print("State saved on latest remote commit.")
                    return
                last_error = safe_error(result.stderr or result.stdout or f"exit {result.returncode}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as ex:
                last_error = safe_error(ex.stderr or ex.stdout or type(ex).__name__)
            print(f"State push attempt {attempt + 1}/6 failed: {last_error}", flush=True)
            if attempt < 5:
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("state push failed after 6 attempts; checkpoint required before further publications: " + last_error)

    def restore_checkpoint(self):
        """Recover a newer failed-run checkpoint before any Telegram publications."""
        repo = os.environ["GITHUB_REPOSITORY"]
        def gh(*args):
            return subprocess.check_output(["gh", *args], cwd=self.root, text=True, timeout=120)
        from datetime import datetime
        from urllib.parse import quote
        artifacts = json.loads(gh("api", f"repos/{repo}/actions/artifacts?per_page=100"))["artifacts"]
        artifacts = [a for a in artifacts if not a.get("expired")
                     and a["name"] == "deals-state-" + str(a.get("workflow_run", {}).get("id"))
                     and a.get("workflow_run", {}).get("head_branch") == self.branch]
        if not artifacts:
            print("No recovery checkpoint required.")
            return
        latest = max(artifacts, key=lambda a: a["created_at"])
        commits = json.loads(gh("api", f"repos/{repo}/commits?sha={quote(self.branch, safe='')}&path=data/state.json&per_page=1"))
        stamp = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if commits and stamp(latest["created_at"]) <= stamp(commits[0]["commit"]["committer"]["date"]):
            print("Repository state is newer than the recovery checkpoint.")
            return
        with tempfile.TemporaryDirectory(prefix="deals-recovery-") as tmp:
            gh("run", "download", str(latest["workflow_run"]["id"]), "--repo", repo,
               "--name", latest["name"], "--dir", tmp)
            recovered = {}
            for name in ("state.json", "history.json", "seen.json"):
                path = Path(tmp) / name
                if not path.exists():
                    path = Path(tmp) / "data" / name
                if path.exists():
                    if path.is_symlink() or path.stat().st_size > 20_000_000:
                        raise ValueError("invalid checkpoint file: " + name)
                    value = path.read_bytes()
                    parsed = json.loads(value)
                    if not isinstance(parsed, dict):
                        raise ValueError("invalid checkpoint JSON: " + name)
                    if name == "state.json" and not all(isinstance(parsed.get(k), dict) for k in ("posted", "processed", "pending")):
                        raise ValueError("invalid state checkpoint structure")
                    recovered["data/" + name] = value
            if not {"data/state.json", "data/history.json"} <= recovered.keys():
                raise ValueError("incomplete recovery checkpoint; stopping before publication")
            self.restore(recovered)
        print("Recovered newer state from " + latest["name"] + ".")

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
            self.validate_code(head)
            # This reset is restricted by main() to an ephemeral Actions runner.
            self.git("reset", "--hard", head)
        finally:
            self.restore(files)
        shutil.rmtree(backup)
        print("Code synchronized; local data preserved.")

    def validate_code(self, head):
        """Reject a broken remote tree before replacing the working checkout."""
        names = self.git("ls-tree", "-r", "--name-only", "-z", head).stdout.split("\0")
        for name in names:
            if name.endswith(".py"):
                source = self.git("show", head + ":" + name).stdout
                compile(source, name, "exec")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("push", "sync", "restore-checkpoint"))
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
    getattr(runner, args.action.replace("-", "_"))()


if __name__ == "__main__":
    main()
