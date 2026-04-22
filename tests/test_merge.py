"""Tests for bincio.render.merge — sidecar edit overlay logic."""

import json
import textwrap
from pathlib import Path

import pytest

from bincio.render.merge import apply_sidecar, merge_all, merge_one, parse_sidecar


def _load_merged_activities(merged_dir: Path) -> dict:
    """Load all activities from year-sharded merged index. Returns id→dict map."""
    root = json.loads((merged_dir / "index.json").read_text())
    all_acts = list(root.get("activities", []))
    for shard in root.get("shards", []):
        shard_path = merged_dir / shard["url"]
        if shard_path.exists():
            sub = json.loads(shard_path.read_text())
            all_acts.extend(sub.get("activities", []))
    return {a["id"]: a for a in all_acts}


# ── parse_sidecar ─────────────────────────────────────────────────────────────


def test_parse_sidecar_full(tmp_path):
    md = tmp_path / "act.md"
    md.write_text(textwrap.dedent("""\
        ---
        title: "Ride to the coast"
        sport: cycling
        highlight: true
        private: false
        hide_stats: [cadence, power]
        gear: "Trek Domane"
        ---

        Great day out with Marco.
    """))
    fm, body = parse_sidecar(md)
    assert fm["title"] == "Ride to the coast"
    assert fm["sport"] == "cycling"
    assert fm["highlight"] is True
    assert fm["private"] is False
    assert fm["hide_stats"] == ["cadence", "power"]
    assert fm["gear"] == "Trek Domane"
    assert body == "Great day out with Marco."


def test_parse_sidecar_no_frontmatter(tmp_path):
    md = tmp_path / "act.md"
    md.write_text("Just a description, no frontmatter.\n")
    fm, body = parse_sidecar(md)
    assert fm == {}
    assert body == "Just a description, no frontmatter."


def test_parse_sidecar_frontmatter_only(tmp_path):
    md = tmp_path / "act.md"
    md.write_text("---\ntitle: Solo spin\n---\n")
    fm, body = parse_sidecar(md)
    assert fm["title"] == "Solo spin"
    assert body == ""


# ── apply_sidecar ─────────────────────────────────────────────────────────────

BASE_DETAIL = {
    "id": "2024-01-01T080000Z-morning-ride",
    "title": "Morning Ride",
    "sport": "cycling",
    "started_at": "2024-01-01T08:00:00Z",
    "description": "Original description from Strava.",
    "privacy": "public",
    "gear": None,
    "custom": {},
}


def test_apply_sidecar_title_and_sport():
    fm = {"title": "Renamed", "sport": "running"}
    result = apply_sidecar(BASE_DETAIL, fm, "")
    assert result["title"] == "Renamed"
    assert result["sport"] == "running"
    # Original must be unchanged
    assert BASE_DETAIL["title"] == "Morning Ride"


def test_apply_sidecar_body_becomes_description():
    result = apply_sidecar(BASE_DETAIL, {}, "My **epic** ride.")
    assert result["description"] == "My **epic** ride."


def test_apply_sidecar_body_takes_precedence_over_fm_description():
    fm = {"description": "FM description"}
    result = apply_sidecar(BASE_DETAIL, fm, "Body description")
    assert result["description"] == "Body description"


def test_apply_sidecar_private_flag():
    result = apply_sidecar(BASE_DETAIL, {"private": True}, "")
    assert result["privacy"] == "unlisted"


def test_apply_sidecar_highlight():
    result = apply_sidecar(BASE_DETAIL, {"highlight": True}, "")
    assert result["custom"]["highlight"] is True


def test_apply_sidecar_hide_stats():
    result = apply_sidecar(BASE_DETAIL, {"hide_stats": ["cadence", "power"]}, "")
    assert result["custom"]["hide_stats"] == ["cadence", "power"]


def test_apply_sidecar_does_not_mutate_input():
    fm = {"title": "New title", "highlight": True}
    original_custom = BASE_DETAIL["custom"]
    apply_sidecar(BASE_DETAIL, fm, "")
    assert BASE_DETAIL["title"] == "Morning Ride"
    assert BASE_DETAIL["custom"] is original_custom
    assert "highlight" not in original_custom


# ── merge_all ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def data_dir(tmp_path):
    acts = tmp_path / "activities"
    acts.mkdir()
    # Two activities
    for act_id, title, sport, started_at in [
        ("2024-01-01T080000Z-morning-ride", "Morning Ride", "cycling", "2024-01-01T08:00:00Z"),
        ("2024-01-02T090000Z-easy-run", "Easy Run", "running", "2024-01-02T09:00:00Z"),
    ]:
        detail = {
            "id": act_id, "title": title, "sport": sport,
            "started_at": started_at,
            "description": "", "privacy": "public", "custom": {},
        }
        (acts / f"{act_id}.json").write_text(json.dumps(detail))
    # Index
    index = {"activities": [
        {"id": "2024-01-01T080000Z-morning-ride", "title": "Morning Ride",
         "sport": "cycling", "started_at": "2024-01-01T08:00:00Z", "privacy": "public", "custom": {}},
        {"id": "2024-01-02T090000Z-easy-run", "title": "Easy Run",
         "sport": "running", "started_at": "2024-01-02T09:00:00Z", "privacy": "public", "custom": {}},
    ]}
    (tmp_path / "index.json").write_text(json.dumps(index))
    return tmp_path


def test_merge_all_no_sidecars(data_dir):
    n = merge_all(data_dir)
    assert n == 0
    merged = data_dir / "_merged"
    assert merged.exists()
    # Unmodified files are symlinked
    detail_link = merged / "activities" / "2024-01-01T080000Z-morning-ride.json"
    assert detail_link.is_symlink()


def test_merge_all_applies_sidecar(data_dir):
    edits = data_dir / "edits"
    edits.mkdir()
    (edits / "2024-01-01T080000Z-morning-ride.md").write_text(
        "---\ntitle: Epic Ride\nhighlight: true\n---\n\nWhat a day!"
    )
    n = merge_all(data_dir)
    assert n == 1

    merged_json = data_dir / "_merged" / "activities" / "2024-01-01T080000Z-morning-ride.json"
    assert not merged_json.is_symlink()
    data = json.loads(merged_json.read_text())
    assert data["title"] == "Epic Ride"
    assert data["custom"]["highlight"] is True
    assert data["description"] == "What a day!"

    # Untouched activity is still a symlink
    run_link = data_dir / "_merged" / "activities" / "2024-01-02T090000Z-easy-run.json"
    assert run_link.is_symlink()


def test_merge_all_private_filtered_from_index(data_dir):
    edits = data_dir / "edits"
    edits.mkdir()
    (edits / "2024-01-01T080000Z-morning-ride.md").write_text("---\nprivate: true\n---\n")
    merge_all(data_dir)

    activities = _load_merged_activities(data_dir / "_merged")
    # unlisted activities are kept in the index; filtering is client-side
    assert "2024-01-01T080000Z-morning-ride" in activities
    assert activities["2024-01-01T080000Z-morning-ride"]["privacy"] == "unlisted"
    assert "2024-01-02T090000Z-easy-run" in activities


def test_merge_all_highlight_sorts_first(data_dir):
    edits = data_dir / "edits"
    edits.mkdir()
    # Highlight the older activity — it should appear first
    (edits / "2024-01-01T080000Z-morning-ride.md").write_text("---\nhighlight: true\n---\n")
    merge_all(data_dir)

    # Highlighted activity must be first within its year shard
    merged_dir = data_dir / "_merged"
    root = json.loads((merged_dir / "index.json").read_text())
    shard_path = merged_dir / root["shards"][0]["url"]
    ids = [a["id"] for a in json.loads(shard_path.read_text())["activities"]]
    assert ids[0] == "2024-01-01T080000Z-morning-ride"


def test_merge_all_idempotent(data_dir):
    edits = data_dir / "edits"
    edits.mkdir()
    (edits / "2024-01-01T080000Z-morning-ride.md").write_text("---\ntitle: Renamed\n---\n")
    merge_all(data_dir)
    merge_all(data_dir)  # second run should not error or double-apply
    data = json.loads(
        (data_dir / "_merged" / "activities" / "2024-01-01T080000Z-morning-ride.json").read_text()
    )
    assert data["title"] == "Renamed"


# ── timeseries file handling ──────────────────────────────────────────────────


@pytest.fixture()
def data_dir_with_timeseries(tmp_path):
    """data_dir fixture extended with .timeseries.json sidecar files."""
    acts = tmp_path / "activities"
    acts.mkdir()
    ACT_ID = "2024-01-01T080000Z-morning-ride"
    detail = {
        "id": ACT_ID, "title": "Morning Ride", "sport": "cycling",
        "started_at": "2024-01-01T08:00:00Z",
        "description": "", "privacy": "public", "custom": {},
        "timeseries_url": f"activities/{ACT_ID}.timeseries.json",
    }
    ts_data = {"t": [0, 1], "lat": [45.0, 45.1], "lon": [7.0, 7.1],
               "elevation_m": [300.0, 301.0], "speed_kmh": [None, None],
               "hr_bpm": [None, None], "cadence_rpm": [None, None],
               "power_w": [None, None], "temperature_c": [None, None]}
    (acts / f"{ACT_ID}.json").write_text(json.dumps(detail))
    (acts / f"{ACT_ID}.timeseries.json").write_text(json.dumps(ts_data))
    index = {"activities": [
        {"id": ACT_ID, "title": "Morning Ride", "sport": "cycling",
         "started_at": "2024-01-01T08:00:00Z", "privacy": "public", "custom": {}},
    ]}
    (tmp_path / "index.json").write_text(json.dumps(index))
    return tmp_path, ACT_ID


def test_merge_all_symlinks_timeseries(data_dir_with_timeseries):
    """merge_all should symlink .timeseries.json alongside the detail JSON."""
    data_dir, act_id = data_dir_with_timeseries
    merge_all(data_dir)

    ts_dest = data_dir / "_merged" / "activities" / f"{act_id}.timeseries.json"
    assert ts_dest.exists(), "timeseries file not present in _merged"
    assert ts_dest.is_symlink(), "timeseries file should be a symlink (no merge needed)"

    # Points to the original
    src = data_dir / "activities" / f"{act_id}.timeseries.json"
    assert ts_dest.resolve() == src.resolve()


def test_merge_all_timeseries_survives_sidecar(data_dir_with_timeseries):
    """When a sidecar is applied (detail JSON is rewritten), the timeseries
    symlink should still be created alongside it."""
    data_dir, act_id = data_dir_with_timeseries
    edits = data_dir / "edits"
    edits.mkdir()
    (edits / f"{act_id}.md").write_text("---\ntitle: Renamed\n---\n")
    merge_all(data_dir)

    detail_dest = data_dir / "_merged" / "activities" / f"{act_id}.json"
    ts_dest = data_dir / "_merged" / "activities" / f"{act_id}.timeseries.json"

    assert not detail_dest.is_symlink(), "sidecar detail should be a copy, not symlink"
    assert ts_dest.exists(), "timeseries should still be present after sidecar merge"
    assert ts_dest.is_symlink(), "timeseries should remain a symlink"


def test_merge_one_symlinks_timeseries(data_dir_with_timeseries):
    """merge_one should symlink the .timeseries.json file for the given activity."""
    data_dir, act_id = data_dir_with_timeseries
    merged_acts = data_dir / "_merged" / "activities"
    merged_acts.mkdir(parents=True)

    merge_one(data_dir, act_id)

    ts_dest = merged_acts / f"{act_id}.timeseries.json"
    assert ts_dest.exists()
    assert ts_dest.is_symlink()


def test_merge_all_idempotent_with_timeseries(data_dir_with_timeseries):
    """Running merge_all twice should not break timeseries symlinks."""
    data_dir, act_id = data_dir_with_timeseries
    merge_all(data_dir)
    merge_all(data_dir)

    ts_dest = data_dir / "_merged" / "activities" / f"{act_id}.timeseries.json"
    assert ts_dest.exists()
    assert ts_dest.is_symlink()
