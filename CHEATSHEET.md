# BincioActivity — Cheatsheet

## Daily workflow

```bash
# Option A — local files (Karoo / Garmin / Wahoo)
uv run bincio extract          # processes new/changed files, skips unchanged

# Option B — pull from Strava (incremental; credentials in extract_config.yaml)
uv run bincio import strava    # fetches only activities since last sync

# Rebuild the site (merges any sidecar edits, then builds)
uv run bincio render

# Done — copy site/dist/ to your host
```

---

## Extract

```bash
uv run bincio extract                            # full run using extract_config.yaml
uv run bincio extract --since 2025-01-01        # only files newer than date
uv run bincio extract --file ride.gpx           # single file → JSON on stdout
uv run bincio extract --input ~/rides \
                      --output ~/bincio_data    # override config paths
uv run bincio extract --dev 50                  # dev mode: 50 files → /tmp/bincio_dev/
```

Re-extraction is safe — unchanged files are skipped (hash-based dedup).
To force a full re-extract: `rm -rf ~/bincio_data && uv run bincio extract`

### Dev mode

`--dev N` samples N files evenly across the full file list (spread by date and format)
and writes to `/tmp/bincio_dev/` so your real data is never touched. Use it for fast
iteration on UI or pipeline changes:

```bash
uv run bincio extract --dev 50
uv run bincio import strava --dev 50     # N most recent Strava activities
uv run bincio render --serve --data-dir /tmp/bincio_dev
```

---

## Import from Strava

```bash
# Install (one-time)
uv sync --extra strava

# Add credentials to extract_config.yaml (gitignored — safe for secrets):
#   import:
#     strava:
#       client_id: 12345
#       client_secret: your_secret

# First run — opens browser for OAuth, then imports all activities:
uv run bincio import strava

# Subsequent runs are incremental (only fetches since last sync):
uv run bincio import strava

# Other options:
uv run bincio import strava --since 2025-01-01   # explicit date cutoff
uv run bincio import strava --reauth             # force new OAuth flow
uv run bincio import strava --output ~/other_dir # override output dir
uv run bincio import strava --dev 50             # dev mode: 50 most recent → /tmp/bincio_dev/
```

Credentials resolution order:
1. `--client-id` / `--client-secret` flags
2. `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` env vars
3. `import.strava.client_id` / `client_secret` in `extract_config.yaml`

Tokens saved to `~/.config/bincio/strava.json` and auto-refreshed (6h TTL).
Sync state (imported IDs + last sync timestamp) in `data_dir/_strava_sync.json`.

---

## File upload (web UI)

When `PUBLIC_EDIT_URL` is set in `site/.env`, a `↑` button appears in the nav.
Drag a FIT/GPX/TCX onto the modal → the activity is extracted and appears immediately.

---

## Render

```bash
uv run bincio render                            # merge edits + production build → site/dist/
uv run bincio render --serve                    # merge edits + dev server → http://localhost:4321
uv run bincio render --data-dir ~/bincio_data   # explicit data dir
```

`bincio render` always runs `merge_all()` first (applies sidecar edits, produces `_merged/`),
then symlinks `site/public/data` → `_merged/` and runs the Astro build or dev server.

```bash
# Direct npm (skips merge step — use for quick site-only iteration)
cd site
npm run dev
npm run build
npm run preview
```

## Edit

```bash
# Install edit dependencies (FastAPI + uvicorn) — one-time
uv sync --extra edit

# Start the edit server (port 4041 by default)
uv run bincio edit --data-dir ~/bincio_data

# Set PUBLIC_EDIT_URL=http://localhost:4041 in site/.env to enable the Edit button
# Then browse to any activity and click Edit — a drawer opens in the same page
```

Saves write a sidecar `.md` to `~/bincio_data/edits/{id}.md` and immediately
trigger a merge. Refresh the page to see the updated content.

### Sidecar format

```markdown
---
title: "Renamed title"
sport: cycling
gear: "Trek Domane"
highlight: true          # sort to top of feed
private: false           # true = hidden from feed
hide_stats: [cadence]    # suppress stat panels
---

Description in **markdown**. Images go in the gallery — drag & drop in the Edit drawer.
```

---

## Python / tests

```bash
uv sync                                  # install / update deps
uv run bincio --help                     # CLI reference
uv run pytest                            # full test suite
uv run pytest tests/test_fit.py -x       # single file, stop on first fail
uv run pytest -k "sport"                 # run tests matching keyword
uv run pytest -v                         # verbose output
```

---

## Data store layout

```
~/bincio_data/
  index.json                    ← feed index (all activities, summaries)
  activities/
    2024-05-15T08:30:00Z.json   ← full detail + 1Hz timeseries
    2024-05-15T08:30:00Z.geojson  ← simplified GPS track
```

Activity ID format: `YYYY-MM-DDTHH:MM:SSZ` (UTC, always Z suffix).
IDs are stable — safe to use in bookmarks and links.

---

## extract_config.yaml — key fields

This file is **gitignored** — copy from `extract_config.example.yaml` and add your credentials safely.

```yaml
owner:
  handle: yourname
  display_name: Your Name

input:
  dirs:
    - ~/Activities          # scanned recursively for GPX/FIT/TCX/.gz
  metadata_csv: ~/strava_export/activities.csv   # optional

output:
  dir: ~/bincio_data

default_privacy: public     # public | blur_start | no_gps | private
incremental: true           # false = re-process everything
track:
  rdp_epsilon: 0.0001       # GPS simplification — larger = fewer points
  timeseries_hz: 1          # samples/sec in stored JSON (1 = 1 Hz)

import:
  strava:
    client_id: 12345        # from strava.com/settings/api
    client_secret: abc      # Authorization Callback Domain must be: localhost

athlete:
  max_hr: 182               # used for context; zones below are authoritative
  ftp_w: 280                # functional threshold power in watts
  hr_zones:                 # 5-zone Coggan, explicit bpm boundaries [[lo, hi], ...]
    - [0,   115]            # Z1 recovery
    - [115, 137]            # Z2 endurance
    - [137, 155]            # Z3 tempo
    - [155, 169]            # Z4 threshold
    - [169, 999]            # Z5 VO2max
  power_zones:              # 7-zone Coggan, explicit watt boundaries
    - [0,   168]            # Z1 active recovery  (< 55% FTP)
    - [168, 224]            # Z2 endurance        (55–75%)
    - [224, 266]            # Z3 tempo            (75–90%)
    - [266, 308]            # Z4 threshold        (90–105%)
    - [308, 364]            # Z5 VO2max           (105–120%)
    - [364, 420]            # Z6 anaerobic        (120–150%)
    - [420, 9999]           # Z7 neuromuscular    (> 150%)
```

Zones are written into `index.json` under `owner.athlete` at extract time and
displayed as overlays on HR and Power histograms in the activity detail page.
After changing zones, re-run `uv run bincio extract` to update `index.json`.

---

## Privacy

| Value | Track served | Stats | In index |
|---|---|---|---|
| `public` | Full GPS | ✓ | ✓ |
| `blur_start` | First/last 200 m removed | ✓ | ✓ |
| `no_gps` | None | ✓ | ✓ |
| `private` | None | ✗ | ✗ |

Set per-activity in a sidecar `.md` file, or globally via `default_privacy`.

---

## Sports

Canonical sport values: `cycling` `running` `hiking` `walking` `swimming` `skiing` `other`

Sub-sports: `road` `mountain` `gravel` `indoor` `trail` `track` `nordic`

FIT files: sport is read from the `sport` frame, with `session` frame as fallback.
Strava CSV: `Activity Type` column overrides the FIT-detected sport (authoritative).
Mapping lives in `bincio/extract/sport.py`.

---

## Patching activities (manual fixes)

Prefer the Edit drawer for title/sport/description/photo changes — it writes a sidecar
and keeps extracted data pristine. For bulk fixes or fields not exposed in the UI,
patch the JSON directly:

```bash
# Fix sport for a single activity
python3 -c "
import json
p = 'site/public/data/activities/2025-03-16T113005Z.json'
d = json.load(open(p))
d['sport'] = 'skiing'
d['sub_sport'] = 'nordic'
json.dump(d, open(p,'w'), separators=(',',':'))
"

# Then update the index.json to match
python3 -c "
import json
idx = json.load(open('site/public/data/index.json'))
for a in idx['activities']:
    if a['id'] == '2025-03-16T113005Z':
        a['sport'] = 'skiing'
        a['sub_sport'] = 'nordic'
json.dump(idx, open('site/public/data/index.json','w'), separators=(',',':'))
"
```

---

## Common diagnostics

```bash
# Count activities by sport in the data store
python3 -c "
import json, glob
from collections import Counter
files = glob.glob('site/public/data/activities/*.json')
c = Counter(json.load(open(f))['sport'] for f in files)
print(dict(c.most_common()))
"

# Find activities with 0 distance
python3 -c "
import json, glob
for f in glob.glob('site/public/data/activities/*.json'):
    d = json.load(open(f))
    if (d.get('distance_m') or 0) == 0 and d.get('sport') != 'other':
        print(d['id'], d['sport'], d['title'])
"

# Find activities still tagged 'other'
python3 -c "
import json
idx = json.load(open('site/public/data/index.json'))
others = [a for a in idx['activities'] if a['sport'] == 'other']
for a in others[:20]:
    print(a['started_at'][:10], a.get('source','?'), a['title'])
print(len(others), 'total')
"
```

---

## Key files

| File | Purpose |
|---|---|
| `extract_config.yaml` | Main config — input dirs, output dir, athlete zones, Strava credentials. **Gitignored.** Copy from `.example`. |
| `site/.env` | Site env vars (`BINCIO_DATA_DIR`, `PUBLIC_EDIT_URL`) — copy from `site/.env.example`. Gitignored. |
| `SCHEMA.md` | BAS format specification |
| `CLAUDE.md` | Dev notes, gotchas, design decisions |
| `bincio/render/merge.py` | Sidecar overlay logic — `parse_sidecar`, `merge_all` |
| `bincio/edit/server.py` | FastAPI edit API — GET/POST activity, image upload, file upload (`POST /api/upload`) |
| `bincio/import_/strava.py` | Strava OAuth2 client + stream → BAS conversion |
| `bincio/extract/sport.py` | Sport name normalisation + mapping |
| `bincio/extract/metrics.py` | Distance, speed, HR, elevation computation |
| `bincio/extract/parsers/fit.py` | FIT file parser |
| `site/src/components/ActivityFeed.svelte` | Feed page — card grid + sport filter |
| `site/src/components/StatsView.svelte` | Stats page — heatmap + year totals |
| `site/src/components/ActivityMap.svelte` | MapLibre GL map |
| `site/src/components/ActivityCharts.svelte` | Observable Plot charts |
| `site/src/lib/format.ts` | `formatDistance`, `formatDuration`, sport icons/colors |
| `site/src/lib/types.ts` | TypeScript types mirroring BAS schema |
| `site/astro.config.mjs` | Astro + Vite config (MapLibre GL workarounds) |
