"""Tests for bincio.extract.dedup."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bincio.extract.dedup import ActivityRecord, DedupIndex, _SOURCE_QUALITY


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dt(hour: int = 8, minute: int = 0) -> datetime:
    return datetime(2024, 6, 1, hour, minute, 0, tzinfo=timezone.utc)


def _record(
    id: str,
    source_hash: str = "sha256:abc",
    started_at: datetime | None = None,
    distance_m: float | None = 10_000.0,
    source: str | None = "fit_file",
    duplicate_of: str | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        id=id,
        source_hash=source_hash,
        started_at=started_at or _dt(),
        distance_m=distance_m,
        source=source,
        duplicate_of=duplicate_of,
    )


@pytest.fixture
def idx(tmp_path: Path) -> DedupIndex:
    return DedupIndex(output_dir=tmp_path)


# ── exact duplicate ───────────────────────────────────────────────────────────

def test_exact_duplicate_not_found_on_empty_index(idx):
    assert idx.is_exact_duplicate("sha256:abc") is None


def test_exact_duplicate_found_after_register(idx):
    idx.register(_record("act-1", source_hash="sha256:aaa"))
    assert idx.is_exact_duplicate("sha256:aaa") == "act-1"


def test_exact_duplicate_different_hash_not_found(idx):
    idx.register(_record("act-1", source_hash="sha256:aaa"))
    assert idx.is_exact_duplicate("sha256:bbb") is None


# ── near-duplicate ────────────────────────────────────────────────────────────

def test_near_dup_same_time_same_distance(idx):
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=10_000.0))
    result = idx.find_near_duplicate(_dt(8, 0), 10_000.0)
    assert result == "act-1"


def test_near_dup_within_5_min_and_5_pct(idx):
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=10_000.0))
    # 4 min 59 s offset, 4.9% distance difference — both within threshold
    from datetime import timedelta
    result = idx.find_near_duplicate(_dt(8, 0) + timedelta(seconds=299), 9_510.0)
    assert result == "act-1"


def test_near_dup_time_too_far(idx):
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=10_000.0))
    from datetime import timedelta
    result = idx.find_near_duplicate(_dt(8, 0) + timedelta(seconds=301), 10_000.0)
    assert result is None


def test_near_dup_distance_too_different(idx):
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=10_000.0))
    # 6% difference
    result = idx.find_near_duplicate(_dt(8, 0), 10_600.0)
    assert result is None


def test_near_dup_skips_already_marked_duplicates(idx):
    # A record already flagged as a duplicate of something else should not be
    # returned as a canonical candidate.
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=10_000.0,
                          duplicate_of="act-0"))
    result = idx.find_near_duplicate(_dt(8, 0), 10_000.0)
    assert result is None


def test_near_dup_both_zero_distance_skipped(idx):
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=0.0))
    result = idx.find_near_duplicate(_dt(8, 0), 0.0)
    assert result is None


def test_near_dup_none_distance_skipped(idx):
    idx.register(_record("act-1", started_at=_dt(8, 0), distance_m=None))
    result = idx.find_near_duplicate(_dt(8, 0), 10_000.0)
    assert result is None


# ── pick_canonical ────────────────────────────────────────────────────────────

def test_pick_canonical_existing_wins_on_tie(idx):
    idx.register(_record("act-1", source="fit_file"))   # quality 4
    result = idx.pick_canonical("act-1", "fit_file")    # also quality 4
    assert result == "act-1"


def test_pick_canonical_new_wins_when_higher_quality(idx):
    idx.register(_record("act-1", source="gpx_file"))   # quality 2
    result = idx.pick_canonical("act-1", "karoo")       # quality 5
    assert result == "__new__"


def test_pick_canonical_existing_wins_when_higher_quality(idx):
    idx.register(_record("act-1", source="karoo"))      # quality 5
    result = idx.pick_canonical("act-1", "tcx_file")    # quality 1
    assert result == "act-1"


def test_pick_canonical_unknown_source_treated_as_zero(idx):
    idx.register(_record("act-1", source="unknown_device"))  # quality 0
    result = idx.pick_canonical("act-1", "fit_file")          # quality 4
    assert result == "__new__"


# ── source quality ranking ────────────────────────────────────────────────────

def test_source_quality_ordering():
    assert _SOURCE_QUALITY["karoo"] > _SOURCE_QUALITY["fit_file"]
    assert _SOURCE_QUALITY["fit_file"] > _SOURCE_QUALITY["strava_export"]
    assert _SOURCE_QUALITY["strava_export"] > _SOURCE_QUALITY["gpx_file"]
    assert _SOURCE_QUALITY["gpx_file"] > _SOURCE_QUALITY["tcx_file"]
    assert _SOURCE_QUALITY["tcx_file"] > _SOURCE_QUALITY["manual"]


# ── persistence ───────────────────────────────────────────────────────────────

def test_save_and_reload(tmp_path: Path):
    idx = DedupIndex(output_dir=tmp_path)
    idx.register(_record("act-1", source_hash="sha256:aaa",
                          started_at=_dt(8, 0), distance_m=5000.0, source="fit_file"))
    idx.save()

    idx2 = DedupIndex(output_dir=tmp_path)
    assert idx2.is_exact_duplicate("sha256:aaa") == "act-1"
    result = idx2.find_near_duplicate(_dt(8, 0), 5000.0)
    assert result == "act-1"


def test_reload_preserves_duplicate_of(tmp_path: Path):
    idx = DedupIndex(output_dir=tmp_path)
    rec = _record("act-2", source_hash="sha256:bbb",
                  started_at=_dt(8, 0), distance_m=5000.0, duplicate_of="act-1")
    idx.register(rec)
    idx.save()

    idx2 = DedupIndex(output_dir=tmp_path)
    # Should not surface as a near-dup candidate
    assert idx2.find_near_duplicate(_dt(8, 0), 5000.0) is None


def test_empty_index_file_creates_on_save(tmp_path: Path):
    idx = DedupIndex(output_dir=tmp_path)
    idx.save()
    cache = tmp_path / ".bincio_cache.json"
    assert cache.exists()
    data = json.loads(cache.read_text())
    assert data["activities"] == []


def test_fresh_index_on_missing_file(tmp_path: Path):
    idx = DedupIndex(output_dir=tmp_path)
    assert idx.is_exact_duplicate("sha256:anything") is None
