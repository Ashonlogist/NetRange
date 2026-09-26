import os
import math
import csv
import io
import time
import secrets
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, abort, Response, session
from flask_cors import CORS
from scanner import scan, get_current_connection, signal_to_dbm
from db import save_scan, load_scans, get_client
from algorithm import delaunay_interpolate, generate_contours, mesh_geojson
from analytics import (aggregate_coverage_cells, carrier_comparison,
                        daily_quality_trend, weak_zones, data_quality_summary)
import analytics
import insights
from geocoding import reverse_geocode_cells
from api_keys import require_api_key
from device_auth import issue_token_for_device, registration_throttled, require_device_token

app = Flask(__name__)
CORS(app)

def _required_env(name):
    """Read an environment variable that has no safe default.

    These back the dashboard's HTTP Basic login and the destructive
    /api/cleanup endpoint, so a missing value has to stop the process at
    import time rather than fall back to something guessable and public.
    """
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set, and it has no default. "
            f"Set it before starting the service "
            f"(Render: Environment > add {name}, then redeploy; "
            f"locally: export {name}=...) and try again."
        )
    return value


# DASHBOARD_USERNAME + DASHBOARD_PASSWORD guard every /dashboard* route and
# /api/cleanup (which deletes scan rows), via _check_dashboard_auth().
# DASHBOARD_SECRET signs the dashboard's session cookie. None are optional,
# and none may be hardcoded: a value baked into source is public the moment
# the repo is, which would leave a data-deletion endpoint behind a known
# password. The username is required too -- checking only the password meant
# any username at all was accepted, which is half of Basic auth doing nothing.
DASHBOARD_SECRET = _required_env("DASHBOARD_SECRET")
DASHBOARD_USERNAME = _required_env("DASHBOARD_USERNAME")
DASHBOARD_PASS = _required_env("DASHBOARD_PASSWORD")
app.secret_key = DASHBOARD_SECRET

# SCAN_AUTH_ENFORCED_AT (the grace window that keeps pre-token app builds
# working) is deliberately *not* read here -- device_auth.scan_auth_enforced()
# reads it per request so the deadline passes without a redeploy. Do not cache
# it at import time; two sources of truth for a security deadline is a trap.

APP_VERSION = "1.4.3"
# The APK is published as a GitHub release asset, not served from this
# service: the build output is gitignored, so a git-deployed instance can
# never have it on disk. Override with APK_URL if hosting ever changes.
APK_URL = os.environ.get("APK_URL") or (
    f"https://github.com/Ashonlogist/NetRange/releases/download/v{APP_VERSION}/netrange.apk"
)


@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/map")
def map_view():
    return render_template("map.html")


@app.route("/download/<filename>")
def download_file(filename):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    candidate = os.path.join(static_dir, filename)
    if os.path.isfile(candidate):
        return send_from_directory(static_dir, filename, as_attachment=True)
    # The APK is not committed (see .gitignore), so a git-deployed instance
    # never has it on disk. Serve the published release instead of 404ing,
    # which keeps APK_URL as the single override point for hosting.
    if filename == "netrange.apk":
        return redirect(APK_URL, code=302)
    abort(404)


# Release notes, keyed by version. These used to be one flat list beside
# APP_VERSION, which meant bumping the version shipped the *previous* release's
# notes -- the in-app prompt cheerfully described 1.4.2's changes under a 1.4.3
# heading. Keying them to the version makes that impossible: a new version with
# no entry gets an honest fallback instead of a lie.
RELEASE_NOTES = {
    "1.4.3": [
        "Carrier: override can be cleared again",
        "Background scanning: shows why it is not running",
        "WebView: real error state with Retry, instead of failing silently",
        "Settings: removed interpolation options that never took effect",
    ],
    "1.4.2": [
        "Scan: tap networks directly as target (no text input)",
        "Cellular network detection improved",
        "Coverage map: browser warnings hidden in-app",
        "Settings: API URL hidden",
    ],
}


@app.route("/api/version")
def api_version():
    return jsonify({
        "version": APP_VERSION,
        "apkUrl": APK_URL,
        "notes": RELEASE_NOTES.get(APP_VERSION, []),
    })


_GEOCODE_URL = "https://photon.komoot.io/api/"
_GEOCODE_TTL = 900  # seconds; also keeps a single user off the provider's back
_geocode_cache = {}

# osm_key/osm_value -> a coarse kind, used by the client to pick a result icon
_GEOCODE_KINDS = {
    ("place", "city"): "city",
    ("place", "town"): "town",
    ("place", "village"): "village",
    ("place", "suburb"): "suburb",
    ("place", "neighbourhood"): "suburb",
    ("place", "quarter"): "suburb",
    ("place", "hamlet"): "village",
    ("place", "county"): "region",
    ("place", "state"): "region",
    ("place", "island"): "region",
    ("highway", "primary"): "street",
    ("highway", "secondary"): "street",
    ("highway", "tertiary"): "street",
    ("highway", "residential"): "street",
    ("highway", "unclassified"): "street",
    ("highway", "service"): "street",
    ("highway", "footway"): "path",
    ("highway", "path"): "path",
    ("highway", "track"): "path",
    ("natural", "peak"): "peak",
    ("natural", "volcano"): "peak",
    ("natural", "water"): "water",
    ("waterway", "river"): "water",
    ("landuse", "reservoir"): "water",
}


def _geocode_label(props, kind):
    """
    Compose a readable one-line label. Street and postcode are only useful
    for address-like results, so they are dropped for named places, which
    otherwise read as "University of Ghana, Hospital Road, Accra".
    """
    parts = []
    name = (props.get("name") or "").strip()
    street = (props.get("street") or "").strip()
    house = (props.get("housenumber") or "").strip()
    city = (props.get("city") or props.get("district") or "").strip()
    state = (props.get("state") or "").strip()
    country = (props.get("country") or "").strip()
    postcode = (props.get("postcode") or "").strip()

    # A named POI reads better without its street ("University of Ghana,
    # Hospital Road, Accra"); a bare address or a street has no name to
    # lead with, so there the street is the whole point.
    address_like = kind in ("street", "path") or not name

    if name:
        parts.append(name)
    if address_like and street:
        street_label = f"{house} {street}".strip()
        if street_label.lower() not in {p.lower() for p in parts}:
            parts.append(street_label)
    if city and city.lower() not in {p.lower() for p in parts}:
        parts.append(city)
    for extra in (state, country):
        if extra and extra.lower() not in {p.lower() for p in parts}:
            parts.append(extra)

    label = ", ".join(p for p in parts if p)
    if address_like and postcode and postcode.lower() not in label.lower():
        label = f"{label} {postcode}".strip()
    return label or name or country or "Unnamed place"


@app.route("/api/geocode")
def api_geocode():
    """
    Forward geocoding for the map search box. Proxies Photon (keyless OSM)
    because it sends no CORS headers, so the WebView cannot call it
    directly. Results are cached briefly to respect the provider.
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"results": []})

    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    cache_key = (q.lower(), round(lat, 2) if lat is not None else None,
                 round(lon, 2) if lon is not None else None)
    cached = _geocode_cache.get(cache_key)
    if cached and time.time() - cached[0] < _GEOCODE_TTL:
        return jsonify({"results": cached[1], "cached": True})

    params = {"q": q, "limit": 8, "lang": "en"}
    # Bias toward the user so "legon" ranks the nearby one first
    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon

    try:
        resp = requests.get(_GEOCODE_URL, params=params, timeout=6, headers={
            "User-Agent": "NetRange/1.4 (https://github.com/Ashonlogist/NetRange)",
            "Accept": "application/json",
        })
        resp.raise_for_status()
        features = resp.json().get("features") or []
    except Exception:
        app.logger.exception("Geocoding request failed for %r", q)
        return jsonify({"results": [], "error": "geocoder unavailable"}), 502

    results = []
    seen = set()
    for f in features:
        props = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        kind = _GEOCODE_KINDS.get(
            (props.get("osm_key"), props.get("osm_value")), "place"
        )
        label = _geocode_label(props, kind)
        # Photon returns the same place several times at different
        # address/place levels; one pin per coordinate is what we want.
        key = (round(coords[0], 4), round(coords[1], 4))
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "label": label,
            "lat": coords[1],
            "lon": coords[0],
            "kind": kind,
            "type": props.get("type") or "",
            "country": props.get("country") or "",
        })

    if len(_geocode_cache) > 500:
        _geocode_cache.clear()
    _geocode_cache[cache_key] = (time.time(), results)
    return jsonify({"results": results, "cached": False})


@app.route("/api/health")
def api_health():
    try:
        client = get_client()
        client.table("scans").select("id").limit(1).execute()
        return jsonify({"status": "ok", "database": "ok"})
    except Exception:
        app.logger.exception("Database health check failed")
        return jsonify({"status": "degraded", "database": "error"}), 503


@app.route("/api/scan", methods=["GET"])
def api_scan():
    networks = scan()
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    ssid_filter = request.args.get("ssid", "").strip().lower()
    for n in networks:
        n["lat"] = lat
        n["lon"] = lon
    if ssid_filter:
        networks = [n for n in networks if n.get("ssid", "").lower() == ssid_filter]
    count = save_scan(networks)
    total = len(load_scans())
    return jsonify({
        "count": count,
        "totalScans": total,
        "networks": networks,
        "message": None if networks else "Server-side scanning requires the laptop's WiFi (nmcli). Use the phone app to scan.",
    })


@app.route("/api/register-device", methods=["POST"])
def api_register_device():
    """Mint a per-install token so this device can write scans.

    Open by necessity -- an install cannot hold a shared secret -- but
    throttled per IP, and the token is only ever returned to the caller that
    asked for it (the server keeps just its SHA-256).
    """
    if registration_throttled():
        return jsonify({
            "error": "Too many registration attempts. Try again later.",
        }), 429

    data = request.get_json(silent=True) or {}
    device_id = (data.get("deviceId") or "").strip()
    if not device_id:
        return jsonify({"error": "deviceId is required."}), 400
    if len(device_id) > 128:
        return jsonify({"error": "deviceId is too long."}), 400

    try:
        token = issue_token_for_device(device_id)
    except Exception:
        return jsonify({"error": "Could not register device."}), 503

    return jsonify({"registered": True, "token": token})


@app.route("/api/scan", methods=["POST"])
@require_device_token
def api_scan_post():
    data = request.get_json(silent=True) or {}
    wifi = data.get("wifi") or []
    cellular = data.get("cellular")
    loc = data.get("location") or {}
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is None or lon is None:
        lat = request.args.get("lat", type=float)
        lon = request.args.get("lon", type=float)
    target = (data.get("targetSsid") or "").strip()
    device_id = data.get("deviceId") or ""
    timestamp = data.get("timestamp")
    download_speed = data.get("download_speed_mbps")

    # One shared conversion for every signal reading in this request -- see
    # scanner.signal_to_dbm for why the branches are split on sign.
    to_dbm = signal_to_dbm

    records = []
    seen_bssids = set()
    for n in wifi:
        ssid = (n.get("ssid") or "").strip()
        if target and ssid.lower() != target.lower():
            continue
        if not ssid:
            continue
        bssid = n.get("bssid", "")
        if bssid and bssid in seen_bssids:
            continue
        seen_bssids.add(bssid)
        signal_dbm = to_dbm(n.get("strength"))
        signal_pct = None if signal_dbm is None else max(0, min(100, round((signal_dbm + 100) * 2)))
        records.append({
            "ssid": ssid,
            "bssid": bssid,
            "signal_dbm": signal_dbm,
            "signal_pct": signal_pct,
            "strength_raw": n.get("strength"),
            "channel": n.get("channel"),
            "frequency": n.get("frequency"),
            "lat": lat,
            "lon": lon,
            "accuracy": loc.get("accuracy"),
            "device_id": device_id,
            "source": "mobile",
            "timestamp": timestamp,
            "download_speed_mbps": download_speed,
        })

    if cellular and isinstance(cellular, dict):
        signal_strength = cellular.get("signalStrength")
        # Left as NULL when the platform gave us no reading. Substituting a
        # plausible -70 here is what made every cellular scan look identical
        # and produced a flat, meaningless coverage map; a NULL is skipped by
        # the interpolator, which is the honest outcome.
        cell_dbm = to_dbm(signal_strength)
        records.append({
            "ssid": (cellular.get("carrier") or "Cellular").strip(),
            "bssid": "",
            "signal_dbm": cell_dbm,
            "signal_pct": None,
            "strength_raw": signal_strength,
            "channel": None,
            "frequency": None,
            "lat": lat,
            "lon": lon,
            "accuracy": loc.get("accuracy"),
            "device_id": device_id,
            "source": "cellular",
            "timestamp": timestamp,
            "download_speed_mbps": download_speed,
        })

    count = save_scan(records) if records else 0
    total = len(load_scans())
    no_signal = sum(1 for r in records if r["signal_dbm"] is None)
    msg = None
    if count == 0 and target:
        msg = f"No scan data for SSID '{target}'. Make sure the phone sees that network."
    elif no_signal:
        msg = (f"{no_signal} of {len(records)} records had no signal reading and were "
               f"stored without one. The device did not report a signal strength.")
    return jsonify({"count": count, "totalScans": total, "noSignal": no_signal, "message": msg})


@app.route("/api/scans", methods=["GET"])
def api_scans():
    scans = load_scans()
    return jsonify({"total": len(scans), "scans": scans})


@app.route("/api/heatmap", methods=["GET"])
def api_heatmap():
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip().lower()
    points = []
    for s in scans:
        if s.get("lat") is not None and s.get("lon") is not None:
            if ssid_filter and s.get("ssid", "").lower() != ssid_filter:
                continue
            # No reading means no weight to derive. Matches prepare_points and
            # the analytics cell builder, which both skip a null signal.
            if s.get("signal_dbm") is None:
                continue
            weight = max(0.1, min(1.0, (s["signal_dbm"] + 100) / 50))
            points.append({
                "lat": s["lat"],
                "lng": s["lon"],
                "weight": weight,
                "ssid": s.get("ssid", ""),
                "signal_dbm": s["signal_dbm"],
            })
    return jsonify({"points": points})


@app.route("/api/current", methods=["GET"])
def api_current():
    conn = get_current_connection()
    scans = load_scans()
    ssid = conn.get("ssid")
    points = []
    if ssid:
        for s in scans:
            if s.get("lat") is not None and s.get("lon") is not None:
                if s.get("ssid", "").lower() == ssid.lower():
                    if s.get("signal_dbm") is None:
                        continue
                    weight = max(0.1, min(1.0, (s["signal_dbm"] + 100) / 50))
                    points.append({
                        "lat": s["lat"],
                        "lng": s["lon"],
                        "weight": weight,
                        "ssid": s.get("ssid", ""),
                        "signal_dbm": s["signal_dbm"],
                    })
    return jsonify({
        "connected": conn.get("connected", False),
        "ssid": conn.get("ssid"),
        "device": conn.get("device"),
        "points": points,
        "totalScans": len(load_scans()),
    })


@app.route("/api/networks", methods=["GET"])
def api_networks():
    networks = scan()
    networks.sort(key=lambda n: n.get("signal_dbm", -100), reverse=True)
    return jsonify({
        "networks": networks,
        "message": None if networks else "Server-side scanning requires the laptop's WiFi (nmcli). Use the phone app to scan.",
    })


@app.route("/api/coverage", methods=["GET"])
def api_coverage():
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip()
    grid_step = request.args.get("step", 0.00005, type=float)
    power = request.args.get("power", 2, type=float)
    max_radius = request.args.get("radius", 0.005, type=float)

    grid = delaunay_interpolate(scans, ssid_filter or None, grid_step, power, max_radius)

    if not grid:
        return jsonify({"grid": [], "message": "Not enough data points for interpolation. Scan at more locations."})

    lats = [p["lat"] for p in grid]
    lons = [p["lng"] for p in grid]
    signals = [p["signal_dbm"] for p in grid]

    return jsonify({
        "grid": grid,
        "stats": {
            "min": min(signals),
            "max": max(signals),
            "avg": round(sum(signals) / len(signals), 1),
            "points": len(grid),
        },
    })


@app.route("/api/contours", methods=["GET"])
def api_contours():
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip()
    grid_step = request.args.get("step", 0.0001, type=float)
    power = request.args.get("power", 2, type=float)
    max_radius = request.args.get("radius", 0.005, type=float)

    contours = generate_contours(scans, ssid_filter or None, grid_step, power, max_radius)

    return jsonify({"contours": contours, "count": len(contours)})


@app.route("/api/mesh", methods=["GET"])
def api_mesh():
    """
    Raw Delaunay triangle mesh: each triangle's three corner points and its
    average signal. Not consumed by map.html yet -- for a future view that
    draws the actual node-to-node triangles instead of only the smoothed
    heatmap/contours.
    """
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip()
    data = mesh_geojson(scans, ssid_filter or None)
    return jsonify(data)


@app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """Delete scans older than the given number of days (default 30)."""
    auth = _check_dashboard_auth()
    if auth:
        return auth
    days = request.json.get("days", 30) if request.is_json else 30
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    client = get_client()
    resp = (
        client.table("scans")
        .delete()
        .lt("created_at", cutoff)
        .execute()
    )
    deleted = len(resp.data) if resp.data else 0
    return jsonify({"deleted": deleted, "cutoff_days": days})


def _constant_time_equals(a, b):
    """Compare two secrets without leaking their common prefix length.

    Both sides are encoded to bytes first: compare_digest raises TypeError on
    non-ASCII str, which would turn a wrong password into a 500 and tell the
    caller the credential was close enough to matter.
    """
    return secrets.compare_digest(
        (a or "").encode("utf-8"), (b or "").encode("utf-8")
    )


def _check_dashboard_auth():
    """HTTP Basic gate for the dashboard and for /api/cleanup.

    Cleanup is the one endpoint that destroys data, so it is gated by the same
    required credentials read at startup -- there is no separate secret here
    to forget to configure, and no anonymous path to it.

    Both halves of the credential are compared, and compared in constant time:
    a byte-by-byte `!=` leaks how much of a guess was correct through response
    timing. Values are encoded first so a non-ASCII password cannot make
    compare_digest raise TypeError (which would turn a wrong password into a
    500). Neither comparison short-circuits, so a correct username with a
    wrong password takes the same time as a wholly wrong pair.
    """
    auth = request.authorization
    if not auth:
        return Response(
            "Unauthorized", 401,
            {"WWW-Authenticate": 'Basic realm="NetRange Dashboard"'},
        )
    user_ok = _constant_time_equals(auth.username or "", DASHBOARD_USERNAME)
    pass_ok = _constant_time_equals(auth.password or "", DASHBOARD_PASS)
    if not (user_ok and pass_ok):
        return Response(
            "Unauthorized", 401,
            {"WWW-Authenticate": 'Basic realm="NetRange Dashboard"'},
        )
    return None


@app.route("/dashboard")
def dashboard():
    auth = _check_dashboard_auth()
    if auth:
        return auth
    return render_template("dashboard.html")


@app.route("/dashboard/logout")
def dashboard_logout():
    return Response("Session expired", 401, {
        "WWW-Authenticate": 'Basic realm="NetRange Dashboard - Expired"',
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    })


@app.route("/api/analytics")
def api_analytics():
    auth = _check_dashboard_auth()
    if auth:
        return auth
    scans = load_scans()
    total = len(scans)
    carriers = {}
    speeds = []
    locations = []
    daily = {}
    for s in scans:
        ssid = s.get("ssid", "Unknown")
        carriers[ssid] = carriers.get(ssid, 0) + 1
        sp = s.get("download_speed_mbps")
        if sp is not None:
            speeds.append(sp)
        lat, lon = s.get("lat"), s.get("lon")
        if lat and lon:
            locations.append({"lat": lat, "lon": lon, "signal_dbm": s.get("signal_dbm", 0), "ssid": ssid, "speed": sp})
        ts = s.get("timestamp", "")
        if ts:
            day = str(ts)[:10]
            daily[day] = daily.get(day, 0) + 1
    avg_speed = round(sum(speeds) / len(speeds), 2) if speeds else None
    return jsonify({
        "total_scans": total,
        "carriers": carriers,
        "avg_speed_mbps": avg_speed,
        "speed_count": len(speeds),
        "locations": locations[:2000],
        "daily": dict(sorted(daily.items())),
    })


@app.route("/api/insights")
def api_insights():
    """
    Diagnostic analysis for the owner-facing dashboard.

    This is deliberately separate from /api/analytics/product: that one is the
    public, k-anonymous publication layer, while this one states plainly what
    the raw data can and cannot support. It is auth-gated because the reasoning
    behind a suppression rule is itself sensitive.
    """
    auth = _check_dashboard_auth()
    if auth:
        return auth
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip().lower()
    if ssid_filter:
        scans = [s for s in scans if (s.get("ssid") or "").lower() == ssid_filter]
    return jsonify(insights.build_insights(scans, ssid_filter))


@app.route("/api/analytics/product")
def api_analytics_product():
    """
    The aggregated, privacy-safe view: area-level coverage cells, per-network
    comparison, a daily quality trend, and a flagged weak-coverage list.
    Every cell/network/day here is suppressed unless at least
    analytics.MIN_DEVICES_PER_CELL distinct devices contributed to it, so
    nothing returned here can be traced back to a single person's location.
    This -- not /api/export's raw rows -- is the shape of data meant to
    leave the building.
    """
    auth = _check_dashboard_auth()
    if auth:
        return auth
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip()
    cell_size_m = request.args.get("cell_size_m", 150.0, type=float)
    weak_threshold = request.args.get("weak_threshold_dbm", -75.0, type=float)

    cells = aggregate_coverage_cells(scans, ssid_filter or None, cell_size_m)
    reverse_geocode_cells(cells)
    return jsonify({
        "cells": cells,
        "carriers": carrier_comparison(scans),
        "daily_trend": daily_quality_trend(scans),
        "weak_zones": weak_zones(cells, weak_threshold),
        "summary": data_quality_summary(scans, cells),
    })


@app.route("/api/export")
def api_export():
    auth = _check_dashboard_auth()
    if auth:
        return auth
    scans = load_scans()
    fmt = request.args.get("format", "csv")
    aggregate = request.args.get("aggregate", "false").lower() == "true"

    if aggregate:
        cells = aggregate_coverage_cells(scans, request.args.get("ssid") or None,
                                          request.args.get("cell_size_m", 150.0, type=float))
        reverse_geocode_cells(cells)
        if fmt == "json":
            return jsonify(cells)
        if fmt == "geojson":
            features = []
            for c in cells:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                    "properties": {
                        "signal_dbm_avg": c["signal_dbm_avg"],
                        "signal_dbm_min": c["signal_dbm_min"],
                        "signal_dbm_max": c["signal_dbm_max"],
                        "speed_mbps_avg": c["speed_mbps_avg"],
                        "sample_count": c["sample_count"],
                        "device_count": c["device_count"],
                        "dominant_network": c["dominant_network"],
                        "location_name": c.get("location_name"),
                        "confidence": c.get("confidence"),
                        "first_seen": c["first_seen"],
                        "last_seen": c["last_seen"],
                    },
                })
            geojson = {"type": "FeatureCollection", "features": features}
            return jsonify(geojson)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["lat", "lon", "location_name", "signal_dbm_avg", "signal_dbm_min", "signal_dbm_max",
                          "speed_mbps_avg", "sample_count", "device_count", "dominant_network",
                          "confidence", "first_seen", "last_seen"])
        for c in cells:
            writer.writerow([c["lat"], c["lon"], c.get("location_name", ""),
                              c["signal_dbm_avg"], c["signal_dbm_min"],
                              c["signal_dbm_max"], c["speed_mbps_avg"], c["sample_count"],
                              c["device_count"], c["dominant_network"], c.get("confidence"),
                              c["first_seen"], c["last_seen"]])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=netrange_coverage_cells_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"},
        )

    # Raw per-scan export -- includes device_id and exact coordinates.
    # This is an internal/debugging export, not the anonymized product --
    # use ?aggregate=true for anything meant to leave the building.
    if fmt == "json":
        return jsonify(scans)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ssid", "signal_dbm", "lat", "lon", "download_speed_mbps", "device_id", "source", "timestamp"])
    for s in scans:
        writer.writerow([
            s.get("ssid", ""),
            s.get("signal_dbm", ""),
            s.get("lat", ""),
            s.get("lon", ""),
            s.get("download_speed_mbps", ""),
            s.get("device_id", ""),
            s.get("source", ""),
            s.get("timestamp", ""),
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=netrange_export_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"},
    )


@app.route("/api/data/coverage")
@require_api_key
def api_data_coverage():
    """
    Public, API-key-gated endpoint for aggregated coverage data.
    Returns GeoJSON FeatureCollection with location names and confidence.

    Usage: GET /api/data/coverage?key=nr_xxxxx
    """
    scans = load_scans()
    ssid_filter = request.args.get("ssid", "").strip()
    cell_size_m = request.args.get("cell_size_m", 150.0, type=float)
    fmt = request.args.get("format", "geojson")

    cells = aggregate_coverage_cells(scans, ssid_filter or None, cell_size_m)
    reverse_geocode_cells(cells)

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["lat", "lon", "location_name", "signal_dbm_avg", "signal_dbm_min",
                          "signal_dbm_max", "speed_mbps_avg", "sample_count", "device_count",
                          "dominant_network", "confidence", "first_seen", "last_seen"])
        for c in cells:
            writer.writerow([c["lat"], c["lon"], c.get("location_name", ""),
                              c["signal_dbm_avg"], c["signal_dbm_min"],
                              c["signal_dbm_max"], c["speed_mbps_avg"], c["sample_count"],
                              c["device_count"], c["dominant_network"], c.get("confidence"),
                              c["first_seen"], c["last_seen"]])
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=netrange_coverage_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"})

    features = []
    for c in cells:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
            "properties": {
                "signal_dbm_avg": c["signal_dbm_avg"],
                "signal_dbm_min": c["signal_dbm_min"],
                "signal_dbm_max": c["signal_dbm_max"],
                "speed_mbps_avg": c["speed_mbps_avg"],
                "sample_count": c["sample_count"],
                "device_count": c["device_count"],
                "dominant_network": c["dominant_network"],
                "location_name": c.get("location_name"),
                "confidence": c.get("confidence"),
                "first_seen": c["first_seen"],
                "last_seen": c["last_seen"],
            },
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/data/carriers")
@require_api_key
def api_data_carriers():
    """Public, API-key-gated per-carrier comparison."""
    scans = load_scans()
    return jsonify({"carriers": carrier_comparison(scans)})


@app.route("/api/leaderboard")
def api_leaderboard():
    """
    Gamification: top contributors by distinct cells scanned, plus
    a coverage completeness score for the known scan area.
    """
    scans = load_scans()
    cells = aggregate_coverage_cells(scans, cell_size_m=150.0)

    # Per-device cell count (privacy: only count, no device_id in output)
    from collections import defaultdict
    device_cells = defaultdict(set)
    for s in scans:
        did = s.get("device_id")
        lat, lon = s.get("lat"), s.get("lon")
        if did and lat is not None and lon is not None:
            key, _, _ = analytics._cell_id(lat, lon, 150.0)
            device_cells[did].add(key)

    ranked = sorted(
        [{"device": did[:12] + "...", "cells_scanned": len(cell_set)}
         for did, cell_set in device_cells.items()],
        key=lambda x: x["cells_scanned"],
        reverse=True,
    )

    # Coverage completeness: what % of the bounding box has data
    if cells:
        lats = [c["lat"] for c in cells]
        lons = [c["lon"] for c in cells]
        area_m2 = (max(lats) - min(lats)) * 111320 * (max(lons) - min(lons)) * 111320 * math.cos(math.radians(sum(lats) / len(lats)))
        cell_area_m2 = 150.0 * 150.0
        coverage_pct = round(min(100.0, (len(cells) * cell_area_m2) / max(1.0, area_m2) * 100), 1)
    else:
        coverage_pct = 0.0

    return jsonify({
        "top_contributors": ranked[:20],
        "total_devices": len(device_cells),
        "total_published_cells": len(cells),
        "coverage_completeness_pct": coverage_pct,
    })


@app.route("/api/gaps")
def api_gaps():
    """
    Coverage gaps: grid cells within the scan bounding box that have
    NO published data. Shows where more scanning is needed.
    """
    scans = load_scans()
    cells = aggregate_coverage_cells(scans, cell_size_m=150.0)

    if not cells:
        return jsonify({"gaps": [], "message": "No published cells yet."})

    lats = [c["lat"] for c in cells]
    lons = [c["lon"] for c in cells]
    from analytics import _cell_id
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # Build set of occupied cell keys
    occupied = set()
    for c in cells:
        key, _, _ = _cell_id(c["lat"], c["lon"], 150.0)
        occupied.add(key)

    # Walk bounding box at 150m grid, find empty cells
    lat_step = 150.0 / 111320.0
    avg_lat = (min_lat + max_lat) / 2
    lon_step = 150.0 / (111320.0 * max(0.0001, math.cos(math.radians(avg_lat))))
    gaps = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            key, _, _ = _cell_id(lat, lon, 150.0)
            if key not in occupied:
                gaps.append({"lat": round(lat, 5), "lon": round(lon, 5)})
            lon += lon_step
        lat += lat_step

    return jsonify({"gaps": gaps[:500], "total_gaps": len(gaps)})


@app.route("/api/snapshot")
@require_api_key
def api_snapshot():
    """
    Time-windowed snapshot: returns aggregated coverage data filtered
    to a specific date range. Buyers can request "coverage as of Q3 2026"
    and get a fixed, citable dataset.

    Usage: GET /api/snapshot?key=nr_xxx&start=2026-07-01&end=2026-09-30
    """
    scans = load_scans()
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    fmt = request.args.get("format", "geojson")

    # Filter scans to date window
    if start or end:
        filtered = []
        for s in scans:
            ts = s.get("timestamp")
            if not ts:
                continue
            day = str(ts)[:10]
            if start and day < start:
                continue
            if end and day > end:
                continue
            filtered.append(s)
        scans = filtered

    cells = aggregate_coverage_cells(scans, request.args.get("ssid") or None,
                                      request.args.get("cell_size_m", 150.0, type=float))
    reverse_geocode_cells(cells)

    meta = {
        "snapshot_start": start or "all-time",
        "snapshot_end": end or "all-time",
        "total_cells": len(cells),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["lat", "lon", "location_name", "signal_dbm_avg", "speed_mbps_avg",
                          "sample_count", "device_count", "dominant_network", "confidence"])
        for c in cells:
            writer.writerow([c["lat"], c["lon"], c.get("location_name", ""),
                              c["signal_dbm_avg"], c["speed_mbps_avg"],
                              c["sample_count"], c["device_count"], c["dominant_network"],
                              c.get("confidence")])
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=netrange_snapshot_{start or 'all'}_{end or 'now'}.csv"})

    features = []
    for c in cells:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
            "properties": {
                "signal_dbm_avg": c["signal_dbm_avg"],
                "speed_mbps_avg": c["speed_mbps_avg"],
                "sample_count": c["sample_count"],
                "device_count": c["device_count"],
                "dominant_network": c["dominant_network"],
                "location_name": c.get("location_name"),
                "confidence": c.get("confidence"),
            },
        })
    return jsonify({"type": "FeatureCollection", "metadata": meta, "features": features})


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("DEBUG", "false").lower() == "true",
    )