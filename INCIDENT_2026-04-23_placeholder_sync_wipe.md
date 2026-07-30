# Incident â€” `sdi_dataset_EL` row wipe after UBC Asset Tag edit

Current documentation refresh: 2026-04-28.

**Date:** 2026-04-23
**Severity:** Data loss (single-row), full recovery from canonical JSON
**Affected asset:** QR `0000184474`, Building `217`, EL dashboard
**Status:** Resolved â€” row recovered, root cause fixed, regression test added, fix live on `reviewel.assetcap.facilities.ubc.ca`

---

## Summary

A user edited the **UBC Asset Tag** for asset `0000184474` in the EL review dashboard (https://reviewel.assetcap.facilities.ubc.ca). After the save, the `sdi_dataset_EL` row and the canonical JSON (`Output_jason_api/0000184474_EL_217.json`) were both observed with every editable column blank. Only `Location`, `Asset Group`, and `Attribute` survived in the DB (fallback-derived during the sync).

The row was fully restored from the intact copy in the developer's local git working tree. The wipe mechanism was identified in code, guarded against, covered by a regression test, and the patched code was deployed to the VM.

## Timeline

| When | Event |
|---|---|
| 2026-04-23 (day of) | User edits UBC Asset Tag on the review page for 0000184474. Save rewrites `Output_jason_api/0000184474_EL_217.json` and UPSERTs `sdi_dataset_EL`. |
| Moments later | The before-request image-sync hook in `sync_image_directory_to_db_el` detects a new image (or is otherwise triggered) and calls `_sync_db_from_structured(qr, building, sd={})` â€” a blank-payload upsert. The narrow guard (`_sdi_row_has_data`) allowed this because both `UBC Asset Tag` and `Equipment ID` were empty, which was the only content it inspected. |
| 2026-04-23 | User reports "all data in this data has been deleted" for the row. |
| 2026-04-23 | Root cause identified; local repo and DB still intact. Recovery script built and dry-run validated on local DB. |
| 2026-04-23 | Patched EL/ME/BF dashboards deployed to the VM via scp; `assetcap-el` restarted; recovery script run; row restored; user re-ran the edit sequence and confirmed the fix holds. |

## Root cause

All three review dashboards (EL, ME, BF) run a periodic `sync_image_directory_to_db*` function (invoked from a `before_request` hook) that walks newly-arrived images in `Capture_photos_upload/` and issues a **blank-payload upsert** into the SDI table to bootstrap a row:

- **EL:** `_sync_db_from_structured(qr, building, sd={})` â€” see
  `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py:437-487`
- **ME:** `_db_upsert_sdi_dataset(qr, bld, {})` â€” see
  `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py:310-338`
- **BF:** `upsert_sdi_dataset(doc_id=doc_id, structured={})` â€” see
  `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py:333-375`

Each upsert issues `UPDATE "<table>" SET <every column>='' WHERE "QR Code"=? AND Building=?`. Missing dict keys default to `""`, so the "bootstrap" path will overwrite every editable column of an existing row if it is allowed to run.

The EL guard `_sdi_row_has_data(qr, building)` was intended to stop this, but it only checked whether `UBC Asset Tag` **OR** `Equipment ID` was non-empty. Editing the tag to a value that also blanked the derived `Equipment ID` removed both signals, so the guard released and the next arriving image wiped the whole row.

ME and BF had **no guard at all** against this path â€” strictly more vulnerable than EL.

## Recovery

Tool: [`scripts/recover_sdi_row_from_json.py`](scripts/recover_sdi_row_from_json.py).

Reads `Output_jason_api/<qr>_EL_<building>.json`, rebuilds every derived column inline (no Flask/auth imports), performs an idempotent UPDATE-then-INSERT against `sdi_dataset_EL`. Skips the `ID_check` column because it is a SQLite generated column (`hidden=2`).

Recovery procedure used on the VM:

```bash
# VM's own JSON was also blank, so the intact local copy was pushed up first:
scp Output_jason_api/0000184474_EL_217.json developer@142.103.68.1:/home/developer/Output_jason_api/

# Then on the VM:
cd /home/developer
python3 scripts/recover_sdi_row_from_json.py --dry-run   # preview diff
python3 scripts/recover_sdi_row_from_json.py             # commit
touch Output_jason_api/0000184474_EL_217.json            # pin mtime vs watcher
```

Post-recovery DB row matched the intact local row byte-for-byte across every writable column.

## Fix

### EL â€” widened `_sdi_row_has_data`

File: [review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py)

The guard now treats a row as "holding real data" if **any** of these columns are non-blank, or `Avg_ai_conf > 0`:

`UBC Asset Tag`, `Equipment ID`, `Branch Panel`, `Supply From`, `Volts`, `Ampere`, `Location`, `Description`, `Asset Group`.

### ME â€” new guard `_sdi_row_has_data_me`

File: [review/Asset_dasboard_browser_ME/asset_plate_reviewer.py](review/Asset_dasboard_browser_ME/asset_plate_reviewer.py)

Added `_sdi_row_has_data_me(qr, building)`; invoked by `sync_image_directory_to_db` before the blank-payload upsert. Signal columns: `UBC Tag`, `Manufacturer`, `Model`, `Serial`, `Asset Group`, `Description`, `Avg_ai_conf > 0`.

### BF â€” new guard `_sdi_row_has_data_bf`

File: [review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py](review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py)

Added `_sdi_row_has_data_bf(doc_id parts)`; invoked by `sync_image_directory_to_db_bf` before the blank-payload upsert. Signal columns: the ME list plus BF-specific `Diameter`, `Year`, `Technical Safety BC`.

### Regression test

File: [test/test_placeholder_sync_guard.py](test/test_placeholder_sync_guard.py) â€” 13 `unittest` cases.

- Per-dashboard cases assert that a row with partial real content (e.g. only `Branch Panel` + `Supply From`, or only `Avg_ai_conf`, or BF-specific `Diameter`+`Year`) is protected, and a fully-blank row is released.
- A "source-drift" test greps each dashboard file for its guard function and asserts every protected column name appears in the function body. Narrowing a guard without updating the test will fail this check.

## Verification

- **Local**: all 13 guard tests pass (`python -m unittest test.test_placeholder_sync_guard`).
- **VM**: all 13 guard tests pass (`cd test && python3 -m unittest test_placeholder_sync_guard`).
- **Live reproduction** by user after `sudo systemctl restart assetcap-el`:
  editing UBC Asset Tag on the review page for `0000184474` updated the tag, `Equipment ID`, and `Description` as intended, and left every other column (`Branch Panel`, `Supply From`, `Ampere`, `Location`, `Asset Group`, `Avg_ai_conf`) untouched. The guard held.

## Files changed

| Path | Change |
|---|---|
| `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` | Widened `_sdi_row_has_data` guard. |
| `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` | New `_sdi_row_has_data_me`; wired into `sync_image_directory_to_db`. |
| `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` | New `_sdi_row_has_data_bf`; wired into `sync_image_directory_to_db_bf`. |
| `test/test_placeholder_sync_guard.py` | New â€” 13 `unittest` cases (behavior + source-drift). |
| `scripts/recover_sdi_row_from_json.py` | New â€” standalone recovery tool. |

## Detection (active)

- **Auditor script:** `scripts/audit_sdi_vs_json.py` â€” stateless JSON-vs-DB cross-check. For every `Output_jason_api/<qr>_<TYPE>_<bldg>.json` with a non-empty identity tag, it confirms the matching SDI row carries that identity and is not otherwise blank. Exits 1 on anomalies, 0 on clean. Covers EL, ME, and BF.
- **Cron (developer user, hourly at :00):**
  ```cron
  0 * * * * /usr/bin/python3 /home/developer/scripts/audit_sdi_vs_json.py --quiet \
                >> /home/developer/logs/sdi_audit.log 2>&1
  ```
  `--quiet` means the log grows only when anomalies are detected. An empty / unchanged log is the healthy state.
- **Current audit on VM: OK** â€” all recovered rows (`0000184474`, `0000183852`, `0000184410`) match their JSONs.

## Backups (active)

- **Script:** `scripts/backup_daily.py` â€” online sqlite `.backup` of `QR_codes.db` (WAL-safe) + gzip tarball of `Output_jason_api/`, written to `/home/developer/backup_app/` with date-stamped filenames (`QR_codes_<YYYY-MM-DD>.db`, `Output_jason_api_<YYYY-MM-DD>.tar.gz`). 14-day rolling retention; only prunes files matching our own name patterns.
- **Cron (developer user, daily 02:00):**
  ```cron
  0 2 * * * /usr/bin/python3 /home/developer/scripts/backup_daily.py \
                >> /home/developer/logs/backup.log 2>&1
  ```
- **Baseline:** `QR_codes_2026-04-23.db` (8.5 MB) + `Output_jason_api_2026-04-23.tar.gz` (1,176 files in 81 KB).
- **Post-recovery snapshot retained:** `/home/developer/backup_app/QR_codes_2026-04-23_165308_post-recovery.db` (wait â€” this one lives under the older `/home/developer/backups/` path used for the one-off; the daily job uses `/home/developer/backup_app/`). The one-off is preserved because its filename doesn't match the retention glob.
- **Not included:** off-machine copy. Daily artefacts live only on the VM; a VM-wide failure loses them. Adding an off-site sync (rsync to NAS / S3 / another host) is a separate follow-up.

## Notes and follow-ups

- **Service restart is mandatory after deploy.** Gunicorn workers hold the old Python module until `sudo systemctl restart <service>` â€” the code on disk does not protect a running process. The three dashboards use different systemd unit names:
  - EL â†’ `assetcap-el` (port 8005)
  - ME â†’ `assetcap-reviewme` (port 8001, NOT `assetcap-me`)
  - BF â†’ `assetcap-bf` (port 8004)
- **All three services restarted on 2026-04-23** with the patched code loaded (EL 16:43 PDT, ME 16:58, BF 16:58). Guards are live in-process.
- **Code committed to git** â€” commit `82062f3 "Fixing the error that was deleting the data"` on `main`; local, remote, and VM are in sync for the core fix.
- **Consider a follow-up**: move the common guard logic to a small shared module so drift between the three dashboards is easier to keep in sync. Current test compensates, but a shared helper would eliminate the duplication.

## Contacts

Reported and resolved by: `gibandradepa@gmail.com` / `gilberto.andrade@ubc.ca`.
Production EL dashboard: `reviewel.assetcap.facilities.ubc.ca` (port 8005, systemd unit `assetcap-el`, gunicorn target `Asset_dashboard_EL:app`, host `/home/developer`).
