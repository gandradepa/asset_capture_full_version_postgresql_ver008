---
name: capture_app
description: Developer skill guide for the UBC Asset Capture App. Covers project structure, Flask routes, camera interface, parameter update service, database operations, and common development tasks.
---

# Asset Capture Application Skill

Current documentation refresh: 2026-05-25.

## Use this skill when
- Adding, modifying, or debugging Flask routes in the mobile Asset Capture App (`app.py`)
- Modifying the frontend UI, camera capture logic, or QR scanning behavior
- Updating the database schemas or queries related to `QR_codes.db` during capture
- Debugging the atomic parameter update service and file rename operations

## Do not use this skill when
- Modifying the core API extraction scripts (refer to `API/.agent` instead)
- Modifying the Dashboard or Plate Review apps (refer to their respective `.agent` folders)

## Instructions
Review the project structure and routing catalog before implementing feature changes. The Capture App favors a mobile-first, utility-class CSS approach and uses raw ES6+ Javascript with no frameworks.

## Project Structure

```
asset_capture_app_dev/
â”œâ”€â”€ app.py                          # Flask app entry point (port 5001, 963 lines)
â”œâ”€â”€ auth_routes.py                  # Auth blueprint (login/logout/change-password)
â”œâ”€â”€ templates/
â”‚   â”œâ”€â”€ base.html                   # Base template (shared head, CSS)
â”‚   â”œâ”€â”€ start.html                  # Landing page (56K â€” QR scan, building/location select)
â”‚   â”œâ”€â”€ capture.html                # Camera interface (36K â€” photo capture + gallery)
â”‚   â”œâ”€â”€ success.html                # Submission confirmation
â”‚   â”œâ”€â”€ index.html                  # Welcome / root redirect
â”‚   â”œâ”€â”€ login.html                  # Login form
â”‚   â”œâ”€â”€ change_password.html        # Password update
â”‚   â””â”€â”€ logo.html                   # UBC logo partial
â”œâ”€â”€ static/
â”‚   â”œâ”€â”€ css/styles.css              # Main stylesheet (46K, utility classes)
â”‚   â”œâ”€â”€ css/ui-components.css       # Component styles (12K)
â”‚   â”œâ”€â”€ css/logo.css                # Logo styles
â”‚   â”œâ”€â”€ js/start.js                 # Start page JS (35K â€” QR scan, forms, API calls)
â”‚   â”œâ”€â”€ img/                        # Static images
â”‚   â””â”€â”€ logos/                      # UBC branding
â”œâ”€â”€ utils/
â”‚   â””â”€â”€ parameter_update_service.py # Atomic param update engine (905 lines)
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ QR_codes.db                 # Primary SQLite database
â”‚   â”œâ”€â”€ User_control.db             # Auth database
â”‚   â””â”€â”€ *.log                       # Processing logs (images, JSON)
â”œâ”€â”€ requirements.txt                # Flask>=2.0, SQLAlchemy, dotenv, etc.
â””â”€â”€ venv/                           # Virtual environment
```

---

## Flask Route Catalog

### Authentication Routes (`auth` Blueprint)

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET/POST | `/auth/login` | `login()` | Login form, credential validation, session start |
| GET | `/auth/logout` | `logout()` | End session, redirect to login |
| GET/POST | `/auth/change-password` | `change_password()` | Password update (min 8 chars, special char required) |

### Page Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/` | `start()` | Landing page â€” renders `start.html` with building options |
| GET/POST | `/capture` | `capture()` | Camera interface â€” renders `capture.html` with existing uploads |
| POST | `/submit` | `submit()` | Process uploads, save to DB, write elapsed JSON |
| GET | `/success` | `submit_success()` | Render confirmation page |
| GET | `/health` | `health()` | Returns `{"status": "ok"}` |
| GET | `/uploads/<filename>` | `uploaded_file()` | Serve uploaded photo files |

### API Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/api/locations?building=XX` | `api_locations()` | Location list for building code (from `Buildings_with_SpaceUID` view) |
| GET | `/api/check-qr?qr=XXXXXXXXXX` | `api_check_qr()` | Check QR existence, return building/location/asset type if found |
| POST | `/api/update-parameters` | `api_update_parameters()` | Atomic update of building/location/asset type (renames files + DB) |
| GET | `/api/get-temp-code` | `api_get_temp_code()` | Assign next available temporary QR code from pool |

---

## Core Module Documentation

### `app.py` â€” Main Application

**Helper Functions:**
| Function | Purpose |
|----------|---------|
| `_safe_str(x)` | Convert any value to stripped string |
| `sanitize_component(s)` | Clean filename component (alphanumeric, truncate) |
| `parse_iso8601_utc(iso)` | Parse ISO timestamps to UTC datetime |
| `elapsed_mmss(started_at)` | Calculate elapsed time as `MM:SS` string |
| `write_elapsed_time_json(...)` | Write elapsed time JSON to output directory |
| `map_asset_type_to_abbrev(t)` | Map "Mechanical" â†’ "ME", "Electrical" â†’ "EL", "Backflow" â†’ "BF" |
| `seq_to_label(type, seq)` | Convert sequence number to human label |
| `save_image_file(storage, path)` | Re-encode JPEG via Pillow; applies `ImageOps.exif_transpose()` to honor EXIF Orientation so phone photos are stored upright (fallback to raw byte write on failure) |

**SQLite Helpers:**
| Function | Purpose |
|----------|---------|
| `_open_db()` | Open connection to `QR_codes.db` |
| `_table_columns(conn, table)` | Get column names for a table |
| `_has_table(conn, table)` | Check if table exists |
| `_find_assets_table(conn)` | Find `QR_code_assets` table (handles naming variations) |
| `_load_locations_from_sqlite(code)` | Load locations for building from `Buildings_with_SpaceUID` view |
| `_load_buildings_from_sqlite()` | Load all buildings as `{code, name}` list |
| `get_building_options()` | Get building dropdown options |
| `upsert_qr_codes(...)` | Insert or update QR_codes record (ensures columns exist) |
| `insert_into_assets(...)` | Bulk insert into QR_code_assets |
| `delete_from_assets_by_qr(...)` | Delete all asset rows for a QR code |
| `delete_files_by_qr(qr)` | Delete all uploaded files for a QR code |
| `qr_exists(conn, qr)` | Check if QR code exists in QR_codes table |
| `list_existing_uploads(...)` | List existing photo files for a QR/building/type combo |
| `get_next_temp_code(conn)` | Get next available temp code from pool |

---

### `parameter_update_service.py` â€” Atomic Parameter Updates

This module handles the complex process of changing building, location, or asset type for an existing QR code. It performs all updates atomically with rollback support.

**Public API:**

| Function | Purpose |
|----------|---------|
| `detect_parameter_changes(old, new)` | Compare old vs. new params, return change flags |
| `get_current_params(conn, qr)` | via `_lookup_dataset_params()` â€” get current building/location/type |
| `get_current_asset_type(conn, qr)` | via `_lookup_dataset_params()` â€” get current asset type only |

**Internal Pipeline (called by `api_update_parameters`):**

```
1. detect_parameter_changes()     â†’ Determine what changed
2. get_affected_files()           â†’ Find all files for this QR
3. backup_files()                 â†’ Create temp backup directory
4. rename_files_atomic()          â†’ Rename with new building/type
5. update_qr_codes_table()        â†’ Update QR_codes row
6. update_assets_table()          â†’ Update QR_code_assets rows
7. purge_orphan_asset_rows()      â†’ Clean up orphan records
8. update_sdi_dataset_table()     â†’ Update sdi_dataset / sdi_dataset_EL
9. update_json_files()            â†’ Rename and edit JSON output files
10. rollback_file_changes()       â†’ (on failure) Restore from backup
```

---

### Legacy Access-DB Utils (removed 2026-06-09)

- `file_handler.py` (upload handler) and `building_lookup.py` (building lookup) targeted the original on-premise Microsoft Access `.accdb` via `pyodbc`
- **Superseded** by the SQLite logic in `app.py`; never imported by the live app
- Deleted as dead code after the 2026-06-09 PG-identifier audit (recoverable from git history)

---

## Camera & Capture Workflow

### Technology Stack
- **Camera Access**: `navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })`
- **Photo Capture**: `ImageCapture` API (where supported) or `<canvas>.toDataURL()` fallback
- **Zoom**: `MediaStreamTrack.applyConstraints({ advanced: [{ zoom: level }] })`
- **UI Framework**: Custom CSS utility classes (similar to Tailwind)

### User Flow
```
1. /start â†’ Scan QR (or enter manually) â†’ Select Building â†’ Select Location â†’ Select Asset Type
2. /capture â†’ Open camera â†’ Take photos (1-N) â†’ Review gallery â†’ (optional: retake/delete)
   â†’ (optional: Notes textarea, 200-char max; Installation Date picker with âœ“ confirm step)
3. /submit â†’ Photos saved to server â†’ DB records created (incl. optional capture_notes /
   installation_date on QR_codes) â†’ Elapsed time JSON written (incl. both new keys)
4. /success â†’ Confirmation shown â†’ Back to /start for next asset
```

### Photo Naming
```
<QR_code> <Building_Code> <AssetType_Abbrev> - <Sequence>.jpg
Example: 0000177276 314-1 ME - 0.jpg
         0000177276 314-1 ME - 1.jpg
```

### Sequence Ranges per Asset Type

| Type | Sequences | Labels |
|---|---|---|
| Mechanical (ME) | `-0`..`-4` | Asset Plate / UBC Tag / Main Asset Photo / Technical Safety BC / **Extra Photo (Optional, `-4`)** |
| Backflow (BF) | `-0`..`-3` | Asset Plate / Asset Plate (additional) / Main Photo / **Extra Photo (Optional, `-3`)** |
| Electrical (EL) | `-0`..`-3` | Asset Plate (Optional) / UBC Asset Tag / Full Interior Panel / **Extra Photo (Optional, `-3`)** |

The Extra Photo is captured/displayed but never feeds completeness, AI confidence, AI extraction (`VALID_SUFFIXES`), or "Missed Photo". Its file input is marked `data-optional="true"` so `updateCompletionState()` excludes it from the green "all required captured" toast.

---

## Database Usage Patterns

### Creating a New QR Record
```python
conn = _open_db()
upsert_qr_codes(conn, qr_code, building_code, location, asset_type)
# â†’ Creates/updates QR_codes row
# â†’ Ensures Building Code, asset_type, elapsetime columns exist
# â†’ Triggers fire: auto_fill_all_on_insert (Space, Floor, Floor Code)
#                  T_set_ai_status (check sdi_dataset existence)
#                  trg_qr_codes_sdi_default_zero (set sdi = 0)
```

### Saving Photo Records
```python
insert_into_assets(conn, file_bases, username)
# â†’ Inserts each filename base into QR_code_assets
# â†’ Sets user and date_hour columns
```

### Checking QR Existence
```python
if qr_exists(conn, qr_code):
    # QR already captured â€” show existing data, offer re-capture
```

---

## Common Development Tasks

### Adding a New Asset Type
1. Update `map_asset_type_to_abbrev()` in both `app.py` and `parameter_update_service.py`
2. Update `seq_to_label()` in `app.py` with the new type's label mapping
3. Add the type option in `start.html` asset type selector
4. Update `capture.html` if the new type requires conditional fields
5. Test full flow: start â†’ capture â†’ submit â†’ verify DB and files

### Modifying Camera Logic
1. Edit the camera functions in `capture.html` (`openCamera`, `capturePhoto`, `stopCamera`)
2. Test on actual mobile devices (not just desktop browser)
3. Ensure `ImageCapture` fallback to `<canvas>` still works
4. Handle orientation changes (portrait/landscape)

### Modifying the Start Page
1. JS logic is primarily in `static/js/start.js` (35K)
2. HTML structure is in `templates/start.html` (56K)
3. API calls for building/location data use `/api/locations` and `/api/check-qr`

### Adding a New API Endpoint
1. Add the route function in `app.py` (after the existing API routes section)
2. Add `@login_required` decorator
3. Return via `jsonify()` with appropriate error handling
4. Document in this SKILL.md route catalog

### Styling Updates
1. Check `static/css/styles.css` first for existing utility classes
2. Add component-specific styles to `static/css/ui-components.css`
3. Always test at mobile viewports (375px minimum width)
4. Use touch-friendly targets (44px minimum)
