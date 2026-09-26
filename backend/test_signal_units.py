"""
Tests for signal-strength unit normalization.

Every coverage cell, weak-zone list and quality metric is an average of
signal_dbm, so a unit mistake here does not crash anything -- it quietly
skews the entire dataset. These tests pin the units down explicitly.

DASHBOARD_* are set defensively so this module can be imported on its own.
"""

import os
import sys
import unittest

import tests_env  # noqa: F401  (must precede `import app`)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner import signal_pct_to_dbm, signal_to_dbm  # noqa: E402


class SignalToDbmTests(unittest.TestCase):
    """Android WifiManager n.level is already dBm; nmcli SIGNAL is percent."""

    def test_negative_value_is_already_dbm_and_passes_through(self):
        # This is the shape the app actually sends: strength = n.level.
        self.assertEqual(signal_to_dbm(-55), -55.0)
        self.assertEqual(signal_to_dbm(-70), -70.0)
        self.assertEqual(signal_to_dbm(-91), -91.0)

    def test_percent_converts_using_pct_over_two_minus_hundred(self):
        self.assertEqual(signal_to_dbm(0), -100.0)
        self.assertEqual(signal_to_dbm(50), -75.0)
        self.assertEqual(signal_to_dbm(100), -50.0)

    def test_dbm_and_percent_agree_at_the_shared_anchor(self):
        # -100 dBm is 0% and -50 dBm is 100%, so the two unit paths meet.
        self.assertEqual(signal_to_dbm(0), signal_pct_to_dbm(0))
        self.assertEqual(signal_to_dbm(100), signal_pct_to_dbm(100))

    def test_result_is_always_rounded_to_one_decimal(self):
        for raw in (-55, -55.55, 33, 33.3):
            v = signal_to_dbm(raw)
            self.assertEqual(v, round(v, 1))
            self.assertEqual(len(str(v).split(".")[1]), 1)

    def test_missing_reading_stays_missing(self):
        # NetInfo has no signal field, so cellular strength is always None.
        # Guessing here would invent a measurement.
        self.assertIsNone(signal_to_dbm(None))
        self.assertIsNone(signal_to_dbm(""))
        self.assertIsNone(signal_to_dbm("weak"))

    def test_bool_is_not_mistaken_for_a_percentage(self):
        # bool subclasses int; True would otherwise become 1% -> -99.5 dBm.
        self.assertIsNone(signal_to_dbm(True))
        self.assertIsNone(signal_to_dbm(False))

    def test_out_of_range_is_refused_not_invented(self):
        self.assertIsNone(signal_to_dbm(150))
        self.assertIsNone(signal_to_dbm(1000))

    def test_removed_zero_to_one_fraction_branch(self):
        # The old code mapped 0..1 as strength*50-100. Nothing produced a
        # 0-1 fraction, so 0 and 1 are now ordinary percentages:
        #   0% -> -100 dBm, 1% -> -99.5 dBm
        # Previously: 0 -> -100.0, 1 -> -50.0 (a 50 dBm error).
        self.assertEqual(signal_to_dbm(0), -100.0)
        self.assertEqual(signal_to_dbm(1), -99.5)

    def test_agrees_with_legacy_percent_helper(self):
        for pct in (0, 1, 25, 50, 75, 99, 100):
            self.assertEqual(signal_to_dbm(pct), signal_pct_to_dbm(pct))


class ScanDisplayFormatTests(unittest.TestCase):
    """Item 12: scanner's __main__ formatted a float with an int format code."""

    def test_dbm_formats_as_float_without_raising(self):
        dbm = signal_pct_to_dbm(70)  # -65.0, a float
        self.assertIsInstance(dbm, float)
        self.assertEqual(f"{dbm:>5.1f}", "-65.0")
        # Width is a minimum, so a narrower value still pads correctly.
        self.assertEqual(f"{-5.0:>5.1f}", " -5.0")
        # The old spec raised ValueError: Unknown format code 'd'.
        with self.assertRaises(ValueError):
            f"{dbm:>5d}"


if __name__ == "__main__":
    unittest.main()


class ExplicitDbmTests(unittest.TestCase):
    """`signalDbm` from TelephonyManager must not be re-guessed.

    The cellular path used to send `signalStrength`, a field with no units,
    which the server had to interpret as dBm-or-percentage. A real reading off
    SignalStrength is unambiguous, so it is taken as-is; anything absent still
    becomes NULL rather than an invented number.
    """

    def _post_cellular(self, cellular):
        import app as appmod
        c = appmod.app.test_client()
        token = c.post("/api/register-device",
                       json={"deviceId": "unittest-device"}).json["token"]
        return c.post("/api/scan",
                      json={"wifi": [], "cellular": cellular,
                            "location": {"lat": 5.6, "lon": -0.2},
                            "deviceId": "unittest-device"},
                      headers={"Authorization": f"Bearer {token}"})

    def setUp(self):
        import app as appmod
        from test_device_auth import _FakeClient
        self.fake = _FakeClient()
        import db
        db._client = self.fake
        appmod.get_client = lambda: self.fake
        self.rows = []
        self._real_save = appmod.save_scan
        appmod.save_scan = lambda recs: self.rows.extend(recs) or len(recs)

    def tearDown(self):
        import app as appmod
        appmod.save_scan = self._real_save

    def test_signal_dbm_taken_verbatim(self):
        self._post_cellular({"carrier": "Telecel", "signalDbm": -83.0,
                             "networkType": "4G", "isConnected": True})
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[0]["signal_dbm"], -83.0)
        self.assertEqual(self.rows[0]["ssid"], "Telecel")

    def test_signal_dbm_zero_is_a_real_reading_not_a_missing_one(self):
        # 0 dBm is a legitimate (if implausible) reading. A truthiness check
        # would discard it and the scan would vanish from the map.
        self._post_cellular({"carrier": "Telecel", "signalDbm": 0,
                             "networkType": "4G", "isConnected": True})
        self.assertEqual(self.rows[0]["signal_dbm"], 0.0)

    def test_missing_signal_stays_null(self):
        self._post_cellular({"carrier": "Telecel", "networkType": "4G",
                             "isConnected": True})
        self.assertIsNone(self.rows[0]["signal_dbm"])

    def test_signal_dbm_wins_over_ambiguous_signal_strength(self):
        self._post_cellular({"carrier": "Telecel", "signalDbm": -77,
                             "signalStrength": 42})
        self.assertEqual(self.rows[0]["signal_dbm"], -77.0)

    def test_legacy_signal_strength_still_converted(self):
        # Older builds send only signalStrength; a negative value is dBm.
        self._post_cellular({"carrier": "MTN", "signalStrength": -66,
                             "networkType": "4G", "isConnected": True})
        self.assertEqual(self.rows[0]["signal_dbm"], -66.0)

    def test_bool_is_not_a_reading(self):
        # bool is an int subclass in Python; True must not become 1 dBm.
        self._post_cellular({"carrier": "Telecel", "signalDbm": True})
        self.assertIsNone(self.rows[0]["signal_dbm"])


class HeatmapSkipReportingTests(unittest.TestCase):
    """An empty heatmap must say *why*, not just be empty.

    Every cellular scan used to be saved with a null signal, so the map was
    always empty while the app cheerfully reported saved rows. The count of
    dropped rows is what makes that diagnosable from the phone.
    """

    def setUp(self):
        import app as appmod
        self.appmod = appmod
        self._real = appmod.load_scans
        appmod.load_scans = lambda: [
            {"ssid": "Telecel", "lat": 5.6, "lon": -0.2, "signal_dbm": -80},
            {"ssid": "Telecel", "lat": 5.7, "lon": -0.3, "signal_dbm": None},
            {"ssid": "Telecel", "lat": 5.8, "lon": -0.4, "signal_dbm": None},
            {"ssid": "Other", "lat": 5.9, "lon": -0.5, "signal_dbm": -70},
        ]

    def tearDown(self):
        self.appmod.load_scans = self._real

    def test_reports_points_and_skipped_count(self):
        c = self.appmod.app.test_client()
        d = c.get("/api/heatmap?ssid=Telecel").get_json()
        self.assertEqual(len(d["points"]), 1)
        self.assertEqual(d["skipped_no_signal"], 2)

    def test_other_targets_are_not_counted(self):
        c = self.appmod.app.test_client()
        d = c.get("/api/heatmap?ssid=Nothing").get_json()
        self.assertEqual(d["points"], [])
        self.assertEqual(d["skipped_no_signal"], 0)
