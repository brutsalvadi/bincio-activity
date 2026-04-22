"""Duplicate activity detection.

Two kinds of duplicates:

1. Exact duplicate — same source_hash. Skip entirely.
2. Near-duplicate — same ride recorded by two devices / exported from two
   platforms. Detected by (started_at ± 5 min) AND (distance ± 5%).
   The "better" source wins; the other gets duplicate_of set.

The deduplication index is a JSON file persisted in the output directory so
that incremental runs don't re-evaluate already-resolved pairs.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_INDEX_FILE = ".bincio_cache.json"

# Source quality ranking (higher = preferred when deduplicating)
_SOURCE_QUALITY: dict[str, int] = {
    "karoo": 5,
    "fit_file": 4,
    "garmin_connect": 4,
    "strava_export": 3,
    "gpx_file": 2,
    "tcx_file": 1,
    "wahoo": 3,
    "komoot": 2,
    "manual": 0,
}


@dataclass
class ActivityRecord:
    """Minimal record stored in the dedup index."""

    id: str
    source_hash: str
    started_at: datetime
    distance_m: Optional[float]
    source: Optional[str]
    duplicate_of: Optional[str] = None


@dataclass
class DedupIndex:
    output_dir: Path
    _records: dict[str, ActivityRecord] = field(default_factory=dict)
    # source_hash → id, for exact-duplicate lookup
    _by_hash: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load()

    def _load(self) -> None:
        p = self.output_dir / _INDEX_FILE
        if not p.exists():
            return
        data = json.loads(p.read_text())
        for item in data.get("activities", []):
            started_at = datetime.fromisoformat(item["started_at"])
            r = ActivityRecord(
                id=item["id"],
                source_hash=item["source_hash"],
                started_at=started_at,
                distance_m=item.get("distance_m"),
                source=item.get("source"),
                duplicate_of=item.get("duplicate_of"),
            )
            self._records[r.id] = r
            self._by_hash[r.source_hash] = r.id

    def save(self) -> None:
        p = self.output_dir / _INDEX_FILE
        data = {
            "activities": [
                {
                    "id": r.id,
                    "source_hash": r.source_hash,
                    "started_at": r.started_at.isoformat(),
                    "distance_m": r.distance_m,
                    "source": r.source,
                    "duplicate_of": r.duplicate_of,
                }
                for r in self._records.values()
            ]
        }
        p.write_text(json.dumps(data, indent=2))

    def is_exact_duplicate(self, source_hash: str) -> Optional[str]:
        """Return existing activity ID if hash is already in the index."""
        return self._by_hash.get(source_hash)

    def find_near_duplicate(
        self,
        started_at: datetime,
        distance_m: Optional[float],
    ) -> Optional[str]:
        """Return ID of a near-duplicate if one exists."""
        for r in self._records.values():
            if r.duplicate_of is not None:
                continue  # skip already-marked duplicates
            if abs((r.started_at - started_at).total_seconds()) > 5 * 60:
                continue
            if distance_m is None or r.distance_m is None:
                continue
            ref = max(distance_m, r.distance_m)
            if ref < 1.0:
                continue  # both near-zero (indoor/manual) — skip distance check
            if abs(distance_m - r.distance_m) / ref < 0.05:
                return r.id
        return None

    def register(self, record: ActivityRecord) -> None:
        self._records[record.id] = record
        self._by_hash[record.source_hash] = record.id

    def pick_canonical(self, existing_id: str, new_source: Optional[str]) -> str:
        """Return the ID of whichever record should be canonical."""
        existing = self._records[existing_id]
        existing_q = _SOURCE_QUALITY.get(existing.source or "", 0)
        new_q = _SOURCE_QUALITY.get(new_source or "", 0)
        # New record is strictly better → existing becomes the duplicate
        if new_q > existing_q:
            return "__new__"
        return existing_id
