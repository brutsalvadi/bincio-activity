"""Import metadata from Strava's activities.csv bulk export.

Strava export columns we care about:
  Activity ID, Activity Date, Activity Name, Activity Description, Filename
"""

import csv
import json
import re
from pathlib import Path
from typing import Iterator, Optional


_STRAVA_DATE_FMTS = (
    "%b %d, %Y, %I:%M:%S %p",  # "Jun 1, 2024, 7:30:12 AM"
    "%Y-%m-%d %H:%M:%S",
)


class StravaMetadata:
    """Maps original filename → Strava metadata, with secondary strava_id index."""

    def __init__(self, csv_path: Path) -> None:
        self._by_filename: dict[str, dict] = {}
        self._by_strava_id: dict[str, dict] = {}
        self._load(csv_path)

    def _load(self, path: Path) -> None:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                filename = row.get("Filename", "").strip()
                if filename:
                    basename = Path(filename).name
                    self._by_filename[basename] = row
                strava_id = row.get("Activity ID", "").strip()
                if strava_id:
                    self._by_strava_id[strava_id] = row

    def lookup(self, source_file: str) -> Optional[dict]:
        """Return the Strava CSV row for a given source filename, or None."""
        return self._by_filename.get(source_file)

    def lookup_by_strava_id(self, strava_id: str) -> Optional[dict]:
        """Return the Strava CSV row for a given Strava activity ID, or None."""
        return self._by_strava_id.get(str(strava_id))

    def enrich(self, source_file: str, activity: object) -> None:
        """Mutate a ParsedActivity with Strava metadata if found."""
        row = self.lookup(source_file)
        if row is None:
            return

        if not activity.title and row.get("Activity Name"):  # type: ignore[attr-defined]
            activity.title = row["Activity Name"].strip()  # type: ignore[attr-defined]

        if not activity.description and row.get("Activity Description"):  # type: ignore[attr-defined]
            activity.description = row["Activity Description"].strip()  # type: ignore[attr-defined]

        if not activity.strava_id and row.get("Activity ID"):  # type: ignore[attr-defined]
            activity.strava_id = row["Activity ID"].strip()  # type: ignore[attr-defined]


# ── Retroactive sidecar update ────────────────────────────────────────────────

def _parse_sidecar(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) from a sidecar .md file."""
    import re as _re
    import yaml
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = _re.split(r"^---[ \t]*$", text, maxsplit=2, flags=_re.MULTILINE)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            return fm, parts[2].strip()
    return {}, text.strip()


def _write_sidecar(path: Path, fm: dict, body: str) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_text = yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True).strip()
    content = f"---\n{fm_text}\n---\n"
    if body:
        content += f"\n{body}\n"
    path.write_text(content, encoding="utf-8")


def _update_sidecar_from_row(sidecar_path: Path, row: dict) -> bool:
    """Create or update a sidecar with CSV title/description.

    Only fills fields that are not already set in the sidecar.
    Returns True if anything changed.
    """
    title = row.get("Activity Name", "").strip()
    description = row.get("Activity Description", "").strip()
    if not title and not description:
        return False

    fm, body = _parse_sidecar(sidecar_path) if sidecar_path.exists() else ({}, "")

    changed = False
    if title and "title" not in fm:
        fm["title"] = title
        changed = True
    if description and not body:
        body = description
        changed = True

    if not changed:
        return False

    _write_sidecar(sidecar_path, fm, body)
    return True


def apply_csv_to_data_dir(data_dir: Path, metadata: StravaMetadata) -> int:
    """Retroactively apply CSV metadata to existing activities via sidecars.

    Scans all activity JSONs in data_dir/activities/.  For each activity that
    has a strava_id, looks up the corresponding CSV row and creates/updates
    the sidecar in data_dir/edits/ with any missing title or description.

    Only writes fields not already present in the sidecar — manual edits are
    never overwritten.

    Returns the count of activities whose sidecars were created or updated.
    """
    activities_dir = data_dir / "activities"
    edits_dir = data_dir / "edits"

    if not activities_dir.exists():
        return 0

    updated = 0
    for json_path in sorted(activities_dir.glob("*.json")):
        try:
            detail = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        strava_id = detail.get("strava_id")
        if not strava_id:
            continue

        row = metadata.lookup_by_strava_id(str(strava_id))
        if row is None:
            continue

        activity_id = json_path.stem
        sidecar_path = edits_dir / f"{activity_id}.md"
        if _update_sidecar_from_row(sidecar_path, row):
            updated += 1

    return updated
