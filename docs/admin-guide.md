# Administrator Guide

This guide covers everything needed to deploy and maintain a multi-user BincioActivity instance.

## Before You Start

**[Multi-user Deployment](deployment/multi-user.md)** has the complete step-by-step instructions. This guide focuses on day-to-day admin tasks once the instance is running.

## Initializing an Instance

```bash
uv sync --extra serve

uv run bincio init \
  --data-dir /var/bincio \
  --handle your_admin_handle \
  --display-name "Your Name" \
  --name "Instance Name"
```

You'll be prompted for a password. This creates:

- `/var/bincio/instance.db` — SQLite database (users, sessions, invites, reset codes)
- `/var/bincio/index.json` — root shard manifest (`"private": true` by default)
- Your admin user account
- A first invite code

`bincio init` is idempotent — safe to re-run.

Optional flags:

- `--max-users N` — limit total registered users (0 or omitted = unlimited)
- `--store-originals false` — don't keep uploaded source files (defaults to true)

## Inviting Users

### Generate an invite code (as admin)

From the web UI at `/invites/` (requires login as admin), or via CLI:

```bash
uv run python -c "
from pathlib import Path
from bincio.serve.db import open_db, create_invite
db = open_db(Path('/var/bincio'))
code = create_invite(db, 'your_handle')
print(f'https://yourdomain.com/register/?code={code}')
"
```

### Invite limits

- **Admins:** unlimited invites
- **Regular users:** 3 invites each (configurable in `bincio/serve/db.py` as `_MAX_USER_INVITES`)

### Share the invite link

Send the registration link to the user:

```
https://yourdomain.com/register/?code=ABCD1234
```

They create their own handle and password. After registration, they can:
- Upload activity files (GPX, FIT, TCX)
- Sync from Strava
- Edit activity titles, descriptions, photos
- Control privacy per activity

## Password Reset

BincioActivity has no email system. Password resets work via **admin-generated one-time codes**.

### Reset a user's password (as admin)

1. Open `/admin/` in the web UI (must be logged in as admin)
2. Find the user and click **Reset password**
3. A code appears (monospace, click to copy)
4. Send the code out-of-band (Signal, Telegram, WhatsApp, etc.)

The code is valid for **24 hours**. Users reset their password at `/reset-password/` by entering:

- Their **handle**
- The **code**
- Their **new password**

### Reset code API (CLI)

To generate a reset code programmatically:

```bash
uv run python -c "
from pathlib import Path
from bincio.serve.db import open_db, create_reset_code
db = open_db(Path('/var/bincio'))
code, expires_in_hours = create_reset_code(db, 'user_handle', 'your_handle')
print(f'Code: {code} (expires in {expires_in_hours} hours)')
"
```

## Monitoring Active Jobs

The `/api/admin/jobs` endpoint (admin-only) shows which uploads/syncs are in progress:

```bash
curl -b "bincio_session=$(cat /tmp/session.txt)" http://localhost:4041/api/admin/jobs
```

Returns:

```json
[
  {
    "id": "a1b2c3d4",
    "user": "alice",
    "started_at": 1712345678,
    "total": 50,
    "done": 23,
    "current": "activity_2026-03-15_120000Z.fit"
  }
]
```

## Triggering Rebuilds

`bincio serve` can trigger incremental rebuilds when you pass `--site-dir`:

```bash
uv run bincio serve \
  --data-dir /var/bincio \
  --site-dir /var/www/bincio/src/site
```

After any write operation (edit, upload, Strava sync), the affected user's shard is rebuilt automatically and the static site is updated.

To manually rebuild a single user's shard:

```bash
uv run bincio render \
  --data-dir /var/bincio \
  --handle alice
```

To rebuild everything (slow):

```bash
uv run bincio render --data-dir /var/bincio
```

## Instance Settings

Settings are stored in `instance.db` and control instance-wide behavior:

| Setting | Default | Controls |
|---------|---------|----------|
| `max_users` | unlimited | Maximum registered users allowed |
| `store_originals` | `true` | Keep uploaded source files and Strava sync data |

Read/set settings via CLI:

```bash
uv run python -c "
from pathlib import Path
from bincio.serve.db import open_db, get_setting, set_setting
db = open_db(Path('/var/bincio'))
print(get_setting(db, 'max_users'))
set_setting(db, 'max_users', 100)
db.commit()
"
```

Or check the database directly:

```bash
sqlite3 /var/bincio/instance.db
> SELECT key, value FROM settings;
```

## Instance Privacy

By default, new instances are **private** — only authenticated users can view anything. Edit the root `index.json` to toggle:

```json
{
  "private": false,
  "shards": [...]
}
```

- **`"private": true`** — all pages (except login/register) require authentication
- **`"private": false`** — public access to all activities; individual activities can still be marked private via the `private` flag in sidecars

After any change, run `bincio render` to apply it:

```bash
uv run bincio render --data-dir /var/bincio
```

## Data Directory Layout

```
/var/bincio/
  instance.db               ← SQLite: users, sessions, invites, reset codes
  index.json                ← root shard manifest
  {handle}/
    index.json              ← user's BAS feed (activities list)
    _merged/                ← sidecar-merged output (served to browser)
    activities/             ← extracted activity JSON files
      {id}.json
      ...
    edits/                  ← user-made sidecar edits
      {id}.md
      images/{id}/
    athlete.json            ← profile (from Strava or manual)
    strava_token.json       ← OAuth token (if synced from Strava)
    originals/              ← source files (if store_originals=true)
  _feedback/                ← user feedback submissions
    {handle}.json
    {handle}/
      {timestamp}_{id}_{filename}
```

## Database Schema

`instance.db` contains:

- **`users`** — handle, password hash, display_name, is_admin, created_at
- **`sessions`** — session_id, handle, created_at, expires_at
- **`invites`** — code, created_by, created_at, used_by, used_at
- **`reset_codes`** — code, handle, created_by, created_at, expires_at, used_at
- **`settings`** — key, value (instance config)
- **`user_preferences`** — handle, key, value (per-user settings)

Query the database directly:

```bash
sqlite3 /var/bincio/instance.db ".tables"
sqlite3 /var/bincio/instance.db "SELECT handle, is_admin FROM users;"
```

## API Endpoints for Admins

The `/api/admin/*` endpoints require authentication and admin privileges:

- `GET /api/admin/users` — List all users
- `POST /api/admin/users/{handle}/reset-password-code` — Generate a reset code
- `GET /api/admin/jobs` — Show active uploads/syncs
- `GET /api/stats` — Community stats (public)

See [API Reference](reference/api.md) for full details.

### Explore the API with Swagger UI

When `bincio serve` is running, visit `/api/docs` to see an interactive Swagger UI. You can:
- Browse all endpoints with their parameters and response types
- Try out requests directly (if you're logged in as admin)
- See live examples of request/response bodies

ReDoc (another API documentation format) is also available at `/api/redoc` with a different UI.

## Running as a systemd service

See [Multi-user Deployment](deployment/multi-user.md#step-5--start-bincio-serve) for the systemd unit file. Key points:

- Set `User=bincio` (unprivileged user)
- Set `WorkingDirectory` to the repo root
- Use `--site-dir` to enable incremental rebuilds
- Restart policy: `Restart=on-failure`

Monitor with:

```bash
systemctl status bincio
journalctl -u bincio -f
```

## Troubleshooting

### Activities not appearing after upload

1. Check if the job is still running: `GET /api/admin/jobs`
2. Check logs: `journalctl -u bincio -f`
3. If `store_originals=true`, verify the source file is readable in `{handle}/originals/`
4. Re-trigger the merge: `uv run bincio render --data-dir /var/bincio --handle alice`

### Database locked

If you see "database is locked":

1. Verify no other `bincio` processes are running: `ps aux | grep bincio`
2. Kill any stuck processes: `pkill -f 'uv run bincio'`
3. Restart the service: `systemctl restart bincio`

### High memory usage

The first rebuild on a large instance can be memory-intensive. Consider:

- Running `bincio render` during off-hours
- Rebuilding one user at a time: `uv run bincio render --data-dir /var/bincio --handle alice`
- Increasing swap or upgrading the machine

## See also

- [Multi-user Deployment](deployment/multi-user.md) — complete step-by-step setup
- [Single-user Deployment](deployment/single-user.md) — if you're hosting a read-only site
- [API Reference](reference/api.md) — all HTTP endpoints
