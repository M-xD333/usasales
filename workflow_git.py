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
import checkpoints
import state_merge


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
        has_bundle = (data / checkpoints.FILE).exists() or (data / checkpoints.BACKUP).exists()
        for path in sorted(data.rglob("*")) if data.exists() else []:
            if not path.is_file() or path.is_symlink() or path.suffix == ".tmp":
                continue
            value = path.read_bytes()
            if path.suffix == ".json" and not (has_bundle and path.name in
                    (checkpoints.FILE, checkpoints.BACKUP, 'state.json', 'history.json')):
                # Never persist conflict markers or replace broken state with empty defaults.
                json.loads(value)
            files[path.relative_to(self.root).as_posix()] = value
        if has_bundle:
            # Compatibility exports may be interrupted; the atomic bundle is authoritative.
            store = checkpoints.Store(data)
            state, history = store.load()
            files['data/' + checkpoints.FILE] = json.dumps(store.previous, ensure_ascii=False).encode()
            if 'data/' + checkpoints.BACKUP in files:
                try:
                    checkpoints.validate_bundle(json.loads(files['data/' + checkpoints.BACKUP]))
                except (ValueError, TypeError):
                    files.pop('data/' + checkpoints.BACKUP)
            files['data/state.json'] = json.dumps(state, ensure_ascii=False).encode()
            files['data/history.json'] = json.dumps(history, ensure_ascii=False).encode()
        else:
            if 'data/state.json' in files:
                checkpoints.validate_state(json.loads(files['data/state.json']))
            if 'data/history.json' in files:
                checkpoints.validate_history(json.loads(files['data/history.json']))
        return files

    def merge_remote_state(self, files, parent):
        def read(ref, name):
            result = self.git('show', ref + ':data/' + name, check=False)
            return json.loads(result.stdout) if result.returncode == 0 else None
        remote_state = read(parent, 'state.json')
        base_state = read('HEAD', 'state.json')
        remote_bundle = read(parent, checkpoints.FILE)
        local_bundle = json.loads(files['data/' + checkpoints.FILE]) if 'data/' + checkpoints.FILE in files else None
        higher_generation = (remote_bundle or {}).get('generation_id', 0) > (local_bundle or {}).get('generation_id', 0)
        remote_history = read(parent, 'history.json')
        base_history = read('HEAD', 'history.json')
        base_bundle = read('HEAD', checkpoints.FILE)
        if (remote_state == base_state and remote_history == base_history
                and remote_bundle == base_bundle and not higher_generation):
            return files
        if remote_state is None and remote_bundle is None:
            return files
        if remote_bundle:
            checkpoints.validate_bundle(remote_bundle)
            remote_state, remote_hist = remote_bundle['state'], remote_bundle['history']
        else:
            remote_hist = read(parent, 'history.json') or {}
        local_state = local_bundle['state'] if local_bundle else json.loads(files['data/state.json'])
        local_hist = local_bundle['history'] if local_bundle else json.loads(files.get('data/history.json',b'{}'))
        if local_state == remote_state and local_hist == remote_hist:
            if higher_generation:
                files = dict(files)
                files['data/' + checkpoints.FILE] = json.dumps(remote_bundle, ensure_ascii=False).encode()
            return files
        merged = state_merge.merge_state(remote_state, local_state)
        history = state_merge.merge_history(remote_hist, local_hist)
        files = dict(files)
        encode = lambda value: json.dumps(value, ensure_ascii=False).encode()
        files['data/state.json'], files['data/history.json'] = encode(merged), encode(history)
        if local_bundle or remote_bundle:
            bundle = {'generation_id': max((local_bundle or {}).get('generation_id',0), (remote_bundle or {}).get('generation_id',0)) + 1,
                      'saved_at': time.time(), 'state': merged, 'history': history}
            files['data/' + checkpoints.FILE] = encode(bundle)
        print('Concurrent remote state merged without overwriting delivery records.')
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
                current_files = self.merge_remote_state(files, parent)
                with tempfile.TemporaryDirectory(prefix="deals-git-index-") as tmp:
                    env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
                    self.git("read-tree", parent, env=env)
                    for name, value in current_files.items():
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
            for name in ("state.json", "history.json", "seen.json", checkpoints.FILE, checkpoints.BACKUP):
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
            if 'data/' + checkpoints.FILE in recovered:
                bundle = checkpoints.validate_bundle(json.loads(recovered['data/' + checkpoints.FILE]))
                recovered['data/state.json'] = json.dumps(bundle['state'], ensure_ascii=False).encode()
                recovered['data/history.json'] = json.dumps(bundle['history'], ensure_ascii=False).encode()
            if not {"data/state.json", "data/history.json"} <= recovered.keys():
                raise ValueError("incomplete recovery checkpoint; stopping before publication")
            local = self.snapshot()
            if 'data/state.json' in local:
                recovered['data/state.json'] = json.dumps(state_merge.merge_state(
                    json.loads(local['data/state.json']), json.loads(recovered['data/state.json'])), ensure_ascii=False).encode()
                recovered['data/history.json'] = json.dumps(state_merge.merge_history(
                    json.loads(local.get('data/history.json', b'{}')), json.loads(recovered['data/history.json'])), ensure_ascii=False).encode()
            if 'data/' + checkpoints.FILE in recovered:
                bundle = json.loads(recovered['data/' + checkpoints.FILE])
                bundle['generation_id'] = max(bundle['generation_id'],
                    json.loads(local.get('data/' + checkpoints.FILE, b'{}')).get('generation_id', 0)) + 1
                bundle['state'], bundle['history'] = json.loads(recovered['data/state.json']), json.loads(recovered['data/history.json'])
                recovered['data/' + checkpoints.FILE] = json.dumps(bundle, ensure_ascii=False).encode()
            elif (self.root / 'data' / checkpoints.FILE).exists():
                # A legacy artifact must not be hidden by an older local canonical bundle.
                store = checkpoints.Store(self.root / 'data')
                store.load()
                store.save(json.loads(recovered['data/state.json']), json.loads(recovered['data/history.json']))
                recovered.pop('data/' + checkpoints.BACKUP, None)
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
            files = self.merge_remote_state(files, head)
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
