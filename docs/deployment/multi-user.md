# Multi-user deployment

Multiple users share one bincio instance. The whole instance requires login to view (private by default). Activities are visible to all logged-in users; the `private` flag hides individual activities.

## Architecture

```
internet
    │
    ▼
 nginx / caddy
    ├── /*      → static files (site/dist/)
    └── /api/*  → proxy → bincio serve (127.0.0.1:4041)
```

`bincio serve` owns all dynamic behaviour — auth, user management, write operations. nginx serves static files and proxies API routes. `bincio serve` never handles static files.

Sessions are httpOnly cookies (`bincio_session`), stored in SQLite. The Astro site calls `GET /api/me` on page load to detect the logged-in user and update nav links.

## Data layout

```
/data/                          ← instance root
  instance.db                   ← SQLite: users, sessions, invites
  index.json                    ← shard manifest (no activity data)
  {handle}/
    index.json                  ← user's BAS feed (activities)
    _merged/                    ← sidecar-merged output (served to browser)
    activities/
    edits/
    athlete.json
    strava_token.json
```

The root `index.json` is a shard manifest — it lists user shard URLs but contains no activity data. Each user's `{handle}/index.json` is a valid standalone BAS feed. The browser resolves all shards concurrently and merges them into a combined feed.

This is the same layout used for single-user deployments — the only addition is `instance.db`.

## Step 1 — Initialise the instance

```bash
uv sync --extra serve

uv run bincio init \
  --data-dir /var/bincio \
  --handle dave \
  --display-name "Dave" \
  --name "My Bincio"
# prompted for password
```

This creates:
- `/var/bincio/instance.db` — SQLite database
- `/var/bincio/dave/` — admin user data directory
- `/var/bincio/index.json` — root shard manifest (with `"private": true`)
- Prints a first invite code

`bincio init` is idempotent — safe to re-run.

## Step 2 — Extract activities

Pass the **instance root** to `--output`. The handle is appended automatically:

```bash
uv run bincio extract --output /var/bincio
# → writes to /var/bincio/dave/
```

## Step 3 — Build the site

```bash
cd site && npm install && cd ..

uv run bincio render \
  --data-dir /var/bincio \
  --site-dir site

# Output: site/dist/
```

`bincio render` always:
1. Runs `merge_all()` for each user's directory
2. Rewrites the root `index.json` shard manifest
3. Symlinks `site/public/data → /var/bincio`
4. Builds the Astro site

Incremental rebuild (one user only, no full site rebuild):

```bash
uv run bincio render --data-dir /var/bincio --handle dave
```

## Step 4 — Configure nginx

```nginx
server {
    listen 443 ssl;
    server_name example.com;

    root /var/www/bincio;   # → site/dist/

    location / {
        try_files $uri $uri/ $uri.html =404;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:4041;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Copy `site/dist/` to `/var/www/bincio` after each build.

## Step 5 — Start bincio serve

```bash
uv run bincio serve \
  --data-dir /var/bincio \
  --site-dir /path/to/site
```

As a systemd service:

```ini
[Unit]
Description=bincio serve
After=network.target

[Service]
Type=simple
User=bincio
WorkingDirectory=/home/bincio/bincio-activity
ExecStart=uv run bincio serve --data-dir /var/bincio --site-dir site
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Local testing (before deploying)

```bash
# 1. Initialise the instance
uv run bincio init --data-dir /tmp/bincio_test --handle dave

# 2. Extract activities (pass instance root, not user dir)
uv run bincio extract --output /tmp/bincio_test
# → writes to /tmp/bincio_test/dave/

# 3. Start everything with one command
uv run bincio dev --data-dir /tmp/bincio_test
# → http://localhost:4321
```

`bincio dev` detects `instance.db`, starts `bincio serve` (port 4041) in the background and `astro dev` (port 4321) in the foreground. No `.env` file needed. Ctrl+C stops both.

## Inviting users

After initialising, `bincio init` prints a first invite code. Generate more from the browser at `/invites/`, or directly:

```bash
uv run python -c "
from pathlib import Path
from bincio.serve.db import open_db, create_invite
db = open_db(Path('/var/bincio'))
print(create_invite(db, 'dave'))
"
```

Share the invite link: `https://example.com/register/?code=XXXXXXXX`

Invite limits: admins — unlimited. Regular users — 3 each (configurable via `_MAX_USER_INVITES` in `bincio/serve/db.py`).

## Instance privacy

`bincio init` sets `"private": true` in the root `index.json` by default. This means every page (except `/login/` and `/register/`) redirects unauthenticated visitors to `/login/`.

To make the instance public, edit the root `index.json` and set `"private": false`. The next `bincio render` preserves this setting.

## Per-user Strava sync

Each user connects their own Strava account. The OAuth token is stored in `{handle}/strava_token.json`. The "Sync from Strava" button in the upload modal works per-session — each user syncs only their own activities.

## Federation

To follow another bincio instance, add a shard entry to the root `index.json`:

```json
{
  "shards": [
    { "handle": "dave",  "url": "dave/_merged/index.json" },
    { "handle": "alice", "url": "https://alice.example.com/index.json" }
  ]
}
```

The browser fetches and merges remote shards concurrently. Remote activities appear in the combined feed with `@alice` attribution.

## See also

- [CLI reference — bincio init](../reference/cli.md#bincio-init)
- [CLI reference — bincio serve](../reference/cli.md#bincio-serve)
- [CLI reference — bincio dev](../reference/cli.md#bincio-dev)
- [API reference](../reference/api.md)
- [BAS schema — instance manifest](../schema.md#instance-manifest)
