"""Verify this Pi's own account password with PAM, without saving it."""
import os
from pathlib import Path
import secrets
import threading
import time


class PiPasswordAuth:
    def __init__(self, username):
        import pam
        import pwd
        if pwd.getpwuid(os.getuid()).pw_name != username or username == "root":
            raise RuntimeError("Counter must run as the non-root Pi account used for login.")
        self.username = username
        self._authenticate = pam.authenticate

    def verify(self, password):
        if not isinstance(password, str) or not password or "\0" in password or len(password) > 1024:
            return False
        return bool(self._authenticate(self.username, password, service="counter", resetcreds=False))


class LoginThrottle:
    """Bound login attempts for this single Pi account, across all clients."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.attempts = []

    def reserve(self):
        with self.lock:
            now = self.clock()
            self.attempts = [at for at in self.attempts if now - at < 60]
            if len(self.attempts) >= 5:
                return max(1, int(60 - (now - self.attempts[0])) + 1)
            self.attempts.append(now)
            return 0


def session_key(path):
    """Keep the signing key outside releases, readable only by the Pi user."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
    if len(key) != 32:
        raise RuntimeError("Counter session key is invalid; refusing to start without authentication.")
    return key
