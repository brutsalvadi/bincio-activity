# VPS deployment guide

Concrete setup for a Debian VPS running a private multi-user bincio instance.
Code is deployed directly from your laptop via `git push` — no GitHub required.

## Assumptions

- Bare Debian 12 VPS with root SSH access
- You own a domain pointed at the VPS
- You have Strava API credentials
- Up to ~30 users

---

## 1. Install system dependencies

```bash
apt update && apt upgrade -y
apt install -y git curl nginx certbot python3-certbot-nginx sqlite3 rsync
```

**Node.js 20 LTS** (the Debian package is too old):
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
```

**uv** (manages Python and all Python deps):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# add to PATH:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

## 2. Set up the code directory

```bash
mkdir -p /opt/bincio
git init --bare /opt/bincio-repo.git
```

Create the post-receive hook at `/opt/bincio-repo.git/hooks/post-receive`:

```bash
#!/bin/bash
set -e

REPO=/opt/bincio-repo.git
DEPLOY=/opt/bincio
DATA=/var/bincio/data

while read oldrev newrev refname; do
  echo "--- Checking out $refname ---"
  git --work-tree=$DEPLOY --git-dir=$REPO checkout -f $newrev

  echo "--- Syncing Python deps ---"
  cd $DEPLOY
  ~/.local/bin/uv sync --extra serve --extra strava --extra garmin

  echo "--- Syncing JS deps ---"
  cd $DEPLOY/site
  npm install --silent

  echo "--- Building site ---"
  cd $DEPLOY
  ~/.local/bin/uv run bincio render --data-dir $DATA --site-dir $DEPLOY/site

  echo "--- Pruning dist/data (nginx serves /data/ directly from $DATA) ---"
  rm -rf $DEPLOY/site/dist/data

  echo "--- Copying dist to webroot ---"
  rsync -a --delete --exclude=data/ $DEPLOY/site/dist/ /var/www/bincio/

  echo "--- Restarting API ---"
  systemctl restart bincio || echo "WARNING: bincio service restart failed — check journalctl -u bincio"

  echo "--- Done ---"
done
```

```bash
chmod +x /opt/bincio-repo.git/hooks/post-receive
mkdir -p /var/www/bincio /var/bincio/data /var/bincio/sources
```

---

## 3. systemd service

The hook restarts the `bincio` service on every deploy, so it must exist before the first push.

Create `/etc/bincio/secrets.env`:

```bash
mkdir -p /etc/bincio
chmod 700 /etc/bincio
cat > /etc/bincio/secrets.env <<EOF
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
EOF
chmod 600 /etc/bincio/secrets.env
```

Create `/etc/systemd/system/bincio.service`:

```ini
[Unit]
Description=BincioActivity API
After=network.target

[Service]
WorkingDirectory=/opt/bincio
ExecStart=/root/.local/bin/uv run bincio serve \
    --data-dir /var/bincio/data \
    --site-dir /opt/bincio/site \
    --webroot /var/www/bincio \
    --host 127.0.0.1 \
    --port 4041 \
    --public-url https://yourdomain.com
EnvironmentFile=/etc/bincio/secrets.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable --now bincio
systemctl status bincio
```

---

## 4. First deploy from your laptop

Add the VPS as a git remote (run this locally, once):

```bash
git remote add vps root@<your-vps-ip>:/opt/bincio-repo.git
```

Push your code:

```bash
git push vps main
```

The hook checks out the code, installs deps, and builds the site.
Subsequent pushes (including unpublished branches) work the same way:

```bash
git push vps mobile_app   # deploy any branch directly
```

---

## 5. Initialise the instance

```bash
cd /opt/bincio

uv run bincio init \
  --data-dir /var/bincio/data \
  --handle dave \
  --display-name "Dave" \
  --name "My Bincio"
# prompted for password; prints a first invite code
```

Enable the edit/upload UI (this env var is read at build time and is gitignored, so it must be set on the server):

```bash
echo "PUBLIC_EDIT_ENABLED=true" > /opt/bincio/site/.env
```

Set the user cap:

```bash
sqlite3 /var/bincio/data/instance.db \
  "INSERT INTO settings VALUES ('max_users', '30');"
```

---

## 6. Prepare your own activities

Source files (raw GPX/FIT) live separately from the BAS output:

```
/var/bincio/sources/dave/    ← raw activity files, rsync'd from laptop
/var/bincio/data/dave/       ← BAS JSON output (bincio extract writes here)
```

Configure `/opt/bincio/extract_config.yaml` on the server to point to your
source dir:

```yaml
sources:
  - path: /var/bincio/sources/dave/activities
    type: strava_export
  - path: /var/bincio/sources/dave/activities.csv
    type: strava_csv

output:
  dir: /var/bincio/data

workers: 2   # cap extract parallelism on the VPS (default: all CPUs)
```

Sync and extract (run from your laptop or SSH in):

```bash
# push raw files from laptop
rsync -avz ~/your-activity-data/ root@<vps>:/var/bincio/sources/dave/

# extract on server
ssh root@<vps> "cd /opt/bincio && uv run bincio extract"

# rebuild site
ssh root@<vps> "cd /opt/bincio && \
  uv run bincio render --data-dir /var/bincio/data --site-dir site && \
  rm -rf site/dist/data && \
  rsync -a --delete --exclude=data/ site/dist/ /var/www/bincio/"
```

---

## 7. nginx

Create `/etc/nginx/sites-available/bincio`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    root /var/www/bincio;
    index index.html;

    client_max_body_size 2G;      # Strava export ZIPs can exceed 1 GB
    client_body_timeout  300s;   # allow slow uploads without nginx dropping the connection

    # API → bincio serve
    location /api/ {
        proxy_pass http://127.0.0.1:4041;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;   # Strava sync can be slow
    }

    # Data files served live from disk — bypasses the build/rsync cycle
    # so uploads and merges are visible immediately without a site rebuild.
    #
    # IMPORTANT: because nginx owns /data/ here, the post-receive hook must
    # delete dist/data/ before rsyncing to the webroot. Otherwise astro build
    # copies all activity JSON (GBs) into dist/ and rsync duplicates it again.
    # The hook already does this; manual rebuilds must do the same.
    location /data/ {
        alias /var/bincio/data/;
        add_header Cache-Control "no-cache, must-revalidate";
    }

    # Activity detail pages: fall back to the dynamic shell for activities uploaded
    # after the last site build (avoids 404 while waiting for a rebuild).
    location /activity/ {
        try_files $uri $uri/ /activity/index.html;
    }

    # Per-user profile pages: fall back to the home page while the background
    # rebuild (triggered automatically on registration) completes.
    location /u/ {
        try_files $uri $uri/ /index.html;
    }

    # Static files
    location / {
        try_files $uri $uri/ $uri.html =404;
    }
}
```

```bash
# disable the default nginx welcome page
rm /etc/nginx/sites-enabled/default
ln -s /etc/nginx/sites-available/bincio /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### Enable gzip compression

The default `nginx.conf` has gzip on but `gzip_types` commented out, so only
HTML is compressed. Activity index shards are JSON and compress ~90% — enable
the full list:

```bash
# In /etc/nginx/nginx.conf, uncomment the gzip block:
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_buffers 16 8k;
gzip_http_version 1.1;
gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
```

```bash
nginx -t && systemctl reload nginx
```

You can verify the site is served correctly by hitting the IP directly:
`http://<your-vps-ip>/` — you should see the bincio activity feed, not the nginx welcome page.

---

## 8. SSL

SSL requires the domain to be pointing at the VPS first. In your DNS provider, add:

```
Type:  A
Name:  @
Value: <your-vps-ip>
TTL:   300
```

Verify propagation before running certbot:

```bash
dig yourdomain.com A +short   # must return your VPS IP
```

Then:

```bash
certbot --nginx -d yourdomain.com
# certbot edits the nginx config and sets up automatic renewal
```

---

## 9. Invite users

After `bincio init` prints the first invite code, you can generate more from
the browser at `/u/{handle}/athlete/` → **Invites** button (visible only to
the page owner), or directly via the CLI:

```bash
sqlite3 /var/bincio/data/instance.db \
  "INSERT INTO invites (code, created_by, created_at) \
   VALUES (upper(hex(randomblob(4))), 'dave', unixepoch());"
```

Share the link: `https://yourdomain.com/register/?code=XXXXXXXX`

Each new user uploads their activities via the **+** button in the top nav
(supports bulk GPX/FIT/TCX drop). They can later connect Strava for
incremental sync from the same modal.

---

## Reading user feedback

Users can submit feedback from the **Feedback** link in the nav (visible when logged in).
Submissions are stored as JSON on the server:

```
/var/bincio/data/_feedback/
  {handle}.json       ← one file per user, array of submissions
  {handle}/           ← attached images
```

To read all feedback:

```bash
cat /var/bincio/data/_feedback/*.json | python3 -m json.tool
```

Per-user only:

```bash
cat /var/bincio/data/_feedback/pres.json | python3 -m json.tool
```

---

## Day-to-day operations

| Task | Command |
|------|---------|
| Deploy code update | `git push vps main` (from laptop) |
| Sync your raw files | `rsync -avz ~/your-activity-data/ root@<vps>:/var/bincio/sources/dave/` |
| Re-extract after sync | `ssh root@<vps> "cd /opt/bincio && uv run bincio extract"` then push again to rebuild |
| View API logs | `journalctl -u bincio -f` |
| Restart API | `systemctl restart bincio` |
| Check nginx logs | `tail -f /var/log/nginx/error.log` |
| Renew SSL (auto) | `certbot renew --dry-run` |

---

## See also

- [Multi-user architecture](multi-user.md)
- [CLI reference](../reference/cli.md)
- [API reference](../reference/api.md)