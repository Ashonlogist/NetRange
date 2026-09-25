"""
Internal analysis layer for the admin panel.

This sits deliberately alongside analytics.py rather than inside it, because
the two have opposite privacy postures:

  analytics.py    the *publication* layer. k-anonymity by suppression, no
                  device_id ever leaves, safe to export or sell.
  insights.py     the *diagnostic* layer. Owner-only, behind dashboard auth,
                  answers "what is actually in this dataset and what can be
                  concluded from it".

The hard problem with an admin panel over a young dataset is not rendering --
it is that empty output is ambiguous. A blank table can mean "no problems
found" or "not enough data to say". Those demand opposite responses from the
reader, and most dashboards conflate them, which is how a broken pipeline
gets mistaken for a healthy network.

So every function here returns a verdict rather than a bare value:

    {
      "id":      "carrier_comparison",
      "status":  "ok" | "insufficient" | "unavailable",
      "why":     "needs >= 2 carriers, have 1",
      "need":    {...},   # what the analysis requires
      "have":    {...},   # what the dataset actually provides
      "data":    {...} | None,
    }

The UI never has to guess. An "insufficient" verdict renders as an
explanation of what is missing and how far off it is, not as an empty box.

No third-party dependencies: the deploy target is a plain Python service and
scipy/numpy are not available there. Statistics are implemented directly and
unit-checked against published critical values in tests/test_insights.py.
"""

import math
import time
from collections import defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Requirement thresholds. Every one of these is a judgement about when a
# statistic stops being noise and starts being information; they are
# collected here so the panel can state them out loud rather than hide them.
# ---------------------------------------------------------------------------

MIN_SAMPLES_FOR_DISTRIBUTION = 20   # below this a "distribution" is a scatter
MIN_SAMPLES_FOR_CONFIDENCE_INTERVAL = 30   # bootstrap median CI collapses below this
MIN_CARRIERS_FOR_COMPARISON = 2     # comparing one carrier to nothing
MIN_DEVICES_PER_CARRIER = 3         # matches the publication k-threshold
MIN_SAMPLES_PER_CARRIER = 30
MIN_DAYS_FOR_TREND = 7              # a week is the floor for "trending"
MIN_DAYS_FOR_WEEKLY = 14            # two weeks before day-of-week means anything
MIN_HOURS_FOR_DIURNAL = 6           # distinct hours of day with data
MIN_SAMPLES_PER_BUCKET = 5          # ...but a median over 1 sample is not a median
NEAR_CONSTANT_SHARE = 0.8           # share of one value that suggests a default, not a measurement
MIN_TOTAL_SAMPLES = 50
STALE_AFTER_DAYS = 7                # newest data older than this is stale

# A column that never varies carries no information. Detecting this matters:
# a fabricated or defaulted measurement looks identical to a real one in a
# count-based chart, and only a variance check catches it.
CONSTANT_VALUE_EPSILON = 1e-9

_METRICS = ("download_speed_mbps", "signal_dbm", "accuracy")


# ---------------------------------------------------------------------------
# Statistics. Small, dependency-free, and checked against known values.
# ---------------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _variance(xs, ddof=1):
    n = len(xs)
    if n - ddof <= 0:
        return None
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - ddof)


def _stdev(xs, ddof=1):
    v = _variance(xs, ddof)
    return None if v is None else math.sqrt(v)


def percentile(xs, p):
    """
    Linear-interpolation percentile (the "R type 7" / numpy default method),
    so it matches what anyone would get from numpy.percentile for comparison.
    p is 0-100.
    """
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(s[int(k)])
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def iqr_fences(xs, k=1.5):
    """Tukey fences. Points outside are conventionally called outliers."""
    q1, q3 = percentile(xs, 25), percentile(xs, 75)
    if q1 is None or q3 is None:
        return None, None
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny, eps, itmax = 1e-30, 3e-16, 300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b).

    Returns None for arguments outside the domain rather than clamping them.
    Clamping x=1.5 to 0.0 would silently turn a bad call into a confident
    p-value of zero, which is the worst possible failure mode for a statistic.
    """
    if a <= 0 or b <= 0:
        return None
    if x < 0.0 or x > 1.0:
        return None
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta + b * math.log(1.0 - x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def t_sf_two_sided(t, df):
    """Two-sided p-value for Student's t."""
    if df <= 0 or t is None:
        return None
    t = abs(t)
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def welch_t_test(a, b):
    """
    Welch's unequal-variance t-test. Preferred over Student's because scan
    speeds across carriers rarely share a variance, and assuming they do
    produces confidently wrong p-values.
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    va, vb = _variance(a), _variance(b)
    if va is None or vb is None or (va == 0 and vb == 0):
        return None
    se2 = va / na + vb / nb
    if se2 <= 0:
        return None
    t = (_mean(a) - _mean(b)) / math.sqrt(se2)
    df = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return {
        "t": round(t, 4),
        "df": round(df, 2),
        "p_value": round(t_sf_two_sided(t, df), 6),
        "mean_difference": round(_mean(a) - _mean(b), 4),
    }


def cohens_d(a, b):
    """Standardised mean difference, with the pooled SD both groups share."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    va, vb = _variance(a), _variance(b)
    pooled = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled <= 0:
        return None
    d = (_mean(a) - _mean(b)) / math.sqrt(pooled)
    mag = ("negligible" if abs(d) < 0.2 else
           "small" if abs(d) < 0.5 else
           "moderate" if abs(d) < 0.8 else "large")
    return {"d": round(d, 4), "magnitude": mag}


def bootstrap_median_ci(xs, iterations=2000, alpha=0.05, seed=12345):
    """
    Percentile bootstrap CI for the median. Used instead of a normal
    approximation because speed distributions are visibly right-skewed,
    which makes the normal-approximation interval wrong in exactly the
    direction that matters (too narrow, too confident).
    Deterministic via a fixed LCG so the panel does not jitter between loads.
    """
    n = len(xs)
    if n < 5:
        return None
    state = seed
    meds = []
    for _ in range(iterations):
        sample = []
        for _ in range(n):
            state = (1103515245 * state + 12345) % (1 << 31)
            sample.append(xs[state % n])
        meds.append(_median(sample))
    meds.sort()
    lo = percentile(meds, alpha / 2 * 100)
    hi = percentile(meds, (1 - alpha / 2) * 100)
    return {"low": round(lo, 3), "high": round(hi, 3), "confidence": int((1 - alpha) * 100)}


def linear_trend(xs, ys):
    """
    Ordinary least squares of ys on xs, with a 95% CI on the slope. Returns
    the slope in units of ys per x, so callers choose readable x (e.g.
    days since first observation).
    """
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in resid)
    sst = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - sse / sst if sst > 0 else None
    dof = n - 2
    se_slope = None
    p_value = None
    if sst <= 0:
        # Every y is identical: there is no variation to explain and no line
        # to fit. r2 stays None and no trend may be claimed.
        return {
            "slope": 0.0, "slope_ci": None, "intercept": my,
            "r_squared": None, "p_value": None, "n": n,
        }
    if sse == 0:
        # Perfect fit: zero residual, so the slope is determined exactly.
        # Reporting p=None here would let a dead-flat line be called a trend.
        significant = slope != 0.0
        p_value = 0.0 if significant else 1.0
        return {
            "slope": round(slope, 6), "slope_ci": [round(slope, 6), round(slope, 6)],
            "intercept": round(intercept, 6), "r_squared": 1.0,
            "p_value": p_value, "n": n,
        }
    if dof > 0:
        se_slope = math.sqrt((sse / dof) / sxx)
        if se_slope > 0:
            t = slope / se_slope
            p_value = t_sf_two_sided(t, dof)
    return {
        "slope": round(slope, 6),
        "slope_ci": ([round(slope - 1.96 * se_slope, 6), round(slope + 1.96 * se_slope, 6)]
                     if se_slope is not None else None),
        "intercept": round(intercept, 6),
        "r_squared": round(r2, 4) if r2 is not None else None,
        "p_value": round(p_value, 6) if p_value is not None else None,
        "n": n,
    }


def describe(xs):
    """Summary statistics for one numeric series."""
    if not xs:
        return None
    n = len(xs)
    lo_f, hi_f = iqr_fences(xs)
    outliers = [x for x in xs if lo_f is not None and (x < lo_f or x > hi_f)]
    return {
        "n": n,
        "min": round(min(xs), 3),
        "p05": round(percentile(xs, 5), 3),
        "p25": round(percentile(xs, 25), 3),
        "median": round(_median(xs), 3),
        "p75": round(percentile(xs, 75), 3),
        "p95": round(percentile(xs, 95), 3),
        "max": round(max(xs), 3),
        "mean": round(_mean(xs), 3),
        "stdev": round(_stdev(xs), 3) if n > 1 else None,
        "iqr_fences": [round(lo_f, 3), round(hi_f, 3)] if lo_f is not None else None,
        "outlier_count": len(outliers),
        "outliers": sorted(round(x, 3) for x in outliers)[:20],
        "median_ci": (bootstrap_median_ci(xs) if n >= MIN_SAMPLES_FOR_CONFIDENCE_INTERVAL
                      else None),
        "median_ci_withheld": (None if n >= MIN_SAMPLES_FOR_CONFIDENCE_INTERVAL else
                               f"needs >= {MIN_SAMPLES_FOR_CONFIDENCE_INTERVAL} readings; a "
                               f"bootstrap median over {n} collapses to zero width and would "
                               f"look more certain than the data is"),
    }


def histogram(xs, bins=12):
    """Equal-width bins, returned with edges so the client can draw real axes."""
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi == lo:
        return [{"x0": lo, "x1": hi, "count": len(xs)}]
    width = (hi - lo) / bins
    counts = [0] * bins
    for x in xs:
        idx = min(bins - 1, int((x - lo) / width))
        counts[idx] += 1
    return [{"x0": round(lo + i * width, 3),
             "x1": round(lo + (i + 1) * width, 3),
             "count": counts[i]} for i in range(bins)]


# ---------------------------------------------------------------------------
# Verdict helper
# ---------------------------------------------------------------------------

def verdict(aid, title, need, have, ok, why_ok=None, why_not=None, data=None,
            status_override=None):
    """One analysis result, with the reason attached either way.

    ok           -> "ok"
    ok=False and status_override given -> that literal status
    otherwise    -> "insufficient"

    Callers that mean "there is no data at all" pass status_override="unavailable"
    explicitly. Inferring that from the wording of the reason used to be done by
    substring match, which silently mislabelled any message that happened to
    contain "no ".
    """
    if ok:
        status = "ok"
    else:
        status = status_override or "insufficient"
    return {
        "id": aid,
        "title": title,
        "status": status,
        "why": why_ok if ok else why_not,
        "need": need,
        "have": have,
        "data": data if ok else None,
    }


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _numeric(scans, metric):
    """Finite numeric values for one metric, ignoring anything that is not a number.

    A collector that starts reporting a string or a nested object should degrade
    the analysis, not crash the dashboard. Non-numeric values are still worth
    knowing about, so they are returned separately for the integrity report.
    """
    good, bad = [], []
    for s in scans:
        v = s.get(metric)
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and math.isfinite(v):
            good.append(float(v))
        else:
            bad.append(v)
    return good, bad


def _all_numeric(scans, metric):
    return _numeric(scans, metric)[0]


def _valid(scans):
    """Drop rows with no usable timestamp/location; the rest assume they exist."""
    out = []
    for s in scans:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# 1. Dataset sufficiency -- the panel's headline
# ---------------------------------------------------------------------------

def dataset_sufficiency(scans):
    devices = {s.get("device_id") for s in scans if s.get("device_id")}
    carriers = {s.get("ssid") or "Unknown" for s in scans}
    days = {str(s["timestamp"])[:10] for s in scans if s.get("timestamp")}
    timestamps = sorted(t for t in (str(s["timestamp"]) for s in scans if s.get("timestamp")))
    hours = {t[11:13] for t in timestamps}

    newest_age_days = None
    if timestamps:
        newest = _parse_ts(timestamps[-1])
        if newest:
            newest_age_days = round((datetime.now(timezone.utc) - newest).total_seconds() / 86400.0, 1)

    checks = [
        {"id": "volume", "label": "Sample volume", "need": MIN_TOTAL_SAMPLES, "have": len(scans),
         "ok": len(scans) >= MIN_TOTAL_SAMPLES,
         "detail": "enough scans to describe the distribution"},
        {"id": "devices", "label": "Distinct devices", "need": MIN_DEVICES_PER_CARRIER, "have": len(devices),
         "ok": len(devices) >= MIN_DEVICES_PER_CARRIER,
         "detail": "k-anonymity: fewer than this and a cell could be traced to one person"},
        {"id": "carriers", "label": "Distinct carriers", "need": MIN_CARRIERS_FOR_COMPARISON, "have": len(carriers),
         "ok": len(carriers) >= MIN_CARRIERS_FOR_COMPARISON,
         "detail": "comparison needs something to compare against"},
        {"id": "days", "label": "Distinct days", "need": MIN_DAYS_FOR_TREND, "have": len(days),
         "ok": len(days) >= MIN_DAYS_FOR_TREND,
         "detail": "a trend needs a series; one day is a snapshot, not a trend"},
        {"id": "freshness", "label": "Newest data age (days)", "need": STALE_AFTER_DAYS,
         "have": newest_age_days if newest_age_days is not None else None,
         "ok": newest_age_days is not None and newest_age_days <= STALE_AFTER_DAYS,
         "detail": "coverage claims go stale as the network changes under them"},
    ]
    return {
        "total_scans": len(scans),
        "distinct_devices": len(devices),
        "distinct_carriers": len(carriers),
        "carriers": sorted(carriers),
        "distinct_days": len(days),
        "hours_observed": len(hours),
        "date_range": {"earliest": timestamps[0] if timestamps else None,
                       "latest": timestamps[-1] if timestamps else None},
        "newest_age_days": newest_age_days,
        "checks": checks,
        "ready_for": [c["label"] for c in checks if c["ok"]],
        "blocked_for": [c["label"] for c in checks if not c["ok"]],
    }


# ---------------------------------------------------------------------------
# 2. Distributions
# ---------------------------------------------------------------------------

def metric_distributions(scans, unusable=None):
    """Distribution per metric, but only for metrics that are actually real.

    Sample count alone is not permission to publish a summary. A column of 85
    identical placeholders satisfies any minimum-n rule, yet describing it
    produces a confident-looking median with a zero-width confidence interval.
    Any metric that integrity_checks rejected is reported as unusable here, with
    a pointer to the reason, so the summary can never be more confident than
    the data deserves.
    """
    if unusable is None:
        unusable = _unusable_metrics(scans)
    data = {}
    for metric in _METRICS:
        xs = _all_numeric(scans, metric)
        need = {"min_samples": MIN_SAMPLES_FOR_DISTRIBUTION}
        have = {"n": len(xs)}
        if metric in unusable:
            data[metric] = verdict(
                f"distribution.{metric}", f"{metric} distribution", need, have, False,
                status_override="unusable",
                why_not=f"{unusable[metric]} -- the data-integrity check rejected this field, "
                        f"so no distribution is reported")
            continue
        if len(xs) < MIN_SAMPLES_FOR_DISTRIBUTION:
            data[metric] = verdict(
                f"distribution.{metric}", f"{metric} distribution", need, have, False,
                why_not=f"needs >= {MIN_SAMPLES_FOR_DISTRIBUTION} readings, have {len(xs)}")
            continue
        data[metric] = verdict(
            f"distribution.{metric}", f"{metric} distribution", need, have, True,
            why_ok=f"{len(xs)} readings",
            data={"describe": describe(xs), "histogram": histogram(xs)})
    return data


def _unusable_metrics(scans):
    """Map of metric -> reason, for fields that fail the integrity checks."""
    out = {}
    for f in integrity_checks(scans)["findings"]:
        for metric in _METRICS:
            if f["id"] in (f"constant.{metric}", f"near_constant.{metric}"):
                out[metric] = f["title"]
    # A column with no numeric values at all is not "short of samples", it is
    # the wrong type of thing entirely, and the two deserve different labels.
    for metric in _METRICS:
        if metric in out:
            continue
        good, bad = _numeric(scans, metric)
        if not good and bad:
            out[metric] = f"every reading is non-numeric (e.g. {bad[0]!r})"
    return out


# ---------------------------------------------------------------------------
# 3. Carrier comparison
# ---------------------------------------------------------------------------

def carrier_analysis(scans, ssid_filter=None):
    subset = scans
    if ssid_filter:
        ssf = ssid_filter.strip().lower()
        subset = [s for s in scans if (s.get("ssid") or "").lower() == ssf]

    groups = defaultdict(lambda: {"download_speed_mbps": [], "signal_dbm": [], "devices": set(), "days": set()})
    for s in subset:
        g = groups[s.get("ssid") or "Unknown"]
        did = s.get("device_id")
        if did:
            g["devices"].add(did)
        ts = s.get("timestamp")
        if ts:
            g["days"].add(str(ts)[:10])
        for m in ("download_speed_mbps", "signal_dbm"):
            v = s.get(m)
            if v is not None:
                g[m].append(v)

    rows = []
    for name, g in groups.items():
        rows.append({
            "carrier": name,
            "samples": len(g["download_speed_mbps"]) or len(g["signal_dbm"]),
            "devices": len(g["devices"]),
            "days": len(g["days"]),
            "speed": (describe(g["download_speed_mbps"])
                      if len(g["download_speed_mbps"]) >= MIN_SAMPLES_FOR_DISTRIBUTION else None),
            "speed_n": len(g["download_speed_mbps"]),
            "signal": (describe(g["signal_dbm"])
                       if len(g["signal_dbm"]) >= MIN_SAMPLES_FOR_DISTRIBUTION else None),
            "signal_n": len(g["signal_dbm"]),
        })
    rows.sort(key=lambda r: r["samples"], reverse=True)

    have = {
        "carriers": len(groups),
        "per_carrier": {r["carrier"]: {"samples": r["samples"], "devices": r["devices"], "days": r["days"]}
                        for r in rows},
    }
    need = {"min_carriers": MIN_CARRIERS_FOR_COMPARISON,
            "min_devices_per_carrier": MIN_DEVICES_PER_CARRIER,
            "min_samples_per_carrier": MIN_SAMPLES_PER_CARRIER}

    eligible = [r for r in rows
                if r["devices"] >= MIN_DEVICES_PER_CARRIER
                and r["samples"] >= MIN_SAMPLES_PER_CARRIER]
    if len(eligible) < 2:
        breakdown = ", ".join(
            "{}: {} devices / {} samples".format(r["carrier"], r["devices"], r["samples"])
            for r in rows) or "no carriers at all"
        return verdict("carrier_comparison", "Carrier comparison", need, have, False,
                       why_not=("needs >= 2 carriers each with >= {} devices and >= {} samples; "
                                "have {} -- {}".format(
                                    MIN_DEVICES_PER_CARRIER, MIN_SAMPLES_PER_CARRIER,
                                    len(groups), breakdown)))

    # pairwise Welch tests on the metric that actually has data
    tests = []
    speeds = {r["carrier"]: [s["download_speed_mbps"] for s in subset
                            if (s.get("ssid") or "Unknown") == r["carrier"]
                            and s.get("download_speed_mbps") is not None] for r in eligible}
    names = sorted(speeds)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            t = welch_t_test(speeds[names[i]], speeds[names[j]])
            d = cohens_d(speeds[names[i]], speeds[names[j]])
            if t:
                tests.append({
                    "a": names[i], "b": names[j],
                    "mean_difference_mbps": t["mean_difference"],
                    "t": t["t"], "df": t["df"], "p_value": t["p_value"],
                    "significant_at_0.05": t["p_value"] < 0.05,
                    "cohens_d": d,
                })
    return verdict("carrier_comparison", "Carrier comparison", need, have, True,
                   why_ok=f"{len(eligible)} carriers meet the thresholds",
                   data={"carriers": rows, "pairwise_speed_tests": tests})


# ---------------------------------------------------------------------------
# 4. Time trend
# ---------------------------------------------------------------------------

def time_trend(scans, metric="download_speed_mbps"):
    by_day = defaultdict(list)
    for s in scans:
        ts = s.get("timestamp")
        v = s.get(metric)
        if ts and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            by_day[str(ts)[:10]].append(float(v))
    days = sorted(by_day)
    have = {"distinct_days": len(days), "metric": metric,
            "samples_per_day": {d: len(by_day[d]) for d in days}}
    need = {"min_days": MIN_DAYS_FOR_TREND}
    if len(days) < MIN_DAYS_FOR_TREND:
        return verdict("time_trend", f"{metric} trend over time", need, have, False,
                       why_not=f"needs >= {MIN_DAYS_FOR_TREND} distinct days, have {len(days)}"
                               + (" -- a single day is a snapshot, not a trend" if len(days) == 1 else ""))
    # x must be elapsed days, not the row index: a week-long hole in the data
    # has to widen the gap, otherwise the fit is dragged by a gap it pretends
    # is not there.
    day0 = _parse_ts(days[0])
    xs = []
    for d in days:
        dt = _parse_ts(d)
        xs.append((dt - day0).days if (dt and day0) else len(xs))
    ys = [_median(by_day[d]) for d in days]
    fit = linear_trend(xs, ys)
    return verdict("time_trend", f"{metric} trend over time", need, have, True,
                   why_ok=f"{len(days)} days",
                   data={"series": [{"date": d, "median": round(_median(by_day[d]), 3),
                                     "n": len(by_day[d])} for d in days],
                         "regression": fit,
                         "verdict": _trend_verdict(fit)})


def _trend_verdict(fit):
    """One honest sentence about the regression.

    fit["p_value"] is None when the fit is degenerate (too few points, or
    every daily median identical). A previous version fell through to
    "significant trend" in that case, which reported significance for data that
    could not support any.
    """
    if not fit:
        return "trend not computable"
    if fit.get("p_value") is None or fit.get("r_squared") is None:
        return "no trend: the daily medians are too flat to fit a line"
    if fit["p_value"] >= 0.05:
        return "no significant trend"
    direction = "rising" if fit["slope"] > 0 else "falling"
    return f"significant {direction} trend"


# ---------------------------------------------------------------------------
# 5. Diurnal / weekly seasonality
# ---------------------------------------------------------------------------

def diurnal_profile(scans, metric="download_speed_mbps"):
    by_hour = defaultdict(list)
    for s in scans:
        ts = s.get("timestamp")
        v = s.get(metric)
        if ts and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            by_hour[int(str(ts)[11:13])].append(float(v))
    # Count only hours with enough samples to have a median worth plotting.
    # Six hours where five of them hold a single scan is not a diurnal profile.
    usable = {h: v for h, v in by_hour.items() if len(v) >= MIN_SAMPLES_PER_BUCKET}
    thin = {h: len(v) for h, v in by_hour.items() if len(v) < MIN_SAMPLES_PER_BUCKET}
    have = {"hours_with_data": len(by_hour), "hours_usable": len(usable),
            "hours_too_thin": thin, "metric": metric}
    need = {"min_hours": MIN_HOURS_FOR_DIURNAL, "min_samples_per_hour": MIN_SAMPLES_PER_BUCKET}
    if len(usable) < MIN_HOURS_FOR_DIURNAL:
        detail = ""
        if thin:
            detail = (" -- {} hour(s) had data but under {} samples each: {}".format(
                len(thin), MIN_SAMPLES_PER_BUCKET,
                ", ".join("{:02d}:00 n={}".format(h, n) for h, n in sorted(thin.items()))))
        return verdict("diurnal", f"{metric} by hour of day", need, have, False,
                       why_not=(f"needs >= {MIN_HOURS_FOR_DIURNAL} hours with >= "
                                f"{MIN_SAMPLES_PER_BUCKET} samples each, have {len(usable)}" + detail))
    rows = [{"hour": h, "median": round(_median(usable[h]), 3), "n": len(usable[h])}
            for h in sorted(usable)]
    best = max(rows, key=lambda r: r["median"])
    worst = min(rows, key=lambda r: r["median"])
    return verdict("diurnal", f"{metric} by hour of day", need, have, True,
                   why_ok=f"{len(usable)} hours with >= {MIN_SAMPLES_PER_BUCKET} samples"
                          + (f" ({len(thin)} thin hour(s) excluded)" if thin else ""),
                   data={"hours": rows, "best_hour": best, "worst_hour": worst,
                         "spread": round(best["median"] - worst["median"], 3)})


def weekly_profile(scans, metric="download_speed_mbps"):
    by_dow = defaultdict(list)
    for s in scans:
        ts = s.get("timestamp")
        v = s.get(metric)
        if not ts or not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            continue
        d = _parse_ts(ts)
        if d:
            by_dow[d.strftime("%A")].append(float(v))
    days_seen = {str(s["timestamp"])[:10] for s in scans if s.get("timestamp")}
    have = {"days": len(days_seen), "weekdays_observed": len(by_dow), "metric": metric}
    need = {"min_days": MIN_DAYS_FOR_WEEKLY}
    if len(days_seen) < MIN_DAYS_FOR_WEEKLY:
        return verdict("weekly", f"{metric} by day of week", need, have, False,
                       why_not=f"needs >= {MIN_DAYS_FOR_WEEKLY} days to see a weekly pattern, have {len(days_seen)}")
    # A single day's worth of samples cannot fill every weekday bucket, so each
    # bar is a median over whatever landed on that weekday. Buckets with too few
    # samples are withheld rather than plotted, for the same reason as diurnal.
    thin = {d: len(v) for d, v in by_dow.items() if len(v) < MIN_SAMPLES_PER_BUCKET}
    usable = {d: v for d, v in by_dow.items() if len(v) >= MIN_SAMPLES_PER_BUCKET}
    if len(usable) < 2:
        return verdict("weekly", f"{metric} by day of week", need, have, False,
                       why_not=(f"{len(days_seen)} days seen, but no weekday reached "
                                f"{MIN_SAMPLES_PER_BUCKET} samples "
                                f"({len(thin)} weekday(s) seen)"))
    rows = [{"day": d, "median": round(_median(usable[d]), 3), "n": len(usable[d])}
            for d in sorted(usable)]
    best = max(rows, key=lambda r: r["median"])
    worst = min(rows, key=lambda r: r["median"])
    return verdict("weekly", f"{metric} by day of week", need, have, True,
                   why_ok=f"{len(usable)} weekdays with >= {MIN_SAMPLES_PER_BUCKET} samples"
                          + (f" ({len(thin)} thin weekday(s) excluded)" if thin else ""),
                   data={"days": rows, "best_day": best, "worst_day": worst,
                         "spread": round(best["median"] - worst["median"], 3)})


# ---------------------------------------------------------------------------
# 6. Cohorts
# ---------------------------------------------------------------------------

def cohort_breakdown(scans):
    by_device = defaultdict(lambda: {"n": 0, "speed": [], "days": set()})
    by_carrier = defaultdict(lambda: {"n": 0, "speed": [], "devices": set()})
    by_place = defaultdict(lambda: {"n": 0, "speed": []})

    for s in scans:
        d = s.get("device_id")
        if d:
            by_device[d]["n"] += 1
            by_device[d]["days"].add(str(s.get("timestamp"))[:10])
            if s.get("download_speed_mbps") is not None:
                by_device[d]["speed"].append(s["download_speed_mbps"])
        c = s.get("ssid") or "Unknown"
        by_carrier[c]["n"] += 1
        by_carrier[c]["devices"].add(d)
        if s.get("download_speed_mbps") is not None:
            by_carrier[c]["speed"].append(s["download_speed_mbps"])
        # coarse place cohort: 2 decimal places ~ 1km, enough to separate
        # "at home" / "at work" / "commuting" without pinpointing anyone
        if s.get("lat") is not None and s.get("lon") is not None:
            p = f"{round(s['lat'], 2)},{round(s['lon'], 2)}"
            by_place[p]["n"] += 1
            if s.get("download_speed_mbps") is not None:
                by_place[p]["speed"].append(s["download_speed_mbps"])

    devices = [{"device": ("device-%d" % (i + 1)), "samples": g["n"], "days": len(g["days"]),
                "median_speed": round(_median(g["speed"]), 2) if g["speed"] else None}
               for i, (d, g) in enumerate(sorted(by_device.items(), key=lambda kv: -kv[1]["n"]))]
    carriers = [{"carrier": k, "samples": g["n"], "devices": len(g["devices"] - {None}),
                 "median_speed": round(_median(g["speed"]), 2) if g["speed"] else None}
                for k, g in sorted(by_carrier.items(), key=lambda kv: -kv[1]["n"])]
    places = [{"area": k, "samples": g["n"],
               "median_speed": round(_median(g["speed"]), 2) if g["speed"] else None}
              for k, g in sorted(by_place.items(), key=lambda kv: -kv[1]["n"])]

    return {
        "by_device": devices,
        "by_carrier": carriers,
        "by_area": places[:20],
        "note": ("devices are labelled device-1..N and never expose a device_id; "
                 "areas are rounded to ~1km"),
    }


# ---------------------------------------------------------------------------
# 7. Anomaly + integrity checks
# ---------------------------------------------------------------------------

def integrity_checks(scans):
    findings = []
    total = len(scans)

    # An empty dataset produces no findings, which would otherwise report
    # clean=True. "Nothing is wrong" and "there is nothing" are different
    # states and the panel must not conflate them.
    if total == 0:
        findings.append({
            "severity": "critical",
            "id": "empty.dataset",
            "title": "no scan data at all",
            "detail": "the dataset is empty, so nothing here can be analysed or verified",
            "affected": 0,
            "action": "confirm the collector is submitting scans and the database is reachable",
        })
        return {"clean": False, "findings": findings, "total": 0}

    # -- constant and near-constant columns. The highest-value check here.
    #    A fabricated or defaulted measurement is invisible in a count chart
    #    but obvious as zero variance. A field that is mostly one value is the
    #    softer version of the same disease and worth flagging separately,
    #    because it means the apparent spread is coming from a few stragglers.
    for metric in _METRICS:
        xs, bad = _numeric(scans, metric)
        if bad:
            findings.append({
                "severity": "warning",
                "id": f"type.{metric}",
                "title": f"{metric} has {len(bad)} non-numeric value(s)",
                "detail": (f"e.g. {bad[0]!r} -- expected a number. Those readings are excluded "
                           f"from every statistic for this field."),
                "affected": len(bad),
                "action": "check the collector is writing a plain number into this column",
            })
        if len(xs) < 10:
            continue
        spread = max(xs) - min(xs)
        if spread <= CONSTANT_VALUE_EPSILON:
            findings.append({
                "severity": "critical",
                "id": f"constant.{metric}",
                "title": f"{metric} never varies",
                "detail": (f"all {len(xs)} readings are exactly {xs[0]!r}. This is not a measurement "
                           f"of the network; it is a placeholder or a defaulted field. Any analysis "
                           f"using it will produce confident nonsense."),
                "affected": len(xs),
                "action": "stop treating this field as data until the collector reports a real value",
            })
            continue
        counts = defaultdict(int)
        for x in xs:
            counts[x] += 1
        modal, modal_n = max(counts.items(), key=lambda kv: kv[1])
        share = modal_n / len(xs)
        if share >= NEAR_CONSTANT_SHARE:
            distinct = len(counts)
            findings.append({
                "severity": "warning",
                "id": f"near_constant.{metric}",
                "title": f"{int(share * 100)}% of {metric} readings are exactly {modal!r}",
                "detail": (f"only {distinct} distinct value(s) across {len(xs)} readings, "
                           f"with the spread driven entirely by {len(xs) - modal_n} outlier(s)"),
                "affected": len(xs),
                "action": ("check whether this is a real measurement or a default; a summary built "
                           "on it mostly describes the default, not reality"),
            })

    # -- completeness
    for metric in _METRICS:
        have = sum(1 for s in scans if s.get(metric) is not None)
        if total and have < total:
            findings.append({
                "severity": "warning",
                "id": f"incomplete.{metric}",
                "title": f"{metric} missing from {total - have} of {total} scans",
                "detail": f"completeness {100.0 * have / total:.1f}%",
                "affected": total - have,
                "action": "distributions below are computed on the subset that has a value",
            })

    # -- temporal
    stamps = sorted(t for t in (str(s["timestamp"]) for s in scans if s.get("timestamp")))
    if stamps:
        newest = _parse_ts(stamps[-1])
        if newest:
            age = (datetime.now(timezone.utc) - newest).days
            if age > STALE_AFTER_DAYS:
                findings.append({
                    "severity": "warning",
                    "id": "stale.dataset",
                    "title": f"newest scan is {age} days old",
                    "detail": f"last observation {stamps[-1][:19]}; coverage changes continuously",
                    "affected": total,
                    "action": "collect fresh scans before trusting any coverage claim",
                })
    if len({t[:10] for t in stamps}) <= 1 and total > 20:
        findings.append({
            "severity": "warning",
            "id": "single.day",
            "title": "all scans fall on a single day",
            "detail": f"{total} scans on {stamps[0][:10]}",
            "affected": total,
            "action": "no time trend, weekly pattern, or before/after comparison is possible",
        })
    if len({s.get("device_id") for s in scans if s.get("device_id")}) <= 1 and total > 20:
        findings.append({
            "severity": "warning",
            "id": "single.device",
            "title": "all scans come from one device",
            "detail": "one device cannot represent a population",
            "affected": total,
            "action": "k-anonymity cannot be satisfied; no cell can be published",
        })

    # -- spatial clustering: a device sitting still produces a tight blob that
    #    looks like broad coverage if you only count distinct points.
    pts = [(s["lat"], s["lon"]) for s in scans if s.get("lat") is not None and s.get("lon") is not None]
    if len(pts) >= 20:
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        span_m = ((max(lats) - min(lats)) * 111320.0, (max(lons) - min(lons)) * 111320.0)
        if max(span_m) < 2000:
            findings.append({
                "severity": "info",
                "id": "spatial.tight",
                "title": "all scans fall within a 2km box",
                "detail": f"extent ~{max(span_m):.0f}m -- effectively a single measurement site",
                "affected": len(pts),
                "action": "coverage 'area' here is one place, not a network",
            })

    # -- per-scan outliers on speed
    speeds = [s["download_speed_mbps"] for s in scans if s.get("download_speed_mbps") is not None]
    if len(speeds) >= MIN_SAMPLES_FOR_DISTRIBUTION:
        lo, hi = iqr_fences(speeds)
        bad = [s for s in scans
               if s.get("download_speed_mbps") is not None
               and (s["download_speed_mbps"] < lo or s["download_speed_mbps"] > hi)]
        if bad:
            findings.append({
                "severity": "info",
                "id": "outliers.speed",
                "title": f"{len(bad)} speed readings outside the IQR fence",
                "detail": f"outside [{lo:.2f}, {hi:.2f}] Mbps",
                "affected": len(bad),
                "action": "usually genuine (a good spot, or a congested one); kept in the stats, not dropped",
            })

    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return {
        "total_scans": total,
        "findings": findings,
        "clean": not any(f["severity"] == "critical" for f in findings),
    }


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def layer_usability(scans, unusable=None):
    """Per-metric verdict on whether the data can back a published layer at all.

    The map renders a signal layer from whatever the collector reports for
    signal_dbm. If that field is constant or missing, the map is not showing
    a weak signal -- it is showing the same colour everywhere by construction.
    This makes that failure explicit instead of letting a meaningless layer
    look like a real measurement.
    """
    if unusable is None:
        unusable = _unusable_metrics(scans)
    layers = {}
    for metric, label, unit in (("download_speed_mbps", "Speed", "Mbps"),
                                ("signal_dbm", "Signal", "dBm")):
        xs = _all_numeric(scans, metric)
        if not xs:
            status, why = "unavailable", f"no scan reports {metric}"
        elif metric in unusable:
            status, why = "placeholder", unusable[metric]
        else:
            status, why = "usable", f"{len(xs)} genuinely varying readings"
        layers[metric] = {
            "metric": metric, "label": label, "unit": unit,
            "status": status, "why": why, "samples": len(xs),
            "distinct_values": len(set(xs)),
        }
    return {
        "any_usable": any(v["status"] == "usable" for v in layers.values()),
        "layers": layers,
        "summary": "; ".join(f"{v['label']}: {v['status']}" for v in layers.values()),
    }


def build_insights(scans, ssid_filter=None):
    # Tolerate None: this bundle is assembled for a dashboard, and a rendering
    # error must never be the reason the page goes blank.
    scans = _valid(scans or [])
    # Integrity runs first: its verdicts gate the distributions and the layer
    # report, so a placeholder column cannot be summarised as a real one.
    integ = integrity_checks(scans)
    unusable = {}
    for f in integ["findings"]:
        for metric in _METRICS:
            if f["id"] in (f"constant.{metric}", f"near_constant.{metric}"):
                unusable[metric] = f["title"]
    return {
        "sufficiency": dataset_sufficiency(scans),
        "distributions": metric_distributions(scans, unusable=unusable),
        "carriers": carrier_analysis(scans, ssid_filter),
        "trend": time_trend(scans),
        "diurnal": diurnal_profile(scans),
        "weekly": weekly_profile(scans),
        "cohorts": cohort_breakdown(scans),
        "integrity": integ,
        "layers": layer_usability(scans, unusable=unusable),
    }
