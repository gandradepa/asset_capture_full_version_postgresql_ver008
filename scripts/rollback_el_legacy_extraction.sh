#!/usr/bin/env bash
# Rollback for the EL legacy EXTRACTION gate deploy (2026-07-29).
#
# Usage:
#   ./rollback_el_legacy_extraction.sh /home/developer/deploy_backups/el_legacy_extraction_<TS>
#
# What it does (code-only rollback — this deploy made NO schema changes):
#   1. Restores API/API_interface_EL_ver00.py and
#      review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py from the bundle.
#   2. DELIBERATELY KEEPS review/Asset_dashboard_browser_EL/legacy_flow.py at the
#      NEW revision. The final whole-branch review proved the pre-deploy revision
#      cannot read back values this deploy writes ("MDC via TX T1", "DCC #1" —
#      its normalize_legacy_supply_from returns fed_from_id='' for all of them),
#      which would blank Fed From on every legacy record's review page. The new
#      revision is purely additive (all pre-existing tests pass against it).
#      Reverting it is a manual, eyes-open decision only.
#   3. Stamps "modified": true on every Output_jason_api/*_EL_*.json carrying
#      "process": "Legacy", and writes the list to <bundle>/stamped_legacy_jsons.txt.
#      Why: with the gate removed, the OLD ungated extraction would treat some
#      legacy-shaped JSONs as stale and overwrite them with PNL-fabricated
#      standard output (re-introducing the exact bug) — the modified flag makes
#      both rescore paths and the overwrite guard leave them alone. Side effect:
#      those records count as "modified" in dashboard filters until un-stamped.
#      To un-stamp after re-deploy: set "modified" back per the manifest.
#   4. Restores the docs files if present in the bundle.
#   5. Graceful HUP of the assetcap-el gunicorn master (Asset_dashboard_EL.py
#      changed, the review app must reload; the extraction API is not a daemon
#      and picks up the revert on its next invocation).
#
# No DB restore is needed for this rollback (no DDL, only ai_status writes which
# the platform recomputes). The bundle still contains a full pre-deploy pg_dump
# for disaster recovery; restoring it is deliberately manual — see the bundle's
# MANIFEST.txt.
set -euo pipefail

BUNDLE=${1:?usage: rollback_el_legacy_extraction.sh <backup_bundle_dir>}
APP=/home/developer/review/Asset_dashboard_browser_EL
JSON_DIR=/home/developer/Output_jason_api

[ -f "$BUNDLE/API/API_interface_EL_ver00.py" ] || {
    echo "ERROR: $BUNDLE does not look like an el_legacy_extraction backup bundle" >&2; exit 1; }

echo "== EL legacy extraction gate rollback from $BUNDLE =="

echo "-- restoring replaced files (NOT legacy_flow.py — see header)"
cp "$BUNDLE/API/API_interface_EL_ver00.py" /home/developer/API/API_interface_EL_ver00.py
cp "$BUNDLE/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py" "$APP/Asset_dashboard_EL.py"
# sld_blueprint.py joined the deploy in the same-day hotfix (SLD swift-save/
# reconcile Buildings.Process gate); restore it when the bundle carries it.
[ -f "$BUNDLE/review/Asset_dashboard_browser_EL/sld_blueprint.py" ] && \
    cp "$BUNDLE/review/Asset_dashboard_browser_EL/sld_blueprint.py" "$APP/sld_blueprint.py"
[ -f "$BUNDLE/Markdowns_documentation/rules/review_apps.rules.md" ] && \
    cp "$BUNDLE/Markdowns_documentation/rules/review_apps.rules.md" /home/developer/Markdowns_documentation/rules/review_apps.rules.md
[ -f "$BUNDLE/Markdowns_documentation/attributes_changes.md" ] && \
    cp "$BUNDLE/Markdowns_documentation/attributes_changes.md" /home/developer/Markdowns_documentation/attributes_changes.md

echo "-- stamping modified:true on process=Legacy JSONs (manifest: $BUNDLE/stamped_legacy_jsons.txt)"
python3 - "$JSON_DIR" "$BUNDLE/stamped_legacy_jsons.txt" <<'PY'
import json, os, sys
json_dir, manifest_path = sys.argv[1], sys.argv[2]
stamped = []
for name in sorted(os.listdir(json_dir)):
    if "_EL_" not in name or not name.endswith(".json"):
        continue
    path = os.path.join(json_dir, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        continue
    if payload.get("process") != "Legacy" or payload.get("modified") is True:
        continue
    payload["modified"] = True
    tmp = path + ".rollback_tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    stamped.append(name)
with open(manifest_path, "w", encoding="utf-8") as f:
    f.write("\n".join(stamped) + ("\n" if stamped else ""))
print(f"stamped {len(stamped)} legacy JSON(s)")
PY

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
python3 -m py_compile /home/developer/API/API_interface_EL_ver00.py "$APP/Asset_dashboard_EL.py" && echo "py_compile OK"
echo "== ROLLBACK COMPLETE =="
echo "NOTE: pause/verify the EL cron (ai_check.sh) before its next tick if the"
echo "stamping step reported 0 files but legacy JSONs exist — see script header."
