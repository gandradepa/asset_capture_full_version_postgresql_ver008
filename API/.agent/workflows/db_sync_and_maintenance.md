---
description: Synchronize JSON extraction results to PostgreSQL `qr_code_db` and manage automation scripts.
---

# Database Sync & Maintenance

Current documentation refresh: 2026-04-28.

This workflow covers the post-extraction database synchronization process and production automation.

## Overview

After extraction scripts produce JSON files in `Output_jason_api/`, the `updating_process_database.py` script synchronizes this data into PostgreSQL `qr_code_db` for the Dashboard and Review apps to consume.

---

## Step 1: Run Database Sync Manually

```bash
python API/updating_process_database.py
```

This script performs three operations:
1. **Scans `Capture_photos_upload/`** â€” Processes JPG filenames to extract QR codes, building codes, and types
2. **Updates `QR_codes.date_set`** â€” Sets the upload date for each QR code
3. **Reads `Output_jason_api/`** â€” Parses all JSON files into a DataFrame and saves to `json_files` table

## Step 2: Verify Sync Results

```bash
python -c "
import sys
sys.path.insert(0, 'asset_capture_app_dev')
import db
conn = db.get_connection()
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM json_files')
print(f'Total JSON records in DB: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM QR_codes WHERE ai_status = 1')
print(f'QR codes with ai_status=1: {cur.fetchone()[0]}')
conn.close()
"
```

---

## Production Automation

### Option A: Cron-based Full Pipeline (`auto_process_assets.sh`)

This script runs EL, ME, and DB sync sequentially with timestamped logging.

```bash
# Add to crontab for automated runs:
crontab -e

# Example: Run every 6 hours
0 */6 * * * /home/developer/API/auto_process_assets.sh
```

**Log location**: `/home/developer/asset_capture_app_dev/logs/automation/run_<timestamp>.log`

> [!IMPORTANT]
> The `auto_process_assets.sh` currently runs EL â†’ ME only. Add BF extraction step if needed.

### Option B: Dashboard-triggered (`run_ai_and_sync.sh`)

The Dashboard's task runner calls this script with the AI script path as an argument:

```bash
bash API/run_ai_and_sync.sh API/API_interface_ME_ver00.py
```

This:
1. Activates the venv
2. Runs the AI extraction script
3. If successful, automatically runs `updating_process_database.py`

### Option C: Generic venv Wrapper (`run_interpreter.sh`)

For ad-hoc Python commands using the project venv:

```bash
bash API/run_interpreter.sh API/updating_process_database.py
```

---

## Database Tables Reference

| Table | Updated By | Purpose |
|-------|-----------|---------|
| `QR_codes` | Extraction scripts | Sets `ai_status=1` after processing |
| `QR_codes.date_set` | `updating_process_database.py` | Upload timestamp from image filenames |
| `json_files` | `updating_process_database.py` | Full DataFrame of JSON output data |
| `sdi_dataset` | Extraction scripts (read-only) | Checks `Approved`/`Flagged` status |

## Maintenance Tasks

### Reset ai_status for Reprocessing

```bash
python -c "
import sys
sys.path.insert(0, 'asset_capture_app_dev')
import db
conn = db.get_connection()
# Reset ALL:
conn.execute('UPDATE "QR_codes" SET ai_status = 0')
# Or reset specific QR:
# conn.execute('UPDATE "QR_codes" SET ai_status = 0 WHERE "QR_code_ID" = ?', ('0000177289',))
conn.commit()
print(f'Updated {conn.total_changes} rows')
conn.close()
"
```

### Clean Stale JSON Output

```bash
# List JSON files older than 30 days
find Output_jason_api/ -name "*.json" -mtime +30

# Remove (with confirmation)
find Output_jason_api/ -name "*.json" -mtime +30 -exec rm -i {} \;
```

### Backup Database

```bash
pg_dump "$QR_PG_DSN" > backup_qr_code_db_$(date +%Y%m%d).sql
```
