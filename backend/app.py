import os
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from scanner import scan, get_current_connection
from db import save_scan, load_scans
from algorithm import delaunay_interpolate, generate_contours, mesh_geojson

app = Flask(__name__)
CORS(app)

APP_VERSION = "1.2.0"
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


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("DEBUG", "false").lower() == "true",
    )