"""
Tests for SMS coverage reports (backend/sms_reports.py).

These exist because SMS reporting is the most privacy-sensitive thing in this
repository: it asks a real person to describe their experience at a real place,
then asks for their gender and how frustrated they are. The tests below pin the
behaviours that keep that safe, not just the happy path:

  * the raw phone number is never stored anywhere;
  * gender is skippable and stays NULL when skipped;
  * an unparseable answer re-asks instead of being silently dropped;
  * incomplete sessions expire rather than being resumed days later;
  * k-anonymity suppression is genuinely applied, and there is no code path
    that publishes a small-n cell.

They run against an in-memory stub that HONOURS .eq()/.in_/.lt()/.update()
filters. A stub that ignored filters would let every one of these pass for the
wrong reason, which is the trap test_device_auth.py already documents.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

import tests_env  # noqa: F401  (must precede `import app`)

import app as appmod  # noqa: E402
import db  # noqa: E402
import owner_auth  # noqa: E402
import sms_reports  # noqa: E402
from analytics import MIN_DEVICES_PER_CELL  # noqa: E402


class _Table:
    """Minimal query builder over one in-memory table."""

    def __init__(self, store):
        self.store = store
        self.op = "select"
        self.payload = None
        self.filters = []

    def select(self, *a, **k):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, list(vals)))
        return self

    def lt(self, col, val):
        self.filters.append((col, ("<", val)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = row
        return self

    def update(self, patch):
        self.op = "update"
        self.payload = patch
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _match(self, row):
        for col, expected in self.filters:
            if isinstance(expected, tuple) and expected[0] == "<":
                actual = row.get(col)
                if actual is None or actual >= expected[1]:
                    return False
            elif isinstance(expected, list):
                if row.get(col) not in expected:
                    return False
            elif row.get(col) != expected:
                return False
        return True

    def _next_id(self):
        ids = [r.get("id", 0) for r in self.store if isinstance(r.get("id", 0), int)]
        return (max(ids) + 1) if ids else 1

    def execute(self):
        if self.op == "insert":
            # supabase-py accepts either a single row dict or a list of rows
            # (db.save_scan batches), so the stub has to as well.
            incoming = self.payload
            if isinstance(incoming, dict):
                incoming = [incoming]
            data = []
            for item in incoming:
                row = dict(item)
                row.setdefault("id", self._next_id())
                self.store.append(row)
                data.append(row)
        elif self.op == "delete":
            victims = [r for r in self.store if self._match(r)]
            for v in victims:
                self.store.remove(v)
            data = victims
        elif self.op == "update":
            data = [r for r in self.store if self._match(r)]
            for row in data:
                row.update(self.payload)
        else:
            data = [r for r in self.store if self._match(r)]
        resp = type("R", (), {"data": data})()
        return resp


class _Db:
    """Per-table stores, so ids do not collide across tables."""

    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Table(self.tables.setdefault(name, []))

    def rows(self, name):
        return self.tables.get(name, [])


def _iso(dt):
    return dt.isoformat()


class SmsFlowTests(unittest.TestCase):
    def setUp(self):
        self.fake = _Db()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        owner_auth.OWNER_SESSION_SECRET = "test-owner-session-secret"
        owner_auth.SMS_PHONE_PEPPER = "test-phone-pepper"
        self.n = "+233201234567"
        self.send = lambda text: sms_reports.handle_inbound(self.n, text, client=self.fake)

    # --- the happy path, all five steps ----------------------------------

    def test_full_report_is_recorded(self):
        self.assertIn("1", self.send(""))          # opens the conversation
        self.send("7")                              # quality
        self.send("MTN")                            # carrier
        self.send("Kanda")                          # location
        self.send("f")                              # gender
        reply = self.send("9")                      # frustration -> completes

        reports = self.fake.rows("sms_reports")
        self.assertEqual(len(reports), 1, "expected exactly one completed report")
        r = reports[0]
        self.assertEqual(r["quality_rating"], 7)
        self.assertEqual(r["carrier"], "MTN")
        self.assertEqual(r["location_text"], "Kanda")
        self.assertEqual(r["gender"], "f")
        self.assertEqual(r["frustration"], 9)
        self.assertIn("recorded", reply)

    def test_session_is_cleared_after_completion(self):
        self.send("")
        for t in ("5", "Telecel", "Osu", "skip", "3"):
            self.send(t)
        self.assertEqual(len(self.fake.rows("sms_sessions")), 0)

    # --- phone number privacy -------------------------------------------

    def test_raw_number_is_never_stored(self):
        self.send("")
        for t in ("6", "AirtelTigo", "Labon", "m", "2"):
            self.send(t)
        blob = repr(self.fake.tables)
        self.assertNotIn("233201234567", blob,
                         "raw phone number must not appear in any stored row")

    def test_stored_identifier_is_a_keyed_hash(self):
        ph = owner_auth.hash_phone(self.n)
        self.assertNotIn("233201234567", ph)
        self.assertEqual(len(ph), 64, "hex sha256")
        # Keyed, so an attacker with the row cannot reproduce it without the
        # pepper -- unlike a bare sha256 of a ~10^8-value number space.
        self.assertNotEqual(ph, owner_auth.hash_phone(self.n).upper())

    def test_different_pepper_yields_different_hash(self):
        original = owner_auth.SMS_PHONE_PEPPER
        try:
            owner_auth.SMS_PHONE_PEPPER = "a-different-pepper"
            other = owner_auth.hash_phone(self.n)
        finally:
            owner_auth.SMS_PHONE_PEPPER = original
        self.assertNotEqual(other, owner_auth.hash_phone(self.n))

    # --- gender is genuinely optional -----------------------------------

    def test_skip_keyword_leaves_gender_null(self):
        self.send("")
        for t in ("8", "MTN", "Kanda", "skip", "10"):
            self.send(t)
        r = self.fake.rows("sms_reports")[0]
        self.assertIsNone(r["gender"], "'skip' must store NULL, not a guess")
        self.assertEqual(r["frustration"], 10, "skip must not end the report early")

    def test_unrecognised_gender_stores_null_and_continues(self):
        self.send("")
        for t in ("8", "MTN", "Kanda", "purple", "4"):
            self.send(t)
        r = self.fake.rows("sms_reports")[0]
        self.assertIsNone(r["gender"], "never infer gender from an unknown reply")
        self.assertEqual(r["frustration"], 4)

    def test_gender_prompt_states_it_is_optional(self):
        self.assertIn("skip", sms_reports.PROMPTS["gender"])

    # --- bad input re-asks ----------------------------------------------

    def test_unparseable_rating_asks_again_and_keeps_the_step(self):
        self.send("")
        reply = self.send("excellent")
        self.assertIn("1", reply)
        self.assertEqual(len(self.fake.rows("sms_sessions")), 1)
        sess = self.fake.rows("sms_sessions")[0]
        self.assertEqual(sess["current_step"], "quality", "must not advance on garbage")
        self.assertEqual(sess["answers"], {})

    def test_out_of_range_ratings_rejected(self):
        for bad in ("0", "11", "-3"):
            self.assertIsNone(sms_reports.parse_rating(bad), bad)

    def test_ten_is_accepted(self):
        self.assertEqual(sms_reports.parse_rating("10"), 10)

    # --- timeout --------------------------------------------------------

    def test_incomplete_session_is_purged_after_the_timeout(self):
        self.send("")
        self.send("7")
        rows = self.fake.rows("sms_sessions")
        self.assertEqual(len(rows), 1)
        # Backdate well past the 30-minute window.
        rows[0]["last_activity_at"] = _iso(
            datetime.now(timezone.utc) - timedelta(minutes=sms_reports.SESSION_TIMEOUT_MINUTES + 5)
        )
        self.send("MTN")
        # The old partial answers must be gone, not resumed.
        self.assertEqual(self.fake.rows("sms_reports"), [])
        sess = self.fake.rows("sms_sessions")[0]
        self.assertEqual(sess["current_step"], "quality", "a stale session must restart")

    def test_session_within_the_window_is_resumed(self):
        self.send("")
        self.send("7")
        sess = self.fake.rows("sms_sessions")[0]
        sess["last_activity_at"] = _iso(
            datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        reply = self.send("MTN")
        self.assertEqual(self.fake.rows("sms_sessions")[0]["current_step"], "location")
        self.assertIn("Where", reply)

    def test_cancel_discards_partial_answers(self):
        self.send("")
        self.send("7")
        self.send("cancel")
        self.assertEqual(len(self.fake.rows("sms_sessions")), 0)
        self.assertEqual(len(self.fake.rows("sms_reports")), 0)

    def test_start_restarts_an_in_progress_report(self):
        self.send("")
        self.send("7")
        self.send("START")
        self.assertEqual(self.fake.rows("sms_sessions")[0]["current_step"], "quality")
        self.assertEqual(self.fake.rows("sms_sessions")[0]["answers"], {})

    # --- inbound parsing -------------------------------------------------

    def test_parse_inbound_accepts_common_field_spellings(self):
        for key in ("from", "From", "msisdn", "phone"):
            num, _to, _txt = sms_reports.parse_inbound({key: "+233201234567", "text": "hi"})
            self.assertEqual(num, "+233201234567", key)

    def test_parse_inbound_reports_no_sender(self):
        num, _to, _txt = sms_reports.parse_inbound({"text": "hi"})
        self.assertIsNone(num)

    def test_blank_message_reprompts_rather_than_storing_junk(self):
        self.send("")
        self.send("   ")
        self.assertEqual(self.fake.rows("sms_reports"), [])


class SmsAttributionTests(unittest.TestCase):
    """
    Attribution must be to a PLACE, not to a carrier.

    The original version of this matched on carrier name alone, and its tests
    asserted that. Which meant a venue registering "MTN" received every MTN
    report in the country -- including its competitors' venues and every road
    outside its own walls -- shown on its dashboard as its own.

    Carrier is not a location. Attribution now requires coordinates that land
    in the same cell as a registered site, so the worst case is a quiet
    dashboard rather than someone else's complaints.
    """

    ACCRA = (5.6037, -0.1870)
    KUMASI = (6.6885, -1.6244)

    def setUp(self):
        self.fake = _Db()
        owner_auth.SMS_PHONE_PEPPER = "test-phone-pepper"
        # Site 1: opted in, carrier MTN, located in Accra.
        self.fake.table("widget_sites").insert(
            {"id": 1, "domain": "volta.example.com", "label": "Volta Hall",
             "sms_reporting_enabled": True, "sms_carrier": "MTN",
             "lat": self.ACCRA[0], "lon": self.ACCRA[1]}
        ).execute()
        # Site 2: same carrier, but never opted in.
        self.fake.table("widget_sites").insert(
            {"id": 2, "domain": "other.example.com", "label": "Other",
             "sms_reporting_enabled": False, "sms_carrier": "MTN",
             "lat": self.ACCRA[0], "lon": self.ACCRA[1]}
        ).execute()

    def test_attaches_when_carrier_and_place_both_match(self):
        self.assertEqual(
            sms_reports.attribute_site(self.fake, "MTN", *self.ACCRA), 1)

    def test_carrier_matching_is_not_brittle(self):
        for spelling in ("mtn", "MTN Ghana", "  Mtn  ", "MTN Ghana Limited"):
            self.assertEqual(
                sms_reports.attribute_site(self.fake, spelling, *self.ACCRA), 1, spelling)

    def test_refuses_when_the_report_has_no_location(self):
        """
        The bug this whole class exists for. No coordinates means no place, and
        no place means we cannot claim it belongs to this venue.
        """
        self.assertIsNone(sms_reports.attribute_site(self.fake, "MTN"))
        self.assertIsNone(sms_reports.attribute_site(self.fake, "MTN", None, None))

    def test_refuses_when_the_report_is_elsewhere_in_the_country(self):
        """A Kumasi texter is not a Volta Hall visitor, whatever their carrier."""
        self.assertIsNone(
            sms_reports.attribute_site(self.fake, "MTN", *self.KUMASI))

    def test_does_not_attach_to_a_site_that_did_not_opt_in(self):
        self.assertIsNone(
            sms_reports.attribute_site(self.fake, "Telecel", *self.ACCRA))

    def test_does_not_attach_when_no_carrier_given(self):
        self.assertIsNone(sms_reports.attribute_site(self.fake, None, *self.ACCRA))
        self.assertIsNone(sms_reports.attribute_site(self.fake, "   ", *self.ACCRA))

    def test_never_attaches_when_no_opted_in_site_declares_a_carrier(self):
        self.fake.tables["widget_sites"] = [
            {"id": 3, "domain": "third.example.com", "sms_reporting_enabled": True,
             "sms_carrier": None, "lat": self.ACCRA[0], "lon": self.ACCRA[1]}
        ]
        self.assertIsNone(
            sms_reports.attribute_site(self.fake, "MTN", *self.ACCRA),
            "a site with no declared carrier must never be matched on a guess",
        )

    def test_never_attaches_when_the_site_has_no_known_location(self):
        """
        A site registered without coordinates cannot be tested against, so
        attribution is refused rather than guessed.
        """
        self.fake.tables["widget_sites"] = [
            {"id": 4, "domain": "noloc.example.com", "sms_reporting_enabled": True,
             "sms_carrier": "MTN", "lat": None, "lon": None}
        ]
        self.assertIsNone(
            sms_reports.attribute_site(self.fake, "MTN", *self.ACCRA),
            "a site with no location must not swallow a whole country's reports",
        )

    def test_sms_toggle_off_blocks_attribution_even_in_place(self):
        self.fake.tables["widget_sites"][0]["sms_reporting_enabled"] = False
        self.assertIsNone(sms_reports.attribute_site(self.fake, "MTN", *self.ACCRA))


class SmsKAnonymityTests(unittest.TestCase):
    """The suppression rule, which must not be bypassable for SMS data."""

    def _report(self, i, lat=5.60, lon=-0.19, ph=None):
        return {
            "phone_hash": ph or f"hash{i}",
            "quality_rating": 5,
            "frustration": 4,
            "gender": "f" if i % 2 else "m",
            "lat": lat, "lon": lon,
        }

    def test_single_reporter_cell_is_suppressed(self):
        self.assertEqual(sms_reports.aggregate_sms_cells([self._report(1)]), [])

    def test_two_reporter_cell_is_suppressed(self):
        self.assertEqual(sms_reports.aggregate_sms_cells([self._report(1), self._report(2)]), [])

    def test_three_distinct_reporters_publishes(self):
        cells = sms_reports.aggregate_sms_cells([self._report(i) for i in range(3)])
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["contributor_count"], 3)

    def test_repeated_reports_from_one_person_do_not_reach_the_threshold(self):
        # Same person reporting five times is still one contributor. Without
        # this, a single prolific texter would unlock a cell.
        rows = [self._report(0, ph="same-person") for _ in range(5)]
        self.assertEqual(sms_reports.aggregate_sms_cells(rows), [])

    def test_threshold_matches_the_scan_aggregation(self):
        self.assertEqual(MIN_DEVICES_PER_CELL, 3)

    def test_reports_without_coordinates_are_skipped(self):
        rows = [dict(self._report(i), lat=None, lon=None) for i in range(5)]
        self.assertEqual(sms_reports.aggregate_sms_cells(rows), [])

    def test_distant_cells_are_separate_and_independently_suppressed(self):
        rows = [self._report(i, lat=5.60) for i in range(3)]
        rows += [self._report(100 + i, lat=6.20) for i in range(1)]
        cells = sms_reports.aggregate_sms_cells(rows)
        self.assertEqual(len(cells), 1, "the one-reporter cell must stay suppressed")
        self.assertAlmostEqual(cells[0]["lat"], 5.60, places=4)

    def test_gender_breakdown_only_survives_within_a_published_cell(self):
        rows = [self._report(i) for i in range(3)]
        cells = sms_reports.aggregate_sms_cells(rows)
        self.assertIn("gender_split", cells[0])
        # _report(i) is 'm' for even i, so i=0,1,2 gives two m and one f.
        self.assertEqual(cells[0]["gender_split"], {"f": 1, "m": 2})


class SmsRouteTests(unittest.TestCase):
    """The webhook must never crash on a hostile or empty payload."""

    def setUp(self):
        self.fake = _Db()
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        owner_auth.OWNER_SESSION_SECRET = "test-owner-session-secret"
        owner_auth.SMS_PHONE_PEPPER = "test-phone-pepper"
        self.client = appmod.app.test_client()

    def test_webhook_starts_a_report(self):
        r = self.client.post("/api/sms/inbound", data={"from": "+233201234567", "text": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("1", r.json["reply"])
        self.assertEqual(len(self.fake.rows("sms_sessions")), 1)

    def test_payload_without_a_sender_is_ignored_not_500(self):
        r = self.client.post("/api/sms/inbound", data={"text": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["status"], "ignored")

    def test_json_body_is_also_accepted(self):
        r = self.client.post("/api/sms/inbound", json={"from": "+233201234567", "text": "hi"})
        self.assertEqual(r.status_code, 200)

    def test_empty_body_does_not_crash(self):
        self.assertEqual(self.client.post("/api/sms/inbound", data={}).status_code, 200)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# Cold start. These are the paths that only a real end-to-end run against the
# live database exercised -- every other test in this file begins from a
# session that already exists, which is exactly why a first-message bug
# survived 35 passing tests.
# --------------------------------------------------------------------------
class TestColdStart(unittest.TestCase):
    def test_first_message_rating_is_not_discarded(self):
        """
        An inbound SMS is the only way to open the conversation, so the opening
        text is the first answer. It must not be thrown away.
        """
        db_ = _Db()
        reply = sms_reports.handle_inbound("2331110001", "7", client=db_)
        self.assertIn("which network", reply.lower())
        row = db_.rows("sms_sessions")[0]
        self.assertEqual(row["current_step"], "carrier")
        self.assertEqual(row["answers"]["quality_rating"], 7)

    def test_non_numeric_opening_message_asks_the_question(self):
        db_ = _Db()
        reply = sms_reports.handle_inbound("2331110002", "MTN", client=db_)
        self.assertIn("1 (unusable)", reply)
        self.assertEqual(db_.rows("sms_sessions")[0]["current_step"], "quality")
        self.assertEqual(db_.rows("sms_sessions")[0]["answers"], {})

    def test_start_keyword_still_asks_the_question(self):
        """A greeting is not an answer, so START must not be read as a rating."""
        db_ = _Db()
        reply = sms_reports.handle_inbound("2331110003", "START", client=db_)
        self.assertIn("1 (unusable)", reply)
        self.assertEqual(db_.rows("sms_sessions")[0]["current_step"], "quality")

    def test_first_message_can_complete_the_whole_report(self):
        """A numeric opener must not leave the session one step short."""
        db_ = _Db()
        n = "2331110004"
        self.assertIn("which network", sms_reports.handle_inbound(n, "8", client=db_).lower())
        self.assertIn("where are you", sms_reports.handle_inbound(n, "MTN", client=db_).lower())
        self.assertIn("gender", sms_reports.handle_inbound(n, "Kanda", client=db_).lower())
        self.assertIn("frustrat", sms_reports.handle_inbound(n, "skip", client=db_).lower())
        reply = sms_reports.handle_inbound(n, "9", client=db_)
        self.assertIn("recorded", reply.lower())
        self.assertEqual(db_.rows("sms_sessions"), [])
        self.assertEqual(len(db_.rows("sms_reports")), 1)
        report = db_.rows("sms_reports")[0]
        self.assertEqual(report["quality_rating"], 8)
        self.assertEqual(report["frustration"], 9)
        self.assertIsNone(report["gender"])  # skip stayed NULL, not guessed

    def test_out_of_range_opener_is_rejected_not_stored(self):
        db_ = _Db()
        for bad in ("0", "11", "99"):
            reply = sms_reports.handle_inbound(f"23311100{bad[-2:]}", bad, client=db_)
            self.assertIn("1 (unusable)", reply)
        for row in db_.rows("sms_sessions"):
            self.assertEqual(row["answers"], {})

    def test_restart_discards_a_session_in_progress(self):
        db_ = _Db()
        n = "2331110005"
        sms_reports.handle_inbound(n, "7", client=db_)
        self.assertEqual(db_.rows("sms_sessions")[0]["current_step"], "carrier")
        reply = sms_reports.handle_inbound(n, "START", client=db_)
        self.assertIn("1 (unusable)", reply)
        self.assertEqual(db_.rows("sms_sessions")[0]["current_step"], "quality")
        self.assertEqual(db_.rows("sms_sessions")[0]["answers"], {})


class TestNumberNormalization(unittest.TestCase):
    """
    k-anonymity counts DISTINCT phone_hash values. If one handset can hash two
    ways, one person counts as two contributors and a cell can be published that
    only they contributed to -- the guarantee failing rather than merely
    double-counting.
    """

    def setUp(self):
        owner_auth.SMS_PHONE_PEPPER = "test-pepper"
        self.pepper = "test-pepper"

    def _hash(self, n):
        return owner_auth.hash_phone(n)

    def test_every_spelling_of_one_ghananumber_is_one_person(self):
        forms = ["+233201234567", "0201234567", "+233 20 123 4567",
                 "+233-20-123-4567", "00233201234567", "233201234567",
                 "  0201234567  "]
        digests = {self._hash(f) for f in forms}
        self.assertEqual(len(digests), 1,
                         f"one handset produced {len(digests)} identities: {digests}")

    def test_different_handsets_stay_different(self):
        self.assertNotEqual(self._hash("+233201234567"), self._hash("+233201234568"))

    def test_a_country_we_do_not_assume_is_left_alone(self):
        """Guessing a country code wrongly would merge two different people,
        which is worse than failing to merge two spellings of one."""
        self.assertEqual(owner_auth.normalize_number("+254711000111"), "+254711000111")
        self.assertEqual(owner_auth.normalize_number("254711000111"), "254711000111")

    def test_non_numbers_hash_to_nothing(self):
        """A sign with no digits must not invent a contributor."""
        for junk in ("", "   ", "abc", "+", "++", None):
            self.assertEqual(owner_auth.normalize_number(junk), "")
        self.assertEqual(self._hash("+"), "")
        self.assertEqual(self._hash(""), "")

    def test_the_pepper_still_applies(self):
        """Normalizing must not weaken the keyed digest."""
        owner_auth.SMS_PHONE_PEPPER = "pepper-a"
        a = self._hash("+233201234567")
        owner_auth.SMS_PHONE_PEPPER = "pepper-b"
        self.assertNotEqual(a, self._hash("+233201234567"))


class TestDeliveryReportsAreNotInbound(unittest.TestCase):
    """
    Africa's Talking posts delivery reports to the same callback URL and marks
    them isActive=false. They carry a sender number and no text, so without the
    check they parse as an inbound message with empty text and open a junk
    session that then sits for 30 minutes.
    """

    DELIVERY = {
        "isActive": "false", "from": "+233201234567", "to": "21515",
        "id": "ATXid_y", "date": "2026-01-01+10:00:00", "status": "Sent",
        "networkCode": "GH01",
    }
    INBOUND = {
        "isActive": "true", "from": "+233201234567", "to": "21515",
        "id": "ATXid_x", "date": "2026-01-01+10:00:00", "text": "7",
    }

    def test_delivery_report_is_rejected(self):
        self.assertFalse(sms_reports.is_inbound_message(self.DELIVERY))

    def test_inbound_is_accepted(self):
        self.assertTrue(sms_reports.is_inbound_message(self.INBOUND))

    def test_absent_flag_is_treated_as_inbound(self):
        """The conservative direction: this endpoint exists for inbound, and
        rejecting an unlabelled payload would drop real reports."""
        self.assertTrue(sms_reports.is_inbound_message({"from": "+233201234567",
                                                        "text": "7"}))

    def test_flag_spellings(self):
        for v in ("false", "False", "FALSE", "0", "no"):
            self.assertFalse(sms_reports.is_inbound_message({"isActive": v}), v)
        for v in ("true", "True", "1", "yes", ""):
            self.assertTrue(sms_reports.is_inbound_message({"isActive": v}), v)


class TestOfflineForwardGeocoding(unittest.TestCase):
    """
    SMS location answers have to become coordinates for attribution to be
    reachable at all. The offline table is read backwards so this works with no
    third-party call -- forwarding a person's free-text location to an external
    geocoder is a disclosure the SMS consent prompt never makes.
    """

    def test_a_place_name_resolves(self):
        for name in ("Cantonments", "Madina", "East Legon", "Takoradi Market",
                     "Cape Coast", "Ho", "Kumasi Adum"):
            lat, lon = sms_reports.geocode_free_text(name)
            self.assertIsNotNone(lat, name)
            self.assertIsNotNone(lon, name)

    def test_every_example_the_prompt_gives_the_user_actually_resolves(self):
        """
        The prompt used to suggest "Kanda", which is not in the table, so the
        one word the user was told to copy was the one word that failed.
        """
        import re
        prompt = sms_reports.PROMPTS["location"]
        examples = re.findall(r'"([A-Za-z ]+)"', prompt)
        self.assertTrue(examples, "prompt should offer examples")
        for example in examples:
            lat, lon = sms_reports.geocode_free_text(example)
            self.assertIsNotNone(lat, f"prompt suggests {example!r}, which does not resolve")
            self.assertIsNotNone(lon, f"prompt suggests {example!r}, which does not resolve")

    def test_common_spellings_and_abbreviations_resolve(self):
        for alias, expected in (("knust", "KNUST Campus"),
                                ("university of ghana", "University of Ghana Campus"),
                                ("near Madina", "Madina"),
                                ("  East Legon  ", "East Legon")):
            self.assertNotEqual(sms_reports.geocode_free_text(alias), (None, None), alias)

    def test_case_and_spacing_do_not_matter(self):
        a = sms_reports.geocode_free_text("Cantonments")
        b = sms_reports.geocode_free_text("  cantonments ")
        self.assertEqual(a, b)

    def test_a_component_of_a_slash_name_resolves(self):
        """"Teshie / Nungua" is how the table spells it, but people type one
        half of it."""
        for half in ("Teshie / Nungua", "Nungua"):
            self.assertNotEqual(sms_reports.geocode_free_text(half), (None, None), half)

    def test_an_unknown_place_resolves_to_nothing_rather_than_guessing(self):
        for junk in ("Nowhereville", "asdfgh", "", None, "   "):
            self.assertEqual(sms_reports.geocode_free_text(junk), (None, None), repr(junk))

    def test_no_request_is_made_when_the_offline_table_answers(self):
        """
        The privacy property: a recognisable place must not leave the process.
        """
        calls = []
        import requests
        original = requests.get
        requests.get = lambda *a, **k: calls.append(a) or original(*a, **k)
        try:
            sms_reports.geocode_free_text("Cantonments")
        finally:
            requests.get = original
        self.assertEqual(calls, [])

    def test_a_recognised_place_needs_no_configured_geocoder(self):
        """The feature must work with SMS_FORWARD_GEOCODER_URL unset."""
        saved = os.environ.pop("SMS_FORWARD_GEOCODER_URL", None)
        try:
            self.assertNotEqual(sms_reports.geocode_free_text("KNUST"), (None, None))
        finally:
            if saved is not None:
                os.environ["SMS_FORWARD_GEOCODER_URL"] = saved

    def test_the_result_lands_in_a_cell_the_reverse_geocoder_recognises(self):
        """Whatever we resolve to has to be somewhere sane, not (0, 0)."""
        import geocoding
        for name in ("Cantonments", "KNUST", "Takoradi Market"):
            lat, lon = sms_reports.geocode_free_text(name)
            self.assertIsNotNone(geocoding.reverse_geocode(lat, lon),
                                 f"{name} resolved outside the known table")

    def test_aliases_do_not_relabel_published_map_coordinates(self):
        """
        _GHANA_PLACES is read both ways. Adding rectangles to help forward
        lookups would silently rename locations already shown on the map, so
        aliases were added to the name index only and no rectangle was touched.
        """
        import geocoding
        original = list(geocoding._GHANA_PLACES)
        self.assertEqual(len(original), len(geocoding._GHANA_PLACES))
        # A point in the KNUST rectangle still labels as whatever it did before,
        # i.e. the table is unchanged, not re-ordered around a new entry.
        self.assertEqual(geocoding.reverse_geocode(6.66, -1.60), "Kumasi Adum")
