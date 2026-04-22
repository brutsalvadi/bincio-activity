"""Pure write operations used by both the single-user edit server and the
multi-user serve server.

No FastAPI, no globals — all context is passed as explicit arguments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

# ── Shared constants (imported by edit/server.py and serve/server.py) ─────────

SPORTS = ["cycling", "running", "hiking", "walking", "swimming", "skiing", "other"]
STAT_PANELS = ["elevation", "speed", "heart_rate", "cadence", "power"]
VALID_ACTIVITY_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9\-]{0,250}$')


def apply_sidecar_edit(activity_id: str, payload: dict[str, Any], data_dir: Path) -> None:
    """Write a sidecar .md file and trigger merge_all().

    Args:
        activity_id: Validated activity ID (caller must validate).
        payload:     Dict with optional keys: title, sport, gear, description,
                     highlight, private, hide_stats.
        data_dir:    Per-user data directory (contains activities/, edits/).
    """
    edits_dir = data_dir / "edits"
    edits_dir.mkdir(exist_ok=True)
    sidecar_path = edits_dir / f"{activity_id}.md"

    lines: list[str] = []
    if payload.get("title"):
        lines.append(f"title: {json.dumps(payload['title'])}")
    if payload.get("sport") and payload["sport"] in SPORTS and payload["sport"] != "other":
        lines.append(f"sport: {payload['sport']}")
    if payload.get("gear"):
        lines.append(f"gear: {json.dumps(payload['gear'])}")
    if payload.get("highlight"):
        lines.append("highlight: true")
    if payload.get("private"):
        lines.append("private: true")
    hide = [s for s in (payload.get("hide_stats") or []) if s in STAT_PANELS]
    if hide:
        lines.append(f"hide_stats: [{', '.join(hide)}]")

    description = (payload.get("description") or "").strip()

    content = "---\n" + "\n".join(lines) + "\n---\n"
    if description:
        content += "\n" + description + "\n"

    sidecar_path.write_text(content, encoding="utf-8")

    from bincio.render.merge import merge_one
    merge_one(data_dir, activity_id)


def run_strava_sync(
    data_dir: Path,
    client_id: str,
    client_secret: str,
    originals_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Fetch new Strava activities and write them into data_dir.

    Args:
        data_dir:      Per-user data directory.
        client_id:     Strava OAuth client ID.
        client_secret: Strava OAuth client secret.
        originals_dir: If set, raw Strava API data (meta + streams) is saved here
                       as JSON files for potential future reprocessing.

    Returns:
        Dict with keys: ok, imported, skipped, error_count, errors.

    Raises:
        RuntimeError: If Strava credentials are missing or API calls fail.
    """
    from bincio.extract.ingest import strava_sync as _strava_sync
    from bincio.render.merge import merge_all

    result = _strava_sync(data_dir, client_id, client_secret, originals_dir=originals_dir)
    if result["imported"]:
        merge_all(data_dir)

    return result
