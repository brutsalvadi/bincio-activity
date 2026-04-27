"""bincio extract — CLI command."""

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from bincio.extract.config import ExtractConfig, default_config, load_config
from bincio.extract.dedup import ActivityRecord, DedupIndex
from bincio.extract.parsers.factory import is_supported

console = Console()


# ── per-worker state (set once via initializer, never re-pickled) ─────────────

_known_hashes: frozenset = frozenset()
_strava_lookup: dict = {}
_output_dir: Path = Path(".")
_privacy: str = "public"
_rdp_epsilon: float = 0.0001


def _worker_init(
    known_hashes: frozenset,
    strava_lookup: dict,
    output_dir: Path,
    privacy: str,
    rdp_epsilon: float,
) -> None:
    global _known_hashes, _strava_lookup, _output_dir, _privacy, _rdp_epsilon
    _known_hashes = known_hashes
    _strava_lookup = strava_lookup
    _output_dir = output_dir
    _privacy = privacy
    _rdp_epsilon = rdp_epsilon


def _process_file(path: Path) -> dict:
    """Runs inside a worker process. Only receives a Path (tiny pickle).
    All heavy shared data (_known_hashes, _strava_lookup, etc.) is already
    in the worker's memory from the initializer — zero per-task overhead.

    Writes to pending files (not final paths) so the main process can
    arbitrate collisions and pick the best version.
    """
    from bincio.extract.metrics import compute
    from bincio.extract.parsers.factory import parse_file
    from bincio.extract.writer import build_summary, make_activity_id, write_activity

    try:
        activity = parse_file(path)
    except Exception as exc:
        return {"status": "error", "path": str(path), "error": str(exc)}

    # Exact-duplicate check (free — just a set lookup)
    if activity.source_hash in _known_hashes:
        return {"status": "duplicate"}

    # Enrich from Strava CSV (CSV is authoritative for sport on Strava activities)
    row = _strava_lookup.get(activity.source_file)
    if row:
        if not activity.title:
            activity.title = row.get("Activity Name", "").strip() or None
        if not activity.description:
            activity.description = row.get("Activity Description", "").strip() or None
        if not activity.strava_id:
            activity.strava_id = row.get("Activity ID", "").strip() or None
        csv_type = row.get("Activity Type", "").strip()
        if csv_type:
            from bincio.extract.sport import normalise_sport
            activity.sport = normalise_sport(csv_type)

    try:
        metrics = compute(activity)
        activity_id = write_activity(
            activity, metrics, _output_dir,
            privacy=_privacy,
            rdp_epsilon=_rdp_epsilon,
            pending=True,
        )
        summary = build_summary(activity, metrics, activity_id, _privacy)
    except Exception as exc:
        return {"status": "error", "path": str(path), "error": str(exc)}

    # Quality signals for the main process to compare competing results
    sensor_channels = sum(1 for v in [
        metrics.avg_hr_bpm, metrics.avg_power_w, metrics.avg_cadence_rpm,
    ] if v is not None)

    return {
        "status": "ok",
        "summary": summary,
        "id": activity_id,
        "hash": activity.source_hash,
        "started_at": activity.started_at.isoformat(),
        "distance_m": metrics.distance_m,
        "source": summary.get("source"),
        "mmp": metrics.mmp,
        "point_count": len(activity.points),
        "sensor_channels": sensor_channels,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--config", "config_path", type=click.Path(exists=True), default=None,
              help="Path to extract_config.yaml (default: ./extract_config.yaml).")
@click.option("--input", "input_dir", type=click.Path(exists=True), default=None,
              help="Input directory (overrides config).")
@click.option("--output", "output_dir", type=click.Path(), default=None,
              help="Output directory (overrides config).")
@click.option("--file", "single_file", type=click.Path(exists=True), default=None,
              help="Process a single file and print JSON to stdout.")
@click.option("--since", default=None, metavar="YYYY-MM-DD",
              help="Only process files modified after this date.")
@click.option("--workers", default=None, type=int,
              help="Parallel worker processes (default: CPU count).")
@click.option("--dev", "dev_sample", default=None, type=int, metavar="N",
              help="Dev mode: sample N files evenly across the full list, output to /tmp/bincio_dev/.")
def extract(
    config_path: Optional[str],
    input_dir: Optional[str],
    output_dir: Optional[str],
    single_file: Optional[str],
    since: Optional[str],
    workers: Optional[int],
    dev_sample: Optional[int],
) -> None:
    """Parse GPX/FIT/TCX files and write BAS JSON data store."""

    if single_file:
        _process_single(Path(single_file))
        return

    cfg = _resolve_config(config_path, input_dir, output_dir)

    if dev_sample is not None:
        cfg.output_dir = Path("/tmp/bincio_dev")
        cfg.incremental = False
        console.print(f"[yellow]Dev mode:[/yellow] sampling {dev_sample} files → [cyan]{cfg.output_dir}[/cyan]")

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    files = _collect_files(cfg, since)
    if not files:
        console.print("[yellow]No supported files found.[/yellow]")
        return

    if dev_sample is not None:
        total = len(files)
        files = _sample_diverse(files, dev_sample)
        console.print(f"Sampled [bold]{len(files)}[/bold] files from {total} total.")
    else:
        console.print(f"Found [bold]{len(files)}[/bold] activity files.")

    # Build strava lookup once (serialised dict, sent to workers via initializer)
    strava_lookup: dict = {}
    if cfg.metadata_csv and cfg.metadata_csv.exists():
        from bincio.extract.strava_csv import StravaMetadata
        strava_lookup = StravaMetadata(cfg.metadata_csv)._by_filename
        console.print(f"Loaded Strava metadata from [cyan]{cfg.metadata_csv.name}[/cyan].")

    dedup = DedupIndex(output_dir=cfg.output_dir)
    # Only skip files whose output actually exists — the cache can outlive a
    # --fresh wipe or manual deletion of the activities directory.
    _acts_dir = cfg.output_dir / "activities"
    known_hashes: frozenset = frozenset(
        h for h, act_id in dedup._by_hash.items()
        if (_acts_dir / f"{act_id}.json").exists()
    )

    n_workers = workers or cfg.workers or os.cpu_count() or 4
    console.print(f"Using [bold]{n_workers}[/bold] worker processes.")

    owner = {"handle": cfg.owner_handle, "display_name": cfg.owner_display_name}
    if cfg.athlete:
        ath = cfg.athlete
        owner["athlete"] = {
            k: v for k, v in {
                "max_hr": ath.max_hr,
                "ftp_w": ath.ftp_w,
                "hr_zones": ath.hr_zones,
                "power_zones": ath.power_zones,
            }.items() if v is not None
        }
    summaries: list[dict] = []
    errors: list[tuple[str, str]] = []
    skipped = 0
    # Collect all pending results, grouped by activity_id for collision arbitration
    pending_by_id: dict[str, list[dict]] = {}

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=len(files))

        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_worker_init,
            initargs=(known_hashes, strava_lookup, cfg.output_dir, cfg.default_privacy, cfg.track.rdp_epsilon),
        ) as pool:
            futures = {pool.submit(_process_file, f): f for f in files}
            for future in as_completed(futures):
                progress.advance(task)
                result = future.result()

                if result["status"] == "duplicate":
                    skipped += 1
                elif result["status"] == "error":
                    errors.append((result["path"], result["error"]))
                else:
                    pending_by_id.setdefault(result["id"], []).append(result)

    # ── Arbitrate collisions and finalize pending files ───────────────────────
    from bincio.extract.writer import (
        activity_quality, cleanup_pending, finalize_pending, write_athlete_json, write_index,
    )

    for activity_id, candidates in pending_by_id.items():
        # Pick the best candidate by quality score
        candidates.sort(key=activity_quality, reverse=True)
        winner = candidates[0]

        # Clean up losing candidates' pending files
        for loser in candidates[1:]:
            cleanup_pending(cfg.output_dir, activity_id, loser["hash"])
            skipped += 1

        # Near-duplicate check against already-known activities
        from datetime import datetime
        started_at = datetime.fromisoformat(winner["started_at"])
        near_id = dedup.find_near_duplicate(started_at, winner["distance_m"])

        if near_id:
            canonical = dedup.pick_canonical(near_id, winner.get("source"))
            if canonical != "__new__":
                # Existing is better — finalize winner as duplicate, then patch it
                final_id = finalize_pending(cfg.output_dir, activity_id, winner["hash"])
                _patch_duplicate_of(cfg.output_dir, final_id, near_id)
                skipped += 1
                continue
            # New is better — patch the existing one as duplicate
            final_id = finalize_pending(cfg.output_dir, activity_id, winner["hash"])
            _patch_duplicate_of(cfg.output_dir, near_id, final_id)
            dedup._records[near_id].duplicate_of = final_id
        else:
            final_id = finalize_pending(cfg.output_dir, activity_id, winner["hash"])

        # Update summary with the finalized ID (may include hash suffix)
        summary = winner["summary"]
        if final_id != activity_id:
            summary = dict(summary)
            summary["id"] = final_id
            summary["detail_url"] = f"activities/{final_id}.json"
            if summary.get("track_url"):
                summary["track_url"] = f"activities/{final_id}.geojson"

        dedup.register(ActivityRecord(
            id=final_id,
            source_hash=winner["hash"],
            started_at=started_at,
            distance_m=winner["distance_m"],
            source=winner.get("source"),
        ))
        summaries.append(summary)

    existing = _load_existing_summaries(cfg.output_dir)
    merged = {s["id"]: s for s in existing}
    for s in summaries:
        merged[s["id"]] = s
    all_summaries = list(merged.values())
    write_index(all_summaries, cfg.output_dir, owner)

    athlete_config: dict = {}
    if cfg.athlete:
        ath = cfg.athlete
        athlete_config = {k: v for k, v in {
            "max_hr": ath.max_hr,
            "ftp_w": ath.ftp_w,
            "hr_zones": ath.hr_zones,
            "power_zones": ath.power_zones,
        }.items() if v is not None}
    write_athlete_json(all_summaries, cfg.output_dir, athlete_config)

    dedup.save()

    console.print(
        f"\n[green]Done.[/green] "
        f"Processed [bold]{len(summaries)}[/bold] activities, "
        f"skipped [bold]{skipped}[/bold] (already up to date), "
        f"errors [bold]{len(errors)}[/bold]."
    )
    if errors:
        console.print("\n[red]Errors:[/red]")
        for path, msg in errors[:20]:
            console.print(f"  {Path(path).name}: {msg}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more.")


# ── helpers ───────────────────────────────────────────────────────────────────

def _process_single(path: Path) -> None:
    from bincio.extract.metrics import compute
    from bincio.extract.parsers.factory import parse_file
    from bincio.extract.writer import build_summary, make_activity_id
    try:
        activity = parse_file(path)
        metrics = compute(activity)
        activity_id = make_activity_id(activity)
        click.echo(json.dumps(build_summary(activity, metrics, activity_id), indent=2))
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


def _resolve_config(
    config_path: Optional[str],
    input_dir: Optional[str],
    output_dir: Optional[str],
) -> ExtractConfig:
    if config_path:
        cfg = load_config(Path(config_path))
    elif Path("extract_config.yaml").exists():
        cfg = load_config(Path("extract_config.yaml"))
    elif input_dir:
        cfg = default_config(
            Path(input_dir).expanduser(),
            Path(output_dir or "./bincio_data").expanduser(),
        )
    else:
        raise click.UsageError(
            "Provide --config, --input, or an extract_config.yaml in the current directory."
        )
    if input_dir:
        cfg.input_dirs = [Path(input_dir).expanduser()]
    if output_dir:
        cfg.output_dir = Path(output_dir).expanduser()
    # Always write into {data_root}/{handle}/ so the data dir is always
    # instance-rooted and single/multi-user share the same layout.
    if cfg.output_dir.name != cfg.owner_handle:
        cfg.output_dir = cfg.output_dir / cfg.owner_handle
    return cfg


def _collect_files(cfg: ExtractConfig, since: Optional[str]) -> list[Path]:
    from datetime import datetime
    since_ts: Optional[float] = None
    if since:
        since_ts = datetime.strptime(since, "%Y-%m-%d").timestamp()
    files = []
    for d in cfg.input_dirs:
        if not d.exists():
            console.print(f"[yellow]Warning:[/yellow] input dir not found: {d}")
            continue
        for path in d.rglob("*"):
            if path.is_file() and is_supported(path):
                if not since_ts or path.stat().st_mtime >= since_ts:
                    files.append(path)
    return files


def _load_existing_summaries(output_dir: Path) -> list[dict]:
    p = output_dir / "index.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("activities", [])
    except Exception:
        return []


def _sample_diverse(files: list[Path], n: int) -> list[Path]:
    """Return n files sampled evenly across the sorted list for date/format diversity."""
    if len(files) <= n:
        return files
    files = sorted(files)
    step = len(files) / n
    return [files[int(i * step)] for i in range(n)]


def _patch_duplicate_of(output_dir: Path, activity_id: str, canonical_id: str) -> None:
    p = output_dir / "activities" / f"{activity_id}.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text())
        data["duplicate_of"] = canonical_id
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("_patch_duplicate_of failed for %s: %s", activity_id, e)
