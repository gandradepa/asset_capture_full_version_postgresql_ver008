#!/bin/bash
D=/home/developer/asset_capture_app_dev/data
LOG=/home/developer/qrdb_watch.log
inotifywait -m -e modify,attrib,create,delete,moved_to,moved_from --format '%e %f' "$D" 2>>"$LOG" | while read ev f; do
  case "$f" in QR_codes.db*) echo "$(date -Is) $ev $f size=$(stat -c %s "$D/$f" 2>/dev/null || echo gone)" >> "$LOG";; esac
done
