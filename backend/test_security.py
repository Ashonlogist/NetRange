"""
Tests for startup refusal and dashboard auth.

Covers the fail-closed env handling, the username check that was missing, and
the constant-time comparison. The startup cases run app.py in a subprocess
because the check fires at import time -- once a module is imported, its env
snapshot cannot be changed, so testing it in-process would not test anything.
"""

import os
import subprocess
import sys
import unittest

import tests_env  # noqa: F401  (must precede `import app`)

import app as appmod  # noqa: E402
import db  # noqa: E402
import device_auth  # noqa: E402
from test_device_auth import _FakeClient  # noqa: E402

BACKEND = os.path.dirname(os.path.abspath(__file__))


def _import_app_with(env_overrides):
    """Import app in a fresh interpreter; return (ok, message)."""
    env = dict(os.environ)
    for key in ("DASHBOARD_SECRET", "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD"):
        env.pop(key, None)
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        env.setdefault(key, "https://stub.supabase.co")
        env[key] = "stub-service-key"
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60,
    )
    return proc.returncode == 0, (proc.stderr or proc.stdout)


class StartupRefusalTests(unittest.TestCase):
    """app.py must not come up without its credentials."""

    def _assert_refuses(self, env, missing):
        ok, out = _import_app_with(env)
        self.assertFalse(ok, f"app imported despite {missing} being unset")
        self.assertIn(missing, out)
        self.assertIn("RuntimeError", out)

    def test_refuses_without_any_credential(self):
        self._assert_refuses({}, "DASHBOARD_SECRET")

    def test_refuses_without_username(self):
        self._assert_refuses(
            {"DASHBOARD_SECRET": "s", "DASHBOARD_PASSWORD": "p"}, "DASHBOARD_USERNAME"
        )

    def test_refuses_without_password(self):
        self._assert_refuses(
            {"DASHBOARD_SECRET": "s", "DASHBOARD_USERNAME": "u"}, "DASHBOARD_PASSWORD"
        )

    def test_refuses_on_whitespace_only_value(self):
        self._assert_refuses(
            {"DASHBOARD_SECRET": "s", "DASHBOARD_USERNAME": "u",
             "DASHBOARD_PASSWORD": "   "},
            "DASHBOARD_PASSWORD",
        )

    def test_imports_when_all_present(self):
        ok, out = _import_app_with({
            "DASHBOARD_SECRET": "s", "DASHBOARD_USERNAME": "u", "DASHBOARD_PASSWORD": "p",
        })
        self.assertTrue(ok, out)

    def test_error_names_how_to_fix_it(self):
        _, out = _import_app_with({})
        self.assertIn("Render", out)


class DashboardAuthTests(unittest.TestCase):
    """Both halves of the Basic credential are required, and compared safely."""

    def setUp(self):
        self.fake = _FakeClient()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        self.client = appmod.app.test_client()
        self.user = appmod.DASHBOARD_USERNAME
        self.pw = appmod.DASHBOARD_PASS

    def _auth(self, user, pw):
        import base64
        raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}

    def _cleanup(self):
        return self.client.post("/api/cleanup", json={"days": 99999},
                                headers=self._auth(self.user, self.pw))

    def test_correct_username_and_password_allowed(self):
        # Must actually reach the handler, not merely avoid a 401.
        r = self._cleanup()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_missing_credentials_rejected(self):
        self.assertEqual(
            self.client.post("/api/cleanup", json={"days": 1}).status_code, 401
        )

    def test_wrong_password_rejected(self):
        r = self.client.post("/api/cleanup", json={"days": 1},
                             headers=self._auth(self.user, self.pw + "x"))
        self.assertEqual(r.status_code, 401)

    def test_wrong_username_rejected(self):
        # This is the case that used to pass: only the password was checked.
        r = self.client.post("/api/cleanup", json={"days": 1},
                             headers=self._auth("attacker", self.pw))
        self.assertEqual(r.status_code, 401)

    def test_empty_username_rejected(self):
        r = self.client.post("/api/cleanup", json={"days": 1},
                             headers=self._auth("", self.pw))
        self.assertEqual(r.status_code, 401)

    def test_dashboard_route_also_requires_username(self):
        r = self.client.get("/dashboard", headers=self._auth("attacker", self.pw))
        self.assertEqual(r.status_code, 401)

    def test_challenge_header_present_on_401(self):
        r = self.client.post("/api/cleanup", json={"days": 1})
        self.assertIn("Basic", r.headers.get("WWW-Authenticate", ""))

    def test_non_ascii_secret_compares_without_raising(self):
        # compare_digest raises TypeError on non-ASCII str, which would turn a
        # wrong password into a 500 instead of a 401.
        self.assertTrue(appmod._constant_time_equals("clé", "clé"))
        self.assertFalse(appmod._constant_time_equals("clé", "autre"))
        self.assertFalse(appmod._constant_time_equals("clé", None))

    def test_unequal_lengths_compare_false(self):
        self.assertFalse(appmod._constant_time_equals("a", "ab"))
        self.assertFalse(appmod._constant_time_equals("", "a"))


class ScanAuthWindowTests(unittest.TestCase):
    """SCAN_AUTH_ENFORCED_AT: strict by default, lenient only before the cutoff."""

    def setUp(self):
        self.fake = _FakeClient()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        device_auth.get_client = lambda: self.fake
        self.client = appmod.app.test_client()
        self._saved = os.environ.get("SCAN_AUTH_ENFORCED_AT")
        device_auth._LEGACY_COUNTS.clear()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SCAN_AUTH_ENFORCED_AT", None)
        else:
            os.environ["SCAN_AUTH_ENFORCED_AT"] = self._saved
        device_auth._LEGACY_COUNTS.clear()

    def _post(self, token=None, device_id="legacydev"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post(
            "/api/scan", json={"wifi": [], "deviceId": device_id}, headers=headers
        )

    def test_unset_means_enforce_immediately(self):
        os.environ.pop("SCAN_AUTH_ENFORCED_AT", None)
        self.assertTrue(device_auth.scan_auth_enforced())
        self.assertEqual(self._post().status_code, 401)

    def test_garbage_date_fails_closed(self):
        # A typo must never leave the write path open.
        os.environ["SCAN_AUTH_ENFORCED_AT"] = "not-a-date"
        self.assertTrue(device_auth.scan_auth_enforced())
        self.assertEqual(self._post().status_code, 401)

    def test_past_date_means_enforced(self):
        os.environ["SCAN_AUTH_ENFORCED_AT"] = "2000-01-01T00:00:00+00:00"
        self.assertTrue(device_auth.scan_auth_enforced())
        self.assertEqual(self._post().status_code, 401)

    def test_future_date_allows_legacy_client(self):
        os.environ["SCAN_AUTH_ENFORCED_AT"] = "2999-01-01T00:00:00+00:00"
        self.assertFalse(device_auth.scan_auth_enforced())
        self.assertEqual(self._post().status_code, 200)

    def test_legacy_path_still_requires_a_device_id(self):
        os.environ["SCAN_AUTH_ENFORCED_AT"] = "2999-01-01T00:00:00+00:00"
        r = self.client.post("/api/scan", json={"wifi": []})
        self.assertEqual(r.status_code, 401)

    def test_legacy_path_is_budgeted(self):
        os.environ["SCAN_AUTH_ENFORCED_AT"] = "2999-01-01T00:00:00+00:00"
        limit = device_auth._LEGACY_MAX_PER_DAY
        codes = [self._post(device_id="chatty").status_code for _ in range(limit + 3)]
        self.assertEqual(codes[:limit], [200] * limit)
        self.assertEqual(codes[limit], 429)
        # Budget is per device, so a different device is unaffected.
        self.assertEqual(self._post(device_id="quiet").status_code, 200)

    def test_valid_token_works_even_inside_the_window(self):
        os.environ["SCAN_AUTH_ENFORCED_AT"] = "2999-01-01T00:00:00+00:00"
        token = self.client.post(
            "/api/register-device", json={"deviceId": "newdev"}
        ).json["token"]
        self.assertEqual(self._post(token=token, device_id="newdev").status_code, 200)

    def test_register_throttle_state_is_isolated(self):
        device_auth._REG_ATTEMPTS.clear()
        for _ in range(device_auth._REG_MAX_PER_WINDOW):
            self.client.post("/api/register-device", json={"deviceId": "d"})
        self.assertEqual(
            self.client.post("/api/register-device", json={"deviceId": "d"}).status_code,
            429,
        )
        device_auth._REG_ATTEMPTS.clear()


if __name__ == "__main__":
    unittest.main()
