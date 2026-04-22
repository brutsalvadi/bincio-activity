"""Facade for writing a parsed or Strava-sourced activity into a BAS data store.

Callers (edit/ops.py) import from here instead of reaching into extract.metrics,
extract.writer, and extract.strava_api individually.  If the internal structure
of the extract package changes, only this file needs updating.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from bincio.extract.models import ParsedActivity


def ingest_parsed(
    parsed: ParsedActivity,
    data_dir: Path,
    privacy: str = "public",
    rdp_epsilon: float = 0.0001,
) -> str:
    """Compute metrics, write activity files, and update index.json.

    Args:
        parsed:      Activity produced by any parser or Strava converter.
        data_dir:    Per-user output directory (contains activities/, index.json).
        privacy:     BAS privacy level — "public", "no_gps", or "private".
        rdp_epsilon: RDP simplification threshold in degrees.

    Returns:
        The BAS activity ID of the written activity.

    Raises:
        FileExistsError: If an activity with the same ID already exists.
    """
    from bincio.extract.metrics import compute
    from bincio.extract.writer import (
        build_summary,
        make_activity_id,
        write_activity,
        write_index,
    )

    activity_id = make_activity_id(parsed)
    if (data_dir / "activities" / f"{activity_id}.json").exists():
        raise FileExistsError(f"Activity already exists: {activity_id}")

    metrics = compute(parsed)
    effective_privacy = parsed.privacy if parsed.privacy is not None else privacy
    write_activity(parsed, metrics, data_dir, privacy=effective_privacy, rdp_epsilon=rdp_epsilon)
    summary = build_summary(parsed, metrics, activity_id, effective_privacy)

    index_path = data_dir / "index.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"owner": {"handle": "unknown"}, "activities": []}
    owner = index_data.get("owner", {})
    summaries: dict[str, Any] = {s["id"]: s for s in index_data.get("activities", [])}
    summaries[activity_id] = summary
    write_index(list(summaries.values()), data_dir, owner)

    # Rebuild athlete.json with updated MMP curves and records.
    # Preserve any manually-set fields (max_hr, ftp_w, zones, etc.) from the existing file.
    from bincio.extract.writer import write_athlete_json
    _COMPUTED = {"bas_version", "generated_at", "power_curve", "records", "best_climbs"}
    athlete_config: dict[str, Any] = {}
    athlete_path = data_dir / "athlete.json"
    if athlete_path.exists():
        try:
            existing = json.loads(athlete_path.read_text(encoding="utf-8"))
            athlete_config = {k: v for k, v in existing.items() if k not in _COMPUTED}
        except Exception:
            pass
    write_athlete_json(list(summaries.values()), data_dir, athlete_config)

    return activity_id


def strava_sync_iter(
    data_dir: Path,
    client_id: str,
    client_secret: str,
    originals_dir: Optional[Path] = None,
):
    """Generator version of strava_sync — yields progress dicts, then a final summary.

    Each yielded dict has a ``type`` key:
      - ``"fetching"``  — about to fetch the activity list from Strava
      - ``"progress"``  — one activity processed; keys: n, total, name, status ("imported"|"skipped"|"error")
      - ``"done"``      — final summary; keys: imported, skipped, error_count, errors
      - ``"error"``     — fatal error before processing started; key: message
    """
    import time

    from bincio.extract.strava_api import (
        StravaError,
        ensure_fresh,
        fetch_activities,
        fetch_streams,
        save_token,
        strava_meta_to_partial,
        strava_to_parsed,
    )
    from bincio.extract.writer import make_activity_id

    if not client_id or not client_secret:
        yield {"type": "error", "message": "Strava not configured"}
        return

    try:
        token = ensure_fresh(data_dir, client_id, client_secret)
    except StravaError as e:
        yield {"type": "error", "message": str(e)}
        return

    yield {"type": "fetching"}

    after: Optional[int] = token.get("last_sync_at")
    try:
        activities = fetch_activities(token["access_token"], after=after)
    except StravaError as e:
        yield {"type": "error", "message": str(e)}
        return

    total = len(activities)
    imported = 0
    skipped = 0
    errors: list[str] = []

    for n, meta in enumerate(activities, 1):
        name = meta.get("name", "Untitled")
        try:
            activity_id = make_activity_id(strava_meta_to_partial(meta))
            if (data_dir / "activities" / f"{activity_id}.json").exists():
                skipped += 1
                yield {"type": "progress", "n": n, "total": total, "name": name, "status": "skipped"}
                continue
            streams = fetch_streams(token["access_token"], meta["id"])
            if originals_dir is not None:
                orig_path = originals_dir / f"{activity_id}.json"
                orig_path.write_text(
                    json.dumps({"meta": meta, "streams": streams}, indent=2),
                    encoding="utf-8",
                )
            parsed = strava_to_parsed(meta, streams)
            ingest_parsed(parsed, data_dir, privacy="public", rdp_epsilon=0.0001)
            imported += 1
            yield {"type": "progress", "n": n, "total": total, "name": name, "status": "imported"}
        except Exception as exc:
            errors.append(f"{meta.get('id')}: {type(exc).__name__}")
            yield {"type": "progress", "n": n, "total": total, "name": name, "status": "error"}

    token["last_sync_at"] = int(time.time())
    save_token(data_dir, token)

    yield {
        "type": "done",
        "imported": imported,
        "skipped": skipped,
        "error_count": len(errors),
        "errors": errors[:5],
    }


def strava_sync(
    data_dir: Path,
    client_id: str,
    client_secret: str,
    originals_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Fetch new Strava activities and ingest them into data_dir.

    Returns:
        Dict with keys: ok, imported, skipped, error_count, errors.

    Raises:
        RuntimeError: If Strava credentials are missing or API calls fail.
    """
    result: dict[str, Any] = {}
    for event in strava_sync_iter(data_dir, client_id, client_secret, originals_dir):
        if event["type"] == "error":
            raise RuntimeError(event["message"])
        if event["type"] == "done":
            result = event
    return {"ok": True, **{k: v for k, v in result.items() if k != "type"}}
