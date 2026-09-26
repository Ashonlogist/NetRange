"""
Tests for the network-owner account system and the widget scan endpoint.

The security properties worth protecting here are specific:

  * an owner can only ever see or mutate their OWN sites (scoped in the query,
    not by hiding buttons);
  * the session cookie is not readable from JS and is not a cross-site POST
    credential;
  * /api/widget-scan writes nothing unless the Origin matches a registered
    domain -- and specifically does NOT inherit /api/scan's device-token model,
    which would be wrong here (see owner_auth.origin_allowed);
  * state-changing owner routes require a CSRF token;
  * login is rate limited, because these accounts are password-only with no
    second factor to fall back on.

The stub client honours .eq() filters, so a scoping bug shows up as a test
failure rather than passing silently.
"""

import pathlib
import re
import unittest

import tests_env  # noqa: F401  (must precede `import app`)

import app as appmod  # noqa: E402
import db  # noqa: E402
import owner_auth  # noqa: E402
from analytics import MIN_DEVICES_PER_CELL  # noqa: E402
from test_sms_reports import _Db  # noqa: E402


class OwnerAccountTests(unittest.TestCase):
    def setUp(self):
        self.fake = _Db()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        owner_auth.OWNER_SESSION_SECRET = "test-owner-session-secret"
        owner_auth.SMS_PHONE_PEPPER = "test-phone-pepper"
        owner_auth._LOGIN_ATTEMPTS.clear()
        self.client = appmod.app.test_client()

    def _approve(self, username="volta"):
        """
        Mark an account approved.

        Most of these tests are about owner scoping and CSRF, not about the
        approval gate (which TestApprovalGate covers). Without approval they
        would all stop at the gate with a 403 and pass for the wrong reason, so
        they approve first and go on to test what they were written for.
        """
        rows = [o for o in self.fake.rows("network_owners") if o["username"] == username]
        if rows:
            owner_auth.set_approval(rows[0]["id"], True, "tester")
        return rows[0]["id"] if rows else None

    # --- registration ----------------------------------------------------

    def test_registration_creates_a_hashed_password(self):
        oid, err = owner_auth.create_owner("volta", "a-long-enough-pass")
        self.assertIsNone(err)
        row = self.fake.rows("network_owners")[0]
        self.assertNotEqual(row["password_hash"], "a-long-enough-pass")
        self.assertIn("scrypt", row["password_hash"], "must use a memory-hard KDF")

    def test_short_password_rejected(self):
        oid, err = owner_auth.create_owner("volta", "short")
        self.assertIsNone(oid)
        self.assertIn("10", err)

    def test_duplicate_username_rejected(self):
        owner_auth.create_owner("volta", "a-long-enough-pass")
        oid, err = owner_auth.create_owner("volta", "another-long-pass")
        self.assertIsNone(oid)
        self.assertIn("taken", err)

    def test_username_charset_enforced(self):
        for bad in ("ab", "Has Upper", "has space", "semi;colon", "x" * 40):
            oid, err = owner_auth.create_owner(bad, "a-long-enough-pass")
            self.assertIsNone(oid, bad)

    # --- login -----------------------------------------------------------

    def test_correct_credentials_log_in(self):
        owner_auth.create_owner("volta", "a-long-enough-pass")
        r = self.client.post("/owner/login", data={"username": "volta",
                                                  "password": "a-long-enough-pass"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/owner/", r.headers["Location"])

    def test_wrong_password_does_not_log_in(self):
        owner_auth.create_owner("volta", "a-long-enough-pass")
        r = self.client.post("/owner/login", data={"username": "volta", "password": "wrong"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Incorrect", r.get_data(as_text=True))

    def test_unknown_user_gives_the_same_message_as_a_wrong_password(self):
        owner_auth.create_owner("volta", "a-long-enough-pass")
        a = self.client.post("/owner/login", data={"username": "volta", "password": "nope"})
        b = self.client.post("/owner/login", data={"username": "ghost", "password": "nope"})
        self.assertEqual(
            a.get_data(as_text=True).count("Incorrect"),
            b.get_data(as_text=True).count("Incorrect"),
            "the two failures must be indistinguishable",
        )

    def test_login_is_rate_limited(self):
        for _ in range(owner_auth._LOGIN_MAX_PER_WINDOW):
            self.client.post("/owner/login", data={"username": "x", "password": "y"})
        r = self.client.post("/owner/login", data={"username": "x", "password": "y"})
        self.assertEqual(r.status_code, 429)

    # --- session cookie --------------------------------------------------

    def test_session_cookie_is_httponly_and_samesite(self):
        owner_auth.create_owner("volta", "a-long-enough-pass")
        r = self.client.post("/owner/login", data={"username": "volta",
                                                  "password": "a-long-enough-pass"})
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        # Guard the exact bug these flags once had: lowercase config keys are
        # accepted by Flask and then ignored, so the cookie silently loses
        # SameSite/Secure while every test still passed.
        for key, expect in (("SESSION_COOKIE_SAMESITE", "SameSite=Lax"),
                            ("SESSION_COOKIE_SECURE", "Secure")):
            self.assertIn(expect, cookie, f"{key} not applied to the cookie")

    # --- owner scoping ---------------------------------------------------

    def test_dashboard_requires_login(self):
        r = self.client.get("/owner/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/owner/login", r.headers["Location"])

    def test_sms_toggle_cannot_touch_another_owners_site(self):
        a, _ = owner_auth.create_owner("alpha", "a-long-enough-pass")
        b, _ = owner_auth.create_owner("beta", "a-long-enough-pass")
        site, _ = owner_auth.register_site(b, "beta.example.com", "Beta")
        self._approve("alpha")
        self.client.post("/owner/login", data={"username": "alpha",
                                               "password": "a-long-enough-pass"})
        # Load the dashboard, which is what mints a CSRF token into the session.
        self.assertEqual(self.client.get("/owner/").status_code, 200)
        with self.client.session_transaction() as sess:
            csrf = sess["owner_csrf"]
        self.assertTrue(csrf, "dashboard should have minted a CSRF token")

        r = self.client.post(f"/owner/sites/{site['id']}/sms",
                             data={"enabled": "1", "csrf_token": csrf})
        self.assertEqual(r.status_code, 404)
        # .get because the stub does not apply column defaults the way Postgres
        # does; the assertion is about the flag never having been flipped.
        self.assertFalse(
            self.fake.rows("widget_sites")[0].get("sms_reporting_enabled"),
            "a cross-owner toggle must update zero rows",
        )
        del a

    def test_owner_sites_only_returns_your_own(self):
        owner_auth.create_owner("alpha", "a-long-enough-pass")
        b, _ = owner_auth.create_owner("beta", "a-long-enough-pass")
        owner_auth.register_site(b, "beta.example.com", "Beta")
        self.assertEqual(owner_auth.owner_sites(1), [])
        self.assertEqual(len(owner_auth.owner_sites(b)), 1)

    def test_rendered_dashboard_exposes_only_your_own_site_ids(self):
        """
        Rendering-level isolation.

        Asserted on the site ids that appear as SMS-toggle targets, not on a
        domain substring: the "add another site" form carries
        placeholder="volta.example.com", so a naive substring check passes for
        the wrong reason. The ids are what an actual request would act on.
        """
        import re
        a, _ = owner_auth.create_owner("alpha", "a-long-enough-pass")
        b, _ = owner_auth.create_owner("beta", "a-long-enough-pass")
        site_a, _ = owner_auth.register_site(a, "alpha.example.com", "Alpha")
        site_b, _ = owner_auth.register_site(b, "beta.example.com", "Beta")

        for user, own, foreign in (("alpha", site_a, site_b), ("beta", site_b, site_a)):
            self._approve(user)
            c = appmod.app.test_client()
            c.post("/owner/login", data={"username": user,
                                         "password": "a-long-enough-pass"})
            body = c.get("/owner/").get_data(as_text=True)
            targets = set(re.findall(r"/owner/sites/(\d+)/sms", body))
            self.assertIn(str(own["id"]), targets, user)
            self.assertNotIn(str(foreign["id"]), targets,
                             f"{user} dashboard leaked another owner's site")

    # --- CSRF ------------------------------------------------------------

    def test_state_change_without_csrf_is_rejected(self):
        owner_auth.create_owner("alpha", "a-long-enough-pass")
        self._approve("alpha")
        self.client.post("/owner/login", data={"username": "alpha",
                                               "password": "a-long-enough-pass"})
        r = self.client.post("/owner/sites", data={"domain": "x.example.com"})
        self.assertEqual(r.status_code, 400)

    # --- domain validation ----------------------------------------------

    def test_domains_normalize_to_bare_hostnames(self):
        for raw, want in (
            ("https://www.Volta.Example.com/path?q=1", "volta.example.com"),
            ("volta.example.com", "volta.example.com"),
            ("  WWW.VOLTA.EXAMPLE.COM  ", "volta.example.com"),
            ("http://volta.example.com:8080", "volta.example.com"),
        ):
            self.assertEqual(owner_auth.normalize_domain(raw), want, raw)

    def test_single_label_domains_rejected(self):
        # A bare "localhost" would let any local site claim any other.
        for bad in ("localhost", "intranet", "", "volta"):
            self.assertFalse(owner_auth.domain_is_valid(
                owner_auth.normalize_domain(bad)), bad)

    def test_path_or_scheme_in_a_registered_domain_is_rejected(self):
        oid, err = owner_auth.create_owner("volta", "a-long-enough-pass")
        site, err = owner_auth.register_site(oid, "not a domain!", "x")
        self.assertIsNone(site)
        self.assertIn("domain", err.lower())


class WidgetScanTests(unittest.TestCase):
    def setUp(self):
        self.fake = _Db()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        owner_auth._WIDGET_ATTEMPTS.clear()
        self.fake.table("widget_sites").insert(
            {"id": 1, "owner_id": 1, "domain": "volta.example.com", "label": "Volta",
             "sms_reporting_enabled": False}
        ).execute()
        self.client = appmod.app.test_client()

    def _post(self, origin="https://volta.example.com", payload=None):
        headers = {"Origin": origin} if origin else {}
        return self.client.post("/api/widget-scan", json=payload or {
            "contributor_id": "abc", "effective_type": "4g",
            "downlink_estimate_mbps": 10, "lat": 5.60, "lon": -0.19,
        }, headers=headers)

    def test_registered_origin_is_accepted(self):
        self.assertEqual(self._post().status_code, 201)

    def test_stored_row_is_labelled_as_widget_ingest(self):
        self._post()
        row = self.fake.rows("scans")[0]
        self.assertEqual(row["ingest_source"], "widget")

    def test_row_records_the_origin_derived_site_not_a_client_supplied_one(self):
        self._post(payload={"contributor_id": "a", "widget_site_id": 999})
        self.assertEqual(self.fake.rows("scans")[0]["widget_site_id"], 1,
                         "a body-supplied site id must never be trusted")

    def test_unregistered_origin_is_refused(self):
        self.assertEqual(self._post(origin="https://evil.example.net").status_code, 403)
        self.assertEqual(self.fake.rows("scans"), [])

    def test_no_origin_header_is_refused(self):
        self.assertEqual(self._post(origin=None).status_code, 403)
        self.assertEqual(self.fake.rows("scans"), [])

    def test_lookalike_domain_is_refused(self):
        for bad in ("https://volta.example.com.evil.net",
                    "https://notvolta.example.com",
                    "https://evil.net/?volta.example.com"):
            self.assertEqual(self._post(origin=bad).status_code, 403, bad)

    def test_referer_is_accepted_as_a_fallback(self):
        r = self.client.post("/api/widget-scan", json={"contributor_id": "a"},
                             headers={"Referer": "https://volta.example.com/page"})
        self.assertEqual(r.status_code, 201)

    def test_widget_endpoint_needs_no_device_token(self):
        # Explicitly asserted: reusing /api/scan's token scheme here would lock
        # out every visitor, since a browser has no install to hold a token.
        self.assertEqual(self._post().status_code, 201)

    def test_widget_rate_limit(self):
        for _ in range(owner_auth._WIDGET_MAX_PER_WINDOW):
            self._post()
        self.assertEqual(self._post().status_code, 429)

    def test_location_is_optional(self):
        r = self._post(payload={"contributor_id": "a", "effective_type": "4g"})
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(self.fake.rows("scans")[0]["lat"])

    def test_out_of_range_coordinates_are_discarded(self):
        self._post(payload={"contributor_id": "a", "lat": 999, "lon": -0.19})
        self.assertIsNone(self.fake.rows("scans")[0]["lat"])

    def test_estimate_is_not_stored_as_a_measured_speed(self):
        self._post(payload={"contributor_id": "a", "downlink_estimate_mbps": 42})
        row = self.fake.rows("scans")[0]
        self.assertEqual(row["downlink_estimate_mbps"], 42)
        self.assertIsNone(row["download_speed_mbps"],
                          "an estimate must never enter the measured-speed column")

    def test_radio_type_is_left_unknown_rather_than_asserted(self):
        self._post()
        self.assertIsNone(self.fake.rows("scans")[0]["source"],
                          "a browser cannot know wifi vs cellular")

    def test_oversized_strings_are_truncated(self):
        self._post(payload={"contributor_id": "c" * 500, "effective_type": "x" * 500})
        row = self.fake.rows("scans")[0]
        self.assertLessEqual(len(row["device_id"]), 64)
        self.assertLessEqual(len(row["effective_type"]), 32)

    def test_widget_js_is_served_with_nosniff(self):
        r = self.client.get("/widget.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("nosniff", r.headers.get("X-Content-Type-Options", ""))


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# The consent notice, pinned verbatim.
# --------------------------------------------------------------------------
# The wording is the agreement. These tests reconstruct the string the browser
# will actually render and compare it to the required copy character by
# character, so a "harmless" reword during a refactor fails the build instead of
# quietly changing what a visitor consents to.

STATIC_WIDGET = pathlib.Path(__file__).resolve().parent / "static" / "widget.js"

REQUIRED_CONSENT = (
    "We'd like to collect anonymous network quality data from this page to help "
    "improve coverage maps.\n"
    "\n"
    "Your data is only shown if at least 3 other people from this area also "
    "reported. Nothing is linked to you.\n"
    "\n"
    "This is optional and anonymous. No account, no personal details, no cookies. "
    "You can change your mind anytime \u2014 the data is only collected once per visit."
)


def _strip_js_comments(src):
    """Remove // and /* */ comments so prose about a rule cannot satisfy a
    test that the rule's code obeys it."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _rendered_consent():
    """Evaluate widget.js's CONSENT array exactly as the browser would."""
    src = pathlib.Path(STATIC_WIDGET).read_text()
    start = src.index("var CONSENT = [")
    end = src.index("].join(", start)
    body = src[start + len("var CONSENT = ["):end]
    out = []
    for m in re.finditer(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", body):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        raw = raw.replace("\\'", "'").replace('\\"', '"')
        # both JS escape forms: \uXXXX and \u{XXXXX}
        raw = re.sub(r"\\u\{([0-9a-fA-F]+)\}",
                     lambda m: chr(int(m.group(1), 16)), raw)
        raw = re.sub(r"\\u([0-9a-fA-F]{4})",
                     lambda m: chr(int(m.group(1), 16)), raw)
        out.append(raw)
    return "\n".join(out)


class TestConsentCopy(unittest.TestCase):
    def test_consent_copy_is_verbatim(self):
        self.assertEqual(_rendered_consent(), REQUIRED_CONSENT)

    def test_copy_states_the_threshold_we_actually_enforce(self):
        self.assertIn(f"at least {MIN_DEVICES_PER_CELL} other people", _rendered_consent())

    def test_copy_states_no_cookies(self):
        self.assertIn("no cookies", _rendered_consent().lower())

    def test_copy_states_nothing_is_linked_to_the_person(self):
        self.assertIn("Nothing is linked to you", _rendered_consent())

    def test_no_fingerprinting_code(self):
        code = _strip_js_comments(pathlib.Path(STATIC_WIDGET).read_text())
        for banned in ("canvas", "toDataURL", "getContext", "AudioContext",
                       "deviceMemory", "hardwareConcurrency", "fonts.check"):
            self.assertNotIn(banned, code, f"{banned} must not appear in widget code")

    def test_nothing_is_sent_before_allow(self):
        """
        Structural check: the only place a request is issued is inside
        collectAndSend(), and the only caller is the Allow handler. The decline
        handler must not reach it.
        """
        code = _strip_js_comments(pathlib.Path(STATIC_WIDGET).read_text())
        senders = [m for m in re.finditer(r"function (collectAndSend|send|onAllow|onDecline)", code)]
        names = [m.group(1) for m in senders]
        self.assertIn("collectAndSend", names)
        self.assertIn("onAllow", names)
        self.assertIn("onDecline", names)
        # collectAndSend is invoked exactly once, from the Allow closure
        # (the "function collectAndSend(prompt)" definition is not a call site)
        sites = [m for m in re.finditer(r"(?<!function )collectAndSend\(prompt\)", code)]
        self.assertEqual(len(sites), 1)
        call = sites[0].start()
        allow_at = code.index("function onAllow")
        decline_at = code.index("function onDecline")
        self.assertGreater(call, allow_at)
        self.assertLess(call, decline_at)
        # the decline handler only tears the prompt down
        decline_body = code[decline_at:decline_at + 220]
        for banned in ("collectAndSend", "send(", "fetch", "beacon"):
            self.assertNotIn(banned, decline_body)


# --------------------------------------------------------------------------
# Venue approval gate.
#
# Coverage reporting is not self-serve: a venue requests it, a person decides,
# and free-vs-billed depends on the scale of the network. These tests pin that
# the decision cannot be skipped by simply registering an account.
# --------------------------------------------------------------------------
class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.fake = _Db()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        owner_auth._LOGIN_ATTEMPTS.clear()
        self.c = appmod.app.test_client()

    def _register(self, name="venue_one"):
        return self.c.post("/owner/register", data={
            "username": name, "password": "a-long-enough-pass"})

    def test_registration_creates_an_unapproved_account(self):
        self._register()
        owners = self.fake.rows("network_owners")
        self.assertEqual(len(owners), 1)
        self.assertIsNone(owners[0].get("approved_at"),
                          "sign-up must not approve itself")

    def test_pending_owner_sees_pending_page_not_a_widget(self):
        self._register()
        r = self.c.get("/owner/")
        body = r.get_data(as_text=True)
        self.assertIn("Awaiting approval", body)
        self.assertNotIn("<script", body, "a pending owner must not be handed a snippet")

    def test_pending_owner_cannot_register_a_domain(self):
        self._register()
        self.c.get("/owner/")
        page = self.c.get("/owner/").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
        r = self.c.post("/owner/sites", data={
            "csrf_token": csrf.group(1) if csrf else "x",
            "domain": "venue.example.com", "label": "Venue"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.fake.rows("widget_sites"), [],
                         "no domain may be registered before approval")

    def test_approval_unlocks_the_dashboard(self):
        self._register()
        owner_id = self.fake.rows("network_owners")[0]["id"]
        owner_auth.set_approval(owner_id, True, "tester")
        body = self.c.get("/owner/").get_data(as_text=True)
        self.assertIn("Register site", body)
        self.assertNotIn("Awaiting approval", body)

    def test_approval_unlocks_domain_registration(self):
        self._register()
        owner_id = self.fake.rows("network_owners")[0]["id"]
        owner_auth.set_approval(owner_id, True, "tester")
        self.c.get("/owner/")
        page = self.c.get("/owner/").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
        r = self.c.post("/owner/sites", data={
            "csrf_token": csrf.group(1) if csrf else "x",
            "domain": "venue.example.com", "label": "Venue"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.fake.rows("widget_sites")), 1)

    def test_revoking_approval_closes_it_again(self):
        self._register()
        owner_id = self.fake.rows("network_owners")[0]["id"]
        owner_auth.set_approval(owner_id, True, "tester")
        self.assertNotIn("Awaiting approval",
                         self.c.get("/owner/").get_data(as_text=True))
        owner_auth.set_approval(owner_id, False, "tester", "billing unresolved")
        self.assertIn("Awaiting approval",
                      self.c.get("/owner/").get_data(as_text=True))

    def test_admin_approval_routes_require_basic_auth(self):
        """A venue must not be able to approve itself."""
        for path in ("/api/access-requests/1", "/api/owners/1/approval"):
            r = self.c.post(path, data={"decision": "approved"},
                            headers={"X-Netrange-Admin": "1"})
            self.assertEqual(r.status_code, 401, path)
        self.assertEqual(self.c.get("/api/access-requests").status_code, 401)

    def test_admin_mutation_requires_the_custom_header(self):
        """
        Basic auth alone is not enough. A browser re-sends cached Basic
        credentials to the same origin even from another site's page, and an
        owner_id is a small sequential integer, so a plain cross-site form POST
        would otherwise be able to approve an arbitrary account. A form cannot
        set a custom header; a fetch() that tries gets a CORS preflight.
        """
        owner_auth.set_approval
        r = self.c.post("/api/owners/1/approval", data={"decision": "approved"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("X-Netrange-Admin", r.get_data(as_text=True))

    def test_cors_is_not_global(self):
        """
        CORS(app) reflects the caller's Origin on every route. Only the widget
        and SMS endpoints are meant to be cross-origin.
        """
        r = self.c.get("/api/version", headers={"Origin": "https://evil.net"})
        self.assertNotIn("https://evil.net",
                         r.headers.get("Access-Control-Allow-Origin", ""),
                         "ordinary API routes must not reflect arbitrary origins")
        r = self.c.get("/api/widget-scan", headers={"Origin": "https://evil.net"})
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertNotIn("true", r.headers.get("Access-Control-Allow-Credentials", ""),
                         "credentials must never be allowed cross-origin")

    def test_intent_request_is_recorded_even_if_never_emailed(self):
        self.c.get("/get-access")
        page = self.c.get("/get-access").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
        r = self.c.post("/get-access", data={
            "csrf_token": csrf.group(1) if csrf else "x",
            "org_name": "KNUST", "contact_email": "wifi@knust.edu.gh",
            "domain": "wifi.knust.edu.gh", "network_type": "campus wifi",
            "scale": "1 campus, ~4000 students", "use_case": "find dead spots"})
        self.assertEqual(r.status_code, 200)
        rows = self.fake.rows("venue_requests")
        self.assertEqual(len(rows), 1, "intent must be durable, not just emailed")
        self.assertEqual(rows[0]["org_name"], "KNUST")
        self.assertEqual(rows[0]["scale"], "1 campus, ~4000 students")
        # and the response must offer the prefilled email
        self.assertIn("netrange@ashonlogist.website",
                      r.get_data(as_text=True))
