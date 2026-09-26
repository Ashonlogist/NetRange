"""
Tests for API key hashing and lookup.

The regression these guard against is specific: a key that authenticates must
still authenticate after the plaintext->hash change, and the plaintext must
never be what gets stored or compared.
"""

import hashlib
import os
import sys
import unittest

import tests_env  # noqa: F401  (must precede `import app`)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api_keys  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
from test_device_auth import _FakeClient  # noqa: E402


class HashApiKeyTests(unittest.TestCase):
    def test_hash_is_sha256_hex(self):
        h = api_keys.hash_api_key("nr_example")
        self.assertEqual(len(h), 64)
        self.assertEqual(h, hashlib.sha256(b"nr_example").hexdigest())

    def test_hash_is_stable(self):
        self.assertEqual(api_keys.hash_api_key("nr_x"), api_keys.hash_api_key("nr_x"))

    def test_distinct_keys_hash_distinctly(self):
        self.assertNotEqual(api_keys.hash_api_key("nr_a"), api_keys.hash_api_key("nr_b"))

    def test_hash_handles_unicode(self):
        self.assertEqual(len(api_keys.hash_api_key("clé-secrète")), 64)


class CreateApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeClient()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        api_keys.get_client = lambda: self.fake

    def test_returns_a_well_formed_key(self):
        key = api_keys.create_api_key("acme")
        self.assertTrue(key.startswith("nr_"))
        # 24 bytes of hex plus the prefix.
        self.assertEqual(len(key), 3 + 48)

    def test_stores_hash_not_plaintext(self):
        key = api_keys.create_api_key("acme")
        row = self.fake.rows[0]
        self.assertEqual(row["key_hash"], api_keys.hash_api_key(key))
        self.assertNotIn("key", row)
        self.assertNotIn(key, str(self.fake.rows))

    def test_keys_are_unique_per_call(self):
        a = api_keys.create_api_key("acme")
        b = api_keys.create_api_key("acme")
        self.assertNotEqual(a, b)
        self.assertNotEqual(
            self.fake.rows[0]["key_hash"], self.fake.rows[1]["key_hash"]
        )

    def test_records_customer_and_limit(self):
        api_keys.create_api_key("acme", daily_limit=42)
        self.assertEqual(self.fake.rows[0]["customer"], "acme")
        self.assertEqual(self.fake.rows[0]["daily_limit"], 42)
        self.assertTrue(self.fake.rows[0]["active"])


# Registered once at import time: Flask >=2.3 refuses to add routes after the
# app has handled its first request, so this cannot live inside setUp. The
# decorator resolves get_client() at call time, which is what the per-test
# stub swap in setUp relies on.
@appmod.app.route("/_test/protected")
@api_keys.require_api_key
def _protected_view():
    return {"ok": True}


class RequireApiKeyTests(unittest.TestCase):
    """The decorator must match on the hash, via both accepted transports."""

    ROUTE = "/_test/protected"

    def setUp(self):
        self.fake = _FakeClient()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        api_keys.get_client = lambda: self.fake
        self.client = appmod.app.test_client()

    def test_missing_key_is_401(self):
        self.assertEqual(self.client.get(self.ROUTE).status_code, 401)

    def test_unknown_key_is_401(self):
        r = self.client.get(f"{self.ROUTE}?key=nr_{'0' * 48}")
        self.assertEqual(r.status_code, 401)

    def test_valid_key_via_query_is_accepted(self):
        key = api_keys.create_api_key("acme")
        self.assertEqual(self.client.get(f"{self.ROUTE}?key={key}").status_code, 200)

    def test_valid_key_via_header_is_accepted(self):
        key = api_keys.create_api_key("acme")
        r = self.client.get(self.ROUTE, headers={"X-API-Key": key})
        self.assertEqual(r.status_code, 200)

    def test_deactivated_key_is_401(self):
        key = api_keys.create_api_key("acme")
        self.fake.rows[0]["active"] = False
        self.assertEqual(self.client.get(f"{self.ROUTE}?key={key}").status_code, 401)

    def test_lookup_never_compares_plaintext(self):
        """Guard the actual regression: querying the plaintext column."""
        seen = []
        original_table = _FakeClient.table

        def spy(self_, name):
            t = original_table(self_, name)
            real_eq = t.eq

            def eq(col, val):
                seen.append((col, val))
                return real_eq(col, val)

            t.eq = eq
            return t

        key = api_keys.create_api_key("acme")
        _FakeClient.table = spy
        try:
            self.client.get(f"{self.ROUTE}?key={key}")
        finally:
            _FakeClient.table = original_table

        looked_up = [c for c, _ in seen if c in ("key", "key_hash")]
        self.assertTrue(looked_up, "no key column was queried at all")
        for col in looked_up:
            self.assertEqual(col, "key_hash")
        # And the value compared is the hash, not the key itself.
        for col, val in seen:
            if col == "key_hash":
                self.assertEqual(val, api_keys.hash_api_key(key))
                self.assertNotEqual(val, key)


if __name__ == "__main__":
    unittest.main()
