import json
from pathlib import Path

import pytest

from bincio.extract.writer import (
    make_activity_id,
    build_summary,
    write_activity,
    finalize_pending,
    cleanup_pending,
    _slugify,
)
from bincio.extract.metrics import ComputedMetrics
from bincio.extract.models import ParsedActivity, DataPoint
from datetime import datetime, timezone


def _dummy_activity(title=None):
    ts = datetime(2024, 6, 1, 7, 30, 12, tzinfo=timezone.utc)
    return ParsedActivity(
        points=[DataPoint(timestamp=ts)],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:abc",
        title=title,
    )


def test_id_with_title():
    act = _dummy_activity("Morning Ride")
    aid = make_activity_id(act)
    assert aid == "2024-06-01T073012Z-morning-ride"


def test_id_without_title():
    act = _dummy_activity()
    aid = make_activity_id(act)
    assert aid == "2024-06-01T073012Z"


def test_slugify():
    assert _slugify("Morning Ride!") == "morning-ride"
    assert _slugify("  Vélo  ") == "velo"   # é → e via NFKD + ASCII
    assert _slugify("") == ""


def test_id_utc_conversion():
    """Non-UTC timestamps should be converted to UTC in the ID."""
    from datetime import timedelta
    tz_plus2 = timezone(timedelta(hours=2))
    ts = datetime(2024, 6, 1, 9, 30, 12, tzinfo=tz_plus2)  # 07:30:12 UTC
    act = ParsedActivity(
        points=[DataPoint(timestamp=ts)],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:abc",
    )
    assert make_activity_id(act) == "2024-06-01T073012Z"


def _dummy_metrics(**overrides):
    defaults = dict(
        distance_m=10000.0, duration_s=3600, moving_time_s=3500,
        elevation_gain_m=100.0, elevation_loss_m=95.0,
        avg_speed_kmh=10.0, max_speed_kmh=20.0,
        avg_hr_bpm=None, max_hr_bpm=None,
        avg_cadence_rpm=None, avg_power_w=None, max_power_w=None,
        bbox=None, start_latlng=None, end_latlng=None,
        mmp=None, best_efforts=None, best_climb_m=None,
    )
    defaults.update(overrides)
    return ComputedMetrics(**defaults)


# ── write_activity (timeseries split) ────────────────────────────────────────

def test_write_activity_creates_timeseries_file(tmp_path):
    """write_activity should produce a separate .timeseries.json and
    set timeseries_url in the detail JSON (no embedded timeseries)."""
    ts = datetime(2024, 6, 1, 7, 30, 12, tzinfo=timezone.utc)
    # Activity with one data point so timeseries is non-null
    act = ParsedActivity(
        points=[DataPoint(timestamp=ts, lat=45.0, lon=7.0, elevation_m=300.0)],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:" + "a" * 56,
    )
    metrics = _dummy_metrics()
    activity_id = write_activity(act, metrics, tmp_path)

    detail_path = tmp_path / "activities" / f"{activity_id}.json"
    ts_path = tmp_path / "activities" / f"{activity_id}.timeseries.json"

    assert detail_path.exists(), "detail JSON not created"
    assert ts_path.exists(), "timeseries JSON not created"

    detail = json.loads(detail_path.read_text())
    assert "timeseries" not in detail, "timeseries must NOT be embedded in detail"
    assert detail["timeseries_url"] == f"activities/{activity_id}.timeseries.json"

    ts_data = json.loads(ts_path.read_text())
    assert "t" in ts_data, "timeseries file must have 't' array"


def test_write_activity_no_points_no_timeseries_file(tmp_path):
    """An activity with no data points should produce no timeseries file
    and timeseries_url should be None."""
    ts = datetime(2024, 6, 1, 7, 30, 12, tzinfo=timezone.utc)
    act = ParsedActivity(
        points=[],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:" + "b" * 56,
    )
    metrics = _dummy_metrics()
    activity_id = write_activity(act, metrics, tmp_path)

    detail = json.loads((tmp_path / "activities" / f"{activity_id}.json").read_text())
    ts_path = tmp_path / "activities" / f"{activity_id}.timeseries.json"

    assert detail["timeseries_url"] is None
    assert not ts_path.exists()


def test_write_activity_pending_creates_pending_timeseries(tmp_path):
    """pending=True should create .pending.timeseries.json alongside .pending.json."""
    ts = datetime(2024, 6, 1, 7, 30, 12, tzinfo=timezone.utc)
    act = ParsedActivity(
        points=[DataPoint(timestamp=ts, lat=45.0, lon=7.0)],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:" + "c" * 56,
    )
    metrics = _dummy_metrics()
    activity_id = write_activity(act, metrics, tmp_path, pending=True)

    acts_dir = tmp_path / "activities"
    tag = "c" * 8
    assert (acts_dir / f"{activity_id}.{tag}.pending.json").exists()
    assert (acts_dir / f"{activity_id}.{tag}.pending.timeseries.json").exists()


def test_finalize_pending_promotes_timeseries(tmp_path):
    """finalize_pending should rename the pending timeseries file to its final path."""
    ts = datetime(2024, 6, 1, 7, 30, 12, tzinfo=timezone.utc)
    act = ParsedActivity(
        points=[DataPoint(timestamp=ts, lat=45.0, lon=7.0)],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:" + "d" * 56,
    )
    metrics = _dummy_metrics()
    activity_id = write_activity(act, metrics, tmp_path, pending=True)
    source_hash = "sha256:" + "d" * 56

    final_id = finalize_pending(tmp_path, activity_id, source_hash)

    acts_dir = tmp_path / "activities"
    assert (acts_dir / f"{final_id}.json").exists()
    assert (acts_dir / f"{final_id}.timeseries.json").exists()

    # Pending files must be gone
    tag = "d" * 8
    assert not (acts_dir / f"{activity_id}.{tag}.pending.timeseries.json").exists()


def test_cleanup_pending_removes_timeseries(tmp_path):
    """cleanup_pending should remove the pending timeseries file."""
    ts = datetime(2024, 6, 1, 7, 30, 12, tzinfo=timezone.utc)
    act = ParsedActivity(
        points=[DataPoint(timestamp=ts, lat=45.0, lon=7.0)],
        sport="cycling",
        started_at=ts,
        source_file="test.fit",
        source_hash="sha256:" + "e" * 56,
    )
    metrics = _dummy_metrics()
    activity_id = write_activity(act, metrics, tmp_path, pending=True)
    source_hash = "sha256:" + "e" * 56

    cleanup_pending(tmp_path, activity_id, source_hash)

    tag = "e" * 8
    acts_dir = tmp_path / "activities"
    assert not (acts_dir / f"{activity_id}.{tag}.pending.json").exists()
    assert not (acts_dir / f"{activity_id}.{tag}.pending.timeseries.json").exists()


def test_build_summary_required_fields():
    """build_summary should include all fields needed by the schema."""
    act = _dummy_activity("Test Ride")
    metrics = ComputedMetrics(
        distance_m=10000.0,
        duration_s=3600,
        moving_time_s=3500,
        elevation_gain_m=100.0,
        elevation_loss_m=95.0,
        avg_speed_kmh=10.0,
        max_speed_kmh=20.0,
        avg_hr_bpm=None,
        max_hr_bpm=None,
        avg_cadence_rpm=None,
        avg_power_w=None,
        max_power_w=None,
        bbox=None,
        start_latlng=None,
        end_latlng=None,
        mmp=None,
        best_efforts=None,
        best_climb_m=None,
    )
    summary = build_summary(act, metrics, "2024-06-01T073012Z-test-ride")
    # Required fields per schema
    assert summary["id"] == "2024-06-01T073012Z-test-ride"
    assert summary["title"] == "Test Ride"
    assert summary["sport"] == "cycling"
    assert "started_at" in summary
    assert "privacy" in summary
    assert "detail_url" in summary
    assert "track_url" in summary
