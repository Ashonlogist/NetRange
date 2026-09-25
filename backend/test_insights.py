"""
Verification for the analysis layer in insights.py.

The statistics are hand-rolled to keep the render instance dependency-light, so
they are checked against exact closed forms and published critical values
rather than against a second implementation of the same idea.

    cd backend && python3 test_insights.py
"""

import math
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import insights as I


def scan(ts, **kw):
    s = {"timestamp": ts, "lat": 5.58, "lon": -0.24, "device_id": "d1",
         "ssid": "Test", "source": "cellular"}
    s.update(kw)
    return s


class TestSpecialFunctions(unittest.TestCase):
    """Exact closed forms. betainc must match polynomials, t must match Cauchy."""

    def test_betainc_polynomials(self):
        x = 0.37
        self.assertAlmostEqual(I.betainc(1, 1, x), x, places=12)
        self.assertAlmostEqual(I.betainc(1, 2, x), 1 - (1 - x) ** 2, places=12)
        self.assertAlmostEqual(I.betainc(2, 2, x), 3 * x ** 2 - 2 * x ** 3, places=12)
        self.assertAlmostEqual(I.betainc(1, 3, 0.62), 1 - 0.38 ** 3, places=12)
        self.assertAlmostEqual(I.betainc(2, 3, 0.30),
                               6 * .09 - 8 * .027 + 3 * .0081, places=12)
        self.assertAlmostEqual(I.betainc(3, 3, 0.41),
                               10 * .41 ** 3 - 15 * .41 ** 4 + 6 * .41 ** 5, places=12)

    def test_betainc_bounds_and_symmetry(self):
        self.assertAlmostEqual(I.betainc(3, 3, 0.5), 0.5, places=12)
        self.assertAlmostEqual(I.betainc(4, 2, 0.5), 1 - I.betainc(2, 4, 0.5), places=12)
        self.assertEqual(I.betainc(2, 3, 0.0), 0.0)
        self.assertEqual(I.betainc(2, 3, 1.0), 1.0)

    def test_betainc_out_of_range(self):
        for bad in (-0.1, 1.5):
            self.assertIsNone(I.betainc(2, 3, bad))

    def test_betainc_skewed_median_below_half(self):
        # Beta(3,7) is right-skewed: its median is below 0.5, so the CDF at 0.5
        # is well above 0.5. Guards against a "CDF(0.5) is always 0.5" bug.
        self.assertGreater(I.betainc(3, 7, 0.5), 0.85)
        self.assertLess(I.betainc(7, 3, 0.5), 0.15)

    def test_t_two_sided_matches_cauchy(self):
        for t in (0.5, 1.0, 1.96, 3.0, 6.314, 12.706):
            self.assertAlmostEqual(I.t_sf_two_sided(t, 1),
                                   1 - (2 / math.pi) * math.atan(t), places=12)

    def test_t_published_critical_values(self):
        for df, tcrit in [(1, 12.706), (2, 4.303), (3, 3.182), (5, 2.571),
                          (10, 2.228), (20, 2.086), (60, 2.000)]:
            self.assertAlmostEqual(I.t_sf_two_sided(tcrit, df), 0.05, delta=1e-3)

    def test_t_converges_to_normal(self):
        for t in (0.5, 1.96, 2.571):
            self.assertAlmostEqual(I.t_sf_two_sided(t, 10 ** 6),
                                   math.erfc(t / math.sqrt(2)), places=4)

    def test_t_symmetric_and_monotone(self):
        self.assertAlmostEqual(I.t_sf_two_sided(1.5, 7),
                               I.t_sf_two_sided(-1.5, 7), places=15)
        vals = [I.t_sf_two_sided(t, 7) for t in (0, 0.5, 1, 1.5, 2, 3, 5, 10)]
        self.assertTrue(all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)))


class TestDescribe(unittest.TestCase):
    def test_percentiles_linear_interpolation(self):
        d = I.describe([1, 2, 3, 4])
        self.assertEqual(d["n"], 4)
        self.assertEqual(d["min"], 1)
        self.assertEqual(d["max"], 4)
        self.assertEqual(d["median"], 2.5)
        self.assertEqual(d["p25"], 1.75)
        self.assertEqual(d["p75"], 3.25)

    def test_even_vs_odd_median(self):
        self.assertEqual(I.describe([1, 2, 3])["median"], 2)
        self.assertEqual(I.describe([4, 1, 3, 2])["median"], 2.5)

    def test_constant_series_has_zero_spread(self):
        d = I.describe([-70] * 30)
        self.assertEqual(d["stdev"], 0.0)
        self.assertEqual(d["min"], d["max"])
        self.assertEqual(d["outlier_count"], 0)

    def test_iqr_outlier_detection(self):
        d = I.describe([1, 2, 3, 4, 5, 6, 7, 8, 9, 100])
        self.assertEqual(d["outlier_count"], 1)

    def test_iqr_fence_both_sides_with_nonzero_spread(self):
        xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        lo, hi = I.iqr_fences(xs)
        self.assertLess(lo, min(xs))
        self.assertGreater(hi, max(xs))

    def test_zero_iqr_flags_both_tails(self):
        # With an IQR of exactly zero, the fences collapse onto the single
        # repeated value, so the low value is an outlier too. This is the
        # mathematically correct outcome, not a bug.
        d = I.describe([1, 2, 2, 2, 2, 2, 2, 2, 2, 100])
        self.assertEqual(d["outlier_count"], 2)

    def test_empty_series(self):
        self.assertIsNone(I.describe([]))

    def test_bootstrap_ci_brackets_median(self):
        xs = [float(i) for i in range(1, 41)]
        ci = I.describe(xs)["median_ci"]
        self.assertLess(ci["low"], I.describe(xs)["median"])
        self.assertGreater(ci["high"], I.describe(xs)["median"])

    def test_ci_withheld_when_sample_too_small(self):
        # A bootstrap median over a handful of points collapses to zero width,
        # which reads as certainty. It is withheld below the threshold instead.
        d = I.describe([4.79, 9.0, 11.38, 19.61, 34.63, 7.2, 15.0, 22.5])
        self.assertIsNone(d["median_ci"])
        self.assertIn(str(I.MIN_SAMPLES_FOR_CONFIDENCE_INTERVAL), d["median_ci_withheld"])


class TestComparisons(unittest.TestCase):
    def test_welch_detects_separated_samples(self):
        a = [10.1, 10.4, 9.9, 10.3, 10.2, 10.0]
        b = [20.1, 20.4, 19.9, 20.3, 20.2, 20.0]
        r = I.welch_t_test(a, b)
        self.assertLess(r["p_value"], 0.01)
        self.assertLess(r["mean_difference"], 0)
        self.assertGreater(abs(I.cohens_d(a, b)["d"]), 1.0)
        self.assertEqual(I.cohens_d(a, b)["magnitude"], "large")

    def test_welch_on_identical_samples_is_not_significant(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(I.welch_t_test(a, list(a))["p_value"], 1.0)

    def test_welch_needs_enough_samples(self):
        self.assertIsNone(I.welch_t_test([1.0], [2.0]))

    def test_cohens_d_undefined_for_constant_groups(self):
        self.assertIsNone(I.cohens_d([1.0] * 5, [2.0] * 5))

    def test_linear_trend_recovers_known_slope(self):
        fit = I.linear_trend([0, 1, 2, 3, 4], [0.0, 1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(fit["slope"], 1.0, places=9)
        self.assertAlmostEqual(fit["r_squared"], 1.0, places=9)

    def test_linear_trend_ci_covers_true_slope(self):
        fit = I.linear_trend([0, 1, 2, 3, 4], [0.0, 1.0, 2.0, 3.0, 4.0])
        lo, hi = fit["slope_ci"]
        self.assertLessEqual(lo, 1.0)
        self.assertGreaterEqual(hi, 1.0)

    def test_linear_trend_rejects_degenerate_input(self):
        self.assertIsNone(I.linear_trend([1.0], [1.0]))
        self.assertIsNone(I.linear_trend([0, 0, 0], [1.0, 2.0, 3.0]))

    def test_flat_series_is_never_called_significant(self):
        # Every daily median identical -> no p-value -> must not read as a trend.
        self.assertIn("too flat", I._trend_verdict(I.linear_trend([0, 1, 2, 3],
                                                                 [5.0] * 4)))
        self.assertEqual(I._trend_verdict(None), "trend not computable")

    def test_trend_verdict_reads_slope_direction(self):
        rising = I.linear_trend([0, 1, 2, 3, 4], [0.0, 1.0, 2.0, 3.0, 4.0])
        self.assertIn("rising", I._trend_verdict(rising))
        falling = I.linear_trend([0, 1, 2, 3, 4], [4.0, 3.0, 2.0, 1.0, 0.0])
        self.assertIn("falling", I._trend_verdict(falling))
        self.assertIn("no significant", I._trend_verdict(
            I.linear_trend([0, 1, 2, 3, 4, 5, 6, 7],
                           [5.0, 5.4, 4.6, 5.5, 4.5, 5.2, 4.8, 5.1])))


class TestIntegrityGating(unittest.TestCase):
    """The point of the layer: a placeholder must never look like data."""

    def _many(self, n=85, **kw):
        base = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        return [scan((base + timedelta(minutes=7 * i)).isoformat().replace("+00:00", "Z"),
                     **kw) for i in range(n)]

    def test_constant_column_is_critical(self):
        f = I.integrity_checks(self._many(signal_dbm=-70))["findings"]
        hit = [x for x in f if x["id"] == "constant.signal_dbm"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["severity"], "critical")

    def test_placeholder_column_blocks_its_distribution(self):
        d = I.metric_distributions(self._many(signal_dbm=-70))
        self.assertEqual(d["signal_dbm"]["status"], "unusable")
        self.assertIsNone(d["signal_dbm"]["data"])

    def test_genuinely_varying_column_is_usable(self):
        rows = self._many()
        for i, r in enumerate(rows):
            r["signal_dbm"] = -50 - (i % 25)
        d = I.metric_distributions(rows)
        self.assertEqual(d["signal_dbm"]["status"], "ok")
        self.assertIsNotNone(d["signal_dbm"]["data"])

    def test_near_constant_column_warns(self):
        vals = [100.0] * 75 + [12.0, 45.0, 88.0, 91.0, 77.0, 60.0, 33.0, 21.0, 9.0, 5.0]
        rows = self._many()
        for r, v in zip(rows, vals):
            r["accuracy"] = v
        d = I.metric_distributions(rows)
        self.assertEqual(d["accuracy"]["status"], "unusable")

    def test_capped_gps_accuracy_is_not_flagged(self):
        # The live data has accuracy=100 for ~78% of scans. That is a real
        # capped GPS field with genuine variation, not a placeholder, so the
        # near-constant rule must not reject it.
        vals = [100.0] * 66 + [52.75, 3.0, 3.0, 3.0, 9.6, 9.6, 45.6, 346.35,
                               27.2, 122.4, 12.0, 45.0, 88.0, 91.0, 77.0, 60.0,
                               33.0, 21.0, 9.0]
        rows = self._many()
        for r, v in zip(rows, vals):
            r["accuracy"] = v
        self.assertEqual(I.metric_distributions(rows)["accuracy"]["status"], "ok")

    def test_non_numeric_values_do_not_crash(self):
        rows = self._many()
        for i, r in enumerate(rows):
            r["accuracy"] = "unknown" if i < 5 else 10.0 + (i % 30)
        d = I.metric_distributions(rows)
        self.assertEqual(d["accuracy"]["status"], "ok")
        findings = I.integrity_checks(rows)["findings"]
        self.assertTrue(any(f["id"] == "type.accuracy" for f in findings))

    def test_non_numeric_only_column_is_unusable(self):
        rows = self._many()
        for r in rows:
            r["accuracy"] = "unknown"
        d = I.metric_distributions(rows)
        self.assertEqual(d["accuracy"]["status"], "unusable")
        self.assertIsNone(d["accuracy"]["data"])

    def test_signal_layer_reported_as_placeholder(self):
        L = I.layer_usability(self._many(signal_dbm=-70))
        self.assertEqual(L["layers"]["signal_dbm"]["status"], "placeholder")
        self.assertEqual(L["layers"]["download_speed_mbps"]["status"], "unavailable")

    def test_speed_layer_usable_when_speed_varies(self):
        rows = self._many(download_speed_mbps=5.0)
        for i, r in enumerate(rows):
            r["download_speed_mbps"] = 3.0 + (i % 30)
        L = I.layer_usability(rows)
        self.assertEqual(L["layers"]["download_speed_mbps"]["status"], "usable")
        self.assertEqual(L["layers"]["signal_dbm"]["status"], "unavailable")

    def test_bundle_is_self_consistent(self):
        """No section may report ok for a field integrity rejected."""
        b = I.build_insights(self._many(signal_dbm=-70, download_speed_mbps=4.0))
        rejected = {f["id"].split(".", 1)[1] for f in b["integrity"]["findings"]
                    if f["id"].startswith(("constant.", "near_constant."))}
        self.assertTrue(rejected, "fixture should trigger at least one rejection")
        for metric in rejected:
            self.assertNotEqual(b["distributions"][metric]["status"], "ok")
            self.assertIsNone(b["distributions"][metric]["data"])
            self.assertNotEqual(b["layers"]["layers"].get(metric, {}).get("status"), "usable")


class TestSufficiencyGates(unittest.TestCase):
    def test_single_device_blocks_carrier_comparison(self):
        rows = [scan("2026-01-01T10:00:00Z", download_speed_mbps=5.0, device_id="d1")
                for _ in range(40)]
        self.assertEqual(I.carrier_analysis(rows)["status"], "insufficient")

    def test_one_day_blocks_trend(self):
        rows = [scan("2026-01-01T10:00:00Z", download_speed_mbps=5.0) for _ in range(40)]
        self.assertEqual(I.time_trend(rows)["status"], "insufficient")

    def test_thin_hour_buckets_excluded_from_diurnal(self):
        # Six distinct hours is not enough if five of them hold a single scan.
        rows = [scan("2026-01-01T08:00:00Z", download_speed_mbps=5.0) for _ in range(20)]
        rows += [scan("2026-01-01T03:00:00Z", download_speed_mbps=5.0)]
        rows += [scan("2026-01-01T09:00:00Z", download_speed_mbps=5.0)]
        d = I.diurnal_profile(rows)
        self.assertEqual(d["status"], "insufficient")

    def test_empty_dataset_is_safe(self):
        b = I.build_insights([])
        self.assertEqual(b["sufficiency"]["total_scans"], 0)
        self.assertFalse(b["integrity"]["clean"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
