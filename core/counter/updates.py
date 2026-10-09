"""Background GitHub update checks and a narrowly scoped Pi updater."""
import os
from pathlib import Path
import re
import subprocess
from threading import Lock, Thread
import time

REPOSITORY = "https://github.com/AloeVeraZ/Counter.git"
HELPER = Path("/usr/local/sbin/counter-update")


class Updates:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("COUNTER_RELEASE", Path(__file__).resolve().parents[2]))
        self.lock = Lock()
        self.checking = False
        self.checked_at = 0
        self.latest = ""
        self.error = ""
        self.installed = ""
        try:
            self.installed = (self.root / "INSTALL_COMMIT").read_text().strip()
        except OSError:
            try:
                self.installed = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass

    def snapshot(self, refresh=False):
        with self.lock:
            if not self.checking and (refresh or time.time() - self.checked_at > 900):
                self.checking = True
                Thread(target=self._check, daemon=True, name="counter-updates").start()
            return {"installed": self.installed[:7], "latest": self.latest[:7],
                    "available": bool(self.latest and self.installed and self.latest != self.installed),
                    "checking": self.checking, "error": self.error,
                    "installable": HELPER.is_file() and os.access(HELPER, os.X_OK),
                    "checked_at": self.checked_at}

    def _check(self):
        latest, error = "", ""
        try:
            result = subprocess.run(["git", "ls-remote", REPOSITORY, "refs/heads/main"],
                capture_output=True, text=True, timeout=20, check=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
            match = re.fullmatch(r"([0-9a-f]{40})\s+refs/heads/main\s*", result.stdout)
            if not match:
                raise ValueError("GitHub did not return a main-branch commit.")
            latest = match[1]
        except (OSError, subprocess.SubprocessError, ValueError) as failure:
            error = f"Could not check GitHub: {str(failure)[:200]}"
        with self.lock:
            self.latest, self.error = latest, error
            self.checked_at = time.time()
            self.checking = False

    def start(self):
        if not HELPER.is_file():
            raise ValueError("Install Counter on the Pi with install.sh before using Update now.")
        result = subprocess.run(["sudo", "-n", str(HELPER)], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300] or "The update helper refused the update.")
        return "Update started. Outputs are stopped; the dashboard reconnects after the service restarts."
