---
description: End-to-end asset capture session â€” from QR scanning through photo submission and database persistence.
---

# Capture Asset Session

Current documentation refresh: 2026-05-25.

This workflow describes the complete end-to-end flow for a field technician capturing a new asset.

---

## Step 1 â€” Login

1. Navigate to `http://<server>:5001`
2. Redirected to `/auth/login` if not authenticated
3. Enter credentials â†’ session created via Flask-Login

---

## Step 2 â€” Start Page (`/`)

1. The `start()` route renders `start.html` with all building options loaded from `Buildings` table
2. Technician performs one of:
   - **Scan QR code** using device camera (JS in `start.js`)
   - **Enter manually** â€” type the 10-digit QR code
   - **Request temp code** â€” calls `/api/get-temp-code` â†’ receives `TEMP-XXXXX`

3. **QR Validation**:
   - Client-side: must be 10 digits or `TEMP-XXXXX` format
   - Server-side: `/api/check-qr?qr=XXXXXXXXXX` checks if QR exists in `QR_codes` table
   - If exists: shows existing building, location, and asset type for re-capture

4. **Select Building**:
   - Dropdown populated from `_load_buildings_from_sqlite()` â†’ `Buildings` table
   - On selection: calls `/api/locations?building=<code>` â†’ loads locations

5. **Select Location**:
   - Dropdown populated from `Buildings_with_SpaceUID` view
   - Format: `<Space_Number> <Space_Name> <Floor_Name>`

6. **Select Asset Type**:
   - Options: Mechanical, Electrical, Backflow
   - Maps to abbreviation: ME, EL, BF

7. Click **Proceed** â†’ navigates to `/capture` with form data

---

## Step 3 â€” Capture Page (`/capture`)

### Page Load
1. `capture()` route receives QR, building, location, asset type via URL params or form data
2. Calls `list_existing_uploads()` to find any previously uploaded photos for this QR+building+type
3. Renders `capture.html` with session data and existing photo gallery

### Camera Interaction
1. Click **Open Camera** â†’ requests camera permission via `getUserMedia()`
2. Camera modal opens with:
   - Live video preview
   - Zoom control (cycle through 1x, 2x, 5x by tapping)
   - **Capture** button â†’ takes high-res photo
3. Photo added to gallery with preview thumbnail
4. Repeat for additional photos. Tile count per asset type:
   - **Mechanical**: 4 required tiles (`-0` Asset Plate, `-1` UBC Tag, `-2` Main Asset Photo, `-3` Technical Safety BC) plus optional `-4` Extra Photo
   - **Backflow**: 3 required tiles (`-0` Asset Plate, `-1` Asset Plate (additional), `-2` Main Photo) plus optional `-3` Extra Photo
   - **Electrical**: 3 required tiles (`-0` Asset Plate, `-1` UBC Asset Tag, `-2` Panel Schedule) plus optional `-3` Extra Photo
5. The Extra Photo tile is rendered with `data-optional="true"`. `updateCompletionState()` ignores it, so the green "all required captured" toast fires once the required tiles are filled — submit is allowed without the Extra Photo.
6. Every submission must include a newly selected `-0` or `-1` photo. Either slot satisfies the minimum; stored photos and `-2` or later uploads do not. Client-side validation highlights the first two cards, and `/submit` repeats the check before any overwrite deletion.

### Photo Management
- **Delete**: Remove a photo from gallery before submission
- **Retake**: Replace a specific photo
- Gallery shows all photos with sequence numbers

### Optional Capture Details (added 2026-07-06)
Below the photo grid, before the sticky Save bar, two optional fields:
- **Notes** — multi-line textarea, `maxlength=200` with a live `0/200` counter; trimmed and clamped again server-side. Never blocks a save.
- **Installation Date** — native date picker capped at today. The picker carries no `name`: the submitted value lives in a hidden `installation_date` input populated only when the user taps the ✓ confirm button (prevents iOS from silently saving an auto-filled "today"); the ✕ button clears it.

### Parameter Change (Mid-Session)
If the technician needs to change building, location, or asset type:
1. Update the fields on the capture page
2. Triggers `/api/update-parameters` â†’ see `parameter_update.md` workflow
3. Existing files are renamed atomically
4. Gallery refreshes with updated filenames

---

## Step 4 â€” Submit (`/submit`)

### Server Processing (in order)
1. **Validate primary photo**: reject unless the request contains a newly uploaded `image_0` or `image_1`; this happens before any destructive overwrite action.
2. **Delete old files**: `delete_files_by_qr(qr_code)` removes previous uploads
3. **Delete old DB rows**: `delete_from_assets_by_qr(conn, qr_code)` clears `QR_code_assets`
4. **Save new photos**: Each file saved via `save_image_file()` with naming pattern:
   ```
   <QR> <Building> <AssetType> - <Seq>.jpg
   ```
   - The submit loop iterates seqs `0..4` (ME) or `0..3` (BF/EL); missing seqs are skipped by the existing `continue` guard.
   - `save_image_file()` applies `ImageOps.exif_transpose()` before writing, so phone photos with an EXIF Orientation tag are physically rotated to portrait on disk before storage. The transpose is a no-op for files without EXIF Orientation or with Orientation = 1.
5. **Insert DB rows**: `insert_into_assets(conn, file_bases, username)` creates `QR_code_assets` records
6. **Upsert QR_codes**: `upsert_qr_codes(conn, qr, building, location, asset_type, ...)` updates master record, including the optional `capture_notes` (clamped to 200 chars) and `installation_date` (validated ISO `YYYY-MM-DD` via `normalize_iso_date()`) when provided — latest non-empty submission wins, an empty resubmission never erases a stored value
   - SQLite triggers fire automatically:
     - `auto_fill_all_on_insert` â†’ populates Space, Floor, Space Details, Floor Code
     - `T_set_ai_status` â†’ checks if extraction data exists in `sdi_dataset`
     - `trg_qr_codes_sdi_default_zero` â†’ sets `sdi = 0` if null
7. **Update process status**: `update_asset_process(conn, qr, "Captured")`
8. **Write elapsed JSON**: `write_elapsed_time_json(qr, building, type, elapsed, capture_notes, installation_date)`

### Post-Submit Behavior
- Default: redirect to `/success` page
- Optional: redirect back to `/capture` for next asset (controlled by `after_submit` param)

---

## Step 5 â€” Success (`/success`)

1. Shows confirmation with QR code, building, asset type, and number of photos saved
2. **Start New Capture** button â†’ returns to `/`
3. **Continue Capturing** option â†’ returns to `/capture` with same session

---

## Database Changes Summary

| Step | Table | Operation |
|------|-------|-----------|
| QR Check | `QR_codes` | SELECT (check existence) |
| QR Check | `sdi_dataset`, `sdi_dataset_EL` | SELECT (load existing data) |
| Temp Code | `temp_code` | UPDATE (mark as used) |
| Submit | `QR_code_assets` | DELETE (old) â†’ INSERT (new) |
| Submit | `QR_codes` | INSERT or UPDATE (upsert) |
| Submit | `process_type` | INSERT (via trigger) |

## File System Changes Summary

| Step | Directory | Operation |
|------|-----------|-----------|
| Submit | Upload dir | DELETE old photos â†’ SAVE new photos |
| Submit | JSON dir | WRITE `<QR>_et.json` (elapsed time + `capture_notes` + `installation_date`) |
