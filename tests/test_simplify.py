"""Tests for bincio.extract.simplify."""

from datetime import datetime, timezone

import pytest

from bincio.extract.models import DataPoint
from bincio.extract.simplify import (
    _rdp_mask,
    build_geojson,
    preview_coords,
    simplify_track,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(i: int = 0) -> datetime:
    from datetime import timedelta
    return datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=i)


def _pt(lat: float, lon: float, ele: float | None = None, spd: float | None = None, i: int = 0) -> DataPoint:
    return DataPoint(timestamp=_ts(i), lat=lat, lon=lon, elevation_m=ele, speed_kmh=spd)


def _pts_no_gps(n: int) -> list[DataPoint]:
    return [DataPoint(timestamp=_ts(i)) for i in range(n)]


# ── _rdp_mask ─────────────────────────────────────────────────────────────────

def test_rdp_mask_collinear_removes_middle():
    # Three collinear points — the middle one should be dropped
    coords = [[0.0, 0.0], [0.5, 0.0], [1.0, 0.0]]
    mask = _rdp_mask(coords, epsilon=0.001)
    assert mask[0] is True
    assert mask[1] is False   # middle collinear point removed
    assert mask[2] is True


def test_rdp_mask_always_keeps_endpoints():
    coords = [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]]
    mask = _rdp_mask(coords, epsilon=0.001)
    assert mask[0] is True
    assert mask[-1] is True


def test_rdp_mask_large_deviation_kept():
    # Middle point is far off the line — must be kept
    coords = [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]]
    mask = _rdp_mask(coords, epsilon=0.001)
    assert mask[1] is True


def test_rdp_mask_single_point():
    mask = _rdp_mask([[0.0, 0.0]], epsilon=0.001)
    assert mask == [True]


def test_rdp_mask_two_points():
    mask = _rdp_mask([[0.0, 0.0], [1.0, 1.0]], epsilon=0.001)
    assert mask == [True, True]


def test_rdp_mask_epsilon_zero_keeps_all():
    coords = [[float(i), 0.0] for i in range(5)]
    mask = _rdp_mask(coords, epsilon=0.0)
    assert all(mask)


# ── simplify_track ────────────────────────────────────────────────────────────

def test_simplify_track_collinear_removes_interior():
    # Straight line — only endpoints should survive with epsilon > 0
    pts = [_pt(48.0 + i * 0.001, 11.0, i=i) for i in range(5)]
    result = simplify_track(pts, epsilon=0.0001)
    # Endpoints always kept; interior collinear points dropped
    assert result[0].lat == pytest.approx(48.0)
    assert result[-1].lat == pytest.approx(48.004)
    assert len(result) < len(pts)


def test_simplify_track_corner_kept():
    # L-shaped route — the corner must survive
    pts = [
        _pt(48.000, 11.000, i=0),
        _pt(48.001, 11.000, i=1),  # going north
        _pt(48.002, 11.000, i=2),
        _pt(48.002, 11.001, i=3),  # turn east — this is the corner
        _pt(48.002, 11.002, i=4),
    ]
    result = simplify_track(pts, epsilon=0.0001)
    latlons = [(p.lat, p.lon) for p in result]
    assert (48.002, 11.000) in latlons   # corner kept


def test_simplify_track_no_gps_points():
    pts = _pts_no_gps(10)
    result = simplify_track(pts)
    assert result == []


def test_simplify_track_single_gps_point():
    pts = [_pt(48.0, 11.0)]
    result = simplify_track(pts)
    assert len(result) == 1


def test_simplify_track_preserves_data_point_fields():
    pts = [
        _pt(48.0, 11.0, ele=100.0, spd=20.0, i=0),
        _pt(48.0, 12.0, ele=200.0, spd=30.0, i=1),
    ]
    result = simplify_track(pts)
    assert result[0].elevation_m == 100.0
    assert result[0].speed_kmh == 20.0


# ── preview_coords ────────────────────────────────────────────────────────────

def test_preview_coords_none_on_no_gps():
    result = preview_coords(_pts_no_gps(10))
    assert result is None


def test_preview_coords_single_point_none():
    result = preview_coords([_pt(48.0, 11.0)])
    assert result is None


def test_preview_coords_respects_max_points():
    pts = [_pt(48.0 + i * 0.001, 11.0, i=i) for i in range(100)]
    result = preview_coords(pts, max_points=10)
    assert result is not None
    assert len(result) <= 10


def test_preview_coords_format():
    pts = [_pt(48.123456789, 11.987654321, i=0), _pt(48.2, 12.0, i=1)]
    result = preview_coords(pts)
    assert result is not None
    for coord in result:
        assert len(coord) == 2
        # Rounded to 5 decimal places
        assert coord[0] == round(coord[0], 5)
        assert coord[1] == round(coord[1], 5)


def test_preview_coords_few_points_returned_all():
    pts = [_pt(48.0, 11.0, i=0), _pt(48.1, 11.1, i=1), _pt(48.2, 11.2, i=2)]
    result = preview_coords(pts, max_points=20)
    assert result is not None
    assert len(result) >= 2


# ── build_geojson ─────────────────────────────────────────────────────────────

def test_build_geojson_structure():
    pts = [_pt(48.0, 11.0, ele=100.0, spd=20.0, i=i) for i in range(3)]
    gj = build_geojson(pts, activity_id="test-123")
    assert gj["type"] == "Feature"
    assert gj["geometry"]["type"] == "LineString"
    assert "coordinates" in gj["geometry"]
    assert "properties" in gj
    props = gj["properties"]
    assert props["id"] == "test-123"
    assert props["simplification"] == "rdp"
    assert "speeds" in props
    assert "point_count_simplified" in props


def test_build_geojson_coordinates_order():
    # GeoJSON uses [lon, lat, ele]
    pts = [_pt(48.0, 11.0, ele=100.0, i=0), _pt(48.5, 11.5, ele=200.0, i=1)]
    gj = build_geojson(pts, "act")
    coords = gj["geometry"]["coordinates"]
    assert len(coords) == 2
    # First coord: lon=11.0, lat=48.0, ele=100.0
    assert coords[0] == [11.0, 48.0, 100.0]


def test_build_geojson_no_elevation_omits_z():
    pts = [_pt(48.0, 11.0, ele=None, i=0), _pt(48.5, 11.5, ele=None, i=1)]
    gj = build_geojson(pts, "act")
    coords = gj["geometry"]["coordinates"]
    for c in coords:
        assert len(c) == 2   # no Z


def test_build_geojson_speeds_parallel():
    pts = [
        _pt(48.0, 11.0, spd=10.0, i=0),
        _pt(48.5, 11.5, spd=None, i=1),
        _pt(49.0, 12.0, spd=20.0, i=2),
    ]
    gj = build_geojson(pts, "act")
    speeds = gj["properties"]["speeds"]
    coords = gj["geometry"]["coordinates"]
    assert len(speeds) == len(coords)


def test_build_geojson_point_counts():
    pts = [_pt(48.0 + i * 0.001, 11.0, i=i) for i in range(10)]
    gj = build_geojson(pts, "act", original_count=100)
    assert gj["properties"]["point_count_original"] == 100
    assert gj["properties"]["point_count_simplified"] <= 10


def test_build_geojson_no_gps_points():
    gj = build_geojson(_pts_no_gps(5), "act")
    assert gj["geometry"]["coordinates"] == []
