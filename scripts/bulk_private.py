#!/usr/bin/env python3
"""
Bulk-set activities matching a title pattern to private by writing sidecar files.

Usage:
    uv run python scripts/bulk_private.py --data-dir /var/bincio/data/brut --match "morning walk" "afternoon walk"

    --dry-run   Print what would be changed without writing anything.
    --handle    Subdirectory name (if data-dir is the root, not the user dir).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


def parse_sidecar(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = re.split(r"^---[ \t]*$", text, maxsplit=2, flags=re.MULTILINE)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            return fm, parts[2].strip()
    return {}, text.strip()


def write_sidecar(path: Path, fm: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "---\n" + yaml.dump(fm, allow_unicode=True, default_flow_style=False) + "---\n"
    if body:
        content += "\n" + body + "\n"
    path.write_text(content, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="User data directory (e.g. /var/bincio/data/brut)")
    ap.add_argument("--handle", default=None, help="Handle subdir if data-dir is the instance root")
    ap.add_argument("--match", nargs="+", required=True, help="Title patterns to match (case-insensitive substring)")
    ap.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if args.handle:
        data_dir = data_dir / args.handle

    index_path = data_dir / "index.json"
    if not index_path.exists():
        sys.exit(f"ERROR: index.json not found at {index_path}")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    activities = index.get("activities", [])

    patterns = [p.lower() for p in args.match]

    matched = [
        a for a in activities
        if any(pat in (a.get("title") or "").lower() for pat in patterns)
    ]

    if not matched:
        print("No activities matched.")
        return

    print(f"Found {len(matched)} matching activities:")
    edits_dir = data_dir / "edits"
    changed = 0

    for act in matched:
        aid = act["id"]
        title = act.get("title", "(no title)")
        date = act.get("started_at", "")[:10]
        sidecar_path = edits_dir / f"{aid}.md"

        # Load existing sidecar if present
        if sidecar_path.exists():
            fm, body = parse_sidecar(sidecar_path)
        else:
            fm, body = {}, ""

        if fm.get("private") is True:
            print(f"  [already private] {date}  {title}")
            continue

        print(f"  {'[DRY RUN] ' if args.dry_run else ''}→ private  {date}  {title}")
        if not args.dry_run:
            fm["private"] = True
            write_sidecar(sidecar_path, fm, body)
            changed += 1

    if args.dry_run:
        print("\nDry run — nothing written. Re-run without --dry-run to apply.")
    else:
        print(f"\n{changed} sidecar(s) written.")
        if changed:
            print("Running merge_all …")
            from bincio.render.merge import merge_all
            n = merge_all(data_dir)
            print(f"merge_all done ({n} sidecar(s) applied).")


if __name__ == "__main__":
    main()
