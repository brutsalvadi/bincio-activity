# Changelog

## [0.1.0] — 2026-04-22

### Improvement — DEM & hysteresis algorithm refinements

**Hysteresis-only recalculation** (`recalculate_elevation_hysteresis`) reworked:

- Pre-smooths the elevation series with a **30 s centred moving average** (O(n)
  cumsum implementation) before accumulation. Pre-smoothing suppresses barometric
  quantization steps and GPS jitter without discarding real terrain.
- Hysteresis thresholds reduced to **1 m (barometric)** / **3 m (GPS/unknown)**
  — safe after pre-smoothing, and accurate enough to capture genuine small climbs
  that the previous 5 m / 10 m thresholds were swallowing.
- Response key renamed `source` → `altitude_source` for consistency with the
  detail JSON field.

**DEM recalculation** median-filter window widened from 45 s → **60 s** to more
reliably absorb the occasional larger SRTM tile-boundary step.

`altitude_source` is now written into the activity detail JSON at extract time
(`writer.py`), making the hysteresis endpoint source-aware for all newly uploaded
activities.

### Tests

- **`tests/test_dem.py`** (new) — 21 tests covering `_moving_average`,
  `_median_filter`, `_hysteresis_gain_loss`, and `recalculate_elevation_hysteresis`
  at the file level (no network, no extract pipeline)
- **`tests/test_edit_server.py`** (new) — 11 `TestClient` API tests for both
  `/recalculate-elevation/hysteresis` and `/recalculate-elevation/dem` endpoints,
  covering happy path, error codes (404/422/503), path-traversal rejection, and
  on-disk JSON patching
- `httpx` added as a dev dependency (required by FastAPI `TestClient`)

---

## [0.1.0-dev] — 2026-04-20

### Improvement — Elevation gain accuracy (hysteresis accumulation)

The previous algorithm accumulated every positive elevation delta between
consecutive track points, counting GPS jitter and barometric quantization
noise as real climbing. This consistently overestimated gain — in extreme
cases by 100% on flat coastal routes.

The new algorithm uses **hysteresis dead-band accumulation**: elevation is
only committed when it changes by more than a source-specific threshold from
the last committed value. GPS noise is suppressed without losing real climbs.

- **`bincio/extract/models.py`** — `ParsedActivity` gains an `altitude_source`
  field (`"barometric"` / `"gps"` / `"unknown"`)
- **`bincio/extract/parsers/fit.py`** — detects whether any record frame used
  `enhanced_altitude` (barometric altimeter) vs `altitude` (GPS-derived) and
  sets `altitude_source` accordingly
- **`bincio/extract/parsers/gpx.py`**, **`tcx.py`** — both set
  `altitude_source = "gps"`
- **`bincio/extract/metrics.py`** — `_elevation()` replaced with hysteresis
  accumulator; thresholds: **5 m** for barometric, **10 m** for GPS/unknown
- **`tests/test_metrics.py`** — 5 new parametric tests: flat GPS noise
  suppression, barometric vs GPS threshold difference, real climb approximation,
  unknown-treated-as-gps invariant

### New feature — On-demand elevation recalculation from the edit drawer

Two new buttons in the activity edit drawer fix inaccurate elevation stats
without re-uploading the file:

**📐 Recalculate (hysteresis)** — re-applies source-aware hysteresis
accumulation to the original recorded elevation. Fast, offline, no network
required. Best for barometric altimeters (Karoo 2, Garmin with
`enhanced_altitude`, Wahoo) that were extracted before the noise-filtering
improvement.

**⛰ Recalculate (DEM)** — replaces GPS altitude with SRTM terrain data, then
re-applies hysteresis. Best for GPS-only devices where the recorded altitude
is noisy.

DEM pipeline (revised after discovering that a naive 5 m threshold produced
results worse than no correction on some activities):
1. Subsample GPS track to one point per 10 s
2. Query Open-Elevation API in batches of 512
3. Linearly interpolate back to the full 1 Hz series
4. Apply a **45 s sliding median filter** to suppress SRTM tile-boundary
   steps (occur every ~7 s at cycling speed; were accumulating through 5 m
   threshold and inflating gain by 50 %+)
5. Apply **10 m hysteresis** to the smoothed series
6. Back up original `elevation_m` as `elevation_m_original` in the timeseries
   on the first DEM run (never overwrites an existing backup)

- **`bincio/extract/dem.py`** (new) — `lookup_elevations()`,
  `recalculate_elevation()` (DEM + median + 10 m hysteresis),
  `recalculate_elevation_hysteresis()` (offline, reads `elevation_m_original`
  if available, uses 5 m/10 m source-aware threshold)
- **`POST /api/activity/{id}/recalculate-elevation/dem`** and
  **`POST /api/activity/{id}/recalculate-elevation/hysteresis`** — on both
  `bincio serve` (auth-gated, triggers `merge_one` + rebuild) and
  `bincio edit` (no auth)
- **`bincio serve --dem-url URL`** / **`bincio edit --dem-url URL`** — override
  the default DEM endpoint (also read from `DEM_URL` env var)
- Default DEM endpoint: **`https://api.open-elevation.com`** — works out of
  the box with no configuration
- **`GET /api/me`** response gains `dem_configured: bool`
- **`EditDrawer.svelte`** — two side-by-side buttons with individual spinners,
  shows `↑ Xm ↓ Ym` on success or inline error

---

## [0.1.0-dev] — 2026-04-16

### New feature — Self-service user settings page

- **`site/src/pages/settings/index.astro`** — new `/settings/` page with three sections:
  - **Account** — display name editor, storage quota view (uploaded activities + originals size)
  - **Integrations** — per-user Strava client ID/secret (replaces instance-level credentials for
    multi-user deployments); saved to `settings` table via `PATCH /api/me`
  - **Danger zone** — two separate destructive actions:
    - **Delete originals** — removes `{user_dir}/originals/` without touching activities
    - **Delete all activities** — wipes all activities, edits, GeoJSON, and `_merged/`; triggers rebuild
  - Nav visibility toggles — user can hide any combination of Feed / Stats / Athlete tabs from
    their navigation; preference saved to `settings` table and applied in `Base.astro`

### New feature — Upload overwrite option

- **`POST /api/upload`** — new `overwrite: bool` form field; when true, an existing activity
  with the same ID is replaced rather than returning 409. UI checkbox added to the upload modal.

### New feature — Admin tools

- **Ghost user detection** — `/admin/` now marks users whose handle has a data directory but
  no entry in the `users` table (e.g. manually created dirs, or users deleted from DB); shown
  with a "ghost" badge
- **Delete directory button** — admin can delete a user's entire data directory without
  touching the DB entry; useful for cleaning up ghost dirs or corrupted accounts
- **Delete all activities** (`DELETE /api/admin/users/{handle}/activities`) — wipes
  `activities/`, `edits/`, `_merged/`, and `index.json` for a handle, then triggers a rebuild;
  admin page shows a confirmation `<dialog>` before firing
- **"Admin" nav link** — visible in the top-right for admins only

### New feature — Password reset (admin-generated one-time code)

No email infrastructure required. Flow:

1. Admin visits `/admin/` → clicks "Reset pwd" → a 24-hour code appears inline (click to copy)
2. Admin sends it out-of-band (Signal, Telegram, etc.)
3. User goes to `/reset-password/`, enters handle + code + new password

- `POST /api/admin/users/{handle}/reset-password-code` (admin) → `{code, expires_in_hours: 24}`
- `POST /api/auth/reset-password` (public) → body `{handle, code, password}`
- `reset_codes` table in `instance.db`; generating a new code invalidates prior unused codes;
  used codes kept for audit

### New feature — Re-extract from Strava originals

- **`POST /api/admin/reextract`** — re-runs the extract pipeline over all
  `{user_dir}/originals/strava/*.json` files without hitting the Strava API again;
  streams progress via SSE; useful after pipeline improvements
- Runs as a subprocess to avoid OOM (`malloc_trim` + `gc.collect` every 50 activities);
  processes in batches of 100 to bound peak RSS

### New feature — Community page

- **`/community/` tab** — sortable table of all registered users: display name, handle,
  member since, invited by; replaces the earlier inline community section on the about page

### New feature — Streaming upload progress

- **`POST /api/upload`** now returns `text/event-stream` instead of JSON
- Per-file progress events: `↓ 3/47 (6%) — morning_ride.fit`
- Final `done` event: `"12 added, 35 duplicates"`
- Vite proxy configured to not buffer the stream

### Bug fixes

- **`elevation_gain_m` null for modern Garmin FIT files** — session message `total_ascent`
  field now read as fallback when per-point elevation gain is zero
- **Map flash on activity detail** — map container height set before `fitBounds` to prevent
  a zero-height frame during load
- **Absolute `track_url` / `detail_url` paths** — `ActivityDetail` and `loadActivity` now
  handle both relative and absolute paths in BAS JSON
- **Corrupted time streams causing OOM** — `metrics.py` guards against non-monotonic or
  pathologically large time arrays before allocating the 1 Hz dense array
- **Merge race condition** — `merge_all` wipe + rewrite is now guarded; concurrent upload
  triggers can no longer interleave a `shutil.rmtree` with a write from another request
- **Temp ZIP leak** — upload temp files now written to `/tmp/` and always deleted in a
  `finally` block; a startup hook auto-cleans any leftovers
- **`bincio init` always overwrites `private`** — fixed to preserve existing value when
  `index.json` already exists
- **Auth wall flash** — `Base.astro` now sets the auth state synchronously from a cookie
  hint before the `fetch('/api/me')` resolves, eliminating the visible flash
- **Single-user redirect loop** — `index.astro` no longer redirects to `/u/{handle}/` on
  private (multi-user) instances
- **Theme-aware Plot tooltips** — forced black text on white background; was rendering
  grey-on-white (unreadable in light mode) and white-on-dark (unreadable in dark mode)
- **Theme-aware chart axis colors** — axis labels and tick marks now use the correct
  foreground color in both light and dark themes
- **TS type annotation in `define:vars` script** — removed; Astro injects `define:vars`
  blocks as plain JS, not TypeScript
- **Image refs with spaces/parens in filenames** — local image references in markdown
  descriptions are now stripped before rendering to avoid broken inline `<img>` tags

---

## [0.1.0-dev] — 2026-04-10

### New feature — Per-instance user limit

Operators can now cap the maximum number of registered users on an instance.

- **`bincio/serve/db.py`**
  - New `settings` table (key/value, upsert-safe via `ON CONFLICT DO UPDATE`).
  - `count_users(db)` — returns total number of rows in `users`.
  - `get_setting(db, key)` / `set_setting(db, key, value)` — generic persistent settings store.

- **`bincio/serve/server.py`** — `POST /api/register` now reads the `max_users` setting; if
  set to N > 0 and the current user count is already ≥ N, registration is rejected with
  HTTP 403 and a clear message. Imports `count_users` and `get_setting`.

- **`bincio/serve/init_cmd.py`** — new `--max-users N` flag (default 0 = unlimited). Saves
  the value to the `settings` table via `set_setting`. Printed in the init summary.

- **`bincio/serve/cli.py`** — new `--max-users N` flag on `bincio serve`. Writes to the DB
  on startup (lets operators change the limit without re-running `bincio init`). Startup
  banner now shows `Users: max N` or `Users: unlimited`.

---

### New feature — Original file storage option (upload & Strava sync)

Users can now choose whether to keep their source files on the server after processing.
Keeping originals allows reprocessing if the pipeline improves; discarding them is the
privacy-conscious choice. Previously, uploaded files were always deleted after processing.

- **`bincio/serve/db.py`** — `store_originals` is stored as a settings key. `bincio init`
  writes `store_originals=true` on first run.

- **`bincio/serve/server.py`** — `POST /api/upload` accepts a new `store_original: bool`
  form field. On success, if true, the staged file is moved to `{user_dir}/originals/`
  instead of being deleted. `GET /api/me` now includes `store_originals_default: bool`
  (read from the instance setting) so the frontend can pre-populate the checkbox.
  `POST /api/strava/sync` checks the `store_originals` instance setting; if true, creates
  `{user_dir}/originals/strava/` and passes it as `originals_dir` to `run_strava_sync`.

- **`bincio/edit/server.py`** — `POST /api/upload` gains the same `store_original` form
  field with identical behaviour (originals stored in `{data_dir}/originals/`).

- **`bincio/edit/ops.py`** — `run_strava_sync` gains an `originals_dir: Optional[Path]`
  parameter, passed through to `ingest.strava_sync`.

- **`bincio/extract/ingest.py`** — `strava_sync` gains `originals_dir: Optional[Path]`.
  When set, saves `{"meta": …, "streams": …}` as JSON to
  `originals_dir/{activity_id}.json` before processing each activity. This preserves the
  raw Strava API response for future reprocessing without needing another API call.

- **`bincio/serve/init_cmd.py`** — sets `store_originals=true` in the settings table on
  first init (skipped if the key already exists, so re-running init doesn't override
  an operator's choice).

- **`site/src/layouts/Base.astro`** — upload modal file view gains a "Keep original file on
  server" checkbox. Defaults to unchecked; pre-checked after login if the instance setting
  is `true` (read from `store_originals_default` in the `/api/me` response). The checkbox
  value is sent as the `store_original` form field.

- **`bincio/serve/server.py`** and **`bincio/edit/server.py`** — `Form` added to the
  FastAPI imports (was missing, causing a startup `NameError`).

---

### New feature — About page (multilingual)

New static `/about/` page explaining the project, with a Ko-fi donation button, data
storage disclaimer, and early-software caveats. Available in four languages.

- **`site/src/pages/about/index.astro`** — English
- **`site/src/pages/about/it/index.astro`** — Italian
- **`site/src/pages/about/es/index.astro`** — Spanish
- **`site/src/pages/about/ca/index.astro`** — Catalan

All four pages share the same structure:
- Language switcher (EN / IT / ES / CA) in the top-right corner.
- Ko-fi donation button (`https://ko-fi.com/brutsalvadi`) at the top.
- **Community stats section** — fetches `GET /api/stats` on load; shown only in
  multi-user mode (silently hidden in single-user mode where the endpoint doesn't exist).
  Displays total member count and an indented invitation tree: each row shows display name,
  `@handle`, membership duration (days / months), and either "founder" or "invited by @X".
  UI labels are fully translated per language.
- Sections: What is this · Your data on this server · Early-stage software · Disclaimer ·
  Open source.
- All pages use `public={true}` so they bypass the instance auth wall.

"About" link added to the main nav bar (visible when not on a public page).
The upload modal's "Keep original file" checkbox links to `/about/` for context.

---

### New feature — Community stats API

- **`bincio/serve/db.py`** — `get_member_tree(db)` joins `users` with `invites` (on
  `used_by`) to reconstruct the invitation graph. Returns a list ordered oldest-first with
  `handle`, `display_name`, `created_at`, and `invited_by` (inviter handle or `None` for
  the founder/admin).

- **`bincio/serve/server.py`** — new public `GET /api/stats` endpoint (no auth required).
  Returns `user_count` and a `members` array where each entry includes `handle`,
  `display_name`, `member_since` (Unix timestamp), `member_for_days`, and `invited_by`.

---

### Fix — `bincio dev` now watches data directory for live re-merge

Previously, editing a sidecar or running `bincio extract` while `bincio dev` was running
required a manual restart to pick up changes. Now a background watcher thread re-merges
automatically.

- **`bincio/dev.py`** — new `_watch_data(data)` function, started as a daemon thread
  alongside `bincio serve`. Uses `watchfiles` (already bundled with `uvicorn[standard]`,
  no new dependency) for OS-level file event watching — no polling.
  - Watches every `{user_dir}/edits/` and `{user_dir}/activities/` directory.
  - On any change, identifies which users were affected and calls `merge_all(user_dir)`
    for each.
  - Skips churn files written by merge itself (`.timeseries.json`, `.geojson`,
    `index.json`) to avoid re-triggering.
  - Prints `↺ {handle}: merged` on each successful re-merge; warns on failure.
  - Astro dev picks up the result automatically since `public/data` is a symlink into
    the live data directory.

---

### Tests

- **`tests/test_server_imports.py`** (new) — smoke tests that import `bincio.serve.server`
  and `bincio.edit.server` at module level, catching `NameError`, missing imports, and
  syntax errors before they reach the runtime. Also asserts that key routes (`/api/me`,
  `/api/upload`, `/api/strava/sync`, `/api/register`, `/api/activity/{activity_id}`) are
  registered on each app.

---

## [0.1.0-dev] — 2026-04-06

### New feature — Strava sync from UI

- **`bincio/extract/strava_api.py`** (new) — Strava OAuth + activity API integration:
  OAuth URL generation, authorization code exchange, token refresh, paged activity list
  fetching, stream fetching (time, latlng, altitude, HR, cadence, power, velocity), and
  conversion of the API response directly to `ParsedActivity` (no file download needed).
  Token stored in `<data-dir>/strava_token.json`; `last_sync_at` tracks incremental syncs.

- **`bincio/edit/server.py`** — three new endpoints:
  - `GET /api/strava/status` — returns `{configured, connected, last_sync}` for the UI
  - `GET /api/strava/auth-url` — returns the OAuth URL for the popup window
  - `GET /api/strava/callback` — exchanges auth code, saves token, redirects to site with `?strava=connected`
  - `POST /api/strava/sync` — fetches activities since `last_sync_at`, runs extract pipeline,
    updates `index.json`, runs `merge_all()`, and updates `last_sync_at` in the token file

- **`bincio/edit/cli.py`** — `--strava-client-id` and `--strava-client-secret` flags added
  (also read from `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` env vars). Strava sync is
  disabled (endpoints return 400) when credentials are not provided.

- **`site/src/layouts/Base.astro`** — upload modal redesigned with a "choose source" screen:
  two buttons — "Upload file" (existing drag-and-drop) and "Sync from Strava". Strava button
  shows "Not configured" when the server lacks credentials, or opens an OAuth popup window.
  After connecting, a "Sync now" button triggers the sync and reloads the feed on import.

**Setup:** register `http://localhost:4041/api/strava/callback` as an allowed redirect URI
in your Strava app settings, then run:
```
bincio edit --strava-client-id YOUR_ID --strava-client-secret YOUR_SECRET
# or via env vars: STRAVA_CLIENT_ID=... STRAVA_CLIENT_SECRET=... bincio edit
```

**Note on the upload button:** the button is visible whenever `PUBLIC_EDIT_URL` is set in
`site/.env`, regardless of whether the edit server is running. This is intentional — the env
var is the "edit mode enabled" flag. Remove it from `.env` to hide the button.

## [0.1.0-dev] — 2026-04-01

### Security fixes (second-pass audit)

- **Sport value not validated before YAML write** (`edit/server.py`) — `sport` field now validated against `SPORTS` allowlist before being written to the sidecar
- **No image content-type validation** (`edit/server.py`) — arbitrary file uploads rejected; only `image/*` content types accepted
- **XSS via unescaped filename in `innerHTML`** (`edit/server.py`) — `escapeHtml()` applied to filenames in `renderImageList` before interpolation
- **No upload size limit** (`edit/server.py`) — 50 MB limit enforced before writing to disk; returns HTTP 413 on oversize uploads
- **Exception message leaks internal paths** (`edit/server.py`) — 422 response now returns `type(exc).__name__` only, not `str(exc)` which could expose filesystem paths

### Bug fixes — data (second-pass audit)

- **Disambiguated ID not written into JSON body** (`writer.py`) — collision suffix was added to the filename but `detail["id"]` still held the original ID; fixed to update `detail["id"]` after disambiguation
- **`write_activity` return value ignored** (`cli.py`) — caller was using the pre-collision ID to build the index summary; now captures the canonical return value from `write_activity`
- **TOCTOU race in collision guard** (`writer.py`, `cli.py`) — concurrent workers could both see no existing file and overwrite each other; workers now write to unique `.pending.json` files and the main process arbitrates by quality score via `finalize_pending()`
- **`athlete.yaml` merge has no field allowlist** (`render/merge.py`) — `merge_all()` now applies only `_ATHLETE_EDITABLE` keys (`max_hr`, `ftp_w`, `hr_zones`, `power_zones`, `seasons`, `gear`) from the sidecar
- **Timezone offsets without colon** (`parsers/tcx.py`) — regex updated to `[+-]\d{2}:?\d{2}` so `+0200` is handled alongside `+02:00`
- **Power data in GPX extensions not parsed** (`parsers/gpx.py`) — extension tags `pwr`, `power`, and `watts` now parsed; MMP no longer always `None` for GPX files with power meters
- **`speeds` array misaligned with `coordinates`** (`simplify.py`) — speeds array now uses the same lat/lon null filter as coordinates

### Bug fixes — frontend (second-pass audit)

- **Hardcoded nav links ignore `BASE_URL`** (`Base.astro`) — `/`, `/stats/`, `/athlete/` nav hrefs now use `baseUrl` from `import.meta.env.BASE_URL`
- **Undeclared `error` variable in `uploadImages`** (`EditDrawer.svelte`) — catch block now uses `saveStatus`/`saveOk` (existing error state) instead of undeclared `error`
- **`ResizeObserver` stale closure in MmpChart** (`MmpChart.svelte`) — reactive variables keep the closure current so resize re-renders with correct data after range selection changes
- **`resetTrim` guard always true** (`ActivityCharts.svelte`) — tracks `lastResetTab` to force trim reset on every tab switch regardless of whether min/max happen to match
- **No `onDestroy` cleanup in ActivityCharts** (`ActivityCharts.svelte`) — chart SVG now removed on component unmount to prevent memory leaks
- **Invalid URL `tab` parameter shows blank content** (`AthleteView.svelte`) — `tab` query param validated against `TABS` array; invalid values fall back to `'power'`

### Schema (second-pass audit)

- **`activity_summary` missing `custom` property** (`bas-v1.schema.json`) — `merge.py` always adds `custom` to summaries but it wasn't declared; added to schema
- **`skiing` missing from SCHEMA.md sport enum** — added alongside sub-sport enum updates
- **Summary fields table incomplete** (`SCHEMA.md`) — `sub_sport`, `mmp`, `best_efforts`, `best_climb_m`, `preview_coords` all added to the summary fields table

### Tests (second-pass audit)

- **`test_id_utc_conversion`** (`test_writer.py`) — verifies non-UTC timestamps are converted to UTC in generated IDs
- **`test_build_summary_required_fields`** (`test_writer.py`) — verifies all schema-required fields present in `build_summary` output
- **Skiing and swimming sport variants** (`test_sport.py`) — `test_skiing_variants` and `test_swimming_variants` added
- **Non-canonical IDs in test fixtures** (`test_merge.py`) — fixture IDs updated to canonical `2024-01-01T080000Z-morning-ride` format

### Security fixes

- **Path traversal prevention** (`edit/server.py`) — all routes now validate `activity_id` against `[a-zA-Z0-9\-]+` regex via `_check_id()`; invalid IDs return 400
- **Path traversal in `delete_image`** — `filename` parameter now stripped to basename via `Path(filename).name` before use in filesystem paths
- **Path traversal in `upload_activity`** — uploaded `file.filename` stripped to basename via `Path(file.filename).name`
- **XSS in activity description** (`ActivityDetail.svelte`) — `marked()` output now wrapped in `DOMPurify.sanitize()` before `{@html}` rendering
- **CORS restricted** (`edit/server.py`) — `allow_origins=["*"]` replaced with `allow_origin_regex` matching `localhost` origins only
- **YAML injection in `hide_stats`** — values filtered against a `STAT_PANELS` allowlist before writing to YAML frontmatter
- **Regex injection in `deleteImage`** (`EditDrawer.svelte`) — filename special characters escaped before `RegExp` construction

### Bug fixes — data

- **MMP sliding window on non-contiguous data** (`metrics.py`) — power series now built as a dense 1 Hz array with gaps zero-filled (standard GoldenCheetah/WKO approach); recording pauses no longer inflate MMP values
- **Best-effort times on non-contiguous data** (`metrics.py`) — speed series uses same zero-fill; pauses count as 0 km/h so windows cannot span them silently
- **Activity ID collision** (`writer.py`) — when two activities share the same start-time + title, the second is disambiguated with a 6-character source hash suffix; re-extracting the same file is idempotent
- **Misaligned lat/lon arrays** (`ActivityMap.svelte`) — lat and lon were filtered for nulls independently; now filtered as pairs so indices always stay aligned
- **Falsy `0.0` speed check** (`metrics.py:89-90`, `parsers/fit.py:89`) — `if avg_speed_kmh` / `if speed_raw` replaced with `is not None`; 0.0 is no longer silently dropped
- **TCX timestamps with numeric timezone offsets** (`parsers/tcx.py`) — `+02:00`-style offsets now parsed correctly and converted to UTC; previously crashed with `ValueError`

### Bug fixes — frontend

- **Backdrop dismiss fires `saved` event** (`EditDrawer.svelte`) — backdrop click and ×-button now dispatch `close` instead of `saved`, preventing unsaved data from overwriting the displayed title/description
- **No error handling in `uploadImages`** (`EditDrawer.svelte`) — wrapped upload loop in try/catch/finally so a network error clears the `uploading` spinner and surfaces an error message instead of locking the UI
- **Stats page pagination** (`StatsView.svelte`) — heatmap now shows 4 years per page with ← Newer / Older → controls; `?page=` persisted in URL

### Bug fixes — data (continued)

- **`_best_climb` joins non-contiguous elevation segments** (`metrics.py`) — `None` elevation samples now reset the Kadane's window instead of being skipped and joined; GPS blackout segments can no longer inflate climb values
- **`save_athlete` mutated `athlete.json` in-place** (`edit/server.py`, `render/merge.py`) — server now only writes `edits/athlete.yaml`; `merge_all()` applies the sidecar overlay when producing `_merged/athlete.json`, preserving extract immutability
- **`preview_coords` off-by-one** (`simplify.py`) — subsampler was appending the final GPS point on top of `max_points`, returning `max_points + 1`; now samples `max_points - 1` slots then appends last point
- **Non-monotonic timeseries** (`timeseries.py`) — dedup guard changed from `t == last_t` to `t <= last_t`; backwards timestamps from corrupt files are now dropped instead of creating out-of-order `t` arrays
- **`_patch_duplicate_of` silently swallows exceptions** (`cli.py`) — changed bare `except: pass` to log a warning so failures surface during extract

### Bug fixes — frontend (continued)

- **Hardcoded `/activity/` URLs ignore `BASE_URL`** (`RecordsView.svelte`, `Base.astro`) — `base` prop now threaded from Astro page → `AthleteView` → `RecordsView`; upload redirect uses `import.meta.env.BASE_URL` via `define:vars`
- **No error handling on stats page fetch** (`StatsView.svelte`) — `index.json` fetch wrapped in try/catch; error message displayed in place of heatmap instead of silent failure
- **Map doesn't resize on container change** (`ActivityMap.svelte`) — `ResizeObserver` added to call `map.resize()` when the map container is resized
- **`formatDuration` fractional seconds** (`format.ts`) — input floored with `Math.floor` before arithmetic; `1500.7 s` no longer displays as `25m 00.7s`
- **Empty YAML config crashes** (`render/cli.py`, `edit/cli.py`) — `yaml.safe_load()` result guarded with `or {}`; empty config file no longer throws `AttributeError` on `.get()`

### Schema

- **Writer output now matches schema** (`bas-v1.schema.json`) — `mmp`, `best_efforts`, `best_climb_m`, `preview_coords`, and `custom` are all declared in the schema; previously `additionalProperties: false` caused validation failures
- **`skiing` added to sport enum** — was produced by the extractor but missing from the schema definition
- **Sub-sport enum extended** — `nordic`, `alpine`, `open_water`, `pool` added to schema
- **Activity ID format corrected in SCHEMA.md** — examples updated from `+0200` offset to `Z` UTC suffix (matching actual code behaviour since v0.1.0)

### Tests

- **Exact ID assertions** (`test_writer.py`) — `test_id_with_title` and `test_id_without_title` now assert the full ID string (`2024-06-01T073012Z-morning-ride`) instead of substrings
- **`normalise_sub_sport` test coverage** (`test_sport.py`) — 3 new tests: Strava CamelCase conversion, ski variants, and unknown/None → `None`
- **Invalid sport in test_merge** (`test_merge.py`) — `sport: "gravel"` replaced with valid `"running"`

### Navigation

- **URL state persistence** — filter and tab state is now stored in the URL query string so the browser back button always restores the exact view you left
  - Activity feed (`/`): `?sport=cycling` — sport filter survives back navigation
  - Stats page (`/stats/`): `?sport=cycling` — same
  - Athlete page (`/athlete/`): `?tab=records` — active tab survives back navigation
  - Records tab (`/athlete/?tab=records`): `?sport=cycling` — sport filter within records also persisted; full URL example: `/athlete/?tab=records&sport=cycling`
  - All use `history.replaceState` (not `pushState`) so clicking filters does not pollute the history stack — back always goes to the previous *page*, not the previous filter state
  - Default values are omitted from the URL for cleanliness (`sport=all` and the default tab are never written)

### Sport classification

- **Sub-sport detection** — `normalise_sub_sport()` in `sport.py` infers sub_sport from raw sport type strings
  - CamelCase Strava types handled correctly (`MountainBikeRide` → `cycling / mountain`, `GravelRide` → `cycling / gravel`, `AlpineSki` → `skiing / alpine`, `NordicSki` → `skiing / nordic`, etc.)
  - All parsers (Strava importer, GPX, TCX) now populate `sub_sport`; FIT parser was already correct
  - Sub-sport shown as a secondary pill on activity detail page: **🚴 Cycling** + **MTB**

### Developer experience

- **`--dev N` flag** on `bincio extract` — samples N files evenly across the full file list (date + format diversity) and writes to `/tmp/bincio_dev/`; `incremental` is disabled automatically
- **`--dev N` flag** on `bincio import strava` — imports only the N most recent activities to `/tmp/bincio_dev/`
- Dev loop: `bincio extract --dev 50 && bincio import strava --dev 50 && bincio render --serve --data-dir /tmp/bincio_dev`

### Data ingestion

- **`bincio import strava`** — OAuth2 Strava importer (`bincio/import_/strava.py` + `bincio/import_/cli.py`)
  - One-shot local OAuth2 callback server (port 8976); opens browser, receives code, exchanges for tokens
  - Tokens saved to `~/.config/bincio/strava.json`; auto-refreshed on expiry (6h TTL)
  - Fetches paginated activity list with `after=` timestamp for efficient incremental runs
  - Per activity: `GET /activities/{id}/streams` → `_strava_to_parsed()` → `compute()` → `write_activity()`
  - `_patch_from_summary()`: fills `None` metrics from Strava summary when sensors are missing (manual entries, indoor rides)
  - Sync state persisted in `data_dir/_strava_sync.json` (imported IDs + last sync timestamp)
  - Rate limit tracking via `X-RateLimit-Usage`; warns at 85% of 15-min window; auto-retries on 429
  - Credentials read from (in order): CLI flags → env vars → `extract_config.yaml` under `import.strava`
  - Install: `uv sync --extra strava`

- **Web file upload** — `POST /api/upload` in `bincio/edit/server.py`
  - Accepts FIT/GPX/TCX (`.gz` variants too); 409 if activity already exists
  - Runs full extract pipeline inline: `parse_file()` → `compute()` → `write_activity()` → `merge_all()`
  - Staged to `data_dir/_uploads/` during processing; cleaned up in `finally`
  - `↑` button in site nav, gated behind `PUBLIC_EDIT_URL`; drag-and-drop modal; auto-redirects on success

- **`extract_config.yaml` is now gitignored** — safe to store credentials under `import.strava`
  - `StravaConfig` dataclass added to `bincio/extract/config.py`; parsed from `import.strava:` block
  - `extract_config.example.yaml` is the tracked template

- **Theme-aware heatmap** (`StatsView.svelte`) — `applyIntensity()` now lerps from the correct
  background colour in both dark (zinc-800 `#27272a`) and light (zinc-200 `#e4e4e7`) modes;
  `emptyColor` and `baseRgb` reactive to `data-theme` via `MutationObserver`

### Athlete page

- **`/athlete` page** — three-tab layout: Power Curve · Records · Profile
- **Mean Maximal Power (MMP) curve** — computed at extract time for each activity with power data
  - Sliding-window O(n) algorithm over 1 Hz power timeseries; 15 standard durations (1 s → 1 h)
  - Multi-curve overlay with range selector: All time / Last 365 d / Last 90 d / user-defined seasons
  - Log-scale x-axis via Observable Plot; FTP reference line; per-point tooltips
  - Seasons configurable in `extract_config.yaml` under `athlete.seasons`
- **Personal records (Records tab)** — sport-specific best efforts computed via sliding window
  - Running: 400 m, 1 km, 1 mile, 5 km, 10 km, half marathon, marathon
  - Cycling: 5 km, 10 km, 20 km, 50 km, 100 km
  - Swimming: 100 m, 200 m, 500 m, 1 km, 2 km
  - Table shows time, pace (running) or speed (cycling/swimming), date, activity link
  - Hiking / Walking: longest distance and most elevation gain
  - **Best climbs** — top 10 biggest single climbs (Kadane's algorithm on 1 Hz elevation deltas); ranked table with elevation, date, activity link
- **Profile tab** — max HR, FTP, HR zones, power zones
- **`bincio edit` athlete API** (`GET /api/athlete`, `POST /api/athlete`) — reads/writes `edits/athlete.yaml`
- **`AthleteDrawer.svelte`** — slide-in profile editor (gated behind `PUBLIC_EDIT_URL`)
  - Max HR and FTP number inputs
  - HR and power zone tables: changing a zone's upper bound auto-cascades to the next zone's lower bound
  - Season list: name + date range, add/remove rows
- **`athlete.json`** — written at extract time; contains pre-aggregated MMP curves and records; symlinked into `_merged/` by `merge_all()`

### Extraction pipeline

- **MMP computation** — `compute_mmp()` added to `metrics.py`; stored in both detail JSON and index summary (enables client-side season filtering without extra fetches)
- **Best-effort computation** — `compute_best_efforts()` two-pointer sliding window on 1 Hz speed; `_best_climb()` Kadane's on elevation deltas
- **`write_athlete_json()`** — aggregates MMP and records from all summaries into `athlete.json`

### Scripts

- **`scripts/backfill.py`** — backfills `mmp`, `best_efforts`, and `best_climb_m` into existing activity JSONs from already-extracted 1 Hz timeseries; no FIT re-parsing needed (~20 s for 2500 activities)

---

## [0.1.0] — 2026-03-29

### Extraction pipeline

- **Parallel extraction** — activities now processed with `ProcessPoolExecutor`; large shared state (Strava lookup, known hashes) sent once per worker via `initializer=` rather than once per task
- **TCX parser fixes** — handles both `http://` and `https://` Garmin namespace URIs
- **Sport classification overhaul**
  - FIT parser now reads sport from the `session` frame as fallback when no separate `sport` frame is present (fixes Karoo and Strava-generated FIT files)
  - Strava CSV `Activity Type` used as authoritative override when present
  - Expanded sport mapping: e-bike variants (`ebikeride`, `e_bike_ride`), `ride`, `run`, date-prefix stripping, and more
  - Skiing added as first-class sport: `cycling` | `running` | `hiking` | `walking` | `swimming` | `skiing` | `other`
  - Nordic sub-sport: FIT sub_sport values `cross_country_skiing`, `nordic_skiing`, `skate_skiing`, `backcountry_skiing` → `"nordic"`
- **Distance calculation fix** — when a FIT device records `distance = 0.0` (not `null`), the extractor now falls back to haversine-computed GPS distance instead of using the zero value directly; fixes skiing activities that had valid tracks and speeds but showed 0 km
- **`metadata_csv` is fully optional** — omitting it from config works cleanly; only needed for Strava bulk exports

### Site — maps & charts

- **MapLibre GL map** fully working on the activity detail page
  - Static import + `optimizeDeps.include` (not `exclude`) fixes silent tile worker failure
  - `build.target: 'es2022'` required for MapLibre's ES2022 class field syntax
  - MapLibre v5 requires explicit `center`/`zoom` in Map constructor and `setLngLat()` before `addTo()`
- **Observable Plot charts** (elevation, speed, HR, cadence) working
  - Switched from dynamic `await import()` to static import — fixes unreliable Svelte reactivity
  - Curve name is `"monotone-x"` not `"monotoneX"`
- **Power chart** added as fifth tab alongside elevation/speed/HR/cadence
- **HR and power zone histograms** — configurable zone boundaries via `athlete.hr_zones` / `athlete.power_zones` in `extract_config.yaml`; histogram x-axis capped at actual data max so sentinel values (`999`, `9999`) don't stretch the axis
- **Adjustable trim range** on histograms

### Site — activity feed

- **SVG track thumbnails** on feed cards — drawn from `preview_coords` (no extra fetch)
- **Sport filter bar** — pill buttons for All / Cycling / Running / Hiking / Walking / Swimming / Skiing / Other

### Site — stats page

- **Sport filter bar** — same pill UI as the feed; all stats and heatmap reflect the selected sport
- **Heatmap colour improvements**
  - Blended colours in "All" mode: each cell's RGB is a weighted average of sport colours by distance
  - Percentile-based intensity scaling (active): each day ranked against all active days, spreading colour evenly regardless of km outliers; configurable back to linear/max-relative (documented in CLAUDE.md)
  - `applyIntensity()` lerps from zinc-800 background to full sport colour — dim cells fade into the background rather than going black
  - `$: cellColors` precomputed as a reactive `Map<string, string>` — fixes Svelte not re-rendering cells when filter changes
- **Month label fix** — labels embedded in the week-column flex grid (no more absolute-positioning bugs); `getWeeks()` uses local date formatting (`localISO()`) instead of `toISOString()` to avoid UTC/local mismatch that produced a spurious "Dec" label at column 0
- **Cell tooltips** — hovering a cell shows a floating card with date, and for each activity: name, sport, distance, duration; each activity is a clickable link to its detail page; 120 ms grace period when moving from cell to tooltip

### Site — activity editing (`bincio edit`)

- **`bincio edit` write API** — FastAPI server (`--data-dir`, default port 4041)
  - `GET /api/activity/{id}` — current values with sidecar overrides applied
  - `POST /api/activity/{id}` — writes sidecar `.md`, triggers `merge_all()`
  - `POST /api/activity/{id}/images` — multipart image upload
  - `DELETE /api/activity/{id}/images/{filename}`
- **Activity sidecar system** (`bincio/render/merge.py`)
  - Sidecars live in `edits/` alongside extracted data (never co-mingled with immutable BAS JSON)
  - Fields: `title`, `sport`, `description`, `hide_stats`, `highlight`, `private`, `gear`
  - `merge_all()` produces `_merged/` output; `public/data` → `_merged/` at runtime
- **`EditDrawer.svelte`** — slide-in drawer in the Astro site (no separate HTML from the server)
  - Opens in-page via Edit button; only rendered when `PUBLIC_EDIT_URL` env var is set
  - Title, sport dropdown, gear, markdown description textarea
  - Image drag-and-drop with chip list + delete
  - Hide-stats toggle buttons (elevation, speed, heart_rate, cadence, power)
  - Highlight and private flags
  - Optimistic local update on save — title and description update immediately without reload
- **Photo gallery + lightbox** on activity detail page — keyboard navigation (←/→/Esc), filename + counter overlay
- **Markdown descriptions** rendered with `marked`; local relative images suppressed from inline rendering (shown in gallery instead)

### Documentation

- **README** rewritten — philosophy statement front and centre, clear two-stage architecture diagram, quick start
- **CHEATSHEET.md** added — daily workflow, all CLI commands, config reference, privacy table, patching snippets, diagnostic scripts, key files table
- **CLAUDE.md** updated — MapLibre GL v5 gotchas, Observable Plot curve names, heatmap colour scaling approaches (linear vs percentile), sidecar/edit architecture decisions
- **`extract_config.example.yaml`** cleaned up — personal paths removed, `metadata_csv` commented out with explanation

### Infrastructure

- `publish.sh` — builds and pushes static site to GitHub Pages via orphan branch
