"""
Tests for per-install device-token auth on the scan write path.

These are the tests that keep POST /api/scan from quietly becoming an open
write endpoint again. They run against a stub client rather than Supabase, so
they need no network and no credentials -- the stub deliberately *honours*
.eq()/.delete(), because a stub that ignores filters makes rotation tests pass
for the wrong reason.

Required environment comes from tests_env, which must be imported before app
(see test_security.py for the startup refusal behaviour itself).
"""

import unittest

import tests_env  # noqa: F401  (must precede `import app`)

import app as appmod  # noqa: E402
import db  # noqa: E402
import device_auth  # noqa: E402


class _Table:
    """Stub that defers every operation to execute(), the way PostgREST does.

    Applying delete() eagerly is a trap: `.delete().eq(...)` chains the filter
    *after* the call, so an eager stub sees an empty filter list, `all([])` is
    True, and it wipes the whole table. That silently broke device isolation
    during development, so this stub mirrors the real query builder.

    It also assigns an auto-incrementing `id` on insert, standing in for the
    `bigserial primary key` defaults the real tables rely on -- without it,
    callers that read back `rec["id"]` fail here but work in production.
    """

    def __init__(self, store):
        self.store = store
        self.filters = []
        self.op = None
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def lt(self, col, val):
        self.filters.append((col, ("<", val)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def delete(self):
        self.op = "delete"
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = row
        return self

    def update(self, patch):
        self.op = "update"
        self.payload = patch
        return self

    def _match(self, row):
        for col, expected in self.filters:
            if isinstance(expected, tuple) and len(expected) == 2 and expected[0] == "<":
                actual = row.get(col)
                if actual is None or actual >= expected[1]:
                    return False
            elif row.get(col) != expected:
                return False
        return True

    def _next_id(self):
        return max([r.get("id", 0) for r in self.store] + [0]) + 1

    def execute(self):
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", self._next_id())
            self.store.append(row)
        elif self.op == "delete":
            self.store[:] = [r for r in self.store if not self._match(r)]
        elif self.op == "update":
            for row in self.store:
                if self._match(row):
                    row.update(self.payload)
        return type("R", (), {"data": [r for r in self.store if self._match(r)]})()


class _FakeClient:
    def __init__(self):
        self.rows = []

    def table(self, name):
        return _Table(self.rows)


class DeviceAuthTests(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeClient()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        # The registration throttle is module-level state keyed by IP, and
        # every test here shares one IP. Reset it so tests don't inherit each
        # other's budget (and so the count assertions below are deterministic).
        device_auth._REG_ATTEMPTS.clear()
        self.client = appmod.app.test_client()

    def _register(self, device_id="dev1"):
        r = self.client.post("/api/register-device", json={"deviceId": device_id})
        self.assertEqual(r.status_code, 200)
        return r.json["token"]

    def _post_scan(self, token=None, header=None):
        headers = {}
        if header is not None:
            headers["Authorization"] = header
        elif token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self.client.post("/api/scan", json={"wifi": []}, headers=headers)

    # --- the endpoint must actually be closed -----------------------------

    def test_scan_write_rejected_without_token(self):
        self.assertEqual(self._post_scan().status_code, 401)

    def test_scan_write_rejected_with_unknown_token(self):
        self.assertEqual(self._post_scan(token="not-a-real-token").status_code, 401)

    def test_malformed_authorization_header_rejected(self):
        # Must not be treated as a valid (empty) token.
        self.assertEqual(self._post_scan(header="nonsense").status_code, 401)

    def test_valid_token_accepted(self):
        self.assertEqual(self._post_scan(token=self._register()).status_code, 200)

    def test_bearer_scheme_is_case_insensitive(self):
        token = self._register()
        self.assertEqual(self._post_scan(header=f"bearer {token}").status_code, 200)

    # --- registration -----------------------------------------------------

    def test_register_returns_token(self):
        self.assertTrue(self._register())

    def test_register_requires_device_id(self):
        self.assertEqual(self.client.post("/api/register-device", json={}).status_code, 400)

    def test_register_rejects_absurdly_long_device_id(self):
        r = self.client.post("/api/register-device", json={"deviceId": "x" * 200})
        self.assertEqual(r.status_code, 400)

    def test_registration_is_rate_limited(self):
        # /api/register-device is necessarily open, so it must be throttled:
        # otherwise it is a public factory for scan-write tokens.
        for _ in range(device_auth._REG_MAX_PER_WINDOW):
            r = self.client.post("/api/register-device", json={"deviceId": "dev"})
            self.assertEqual(r.status_code, 200)
        blocked = self.client.post("/api/register-device", json={"deviceId": "dev"})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Too many", blocked.json["error"])

    # --- rotation / isolation ---------------------------------------------

    def test_re_registration_rotates_token_and_invalidates_old(self):
        old = self._register()
        new = self._register()
        self.assertNotEqual(old, new)
        self.assertEqual(self._post_scan(token=old).status_code, 401)
        self.assertEqual(self._post_scan(token=new).status_code, 200)

    def test_one_live_row_per_device(self):
        self._register()
        self._register()
        self._register()
        rows = [r for r in self.fake.rows if r["device_id"] == "dev1"]
        self.assertEqual(len(rows), 1)

    def test_devices_are_isolated(self):
        a = self._register("devA")
        b = self._register("devB")
        self.assertEqual(self._post_scan(token=a).status_code, 200)
        self.assertEqual(self._post_scan(token=b).status_code, 200)
        # Re-registering B must not let it keep using A's old token.
        self._register("devA")
        self.assertEqual(self._post_scan(token=b).status_code, 200)

    # --- storage hygiene ---------------------------------------------------

    def test_plaintext_token_is_never_stored(self):
        token = self._register()
        self.assertNotIn(token, str(self.fake.rows))

    def test_stored_value_is_sha256_hex(self):
        self._register()
        for row in self.fake.rows:
            self.assertEqual(len(row["token_hash"]), 64)
            int(row["token_hash"], 16)  # raises if not hex

    def test_hash_matches_client_token(self):
        import hashlib
        token = self._register()
        expected = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(self.fake.rows[0]["token_hash"], expected)


if __name__ == "__main__":
    unittest.main()
