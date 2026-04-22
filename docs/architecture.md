# Architecture

BincioActivity is a two-stage pipeline that produces a self-contained static website from raw activity files.

```
GPX / FIT / TCX files
        │
        ▼
  bincio extract          (Python)
        │
        ▼
  BAS data store          (plain JSON + GeoJSON files)
        │
        ▼
  bincio render           (wraps Astro build)
        │
        ▼
  site/dist/              (static HTML/JS/CSS)
        │
        ▼
  Any static host         (GitHub Pages, Netlify, VPS, USB stick, …)
```

The BAS data store is the contract between the two stages. Any tool in any language can produce BAS-compliant JSON. See [SCHEMA.md](schema.md) for the format.

---

## Stages

### Stage 1 — Extract (`bincio/extract/`)

Reads raw activity files, computes stats, and writes BAS JSON.

Key modules:

| Module | Role |
|---|---|
| `parsers/` | GPX, FIT, TCX parsers + format detection |
| `metrics.py` | Haversine-based stats computation (single pass) |
| `timeseries.py` | Downsample to 1 Hz, build BAS timeseries object |
| `simplify.py` | RDP track simplification → GeoJSON |
| `dedup.py` | Exact (hash) + near-duplicate detection |
| `strava_csv.py` | Strava activities.csv metadata enrichment |
| `writer.py` | BAS JSON + GeoJSON writer |
| `config.py` | `extract_config.yaml` loader |

Extract is incremental: unchanged files (same SHA-256) are skipped. To force a full re-extract, delete the output directory.

Large data is passed to worker processes once per worker via `initializer=` (not once per task), keeping ProcessPoolExecutor overhead low.

### Stage 2 — Render (`bincio/render/`)

Merges sidecar edits, symlinks data, and runs `astro build`.

```
data_dir/
  activities/   ← immutable extract output
  edits/        ← user-written sidecar markdown files
  _merged/      ← render-time merge output (served to browser)
```

`merge_all()` overlays sidecar fields onto extracted JSON and writes `_merged/`. The browser always reads from `_merged/`.

---

## Site (`site/`)

Astro + Svelte + Tailwind + MapLibre GL + Observable Plot.

All data fetching is client-side — the site is fully static. On page load, the browser fetches `index.json`, resolves shards, and renders the feed.

Key components:

| Component | Role |
|---|---|
| `ActivityFeed.svelte` | Card grid, sport filter, pagination |
| `ActivityDetail.svelte` | Map + stats + charts + photo gallery |
| `ActivityMap.svelte` | MapLibre GL (gradient track, hover marker) |
| `ActivityCharts.svelte` | Observable Plot (elevation/speed/HR/cadence) |
| `StatsView.svelte` | Yearly heatmap + totals |
| `EditDrawer.svelte` | Slide-in edit panel (visible when edit server is running) |

### Data loading

`site/src/lib/dataloader.ts` fetches `index.json` and recursively resolves shard URLs. Shards are fetched concurrently. The same mechanism handles yearly pagination and multi-user federation.

```
index.json
  └── shards: [
        { url: "dave/_merged/index.json" },   ← user shard
        { url: "https://alice.example.com/index.json" }  ← federated instance
      ]
```

---

## Deployment modes

Single-user and multi-user share the same data layout. The only difference is whether `instance.db` exists (which enables auth).

### Data layout (always)

```
{data-root}/
  index.json        ← shard manifest (always; one shard for single-user)
  instance.db       ← SQLite auth (only in multi-user, created by bincio init)
  {handle}/
    index.json      ← user's BAS feed
    _merged/        ← sidecar-merged output
    activities/
    edits/
    athlete.json
```

### Single-user (static)

No login, no server. Run `bincio dev --data-dir {root}` or `bincio render`, drop `site/dist/` anywhere. The site opens directly at `/u/{handle}/`. The "Feed" tab (combined feed) is hidden — there's only one user.

The edit drawer requires `bincio edit` running locally and `PUBLIC_EDIT_URL` set in `site/.env`.

### Multi-user (VPS)

```
internet
    │
    ▼
nginx / caddy
    ├── /*      → static files (site/dist/)
    └── /api/*  → proxy → bincio serve (127.0.0.1:4041)
```

`bincio serve` is a FastAPI application that owns auth, user management, and write operations. It never serves static files. nginx handles TLS and static file serving.

The root `index.json` shard manifest lists all user shard URLs. The browser resolves them concurrently and merges activities into a combined feed at `/`.

### Instance privacy

When `instance.private = true` in the root `index.json`, the site's `Base.astro` layout injects a client-side auth wall: it calls `GET /api/me` on every page load and redirects to `/login/` on 401/404. The `/login/` and `/register/` pages opt out of this wall via `public={true}`.

This is a best-effort client-side guard. The static files themselves are always readable by anyone with direct URL access. Use nginx-level auth if you need true access control on the static assets.

---

## Edit flow

```
Browser (EditDrawer.svelte)
    │  POST /api/activity/{id}
    ▼
bincio edit / bincio serve
    │  writes edits/{id}.md
    │  calls merge_all()
    ▼
_merged/{id}.json updated
```

In multi-user mode, `bincio serve` additionally spawns `bincio render --handle {user}` to rewrite the shard manifest after each save.

---

## Federation

Any BAS-compliant feed can be included in the root `index.json`:

```json
{
  "shards": [
    { "handle": "dave",  "url": "dave/_merged/index.json" },
    { "handle": "alice", "url": "https://alice.example.com/index.json" }
  ]
}
```

Remote activities appear in the combined feed with `@alice` attribution. The browser fetches remote shards directly — there is no server-side aggregation.

---

## Key design decisions

- **No database, no server** — everything is static files except in multi-user VPS mode, where `bincio serve` owns only the auth and write API.
- **Haversine (not geopy)** for distance calculations — 10× faster for bulk processing.
- **Iterative RDP** for track simplification — no `rdp` PyPI package dependency (not available as a pure-Python wheel for Pyodide).
- **Worker initializer pattern** — large shared dicts (Strava lookup, known hashes) are sent once per worker process, not once per task.
- **BAS activity IDs always use UTC with Z suffix** — URL-safe, unambiguous, sortable.
- **TCX files** from Garmin use both `http://` and `https://` namespace URIs — the parser handles both.
- **Shard manifest for multi-user** — no activity data duplication; each user's feed is a valid standalone BAS feed; the root manifest just points at them.
