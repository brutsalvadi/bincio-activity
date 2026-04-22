"""bincio init — bootstrap a fresh multi-user instance."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

console = Console()


@click.command("init")
@click.option("--data-dir", required=True, type=click.Path(), help="BAS data directory to initialise")
@click.option("--handle", required=True, help="Admin user handle (e.g. 'dave')")
@click.option("--password", required=True, hide_input=True, confirmation_prompt=True, help="Admin password")
@click.option("--display-name", default="", help="Admin display name (defaults to handle)")
@click.option("--name", default="", help="Instance name shown in the feed")
@click.option("--max-users", default=0, type=int, help="Maximum number of users allowed (0 = unlimited)")
def init(data_dir: str, handle: str, password: str, display_name: str, name: str, max_users: int) -> None:
    """Bootstrap a fresh bincio multi-user instance.

    Creates the SQLite database, the admin user, the per-user data directory,
    and prints a first invite code. Safe to re-run — skips steps already done.
    """
    from bincio.serve.db import create_invite, create_user, get_user, open_db, set_setting, get_setting

    dd = Path(data_dir).expanduser().resolve()
    dd.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]Initialising bincio instance[/bold] at [cyan]{dd}[/cyan]")

    # ── Database ─────────────────────────────────────────────────────────────
    db = open_db(dd)
    console.print("  [green]✓[/green] instance.db ready")

    # ── Admin user ───────────────────────────────────────────────────────────
    existing = get_user(db, handle)
    if existing:
        console.print(f"  [yellow]·[/yellow] user '{handle}' already exists — skipping")
    else:
        create_user(db, handle, display_name or handle, password, is_admin=True)
        console.print(f"  [green]✓[/green] admin user '{handle}' created")

    # ── User data directory ───────────────────────────────────────────────────
    user_dir = dd / handle
    user_dir.mkdir(exist_ok=True)
    (user_dir / "activities").mkdir(exist_ok=True)
    (user_dir / "edits").mkdir(exist_ok=True)
    console.print(f"  [green]✓[/green] data dir {dd / handle}/ ready")

    # ── Root index.json shard manifest ───────────────────────────────────────
    import json
    from datetime import datetime, timezone

    root_index = dd / "index.json"
    if root_index.exists():
        # Preserve existing manifest but always enforce private: True for a multi-user instance.
        manifest = json.loads(root_index.read_text())
        instance = manifest.setdefault("instance", {})
        if not instance.get("private"):
            instance["private"] = True
            if name:
                instance["name"] = name
            root_index.write_text(json.dumps(manifest, indent=2))
            console.print("  [green]✓[/green] root index.json updated (private: true)")
        else:
            console.print("  [yellow]·[/yellow] root index.json already private — skipping")
    else:
        manifest = {
            "bas_version": "1.0",
            "instance": {"name": name or "BincioActivity", "private": True},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "shards": [{"handle": handle, "url": f"{handle}/index.json"}],
            "activities": [],
        }
        root_index.write_text(json.dumps(manifest, indent=2))
        console.print("  [green]✓[/green] root index.json manifest written")

    # ── User limit ────────────────────────────────────────────────────────────
    if max_users > 0:
        set_setting(db, "max_users", str(max_users))
        console.print(f"  [green]✓[/green] user limit set to {max_users}")
    else:
        console.print("  [dim]·[/dim] no user limit (unlimited)")

    # ── Original file storage default ─────────────────────────────────────────
    if get_setting(db, "store_originals") is None:
        set_setting(db, "store_originals", "true")
        console.print("  [green]✓[/green] store_originals = true (users can override per upload)")

    # ── First invite code ─────────────────────────────────────────────────────
    code = create_invite(db, handle)

    console.print()
    console.print(Panel(
        f"[bold green]Instance ready![/bold green]\n\n"
        f"Admin:        [cyan]{handle}[/cyan]\n"
        f"Data dir:     [cyan]{dd}[/cyan]\n\n"
        f"First invite code:\n\n"
        f"  [bold yellow]{code}[/bold yellow]\n\n"
        f"Share this link with your first user:\n"
        f"  /register/?code={code}",
        title="bincio init",
        border_style="green",
    ))
