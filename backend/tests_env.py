"""
Environment defaults for the test suite.

app.py refuses to import without DASHBOARD_SECRET / DASHBOARD_USERNAME /
DASHBOARD_PASSWORD, which is the point in production but makes the tests
need ceremony. This module centralises that ceremony so the requirement is
declared once instead of copy-pasted into every test module (where it would
drift the moment a new required var is added).

Import this BEFORE `import app`. It is deliberately not named test_*.py so
unittest discovery does not try to run it as a test module.
"""

import os

os.environ.setdefault("DASHBOARD_SECRET", "test-secret")
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-pass")
os.environ.setdefault("SUPABASE_URL", "https://stub.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "stub-service-key")

# Owner accounts + SMS reporting (network-owner widget path).
#
# app.py refuses to import without these. Both are secrets that must never
# share a value in production -- OWNER_SESSION_SECRET signs owner session
# cookies and SMS_PHONE_PEPPER keys the phone-number HMAC -- so they get
# distinct non-default test values. If a test ever asserted that one of them
# worked while set to the other, it would catch a real coupling bug.
os.environ.setdefault("OWNER_SESSION_SECRET", "test-owner-session-secret")
os.environ.setdefault("SMS_PHONE_PEPPER", "test-phone-pepper")
