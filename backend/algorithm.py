"""
NetRange coverage algorithm.

Replaces flat inverse-distance-weighting with a mesh built from Delaunay
triangulation of live scan points, interpolated with barycentric
coordinates inside the mesh and nearest-neighbor extrapolation outside it.

Concepts
--------
Node   -- a device (phone) identified by device_id. A node produces a
          stream of timestamped points as it moves.
Point  -- one scan: lat, lon, signal_dbm, timestamp, accuracy, device_id.

Every point is weighted by two things before it feeds the mesh:
  - recency (exponential decay -- older points matter less)
  - GPS accuracy (a 50m-accuracy fix matters less than a 5m one)

Before triangulation, each device's raw scan trail is collapsed into a
small number of stable "anchor" points (see compute_node_anchors) so
GPS jitter from a stationary phone doesn't fragment into a cluster of
spurious sliver triangles.

Public functions mirror the return shapes scanner.idw_interpolate() and
scanner.generate_contours() already produce, so app.py's /api/coverage
and /api/contours responses -- and the existing frontend that consumes
them -- do not need to change.
"""

import math
import time
from datetime import datetime, timezone

import numpy as np
from scipy.spatial import Delaunay
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

MIN_POINTS_FOR_MESH = 3
DEFAULT_HALF_LIFE_SECONDS = 15 * 60  # a scan from 15 min ago counts half as much
DEFAULT_MIN_ACCURACY_WEIGHT = 0.15   # even a bad GPS fix keeps some influence
DEFAULT_STAY_RADIUS_M = 12.0         # floor for "same spot" -- consumer GPS is rarely
                                      # genuinely better than this even when it claims to be,
                                      # especially indoors where accuracy is often optimistic
DEFAULT_ACCURACY_M = 15.0            # assumed accuracy when a scan doesn't report one


def _haversine_meters(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters. Exact everywhere, unlike fixed-degree rounding."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def _parse_timestamp(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # accept both seconds and JS-style milliseconds
        return ts / 1000.0 if ts > 1e12 else float(ts)
    try:
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def recency_weight(timestamp, now=None, half_life_seconds=DEFAULT_HALF_LIFE_SECONDS):
    """1.0 for a point from right now, 0.5 after one half-life, etc."""
    now = now if now is not None else time.time()
    ts = _parse_timestamp(timestamp)
    if ts is None:
        return 1.0  # no timestamp -- don't penalize, treat as fresh
    age = max(0.0, now - ts)
    if half_life_seconds <= 0:
        return 1.0
    return 0.5 ** (age / half_life_seconds)


def accuracy_weight(accuracy, min_weight=DEFAULT_MIN_ACCURACY_WEIGHT):
    """1.0 for a tight GPS fix, decaying toward min_weight as accuracy (meters) worsens."""
    if accuracy is None:
        return 1.0
    try:
        acc = float(accuracy)
    except (TypeError, ValueError):
        return 1.0
    if acc <= 5:
        return 1.0
    # halves every 20m of additional error, floored at min_weight
    w = 0.5 ** ((acc - 5) / 20.0)
    return max(min_weight, min(1.0, w))


def prepare_points(scans, ssid_filter=None, now=None,
                    half_life_seconds=DEFAULT_HALF_LIFE_SECONDS):
    """Filter to geolocated scans with a signal reading and attach a combined weight."""
    filtered = scans
    if ssid_filter:
        ssf = ssid_filter.strip().lower()
        filtered = [s for s in scans if s.get("ssid", "").lower() == ssf]

    now = now if now is not None else time.time()
    points = []
    for s in filtered:
        lat, lon, sig = s.get("lat"), s.get("lon"), s.get("signal_dbm")
        if lat is None or lon is None or sig is None:
            continue
        ts = _parse_timestamp(s.get("timestamp"))
        acc = s.get("accuracy")
        try:
            acc_m = float(acc) if acc is not None else DEFAULT_ACCURACY_M
        except (TypeError, ValueError):
            acc_m = DEFAULT_ACCURACY_M
        w = recency_weight(s.get("timestamp"), now, half_life_seconds) * \
            accuracy_weight(s.get("accuracy"))
        points.append({
            "lat": float(lat), "lon": float(lon), "signal_dbm": float(sig),
            "weight": w, "device_id": s.get("device_id"),
            "download_speed_mbps": s.get("download_speed_mbps"),
            "accuracy_m": acc_m, "timestamp": ts if ts is not None else now,
        })
    return points


def compute_node_anchors(points, stay_radius_m=DEFAULT_STAY_RADIUS_M):
    """
    Collapse each device's raw scan trail into a small number of stable
    "anchor" points, instead of triangulating every raw GPS fix directly.

    Consumer GPS jitters -- a phone sitting still in one room can report a
    different lat/lon on every scan, easily 10-30m of noise indoors. Fed
    directly into Delaunay, each jittery fix becomes its own triangle
    vertex, producing a cluster of spurious sliver triangles for what is
    physically one location.

    Per device, points are walked in time order. A new point merges into
    the current anchor (weighted-average position/signal, weight favoring
    tighter GPS accuracy and more recent scans) if it falls within the
    anchor's uncertainty radius -- max(stay_radius_m, the point's own
    reported accuracy). Once a point falls outside that radius, the
    device has genuinely moved: the current anchor is finalized and a new
    one starts. This is a standard "stay point" pattern from mobility
    trace analysis, adapted to blend signal readings instead of just
    detecting dwell locations.

    Points with no device_id (shouldn't happen from the app, but keeps
    this robust) skip smoothing and pass through individually, since
    there's no way to know if they came from the same phone.
    """
    by_device = {}
    singles = []
    for p in points:
        did = p.get("device_id")
        if not did:
            singles.append(p)
        else:
            by_device.setdefault(did, []).append(p)

    anchors = []

    for did, pts in by_device.items():
        pts.sort(key=lambda p: p["timestamp"])
        anchor = None
        for p in pts:
            if anchor is None:
                anchor = _new_anchor(p)
                continue
            dist = _haversine_meters(anchor["lat"], anchor["lon"], p["lat"], p["lon"])
            radius = max(stay_radius_m, p["accuracy_m"], anchor["accuracy_m"])
            if dist <= radius:
                _merge_into_anchor(anchor, p)
            else:
                anchors.append(_finalize_anchor(anchor))
                anchor = _new_anchor(p)
        if anchor is not None:
            anchors.append(_finalize_anchor(anchor))

    for p in singles:
        anchors.append(_finalize_anchor(_new_anchor(p)))

    return anchors


def _new_anchor(p):
    return {
        "lat_sum": p["lat"] * p["weight"], "lon_sum": p["lon"] * p["weight"],
        "sig_sum": p["signal_dbm"] * p["weight"], "w_sum": p["weight"],
        "lat": p["lat"], "lon": p["lon"],
        "accuracy_m": p["accuracy_m"], "max_weight": p["weight"],
        "speeds": [p["download_speed_mbps"]] if p.get("download_speed_mbps") is not None else [],
    }


def _merge_into_anchor(anchor, p):
    anchor["lat_sum"] += p["lat"] * p["weight"]
    anchor["lon_sum"] += p["lon"] * p["weight"]
    anchor["sig_sum"] += p["signal_dbm"] * p["weight"]
    anchor["w_sum"] += p["weight"]
    anchor["lat"] = anchor["lat_sum"] / anchor["w_sum"]
    anchor["lon"] = anchor["lon_sum"] / anchor["w_sum"]
    anchor["accuracy_m"] = min(anchor["accuracy_m"], p["accuracy_m"])
    anchor["max_weight"] = max(anchor["max_weight"], p["weight"])
    if p.get("download_speed_mbps") is not None:
        anchor["speeds"].append(p["download_speed_mbps"])


def _finalize_anchor(anchor):
    w = anchor["w_sum"] or 1e-9
    avg_speed = round(sum(anchor["speeds"]) / len(anchor["speeds"]), 2) if anchor["speeds"] else None
    return {
        "lat": anchor["lat"], "lon": anchor["lon"],
        "signal_dbm": anchor["sig_sum"] / w,
        "weight": anchor["max_weight"],
        "download_speed_mbps": avg_speed,
    }


def _idw_fallback_grid(points, grid_step, power, max_radius):
    """Used only when there aren't enough points for a triangulation (<3)."""
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    signals = [p["signal_dbm"] for p in points]

    min_lat, max_lat = min(lats) - max_radius, max(lats) + max_radius
    min_lon, max_lon = min(lons) - max_radius, max(lons) + max_radius

    grid = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            weights, values = [], []
            for i in range(len(points)):
                d = math.sqrt((lat - lats[i]) ** 2 + (lon - lons[i]) ** 2)
                if d < 1e-12:
                    weights, values = [1.0], [signals[i]]
                    break
                if d > max_radius:
                    continue
                weights.append(1.0 / (d ** power))
                values.append(signals[i])
            if weights:
                interpolated = sum(w * v for w, v in zip(weights, values)) / sum(weights)
                grid.append({
                    "lat": round(lat, 6), "lng": round(lon, 6),
                    "signal_dbm": round(interpolated, 1),
                    "weight": max(0.1, min(1.0, (interpolated + 100) / 50)),
                })
            lon += grid_step
        lat += grid_step
    return grid


def build_mesh(scans, ssid_filter=None, now=None):
    """
    Core triangulation step. Returns None if there aren't enough points
    (caller should fall back to IDW), otherwise a dict with the raw
    Delaunay mesh plus the source points, for use by both the coverage
    grid and (later, if wanted) a frontend that draws the triangles directly.
    """
    points = prepare_points(scans, ssid_filter, now)
    points = compute_node_anchors(points)

    if len(points) < MIN_POINTS_FOR_MESH:
        return None

    coords = np.array([[p["lon"], p["lat"]] for p in points])
    signals = np.array([p["signal_dbm"] for p in points])
    weights = np.array([p["weight"] for p in points])
    speeds = np.array([p.get("download_speed_mbps") if p.get("download_speed_mbps") is not None else np.nan for p in points])

    try:
        tri = Delaunay(coords)
    except Exception:
        # collinear or otherwise degenerate point set -- can't triangulate
        return None

    return {"points": points, "coords": coords, "signals": signals,
            "weights": weights, "speeds": speeds, "triangulation": tri}


def delaunay_interpolate(scans, ssid_filter=None, grid_step=0.00005,
                          power=2, max_radius=0.005, now=None):
    """
    Drop-in replacement for scanner.idw_interpolate(). Same return shape:
    a list of {"lat", "lng", "signal_dbm", "weight"} dicts on a regular grid.

    Interior of the point mesh: linear (barycentric) interpolation across
    Delaunay triangles -- this is the "average the shape formed by 3+
    points" behavior.
    Outside the mesh (nothing to triangulate toward): nearest-neighbor,
    so the grid still has a value out to max_radius instead of a hole.
    """
    mesh = build_mesh(scans, ssid_filter, now)
    if mesh is None:
        points = prepare_points(scans, ssid_filter, now)
        points = compute_node_anchors(points)
        if not points:
            return []
        return _idw_fallback_grid(points, grid_step, power, max_radius)

    coords, signals = mesh["coords"], mesh["signals"]
    lons, lats = coords[:, 0], coords[:, 1]

    min_lat, max_lat = lats.min() - max_radius, lats.max() + max_radius
    min_lon, max_lon = lons.min() - max_radius, lons.max() + max_radius

    grid_lats = np.arange(min_lat, max_lat + grid_step, grid_step)
    grid_lons = np.arange(min_lon, max_lon + grid_step, grid_step)
    gx, gy = np.meshgrid(grid_lons, grid_lats)
    query = np.column_stack([gx.ravel(), gy.ravel()])

    interior = LinearNDInterpolator(mesh["triangulation"], signals)
    exterior = NearestNDInterpolator(coords, signals)

    values = interior(query)
    nan_mask = np.isnan(values)
    if nan_mask.any():
        values[nan_mask] = exterior(query[nan_mask])

    # drop grid points too far from every source point, same as old max_radius cutoff
    from scipy.spatial import cKDTree
    tree = cKDTree(coords)
    dist, _ = tree.query(query, k=1)
    keep = dist <= max_radius

    grid = []
    for (lon, lat), sig, ok in zip(query, values, keep):
        if not ok or np.isnan(sig):
            continue
        grid.append({
            "lat": round(float(lat), 6),
            "lng": round(float(lon), 6),
            "signal_dbm": round(float(sig), 1),
            "weight": max(0.1, min(1.0, (float(sig) + 100) / 50)),
        })
    return grid


def generate_contours(scans, ssid_filter=None, grid_step=0.0001,
                       power=2, max_radius=0.005, now=None):
    """
    Drop-in replacement for scanner.generate_contours(). Builds the grid
    via delaunay_interpolate() then runs the same marching-squares
    contour extraction as before, so the response shape to the frontend
    (level/label/color/polygon) is unchanged.
    """
    grid = delaunay_interpolate(scans, ssid_filter, grid_step, power, max_radius, now)
    if not grid:
        return []

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

    segment_lookup = {
        1: lambda t, r, b, l: [b, l], 2: lambda t, r, b, l: [r, b],
        3: lambda t, r, b, l: [r, l], 4: lambda t, r, b, l: [t, r],
        5: lambda t, r, b, l: [t, r, b, l], 6: lambda t, r, b, l: [t, b],
        7: lambda t, r, b, l: [t, l], 8: lambda t, r, b, l: [l, t],
        9: lambda t, r, b, l: [l, b], 10: lambda t, r, b, l: [t, l, r, b],
        11: lambda t, r, b, l: [t, r], 12: lambda t, r, b, l: [r, l],
        13: lambda t, r, b, l: [r, b], 14: lambda t, r, b, l: [b, l],
    }

    contours = []
    for threshold, label, color in levels:
        segments = []
        for r in range(n_rows - 1):
            for c in range(n_cols - 1):
                tl, tr, br, bl = matrix[r][c], matrix[r][c + 1], matrix[r + 1][c + 1], matrix[r + 1][c]
                if any(v is None for v in (tl, tr, br, bl)):
                    continue

                case = 0
                if tl >= threshold: case |= 8
                if tr >= threshold: case |= 4
                if br >= threshold: case |= 2
                if bl >= threshold: case |= 1
                if case in (0, 15):
                    continue

                lat0 = min_lat + r * grid_step
                lng0 = min_lng + c * grid_step
                top = (lat0, lng0 + interp(tl, tr, threshold) * grid_step)
                right = (lat0 + interp(tr, br, threshold) * grid_step, lng0 + grid_step)
                bottom = (lat0 + grid_step, lng0 + interp(bl, br, threshold) * grid_step)
                left = (lat0 + interp(tl, bl, threshold) * grid_step, lng0)

                if case == 15:
                    segments.append([top, right, bottom, left])
                elif case in segment_lookup:
                    segments.append(segment_lookup[case](top, right, bottom, left))

        if segments:
            all_pts = {(round(p[0], 6), round(p[1], 6)) for seg in segments for p in seg}
            if len(all_pts) >= 3:
                pts = list(all_pts)
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
                contours.append({"level": threshold, "label": label, "color": color, "polygon": pts})

    return contours


def mesh_geojson(scans, ssid_filter=None, now=None):
    """
    Optional extra: the raw triangle mesh itself (nodes + edges + per-triangle
    average signal), for a future frontend view that draws the actual
    triangles/circles instead of only the smoothed heatmap. Not required
    by the current app.py routes -- wire up an /api/mesh route if you want
    to expose it.
    """
    mesh = build_mesh(scans, ssid_filter, now)
    if mesh is None:
        return {"triangles": [], "points": []}

    coords, signals, tri = mesh["coords"], mesh["signals"], mesh["triangulation"]
    speeds = mesh.get("speeds")
    triangles = []
    for simplex in tri.simplices:
        pts = coords[simplex]
        vals = signals[simplex]
        avg_speed = None
        if speeds is not None:
            tri_speeds = speeds[simplex]
            valid = tri_speeds[~(tri_speeds != tri_speeds)]
            if len(valid) > 0:
                avg_speed = round(float(valid.mean()), 2)
        triangles.append({
            "vertices": [{"lat": float(p[1]), "lng": float(p[0])} for p in pts],
            "avg_signal_dbm": round(float(vals.mean()), 1),
            "avg_download_speed_mbps": avg_speed,
        })
    points = [{"lat": float(p["lat"]), "lng": float(p["lon"]),
               "signal_dbm": round(p["signal_dbm"], 1),
               "download_speed_mbps": p.get("download_speed_mbps")} for p in mesh["points"]]
    return {"triangles": triangles, "points": points}
