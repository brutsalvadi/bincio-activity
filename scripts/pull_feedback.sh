#!/usr/bin/env bash
# Pull user feedback from the VPS into ./feedback/ locally.
# Usage: bash scripts/pull_feedback.sh <user@host>

set -e
VPS=${1:?Usage: $0 user@host}
REMOTE=/var/bincio/data/_feedback
LOCAL=$(dirname "$0")/../feedback

mkdir -p "$LOCAL"

echo "Syncing feedback from $VPS:$REMOTE → $LOCAL"
rsync -avz --progress "${VPS}:${REMOTE}/" "$LOCAL/"

echo ""
echo "=== Feedback summary ==="
for f in "$LOCAL"/*.json; do
  [[ -f "$f" ]] || continue
  handle=$(basename "$f" .json)
  count=$(python3 -c "import json,sys; d=json.load(open('$f')); print(len(d) if isinstance(d, list) else 1)" 2>/dev/null || echo "?")
  echo "  @$handle: $count submission(s)"
done
