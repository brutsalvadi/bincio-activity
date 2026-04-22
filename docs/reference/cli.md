# CLI reference

All commands are run via `uv run bincio <command>` from the project root.

---

## bincio dev

Start the full local development environment. One command replaces the two-terminal setup.

```bash
uv sync --extra serve
uv run bincio dev [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--data-dir DIR` | auto-detected | BAS data directory (must contain `instance.db`) |
| `--site-dir DIR` | `./site` | Astro project directory |
| `--port PORT` | `4321` | Astro dev server port |
| `--api-port PORT` | `4041` | bincio serve API port |

`bincio dev` runs the following steps automatically:
1. Merges sidecar edits for all users (`merge_all()`)
2. Rewrites the root `index.json` shard manifest
3. Symlinks `site/public/data` → data directory
4. Starts `bincio serve` on `--api-port` in a background thread (**only if `instance.db` exists**)
5. Starts `astro dev` on `--port` in the foreground

No `.env` file needed — `BINCIO_DATA_DIR` and `PUBLIC_EDIT_URL` are set automatically.

Works in both modes:
- **Single-user** (no `instance.db`): no login, no API server, just `astro dev`
- **Multi-user** (`instance.db` present): starts `bincio serve` alongside `astro dev`

Ctrl+C stops everything.

---

## bincio extract

Extract GPX/FIT/TCX files into a BAS data store.

```bash
uv run bincio extract [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `extract_config.yaml` | Path to config file |
| `--input DIR` | from config | Input directory (scanned recursively) |
| `--output DIR` | from config | Instance root directory |
| `--file PATH` | — | Extract a single file, print JSON to stdout |
| `--since DATE` | — | Only process files newer than this date (YYYY-MM-DD) |
| `--dev N` | — | Dev mode: sample N files evenly, output to `/tmp/bincio_dev/` |

`--output` (and `output.dir` in config) is the **instance root**, not the user directory. The handle from `owner.handle` in `extract_config.yaml` is always appended automatically:

```
bincio extract --output ~/bincio_data
# → writes to ~/bincio_data/{handle}/
```

This applies to both single-user and multi-user setups — the data layout is always the same.

Extraction is incremental by default — unchanged files (same hash) are skipped. To force a full re-extract, delete the user directory: `rm -rf ~/bincio_data/{handle}`.

Supported formats: GPX, FIT, TCX — all with optional `.gz` compression.

---

## bincio render

Merge sidecar edits and build (or serve) the Astro site.

```bash
uv run bincio render [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--data-dir DIR` | auto-detected | BAS data store |
| `--site-dir DIR` | `./site` | Astro project directory |
| `--out DIR` | `site/dist` | Build output directory |
| `--serve` | false | Start dev server instead of building |
| `--deploy TARGET` | — | Deploy after build. Currently: `github` |
| `--handle HANDLE` | — | (Multi-user) Re-merge one user's shard only, then rewrite root manifest |

`bincio render` always:
1. Runs `merge_all()` — applies sidecar edits, produces `_merged/`
2. Rewrites the root `index.json` shard manifest
3. Symlinks `site/public/data` → data directory
4. Runs `astro build` (or `astro dev` with `--serve`)

Data directory auto-detection order:
1. `--data-dir` flag
2. `output.dir` in `extract_config.yaml` (if found in cwd)
3. `./site/public/data` (symlink)
4. `../bincio_data`

---

## bincio edit

Start the local single-user edit server. For personal use only — no authentication.

```bash
uv sync --extra edit   # install dependencies (one-time)
uv run bincio edit [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--data-dir DIR` | auto-detected | BAS data store |
| `--host HOST` | `127.0.0.1` | Bind address |
| `--port PORT` | `4041` | Bind port |
| `--strava-client-id ID` | from config | Strava OAuth client ID |
| `--strava-client-secret SECRET` | from config | Strava OAuth client secret |
| `--dem-url URL` | `https://api.open-elevation.com` | Open-Elevation-compatible API for the "Recalculate elevation" button (also `DEM_URL` env var) |

Set `PUBLIC_EDIT_URL=http://localhost:4041` in `site/.env` to enable the Edit button and Upload ↑ button in the site.

Credentials resolution: `--strava-client-*` flags → `STRAVA_CLIENT_ID/SECRET` env vars → `import.strava.*` in `extract_config.yaml`.

---

## bincio init

Bootstrap a fresh multi-user instance. Run once per VPS.

```bash
uv sync --extra serve   # install dependencies (one-time)
uv run bincio init [OPTIONS]
```

| Option | Required | Description |
|---|---|---|
| `--data-dir DIR` | yes | BAS data directory to initialise |
| `--handle HANDLE` | yes | Admin user handle (lowercase, URL-safe) |
| `--password PASSWORD` | yes | Admin password (prompted if omitted) |
| `--display-name NAME` | no | Admin display name (defaults to handle) |
| `--name NAME` | no | Instance name shown in the feed |

Creates:
- `instance.db` — SQLite database with users/sessions/invites tables
- `{handle}/` — admin user data directory and subdirectories
- `index.json` — root shard manifest with `"private": true`
- Prints a first invite code to stdout

Idempotent — safe to re-run. Skips steps already completed.

---

## bincio serve

Start the multi-user application server (VPS mode).

```bash
uv run bincio serve [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--data-dir DIR` | required | BAS data directory (must contain `instance.db`) |
| `--site-dir DIR` | — | Astro site dir — enables post-write incremental rebuilds |
| `--host HOST` | `127.0.0.1` | Bind address (keep on localhost; nginx proxies from outside) |
| `--port PORT` | `4041` | Bind port |
| `--dem-url URL` | `https://api.open-elevation.com` | Open-Elevation-compatible API for the "Recalculate elevation" button (also `DEM_URL` env var) |

Requires `bincio init` to have been run first. Handles auth, user management, and write operations. nginx is responsible for serving static files and proxying `/api/*` to this server.

See [multi-user deployment](../deployment/multi-user.md) for nginx configuration.

---

## bincio import strava

Import activities directly from the Strava API.

```bash
uv sync --extra strava
uv run bincio import strava [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--output DIR` | from config | BAS data store output directory |
| `--since DATE` | last sync | Only import activities after this date |
| `--reauth` | false | Force a new OAuth flow even if a token exists |
| `--dev N` | — | Dev mode: import N most recent activities to `/tmp/bincio_dev/` |

Credentials: set `import.strava.client_id` and `import.strava.client_secret` in `extract_config.yaml`. The Authorization Callback Domain in the Strava app settings must be `localhost`.

Tokens are stored in `<data_dir>/strava_token.json` and auto-refreshed.

---

## Global flags

```bash
uv run bincio --version   # print version
uv run bincio --help      # list commands
uv run bincio <cmd> --help   # command-specific help
```
