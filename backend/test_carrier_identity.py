"""
Carrier identity: deciding which network a cellular scan belongs to.

The bug these lock down: `ssid` was the carrier *name* the phone displayed,
and it was used as the grouping key for coverage. A name is not an identity.
It changes when a carrier rebrands (62002 is branded Telecel on the SIM while
the network is Vodafone Ghana), when Android rewrites it from a different
source, and when a user sets an override in the app. The live data already had
the same network under two spellings -- 'Telecel' and 'telecel' -- which split
one carrier's coverage in two for no reason a user could see.

Attribution is now keyed on the MCC/MNC, with the name kept only as a label.
"""

import unittest

from algorithm import (
    GHANA_MNC,
    carrier_from_name,
    carrier_identity,
    normalize_plmn,
    prepare_points,
    scan_group_key,
    scan_matches,
)


def scan(ssid=None, numeric=None, identity=None, source="cellular",
         lat=5.6, lon=-0.2, dbm=-80, timestamp="2026-01-01T00:00:00Z"):
    return {
        "ssid": ssid,
        "source": source,
        "lat": lat,
        "lon": lon,
        "signal_dbm": dbm,
        "timestamp": timestamp,
        "carrier_numeric": numeric,
        "carrier_identity": identity,
    }


class NormalizePlmn(unittest.TestCase):
    def test_accepts_the_shapes_android_emits(self):
        self.assertEqual(normalize_plmn("62002"), "62002")
        self.assertEqual(normalize_plmn(" 62002 "), "62002")
        self.assertEqual(normalize_plmn(62002), "62002")
        self.assertEqual(normalize_plmn("620-02"), "62002")

    def test_rejects_placeholders_and_junk(self):
        # 65535 and 0 are what OEMs emit when they will not say. Treating
        # either as a real PLMN would group unrelated networks together.
        for bad in (None, "", "0", "00000", "65535", "620", True, False, [], {}):
            self.assertIsNone(normalize_plmn(bad), bad)

    def test_booleans_are_not_treated_as_numbers(self):
        # isinstance(True, int) is True in Python, so this needs saying.
        self.assertIsNone(normalize_plmn(True))


class CarrierIdentity(unittest.TestCase):
    def test_plmn_decides_identity_and_the_name_only_labels_it(self):
        identity, display, canonical = carrier_identity("62002", "Telecel")
        self.assertEqual(identity, "62002")
        self.assertEqual(display, "Telecel")
        self.assertEqual(canonical, "Telecel")

    def test_a_stale_display_name_cannot_change_the_group(self):
        # The whole point: same network, different label, one bucket.
        self.assertEqual(carrier_identity("62001", "MTN GH")[0],
                         carrier_identity("62001", "MTN")[0])

    def test_display_falls_back_to_a_name_we_recognise(self):
        # "MTN GH" is the string on the SIM; the label a user reads is "MTN".
        self.assertEqual(carrier_identity("62001", "MTN GH")[1], "MTN")

    def test_unknown_plmn_passes_through_rather_than_being_guessed(self):
        identity, display, canonical = carrier_identity("62099", "Zebra")
        self.assertEqual(identity, "62099")
        self.assertEqual(display, "Zebra")
        self.assertIsNone(canonical)

    def test_ghana_networks_all_resolve(self):
        for mnc in GHANA_MNC:
            identity, display, canonical = carrier_identity("620" + mnc, "whatever")
            self.assertEqual(identity, "620" + mnc)
            self.assertEqual(canonical, GHANA_MNC[mnc])
            self.assertEqual(display, GHANA_MNC[mnc])

    def test_at_has_two_mncs_and_both_are_the_same_network(self):
        # 620-03 and 620-06 are both AT. Keyed by MNC they would be two
        # networks, which is the split this exists to prevent.
        a = carrier_identity("62003", "AT")[0]
        b = carrier_identity("62006", "AT")[0]
        self.assertNotEqual(a, b)
        self.assertEqual(carrier_identity("62003", "AT")[1],
                         carrier_identity("62006", "AT")[1])

    def test_without_a_plmn_the_name_is_all_there_is(self):
        identity, display, _ = carrier_identity(None, "Telecel")
        self.assertEqual(identity, "telecel")
        self.assertEqual(display, "Telecel")

    def test_absent_everything_still_yields_a_label(self):
        self.assertEqual(carrier_identity(None, None)[1], "Cellular")


class CarrierFromName(unittest.TestCase):
    """Only used to backfill rows stored before carrier_numeric existed."""

    def test_recognises_the_names_in_the_live_data(self):
        self.assertEqual(carrier_from_name("MTN GH"), "62001")
        self.assertEqual(carrier_from_name("Telecel"), "62002")
        self.assertEqual(carrier_from_name("telecel"), "62002")

    def test_refuses_to_guess(self):
        # A wrong answer here would attach a network's coverage to the wrong
        # operator, which is worse than leaving the row alone.
        for name in ("Zesty", "Cellular", "", None, "vodafone"):
            self.assertIsNone(carrier_from_name(name), name)


class ScanGroupKey(unittest.TestCase):
    def test_prefers_the_stable_identity(self):
        self.assertEqual(scan_group_key(scan("Telecel", identity="62002")), "62002")

    def test_case_variants_of_one_network_collapse(self):
        # 'Telecel' and 'telecel' are both in production right now.
        self.assertEqual(scan_group_key(scan("Telecel")),
                         scan_group_key(scan("telecel")))

    def test_different_networks_do_not_collapse(self):
        self.assertNotEqual(scan_group_key(scan("MTN GH")),
                            scan_group_key(scan("Telecel")))

    def test_wifi_rows_are_unaffected(self):
        row = scan("HomeWifi", source="mobile")
        self.assertEqual(scan_group_key(row), "homewifi")


class ScanMatches(unittest.TestCase):
    def test_matches_on_either_the_key_or_the_label(self):
        row = scan("Telecel", identity="62002", numeric="62002")
        self.assertTrue(scan_matches(row, "62002"))
        self.assertTrue(scan_matches(row, "telecel"))

    def test_a_legacy_row_still_matches_its_display_name(self):
        self.assertTrue(scan_matches(scan("MTN GH"), "mtn gh"))

    def test_a_query_is_normalised_the_same_way_the_key_is(self):
        # The bug this locks down: the row's key is lowercased but the query
        # was not, so any label with capitals matched nothing. The old
        # exact-match filter had the same flaw, which made "MTN GH" -- a real
        # display name on the device -- unfilterable.
        row = scan("MTN GH")
        self.assertTrue(scan_matches(row, "MTN GH"))
        self.assertTrue(scan_matches(row, "  MTN GH  "))
        self.assertTrue(scan_matches(row, "mtn gh"))
        self.assertTrue(scan_matches(scan("Telecel"), "TELECEL"))

    def test_a_backfilled_row_matches_both_of_its_names(self):
        # ssid keeps what the phone showed; carrier_name is the canonical
        # label. Filtering on either has to find the row.
        row = scan("MTN GH", numeric="62001")
        row["carrier_name"] = "MTN"
        self.assertTrue(scan_matches(row, "MTN"))
        self.assertTrue(scan_matches(row, "MTN GH"))
        self.assertTrue(scan_matches(row, "62001"))

    def test_the_operator_name_matches_even_with_no_name_stored(self):
        # Nothing recorded but the PLMN, and the UI shows "MTN".
        row = scan(None, numeric="62001")
        self.assertTrue(scan_matches(row, "MTN"))
        self.assertTrue(scan_matches(row, "62001"))
        # But the SIM's own raw label is not recoverable from a PLMN alone, so
        # it must NOT match. Inventing that association is the failure this
        # whole mechanism exists to prevent.
        self.assertFalse(scan_matches(row, "MTN GH"))

    def test_a_name_that_is_not_this_carrier_does_not_match(self):
        row = scan("MTN GH", numeric="62001")
        self.assertFalse(scan_matches(row, "Telecel"))
        self.assertFalse(scan_matches(row, "62002"))

    def test_a_whitespace_only_filter_is_treated_as_no_filter(self):
        self.assertTrue(scan_matches(scan("MTN GH"), "   "))

    def test_empty_filter_matches_everything(self):
        self.assertTrue(scan_matches(scan("MTN GH"), ""))
        self.assertTrue(scan_matches(scan("MTN GH"), None))

    def test_a_different_network_does_not_match(self):
        self.assertFalse(scan_matches(scan("Telecel", identity="62002"), "62001"))


class PreparePointsFiltering(unittest.TestCase):
    def test_a_rebrand_does_not_drop_rows_from_a_name_filter(self):
        # Two rows for one network: one recorded before the numeric existed and
        # one after. Filtering on the label must keep both -- a rename must not
        # silently drop half a network's history.
        old = scan("Telecel", lat=5.60, dbm=-80)
        new = scan("Vodafone Ghana", identity="62002", numeric="62002",
                   lat=5.61, dbm=-85)
        self.assertEqual(len(prepare_points([old, new], "telecel")), 2)
        self.assertEqual(len(prepare_points([old, new], "Telecel")), 2)

    def test_a_plmn_filter_only_returns_rows_that_actually_carry_it(self):
        # The honest limit of the backfill: a row stored before carrier_numeric
        # existed cannot answer "62002?", so it is excluded rather than guessed.
        # The migration resolves those rows once, in carrier_from_name().
        old = scan("Telecel", lat=5.60, dbm=-80)
        new = scan("Vodafone Ghana", identity="62002", numeric="62002",
                   lat=5.61, dbm=-85)
        self.assertEqual(len(prepare_points([old, new], "62002")), 1)

    def test_case_variant_collapse_end_to_end(self):
        rows = [scan("Telecel", lat=5.60), scan("telecel", lat=5.61)]
        self.assertEqual(len(prepare_points(rows, "telecel")), 2)

    def test_networks_stay_separate(self):
        rows = [scan("MTN GH", identity="62001", numeric="62001", lat=5.60),
                scan("Telecel", identity="62002", numeric="62002", lat=5.61)]
        self.assertEqual(len(prepare_points(rows, "62001")), 1)
        self.assertEqual(len(prepare_points(rows, "62002")), 1)
        self.assertEqual(len(prepare_points(rows)), 2)


if __name__ == "__main__":
    unittest.main()
