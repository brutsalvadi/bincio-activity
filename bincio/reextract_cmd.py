"""bincio reextract-originals — re-extract activities from stored Strava originals."""

from __future__ import annotations

import ctypes
import gc
import json
import sys
from pathlib import Path

import click


def _emit(obj: dict) -> None:
    """Write a JSON progress line to stdout (flushed immediately)."""
    print(json.dumps(obj), flush=True)


# On Linux, malloc_trim(0) returns freed arenas to the OS, keeping RSS low.
# CPython's allocator otherwise holds onto freed memory indefinitely.
try:
    _libc = ctypes.CDLL("libc.so.6")
    def _trim_heap() -> None:
        _libc.malloc_trim(0)
except Exception:
    def _trim_heap() -> None:  # type: ignore[misc]
        pass

_GC_EVERY = 50  # call gc.collect() + malloc_trim every N activities


@click.command("reextract-originals")
@click.option("--data-dir", required=True, type=click.Path(), help="BAS data directory")
@click.option("--handle", required=True, help="User handle to re-extract for")
@click.option("--force", is_flag=True, default=False, help="Re-extract even if activity JSON already exists")
@click.option("--offset", default=0, type=int, help="Skip first N originals (for batch processing)")
@click.option("--limit", default=0, type=int, help="Process at most N originals then stop (0 = all)")
def reextract_originals(data_dir: str, handle: str, force: bool, offset: int, limit: int) -> None:
    """Re-extract activities from stored Strava originals (originals/strava/*.json).

    Prints one JSON object per line to stdout for streaming progress:
      {"type": "status", "message": "..."}
      {"type": "progress", "n": 1, "total": 2015, "name": "...", "status": "imported"|"skipped"|"error", ["detail": "..."]}
      {"type": "done", "imported": N, "skipped": N, "errors": N}
      {"type": "error", "message": "..."}
    """
    from bincio.extract.strava_api import strava_to_parsed
    from bincio.extract.metrics import compute as compute_metrics
    from bincio.extract.writer import (
        build_summary, make_activity_id, write_activity, write_index,
    )
    from bincio.render.merge import merge_all

    dd = Path(data_dir).expanduser().resolve()
    user_dir = dd / handle
    originals_dir = user_dir / "originals" / "strava"

    if not originals_dir.exists():
        _emit({"type": "error", "message": f"No Strava originals directory at {originals_dir}"})
        sys.exit(1)

    all_files = sorted(originals_dir.glob("*.json"))
    if not all_files:
        _emit({"type": "error", "message": "No Strava originals found"})
        sys.exit(1)

    # Apply offset/limit for batch processing
    batch = all_files[offset:] if not limit else all_files[offset: offset + limit]
    total_all = len(all_files)
    total = len(batch)
    original_files = batch

    _emit({"type": "status", "message": (
        f"Batch {offset + 1}–{offset + total} of {total_all}, starting extraction…"
        if offset or limit else
        f"Found {total_all} originals, starting extraction…"
    )})

    # Load existing index to get owner info and existing summaries
    index_path = user_dir / "index.json"
    try:
        existing_index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    except Exception:
        existing_index = {}
    owner = existing_index.get("owner", {"handle": handle})
    summaries: dict[str, dict] = {s["id"]: s for s in existing_index.get("activities", [])}

    imported = skipped = errors = 0

    for n, orig_path in enumerate(original_files, 1):
        try:
            raw = json.loads(orig_path.read_text(encoding="utf-8"))
            meta = raw.get("meta", {})
            streams = raw.get("streams", {})
            name = meta.get("name", orig_path.stem)

            parsed = strava_to_parsed(meta, streams)
            activity_id = make_activity_id(parsed)

            if not force and (user_dir / "activities" / f"{activity_id}.json").exists():
                skipped += 1
                _emit({"type": "progress", "n": n, "total": total, "name": name, "status": "skipped"})
            else:
                metrics = compute_metrics(parsed)
                ep = parsed.privacy if parsed.privacy is not None else "public"
                write_activity(parsed, metrics, user_dir, privacy=ep, rdp_epsilon=0.0001)
                summaries[activity_id] = build_summary(parsed, metrics, activity_id, ep)
                imported += 1
                _emit({"type": "progress", "n": n, "total": total, "name": name, "status": "imported"})

            # Explicitly free large objects; also free the raw JSON dict and streams
            raw = meta = streams = None  # type: ignore[assignment]
            try:
                del parsed, metrics
            except NameError:
                pass

        except Exception as exc:
            errors += 1
            _emit({"type": "progress", "n": n, "total": total, "name": orig_path.stem,
                   "status": "error", "detail": str(exc)})

        # Periodically reclaim freed memory from CPython's allocator arena
        if n % _GC_EVERY == 0:
            gc.collect()
            _trim_heap()

    # Final cleanup before the index write (which loads all summaries at once)
    gc.collect()
    _trim_heap()

    if imported > 0:
        _emit({"type": "status", "message": "Writing index…"})
        try:
            write_index(list(summaries.values()), user_dir, owner)
        except Exception as exc:
            _emit({"type": "error", "message": f"write_index failed: {exc}"})
            sys.exit(1)

        _emit({"type": "status", "message": "Running merge…"})
        try:
            merge_all(user_dir)
        except Exception as exc:
            _emit({"type": "error", "message": f"merge_all failed: {exc}"})
            sys.exit(1)

    _emit({"type": "done", "imported": imported, "skipped": skipped, "errors": errors})
