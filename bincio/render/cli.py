"""bincio render — build or serve the Astro static site."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

console = Console()


def _find_site_dir(explicit: Optional[str]) -> Path:
    """Locate the Astro project directory."""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not (p / "package.json").exists():
            raise click.UsageError(f"No package.json found in --site-dir {p}")
        return p

    # Search upward from cwd: ./site, ../site (for when cwd is bincio_data/)
    for candidate in [Path.cwd() / "site", Path.cwd().parent / "site"]:
        if (candidate / "package.json").exists():
            return candidate

    raise click.UsageError(
        "Could not find the Astro site directory. "
        "Run from the project root or pass --site-dir."
    )


def _find_data_dir(explicit: Optional[str], config_path: Optional[str]) -> Path:
    """Resolve the BAS data directory."""
    if explicit:
        return Path(explicit).expanduser().resolve()

    if config_path and Path(config_path).exists():
        import yaml
        raw = yaml.safe_load(Path(config_path).read_text()) or {}
        out = raw.get("output", {}).get("dir")
        if out:
            return Path(out).expanduser().resolve()

    # Auto-detect: try extract_config.yaml in cwd
    auto_config = Path.cwd() / "extract_config.yaml"
    if auto_config.exists():
        import yaml
        raw = yaml.safe_load(auto_config.read_text()) or {}
        out = raw.get("output", {}).get("dir")
        if out:
            return Path(out).expanduser().resolve()

    # Default: ./bincio_data next to cwd
    default = Path.cwd() / "bincio_data"
    if default.exists():
        return default

    raise click.UsageError(
        "Could not find the BAS data directory. "
        "Run `bincio extract` first, or pass --data-dir."
    )


def _ensure_npm(site: Path) -> None:
    """Run `npm install` if node_modules is missing or stale."""
    if not (site / "node_modules").exists():
        console.print("Running [cyan]npm install[/cyan]…")
        subprocess.run(["npm", "install"], cwd=site, check=True)


def _user_dirs(data: Path) -> list[Path]:
    """Return all per-user subdirectories (contain an activities/ dir)."""
    return sorted(
        p for p in data.iterdir()
        if p.is_dir() and (p / "activities").exists()
    )


def _merge_edits(data: Path, handle: str | None = None) -> None:
    """Run the sidecar merge step for one user or all users."""
    from bincio.render.merge import merge_all

    targets = [data / handle] if handle else _user_dirs(data)
    total = 0
    for user_dir in targets:
        n = merge_all(user_dir)
        total += n
        console.print(f"  [cyan]{user_dir.name}[/cyan]: {n} sidecar(s) merged")
    if not total:
        console.print("No sidecars found — _merged/ dirs mirror extracted data.")


def _write_root_manifest(data: Path) -> None:
    """Rewrite the root index.json shard manifest from current user dirs."""
    import json
    from datetime import datetime, timezone

    users = _user_dirs(data)
    # Read existing manifest to preserve instance metadata
    root = data / "index.json"
    existing: dict = {}
    if root.exists():
        try:
            existing = json.loads(root.read_text())
        except Exception:
            pass

    has_auth = (data / "instance.db").exists()
    existing_instance = existing.get("instance", {"name": "BincioActivity"})
    if not has_auth:
        # Single-user: no auth server, force private off regardless of what was written before.
        existing_instance = {**existing_instance, "private": False}
    elif "private" not in existing_instance:
        # Multi-user first run: default to private.
        existing_instance = {**existing_instance, "private": True}
    manifest = {
        "bas_version": "1.0",
        "instance": existing_instance,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shards": [
            {
                "handle": u.name,
                "url": f"{u.name}/_merged/index.json"
                       if (u / "_merged" / "index.json").exists()
                       else f"{u.name}/index.json",
            }
            for u in users
        ],
        "activities": [],
    }
    root.write_text(json.dumps(manifest, indent=2))
    console.print(f"Root manifest updated: [cyan]{len(users)}[/cyan] user shard(s)")

    if len(users) > 1:
        from bincio.render.merge import write_combined_feed
        n = write_combined_feed(data)
        console.print(f"Combined feed: [cyan]{n}[/cyan] activities across all users")


def _link_data(site: Path, data: Path) -> None:
    """Symlink site/public/data → data root (each user has their own _merged/)."""
    target = data
    public_data = site / "public" / "data"
    public_data.parent.mkdir(parents=True, exist_ok=True)
    if public_data.is_symlink():
        if public_data.resolve() == target.resolve():
            return  # already correct
        public_data.unlink()
    elif public_data.exists():
        console.print(
            f"[yellow]Warning:[/yellow] {public_data} exists and is not a symlink — "
            "remove it manually if you want bincio to manage it."
        )
        return
    public_data.symlink_to(target)
    console.print(f"Linked data: [cyan]{target}[/cyan] → [cyan]{public_data}[/cyan]")


@click.command()
@click.option("--config", "config_path", default=None,
              help="Path to extract_config.yaml (reads output.dir from it).")
@click.option("--data-dir", default=None,
              help="BAS data store directory (output of bincio extract).")
@click.option("--site-dir", default=None,
              help="Astro project directory (default: ./site).")
@click.option("--out", "out_dir", default=None,
              help="Build output directory (default: site/dist).")
@click.option("--serve", is_flag=True,
              help="Start dev server with hot reload instead of building.")
@click.option("--deploy", default=None, metavar="TARGET",
              help="Deploy after build. Currently supports: github.")
@click.option("--handle", default=None,
              help="(Multi-user) Incrementally re-merge one user's shard only.")
@click.option("--no-build", "no_build", is_flag=True,
              help="Skip the Astro build step (just merge sidecars and update manifests).")
def render(
    config_path: Optional[str],
    data_dir: Optional[str],
    site_dir: Optional[str],
    out_dir: Optional[str],
    serve: bool,
    deploy: Optional[str],
    handle: Optional[str],
    no_build: bool,
) -> None:
    """Build (or serve) the BincioActivity static site from a BAS data store."""

    site = _find_site_dir(site_dir)
    data = _find_data_dir(data_dir, config_path)

    console.print(f"Site:  [cyan]{site}[/cyan]")
    console.print(f"Data:  [cyan]{data}[/cyan]")

    _merge_edits(data, handle=handle)
    _write_root_manifest(data)

    if no_build:
        console.print("[green]Data updated.[/green] Skipping Astro build (--no-build).")
        return

    _ensure_npm(site)
    _link_data(site, data)

    env = {**os.environ, "BINCIO_DATA_DIR": str(data)}

    if serve:
        console.print("Starting [cyan]astro dev[/cyan]…")
        subprocess.run(["npm", "run", "dev"], cwd=site, env=env)
        return

    # Build
    cmd = ["npm", "run", "build"]
    if out_dir:
        # Pass outDir via Astro CLI flag
        cmd = ["npx", "astro", "build", "--outDir", str(Path(out_dir).resolve())]

    console.print("Running [cyan]astro build[/cyan]…")
    result = subprocess.run(cmd, cwd=site, env=env)
    if result.returncode != 0:
        console.print("[red]Build failed.[/red]")
        sys.exit(result.returncode)

    dist = Path(out_dir).resolve() if out_dir else site / "dist"
    console.print(f"\n[green]Build complete.[/green] Output: [cyan]{dist}[/cyan]")

    if deploy == "github":
        _deploy_github(site, dist)


def _deploy_github(site: Path, dist: Path) -> None:
    """Push dist/ to the gh-pages branch."""
    console.print("Deploying to [cyan]GitHub Pages[/cyan]…")
    # Requires npx gh-pages or git subtree push
    result = subprocess.run(
        ["npx", "gh-pages", "-d", str(dist)],
        cwd=site,
    )
    if result.returncode != 0:
        console.print(
            "[yellow]Tip:[/yellow] install gh-pages with `npm install -g gh-pages`"
        )
