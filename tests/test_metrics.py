"""Tests for bincio.extract.metrics."""

import math
from datetime import datetime, timezone

import pytest

from bincio.extract.metrics import (
    MMP_DURATIONS_S,
    _best_climb,
    _elevation,
    _fastest_time_for_distance,
    _haversine_m,
    compute,
    compute_best_efforts,
    compute_mmp,
)
from bincio.extract.models import DataPoint, ParsedActivity


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(offset_s: int) -> datetime:
    from datetime import timedelta
    return datetime(2024, 6, 1, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _pt(offset_s: int, **kw) -> DataPoint:
    return DataPoint(timestamp=_ts(offset_s), **kw)


def _activity(points: list[DataPoint], sport: str = "cycling") -> ParsedActivity:
    return ParsedActivity(
        points=points,
        sport=sport,
        started_at=_ts(0),
        source_file="test.fit",
        source_hash="sha256:abc",
    )


# ── haversine ─────────────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine_m(48.0, 11.0, 48.0, 11.0) == 0.0


def test_haversine_known_distance():
    # London (51.5074, -0.1278) to Paris (48.8566, 2.3522) ≈ 343 km
    d = _haversine_m(51.5074, -0.1278, 48.8566, 2.3522)
    assert 340_000 < d < 347_000


def test_haversine_symmetry():
    a = _haversine_m(48.0, 11.0, 48.1, 11.1)
    b = _haversine_m(48.1, 11.1, 48.0, 11.0)
    assert abs(a - b) < 1e-6


def test_haversine_short_segment():
    # ~111 m per 0.001 degrees latitude at equator
    d = _haversine_m(0.0, 0.0, 0.001, 0.0)
    assert 110 < d < 112


# ── compute() ─────────────────────────────────────────────────────────────────

def test_compute_empty_activity():
    m = compute(_activity([]))
    assert m.distance_m is None
    assert m.duration_s is None
    assert m.elevation_gain_m is None


def test_compute_duration():
    pts = [_pt(0, lat=48.0, lon=11.0), _pt(3600, lat=48.1, lon=11.1)]
    m = compute(_activity(pts))
    assert m.duration_s == 3600


def test_compute_gps_distance():
    # Two points ~111 m apart (0.001° lat), 10 s apart
    pts = [_pt(0, lat=48.0, lon=11.0), _pt(10, lat=48.001, lon=11.0)]
    m = compute(_activity(pts))
    assert m.distance_m is not None
    assert 100 < m.distance_m < 120


def test_compute_device_distance_preferred():
    # Device reports a different cumulative distance — it should be used.
    pts = [
        _pt(0, lat=48.0, lon=11.0, distance_m=0.0),
        _pt(10, lat=48.001, lon=11.0, distance_m=500.0),
    ]
    m = compute(_activity(pts))
    assert m.distance_m == 500.0


def test_compute_moving_time_excludes_stops():
    # Three segments: moving, stopped, moving
    pts = [
        _pt(0,   lat=48.0,   lon=11.0),
        _pt(10,  lat=48.001, lon=11.0),   # ~111 m in 10 s → moving
        _pt(70,  lat=48.001, lon=11.0),   # 0 m in 60 s → stopped
        _pt(80,  lat=48.002, lon=11.0),   # ~111 m in 10 s → moving
    ]
    m = compute(_activity(pts))
    assert m.moving_time_s is not None
    assert m.moving_time_s < m.duration_s  # stopped time excluded


def test_compute_elevation_gain():
    pts = [
        _pt(0,  lat=48.0, lon=11.0, elevation_m=100.0),
        _pt(10, lat=48.001, lon=11.0, elevation_m=150.0),
        _pt(20, lat=48.002, lon=11.0, elevation_m=120.0),
    ]
    m = compute(_activity(pts))
    assert m.elevation_gain_m == 50.0
    assert m.elevation_loss_m == 30.0


def test_compute_no_elevation():
    pts = [_pt(0, lat=48.0, lon=11.0), _pt(10, lat=48.001, lon=11.0)]
    m = compute(_activity(pts))
    assert m.elevation_gain_m is None
    assert m.elevation_loss_m is None


# ── elevation hysteresis ──────────────────────────────────────────────────────

def _ele_pts(elevations: list[float]) -> list[DataPoint]:
    return [_pt(i, elevation_m=e) for i, e in enumerate(elevations)]


def test_elevation_hysteresis_large_step_always_counted():
    # A single 50m step is way above any threshold — both sources should count it.
    pts = _ele_pts([100.0, 150.0])
    gain_baro, _ = _elevation(pts, "barometric")
    gain_gps,  _ = _elevation(pts, "gps")
    assert gain_baro == 50.0
    assert gain_gps  == 50.0


def test_elevation_hysteresis_flat_gps_noise_suppressed():
    # Flat coastal route: 16m of GPS noise oscillating within ±8m.
    # All steps are sub-1m — hysteresis should return ~0 gain.
    import math
    n = 1000
    elevations = [100.0 + 3.0 * math.sin(i * 0.1) for i in range(n)]
    pts = _ele_pts(elevations)
    gain, loss = _elevation(pts, "gps")
    # With threshold=10m no oscillation within ±3m should ever commit.
    assert gain == 0.0
    assert loss == 0.0


def test_elevation_hysteresis_barometric_threshold_lower():
    # Steps of exactly 7m — above barometric (5m) but below GPS (10m) threshold.
    elevations = [0.0, 7.0, 0.0, 7.0]
    pts = _ele_pts(elevations)
    gain_baro, _ = _elevation(pts, "barometric")
    gain_gps,  _ = _elevation(pts, "gps")
    assert gain_baro == 14.0   # both 7m steps committed
    assert gain_gps  == 0.0    # 7m < 10m threshold → suppressed


def test_elevation_hysteresis_real_climb_approximated():
    # Simulate a 200m climb with 0.2m barometric quantization noise.
    # Build a staircase: 1000 steps, mostly 0.2m up/down noise, with a 200m net climb.
    import random
    random.seed(42)
    elevations = [0.0]
    for i in range(999):
        # Mostly quantization noise, but drift upward at 0.2 m/step net
        step = random.choice([-0.2, 0.0, 0.0, 0.2, 0.2, 0.4])
        elevations.append(elevations[-1] + step)

    # Force net gain ~200m by scaling
    scale = 200.0 / (elevations[-1] - elevations[0]) if elevations[-1] != elevations[0] else 1
    elevations = [e * scale for e in elevations]

    pts = _ele_pts(elevations)
    gain, _ = _elevation(pts, "barometric")
    # Hysteresis should produce substantially less than naive accumulation
    # and land reasonably close to the 200m net climb.
    assert gain is not None
    assert gain < 500.0   # not inflated like naive sum
    assert gain > 100.0   # not zero either — real climbing exists


def test_elevation_hysteresis_unknown_treated_as_gps():
    # "unknown" should apply the same 10m threshold as "gps"
    elevations = [0.0, 7.0, 0.0, 7.0]  # 7m steps
    pts = _ele_pts(elevations)
    gain_unknown, _ = _elevation(pts, "unknown")
    gain_gps,     _ = _elevation(pts, "gps")
    assert gain_unknown == gain_gps


def test_compute_hr_stats():
    pts = [
        _pt(0,  lat=48.0, lon=11.0, hr_bpm=120),
        _pt(10, lat=48.001, lon=11.0, hr_bpm=160),
        _pt(20, lat=48.002, lon=11.0, hr_bpm=140),
    ]
    m = compute(_activity(pts))
    assert m.avg_hr_bpm == 140
    assert m.max_hr_bpm == 160


def test_compute_hr_null_points_ignored():
    pts = [
        _pt(0,  lat=48.0, lon=11.0, hr_bpm=None),
        _pt(10, lat=48.001, lon=11.0, hr_bpm=150),
    ]
    m = compute(_activity(pts))
    assert m.avg_hr_bpm == 150
    assert m.max_hr_bpm == 150


def test_compute_no_hr():
    pts = [_pt(0, lat=48.0, lon=11.0), _pt(10, lat=48.001, lon=11.0)]
    m = compute(_activity(pts))
    assert m.avg_hr_bpm is None
    assert m.max_hr_bpm is None


def test_compute_bbox():
    pts = [
        _pt(0,  lat=48.0, lon=11.0),
        _pt(10, lat=48.5, lon=11.8),
        _pt(20, lat=48.2, lon=11.3),
    ]
    m = compute(_activity(pts))
    assert m.bbox == (11.0, 48.0, 11.8, 48.5)   # min_lon, min_lat, max_lon, max_lat


def test_compute_start_end_latlng():
    pts = [
        _pt(0,  lat=48.0, lon=11.0),
        _pt(10, lat=48.5, lon=11.8),
    ]
    m = compute(_activity(pts))
    assert m.start_latlng == (48.0, 11.0)
    assert m.end_latlng == (48.5, 11.8)


def test_compute_power_stats():
    pts = [
        _pt(0,  lat=48.0, lon=11.0, power_w=200),
        _pt(1,  lat=48.0, lon=11.0, power_w=300),
        _pt(2,  lat=48.0, lon=11.0, power_w=250),
    ]
    m = compute(_activity(pts))
    assert m.avg_power_w == 250
    assert m.max_power_w == 300


# ── MMP ───────────────────────────────────────────────────────────────────────

def test_mmp_no_power():
    pts = [_pt(i, lat=48.0, lon=11.0) for i in range(10)]
    m = compute_mmp(pts, _ts(0))
    assert m is None


def test_mmp_constant_power():
    # 60 s at 200 W → 1 s MMP = 200, 5 s MMP = 200, 30 s MMP = 200, 60 s MMP = 200
    pts = [_pt(i, power_w=200) for i in range(61)]
    result = compute_mmp(pts, _ts(0))
    assert result is not None
    by_dur = {d: w for d, w in result}
    assert by_dur[1]  == 200
    assert by_dur[5]  == 200
    assert by_dur[30] == 200
    assert by_dur[60] == 200


def test_mmp_peak_window():
    # 120 s total: first 60 s at 100 W, last 60 s at 300 W
    pts = [_pt(i, power_w=100) for i in range(60)]
    pts += [_pt(i, power_w=300) for i in range(60, 121)]
    result = compute_mmp(pts, _ts(0))
    assert result is not None
    by_dur = {d: w for d, w in result}
    # 1 s MMP should be 300 (last segment)
    assert by_dur[1] == 300
    # 60 s MMP: best 60-second window is the last 60 s at 300 W
    assert by_dur[60] == 300


def test_mmp_activity_shorter_than_all_durations():
    # Only 5 seconds of data
    pts = [_pt(i, power_w=200) for i in range(6)]
    result = compute_mmp(pts, _ts(0))
    assert result is not None
    durations = [d for d, _ in result]
    # Should only include durations ≤ 5 s
    assert all(d <= 5 for d in durations)
    assert 60 not in durations


# ── best efforts ─────────────────────────────────────────────────────────────

def test_fastest_time_for_distance_exact():
    # 36 km/h for 100 s = 1 km exactly (36/3600 * 100 = 1.0 with no fp issues)
    speed_1hz = [36.0] * 100
    t = _fastest_time_for_distance(speed_1hz, 1.0)
    assert t is not None
    assert t <= 100


def test_fastest_time_for_distance_target_not_reached():
    # Only 0.5 km of data at 10 km/h
    speed_1hz = [10.0] * 180
    t = _fastest_time_for_distance(speed_1hz, 1.0)
    assert t is None


def test_fastest_time_picks_fastest_window():
    # First 200 s at 1 km/h (barely moving), then 100 s at 36 km/h (= 1 km)
    speed_1hz = [1.0] * 200 + [36.0] * 100
    t = _fastest_time_for_distance(speed_1hz, 1.0)
    # The fast window can cover 1 km; the slow window alone cannot.
    # Algorithm uses inclusive right-left+1 counting so result may be 100 or 101.
    assert t is not None
    assert t <= 101


def test_best_efforts_running():
    # 15 km/h for 3600 s = 15 km — should cover 1 km, 5 km, 10 km targets
    pts = [_pt(i, lat=48.0 + i * 0.0001, lon=11.0, speed_kmh=15.0) for i in range(3601)]
    efforts, _ = compute_best_efforts(pts, _ts(0), "running")
    assert efforts is not None
    covered = [d for d, _ in efforts]
    assert 1.0 in covered
    assert 5.0 in covered
    assert 10.0 in covered
    # 42.195 km not reachable in 3600 s at 15 km/h
    assert 42.195 not in covered


def test_best_efforts_no_targets_for_sport():
    pts = [_pt(i, lat=48.0, lon=11.0) for i in range(100)]
    efforts, _ = compute_best_efforts(pts, _ts(0), "hiking")
    assert efforts is None


# ── best climb ────────────────────────────────────────────────────────────────

def test_best_climb_simple_ascent():
    # 0 → 100 m with no gaps
    ele = [float(i) for i in range(101)]
    result = _best_climb(ele)
    assert result == 100.0


def test_best_climb_with_descent():
    # Up 50, down 20, up 80 → best contiguous window = 80
    ele = list(range(0, 51)) + list(range(50, 30, -1)) + list(range(30, 111))
    result = _best_climb(ele)
    assert result is not None
    assert result >= 80.0


def test_best_climb_none_gap_resets_window():
    # 50 m up, then a GPS gap, then 30 m up — windows don't bridge the gap
    ele: list = list(range(0, 51)) + [None] + list(range(0, 31))
    result = _best_climb(ele)
    assert result == 50.0


def test_best_climb_only_descent():
    ele = [100.0, 80.0, 60.0, 40.0]
    result = _best_climb(ele)
    assert result is None


def test_best_climb_too_few_samples():
    assert _best_climb([]) is None
    assert _best_climb([100.0]) is None
