"""Compute aggregated metrics from a ParsedActivity.

All calculations are self-contained — no external state needed.
Uses inline haversine rather than geopy.geodesic to keep the hot path fast.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bincio.extract.models import DataPoint, ParsedActivity

# Standard MMP durations (seconds). Log-spaced so the curve looks good on a log-x axis.
MMP_DURATIONS_S = [1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 1200, 1800, 3600]

# Standard best-effort distances (km) per sport.
BEST_EFFORT_DISTANCES: dict[str, list[float]] = {
    "running": [0.4, 1.0, 1.609, 5.0, 10.0, 21.097, 42.195],
    "cycling": [5.0, 10.0, 20.0, 50.0, 100.0],
    "swimming": [0.1, 0.2, 0.5, 1.0, 2.0],
    "hiking":  [],   # no sliding-window records; aggregate from summaries only
    "walking": [],
    "skiing":  [],
    "other":   [],
}

# Speed below which we consider the athlete stopped (km/h)
_STOPPED_THRESHOLD_KMH = 1.0
_EARTH_R = 6_371_000.0  # metres


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. ~10x faster than geopy.geodesic."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi * 0.5) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam * 0.5) ** 2
    return 2.0 * _EARTH_R * math.asin(math.sqrt(min(a, 1.0)))


@dataclass
class ComputedMetrics:
    distance_m: Optional[float]
    duration_s: Optional[int]
    moving_time_s: Optional[int]
    elevation_gain_m: Optional[float]
    elevation_loss_m: Optional[float]
    avg_speed_kmh: Optional[float]
    max_speed_kmh: Optional[float]
    avg_hr_bpm: Optional[int]
    max_hr_bpm: Optional[int]
    avg_cadence_rpm: Optional[int]
    avg_power_w: Optional[int]
    max_power_w: Optional[int]
    bbox: Optional[tuple[float, float, float, float]]   # min_lon, min_lat, max_lon, max_lat
    start_latlng: Optional[tuple[float, float]]
    end_latlng: Optional[tuple[float, float]]
    mmp: Optional[list[list[int]]]  # [[duration_s, avg_watts], ...] — None if no power data
    # [[distance_km, time_s], ...] sorted by distance — None if sport has no distance targets
    best_efforts: Optional[list[list[float]]]
    best_climb_m: Optional[float]   # max net elevation gain in one contiguous window (cycling only)


def compute(activity: ParsedActivity) -> ComputedMetrics:
    pts = activity.points
    if not pts:
        return _empty()

    duration_s = _duration(pts)
    distance_m, moving_time_s, avg_speed_kmh, max_speed_kmh = _gps_stats(pts)
    gain, loss = _elevation(pts, activity.altitude_source)
    avg_hr, max_hr = _hr_stats(pts)
    avg_cad = _avg_nonnull([p.cadence_rpm for p in pts])
    avg_pow = _avg_nonnull([p.power_w for p in pts])
    max_pow = _max_nonnull([p.power_w for p in pts])
    bbox = _bbox(pts)
    start_ll, end_ll = _endpoints(pts)
    mmp = compute_mmp(pts, activity.started_at)
    best_efforts, best_climb_m = compute_best_efforts(pts, activity.started_at, activity.sport)

    return ComputedMetrics(
        distance_m=distance_m,
        duration_s=duration_s,
        moving_time_s=moving_time_s,
        elevation_gain_m=round(gain, 1) if gain is not None else None,
        elevation_loss_m=round(abs(loss), 1) if loss is not None else None,
        avg_speed_kmh=round(avg_speed_kmh, 2) if avg_speed_kmh is not None else None,
        max_speed_kmh=round(max_speed_kmh, 2) if max_speed_kmh is not None else None,
        avg_hr_bpm=avg_hr,
        max_hr_bpm=max_hr,
        avg_cadence_rpm=avg_cad,
        avg_power_w=avg_pow,
        max_power_w=max_pow,
        bbox=bbox,
        start_latlng=start_ll,
        end_latlng=end_ll,
        mmp=mmp,
        best_efforts=best_efforts,
        best_climb_m=best_climb_m,
    )


# ── mean maximal power ────────────────────────────────────────────────────────

def compute_mmp(pts: list[DataPoint], started_at: datetime) -> Optional[list[list[int]]]:
    """Compute Mean Maximal Power curve at the standard MMP_DURATIONS_S.

    Builds a 1 Hz power series (same downsampling as timeseries.py), then uses
    a O(n) sliding-window sum for each duration.  Returns a list of
    [duration_s, avg_watts] pairs (integers), or None when the activity has no
    power data.  Only durations shorter than the total activity are included.
    """
    # Build a dense 1 Hz power array with gaps zero-filled.
    # Zero-filling is the standard approach (matches GoldenCheetah / WKO):
    # a recording gap counts as 0 W so windows cannot silently span pauses
    # and inflate MMP values.
    sparse: dict[int, int] = {}
    last_t = -1
    for p in pts:
        t = int((p.timestamp - started_at).total_seconds())
        if t < 0 or t == last_t:
            continue
        last_t = t
        if p.power_w is not None:
            sparse[t] = p.power_w

    if len(sparse) < 2:
        return None

    t_min = min(sparse)
    t_max = max(sparse)
    # Guard against corrupted time data (e.g. absolute Unix timestamps stored as
    # elapsed offsets, which can make t_max astronomically large and OOM the process).
    if t_max - t_min > 7 * 24 * 3600:  # > 1 week → corrupted stream
        return None
    power_1hz: list[int] = [sparse.get(t, 0) for t in range(t_min, t_max + 1)]

    n = len(power_1hz)
    results: list[list[int]] = []

    for d in MMP_DURATIONS_S:
        if d > n:
            break  # activity shorter than this duration — stop (durations are sorted)

        # Sliding window of exactly d samples = d seconds at 1 Hz.
        window_sum = sum(power_1hz[:d])
        best = window_sum

        for i in range(1, n - d + 1):
            window_sum += power_1hz[i + d - 1] - power_1hz[i - 1]
            if window_sum > best:
                best = window_sum

        results.append([d, round(best / d)])

    return results if results else None


# ── best efforts & best climb ─────────────────────────────────────────────────

def compute_best_efforts(
    pts: list[DataPoint],
    started_at: datetime,
    sport: str,
) -> tuple[Optional[list[list[float]]], Optional[float]]:
    """Return (best_efforts, best_climb_m) for this activity.

    best_efforts: [[distance_km, time_s], ...] — one entry per target distance
                  where the activity was long enough to contain that effort.
    best_climb_m: maximum net elevation gain over any contiguous window (cycling).

    Both use the same 1 Hz downsampled series as the timeseries writer.
    """
    targets = BEST_EFFORT_DISTANCES.get(sport, [])

    # Build dense 1 Hz speed (km/h) and elevation (m) arrays with gap zero-filling.
    # Zero-filling speed gaps (0 km/h) prevents best-effort windows from spanning
    # recording pauses and producing artificially fast times.
    sparse_speed: dict[int, float] = {}
    sparse_ele: dict[int, Optional[float]] = {}
    last_t = -1
    for p in pts:
        t = int((p.timestamp - started_at).total_seconds())
        if t < 0 or t == last_t:
            continue
        last_t = t
        sparse_speed[t] = p.speed_kmh if p.speed_kmh is not None else 0.0
        sparse_ele[t] = p.elevation_m

    if not sparse_speed:
        return None, None

    t_min = min(sparse_speed)
    t_max = max(sparse_speed)
    # Guard against corrupted time data (e.g. absolute Unix timestamps stored as
    # elapsed offsets, which can make t_max astronomically large and OOM the process).
    if t_max - t_min > 7 * 24 * 3600:  # > 1 week → corrupted stream
        return None, None
    speed_1hz: list[float] = [sparse_speed.get(t, 0.0) for t in range(t_min, t_max + 1)]
    ele_1hz: list[Optional[float]] = [sparse_ele.get(t) for t in range(t_min, t_max + 1)]

    best_efforts: Optional[list[list[float]]] = None
    if targets and speed_1hz:
        results = []
        for d_km in targets:
            t_s = _fastest_time_for_distance(speed_1hz, d_km)
            if t_s is not None:
                results.append([d_km, t_s])
        best_efforts = results if results else None

    best_climb_m: Optional[float] = None
    if sport == "cycling":
        best_climb_m = _best_climb(ele_1hz)

    return best_efforts, best_climb_m


def _fastest_time_for_distance(speed_1hz: list[float], target_km: float) -> Optional[int]:
    """Minimum number of seconds to cover target_km using a two-pointer sliding window.

    Each sample contributes speed_kmh / 3600 km (one second at that speed).
    Nulls/zeros extend the window without adding distance — naturally deprioritised.
    """
    n = len(speed_1hz)
    left = 0
    window_dist = 0.0
    best_s: Optional[int] = None

    for right in range(n):
        window_dist += speed_1hz[right] / 3600.0

        # Shrink from the left while we still cover the target
        while window_dist >= target_km and left <= right:
            window_s = right - left + 1
            if best_s is None or window_s < best_s:
                best_s = window_s
            window_dist -= speed_1hz[left] / 3600.0
            left += 1

    return best_s


def _best_climb(ele_1hz: list[Optional[float]]) -> Optional[float]:
    """Maximum net elevation gain over any contiguous window (Kadane's on deltas).

    None samples are treated as breaks between segments — the Kadane window is
    reset to 0 at each gap so non-contiguous elevation data is never joined.
    Returns None if fewer than two non-None samples exist.
    """
    non_null = sum(1 for e in ele_1hz if e is not None)
    if non_null < 2:
        return None

    max_gain = 0.0
    current = 0.0
    prev: Optional[float] = None

    for e in ele_1hz:
        if e is None:
            # Gap — reset window so we don't bridge the discontinuity
            current = 0.0
            prev = None
            continue
        if prev is not None:
            current = max(0.0, current + (e - prev))
            if current > max_gain:
                max_gain = current
        prev = e

    return round(max_gain, 1) if max_gain > 0 else None


# ── single-pass GPS stats ──────────────────────────────────────────────────────
# distance, moving time, avg speed, and max speed are all derived from the same
# per-segment loop, so we compute them in one pass instead of four.

def _gps_stats(
    pts: list[DataPoint],
) -> tuple[Optional[float], Optional[int], Optional[float], Optional[float]]:
    """Return (distance_m, moving_time_s, avg_speed_kmh, max_speed_kmh)."""

    # Prefer device-recorded cumulative distance (FIT files always have this)
    device_dist = next(
        (p.distance_m for p in reversed(pts) if p.distance_m is not None), None
    )

    moving_s = 0
    moving_dist_m = 0.0
    total_dist_m = 0.0
    max_seg_kmh = 0.0
    has_data = False

    # Device speed values (used for max if present)
    device_max_kmh: Optional[float] = None
    if any(p.speed_kmh is not None for p in pts):
        device_max_kmh = max(p.speed_kmh for p in pts if p.speed_kmh is not None)

    for a, b in zip(pts, pts[1:]):
        dt = (b.timestamp - a.timestamp).total_seconds()
        if dt <= 0:
            continue

        if a.lat is not None and a.lon is not None and b.lat is not None and b.lon is not None:
            seg_m = _haversine_m(a.lat, a.lon, b.lat, b.lon)
            seg_kmh = (seg_m / dt) * 3.6
            has_data = True
        elif a.speed_kmh is not None:
            seg_kmh = a.speed_kmh
            seg_m = (seg_kmh / 3.6) * dt
            has_data = True
        else:
            continue

        total_dist_m += seg_m
        if seg_kmh > max_seg_kmh:
            max_seg_kmh = seg_kmh

        if seg_kmh >= _STOPPED_THRESHOLD_KMH:
            moving_s += int(dt)
            moving_dist_m += seg_m

    if not has_data:
        return device_dist, None, None, None

    # Fall back to haversine distance if device recorded 0 but we computed real GPS distance
    if device_dist is not None and device_dist > 0:
        distance_m = device_dist
    else:
        distance_m = round(total_dist_m, 1) if total_dist_m > 0 else device_dist
    moving_time_s = moving_s if moving_s > 0 else None
    avg_speed_kmh = (moving_dist_m / moving_s) * 3.6 if moving_s > 0 else None
    # Prefer device speed for max (more stable than GPS-derived per-second spikes)
    max_speed_kmh = device_max_kmh if device_max_kmh is not None else (
        max_seg_kmh if max_seg_kmh > 0 else None
    )

    return distance_m, moving_time_s, avg_speed_kmh, max_speed_kmh


# ── remaining helpers ──────────────────────────────────────────────────────────

def _duration(pts: list[DataPoint]) -> Optional[int]:
    if len(pts) < 2:
        return None
    return int((pts[-1].timestamp - pts[0].timestamp).total_seconds())


# Hysteresis thresholds per altitude source.
# Only commit a new elevation when it differs from the last committed value by
# at least this amount, filtering out GPS noise and barometric quantization steps.
_ELEVATION_THRESHOLD: dict[str, float] = {
    "barometric": 5.0,   # barometric altimeter: smaller steps are real
    "gps":        10.0,  # GPS altitude: noisier, needs wider dead-band
    "unknown":    10.0,  # treat unknown as GPS to be conservative
}


def _elevation(
    pts: list[DataPoint],
    altitude_source: str = "unknown",
) -> tuple[Optional[float], Optional[float]]:
    """Hysteresis-based elevation accumulation.

    Only commits a new elevation when it differs from the last committed value
    by at least the source-specific threshold, filtering GPS jitter and
    barometric quantization noise that would otherwise inflate the gain figure.
    """
    elevations = [p.elevation_m for p in pts if p.elevation_m is not None]
    if len(elevations) < 2:
        return None, None
    threshold = _ELEVATION_THRESHOLD.get(altitude_source, 10.0)
    gain = loss = 0.0
    committed = elevations[0]
    for e in elevations[1:]:
        diff = e - committed
        if abs(diff) >= threshold:
            if diff > 0:
                gain += diff
            else:
                loss += diff
            committed = e
    return gain, loss


def _hr_stats(pts: list[DataPoint]) -> tuple[Optional[int], Optional[int]]:
    hrs = [p.hr_bpm for p in pts if p.hr_bpm is not None]
    if not hrs:
        return None, None
    return int(sum(hrs) / len(hrs)), max(hrs)


def _avg_nonnull(values: list) -> Optional[int]:
    v = [x for x in values if x is not None]
    return int(sum(v) / len(v)) if v else None


def _max_nonnull(values: list) -> Optional[int]:
    v = [x for x in values if x is not None]
    return max(v) if v else None


def _bbox(pts: list[DataPoint]) -> Optional[tuple[float, float, float, float]]:
    lats = [p.lat for p in pts if p.lat is not None]
    lons = [p.lon for p in pts if p.lon is not None]
    if not lats:
        return None
    return (min(lons), min(lats), max(lons), max(lats))


def _endpoints(
    pts: list[DataPoint],
) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]]:
    gps = [(p.lat, p.lon) for p in pts if p.lat is not None and p.lon is not None]
    if not gps:
        return None, None
    return gps[0], gps[-1]


def _empty() -> ComputedMetrics:
    return ComputedMetrics(
        distance_m=None, duration_s=None, moving_time_s=None,
        elevation_gain_m=None, elevation_loss_m=None,
        avg_speed_kmh=None, max_speed_kmh=None,
        avg_hr_bpm=None, max_hr_bpm=None,
        avg_cadence_rpm=None, avg_power_w=None, max_power_w=None,
        bbox=None, start_latlng=None, end_latlng=None,
        mmp=None, best_efforts=None, best_climb_m=None,
    )
