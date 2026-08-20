"""
Aggregated, privacy-safe coverage statistics.

Raw scan rows (backend/db.py) are per-device, per-location, per-timestamp --
useful for building the coverage mesh, but not something to sell or export
wholesale. A device_id repeating at the same coordinates over time can
reveal where a specific person lives or moves, even without a name
attached to it.

This module turns raw scans into area-level rollups instead:
  - space is bucketed into fixed-size grid cells (aggregate_coverage_cells)
  - a cell is only ever returned if data from at least MIN_DEVICES_PER_CELL
    distinct devices contributed to it -- k-anonymity by suppression, so no
    single person's location is ever identifiable from the output
  - raw device_id never appears in any function's return value here, only
    a count of how many distinct ones contributed

This -- not raw exports -- is the shape of data actually worth building a
product around: it's what coverage-data companies (Opensignal, Ookla, etc.)
sell, for exactly this reason.
"""

import math
import time
from datetime import datetime, timezone
from collections import defaultdict

MIN_DEVICES_PER_CELL = 3      # suppress any cell fewer than this many distinct
                               # devices contributed to
DEFAULT_CELL_SIZE_M = 150.0   # roughly a short city block
FRESHNESS_HALF_LIFE_DAYS = 30  # cells built entirely from 30-day-old data have confidence 0.5


def _is_outlier(s):
    """
    Reject physically impossible or clearly spoofed readings.

    - signal_dbm outside the range any real radio produces
    - download speed beyond what any consumer connection delivers
    - GPS accuracy so bad the fix is useless
    """
    sig = s.get("signal_dbm")
    if sig is not None and (sig > -5 or sig < -120):
        return True
    speed = s.get("download_speed_mbps")
    if speed is not None and (speed < 0 or speed > 500):
        return True
    acc = s.get("accuracy")
    if acc is not None:
        try:
            if float(acc) > 500:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _cell_confidence(timestamps):
    """
    Confidence that a cell's data is still fresh. 1.0 = all scans from
    today, decays exponentially with a 30-day half-life. A cell built
    entirely from 3-month-old scans scores ~0.06 -- not wrong, just stale.
    """
    if not timestamps:
        return 0.0
    now = time.time()
    half_life = FRESHNESS_HALF_LIFE_DAYS * 86400
    total_w = 0.0
    for ts in timestamps:
        try:
            age = max(0.0, now - datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
        except (ValueError, TypeError):
            age = 0.0
        total_w += 0.5 ** (age / half_life)
    return round(min(1.0, total_w / len(timestamps)), 3)


def _cell_id(lat, lon, cell_size_m):
    """Snap a coordinate to a grid cell index. Same cell in, same id out."""
    lat_deg_per_m = 1.0 / 111320.0
    lon_deg_per_m = 1.0 / (111320.0 * max(0.0001, math.cos(math.radians(lat))))
    cell_lat = cell_size_m * lat_deg_per_m
    cell_lon = cell_size_m * lon_deg_per_m
    return (round(lat / cell_lat), round(lon / cell_lon)), cell_lat, cell_lon


def aggregate_coverage_cells(scans, ssid_filter=None, cell_size_m=DEFAULT_CELL_SIZE_M,
                              min_devices=MIN_DEVICES_PER_CELL):
    """
    Bucket scans into grid cells and return one aggregate record per cell
    that meets the minimum-distinct-device threshold. This is the core
    "product" output -- an area got measured, here's what it looks like,
    with no way to trace any single reading back to who took it.
    """
    filtered = scans
    if ssid_filter:
        ssf = ssid_filter.strip().lower()
        filtered = [s for s in scans if s.get("ssid", "").lower() == ssf]

    cells = defaultdict(lambda: {
        "signals": [], "speeds": [], "devices": set(), "carriers": defaultdict(int),
        "timestamps": [], "lat_sum": 0.0, "lon_sum": 0.0, "n": 0,
    })

    for s in filtered:
        if _is_outlier(s):
            continue
        lat, lon, sig = s.get("lat"), s.get("lon"), s.get("signal_dbm")
        if lat is None or lon is None or sig is None:
            continue
        key, _, _ = _cell_id(lat, lon, cell_size_m)
        c = cells[key]
        c["signals"].append(sig)
        c["lat_sum"] += lat
        c["lon_sum"] += lon
        c["n"] += 1
        sp = s.get("download_speed_mbps")
        if sp is not None:
            c["speeds"].append(sp)
        did = s.get("device_id")
        if did:
            c["devices"].add(did)
        ssid = s.get("ssid") or "Unknown"
        c["carriers"][ssid] += 1
        ts = s.get("timestamp")
        if ts:
            c["timestamps"].append(str(ts))

    out = []
    for key, c in cells.items():
        if len(c["devices"]) < min_devices:
            continue  # suppressed: not enough distinct contributors to be safe to publish
        signals = c["signals"]
        out.append({
            "lat": round(c["lat_sum"] / c["n"], 5),
            "lon": round(c["lon_sum"] / c["n"], 5),
            "signal_dbm_avg": round(sum(signals) / len(signals), 1),
            "signal_dbm_min": round(min(signals), 1),
            "signal_dbm_max": round(max(signals), 1),
            "speed_mbps_avg": round(sum(c["speeds"]) / len(c["speeds"]), 2) if c["speeds"] else None,
            "sample_count": c["n"],
            "device_count": len(c["devices"]),
            "dominant_network": max(c["carriers"], key=c["carriers"].get),
            "networks_seen": dict(c["carriers"]),
            "first_seen": min(c["timestamps"]) if c["timestamps"] else None,
            "last_seen": max(c["timestamps"]) if c["timestamps"] else None,
            "confidence": _cell_confidence(c["timestamps"]),
        })
    return out


def carrier_comparison(scans, min_devices=MIN_DEVICES_PER_CELL):
    """
    Per-network (ssid/carrier) quality comparison: average signal, average
    speed, sample size, distinct-device count. Suppresses any network with
    fewer than min_devices distinct contributors, same reasoning as cells.
    """
    by_network = defaultdict(lambda: {"signals": [], "speeds": [], "devices": set()})
    for s in scans:
        ssid = s.get("ssid") or "Unknown"
        sig = s.get("signal_dbm")
        if sig is None:
            continue
        n = by_network[ssid]
        n["signals"].append(sig)
        sp = s.get("download_speed_mbps")
        if sp is not None:
            n["speeds"].append(sp)
        did = s.get("device_id")
        if did:
            n["devices"].add(did)

    out = []
    for ssid, n in by_network.items():
        if len(n["devices"]) < min_devices:
            continue
        out.append({
            "network": ssid,
            "avg_signal_dbm": round(sum(n["signals"]) / len(n["signals"]), 1),
            "avg_speed_mbps": round(sum(n["speeds"]) / len(n["speeds"]), 2) if n["speeds"] else None,
            "sample_count": len(n["signals"]),
            "device_count": len(n["devices"]),
        })
    out.sort(key=lambda r: r["sample_count"], reverse=True)
    return out


def daily_quality_trend(scans, min_devices_per_day=MIN_DEVICES_PER_CELL):
    """
    Per-day average signal/speed (not just scan counts) -- shows whether
    coverage is trending better or worse over time, which is the kind of
    thing worth paying for; a raw daily scan-count chart isn't.
    """
    by_day = defaultdict(lambda: {"signals": [], "speeds": [], "devices": set()})
    for s in scans:
        ts = s.get("timestamp")
        sig = s.get("signal_dbm")
        if not ts or sig is None:
            continue
        day = str(ts)[:10]
        d = by_day[day]
        d["signals"].append(sig)
        sp = s.get("download_speed_mbps")
        if sp is not None:
            d["speeds"].append(sp)
        did = s.get("device_id")
        if did:
            d["devices"].add(did)

    out = []
    for day, d in sorted(by_day.items()):
        if len(d["devices"]) < min_devices_per_day:
            continue
        out.append({
            "date": day,
            "avg_signal_dbm": round(sum(d["signals"]) / len(d["signals"]), 1),
            "avg_speed_mbps": round(sum(d["speeds"]) / len(d["speeds"]), 2) if d["speeds"] else None,
            "sample_count": len(d["signals"]),
            "device_count": len(d["devices"]),
        })
    return out


def weak_zones(cells, threshold_dbm=-75.0, min_samples=5):
    """
    Cells worth flagging as poor coverage -- the specific, actionable list
    a carrier or ISP would pay for (where to prioritize a new tower/repeater).
    Takes the output of aggregate_coverage_cells(), not raw scans.
    """
    weak = [c for c in cells if c["signal_dbm_avg"] <= threshold_dbm and c["sample_count"] >= min_samples]
    weak.sort(key=lambda c: c["signal_dbm_avg"])
    return weak


def data_quality_summary(scans, cells):
    """
    High-level "is this dataset big enough to be worth anything" snapshot --
    total distinct devices (never exposed individually), date range, and
    how much of the raw data survived aggregation vs. got suppressed for
    having too few contributors.
    """
    all_devices = {s.get("device_id") for s in scans if s.get("device_id")}
    timestamps = [str(s["timestamp"]) for s in scans if s.get("timestamp")]
    return {
        "total_scans": len(scans),
        "total_distinct_devices": len(all_devices),
        "published_cells": len(cells),
        "date_range": {
            "earliest": min(timestamps) if timestamps else None,
            "latest": max(timestamps) if timestamps else None,
        },
    }
