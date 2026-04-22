# Getting started

BincioActivity turns a folder of GPX/FIT/TCX files into a static website you host yourself. No database. No cloud dependency. No account.

## Prerequisites

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- Node ≥ 20 and npm (for the site)
- Your activity files (Strava export, Garmin export, Karoo, etc.)

## Install

```bash
git clone https://github.com/brutsalvadi/bincio-activity.git
cd bincio-activity
uv sync
```

## Configure

```bash
cp extract_config.example.yaml extract_config.yaml
$EDITOR extract_config.yaml
```

Set your handle and input directory at minimum:

```yaml
owner:
  handle: yourname          # used in URLs: /u/yourname/
  display_name: Your Name

input:
  dirs:
    - ~/your-activity-data/activities

output:
  dir: ~/bincio_data        # instance root; activities go into ~/bincio_data/yourname/
```

The config file is gitignored — safe to store Strava credentials here.

---

## Extract

```bash
uv run bincio extract
```

Reads all GPX/FIT/TCX files and writes a BAS data store to `~/bincio_data/yourname/`. Re-running is safe — unchanged files are skipped (hash-based).

> `--output` overrides `output.dir` from the config and is the **instance root**,
> not the user directory. The handle is always appended automatically:
> `bincio extract --output ~/bincio_data` → writes to `~/bincio_data/yourname/`.

---

## Single-user — no login, static site

```bash
# Build and preview
cd site && npm install && cd ..
uv run bincio dev --data-dir ~/bincio_data
# → http://localhost:4321
```

`bincio dev` merges edits, builds the shard manifest, and starts `astro dev`. No login required — the site opens directly at `/u/yourname/`.

To build for deployment (no live server):

```bash
uv run bincio render --data-dir ~/bincio_data
# output: site/dist/
```

See [Single-user deployment](deployment/single-user.md).

---

## Multi-user — shared instance, login required

```bash
uv sync --extra serve

# One-time: create the instance database and admin account
uv run bincio init --data-dir ~/bincio_data --handle yourname

# Start everything
uv run bincio dev --data-dir ~/bincio_data
# → http://localhost:4321  (login with the password set during init)
```

`bincio dev` detects the `instance.db` and automatically starts `bincio serve` alongside `astro dev`. Ctrl+C stops both.

See [Multi-user deployment](deployment/multi-user.md).

---

## Enable the edit UI (single-user)

The edit UI lets you rename activities, add descriptions, upload photos, and sync from Strava — from the browser.

```bash
uv sync --extra edit
uv run bincio edit --data-dir ~/bincio_data
# Add to site/.env:
# PUBLIC_EDIT_URL=http://localhost:4041
```

In multi-user mode the edit UI is always available via `bincio serve` — no extra step needed.

---

## Next steps

- [Single-user deployment](deployment/single-user.md) — GitHub Pages, Netlify, VPS
- [Multi-user deployment](deployment/multi-user.md) — VPS with nginx, inviting users
- [CLI reference](reference/cli.md) — all commands and options
- [BAS schema](schema.md) — the data format and federation protocol
