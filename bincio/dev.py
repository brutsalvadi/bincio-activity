"""bincio dev — start the full local development environment.

Runs bincio serve (API) in a background thread and astro dev in the
foreground. One command replaces the two-terminal setup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

console = Console()


def _find_site_dir(explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not (p / "package.json").exists():
            raise click.UsageError(f"No package.json in --site-dir {p}")
        return p
    for candidate in [Path.cwd() / "site", Path.cwd().parent / "site"]:
        if (candidate / "package.json").exists():
            return candidate
    raise click.UsageError(
        "Could not find the Astro site directory. Pass --site-dir."
    )


def _find_data_dir(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    auto_config = Path.cwd() / "extract_config.yaml"
    if auto_config.exists():
        import yaml
        raw = yaml.safe_load(auto_config.read_text()) or {}
        out = raw.get("output", {}).get("dir")
        if out:
            return Path(out).expanduser().resolve()
    for candidate in [Path.cwd() / "bincio_data", Path.cwd().parent / "bincio_data"]:
        if candidate.exists() and _user_dirs(candidate):
            return candidate
    raise click.UsageError(
        "Could not find a data directory with user subdirectories. "
        "Run `bincio extract` first, or pass --data-dir."
    )


def _ensure_npm(site: Path) -> None:
    if not (site / "node_modules").exists():
        console.print("Running [cyan]npm install[/cyan]…")
        subprocess.run(["npm", "install"], cwd=site, check=True)


def _user_dirs(data: Path) -> list[Path]:
    return sorted(p for p in data.iterdir() if p.is_dir() and (p / "activities").exists())


def _merge_all_users(data: Path) -> None:
    from bincio.render.cli import _merge_edits, _write_root_manifest
    _merge_edits(data)
    _write_root_manifest(data)


def _start_serve(data: Path, api_port: int, site: Path) -> None:
    """Start bincio serve in a background thread."""
    import uvicorn
    import bincio.serve.server as srv

    srv.data_dir = data
    srv.site_dir = site

    config = uvicorn.Config(
        srv.app,
        host="127.0.0.1",
        port=api_port,
        log_level="warning",   # quiet — astro dev output takes priority
    )
    server = uvicorn.Server(config)
    server.run()


def _watch_data(data: Path) -> None:
    """Watch the data directory for sidecar/activity changes and re-merge.

    Monitors every user's edits/ and activities/ subdirectories. When any file
    changes (new activity extracted, sidecar saved), re-runs merge_all for that
    user so the _merged/ symlink tree stays current. Astro dev picks up the
    result automatically because public/data is a symlink into the live data dir.

    Uses watchfiles (bundled with uvicorn[standard]) for efficient OS-level
    file watching — no polling.
    """
    from watchfiles import watch, Change

    watch_paths = []
    for user_dir in _user_dirs(data):
        for sub in ("edits", "activities"):
            p = user_dir / sub
            p.mkdir(exist_ok=True)
            watch_paths.append(p)

    if not watch_paths:
        return

    console.print(f"  [dim]Watching {len(watch_paths)} director{'y' if len(watch_paths) == 1 else 'ies'} for changes…[/dim]")

    # Build a map from path prefix → user dir for targeted merge
    prefix_to_user: dict[str, Path] = {}
    for user_dir in _user_dirs(data):
        for sub in ("edits", "activities"):
            prefix_to_user[str(user_dir / sub)] = user_dir

    for changes in watch(*watch_paths, yield_on_timeout=False):
        # Find which users were affected
        affected: set[Path] = set()
        for change_type, path in changes:
            # Skip timeseries / geojson / index churn written by merge itself
            if any(path.endswith(s) for s in (".timeseries.json", ".geojson", "index.json")):
                continue
            for prefix, user_dir in prefix_to_user.items():
                if path.startswith(prefix):
                    affected.add(user_dir)
                    break

        if not affected:
            continue

        for user_dir in affected:
            handle = user_dir.name
            try:
                from bincio.render.merge import merge_all
                merge_all(user_dir)
                console.print(f"  [dim]↺  {handle}: merged[/dim]")
            except Exception as exc:
                console.print(f"  [yellow]⚠  {handle}: merge failed — {exc}[/yellow]")


@click.command("dev")
@click.option("--data-dir", default=None, help="BAS data directory (must contain instance.db)")
@click.option("--site-dir", default=None, help="Astro project directory (default: ./site)")
@click.option("--port", default=4321, show_default=True, help="Astro dev server port")
@click.option("--api-port", default=4041, show_default=True, help="bincio serve API port")
def dev(
    data_dir: Optional[str],
    site_dir: Optional[str],
    port: int,
    api_port: int,
) -> None:
    """Start the local dev environment: bincio serve + astro dev.

    Equivalent to running both servers manually in two terminals.
    Requires `bincio init` to have been run first.

    \b
    Quick start:
      uv run bincio init --data-dir ./data --handle you --password secret
      uv run bincio extract --output ./data/you
      uv run bincio dev --data-dir ./data
    """
    data = _find_data_dir(data_dir)
    site = _find_site_dir(site_dir)

    has_auth = (data / "instance.db").exists()

    console.print(f"[bold]bincio dev[/bold]")
    console.print(f"  Data:    [cyan]{data}[/cyan]")
    console.print(f"  Site:    [cyan]{site}[/cyan]")
    if has_auth:
        console.print(f"  API:     [cyan]http://127.0.0.1:{api_port}[/cyan]")
    else:
        console.print(f"  Auth:    [yellow]none[/yellow] (single-user, no instance.db)")
    console.print(f"  Browser: [cyan]http://localhost:{port}[/cyan]")
    console.print()

    _ensure_npm(site)

    console.print("Merging sidecars…")
    _merge_all_users(data)

    # Symlink site/public/data → data dir
    public_data = site / "public" / "data"
    public_data.parent.mkdir(parents=True, exist_ok=True)
    if public_data.is_symlink():
        if public_data.resolve() != data.resolve():
            public_data.unlink()
            public_data.symlink_to(data)
    elif not public_data.exists():
        public_data.symlink_to(data)

    # Start bincio serve only when instance.db exists (auth / write API)
    if has_auth:
        console.print(f"Starting [cyan]bincio serve[/cyan] on port {api_port}…")
        t = threading.Thread(target=_start_serve, args=(data, api_port, site), daemon=True)
        t.start()

    # Watch data dir for sidecar/activity changes → auto-merge
    watcher = threading.Thread(target=_watch_data, args=(data,), daemon=True)
    watcher.start()

    # Build env for astro dev
    env = {
        **os.environ,
        "BINCIO_DATA_DIR": str(data),
        "PUBLIC_EDIT_URL": "",                          # empty = proxy /api/* to bincio serve
        "PUBLIC_EDIT_ENABLED": "true" if has_auth else "",  # show edit/upload UI in VPS mode
        "PUBLIC_MOBILE_APP": "",                        # Record/Convert tabs off by default
        "VITE_API_PORT": str(api_port),                 # picked up by astro.config.mjs
    }

    # Start astro dev in foreground (Ctrl+C stops everything)
    console.print(f"Starting [cyan]astro dev[/cyan] on port {port}…")
    console.print()
    try:
        subprocess.run(
            ["npm", "run", "dev", "--", "--port", str(port)],
            cwd=site,
            env=env,
        )
    except KeyboardInterrupt:
        pass
