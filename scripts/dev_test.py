#!/usr/bin/env python3
"""Manual two-user dev test.

Sets up a fresh multi-user instance with dave + brut, extracts their
activities, then hands off to `bincio dev` so you can browse the site.

Run from the project root:

    uv run python scripts/dev_test.py

Options:
    --fresh    Wipe DATA_DIR before starting (default: reuse if it exists)
    --no-dev   Stop after extract (skip `bincio dev`)
    --mobile   Bind API to 0.0.0.0 for mobile app testing on the same WiFi

Credentials:  dave / testpass  and  brut / testpass
URL:          http://localhost:4321
"""

import argparse
import platform
import resource
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR    = Path("/tmp/bincio_dev_test")
DAVE_INPUT  = PROJECT_DIR / "tests" / "data" / "dave"
BRUT_INPUT  = PROJECT_DIR / "tests" / "data" / "brut"
PASSWORD    = "testpass"


def section(msg: str) -> None:
    print(f"\n\033[1;36m▸ {msg}\033[0m")


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m  {msg}")


def warn(msg: str) -> None:
    print(f"  \033[33m·\033[0m  {msg}")


# ── 1. Init instance (dave = admin) ──────────────────────────────────────────

def init_instance() -> None:
    section("Initialising instance")
    from bincio.serve.db import create_user, get_user, open_db

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    db = open_db(DATA_DIR)
    ok("instance.db ready")

    if get_user(db, "dave"):
        warn("user 'dave' already exists — skipping")
    else:
        create_user(db, "dave", "Dave", PASSWORD, is_admin=True)
        ok("admin user 'dave' created")

    if get_user(db, "brut"):
        warn("user 'brut' already exists — skipping")
    else:
        create_user(db, "brut", "Brut", PASSWORD, is_admin=False)
        ok("user 'brut' created")

    for handle in ("dave", "brut"):
        user_dir = DATA_DIR / handle
        (user_dir / "activities").mkdir(parents=True, exist_ok=True)
        (user_dir / "edits").mkdir(parents=True, exist_ok=True)

    import json
    from datetime import datetime, timezone
    root_index = DATA_DIR / "index.json"
    if not root_index.exists():
        root_index.write_text(json.dumps({
            "bas_version": "1.0",
            "instance": {"name": "Dev Test", "private": True},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "shards": [
                {"handle": "dave", "url": "dave/index.json"},
                {"handle": "brut", "url": "brut/index.json"},
            ],
            "activities": [],
        }, indent=2))
        ok("root index.json written")
    else:
        warn("root index.json already exists — skipping")


# ── 2. Extract activities ─────────────────────────────────────────────────────

def extract_user(handle: str, input_dir: Path) -> None:
    section(f"Extracting activities for {handle}")
    if not input_dir.exists():
        print(f"  \033[31m✗\033[0m  Input dir not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    cfg_path = DATA_DIR / f"_cfg_{handle}.yaml"
    cfg_path.write_text(
        f"owner:\n  handle: {handle}\n"
        f"input:\n  dirs:\n    - {input_dir}\n"
        f"output:\n  dir: {DATA_DIR}\n"
    )

    from click.testing import CliRunner
    from bincio.extract.cli import extract as extract_cmd
    result = CliRunner().invoke(extract_cmd, ["--config", str(cfg_path)])

    if result.exit_code != 0:
        print(f"  \033[31m✗\033[0m  Extract failed:\n{result.output}", file=sys.stderr)
        if result.exception:
            import traceback
            traceback.print_exception(type(result.exception), result.exception,
                                      result.exception.__traceback__, file=sys.stderr)
        sys.exit(1)

    acts = list((DATA_DIR / handle / "activities").glob("*.json"))
    ok(f"{len(acts)} activities extracted → {DATA_DIR / handle / 'activities'}")


# ── 3. Merge + manifest ───────────────────────────────────────────────────────

def prepare_serve() -> None:
    section("Merging sidecars + writing root manifest")
    from bincio.render.merge import merge_all
    from bincio.render.cli import _write_root_manifest
    import bincio.render.cli as render_cli
    from rich.console import Console
    render_cli.console = Console()  # normal output

    for handle in ("dave", "brut"):
        n = merge_all(DATA_DIR / handle)
        ok(f"{handle}: {n} sidecar(s) merged")

    _write_root_manifest(DATA_DIR)
    ok("root manifest updated")


# ── 4. Hand off to bincio dev ─────────────────────────────────────────────────

def start_dev(mobile: bool = False) -> None:
    section("Starting bincio dev")
    print()
    print("  \033[1mCredentials\033[0m")
    print(f"    dave  /  {PASSWORD}  (admin)")
    print(f"    brut  /  {PASSWORD}")
    print()
    print("  \033[1mURL\033[0m  http://localhost:4321")
    print()
    print("  Press Ctrl+C to stop.\n")

    cmd = ["uv", "run", "bincio", "dev", "--data-dir", str(DATA_DIR)]
    if mobile:
        cmd += ["--api-host", "0.0.0.0"]

    try:
        subprocess.run(cmd, cwd=PROJECT_DIR)
    except KeyboardInterrupt:
        pass


# ── main ──────────────────────────────────────────────────────────────────────

def raise_open_file_limit() -> None:
    # Astro's file watcher opens many handles; macOS defaults to 256, which
    # causes EMFILE errors under a large project tree.
    if platform.system() != "Darwin":
        return
    target = 65536
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(target, hard), hard))
        ok(f"open-file limit raised to {min(target, hard)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh",  action="store_true", help="Wipe DATA_DIR before starting")
    parser.add_argument("--no-dev", action="store_true", help="Stop after extract, skip bincio dev")
    parser.add_argument("--mobile", action="store_true", help="Bind API to 0.0.0.0 for local mobile testing")
    args = parser.parse_args()

    raise_open_file_limit()

    print(f"\033[1mbincio dev test\033[0m  →  {DATA_DIR}")

    if args.fresh and DATA_DIR.exists():
        section("Wiping existing data dir")
        shutil.rmtree(DATA_DIR)
        ok(f"{DATA_DIR} removed")

    init_instance()
    extract_user("dave", DAVE_INPUT)
    extract_user("brut", BRUT_INPUT)
    prepare_serve()

    if not args.no_dev:
        start_dev(mobile=args.mobile)
    else:
        print(f"\n\033[32mDone.\033[0m  Data ready at {DATA_DIR}")
        print(f"Run:  uv run bincio dev --data-dir {DATA_DIR}\n")


if __name__ == "__main__":
    main()
