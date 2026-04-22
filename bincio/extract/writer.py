"""Write a processed activity to BAS JSON files."""

import json
import re
import unicodedata
from pathlib import Path

from bincio.extract.metrics import ComputedMetrics
from bincio.extract.models import LapData, ParsedActivity
from bincio.extract.simplify import build_geojson, preview_coords
from bincio.extract.timeseries import build_timeseries


def make_activity_id(activity: ParsedActivity) -> str:
    """Generate a BAS activity ID from started_at + optional title slug.

    Always uses UTC with Z suffix so IDs are URL-safe (no + chars).
    """
    from datetime import timezone
    ts = activity.started_at.astimezone(timezone.utc)
    ts_part = ts.strftime("%Y-%m-%dT%H%M%SZ")

    if activity.title:
        slug = _slugify(activity.title)
        return f"{ts_part}-{slug}" if slug else ts_part
    return ts_part


def write_activity(
    activity: ParsedActivity,
    metrics: ComputedMetrics,
    output_dir: Path,
    privacy: str = "public",
    duplicate_of: str | None = None,
    rdp_epsilon: float = 0.0001,
    pending: bool = False,
) -> str:
    """Write {id}.json and (if GPS available) {id}.geojson. Returns the ID.

    When pending=True, writes to a uniquely-named pending file
    ({id}.{hash[:8]}.pending.json) instead of the final path. This avoids
    race conditions when multiple workers process activities with the same ID.
    The main process is responsible for promoting pending files to final paths
    via finalize_pending().
    """
    activity_id = make_activity_id(activity)
    acts_dir = output_dir / "activities"
    acts_dir.mkdir(parents=True, exist_ok=True)

    source = _infer_source(activity)
    # "unlisted" activities keep their GPS track (not in the public feed, but the
    # URL is not secret — same model as the detail JSON). Only "no_gps" suppresses
    # the track. "private" is the legacy alias for "unlisted".
    has_gps = metrics.bbox is not None and privacy not in ("no_gps",)

    # Build timeseries once — written to a separate file to keep detail JSON small.
    # Treat an empty timeseries (no points) as None so no file is created.
    _ts = build_timeseries(activity.points, activity.started_at, privacy)
    timeseries = _ts if _ts.get("t") else None
    tag = activity.source_hash[-8:] if activity.source_hash else "unknown"

    # ── detail JSON ──────────────────────────────────────────────────────────
    detail: dict = {
        "bas_version": "1.0",
        "id": activity_id,
        "title": activity.title or _auto_title(activity),
        "description": activity.description,
        "sport": activity.sport,
        "sub_sport": activity.sub_sport,
        "started_at": activity.started_at.isoformat(),
        "distance_m": metrics.distance_m,
        "duration_s": metrics.duration_s,
        "moving_time_s": metrics.moving_time_s,
        "elevation_gain_m": metrics.elevation_gain_m,
        "elevation_loss_m": metrics.elevation_loss_m,
        "avg_speed_kmh": metrics.avg_speed_kmh,
        "max_speed_kmh": metrics.max_speed_kmh,
        "avg_hr_bpm": metrics.avg_hr_bpm,
        "max_hr_bpm": metrics.max_hr_bpm,
        "avg_cadence_rpm": metrics.avg_cadence_rpm,
        "avg_power_w": metrics.avg_power_w,
        "max_power_w": metrics.max_power_w,
        "gear": activity.gear,
        "device": activity.device,
        "bbox": list(metrics.bbox) if metrics.bbox else None,
        "start_latlng": list(metrics.start_latlng) if metrics.start_latlng else None,
        "end_latlng": list(metrics.end_latlng) if metrics.end_latlng else None,
        "mmp": metrics.mmp,
        "best_efforts": metrics.best_efforts,
        "best_climb_m": metrics.best_climb_m,
        "laps": [_serialise_lap(lap) for lap in activity.laps],
        "timeseries_url": f"activities/{activity_id}.timeseries.json" if timeseries else None,
        "source": source,
        "source_file": activity.source_file,
        "source_hash": activity.source_hash,
        "altitude_source": activity.altitude_source,
        "strava_id": activity.strava_id,
        "duplicate_of": duplicate_of,
        "privacy": privacy,
        "custom": {},
    }

    if pending:
        # Write to a unique pending file — no collision possible
        json_path = acts_dir / f"{activity_id}.{tag}.pending.json"
    else:
        json_path = acts_dir / f"{activity_id}.json"
        # Legacy non-pending path: collision guard for callers that don't use
        # the pending workflow (e.g. edit server upload_activity)
        if json_path.exists():
            existing = json.loads(json_path.read_text(encoding="utf-8"))
            if existing.get("source_hash") != activity.source_hash:
                activity_id = f"{activity_id}-{activity.source_hash[-6:]}"
                json_path = acts_dir / f"{activity_id}.json"
                detail["id"] = activity_id
                detail["timeseries_url"] = f"activities/{activity_id}.timeseries.json" if timeseries else None

    json_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False))

    # ── timeseries JSON (separate file — lazy-loaded by the UI) ─────────────
    if timeseries:
        if pending:
            ts_path = acts_dir / f"{activity_id}.{tag}.pending.timeseries.json"
        else:
            ts_path = acts_dir / f"{activity_id}.timeseries.json"
        ts_path.write_text(json.dumps(timeseries, indent=2, ensure_ascii=False))

    # ── GeoJSON track ────────────────────────────────────────────────────────
    if has_gps:
        geojson = build_geojson(activity.points, activity_id, epsilon=rdp_epsilon)
        if pending:
            geojson_path = acts_dir / f"{activity_id}.{tag}.pending.geojson"
        else:
            geojson_path = acts_dir / f"{activity_id}.geojson"
        geojson_path.write_text(json.dumps(geojson, indent=2, ensure_ascii=False))

    return activity_id


def activity_quality(result: dict) -> int:
    """Compute a quality score for an activity result from a worker.

    Higher is better. Used by the main process to pick the best version
    when multiple workers produce results for the same activity ID.
    """
    from bincio.extract.dedup import _SOURCE_QUALITY

    score = 0
    # Source type quality (FIT > GPX > TCX)
    score += _SOURCE_QUALITY.get(result.get("source") or "", 0) * 100
    # Sensor channel count
    score += result.get("sensor_channels", 0) * 10
    # Point count (more data = better)
    score += min(result.get("point_count", 0), 50000) // 100
    return score


def finalize_pending(output_dir: Path, activity_id: str, source_hash: str) -> str:
    """Promote a pending file to its final path via atomic rename.

    If another activity already occupies the ID (different source_hash),
    the pending file is disambiguated with a hash suffix.

    Returns the final activity_id (may include suffix).
    """
    acts_dir = output_dir / "activities"
    tag = source_hash[-8:] if source_hash else "unknown"

    pending_json = acts_dir / f"{activity_id}.{tag}.pending.json"
    pending_geojson = acts_dir / f"{activity_id}.{tag}.pending.geojson"
    pending_ts = acts_dir / f"{activity_id}.{tag}.pending.timeseries.json"

    final_id = activity_id
    final_json = acts_dir / f"{final_id}.json"

    # Check for ID collision with a different activity
    if final_json.exists():
        existing = json.loads(final_json.read_text(encoding="utf-8"))
        if existing.get("source_hash") != source_hash:
            final_id = f"{activity_id}-{source_hash[-6:]}"
            final_json = acts_dir / f"{final_id}.json"

    # Update the ID inside the JSON if it changed
    if final_id != activity_id and pending_json.exists():
        detail = json.loads(pending_json.read_text(encoding="utf-8"))
        detail["id"] = final_id
        if detail.get("timeseries_url"):
            detail["timeseries_url"] = f"activities/{final_id}.timeseries.json"
        pending_json.write_text(json.dumps(detail, indent=2, ensure_ascii=False))

    # Atomic rename: pending → final
    if pending_json.exists():
        pending_json.rename(final_json)

    final_geojson = acts_dir / f"{final_id}.geojson"
    if pending_geojson.exists():
        # Update the ID in GeoJSON properties too
        if final_id != activity_id:
            geo = json.loads(pending_geojson.read_text(encoding="utf-8"))
            geo["properties"]["id"] = final_id
            pending_geojson.write_text(json.dumps(geo, indent=2, ensure_ascii=False))
        pending_geojson.rename(final_geojson)

    final_ts = acts_dir / f"{final_id}.timeseries.json"
    if pending_ts.exists():
        pending_ts.rename(final_ts)

    return final_id


def cleanup_pending(output_dir: Path, activity_id: str, source_hash: str) -> None:
    """Remove pending files for a losing activity (the one not chosen as canonical)."""
    acts_dir = output_dir / "activities"
    tag = source_hash[-8:] if source_hash else "unknown"
    for suffix in (".pending.json", ".pending.geojson", ".pending.timeseries.json"):
        p = acts_dir / f"{activity_id}.{tag}{suffix}"
        p.unlink(missing_ok=True)


def build_summary(
    activity: ParsedActivity,
    metrics: ComputedMetrics,
    activity_id: str,
    privacy: str = "public",
) -> dict:
    """Build the Activity Summary object for index.json."""
    has_gps = metrics.bbox is not None and privacy not in ("no_gps",)
    return {
        "id": activity_id,
        "title": activity.title or _auto_title(activity),
        "sport": activity.sport,
        "sub_sport": activity.sub_sport,
        "started_at": activity.started_at.isoformat(),
        "distance_m": metrics.distance_m,
        "duration_s": metrics.duration_s,
        "moving_time_s": metrics.moving_time_s,
        "elevation_gain_m": metrics.elevation_gain_m,
        "avg_speed_kmh": metrics.avg_speed_kmh,
        "max_speed_kmh": metrics.max_speed_kmh,
        "avg_hr_bpm": metrics.avg_hr_bpm,
        "max_hr_bpm": metrics.max_hr_bpm,
        "avg_cadence_rpm": metrics.avg_cadence_rpm,
        "avg_power_w": metrics.avg_power_w,
        "mmp": metrics.mmp,
        "best_efforts": metrics.best_efforts,
        "best_climb_m": metrics.best_climb_m,
        "source": _infer_source(activity),
        "privacy": privacy,
        "detail_url": f"activities/{activity_id}.json",
        "track_url": f"activities/{activity_id}.geojson" if has_gps else None,
        # Small track preview for card thumbnails — no separate fetch needed
        "preview_coords": preview_coords(activity.points) if has_gps else None,
    }


def write_athlete_json(summaries: list[dict], output_dir: Path, athlete_config: dict) -> None:
    """Aggregate per-activity MMP curves and personal records into athlete.json."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    def _cutoff_iso(days: int) -> str:
        from datetime import timedelta
        return (now - timedelta(days=days)).isoformat()

    cutoff_365 = _cutoff_iso(365)
    cutoff_90  = _cutoff_iso(90)

    # ── MMP aggregation ───────────────────────────────────────────────────────

    def _merge_mmps(activity_mmps: list[list[list[int]]]) -> list[list[int]]:
        best: dict[int, int] = {}
        for mmp in activity_mmps:
            for d, w in mmp:
                if d not in best or w > best[d]:
                    best[d] = w
        return [[d, w] for d, w in sorted(best.items())]

    all_mmps = [s["mmp"] for s in summaries if s.get("mmp")]
    mmps_365 = [s["mmp"] for s in summaries if s.get("mmp") and s["started_at"] >= cutoff_365]
    mmps_90  = [s["mmp"] for s in summaries if s.get("mmp") and s["started_at"] >= cutoff_90]

    # ── Personal records aggregation ──────────────────────────────────────────
    # records[sport][distance_km] = {time_s, activity_id, started_at, title}
    # best_climb[activity_id] = {climb_m, started_at, title}

    SPORTS = ["running", "cycling", "swimming", "hiking", "walking", "skiing", "other"]
    records: dict[str, dict[float, dict]] = {s: {} for s in SPORTS}
    best_climb: list[dict] = []   # top 10 best climbs for cycling

    for s in summaries:
        sport = s.get("sport", "other")
        act_id = s.get("id", "")
        started = s.get("started_at", "")
        title = s.get("title", "")

        # Distance-based best efforts
        for d_km, t_s in (s.get("best_efforts") or []):
            bucket = records.get(sport, {})
            existing = bucket.get(d_km)
            if existing is None or t_s < existing["time_s"]:
                bucket[d_km] = {
                    "time_s": t_s,
                    "activity_id": act_id,
                    "started_at": started,
                    "title": title,
                }
            records[sport] = bucket

        # Best climb (cycling only) — collect all, trim to top 10 after loop
        climb = s.get("best_climb_m")
        if climb and sport == "cycling":
            best_climb.append({
                "climb_m": climb,
                "activity_id": act_id,
                "started_at": started,
                "title": title,
            })

        # Hiking / walking: track longest distance and most elevation from summaries
        if sport in ("hiking", "walking"):
            dist = s.get("distance_m") or 0
            elev = s.get("elevation_gain_m") or 0
            for metric, key, val in [("longest_m", "distance_m", dist),
                                      ("most_elevation_m", "elevation_gain_m", elev)]:
                bucket = records[sport]
                existing = bucket.get(metric)
                if val and (existing is None or val > existing.get("value", 0)):
                    bucket[metric] = {
                        "value": val,
                        "activity_id": act_id,
                        "started_at": started,
                        "title": title,
                    }
                records[sport] = bucket

    # Serialise records: convert float keys to strings for JSON
    def _serialise_sport_records(bucket: dict) -> dict:
        return {str(k): v for k, v in bucket.items()}

    athlete = {
        "bas_version": "1.0",
        "generated_at": now.isoformat(),
        "power_curve": {
            "all_time":  _merge_mmps(all_mmps) if all_mmps else None,
            "last_365d": _merge_mmps(mmps_365) if mmps_365 else None,
            "last_90d":  _merge_mmps(mmps_90)  if mmps_90  else None,
        },
        "records": {
            sport: _serialise_sport_records(records[sport])
            for sport in SPORTS
            if records[sport]
        },
        "best_climbs": sorted(best_climb, key=lambda x: x["climb_m"], reverse=True)[:10],
        **athlete_config,
    }
    (output_dir / "athlete.json").write_text(
        json.dumps(athlete, indent=2, ensure_ascii=False)
    )


def write_index(summaries: list[dict], output_dir: Path, owner: dict) -> None:
    """Write index.json (sorted newest first)."""
    sorted_summaries = sorted(
        summaries,
        key=lambda s: s["started_at"],
        reverse=True,
    )
    index = {
        "bas_version": "1.0",
        "owner": owner,
        "generated_at": _now_iso(),
        "shards": [],
        "activities": sorted_summaries,
    }
    (output_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False)
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _auto_title(activity: ParsedActivity) -> str:
    ts = activity.started_at
    hour = ts.hour
    if 5 <= hour < 12:
        time_of_day = "Morning"
    elif 12 <= hour < 17:
        time_of_day = "Afternoon"
    elif 17 <= hour < 21:
        time_of_day = "Evening"
    else:
        time_of_day = "Night"
    sport = activity.sport.capitalize()
    return f"{time_of_day} {sport}"


def _infer_source(activity: ParsedActivity) -> str | None:
    if activity.strava_id:
        return "strava_export"
    name = activity.source_file.lower()
    # Karoo uses UUID-style names
    if "activity" in name and len(name.split(".")) >= 3:
        return "karoo"
    if name.endswith(".fit") or name.endswith(".fit.gz"):
        return "fit_file"
    if name.endswith(".gpx") or name.endswith(".gpx.gz"):
        return "gpx_file"
    if name.endswith(".tcx") or name.endswith(".tcx.gz"):
        return "tcx_file"
    return None


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:60]


def _serialise_lap(lap: LapData) -> dict:
    return {
        "index": lap.index,
        "started_at": lap.started_at.isoformat(),
        "duration_s": lap.duration_s,
        "distance_m": lap.distance_m,
        "elevation_gain_m": lap.elevation_gain_m,
        "avg_speed_kmh": lap.avg_speed_kmh,
        "avg_hr_bpm": lap.avg_hr_bpm,
        "avg_power_w": lap.avg_power_w,
    }
