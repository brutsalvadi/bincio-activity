"""GPS track simplification using the Ramer-Douglas-Peucker algorithm."""

from typing import Optional

from bincio.extract.models import DataPoint


def _rdp_mask(coords: list[list[float]], epsilon: float) -> list[bool]:
    """Pure-Python RDP — returns a boolean keep-mask of the same length as coords."""
    n = len(coords)
    if n < 2:
        return [True] * n

    mask = [False] * n
    mask[0] = mask[-1] = True

    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        x1, y1 = coords[start]
        x2, y2 = coords[end]
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy

        max_dist = -1.0
        max_idx = start + 1
        for i in range(start + 1, end):
            x0, y0 = coords[i]
            if seg_len_sq == 0:
                d = ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5
            else:
                t = ((x0 - x1) * dx + (y0 - y1) * dy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                px, py = x1 + t * dx, y1 + t * dy
                d = ((x0 - px) ** 2 + (y0 - py) ** 2) ** 0.5
            if d > max_dist:
                max_dist = d
                max_idx = i

        if max_dist >= epsilon:
            mask[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))

    return mask


def simplify_track(
    points: list[DataPoint],
    epsilon: float = 0.0001,
) -> list[DataPoint]:
    """Return a simplified subset of points using RDP.

    epsilon is in degrees (~11m at equator for 0.0001).
    Points without GPS coordinates are dropped.
    """
    gps_pts = [(p, p.lat, p.lon) for p in points if p.lat is not None and p.lon is not None]
    if len(gps_pts) < 2:
        return [p for p, _, _ in gps_pts]

    coords = [[lon, lat] for _, lat, lon in gps_pts]
    mask = _rdp_mask(coords, epsilon=epsilon)
    return [p for (p, _, _), keep in zip(gps_pts, mask) if keep]


def preview_coords(
    points: list[DataPoint],
    max_points: int = 20,
) -> list[list[float]] | None:
    """Return a small list of [lat, lon] pairs for card thumbnail rendering.

    Uses a coarser RDP pass, then subsamples to at most max_points.
    Returns None if there is no GPS data.
    """
    gps = [(p.lat, p.lon) for p in points if p.lat is not None and p.lon is not None]
    if len(gps) < 2:
        return None

    # Coarse RDP (larger epsilon = fewer points)
    coords = [[lon, lat] for lat, lon in gps]
    mask = _rdp_mask(coords, epsilon=0.001)
    reduced = [gps[i] for i, keep in enumerate(mask) if keep]

    # Subsample if still too many — always include last point without exceeding max_points
    if len(reduced) > max_points:
        step = len(reduced) / (max_points - 1)
        reduced = [reduced[int(i * step)] for i in range(max_points - 1)]
        reduced.append(gps[-1])

    return [[round(lat, 5), round(lon, 5)] for lat, lon in reduced]


def build_geojson(
    points: list[DataPoint],
    activity_id: str,
    epsilon: float = 0.0001,
    original_count: Optional[int] = None,
) -> dict:
    """Build a GeoJSON Feature for the simplified track."""
    simplified = simplify_track(points, epsilon=epsilon)

    coordinates = [
        [p.lon, p.lat, p.elevation_m] if p.elevation_m is not None else [p.lon, p.lat]
        for p in simplified
        if p.lon is not None and p.lat is not None
    ]

    # Parallel speed array for gradient coloring — same filter as coordinates
    speeds = [
        round(p.speed_kmh, 2) if p.speed_kmh is not None else None
        for p in simplified
        if p.lon is not None and p.lat is not None
    ]

    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
        "properties": {
            "id": activity_id,
            "speeds": speeds,
            "simplification": "rdp",
            "rdp_epsilon": epsilon,
            "point_count_original": original_count or len(points),
            "point_count_simplified": len(coordinates),
        },
    }
