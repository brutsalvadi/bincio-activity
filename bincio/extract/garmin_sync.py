"""Garmin Connect incremental sync — generator-based, mirrors strava_sync_iter.

Sync state is stored in {user_dir}/garmin_sync.json:
  {
    "last_sync_at": "2026-04-12"   ← date of last successful sync (YYYY-MM-DD)
  }

We query Garmin for all activities from (last_sync_at - 1 day) to today,
then skip any that already exist (FileExistsError from ingest_parsed).
The -1 day buffer catches activities that were saved to Garmin slightly
after their recorded end time crosses midnight.

Each yielded dict has a ``type`` key:
  - ``"fetching"``  — about to contact Garmin
  - ``"progress"``  — one activity processed; keys: n, total, name, status, garmin_id
  - ``"done"``      — final summary; keys: imported, skipped, error_count, errors
  - ``"error"``     — fatal error; key: message
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

_SYNC_FILE = "garmin_sync.json"


# ── Sync state helpers ────────────────────────────────────────────────────────

def _load_sync_state(user_dir: Path) -> dict:
    p = user_dir / _SYNC_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_sync_state(user_dir: Path, state: dict) -> None:
    (user_dir / _SYNC_FILE).write_text(json.dumps(state, indent=2))


# ── FIT extraction from ZIP ───────────────────────────────────────────────────

def _extract_fit(zip_bytes: bytes) -> tuple[bytes, str]:
    """Return (fit_bytes, filename) from a Garmin activity ZIP.

    Garmin always packages the original FIT as the first .fit entry.
    Raises ValueError if no FIT file is found.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
        if not fit_names:
            raise ValueError(f"No FIT file in archive. Contents: {zf.namelist()}")
        name = fit_names[0]
        return zf.read(name), name


# ── Main generator ────────────────────────────────────────────────────────────

def garmin_sync_iter(
    data_dir: Path,
    user_dir: Path,
) -> Generator[dict, None, None]:
    """Fetch new activities from Garmin Connect and ingest them.

    Args:
        data_dir:  Root data directory (used for encryption key lookup).
        user_dir:  Per-user directory (contains activities/, garmin_creds.json, etc.).
    """
    from bincio.extract.garmin_api import GarminError, get_client
    from bincio.extract.ingest import ingest_parsed
    from bincio.extract.parsers.fit import FitParser

    # ── Login ──────────────────────────────────────────────────────────────────
    try:
        client = get_client(data_dir, user_dir)
    except GarminError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    yield {"type": "fetching"}

    # ── Determine date range ───────────────────────────────────────────────────
    state = _load_sync_state(user_dir)
    last = state.get("last_sync_at")

    if last:
        # Start one day before last sync to catch edge cases around midnight
        start_dt = datetime.fromisoformat(last) - timedelta(days=1)
    else:
        # First sync: import everything Garmin has
        start_dt = datetime(2000, 1, 1)

    start_date = start_dt.strftime("%Y-%m-%d")
    end_date   = datetime.now().strftime("%Y-%m-%d")

    # ── Fetch activity list ────────────────────────────────────────────────────
    try:
        activities = client.get_activities_by_date(
            startdate=start_date,
            enddate=end_date,
        )
    except Exception as exc:
        yield {"type": "error", "message": f"Failed to fetch activity list: {exc}"}
        return

    total      = len(activities)
    imported   = 0
    skipped    = 0
    errors: list[str] = []
    parser     = FitParser()

    # ── Process each activity ──────────────────────────────────────────────────
    for n, meta in enumerate(activities, 1):
        garmin_id = meta.get("activityId")
        name      = meta.get("activityName") or "Untitled"

        try:
            # Download original FIT (wrapped in a ZIP by Garmin)
            try:
                zip_bytes = client.download_activity(
                    garmin_id,
                    dl_fmt=client.ActivityDownloadFormat.ORIGINAL,
                )
            except Exception as exc:
                raise RuntimeError(f"Download failed: {exc}") from exc

            try:
                fit_bytes, fit_name = _extract_fit(zip_bytes)
            except Exception as exc:
                raise RuntimeError(f"ZIP extraction failed: {exc}") from exc

            # Parse FIT — pass a dummy Path so the parser has a filename for
            # any format-detection logic; raw bytes are the actual data.
            fake_path = Path(fit_name)
            try:
                parsed = parser.parse(fake_path, fit_bytes)
            except Exception as exc:
                raise RuntimeError(f"FIT parse error: {exc}") from exc

            # Ingest — raises FileExistsError if already present (dedup)
            ingest_parsed(parsed, user_dir)
            imported += 1
            yield {
                "type": "progress",
                "n": n, "total": total, "name": name,
                "status": "imported",
                "garmin_id": garmin_id,
            }

        except FileExistsError:
            skipped += 1
            yield {
                "type": "progress",
                "n": n, "total": total, "name": name,
                "status": "skipped",
                "garmin_id": garmin_id,
            }

        except Exception as exc:
            errors.append(f"{garmin_id} ({name}): {type(exc).__name__}: {exc}")
            yield {
                "type": "progress",
                "n": n, "total": total, "name": name,
                "status": "error",
                "garmin_id": garmin_id,
            }

    # ── Persist sync state ─────────────────────────────────────────────────────
    state["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _save_sync_state(user_dir, state)

    yield {
        "type": "done",
        "imported":    imported,
        "skipped":     skipped,
        "error_count": len(errors),
        "errors":      errors[:5],
    }


def run_garmin_sync(data_dir: Path, user_dir: Path) -> dict:
    """Blocking wrapper around garmin_sync_iter for non-SSE callers."""
    result: dict = {}
    for event in garmin_sync_iter(data_dir, user_dir):
        if event["type"] == "done":
            result = event
        elif event["type"] == "error":
            raise RuntimeError(event["message"])
    return result
