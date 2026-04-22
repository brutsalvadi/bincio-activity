"""Tests for bincio.extract.dem — pure functions and file-level hysteresis.

No API calls, no extract pipeline, no large data.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from bincio.extract.dem import (
    _hysteresis_gain_loss,
    _median_filter,
    _moving_average,
    recalculate_elevation_hysteresis,
)


# ── _moving_average ───────────────────────────────────────────────────────────

def test_moving_average_flat():
    data = [5.0] * 20
    result = _moving_average(data, 5)
    assert result == pytest.approx(data)


def test_moving_average_ramp():
    # A perfect ramp should be preserved (MA of linear is linear).
    data = [float(i) for i in range(20)]
    result = _moving_average(data, 5)
    # Interior points should be exact; edges shrink the window so they may
    # differ slightly — just check the middle is right.
    for i in range(2, 18):
        assert result[i] == pytest.approx(data[i], abs=1e-9)


def test_moving_average_spike():
    # A single spike should be strongly attenuated.
    data = [100.0] * 60
    data[30] = 200.0  # +100 m spike
    result = _moving_average(data, 30)
    # At the spike position the average over 30 samples pulls it down a lot
    assert result[30] < 110.0


def test_moving_average_length_preserved():
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert len(_moving_average(data, 3)) == 5


def test_moving_average_single():
    assert _moving_average([42.0], 5) == [42.0]


# ── _median_filter ────────────────────────────────────────────────────────────

def test_median_filter_flat():
    data = [10.0] * 30
    assert _median_filter(data, 5) == pytest.approx(data)


def test_median_filter_spike_removed():
    data = [100.0] * 61
    data[30] = 300.0  # outlier spike
    result = _median_filter(data, 45)
    # The spike should be completely removed by the median
    assert result[30] == pytest.approx(100.0)


def test_median_filter_length_preserved():
    data = list(range(10, 20, 1))
    assert len(_median_filter([float(x) for x in data], 5)) == 10


# ── _hysteresis_gain_loss ─────────────────────────────────────────────────────

def test_hysteresis_flat():
    data = [100.0] * 100
    gain, loss = _hysteresis_gain_loss(data, 5.0)
    assert gain == 0.0
    assert loss == 0.0


def test_hysteresis_single_climb():
    # 50 m climb, well above any threshold.
    data = [0.0] * 50 + [50.0] * 50
    gain, loss = _hysteresis_gain_loss(data, 5.0)
    assert gain == pytest.approx(50.0)
    assert loss == pytest.approx(0.0)


def test_hysteresis_up_and_down():
    data = [0.0, 20.0, 0.0]
    gain, loss = _hysteresis_gain_loss(data, 5.0)
    assert gain == pytest.approx(20.0)
    assert loss == pytest.approx(20.0)


def test_hysteresis_noise_suppressed():
    # Oscillation below threshold → nothing accumulates.
    data = [100.0 + (3.0 if i % 2 == 0 else 0.0) for i in range(100)]
    gain, loss = _hysteresis_gain_loss(data, 5.0)
    assert gain == 0.0
    assert loss == 0.0


def test_hysteresis_noise_passes_low_threshold():
    # Same oscillation does accumulate with a threshold below it.
    data = [100.0 + (3.0 if i % 2 == 0 else 0.0) for i in range(100)]
    gain, loss = _hysteresis_gain_loss(data, 1.0)
    assert gain > 0.0


def test_hysteresis_both_positive():
    data = [0.0, 30.0, 10.0, 40.0]
    gain, loss = _hysteresis_gain_loss(data, 5.0)
    assert gain > 0.0
    assert loss > 0.0


# ── recalculate_elevation_hysteresis (file-level) ─────────────────────────────

def _write_activity(tmp_path: Path, activity_id: str, elevations: list[float],
                    altitude_source: str = "barometric",
                    with_original_backup: bool = False) -> Path:
    """Write minimal activity + timeseries JSON files for testing."""
    acts = tmp_path / "activities"
    acts.mkdir()

    detail = {
        "id": activity_id,
        "elevation_gain_m": 0.0,
        "elevation_loss_m": 0.0,
        "altitude_source": altitude_source,
    }
    (acts / f"{activity_id}.json").write_text(json.dumps(detail))

    ts: dict = {"t": list(range(len(elevations))), "elevation_m": elevations}
    if with_original_backup:
        ts["elevation_m_original"] = elevations
    (acts / f"{activity_id}.timeseries.json").write_text(json.dumps(ts))

    return tmp_path


def test_hysteresis_recalc_barometric(tmp_path):
    # Long ramp (1800 s = 30 min, +1 m/s) so the 30s MA edge effect is small.
    # Edge effect ≈ window/2 metres on each side = ~15 m total on 1800 m climb.
    elevations = [float(i) for i in range(1801)]  # 0→1800 m
    _write_activity(tmp_path, "test-act", elevations, altitude_source="barometric")

    result = recalculate_elevation_hysteresis(tmp_path, "test-act")

    assert result["altitude_source"] == "barometric"
    assert result["threshold_m"] == pytest.approx(1.0)
    # Edge effect is ≤1% on a 30-min ramp
    assert result["elevation_gain_m"] == pytest.approx(1800.0, rel=0.02)
    assert result["elevation_loss_m"] == pytest.approx(0.0, abs=1.0)


def test_hysteresis_recalc_gps(tmp_path):
    elevations = [float(i) for i in range(1801)]
    _write_activity(tmp_path, "test-act", elevations, altitude_source="gps")

    result = recalculate_elevation_hysteresis(tmp_path, "test-act")

    assert result["threshold_m"] == pytest.approx(3.0)
    assert result["elevation_gain_m"] == pytest.approx(1800.0, rel=0.02)


def test_hysteresis_recalc_uses_original_backup(tmp_path):
    # Simulate: DEM already replaced elevation_m with flat terrain,
    # but elevation_m_original holds the real barometric climb.
    acts = tmp_path / "activities"
    acts.mkdir()
    aid = "test-act"

    original = [float(i) for i in range(1801)]  # real 1800 m climb
    dem_flat  = [900.0] * 1801                   # DEM said flat

    detail = {"id": aid, "elevation_gain_m": 0.0, "elevation_loss_m": 0.0,
              "altitude_source": "barometric"}
    (acts / f"{aid}.json").write_text(json.dumps(detail))

    ts = {"t": list(range(1801)), "elevation_m": dem_flat,
          "elevation_m_original": original}
    (acts / f"{aid}.timeseries.json").write_text(json.dumps(ts))

    result = recalculate_elevation_hysteresis(tmp_path, aid)

    # Should use the original backup (1800 m climb), not the flat DEM array (0 m)
    assert result["elevation_gain_m"] == pytest.approx(1800.0, rel=0.02)


def test_hysteresis_recalc_patches_detail_json(tmp_path):
    elevations = [float(i) for i in range(101)]
    _write_activity(tmp_path, "test-act", elevations)

    recalculate_elevation_hysteresis(tmp_path, "test-act")

    detail = json.loads((tmp_path / "activities" / "test-act.json").read_text())
    assert "elevation_gain_m" in detail
    assert detail["elevation_gain_m"] > 0


def test_hysteresis_recalc_patches_index(tmp_path):
    elevations = [float(i) for i in range(101)]
    _write_activity(tmp_path, "test-act", elevations)

    index = {"activities": [{"id": "test-act", "elevation_gain_m": 0.0}]}
    (tmp_path / "index.json").write_text(json.dumps(index))

    recalculate_elevation_hysteresis(tmp_path, "test-act")

    updated = json.loads((tmp_path / "index.json").read_text())
    assert updated["activities"][0]["elevation_gain_m"] > 0


def test_hysteresis_recalc_missing_activity(tmp_path):
    (tmp_path / "activities").mkdir()
    with pytest.raises(FileNotFoundError):
        recalculate_elevation_hysteresis(tmp_path, "nonexistent")


def test_hysteresis_recalc_no_timeseries(tmp_path):
    acts = tmp_path / "activities"
    acts.mkdir()
    (acts / "test-act.json").write_text(json.dumps({"id": "test-act"}))
    with pytest.raises(ValueError, match="timeseries"):
        recalculate_elevation_hysteresis(tmp_path, "test-act")
