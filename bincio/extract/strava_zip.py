"""Process a Strava bulk export ZIP file into a BAS data store.

The ZIP (downloaded from strava.com/athlete/delete_your_account or the data export
page) contains:
  activities/          ← GPX, FIT, TCX files (plain or .gz variants)
  activities.csv       ← metadata (title, description, gear, strava ID)
  bikes.csv / shoes.csv / … (ignored here)

Processing strategy: stream one activity at a time to keep disk usage low.
The ZIP is never fully extracted; each activity file is extracted to a temp path,
parsed, ingested, then immediately deleted. The ZIP itself is deleted once done.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Generator, Optional


# File extensions recognised as activity files inside the ZIP.
_ACTIVITY_SUFFIXES = {".gpx", ".fit", ".tcx", ".gpx.gz", ".fit.gz", ".tcx.gz"}


def _is_activity_file(name: str) -> bool:
    n = name.lower()
    return any(n.endswith(s) for s in _ACTIVITY_SUFFIXES)


def strava_zip_iter(
    zip_path: Path,
    data_dir: Path,
    originals_dir: Optional[Path] = None,
    privacy: str = "public",
) -> Generator[dict, None, None]:
    """Process a Strava export ZIP, yielding SSE-style progress dicts.

    Event types:
      {"type": "validating"}
      {"type": "error", "message": str}
      {"type": "extracting_csv"}
      {"type": "progress", "n": int, "total": int, "name": str, "status": "imported"|"skipped"|"error"}
      {"type": "done", "imported": int, "skipped": int, "error_count": int, "errors": list[str]}

    The zip_path file is deleted after processing regardless of success/failure.
    """
    from bincio.extract.ingest import ingest_parsed
    from bincio.extract.parsers.factory import parse_file
    from bincio.extract.strava_csv import StravaMetadata

    yield {"type": "validating"}

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as e:
        zip_path.unlink(missing_ok=True)
        yield {"type": "error", "message": f"Not a valid ZIP file: {e}"}
        return

    try:
        names = zf.namelist()

        # Validate structure
        has_csv = "activities.csv" in names
        activity_files = [n for n in names if n.startswith("activities/") and _is_activity_file(n)]

        if not has_csv:
            yield {"type": "error", "message": "This doesn't look like a Strava export: activities.csv not found"}
            return
        if not activity_files:
            yield {"type": "error", "message": "No activity files found in activities/ folder"}
            return

        # Load activities.csv into memory (it's small — ~700 KB)
        yield {"type": "extracting_csv"}
        csv_bytes = zf.read("activities.csv")
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_csv:
            tmp_csv.write(csv_bytes)
            tmp_csv_path = Path(tmp_csv.name)
        try:
            metadata = StravaMetadata(tmp_csv_path)
        finally:
            tmp_csv_path.unlink(missing_ok=True)

        total = len(activity_files)
        imported = 0
        skipped = 0
        errors: list[str] = []

        for n, zip_entry in enumerate(activity_files, 1):
            entry_name = Path(zip_entry).name  # e.g. "12345678.fit.gz"
            # Title from metadata if available; fall back to filename stem
            meta_row = metadata.lookup(entry_name)
            display_name = (meta_row or {}).get("Activity Name", "").strip() or entry_name

            # Determine activity ID from entry to check for duplicates before extracting
            # (can't do this without parsing, so we extract to a small temp file)
            suffix = "".join(Path(entry_name).suffixes)  # ".fit.gz" or ".gpx" etc.
            tmp_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=data_dir) as tmp:
                    tmp.write(zf.read(zip_entry))
                    tmp_path = Path(tmp.name)

                parsed = parse_file(tmp_path)

                # Enrich with CSV metadata
                if meta_row:
                    if not parsed.title and meta_row.get("Activity Name"):
                        parsed.title = meta_row["Activity Name"].strip()
                    if not parsed.description and meta_row.get("Activity Description"):
                        parsed.description = meta_row["Activity Description"].strip()
                    if not parsed.strava_id and meta_row.get("Activity ID"):
                        parsed.strava_id = meta_row["Activity ID"].strip()

                if originals_dir is not None:
                    import shutil
                    orig_dest = originals_dir / entry_name
                    shutil.copy2(tmp_path, orig_dest)

                ingest_parsed(parsed, data_dir, privacy=privacy)
                imported += 1
                yield {"type": "progress", "n": n, "total": total, "name": display_name, "status": "imported"}

            except FileExistsError:
                skipped += 1
                yield {"type": "progress", "n": n, "total": total, "name": display_name, "status": "skipped"}
            except Exception as exc:
                errors.append(f"{entry_name}: {type(exc).__name__}")
                yield {"type": "progress", "n": n, "total": total, "name": display_name, "status": "error"}
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)

    finally:
        zf.close()
        zip_path.unlink(missing_ok=True)

    yield {
        "type": "done",
        "imported": imported,
        "skipped": skipped,
        "error_count": len(errors),
        "errors": errors[:5],
    }
