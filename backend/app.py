import os
import json
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from scanner import scan, save_scan, load_scans, get_current_connection, idw_interpolate

app = Flask(__name__)
CORS(app)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCANS_FILE = os.path.join(DATA_DIR, "scans.json")


@app.route("/")
def index():
    return render_template("map.html")


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
    count = save_scan(networks, SCANS_FILE)
    total = len(load_scans(SCANS_FILE))
    return jsonify({
        "count": count,
        "totalScans": total,
        "networks": networks,
        "message": None if networks else "Server-side scanning requires the laptop's WiFi (nmcli). Use the phone app to scan.",
    })


@app.route("/api/scans", methods=["GET"])
def api_scans():
    scans = load_scans(SCANS_FILE)
    return jsonify({"total": len(scans), "scans": scans})


@app.route("/api/heatmap", methods=["GET"])
def api_heatmap():
    scans = load_scans(SCANS_FILE)
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
    scans = load_scans(SCANS_FILE)
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
        "totalScans": len(load_scans(SCANS_FILE)),
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
    scans = load_scans(SCANS_FILE)
    ssid_filter = request.args.get("ssid", "").strip()
    grid_step = request.args.get("step", 0.00005, type=float)
    power = request.args.get("power", 2, type=float)
    max_radius = request.args.get("radius", 0.005, type=float)

    grid = idw_interpolate(scans, ssid_filter or None, grid_step, power, max_radius)

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


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("DEBUG", "false").lower() == "true",
    )