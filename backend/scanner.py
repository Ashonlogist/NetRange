import subprocess
import json
import os
import re
import math
import shutil
from datetime import datetime, timezone


INTERFACE = "wlp3s0"

NMCLI = shutil.which("nmcli")


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
    if not NMCLI:
        return []
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,ACTIVE", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
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

    if len(points_with_signal) < 1:
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


def generate_contours(scans, ssid_filter=None, grid_step=0.0001, power=2, max_radius=0.01):
    grid = idw_interpolate(scans, ssid_filter, grid_step, power, max_radius)
    if not grid:
        return []

    grid_step_lat = grid_step
    grid_step_lon = grid_step
    min_lat = min(p["lat"] for p in grid)
    min_lng = min(p["lng"] for p in grid)

    n_rows = round((max(p["lat"] for p in grid) - min_lat) / grid_step) + 1
    n_cols = round((max(p["lng"] for p in grid) - min_lng) / grid_step) + 1

    matrix = [[None] * n_cols for _ in range(n_rows)]
    for p in grid:
        r = round((p["lat"] - min_lat) / grid_step)
        c = round((p["lng"] - min_lng) / grid_step)
        if 0 <= r < n_rows and 0 <= c < n_cols:
            matrix[r][c] = p["signal_dbm"]

    def interp(v1, v2, threshold):
        if v1 is None or v2 is None:
            return 0.5
        if abs(v2 - v1) < 1e-12:
            return 0.5
        return (threshold - v1) / (v2 - v1)

    levels = [
        (-35, "Excellent", "#22c55e"),
        (-45, "Good", "#06b6d4"),
        (-55, "Fair", "#eab308"),
        (-65, "Weak", "#f97316"),
        (-75, "Poor", "#ef4444"),
    ]

    contours = []
    for threshold, label, color in levels:
        segments = []
        for r in range(n_rows - 1):
            for c in range(n_cols - 1):
                tl = matrix[r][c]
                tr = matrix[r][c + 1]
                br = matrix[r + 1][c + 1]
                bl = matrix[r + 1][c]

                if any(v is None for v in [tl, tr, br, bl]):
                    continue

                case = 0
                if tl >= threshold: case |= 8
                if tr >= threshold: case |= 4
                if br >= threshold: case |= 2
                if bl >= threshold: case |= 1

                if case == 0 or case == 15:
                    continue

                lat0 = min_lat + r * grid_step
                lng0 = min_lng + c * grid_step

                top = (lat0, lng0 + interp(tl, tr, threshold) * grid_step)
                right = (lat0 + interp(tr, br, threshold) * grid_step, lng0 + grid_step)
                bottom = (lat0 + grid_step, lng0 + interp(bl, br, threshold) * grid_step)
                left = (lat0 + interp(tl, bl, threshold) * grid_step, lng0)

                segment_lookup = {
                    1: [bottom, left], 2: [right, bottom], 3: [right, left],
                    4: [top, right], 5: [top, right, bottom, left],
                    6: [top, bottom], 7: [top, left], 8: [left, top],
                    9: [left, bottom], 10: [top, left, right, bottom],
                    11: [top, right], 12: [right, left], 13: [right, bottom],
                    14: [bottom, left],
                }

                if case in segment_lookup:
                    seg = segment_lookup[case]
                    segments.append(seg)

        if segments:
            all_pts = set()
            for seg in segments:
                for pt in seg:
                    all_pts.add((round(pt[0], 6), round(pt[1], 6)))

            if len(all_pts) >= 3:
                pts = list(all_pts)
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
                contours.append({
                    "level": threshold,
                    "label": label,
                    "color": color,
                    "polygon": pts,
                })

    return contours


def get_current_connection():
    if not NMCLI:
        return {"ssid": None, "uuid": None, "device": None, "connected": False}
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"ssid": None, "uuid": None, "device": None, "connected": False}
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