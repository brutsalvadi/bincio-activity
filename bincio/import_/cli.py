"""bincio import — CLI command group for external platform importers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
from rich.console import Console

console = Console()


@click.group("import")
def import_group() -> None:
    """Import activities from external platforms (Strava, Garmin, …)."""


@import_group.command("strava")
@click.option("--client-id",     default=None, envvar="STRAVA_CLIENT_ID",
              help="Strava API client ID. Falls back to import.strava.client_id in extract_config.yaml.")
@click.option("--client-secret", default=None, envvar="STRAVA_CLIENT_SECRET",
              help="Strava API client secret. Falls back to import.strava.client_secret in extract_config.yaml.")
@click.option("--output", "output_dir", default=None,
              help="BAS data store directory (default: from config or ~/bincio_data).")
@click.option("--config", "config_path", default=None,
              help="Path to extract_config.yaml (default: ./extract_config.yaml).")
@click.option("--since", default=None, metavar="YYYY-MM-DD",
              help="Only import activities after this date (default: incremental from last sync).")
@click.option("--reauth", is_flag=True, default=False,
              help="Force re-authorization even if valid tokens exist.")
@click.option("--dev", "dev_sample", default=None, type=int, metavar="N",
              help="Dev mode: import only the N most recent activities, output to /tmp/bincio_dev/.")
def strava_cmd(
    client_id:    Optional[str],
    client_secret: Optional[str],
    output_dir:   Optional[str],
    config_path:  Optional[str],
    since:        Optional[str],
    reauth:       bool,
    dev_sample:   Optional[int],
) -> None:
    """Import activities from Strava.

    Credentials are resolved in this order:
      1. --client-id / --client-secret flags
      2. STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET environment variables
      3. import.strava.client_id / client_secret in extract_config.yaml

    Tokens are saved to ~/.config/bincio/strava.json and refreshed automatically.

    \b
    How to get API credentials (takes ~2 minutes, no approval needed):
      1. Go to strava.com/settings/api
      2. Create an application (name/website can be anything;
         Authorization Callback Domain: localhost)
      3. Copy the Client ID and Client Secret into extract_config.yaml:

    \b
         import:
           strava:
             client_id: 12345
             client_secret: your_secret_here

    \b
    Examples:
      bincio import strava                          # uses extract_config.yaml
      bincio import strava --since 2025-01-01       # only activities from 2025
      bincio import strava --reauth                 # force fresh OAuth flow
    """
    try:
        import requests  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "requests is required for the Strava importer.\n"
            "Install with: uv sync --extra strava"
        )

    from bincio.import_.strava import StravaClient, TOKENS_FILE, sync as strava_sync

    # Load config to get credentials + output dir if not given on CLI
    cfg = _load_config(config_path)

    # Resolve credentials: CLI flag > env var (already consumed by click) > config file
    if not client_id and cfg and cfg.strava:
        client_id = cfg.strava.client_id or None
    if not client_secret and cfg and cfg.strava:
        client_secret = cfg.strava.client_secret or None

    if not client_id or not client_secret:
        raise click.UsageError(
            "Strava client ID and secret are required.\n"
            "Add them to extract_config.yaml under import.strava, or pass --client-id/--client-secret."
        )

    if dev_sample is not None:
        out = Path("/tmp/bincio_dev")
        console.print(f"[yellow]Dev mode:[/yellow] importing {dev_sample} activities → [cyan]{out}[/cyan]")
    else:
        out = _resolve_output(output_dir, cfg)
    console.print(f"Output dir: [cyan]{out}[/cyan]")

    if reauth and TOKENS_FILE.exists():
        TOKENS_FILE.unlink()
        console.print("Removed saved tokens — starting fresh OAuth flow.")

    client = StravaClient(client_id, client_secret, console)
    client.authenticate()

    since_dt = None
    if since:
        from datetime import datetime, timezone
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise click.BadParameter(f"Expected YYYY-MM-DD, got {since!r}", param_hint="--since")

    strava_sync(client, out, since_dt, console, limit=dev_sample)


def _load_config(config_path: Optional[str]):
    """Load extract_config.yaml if available; return None if not found."""
    from bincio.extract.config import load_config
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    candidates.append(Path("extract_config.yaml"))
    for p in candidates:
        if p.exists():
            return load_config(p)
    return None


def _resolve_output(explicit: Optional[str], cfg) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if cfg and cfg.output_dir:
        return cfg.output_dir
    default = Path.home() / "bincio_data"
    console.print(f"[yellow]No output dir specified; using [cyan]{default}[/cyan]")
    return default
