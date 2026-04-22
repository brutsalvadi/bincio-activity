"""bincio serve — CLI entry point for the multi-user VPS server."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
from rich.console import Console

console = Console()


@click.command("serve")
@click.option("--data-dir", required=True, type=click.Path(), help="BAS data directory (contains instance.db)")
@click.option("--site-dir", default=None, type=click.Path(), help="Astro site dir for post-write rebuilds")
@click.option("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1 — proxy via nginx)")
@click.option("--port", default=4041, help="Bind port (default: 4041)")
@click.option("--strava-client-id", default=None, envvar="STRAVA_CLIENT_ID", help="Strava OAuth client ID (enables per-user Strava sync)")
@click.option("--strava-client-secret", default=None, envvar="STRAVA_CLIENT_SECRET", help="Strava OAuth client secret")
@click.option("--max-users", default=None, type=int, help="Override max users for this instance (0 = unlimited; updates the DB setting)")
@click.option("--public-url", default=None, envvar="PUBLIC_URL", help="Public base URL (e.g. https://yourdomain.com). Required for Strava OAuth to work behind a reverse proxy.")
@click.option("--webroot", default=None, type=click.Path(), help="Nginx webroot (e.g. /var/www/bincio). When set, uploads trigger a full Astro build + rsync so new activity pages are immediately accessible without a git push.")
@click.option("--dem-url", default=None, envvar="DEM_URL", help="Base URL of an Open-Elevation-compatible API (default: https://api.open-elevation.com).")
def serve(data_dir: str, site_dir: Optional[str], host: str, port: int,
          strava_client_id: Optional[str], strava_client_secret: Optional[str],
          max_users: Optional[int], public_url: Optional[str],
          webroot: Optional[str], dem_url: Optional[str]) -> None:
    """Start the bincio multi-user application server.

    Handles auth, user management, and write operations.
    Intended to run behind nginx which serves static files.

    Requires a data directory initialised with `bincio init`.
    """
    import uvicorn
    import bincio.serve.server as srv
    from bincio.serve.db import open_db, set_setting, get_setting

    dd = Path(data_dir).expanduser().resolve()
    if not (dd / "instance.db").exists():
        raise click.UsageError(
            f"No instance.db found in {dd}. Run `bincio init --data-dir {dd}` first."
        )

    if max_users is not None:
        db = open_db(dd)
        set_setting(db, "max_users", str(max_users))
        db.close()

    srv.data_dir = dd
    if site_dir:
        srv.site_dir = Path(site_dir).expanduser().resolve()
    if strava_client_id:
        srv.strava_client_id = strava_client_id
    if strava_client_secret:
        srv.strava_client_secret = strava_client_secret
    if public_url:
        srv.public_url = public_url
    if webroot and site_dir:
        srv.webroot = Path(webroot).expanduser().resolve()
    if dem_url:
        srv.dem_url = dem_url

    db = open_db(dd)
    current_limit = get_setting(db, "max_users")
    db.close()

    console.print(f"[bold]bincio serve[/bold]")
    console.print(f"  Data:  [cyan]{dd}[/cyan]")
    if srv.site_dir:
        console.print(f"  Site:  [cyan]{srv.site_dir}[/cyan]")
    if srv.webroot:
        console.print(f"  Web:   [cyan]{srv.webroot}[/cyan] (auto-rebuild on upload)")
    console.print(f"  URL:   [cyan]http://{host}:{port}[/cyan]")
    if current_limit and int(current_limit) > 0:
        console.print(f"  Users: [yellow]max {current_limit}[/yellow]")
    else:
        console.print(f"  Users: [dim]unlimited[/dim]")
    console.print(f"  DEM:   [cyan]{srv.dem_url}[/cyan]")
    console.print()

    log_config = uvicorn.config.LOGGING_CONFIG.copy()
    # Make bincio.serve logger emit at INFO through uvicorn's handler
    log_config["loggers"]["bincio.serve"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    uvicorn.run(srv.app, host=host, port=port, log_level="info", log_config=log_config)
