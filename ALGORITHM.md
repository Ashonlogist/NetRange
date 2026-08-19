# NetRange coverage algorithm

This document explains how NetRange turns raw phone scans into a live
coverage map. The implementation lives in `backend/algorithm.py`.

## Vocabulary

- **Node** — a device (a phone) taking part in the scan. Identified by
  `device_id`. A node produces a stream of points over time as it moves
  around campus.
- **Point** — a single scan reading: `lat`, `lon`, `signal_dbm`,
  `timestamp`, `accuracy`, and which node produced it.

## Why not just blur the map (the old approach)

The original implementation used inverse-distance weighting (IDW):
every grid cell's value was a weighted average of *every* nearby scan
point, weighted only by 1/distance. That produces a smooth blur, but
the blur has no relationship to the actual geometry of where points
were taken — it can shade in areas that sit nowhere near any real
measurement, and it treats "3 points loosely scattered" the same as
"3 points that clearly bound a region."

## The mesh: Delaunay triangulation

Instead, every scan point is a vertex. We connect points into
triangles such that no other point ever falls inside any triangle's
circumcircle (the circle drawn through its three corners). That
circumcircle rule is what keeps triangles well-shaped instead of long
and sliver-thin, and it's a standard, solved algorithm — the code uses
`scipy.spatial.Delaunay` rather than hand-rolling it.

This is the direct implementation of "point A, point B, point C —
draw a circle through them, use the triangle they form" — the more
nodes report in, the more triangles subdivide the area, and each
triangle gets smaller and more locally accurate. That shrinking-shape
behavior isn't something extra to build — it falls straight out of
triangulating more points.

## Filling in a triangle: barycentric interpolation

Once the mesh exists, any point *inside* a triangle gets a signal
value that's a blend of the three corner signals, weighted by how
close it sits to each corner (its barycentric coordinates relative to
that triangle). This is what `LinearNDInterpolator` computes. It's the
"average network in that shape" step — a location near corner A reads
close to A's signal; the centroid reads close to the average of all
three.

Points *outside* the mesh (nothing to triangulate toward yet, e.g. off
the edge of where anyone has scanned) fall back to nearest-neighbor
extrapolation (`NearestNDInterpolator`), so the coverage grid still
has a value out to the configured radius instead of a hole.

If there are fewer than 3 usable points, there's nothing to
triangulate — the algorithm falls back to the old IDW blend for that
case only, so the map never breaks on sparse data.

## Making it live

Two things keep the mesh honest as coverage genuinely changes over
time (a router reboots, a corridor gets congested, someone's still
standing there five minutes later):

- **Recency weighting** — each point's influence decays with age
  (`recency_weight`, default half-life 15 minutes). A scan from just
  now counts fully; a scan from an hour ago barely moves the result.
- **Accuracy weighting** — a scan taken with a tight GPS fix (a few
  meters) counts more than one from a phone reporting 50m accuracy
  (`accuracy_weight`).

Both weights currently affect the fallback IDW path directly, and are
computed and attached to every point (`prepare_points`) for use in
blending near-duplicate points from a stationary node
(`_dedupe_and_blend`) before triangulation. Because Delaunay
triangulation itself is cheap (O(n log n), and this app realistically
has tens to low hundreds of live points, not millions), the mesh can
be rebuilt from scratch every time new scans land or on a short
polling interval — there's no need to incrementally patch the old
mesh.

## What ships in each response

`app.py`'s existing routes are unchanged in shape, so the current
frontend (`map.html`) keeps working without modification:

- `GET /api/coverage` → `delaunay_interpolate()` → same
  `{lat, lng, signal_dbm, weight}` grid as before.
- `GET /api/contours` → `generate_contours()` → same
  `{level, label, color, polygon}` shape as before (marching squares
  run on top of the new grid instead of the old IDW grid).
- `GET /api/mesh` (new, optional) → `mesh_geojson()` → the raw
  triangles themselves (three vertices + average signal each), for a
  future view that draws the actual node-to-node mesh instead of only
  the smoothed heatmap/contours. Nothing in the current frontend calls
  this yet.

## Known limits, honestly

- `data/scans.json` is still a flat JSON file rewritten on every save.
  The algorithm change doesn't fix that — see the earlier review notes
  on moving to SQLite (or at least a rolling per-node buffer) if scan
  volume grows.
- Recency/accuracy weighting currently shapes the IDW fallback path
  directly; inside the triangulated region they influence which points
  survive `_dedupe_and_blend`, but the interior interpolation itself
  (`LinearNDInterpolator`) is unweighted linear blending across a
  triangle's three corners. If you want stale/inaccurate points to
  visibly fade *within* a triangle too (not just at the dedupe step),
  that's the next thing to add — happy to build it once this version
  is confirmed working end to end.
- There's still no auth on `/api/scan` — anyone with the URL can post
  fake points into the mesh. Not addressed here; flagged in the earlier
  review.
