---
description: Run the full asset extraction pipeline for Mechanical (ME), Electrical (EL), and Backflow (BF) assets.
---

# Run Full Extraction Pipeline

Current documentation refresh: 2026-04-28.

This workflow guides you through running the complete data extraction and database synchronization process for all asset types.

## Prerequisites

1. **Environment Check**:
   - Ensure `.env` file exists at `API/OpenAI_key_giba.env` with `OPENAI_API_KEY`
   - Ensure Tesseract OCR is installed and added to PATH (or configured in scripts)
   - Ensure Python venv is activated with all dependencies from `requirements.txt`

2. **Input Data**:
   - Place asset photos in `Capture_photos_upload/`
   - Ensure filenames follow the pattern: `<QR> <Building> <Type>-<Seq>.jpg`
   - Example: `0000183710 100 EL-1.jpg`

## Step 1: Run Mechanical (ME) Extraction

Process HVAC units, pumps, fans, and other mechanical equipment.

```bash
python API/API_interface_ME_ver00.py
```

Expected image sequences: `-0` (Nameplate), `-1` (UBC Tag), `-3` (Technical Safety BC)

## Step 2: Run Electrical (EL) Extraction

Process panels, splitters, disconnects, and other electrical equipment.

```bash
python API/API_interface_EL_ver00.py
```

Expected image sequences: `-0` (Tag), `-1` (Plate), `-2` (Fed-From label)

## Step 3: Run Backflow (BF) Extraction

Process backflow prevention devices.

```bash
python API/API_interface_BF_ver00.py
```

Expected image sequences: `-0` (Nameplate), `-1` (Context) â€” **2 images only**

## Step 4: Run Database Synchronization

Sync JSON output files and image metadata into PostgreSQL `qr_code_db` through `db.py`.

```bash
python API/updating_process_database.py
```

This updates the `QR_codes.date_set` column and writes all JSON data to the `json_files` table.

## Step 5 (Optional): Run via Shell Automation

For production (Ubuntu), use the shell scripts instead of manual steps:

```bash
# Option A: Full pipeline via cron script (EL â†’ ME â†’ DB sync)
bash API/auto_process_assets.sh

# Option B: Single type via Dashboard launcher (script + DB sync)
bash API/run_ai_and_sync.sh API/API_interface_ME_ver00.py
```

## Verification

1. **Check Output**:
   - Navigate to `Output_jason_api/`
   - Verify JSON files are created for processed assets (e.g., `0000183710_EL_100.json`)
   - Open a JSON file and confirm `completeness_score` is reasonable

2. **Check Console Summary**:
   - Review console output for the summary line: `Successfully saved: N`
   - Confirm each saved asset is listed: `- QR: {qr} | {TYPE} | {TIMESTAMP} | Building: {building}`

3. **Check Database**:
   ```bash
   python -c "
   import sys
   sys.path.insert(0, 'asset_capture_app_dev')
   import db
   conn = db.get_connection()
   cur = conn.cursor()
   cur.execute('SELECT COUNT(*) FROM "QR_codes" WHERE ai_status = 1')
   print(f'Processed QRs: {cur.fetchone()[0]}')
   cur.execute('SELECT COUNT(*) FROM json_files')
   print(f'JSON records: {cur.fetchone()[0]}')
   conn.close()
   "
   ```

4. **Check Logs** (if using shell automation):
   ```bash
   ls -lt asset_capture_app_dev/logs/automation/ | head -5
   cat asset_capture_app_dev/logs/automation/run_<latest>.log
   ```
