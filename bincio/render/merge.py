"""Apply sidecar .md edits to BAS JSON files.

Produces data_dir/_merged/ — a mirror of data_dir where:
- Files without sidecars are symlinked to the originals (cheap, preserves extracted data)
- Files with sidecars are written as merged copies
- index.json is rewritten with private filtering + highlight sort

This keeps data_dir/activities/*.json pristine (re-running extract never clobbers
user edits, and removing a sidecar always reverts fully on the next render).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml


def parse_sidecar(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, markdown_body) from a sidecar .md file."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = re.split(r"^---[ \t]*$", text, maxsplit=2, flags=re.MULTILINE)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            return fm, parts[2].strip()
    return {}, text.strip()


def apply_sidecar(detail: dict, fm: dict, body: str) -> dict:
    """Apply sidecar overrides to a detail JSON dict. Returns a modified copy."""
    d = dict(detail)
    d.setdefault("custom", {})
    d["custom"] = dict(d["custom"])  # don't mutate original

    if "title" in fm:
        d["title"] = str(fm["title"])
    if "sport" in fm:
        d["sport"] = str(fm["sport"])
    if "gear" in fm:
        d["gear"] = str(fm["gear"]) if fm["gear"] else d.get("gear")
    if body:
        d["description"] = body
    elif "description" in fm:
        d["description"] = str(fm["description"])
    if "highlight" in fm:
        d["custom"]["highlight"] = bool(fm["highlight"])
    if "private" in fm:
        d["privacy"] = "unlisted" if fm["private"] else detail.get("privacy", "public")
    if "hide_stats" in fm:
        d["custom"]["hide_stats"] = [str(s) for s in (fm["hide_stats"] or [])]

    return d


def _apply_sidecar_summary(summary: dict, fm: dict) -> dict:
    """Apply sidecar overrides to an index summary entry."""
    s = dict(summary)
    s.setdefault("custom", {})
    s["custom"] = dict(s["custom"])

    if "title" in fm:
        s["title"] = str(fm["title"])
    if "sport" in fm:
        s["sport"] = str(fm["sport"])
    if "highlight" in fm:
        s["custom"]["highlight"] = bool(fm["highlight"])
    if "private" in fm:
        s["privacy"] = "unlisted" if fm["private"] else summary.get("privacy", "public")

    return s


def merge_one(data_dir: Path, activity_id: str) -> None:
    """Apply (or remove) sidecar overrides for a single activity.

    Updates data_dir/_merged/activities/{id}.json and rewrites
    _merged/index.json.  Faster than merge_all() for interactive edits
    because it touches only one activity file instead of rebuilding the
    whole _merged/activities/ directory.

    Use merge_all() for bulk operations (first run, Strava sync, etc.).
    """
    edits_dir  = data_dir / "edits"
    acts_dir   = data_dir / "activities"
    merged_dir = data_dir / "_merged"
    merged_acts = merged_dir / "activities"
    merged_acts.mkdir(parents=True, exist_ok=True)

    src = acts_dir / f"{activity_id}.json"
    if not src.exists():
        return

    dest = merged_acts / f"{activity_id}.json"

    # Determine if a sidecar or image list applies to this activity
    sidecar_path = edits_dir / f"{activity_id}.md" if edits_dir.exists() else None
    images_dir   = edits_dir / "images" / activity_id if edits_dir.exists() else None
    has_sidecar  = sidecar_path is not None and sidecar_path.exists()
    image_files: list[str] = []
    if images_dir and images_dir.exists():
        image_files = sorted(
            p.name for p in images_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )

    needs_merge = has_sidecar or bool(image_files)

    # Symlink the timeseries file (never merged — always points to the extract output)
    ts_src = acts_dir / f"{activity_id}.timeseries.json"
    ts_dest = merged_acts / f"{activity_id}.timeseries.json"
    if ts_dest.exists() or ts_dest.is_symlink():
        ts_dest.unlink()
    if ts_src.exists():
        ts_dest.symlink_to(ts_src.resolve())

    # Remove the old dest (symlink or file) before writing the new one
    if dest.exists() or dest.is_symlink():
        dest.unlink()

    if needs_merge:
        detail = json.loads(src.read_text(encoding="utf-8"))
        if has_sidecar:
            fm, body = parse_sidecar(sidecar_path)  # type: ignore[arg-type]
            detail = apply_sidecar(detail, fm, body)
        if image_files:
            detail["custom"] = dict(detail.get("custom") or {})
            detail["custom"]["images"] = image_files
        dest.write_text(json.dumps(detail, indent=2, ensure_ascii=False))
    else:
        dest.symlink_to(src.resolve())

    # Rewrite index — load the full sidecar map so all summaries stay consistent
    index_path = data_dir / "index.json"
    if not index_path.exists():
        return

    all_sidecars: dict[str, tuple[dict, str]] = {}
    if edits_dir and edits_dir.exists():
        for md_path in edits_dir.glob("*.md"):
            all_sidecars[md_path.stem] = parse_sidecar(md_path)

    index = json.loads(index_path.read_text(encoding="utf-8"))
    activities = []
    for s in index.get("activities", []):
        aid = s.get("id", "")
        if aid in all_sidecars:
            fm, _ = all_sidecars[aid]
            s = _apply_sidecar_summary(s, fm)
        activities.append(s)

    activities.sort(key=lambda a: a.get("started_at", ""), reverse=True)
    activities.sort(key=lambda a: 0 if a.get("custom", {}).get("highlight") else 1)

    _write_year_shards(merged_dir, activities, index)


def merge_all(data_dir: Path) -> int:
    """Build data_dir/_merged/ with all sidecar overrides applied.

    Returns the number of sidecars found and applied.
    """
    edits_dir = data_dir / "edits"
    acts_dir = data_dir / "activities"
    merged_dir = data_dir / "_merged"
    merged_acts = merged_dir / "activities"

    # Collect sidecars upfront
    sidecars: dict[str, tuple[dict, str]] = {}
    if edits_dir.exists():
        for md_path in sorted(edits_dir.glob("*.md")):
            sidecars[md_path.stem] = parse_sidecar(md_path)

    # Collect image lists — activities with uploaded images get custom.images even
    # if they have no sidecar text yet
    image_lists: dict[str, list[str]] = {}
    images_root = edits_dir / "images" if edits_dir.exists() else None
    if images_root and images_root.exists():
        for img_dir in sorted(images_root.iterdir()):
            if img_dir.is_dir():
                files = sorted(
                    p.name for p in img_dir.iterdir()
                    if p.is_file() and not p.name.startswith(".")
                )
                if files:
                    image_lists[img_dir.name] = files

    to_merge = set(sidecars) | set(image_lists)

    # Wipe and recreate _merged/activities/
    if merged_acts.exists():
        shutil.rmtree(merged_acts)
    merged_acts.mkdir(parents=True)

    # Mirror activities/ — symlink unmodified, write merged copies for overridden
    if acts_dir.exists():
        for src in sorted(acts_dir.iterdir()):
            if not src.is_file():
                continue
            dest = merged_acts / src.name
            activity_id = src.stem
            if src.suffix == ".json" and activity_id in to_merge:
                detail = json.loads(src.read_text(encoding="utf-8"))
                if activity_id in sidecars:
                    fm, body = sidecars[activity_id]
                    detail = apply_sidecar(detail, fm, body)
                if activity_id in image_lists:
                    detail["custom"] = dict(detail.get("custom") or {})
                    detail["custom"]["images"] = image_lists[activity_id]
                dest.write_text(json.dumps(detail, indent=2, ensure_ascii=False))
            else:
                dest.symlink_to(src.resolve())

    # Mirror edits/images/ → _merged/activities/images/ so the site can serve them
    if images_root and images_root.exists():
        merged_images = merged_acts / "images"
        merged_images.mkdir(exist_ok=True)
        for img_dir in images_root.iterdir():
            if img_dir.is_dir():
                dest_img = merged_images / img_dir.name
                if not dest_img.exists():
                    dest_img.symlink_to(img_dir.resolve())

    # Produce merged athlete.json — base from extract overlaid with edits/athlete.yaml
    athlete_src = data_dir / "athlete.json"
    athlete_dest = merged_dir / "athlete.json"
    if athlete_dest.exists() or athlete_dest.is_symlink():
        athlete_dest.unlink()
    if athlete_src.exists():
        athlete_edits_path = data_dir / "edits" / "athlete.yaml"
        if athlete_edits_path.exists():
            try:
                import yaml as _yaml
                edits = _yaml.safe_load(athlete_edits_path.read_text(encoding="utf-8")) or {}
            except Exception:
                edits = {}
        else:
            edits = {}
        _ATHLETE_EDITABLE = {"max_hr", "ftp_w", "hr_zones", "power_zones", "seasons", "gear"}
        if edits:
            athlete_data = json.loads(athlete_src.read_text(encoding="utf-8"))
            athlete_data.update({k: v for k, v in edits.items() if k in _ATHLETE_EDITABLE})
            athlete_dest.write_text(json.dumps(athlete_data, indent=2, ensure_ascii=False))
        else:
            athlete_dest.symlink_to(athlete_src.resolve())

    # Write merged index.json (private filtered, highlight sorted)
    index_path = data_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        activities = []
        for s in index.get("activities", []):
            aid = s.get("id", "")
            if aid in sidecars:
                fm, _ = sidecars[aid]
                s = _apply_sidecar_summary(s, fm)
            activities.append(s)

        # "unlisted" (and legacy "private") activities are kept in the index so
        # the owner can reach them by direct URL; the feed UI filters them out
        # for non-owners client-side.
        # Sort: newest first, then bring highlighted activities to the top.
        activities.sort(key=lambda a: a.get("started_at", ""), reverse=True)
        activities.sort(key=lambda a: 0 if a.get("custom", {}).get("highlight") else 1)

        _write_year_shards(merged_dir, activities, index)
    else:
        # Remove any stale year shard files if the source index disappeared
        for f in merged_dir.glob("index-*.json"):
            f.unlink()
        if (merged_dir / "index.json").exists():
            (merged_dir / "index.json").unlink()

    return len(sidecars)


# Fields only needed for athlete.json aggregation at extract time — they add
# bulk to every summary entry but are never read by the feed UI.
_FEED_STRIP = {"best_efforts", "best_climb_m", "source"}


def _write_year_shards(merged_dir: Path, activities: list[dict], index_meta: dict) -> None:
    """Split activities by year and write index-{year}.json shards.

    Replaces merged_dir/index.json with a shard manifest so the feed can
    load only the most-recent year on first paint and fetch older years lazily.
    """
    from collections import defaultdict

    # Remove stale year shard files from previous runs
    for f in merged_dir.glob("index-*.json"):
        f.unlink()

    by_year: dict[str, list[dict]] = defaultdict(list)
    for a in activities:
        year = (a.get("started_at") or "")[:4] or "unknown"
        # Strip aggregation-only fields to keep shard files small
        slim = {k: v for k, v in a.items() if k not in _FEED_STRIP}
        by_year[year].append(slim)

    years = sorted(by_year.keys(), reverse=True)  # newest first
    shards = []
    for year in years:
        shard_doc = {
            **{k: v for k, v in index_meta.items() if k not in ("activities", "shards")},
            "shards": [],
            "activities": by_year[year],
        }
        fname = f"index-{year}.json"
        (merged_dir / fname).write_text(json.dumps(shard_doc, indent=2, ensure_ascii=False))
        shards.append({"url": fname, "year": int(year) if year.isdigit() else 0,
                        "count": len(by_year[year])})

    root_doc = {
        **{k: v for k, v in index_meta.items() if k not in ("activities", "shards")},
        "shards": shards,
        "activities": [],
    }
    (merged_dir / "index.json").write_text(json.dumps(root_doc, indent=2, ensure_ascii=False))


FEED_PAGE_SIZE = 50

# Extra fields stripped from the combined feed — preview_coords is the biggest
# contributor (~24% of shard size) but the feed cards need it for thumbnails,
# so we keep it.  mmp is never displayed in feed cards.
_COMBINED_FEED_STRIP = _FEED_STRIP | {"mmp"}


def write_combined_feed(data_dir: Path) -> int:
    """Build data_dir/feed.json — the N most recent activities across all users.

    The global feed page loads this single file instead of resolving 20+ user
    shards recursively.  Returns the number of activities written.
    """
    user_dirs = sorted(
        p for p in data_dir.iterdir()
        if p.is_dir() and (p / "activities").exists()
    )

    all_activities: list[dict] = []
    for user_dir in user_dirs:
        handle = user_dir.name
        merged = user_dir / "_merged"
        index_path = merged / "index.json" if merged.exists() else user_dir / "index.json"
        if not index_path.exists():
            continue

        index = json.loads(index_path.read_text(encoding="utf-8"))
        shards = index.get("shards", [])
        activities = index.get("activities", [])

        if shards:
            year_shards = [s for s in shards if re.match(r"index-\d{4}\.json$", s.get("url", ""))]
            base = index_path.parent
            for shard in year_shards:
                shard_path = base / shard["url"]
                if shard_path.exists():
                    shard_data = json.loads(shard_path.read_text(encoding="utf-8"))
                    for a in shard_data.get("activities", []):
                        a_tagged = {**a, "handle": handle}
                        detail_url = a_tagged.get("detail_url", "")
                        if detail_url and not detail_url.startswith("http") and not detail_url.startswith("/"):
                            merged_rel = f"{handle}/_merged/" if merged.exists() else f"{handle}/"
                            a_tagged["detail_url"] = merged_rel + detail_url
                        track_url = a_tagged.get("track_url", "")
                        if track_url and not track_url.startswith("http") and not track_url.startswith("/"):
                            merged_rel = f"{handle}/_merged/" if merged.exists() else f"{handle}/"
                            a_tagged["track_url"] = merged_rel + track_url
                        all_activities.append(a_tagged)
        else:
            for a in activities:
                all_activities.append({**a, "handle": handle})

    all_activities.sort(key=lambda a: a.get("started_at", ""), reverse=True)

    # Remove stale feed pages
    for f in data_dir.glob("feed*.json"):
        f.unlink()

    if not all_activities:
        return 0

    pages = [all_activities[i:i + FEED_PAGE_SIZE] for i in range(0, len(all_activities), FEED_PAGE_SIZE)]
    for page_num, page in enumerate(pages):
        slim = [{k: v for k, v in a.items() if k not in _COMBINED_FEED_STRIP} for a in page]
        fname = "feed.json" if page_num == 0 else f"feed-{page_num + 1}.json"
        doc = {
            "bas_version": "1.0",
            "page": page_num + 1,
            "total_pages": len(pages),
            "total_activities": len(all_activities),
            "activities": slim,
        }
        (data_dir / fname).write_text(json.dumps(doc, indent=2, ensure_ascii=False))

    return len(all_activities)
