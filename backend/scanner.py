import subprocess
import json
import os
import re
import math
from datetime import datetime, timezone


INTERFACE = "wlp3s0"


def split_terse(line):
    parts = []
    current = ""
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == ":":
            current += ":"
            i += 2
        elif line[i] == ":":
            parts.append(current)
            current = ""
            i += 1
        else:
            current += line[i]
            i += 1
    parts.append(current)
    return parts


def scan():
    result = subprocess.run(
        ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,ACTIVE", "device", "wifi", "list"],
        capture_output=True, text=True, timeout=15,
    )
    networks = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = split_terse(line)
        if len(parts) < 5:
            continue
        ssid = parts[0].strip()
        bssid = parts[1].strip()
        signal_pct = int(parts[2].strip())
        channel = parts[3].strip()
        active = parts[4].strip() == "yes"

        dbm = signal_pct_to_dbm(signal_pct)
        networks.append({
            "ssid": ssid,
            "bssid": bssid,
            "signal_pct": signal_pct,
            "signal_dbm": dbm,
            "channel": channel,
            "active": active,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return networks


def signal_pct_to_dbm(pct):
    return round((pct / 2) - 100, 1)


def save_scan(networks, filepath="data/scans.json"):
    import os
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing = []
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            existing = json.load(f)
    existing.extend(networks)
    with open(filepath, "w") as f:
        json.dump(existing, f, indent=2)
    return len(networks)


def load_scans(filepath="data/scans.json"):
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r") as f:
        return json.load(f)


def idw_interpolate(scans, ssid_filter=None, grid_step=0.0001, power=2, max_radius=0.01):
    filtered = scans
    if ssid_filter:
        ssf = ssid_filter.strip().lower()
        filtered = [s for s in scans if s.get("ssid", "").lower() == ssf]

    points_with_signal = [
        s for s in filtered
        if s.get("lat") is not None and s.get("lon") is not None and s.get("signal_dbm") is not None
    ]

    if len(points_with_signal) < 3:
        return []

    lats = [s["lat"] for s in points_with_signal]
    lons = [s["lon"] for s in points_with_signal]
    signals = [s["signal_dbm"] for s in points_with_signal]

    min_lat = min(lats) - max_radius
    max_lat = max(lats) + max_radius
    min_lon = min(lons) - max_radius
    max_lon = max(lons) + max_radius

    grid = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            weights = []
            values = []
            for i in range(len(points_with_signal)):
                d = math.sqrt((lat - lats[i]) ** 2 + (lon - lons[i]) ** 2)
                if d < 1e-12:
                    weights.append(1.0)
                    values.append(signals[i])
                    break
                if d > max_radius:
                    continue
                w = 1.0 / (d ** power)
                weights.append(w)
                values.append(signals[i])

            if weights and sum(weights) > 0:
                interpolated = sum(w * v for w, v in zip(weights, values)) / sum(weights)
                grid.append({
                    "lat": round(lat, 6),
                    "lng": round(lon, 6),
                    "signal_dbm": round(interpolated, 1),
                    "weight": max(0.1, min(1.0, (interpolated + 100) / 50)),
                })
            lon += grid_step
        lat += grid_step

    return grid


def get_current_connection():
    result = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
        capture_output=True, text=True, timeout=10,
    )
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) >= 4 and parts[2].strip() == "802-11-wireless":
            return {
                "ssid": parts[0].strip(),
                "uuid": parts[1].strip(),
                "device": parts[3].strip(),
                "connected": True,
            }
    return {"ssid": None, "uuid": None, "device": None, "connected": False}


if __name__ == "__main__":
    conn = get_current_connection()
    print(f"Connected: {conn['connected']}, SSID: {conn['ssid']}")
    networks = scan()
    for n in networks:
        print(f"{n['ssid']:30s} {n['bssid']:17s} {n['signal_dbm']:>5d} dBm  ch{n['channel']}")