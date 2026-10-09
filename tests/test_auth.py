import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from counter.auth import LoginThrottle, PiPasswordAuth, session_key
from counter.config import ConfigStore
from counter.controller import Counter
from counter.hardware import SimulatedBoard
from counter.web import create_app


class PasswordVerifier:
    username = "pi-owner"
    password = "first-pi-password"

    def verify(self, password):
        return bool(password) and password == self.password


class AuthWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.counter = Counter(ConfigStore(Path(self.temp.name) / "config.json"), SimulatedBoard())
        self.addCleanup(self.counter.close)
        self.verifier = PasswordVerifier()
        self.app = create_app(self.counter, auth=self.verifier, secret_key=b"x" * 32)
        self.app.testing = True
        self.client = self.app.test_client()

    def login(self, password=None, client=None, **kwargs):
        client = client or self.client
        html = client.get("/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', html)[1]
        return client.post("/login", data={"csrf": csrf, "password": password or self.verifier.password}, **kwargs)

    def headers(self):
        html = self.client.get("/").get_data(as_text=True)
        return {"X-Counter-Token": re.search(r'name="counter-token" content="([^"]+)"', html)[1]}

    def test_dashboard_and_every_api_require_login(self):
        self.assertEqual(self.client.get("/").location, "/login")
        for path in ["/api/state", "/api/updates"]:
            self.assertEqual(self.client.get(path).status_code, 401)
        for path in ["/api/arm", "/api/stop", "/api/number", "/api/step", "/api/setup",
                     "/api/calibration", "/api/preview", "/api/updates", "/api/logout"]:
            self.assertEqual(self.client.post(path, json={}).status_code, 401)
        self.assertFalse(self.counter.armed)
        self.assertEqual(set(self.client.get("/health").json), {"installed_commit"})
        with self.client.get("/static/wiring.svg") as response:
            self.assertEqual(response.status_code, 200)

    def test_update_needs_the_pi_password_before_anything_starts(self):
        started = []
        updater = SimpleNamespace(installed="", snapshot=lambda refresh=False: {},
                                  start=lambda branch=None, acknowledge_testing=False: started.append(branch) or "Update started")
        client = create_app(self.counter, updater, auth=self.verifier, secret_key=b"x" * 32).test_client()
        self.login(client=client)
        html = client.get("/").get_data(as_text=True)
        headers = {"X-Counter-Token": re.search(r'name="counter-token" content="([^"]+)"', html)[1]}
        for body in [{"branch": "main"}, {"branch": "main", "password": "wrong"}]:
            response = client.post("/api/updates", json=body, headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertTrue(response.json["password_required"])
        self.assertEqual(started, [])
        response = client.post("/api/updates", json={"branch": "main", "password": self.verifier.password}, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(started, ["main"])

    def test_password_only_login_never_enables_motion(self):
        page = self.client.get("/login").get_data(as_text=True)
        self.assertIn('name="password" type="password"', page)
        self.assertNotIn('type="text"', page)
        self.assertEqual(self.login("wrong").status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 302)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(self.verifier.password, cookie)
        with self.client.session_transaction() as session:
            self.assertNotIn("password", session)
            self.assertNotIn("login_csrf", session)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertFalse(self.client.get("/api/state").json["armed"])
        headers = self.headers()
        self.assertEqual(self.client.post("/api/setup", json={"count": 1}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/calibration", json={"channel": 0, "positions": [1500] * 10}, headers=headers).status_code, 200)

    def test_password_change_and_different_pi_passwords(self):
        self.assertEqual(self.login("first-pi-password").status_code, 302)
        self.client.post("/api/logout", json={}, headers=self.headers())
        self.verifier.password = "new-pi-password"
        self.assertEqual(self.login("first-pi-password").status_code, 401)
        self.assertEqual(self.login("new-pi-password").status_code, 302)
        other_pi = PasswordVerifier()
        other_pi.password = "different-pi-password"
        other_app = create_app(self.counter, auth=other_pi, secret_key=b"y" * 32)
        other_client = other_app.test_client()
        html = other_client.get("/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', html)[1]
        self.assertEqual(other_client.post("/login", data={"csrf": csrf, "password": "new-pi-password"}).status_code, 401)

    def test_login_requires_csrf_and_same_origin(self):
        self.assertEqual(self.client.post("/login", data={"password": self.verifier.password}).status_code, 403)
        self.assertEqual(self.login(headers={"Origin": "http://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.post("/login", data={"csrf": "☃", "password": self.verifier.password}).status_code, 403)
        self.assertEqual(self.login(headers={"Origin": "http://localhost"}).status_code, 302)
        self.assertEqual(self.client.post("/api/arm", json={}).status_code, 403)

    def test_opening_login_in_another_tab_preserves_the_first_form(self):
        html = self.client.get("/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', html)[1]
        self.client.get("/login")
        response = self.client.post("/login", data={"csrf": csrf, "password": self.verifier.password})
        self.assertEqual(response.status_code, 302)

    def test_logout_stops_outputs_and_locks_dashboard(self):
        self.login()
        headers = self.headers()
        self.client.post("/api/arm", json={}, headers=headers)
        self.assertTrue(self.counter.armed)
        self.assertEqual(self.client.post("/api/logout", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/logout", json={}, headers=headers).status_code, 200)
        self.assertFalse(self.counter.armed)
        self.assertEqual(self.client.get("/api/state").status_code, 401)

    def test_failed_auth_backend_does_not_unlock_dashboard(self):
        with patch.object(self.verifier, "verify", side_effect=RuntimeError("PAM unavailable")):
            self.assertEqual(self.login().status_code, 503)
        self.assertEqual(self.client.get("/api/state").status_code, 401)

    def test_rate_limit_cannot_be_bypassed_by_new_browser(self):
        for _ in range(5):
            self.assertEqual(self.login("wrong").status_code, 401)
        response = self.login(client=self.app.test_client())
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)

    def test_tampered_and_expired_sessions_are_rejected(self):
        self.login()
        cookie = self.client.get_cookie("session").value
        self.client.set_cookie("session", cookie + "tampered")
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        self.client.delete_cookie("session")
        self.login()
        import time
        with patch("itsdangerous.timed.time.time", return_value=time.time() + 8 * 3600 + 1):
            self.assertEqual(self.client.get("/api/state").status_code, 401)

    def test_hardware_and_authenticated_apps_cannot_start_unprotected(self):
        with self.assertRaises(ValueError):
            create_app(self.counter, auth=self.verifier)
        self.counter.board.simulated = False
        with self.assertRaisesRegex(ValueError, "Hardware mode requires"):
            create_app(self.counter)


class PiAuthenticationTests(unittest.TestCase):
    def test_uses_live_pam_for_the_current_pi_user(self):
        verify = Mock(side_effect=[False, True])
        with patch("counter.auth.os.getuid", return_value=1000, create=True), patch.dict(sys.modules, {"pam": SimpleNamespace(authenticate=verify),
                                     "pwd": SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(pw_name="owner"))}):
            auth = PiPasswordAuth("owner")
            self.assertFalse(auth.verify("old-password"))
            self.assertTrue(auth.verify("new-password"))
            verify.assert_called_with("owner", "new-password", service="counter", resetcreds=False)
            for invalid in ["", "x\0y", "x" * 1025, None]:
                self.assertFalse(auth.verify(invalid))
            self.assertEqual(verify.call_count, 2)
            with self.assertRaises(RuntimeError):
                PiPasswordAuth("some-other-account")

    def test_throttle_recovers_after_window(self):
        now = [100.0]
        throttle = LoginThrottle(lambda: now[0])
        for _ in range(5):
            self.assertEqual(throttle.reserve(), 0)
        self.assertGreater(throttle.reserve(), 0)
        now[0] += 61
        self.assertEqual(throttle.reserve(), 0)

    def test_session_key_survives_updates_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.key"
            key = session_key(path)
            self.assertEqual(len(key), 32)
            self.assertEqual(key, session_key(path))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_bytes(b"broken")
            with self.assertRaises(RuntimeError):
                session_key(path)
