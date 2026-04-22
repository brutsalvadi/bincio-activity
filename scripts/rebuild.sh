#!/bin/bash
set -e

REPO=/opt/bincio-repo.git
DEPLOY=/opt/bincio
DATA=/var/bincio/data

echo "--- Syncing Python deps ---"
cd $DEPLOY
~/.local/bin/uv sync --extra serve --extra strava --extra garmin

echo "--- Syncing JS deps ---"
cd $DEPLOY/site
npm install --silent

echo "--- Building site ---"
cd $DEPLOY
~/.local/bin/uv run bincio render --data-dir $DATA --site-dir $DEPLOY/site

echo "--- Copying dist to webroot ---"
rsync -a --delete $DEPLOY/site/dist/ /var/www/bincio/

echo "--- Restarting API ---"
systemctl restart bincio || echo "WARNING: bincio service restart failed — check journalctl -u bincio"

echo "--- Done ---"