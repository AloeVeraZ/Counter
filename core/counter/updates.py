"""Update checks for the two supported Counter release channels."""
import os
from pathlib import Path
import re
import subprocess
from threading import Lock, Thread
import time

REPOSITORY = "https://github.com/AloeVeraZ/Counter.git"
HELPER = Path("/usr/local/sbin/counter-update")
BRANCHES = ("main", "testing")
UPDATE_LOG = Path("/var/log/counter-update.log")


def update_job():
    try:
        lines = UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
    except OSError:
        return {"state": "idle", "log": []}
    if lines and lines[-1].startswith("Counter update completed."):
        state = "finished"
    elif lines and lines[-1].startswith("Counter update failed"):
        state = "failed"
    else:
        try:
            active = subprocess.run(["systemctl", "is-active", "counter-update.service"],
                                    capture_output=True, text=True, timeout=3).stdout.strip()
            state = "running" if active in {"active", "activating"} else "failed"
        except (OSError, subprocess.SubprocessError):
            state = "failed"
    return {"state": state, "log": lines}


class Updates:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("COUNTER_RELEASE", Path(__file__).resolve().parents[2]))
        self.lock = Lock()
        self.checking = False
        self.checked_at = 0
        self.latest = ""
        self.remotes = {}
        self.error = ""
        self.installed = ""
        self.branch = "main"
        try:
            ref = (self.root / "INSTALL_REF").read_text().strip()
            if ref in BRANCHES:
                self.branch = ref
        except OSError:
            pass
        try:
            self.installed = (self.root / "INSTALL_COMMIT").read_text().strip()
        except OSError:
            try:
                self.installed = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass

    def snapshot(self, refresh=False):
        job = update_job()
        with self.lock:
            if not self.checking and (refresh or time.time() - self.checked_at > 900):
                self.checking = True
                Thread(target=self._check, daemon=True, name="counter-updates").start()
            targets = [{"branch": branch, "latest": self.remotes.get(branch, "")[:7],
                        "available": bool(self.remotes.get(branch) and self.installed and
                                          (branch != self.branch or self.remotes[branch] != self.installed))}
                       for branch in BRANCHES]
            return {"installed": self.installed[:7], "branch": self.branch,
                    "latest": self.latest[:7], "targets": targets,
                    "available": next(row["available"] for row in targets if row["branch"] == self.branch),
                    "checking": self.checking, "error": self.error,
                    "installable": HELPER.is_file() and os.access(HELPER, os.X_OK),
                    "checked_at": self.checked_at, "job": job}

    def _check(self):
        remotes, error = {}, ""
        try:
            result = subprocess.run(["git", "ls-remote", "--heads", REPOSITORY,
                                     *[f"refs/heads/{branch}" for branch in BRANCHES]],
                capture_output=True, text=True, timeout=20, check=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
            for line in result.stdout.splitlines():
                match = re.fullmatch(r"([0-9a-f]{40})\s+refs/heads/(main|testing)\s*", line)
                if match:
                    remotes[match[2]] = match[1]
            if not remotes:
                raise ValueError("GitHub did not return a supported branch commit.")
            if self.branch not in remotes:
                error = f"GitHub did not return the {self.branch} branch."
        except (OSError, subprocess.SubprocessError, ValueError) as failure:
            error = f"Could not check GitHub: {str(failure)[:200]}"
        with self.lock:
            self.remotes = remotes
            self.latest = remotes.get(self.branch, "")
            self.error = error
            self.checked_at = time.time()
            self.checking = False

    def start(self, branch=None, *, acknowledge_testing=False):
        branch = self.branch if branch is None else branch
        if branch not in BRANCHES:
            raise ValueError("Choose main or testing.")
        if branch == "testing" and acknowledge_testing is not True:
            raise ValueError("Acknowledge the testing warning before installing testing.")
        if not HELPER.is_file():
            raise ValueError("Install Counter on the Pi with install.sh before using Update now.")
        result = subprocess.run(["sudo", "-n", str(HELPER), branch], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300] or "The update helper refused the update.")
        return f"Update started / {branch}. Outputs are stopped; Counter reconnects after the service restarts."
