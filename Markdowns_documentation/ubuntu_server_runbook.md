# Ubuntu Server Runbook

Current documentation refresh: 2026-07-27.

## Overview

This document records the current Ubuntu production environment and the operational runbook used for the EL amperage database migration on 2026-04-01. The service inventory below was rechecked against the current repository layout during the 2026-04-28 documentation refresh.

## Server Baseline

- OS: Ubuntu 22.04 LTS (server)
- Linux user: `developer`
- SSH: `ssh developer@142.103.68.1`
- Web server / reverse proxy: Nginx 1.24
- Application server: Gunicorn, one `systemd` service per Flask app
- SSL/TLS: Let's Encrypt via Certbot
- Database: PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`, via `db.py` / `DB_BACKEND=postgres`); SQLite `QR_codes.db` is the frozen rollback. Auth DB stays SQLite.

## Service Inventory

| Service | Port | Domain | systemd service | Path | Gunicorn target | Restart command |
|---|---:|---|---|---|---|---|
| SDI - Planon Process Management | 8003 | `sdiprocess.assetcap.facilities.ubc.ca` | `sdi_process` | `/home/developer/SDI_process` | `app:app` | `sudo systemctl restart sdi_process` |
| Asset Capture App | 8000 | `appprod.assetcap.facilities.ubc.ca` | `assetcap-app.service` | `/home/developer/asset_capture_app_dev` | `app:app` | `sudo systemctl restart assetcap-app.service` |
| Asset Reviewer - Mechanical (ME) | 8001 | `reviewme.assetcap.facilities.ubc.ca` | `assetcap-reviewme` | `/home/developer/review/Asset_dasboard_browser_ME` | `asset_plate_reviewer:app` | `sudo systemctl restart assetcap-reviewme` |
| Asset Reviewer - Backflow (BF) | 8004 | `reviewbf.assetcap.facilities.ubc.ca` | `assetcap-bf` | `/home/developer/review/Asset_dasboard_browser_BF` | `asset_plate_reviewer_bf:app` | `sudo systemctl restart assetcap-bf` |
| Asset Portal Dashboard (Main) | 8002 | `dashboardprod.assetcap.facilities.ubc.ca` | `assetcap-dashboard` | `/home/developer/Dashboard` | `Asset_portal_dashboard:app` | `sudo systemctl restart assetcap-dashboard` |
| Asset Reviewer - Electrical (EL) | 8005 | `reviewel.assetcap.facilities.ubc.ca` | `assetcap-el` | `/home/developer/review/Asset_dashboard_browser_EL` | `Asset_dashboard_EL:app` | `sudo systemctl restart assetcap-el` |

## Database Paths

- Main production DB: PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`) as of the 2026-06-08 C4 cutover. Legacy SQLite `/home/developer/asset_capture_app_dev/data/QR_codes.db` is the frozen rollback.
- Migration script path: `/home/developer/scripts/backfill_amperage_columns_sqlite.py` (historical SQLite amperage backfill — see the dated procedure below)

## PostgreSQL IPC Safeguard and Recovery

The AssetCap PostgreSQL service is `qr-postgres.service`. It runs as OS user `developer` from `/home/developer/QR_database/pgdata`, listens on port 5433, and places its Unix socket in `/tmp`.

The custom cluster uses POSIX dynamic shared memory. With systemd-logind's default `RemoveIPC=yes`, a full logout can remove `/dev/shm/PostgreSQL.*` objects that the running cluster still needs. The resulting signature is:

```text
FATAL: could not open shared memory segment "/PostgreSQL.<id>": No such file or directory
```

The persistent production safeguard enabled on 2026-07-27 is user lingering:

```bash
loginctl show-user developer -p Linger
stat /var/lib/systemd/linger/developer
```

Expected: `Linger=yes` and a persistent linger marker. Do not disable lingering while `qr-postgres.service` runs as `developer` with `dynamic_shared_memory_type=posix`. A host-wide alternative is an administrator-managed `RemoveIPC=no` setting in `logind.conf`; that requires root access and a coordinated logind restart.

`pg_isready` is not sufficient for this failure mode: the postmaster can report that it accepts connections while every backend fails to attach shared memory. Always verify a real query:

```bash
pg_isready -h /tmp -p 5433 -U developer -d qr_code_db
psql -X -h /tmp -p 5433 -U developer -d qr_code_db \
  -v ON_ERROR_STOP=1 -c 'SELECT 1;'
```

If the shared-memory object has already been removed, use the normal administrator-controlled restart and then repeat the real-query check:

```bash
sudo systemctl restart qr-postgres.service
systemctl is-active qr-postgres.service
psql -X -h /tmp -p 5433 -U developer -d qr_code_db \
  -v ON_ERROR_STOP=1 -c 'SELECT 1;'
```

Do not restart the healthy system PostgreSQL cluster on port 5432 when recovering the AssetCap cluster on port 5433.

## SLD Extraction Log Artifacts

The SLD AI extraction pipeline (EL Reviewer's `POST /sld/api/process` → `extract_electrical_schema.py`) writes three log channels. The Dashboard's `AI Process Queue → System Logs → SLD Extraction Runs` view reads channels A and B directly from disk.

| Channel | Path | Format | Content |
|---|---|---|---|
| A — rolling text log | `/home/developer/sld_extract.log` (override `SLD_EXTRACT_LOG`) | plain text | START/END envelopes per run + captured stdout/stderr from the extractor subprocess |
| B — per-run JSONL | `/home/developer/sld_extract_feedback/<run_id>.jsonl` (override `SLD_FEEDBACK_DIR`) | JSON Lines | typed events: `run_meta`, `model_call`, `run_summary`, `wrapper_event` |
| B — global corrections | `/home/developer/sld_extract_feedback/corrections.jsonl` | JSON Lines | `human_correction` events appended whenever a user edits an extracted SLD field; joins back to a run via `ai_run_id` |

Both Dashboard and EL Reviewer must have read access to `SLD_FEEDBACK_DIR`. Set `SLD_FEEDBACK_DISABLED=1` in either service's environment to suppress JSONL writes (Channel A still appends).

Re-running a prior extraction:

```bash
# Loopback target the Dashboard reverse-proxies to (admin-only):
curl -X POST http://127.0.0.1:8005/sld/api/rerun/<run_id> \
  --cookie "session=<your_session_cookie>"
```

The endpoint reads `<feedback_dir>/<run_id>.jsonl`'s `run_meta` event to recover the source PDF and building, then invokes the same extraction wrapper as `/sld/api/process` with `replace=true`. A new `run_id` is minted by the wrapper and returned in the response.

To wipe the SLD table and restart from scratch on this server, follow the procedure documented in the `electrical_building_schema` reset plan (DELETE rows + reset `sqlite_sequence` while `assetcap-el` is stopped — Channel B feedback files in `sld_extract_feedback/` are preserved as the audit trail).

## EL Amperage Migration

### Purpose

Populate and maintain these SQLite columns for EL and SDI package data:

- `Amperage Rating`
- `Amperage Rating (UoM)`

Rules applied by the migration:

- `Amperage Rating` is the canonical amperage field
- amperage is stored as integer-only text
- `Amperage Rating (UoM)` is `AMP` when `Amperage Rating` is populated, else blank
- legacy `Ampere` is kept as a compatibility mirror where that column exists

### Production Procedure Used

Stop the relevant writers before touching the operational database (this 2026-04-01 run predated the cutover and targeted the SQLite DB; post-cutover the same safe-write intent applies to PostgreSQL `qr_code_db` — quiesce the writers, then run the migration against the live PG backend):

```bash
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
```

Run the migration dry-run, then apply, then verify with a second dry-run:

```bash
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
```

Bring the services back online:

```bash
sudo systemctl start assetcap-app.service assetcap-el sdi_process
```

### Actual Result on 2026-04-01

Dry-run before apply:

```text
Dry run summary
  - sdi_dataset_EL: total=176, changed=0, rating_nonblank=146, uom_nonblank=146, ampere_nonblank=146
  - sdi_print_out: total=16, changed=0, rating_nonblank=0, uom_nonblank=0, ampere_nonblank=0 missing=['Amperage Rating (UoM)']
  - sdi_print_out_arch: total=388, changed=123, rating_nonblank=0, uom_nonblank=0, ampere_nonblank=123 missing=['Amperage Rating', 'Amperage Rating (UoM)']
No changes were applied.
```

Apply result:

```text
Backup created: /home/developer/asset_capture_app_dev/data/QR_codes.bak_20260401_091330_amperage_columns_migration.db
Migration summary
  - sdi_dataset_EL: total=176, changed=0, rating_nonblank=146, uom_nonblank=146, ampere_nonblank=146
  - sdi_print_out: total=16, changed=0, rating_nonblank=0, uom_nonblank=0, ampere_nonblank=0
  - sdi_print_out_arch: total=388, changed=123, rating_nonblank=123, uom_nonblank=123, ampere_nonblank=123
```

Verification dry-run after apply:

```text
Dry run summary
  - sdi_dataset_EL: total=176, changed=0, rating_nonblank=146, uom_nonblank=146, ampere_nonblank=146
  - sdi_print_out: total=16, changed=0, rating_nonblank=0, uom_nonblank=0, ampere_nonblank=0
  - sdi_print_out_arch: total=388, changed=0, rating_nonblank=123, uom_nonblank=123, ampere_nonblank=123
No changes were applied.
```

## Service Health Check

After restarting the services, verify they are running:

```bash
sudo systemctl status assetcap-app.service assetcap-el sdi_process --no-pager
```

Observed result on 2026-04-01:

- `assetcap-app.service`: active
- `assetcap-el.service`: active
- `sdi_process.service`: active

## Verification Query

Use this query to verify final counts for the amperage columns. The `sqlite3` form below is the historical 2026-04-01 record (it runs against the legacy SQLite rollback); post-cutover, run the equivalent `SELECT` against the operational PostgreSQL `qr_code_db` (`psql -h 127.0.0.1 -p 5433 -U developer -d qr_code_db`) — the column expressions are identical.

```bash
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "
SELECT 'sdi_dataset_EL', COUNT(*),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating\",'')) != '' THEN 1 ELSE 0 END),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating (UoM)\",'')) != '' THEN 1 ELSE 0 END)
FROM sdi_dataset_EL
UNION ALL
SELECT 'sdi_print_out', COUNT(*),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating\",'')) != '' THEN 1 ELSE 0 END),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating (UoM)\",'')) != '' THEN 1 ELSE 0 END)
FROM sdi_print_out
UNION ALL
SELECT 'sdi_print_out_arch', COUNT(*),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating\",'')) != '' THEN 1 ELSE 0 END),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating (UoM)\",'')) != '' THEN 1 ELSE 0 END)
FROM sdi_print_out_arch;
"
```

Observed result on 2026-04-01:

```text
sdi_dataset_EL|176|146|146
sdi_print_out|16|0|0
sdi_print_out_arch|388|123|123
```

## Notes

- The migration script is idempotent and can be run again safely.
- The script creates its own SQLite backup before applying changes.
- If the script lives at `/home/developer/scripts`, its default DB path resolves correctly to `/home/developer/asset_capture_app_dev/data/QR_codes.db`.
