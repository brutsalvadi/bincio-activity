"""bincio edit — local edit server for activity sidecar files."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
from rich.console import Console

console = Console()


@click.command()
@click.option("--data-dir", default=None,
              help="BAS data store directory (output of bincio extract).")
@click.option("--port", default=4041, show_default=True,
              help="Port for the edit server.")
@click.option("--site-url", default="http://localhost:4321", show_default=True,
              help="URL of the Astro dev server (for the Back link).")
@click.option("--config", "config_path", default=None,
              help="Path to extract_config.yaml (reads output.dir from it).")
@click.option("--strava-client-id", default=None, envvar="STRAVA_CLIENT_ID",
              help="Strava API client ID (enables Strava sync in the UI). Also reads STRAVA_CLIENT_ID env var.")
@click.option("--strava-client-secret", default=None, envvar="STRAVA_CLIENT_SECRET",
              help="Strava API client secret. Also reads STRAVA_CLIENT_SECRET env var.")
@click.option("--dem-url", default=None, envvar="DEM_URL",
              help="Base URL of an Open-Elevation-compatible API (default: https://api.open-elevation.com).")
def edit(
    data_dir: Optional[str],
    port: int,
    site_url: str,
    config_path: Optional[str],
    strava_client_id: Optional[str],
    strava_client_secret: Optional[str],
    dem_url: Optional[str],
) -> None:
    """Start a local web UI for editing activity sidecar files.

    Writes sidecar .md files to <data-dir>/edits/ which bincio render picks
    up and applies at build time.

    Run alongside the Astro dev server:

    \b
        bincio render --serve      # port 4321  (or npm run dev)
        bincio edit                # port 4041
    """
    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "uvicorn is required for the edit server.\n"
            "Install with: uv sync --extra edit"
        )

    data = _resolve_data_dir(data_dir, config_path)

    # Fall back to extract_config.yaml for Strava credentials
    if not strava_client_id or not strava_client_secret:
        cfg_strava = _load_config(config_path).get("import", {}).get("strava", {})
        strava_client_id = strava_client_id or str(cfg_strava.get("client_id") or "")
        strava_client_secret = strava_client_secret or str(cfg_strava.get("client_secret") or "")

    console.print(f"Data dir: [cyan]{data}[/cyan]")
    console.print(f"Edit UI:  [cyan]http://localhost:{port}/edit/<activity-id>[/cyan]")
    console.print(f"Site URL: [cyan]{site_url}[/cyan]")
    console.print("Press [bold]Ctrl+C[/bold] to stop.\n")

    import bincio.edit.server as srv
    srv.data_dir = data
    srv.site_url = site_url
    srv.strava_client_id = strava_client_id or ""
    srv.strava_client_secret = strava_client_secret or ""
    srv.dem_url = dem_url or ""

    if strava_client_id:
        console.print(f"Strava sync: [green]enabled[/green] (client {strava_client_id})")
    else:
        console.print("Strava sync: [yellow]disabled[/yellow] (pass --strava-client-id to enable)")
    console.print(f"DEM:         [cyan]{srv.dem_url}[/cyan]")

    uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning")


def _load_config(config_path: Optional[str]) -> dict:
    """Load extract_config.yaml — explicit path first, then cwd auto-discovery."""
    import yaml
    for cfg in filter(None, [config_path and Path(config_path), Path("extract_config.yaml")]):
        if Path(cfg).exists():
            return yaml.safe_load(Path(cfg).read_text()) or {}
    return {}


def _resolve_data_dir(explicit: Optional[str], config_path: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    raw = _load_config(config_path)
    out = raw.get("output", {}).get("dir")
    if out:
        return Path(out).expanduser().resolve()

    default = Path.cwd() / "bincio_data"
    if default.exists():
        return default

    raise click.UsageError(
        "Could not find the BAS data directory. "
        "Run `bincio extract` first, or pass --data-dir."
    )
