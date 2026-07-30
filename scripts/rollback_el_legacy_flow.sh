#!/usr/bin/env bash
# Rollback for the EL legacy flow deploy (2026-07-28).
#
# Usage:
#   ./rollback_el_legacy_flow.sh /home/developer/deploy_backups/el_legacy_flow_<TS> [--code-only|--full]
#
# Modes:
#   --code-only (default)  Restore the replaced files, delete the files this deploy
#                          introduced, and gracefully reload assetcap-el. Leaves the
#                          Buildings."Process" column and its data in place — the column
#                          is additive and the restored (pre-deploy) code never reads it,
#                          so this is the safe first response to any problem.
#   --full                 Everything --code-only does, PLUS drops ck_buildings_process
#                          and the "Process" column from "Buildings". Use only if the
#                          schema itself must go; classification data is lost.
#
# Disaster recovery (data corruption, not just code): restore the full pre-deploy dump —
#   set -a; . /home/developer/db_backend.env; set +a
#   psql "$QR_PG_DSN" < <bundle>/db/backup_qr_code_db_predeploy.sql   # review first; full overwrite
# — with assetcap-el stopped (sudo systemctl stop assetcap-el). That path is deliberately
# NOT automated here.
#
# Service reload: uses a no-sudo graceful HUP of the developer-owned gunicorn master.
# With an interactive sudo session, `sudo systemctl restart assetcap-el` is equivalent.
set -euo pipefail

BUNDLE=${1:?usage: rollback_el_legacy_flow.sh <backup_bundle_dir> [--code-only|--full]}
MODE=${2:---code-only}
APP=/home/developer/review/Asset_dashboard_browser_EL

[ -f "$BUNDLE/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py" ] || {
    echo "ERROR: $BUNDLE does not look like an el_legacy_flow backup bundle" >&2; exit 1; }

echo "== EL legacy flow rollback from $BUNDLE (mode: $MODE) =="

echo "-- restoring replaced files"
cp "$BUNDLE/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py" "$APP/Asset_dashboard_EL.py"
cp "$BUNDLE/review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py" "$APP/sld/extract_electrical_schema.py"
cp "$BUNDLE/dictionary/electrical.dictionary.py" /home/developer/dictionary/electrical.dictionary.py
[ -f "$BUNDLE/Markdowns_documentation/attributes_changes.md" ] && \
    cp "$BUNDLE/Markdowns_documentation/attributes_changes.md" /home/developer/Markdowns_documentation/attributes_changes.md
[ -f "$BUNDLE/Markdowns_documentation/rules/review_apps.rules.md" ] && \
    cp "$BUNDLE/Markdowns_documentation/rules/review_apps.rules.md" /home/developer/Markdowns_documentation/rules/review_apps.rules.md

echo "-- removing files introduced by this deploy"
rm -f "$APP/legacy_flow.py"
rm -f /home/developer/dictionary/electrical.dictionary_old.py
rm -f /home/developer/scripts/migrate_buildings_process_column.py

if [ "$MODE" = "--full" ]; then
    echo "-- dropping Buildings.Process column + constraint"
    set -a; . /home/developer/db_backend.env; set +a
    psql "$QR_PG_DSN" -c 'ALTER TABLE "Buildings" DROP CONSTRAINT IF EXISTS ck_buildings_process;'
    psql "$QR_PG_DSN" -c 'ALTER TABLE "Buildings" DROP COLUMN IF EXISTS "Process";'
fi

echo "-- graceful reload of assetcap-el (HUP to gunicorn master)"
# Master = the gunicorn process whose parent is PID 1 (systemd). Never pgrep|head:
# workers match the same pattern, and inline-ssh shells can self-match.
MASTER=$(ps -eo pid,ppid,cmd | awk '/venv\/bin\/gunicorn/ && /Asset_dashboard_E[L]:app/ && $2==1 {print $1}')
if [ -n "$MASTER" ]; then
    kill -HUP "$MASTER"
    sleep 5
else
    echo "WARN: gunicorn master not found; restart manually: sudo systemctl restart assetcap-el" >&2
fi

echo "-- verification"
systemctl is-active assetcap-el
curl -s -o /dev/null -w "EL app HTTP %{http_code}\n" http://127.0.0.1:8005/ || true
echo "== ROLLBACK COMPLETE (mode: $MODE) =="
