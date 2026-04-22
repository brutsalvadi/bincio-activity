"""API tests for the /recalculate-elevation/* endpoints in bincio.edit.server.

Uses httpx TestClient — no real network, no uvicorn process.
The module-level `data_dir` variable is patched to a tmp_path fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bincio.edit.server as edit_server
from bincio.edit.server import app

CLIENT = TestClient(app, raise_server_exceptions=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_activity(
    data_dir: Path,
    activity_id: str,
    elevations: list[float],
    altitude_source: str = "barometric",
    elevation_m_original: list[float] | None = None,
) -> None:
    acts = data_dir / "activities"
    acts.mkdir(exist_ok=True)

    detail = {
        "id": activity_id,
        "elevation_gain_m": 0.0,
        "elevation_loss_m": 0.0,
        "altitude_source": altitude_source,
    }
    (acts / f"{activity_id}.json").write_text(json.dumps(detail))

    ts: dict = {"t": list(range(len(elevations))), "elevation_m": elevations}
    if elevation_m_original is not None:
        ts["elevation_m_original"] = elevation_m_original
    (acts / f"{activity_id}.timeseries.json").write_text(json.dumps(ts))

    # Minimal index.json so merge_one doesn't crash
    index_path = data_dir / "index.json"
    if not index_path.exists():
        index_path.write_text(json.dumps({"activities": [
            {"id": activity_id, "elevation_gain_m": 0.0}
        ]}))


@pytest.fixture(autouse=True)
def patch_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_server, "data_dir", tmp_path)
    return tmp_path


# ── /recalculate-elevation/hysteresis ─────────────────────────────────────────

class TestHysteresisEndpoint:
    AID = "2024-01-01T080000Z-test-climb"

    def test_returns_200_with_gain_loss(self, tmp_path):
        elevations = [float(i) for i in range(1801)]
        _make_activity(tmp_path, self.AID, elevations, altitude_source="barometric")

        r = CLIENT.post(f"/api/activity/{self.AID}/recalculate-elevation/hysteresis")

        assert r.status_code == 200
        body = r.json()
        assert "elevation_gain_m" in body
        assert "elevation_loss_m" in body
        assert body["elevation_gain_m"] > 0
        assert body["altitude_source"] == "barometric"
        assert body["threshold_m"] == pytest.approx(1.0)

    def test_gps_source_uses_3m_threshold(self, tmp_path):
        elevations = [float(i) for i in range(1801)]
        _make_activity(tmp_path, self.AID, elevations, altitude_source="gps")

        r = CLIENT.post(f"/api/activity/{self.AID}/recalculate-elevation/hysteresis")

        assert r.status_code == 200
        assert r.json()["threshold_m"] == pytest.approx(3.0)

    def test_unknown_source_falls_back_to_gps_threshold(self, tmp_path):
        elevations = [float(i) for i in range(1801)]
        _make_activity(tmp_path, self.AID, elevations, altitude_source="unknown")

        r = CLIENT.post(f"/api/activity/{self.AID}/recalculate-elevation/hysteresis")

        assert r.status_code == 200
        assert r.json()["threshold_m"] == pytest.approx(3.0)

    def test_uses_original_elevation_when_dem_backup_present(self, tmp_path):
        original = [float(i) for i in range(1801)]   # real 1800 m climb
        dem_flat  = [900.0] * 1801                    # DEM flattened it
        _make_activity(tmp_path, self.AID, dem_flat,
                       altitude_source="barometric",
                       elevation_m_original=original)

        r = CLIENT.post(f"/api/activity/{self.AID}/recalculate-elevation/hysteresis")

        assert r.status_code == 200
        assert r.json()["elevation_gain_m"] == pytest.approx(1800.0, rel=0.02)

    def test_patches_detail_json_on_disk(self, tmp_path):
        elevations = [float(i) for i in range(1801)]
        _make_activity(tmp_path, self.AID, elevations)

        CLIENT.post(f"/api/activity/{self.AID}/recalculate-elevation/hysteresis")

        detail = json.loads(
            (tmp_path / "activities" / f"{self.AID}.json").read_text()
        )
        assert detail["elevation_gain_m"] > 0

    def test_404_for_missing_activity(self, tmp_path):
        (tmp_path / "activities").mkdir()
        r = CLIENT.post("/api/activity/2024-01-01T080000Z-no-such/recalculate-elevation/hysteresis")
        assert r.status_code == 404

    def test_422_for_missing_timeseries(self, tmp_path):
        acts = tmp_path / "activities"
        acts.mkdir()
        aid = self.AID
        (acts / f"{aid}.json").write_text(json.dumps({"id": aid, "altitude_source": "gps"}))
        # No timeseries file

        r = CLIENT.post(f"/api/activity/{aid}/recalculate-elevation/hysteresis")
        assert r.status_code == 422

    def test_400_for_invalid_id(self):
        r = CLIENT.post("/api/activity/../etc/passwd/recalculate-elevation/hysteresis")
        assert r.status_code in (400, 404, 422)


# ── /recalculate-elevation/dem ────────────────────────────────────────────────

class TestDemEndpoint:
    AID = "2024-01-01T080000Z-test-climb"

    def test_503_when_dem_url_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(edit_server, "dem_url", "")
        r = CLIENT.post(f"/api/activity/{self.AID}/recalculate-elevation/dem")
        assert r.status_code == 503

    def test_404_for_missing_activity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(edit_server, "dem_url", "https://api.open-elevation.com")
        (tmp_path / "activities").mkdir()
        r = CLIENT.post("/api/activity/2024-01-01T080000Z-no-such/recalculate-elevation/dem")
        assert r.status_code == 404

    def test_400_for_invalid_id(self, monkeypatch):
        monkeypatch.setattr(edit_server, "dem_url", "https://api.open-elevation.com")
        r = CLIENT.post("/api/activity/../../evil/recalculate-elevation/dem")
        assert r.status_code in (400, 404, 422)
