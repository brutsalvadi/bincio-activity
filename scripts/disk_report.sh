#!/usr/bin/env bash
# Bincio VPS disk usage report
# Run on the VPS: bash scripts/disk_report.sh
# Or remotely:   ssh root@<vps> 'bash -s' < scripts/disk_report.sh

DATA=/var/bincio/data
SITE=/var/bincio/site   # adjust if your site build lives elsewhere

hr() { echo; echo "── $* ──────────────────────────────────────"; }

hr "DISK OVERVIEW"
df -h / | tail -1 | awk '{printf "Used: %s / %s  (%s full)\n", $3, $2, $5}'

hr "BINCIO ROOT"
du -sh /var/bincio/ 2>/dev/null

hr "DATA ROOT: $DATA"
du -sh "$DATA" 2>/dev/null

hr "PER-USER BREAKDOWN"
for user_dir in "$DATA"/*/; do
    handle=$(basename "$user_dir")
    [[ "$handle" == _* ]] && continue   # skip _feedback etc.

    total=$(du -sh "$user_dir" 2>/dev/null | cut -f1)

    act=$(du -sh "$user_dir/activities"      2>/dev/null | cut -f1 || echo "—")
    merged=$(du -sh "$user_dir/_merged"      2>/dev/null | cut -f1 || echo "—")
    edits=$(du -sh "$user_dir/edits"         2>/dev/null | cut -f1 || echo "—")
    images=$(du -sh "$user_dir/edits/images" 2>/dev/null | cut -f1 || echo "—")
    orig=$(du -sh "$user_dir/originals"      2>/dev/null | cut -f1 || echo "—")
    orig_strava=$(du -sh "$user_dir/originals/strava" 2>/dev/null | cut -f1 || echo "—")
    orig_fit=$(du -sh "$user_dir/originals"  2>/dev/null)  # will count below by extension

    n_act=$(find "$user_dir/activities" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
    n_orig=$(find "$user_dir/originals" -type f        2>/dev/null | wc -l | tr -d ' ')
    n_strava=$(find "$user_dir/originals/strava" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')

    echo ""
    echo "  @$handle  (total: $total)"
    echo "    activities/       $act   ($n_act JSON files)"
    echo "    _merged/          $merged"
    echo "    edits/            $edits   (images: $images)"
    echo "    originals/        $orig   ($n_orig files)"
    echo "      strava/         $orig_strava   ($n_strava JSON)"
done

hr "FEEDBACK"
du -sh "$DATA/_feedback" 2>/dev/null || echo "  (none)"

hr "SITE BUILD"
du -sh "$SITE" 2>/dev/null || echo "  (not found at $SITE)"

hr "LOGS"
journalctl --disk-usage 2>/dev/null || echo "  (journalctl unavailable)"

hr "LARGEST FILES IN DATA (top 20)"
find "$DATA" -type f -printf '%s\t%p\n' 2>/dev/null \
    | sort -rn | head -20 \
    | awk '{
        size=$1; path=$2;
        if (size >= 1048576) printf "%6.1f MB  %s\n", size/1048576, path;
        else if (size >= 1024) printf "%6.1f KB  %s\n", size/1024, path;
        else printf "%6d  B  %s\n", size, path;
      }'

hr "EXTENSION BREAKDOWN IN originals/"
find "$DATA" -path "*/originals/*" -type f 2>/dev/null \
    | sed 's/.*\.//' | sort | uniq -c | sort -rn \
    | awk '{printf "  %6d  .%s\n", $1, $2}'

echo
