"""
Offline reverse geocoding for Ghana locations.

Uses bounding-box lookups for known neighborhoods, campuses, and landmarks.
No API calls, no network dependency — just a dictionary of rectangles.
Returns None for coordinates outside the lookup table; callers should
fall back to raw coordinates in that case.
"""

import math

# (min_lat, max_lat, min_lon, max_lon, name)
# Sorted most-specific first so smaller neighborhoods match before regions.
_GHANA_PLACES = [
    # Greater Accra — East Legon / Airport area
    (5.635, 5.670, -0.185, -0.155, "East Legon"),
    (5.600, 5.640, -0.210, -0.175, "Airport Residential Area"),
    (5.590, 5.620, -0.195, -0.165, "Legon"),
    (5.555, 5.590, -0.200, -0.170, "University of Ghana Campus"),
    (5.645, 5.675, -0.230, -0.195, "Achimota"),
    (5.570, 5.600, -0.240, -0.210, "Haatso"),
    (5.545, 5.575, -0.235, -0.205, "Madina"),
    (5.530, 5.555, -0.190, -0.160, "Adenta"),
    (5.550, 5.580, -0.165, -0.135, "Teshie / Nungua"),
    (5.560, 5.590, -0.155, -0.125, "Kasoa (East)"),
    (5.600, 5.630, -0.265, -0.235, "Osu"),
    (5.580, 5.610, -0.225, -0.195, "Labone"),
    (5.560, 5.590, -0.220, -0.190, "Cantonments"),
    (5.540, 5.570, -0.215, -0.185, "Roman Ridge"),
    (5.520, 5.550, -0.215, -0.185, "Bonso"),
    (5.500, 5.530, -0.200, -0.170, "Korle Bu"),
    (5.600, 5.630, -0.300, -0.265, "Dansoman"),
    (5.630, 5.660, -0.300, -0.265, "Amasaman"),
    (5.680, 5.710, -0.260, -0.230, "Tema Community 1"),
    (5.670, 5.700, -0.230, -0.200, "Tema Community 7"),
    (5.660, 5.690, -0.200, -0.170, "Tema Community 25"),
    (5.700, 5.730, -0.190, -0.160, "Kpone"),

    # Ashanti Region
    (6.660, 6.700, -1.630, -1.590, "Kumasi Adum"),
    (6.680, 6.720, -1.650, -1.610, "Kumasi Kejetia"),
    (6.640, 6.680, -1.620, -1.580, "KNUST Campus"),
    (6.690, 6.720, -1.600, -1.560, "Ahodwo"),
    (6.650, 6.690, -1.660, -1.620, "Ahinsan"),
    (6.710, 6.750, -1.640, -1.600, "Ejisu"),

    # Northern Region
    (9.390, 9.430, -0.850, -0.810, "Tamale Central"),
    (9.400, 9.440, -0.870, -0.840, "Tamale Korle"),
    (9.370, 9.400, -0.830, -0.800, "TamaleWatani"),

    # Western Region
    (4.880, 4.920, -1.780, -1.740, "Takoradi Market"),
    (4.890, 4.930, -1.760, -1.720, "Sekondi"),

    # Central Region
    (5.100, 5.140, -1.290, -1.250, "Cape Coast"),
    (5.090, 5.120, -1.260, -1.230, "University of Cape Coast"),

    # Volta Region
    (6.080, 6.120, 0.250, 0.290, "Ho"),
]


def reverse_geocode(lat, lon):
    """
    Return a human-readable location name for Ghana coordinates,
    or None if no match is found (caller should show raw coords).
    """
    if lat is None or lon is None:
        return None
    for min_lat, max_lat, min_lon, max_lon, name in _GHANA_PLACES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return name
    return None


def reverse_geocode_cells(cells):
    """
    Attach a location_name to each cell dict in-place.
    """
    for c in cells:
        c["location_name"] = reverse_geocode(c.get("lat"), c.get("lon"))
    return cells
