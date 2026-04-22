# BincioActivity

> **Your data. Your server. Your rules.**
> No cloud. No subscriptions. No lock-in.

BincioActivity is a self-hosted, federated activity stats platform. You point it at a folder of GPX/FIT/TCX files, it produces a static website. The website runs anywhere — a Raspberry Pi, GitHub Pages, a USB stick. No database. No server process. No account required.

**The philosophy in one sentence:** your activity data is yours, it lives as plain files on your disk, and this tool turns those files into a beautiful site you control entirely.

---

## How it works

```
GPX / FIT / TCX files        Strava API
        │                        │
        ▼                        ▼
  bincio extract        bincio import strava   ← Pull from Strava, or upload via browser ↑
        │                        │
        └────────────┬───────────┘
                     ▼
              ~/bincio_data/       ← BAS data store. Plain JSON + GeoJSON.
               edits/*.md          ← Optional sidecar edits (titles, descriptions, photos).
                     │
                     ▼
              bincio render        ← Merges sidecars → _merged/. Runs Astro build.
                     │
                     ▼
              site/dist/           ← Drop anywhere. Open index.html. Done.
```

Everything in `~/bincio_data/` is plain text you can read, edit, back up, or publish to a CDN. The site build is fully reproducible from those files.

---

## Quick start

```bash
# 1. Clone and install (requires Python >= 3.12 and uv)
git clone https://github.com/brutsalvadi/bincio-activity.git
cd bincio-activity
uv sync                     # installs the bincio package + all dependencies

# 2. Configure
cp extract_config.example.yaml extract_config.yaml
$EDITOR extract_config.yaml     # set input dirs, output dir, your name
# extract_config.yaml is gitignored — safe to add credentials here

# 3a. Extract from local files
uv run bincio extract

# 3b. Or import from Strava
uv sync --extra strava
# Add credentials to extract_config.yaml under import.strava, then:
bincio import strava            # opens browser on first run

# 4. Build the site (requires Node >= 20)
cd site && npm install && cd ..
cp site/.env.example site/.env  # configure BINCIO_DATA_DIR
uv run bincio render            # merges edits + runs astro build
# → open site/dist/index.html
```

For live development with hot reload:
```bash
uv run bincio render --serve    # merges edits, links data, starts astro dev
# → http://localhost:4321

# Optional: enable the activity edit UI + file upload
uv sync --extra edit            # install FastAPI + uvicorn (one-time)
uv run bincio edit              # starts edit server on http://localhost:4041
# Set PUBLIC_EDIT_URL=http://localhost:4041 in site/.env
# → Edit button and ↑ Upload button appear in the site nav
```

---

## Cheatsheet

### Extract

```bash
bincio extract                              # uses extract_config.yaml
bincio extract --input ~/rides --output ~/bincio_data
bincio extract --file ride.gpx             # single file, prints JSON to stdout
bincio extract --since 2025-01-01          # only files newer than date
```

Supported formats: GPX, FIT, TCX — all with optional `.gz` compression.
Strava bulk export: point `metadata_csv` at `activities.csv` to pull in titles, descriptions, and gear.

Extraction is **incremental by default** (`incremental: true` in config). Re-running only processes new or changed files. To force a full re-extract, delete `~/bincio_data/` or set `incremental: false`.

### Site dev

```bash
cd site
npm run dev       # http://localhost:4321 — live reload on data or code changes
npm run build     # production build → site/dist/
npm run preview   # serve site/dist/ locally to check the production build
```

The site reads data from `site/public/data/`. Symlink your BAS store there:
```bash
ln -sf ~/bincio_data site/public/data
```

### Python / tests

```bash
uv run pytest                          # full test suite
uv run pytest tests/test_fit.py -x     # single file, stop on first failure
uv run bincio --help                   # CLI help
uv sync                                # install / update dependencies
```

---

## Configuration

### `extract_config.yaml`

This is the single configuration file for the Python side of BincioActivity.
It is **gitignored** — safe to store credentials here. Copy from `extract_config.example.yaml`.

```yaml
owner:
  handle: yourname
  display_name: Your Name

input:
  dirs:
    - ~/Activities/gpx
    - ~/Activities/fit
  metadata_csv: ~/strava_export/activities.csv   # optional — Strava titles/descriptions

output:
  dir: ~/bincio_data

default_privacy: public   # public | blur_start | no_gps | private

track:
  rdp_epsilon: 0.0001     # GPS track simplification (~11 m at equator)
  timeseries_hz: 1        # data samples per second stored in JSON

incremental: true         # skip files whose hash hasn't changed

# Strava API credentials — from strava.com/settings/api
# Authorization Callback Domain must be set to: localhost
import:
  strava:
    client_id: 12345
    client_secret: your_client_secret_here

# Optional: athlete profile for zone overlays on HR/power charts
athlete:
  max_hr: 182
  ftp_w: 280
  hr_zones:               # [[lo, hi], ...] in bpm — 5-zone Coggan
    - [0,   115]
    - [115, 137]
    - [137, 155]
    - [155, 169]
    - [169, 999]
  power_zones:            # [[lo, hi], ...] in watts — 7-zone Coggan
    - [0,   168]
    - [168, 224]
    - [224, 266]
    - [266, 308]
    - [308, 364]
    - [364, 420]
    - [420, 9999]
```

Zones are written into `index.json` at extract time and displayed as overlays on
HR and Power histograms in the activity detail page. After changing zones, re-run
`uv run bincio extract` to regenerate `index.json`.

### Privacy levels

| Level | GPS track | Stats | Appears in index |
|---|---|---|---|
| `public` | Full | Yes | Yes |
| `blur_start` | First/last 200 m removed | Yes | Yes |
| `no_gps` | Not published | Yes | Yes |
| `private` | Not published | No | No |

Privacy is enforced at extract time. A `private` activity never enters `index.json` and is never served.

---

## The BAS data store

`bincio extract` produces a directory of plain files — the **BincioActivity Schema (BAS)** store:

```
~/bincio_data/
  index.json                    ← summary of all activities + owner info
  activities/
    2024-05-15T08:30:00Z.json   ← full activity: stats, laps, timeseries
    2024-05-15T08:30:00Z.geojson  ← simplified GPS track (RDP)
```

`index.json` is everything the feed page needs — no extra fetches until you open an activity. `{id}.json` contains the full timeseries (elevation, speed, HR, cadence, power at 1 Hz) for charts and the detail map. Both are human-readable and editable with any text editor.

See [SCHEMA.md](docs/schema.md) for the full specification.

---

## Multi-user mode (VPS)

Invite friends and run a shared instance where everyone's activities appear in a combined feed.

```bash
# One-time setup on the VPS
uv sync --extra serve
uv run bincio init --data-dir /var/bincio --handle dave --password 'pw' --name "Our Rides"

# Extract your activities into your user shard
uv run bincio extract --input ~/gpx-files --output /var/bincio/dave

# Build the site
uv run bincio render --data-dir /var/bincio --site-dir site

# Start the API server (nginx proxies /api/* to this)
uv run bincio serve --data-dir /var/bincio --site-dir site
```

Invite users: `bincio init` prints a first invite code. Share `https://example.com/register/?code=XXXXXXXX`. Invited users register themselves and upload their own activities via the browser.

See [Multi-user deployment](docs/deployment/multi-user.md) for the full nginx configuration.

---

## Federation (work in progress)

Add a friend's published `index.json` URL to your `site_config.yaml`:

```yaml
data_sources:
  - type: local
    path: ~/bincio_data
  - type: remote
    handle: alice
    url: https://alice.example.com/bincio/index.json
```

At build time the renderer fetches their public data and renders it under `/friends/alice/`. Your site, their data — with full attribution. They control what they publish; you control what you display.

---

## Tech stack

| Layer | Technology |
|---|---|
| Extract | Python 3.12, click, fitdecode, gpxpy, lxml |
| Strava import | requests (optional extra: `uv sync --extra strava`) |
| Edit server | FastAPI + uvicorn (optional extra: `uv sync --extra edit`) |
| Serve (VPS) | FastAPI + uvicorn + bcrypt + SQLite (optional extra: `uv sync --extra serve`) |
| Site framework | Astro 4 (static output) |
| UI components | Svelte 5 |
| Styling | Tailwind CSS v3 |
| Charts | Observable Plot |
| Maps | MapLibre GL v5 + OpenFreeMap tiles |
| Python packages | uv |
| Node packages | npm |

---

## Project layout

```
bincio/                 Python package
  extract/
    cli.py              `bincio extract` entry point
    parsers/            GPX, FIT, TCX parsers
    sport.py            sport name normalisation
    metrics.py          haversine stats (single-pass)
    timeseries.py       1 Hz downsampling
    simplify.py         RDP track simplification
    dedup.py            hash-based + near-duplicate detection
    strava_csv.py       Strava activities.csv reader
    writer.py           BAS JSON + GeoJSON writer
    config.py           extract_config.yaml loader (includes import.strava)
  import_/
    strava.py           Strava OAuth2 + streams → BAS JSON
    cli.py              `bincio import strava` entry point
  render/
    cli.py              `bincio render` — merge + astro build/serve
    merge.py            sidecar edit overlay (produces _merged/)
  edit/
    cli.py              `bincio edit` — local edit server
    server.py           FastAPI write API (activity edits, image + file upload)
schema/
  bas-v1.schema.json    JSON Schema for BAS format
site/                   Astro project
  src/
    pages/
      index.astro               Activity feed
      activity/[id].astro       Single activity detail
      stats/index.astro         Yearly heatmaps + totals
    components/
      ActivityFeed.svelte        Card grid, sport filter, pagination
      ActivityDetail.svelte      Map + stats + charts + photo gallery
      ActivityMap.svelte         MapLibre GL map
      ActivityCharts.svelte      Observable Plot charts
      StatsView.svelte           Heatmap, percentile scaling, sport filter
      EditDrawer.svelte          Slide-in activity editor
    lib/
      types.ts                  BAS TypeScript types
      format.ts                 Formatting helpers
```

---

## Why no database?

Databases add operational complexity — backups, migrations, running processes, credentials. Activity data is append-only and read-heavy. Plain JSON files handle this perfectly, are trivially backed up with `cp` or `rsync`, can be diffed in git, and work offline. The site is a folder you can zip and email.

## Why federation?

Strava, Garmin Connect, and similar platforms are silos. If the company shuts down or changes its terms, your data and your social graph go with it. BincioActivity's federation model is inspired by the open web: you host your own data at a URL, friends subscribe to that URL, and no central authority is involved.
