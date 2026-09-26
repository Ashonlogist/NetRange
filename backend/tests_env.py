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
