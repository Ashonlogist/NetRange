import os
import csv
import io
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, session
from flask_cors import CORS
from scanner import scan, get_current_connection
from db import save_scan, load_scans, get_client
from algorithm import delaunay_interpolate, generate_contours, mesh_geojson
from analytics import (aggregate_coverage_cells, carrier_comparison,
                        daily_quality_trend, weak_zones, data_quality_summary)
from geocoding import reverse_geocode_cells
from api_keys import require_api_key

app = Flask(__name__)
CORS(app)

DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "nr-secret-2026-analytics-prod")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASSWORD", "netrange2026")
app.secret_key = DASHBOARD_SECRET

APP_VERSION = "1.4.0"
APK_URL = "https://netrange.ashonlogist.website/download/netrange.apk"


@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/map")
def map_view():
    return render_template("map.html")


@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), "static"), filename, as_attachment=True)


@app.route("/api/version")
def api_version():
    return jsonify({
        "version": APP_VERSION,
        "apkUrl": APK_URL,
        "notes": [
            "Scan: tap networks directly as target (no text input)",
            "Cellular network detection improved",
            "Coverage map: browser warnings hidden in-app",
            "Settings: API URL hidden",
        ],
    })


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


@app.route("/api/scan", methods=["POST"])
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

    def to_dbm(strength):
        if not isinstance(strength, (int, float)):
            return None
        if strength < 0:
            return round(strength, 1)
        if strength <= 1:
            return round(strength * 50 - 100, 1)
        return round(strength / 2 - 100, 1)

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
        if signal_dbm is None:
            signal_dbm = -70.0
        signal_pct = max(0, min(100, round((signal_dbm + 100) * 2)))
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
        cell_dbm = to_dbm(signal_strength)
        if cell_dbm is None:
            cell_dbm = -70.0
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
    msg = None
    if count == 0 and target:
        msg = f"No scan data for SSID '{target}'. Make sure the phone sees that network."
    return jsonify({"count": count, "totalScans": total, "message": msg})


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
            weight = max(0.1, min(1.0, (s.get("signal_dbm", -80) + 100) / 50))
            points.append({
                "lat": s["lat"],
                "lng": s["lon"],
                "weight": weight,
                "ssid": s.get("ssid", ""),
                "signal_dbm": s.get("signal_dbm", 0),
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
                    weight = max(0.1, min(1.0, (s.get("signal_dbm", -80) + 100) / 50))
                    points.append({
                        "lat": s["lat"],
                        "lng": s["lon"],
                        "weight": weight,
                        "ssid": s.get("ssid", ""),
                        "signal_dbm": s.get("signal_dbm", 0),
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


def _check_dashboard_auth():
    auth = request.authorization
    if not auth or auth.password != DASHBOARD_PASS:
        return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="NetRange Dashboard"'})
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