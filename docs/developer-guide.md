# Developer Guide

This guide is for developers contributing to BincioActivity.

## Prerequisites

- **Python 3.12+** with [uv](https://docs.astral.sh/uv/)
- **Node 20+** with npm
- **Git**

## Local Setup

```bash
git clone https://github.com/brutsalvadi/bincio-activity.git
cd bincio-activity

# Install Python dependencies
uv sync

# Install optional extras for multi-user development
uv sync --extra serve --extra edit

# Install Node dependencies (for the site)
cd site && npm install && cd ..
```

## Running Locally

### Single-user (fastest for testing extract logic)

```bash
# Configure where to find your test activities
cp extract_config.example.yaml extract_config.yaml
$EDITOR extract_config.yaml  # set input.dirs and output.dir

# Extract activities
uv run bincio extract

# Start the dev server (no login, no API server)
uv run bincio dev --data-dir ~/bincio_data
# → http://localhost:4321/u/{handle}/
```

### Multi-user (for testing auth, write API, admin features)

```bash
# Create a test instance with an admin user
uv run bincio init --data-dir /tmp/bincio_test --handle testadmin

# Extract activities
uv run bincio extract --output /tmp/bincio_test

# Start everything (bincio serve + astro dev)
uv run bincio dev --data-dir /tmp/bincio_test
# → http://localhost:4321 (login with testadmin/{password})
```

Ctrl+C stops both servers.

## Running Tests

```bash
# All tests
uv run pytest

# Specific test file
uv run pytest tests/extract/test_parsers.py

# Specific test function
uv run pytest tests/extract/test_parsers.py::test_gpx_parser

# With verbose output
uv run pytest -vv

# With coverage report
uv run pytest --cov=bincio
```

Tests are in `tests/` and use pytest + fixtures for DRY test data.

## Project Structure

```
bincio/
  extract/           Python package for GPX/FIT/TCX parsing
    models.py        DataPoint, ParsedActivity, LapData
    parsers/         GPX, FIT, TCX parsers + factory
    sport.py         Sport name normalization
    metrics.py       Haversine-based stats (distance, elevation)
    timeseries.py    1Hz downsampling → BAS timeseries object
    simplify.py      RDP track simplification (no external deps)
    dedup.py         Exact + fuzzy duplicate detection
    strava_csv.py    Strava activities.csv importer
    writer.py        BAS JSON + GeoJSON output
    config.py        extract_config.yaml loader
    cli.py           `bincio extract` command
  render/
    cli.py           `bincio render` command
    merge.py         Sidecar edit overlay (produces _merged/)
  edit/
    cli.py           `bincio edit` FastAPI server
    server.py        Edit API endpoints
  serve/
    cli.py           `bincio serve` command
    server.py        Multi-user FastAPI server (auth, invites, admin)
    db.py            SQLite data layer
    init_cmd.py      `bincio init` bootstrap
  shared/            (if needed)

site/                Astro + Svelte + Tailwind frontend
  src/
    layouts/         Base.astro (auth wall, nav)
    pages/           Routes (activity feed, detail, login, etc.)
    components/      Svelte components (maps, charts, edit drawer)
    lib/             TypeScript utilities (types, format, dataloader)

tests/               pytest test suite
  extract/
  render/
  serve/
  fixtures/          Shared test data
```

## Key Concepts

### BAS (BincioActivity Schema)

Activity data flows as **BAS JSON** files in `{user}/activities/`. The format is specified in [SCHEMA.md](schema.md).

Key files:

- `{id}.json` — activity metadata + timeseries
- `_merged/` symlink — sidecar edits overlaid on activities
- `edits/{id}.md` — user-created sidecar (optional)

### Shard model

Multi-user instances use a **shard manifest** (root `index.json`) that lists per-user shards. The browser fetches all shards concurrently and merges them. This allows:

- Federation (remote shard URLs)
- Yearly pagination
- No data duplication

### Extract pipeline

```
GPX/FIT/TCX files
    ↓ (parse)
ParsedActivity
    ↓ (calculate metrics)
BAS Activity JSON
    ↓ (downsample to 1Hz)
Timeseries
    ↓ (simplify with RDP)
GeoJSON
    ↓ (write)
activities/{id}.json + activities/{id}.geojson
```

### Render pipeline

```
{user}/
  activities/*.json (extracted)
  edits/*.md (user sidecars)
    ↓ (merge_all)
_merged/
  index.json (sidecar edits applied)
  activities/{id}.json
  {id}.geojson
    ↓ (astro build)
site/dist/
```

Editing does not require re-extraction.

## Making Changes

### Adding a new endpoint

1. Add a route in `bincio/serve/server.py` (or `bincio/edit/server.py` for single-user)
2. Add Pydantic models for request/response if needed
3. Add tests in `tests/serve/`
4. Update `docs/reference/api.md` with the new endpoint
5. If admin-only, protect it with `await _require_admin(bincio_session)`

### Adding a parser for a new format

1. Create `bincio/extract/parsers/myformat.py`
2. Implement a parser class with `parse(file_path: Path) -> ParsedActivity`
3. Register it in `bincio/extract/parsers/__init__.py`
4. Add tests in `tests/extract/test_parsers.py`

### Modifying BAS schema

1. Edit `schema/bas-v1.schema.json` (JSON Schema)
2. Update `SCHEMA.md` (human-readable spec)
3. Update TypeScript types in `site/src/lib/types.ts`
4. Add a migration if the change is breaking

### Frontend changes

**Svelte components** are in `site/src/components/`. Key ones:

- `ActivityFeed.svelte` — activity grid + filters
- `ActivityDetail.svelte` — activity page (maps, charts, photos)
- `EditDrawer.svelte` — sidecar editor

Use `uv run bincio dev` to test changes live. The site hot-reloads on file changes.

## Code Style

- **Python:** PEP 8, type hints where possible
- **JavaScript/TypeScript:** ESLint + Prettier (configured in `site/`)
- **Svelte:** No self-closing non-void tags; interactive divs need `role` + keyboard handler

## Git Workflow

1. Create a branch: `git checkout -b feature/my-feature`
2. Make changes and test locally
3. Commit: `git commit -m "Clear, specific commit message"`
4. Push: `git push origin feature/my-feature`
5. Open a pull request

**Commit message style:**

- Imperative mood ("add feature", not "added feature")
- Reference issues if relevant: "fix #123"
- First line ≤ 50 characters
- Blank line, then detailed explanation if needed

## Performance Considerations

### Extract speed

- **ProcessPoolExecutor with initializer** — large data (Strava lookups, hash sets) is sent once per worker, not per task
- **Haversine** — 10x faster than geopy for distance calculations
- **Lazy parsing** — FIT files decoded only once per task

### Render speed

- **RDP simplification** — custom implementation (no external wheels for Pyodide)
- **Gzip compression** — activity JSON and geojson are served gzipped
- **Concurrent shard fetch** — browser loads all shards in parallel

### Frontend

- **MapLibre GL v5** — requires explicit center/zoom and workarounds
- **Observable Plot** — use hyphenated curve names (e.g. `"monotone-x"`)
- **Client-only for complex components** — use `client:only="svelte"` for activity detail to avoid hydration mismatches

## Debugging

### Python

```bash
# Interactive debugger
uv run python -m pdb -m bincio.extract.cli

# Or use breakpoint() in code
breakpoint()
uv run bincio extract
```

### TypeScript

Check your editor's TypeScript integration. The site has strict `tsconfig.json`.

### Frontend

- Open DevTools (F12)
- Check the Network tab for API calls
- Check Console for client-side errors

### Database

```bash
# Inspect the SQLite database directly
sqlite3 /tmp/bincio_test/instance.db
> SELECT * FROM users;
```

## Documentation

- User-facing docs go in `docs/`
- API docs are auto-generated from FastAPI routes (and should be typed with Pydantic models)
- Code comments should explain *why*, not *what*

## Known Issues & Limitations

See the [GitHub repository](https://github.com/brutsalvadi/bincio-activity) for known issues and planned features.

## Contributing

Contributions are welcome! Please:

1. Check existing issues/PRs so you're not duplicating work
2. Open an issue first for large changes
3. Include tests for new features
4. Update docs (user guide, API ref, or developer guide)
5. Follow the code style guidelines

## See also

- [Architecture](architecture.md) — system design and data flow
- [BAS Schema](schema.md) — activity data format
- [API Reference](reference/api.md) — all HTTP endpoints
