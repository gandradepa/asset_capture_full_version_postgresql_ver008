# UBC Asset Capture Application â€“ Technical Documentation

Current documentation refresh: 2026-05-25.

## 1. System Overview

The UBC Asset Capture Application is a distributed monolith platform for capturing, extracting, reviewing, and packaging asset data. It integrates mobile capture, AI extraction (OCR + LLM), human review, and SDI packaging for downstream Planon CMMS integration.

### 1.1 Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask, Gunicorn |
| Database | PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`; SQLite `QR_codes.db` is the frozen rollback), SQLAlchemy/SQLite (auth) |
| Frontend | HTML5, Vanilla JS (ES6+), CSS3 |
| AI/ML | OpenAI GPT API, Tesseract OCR |
| Charts | Matplotlib, Altair (FLS), custom chart modules |
| Auth | Flask-Login, bcrypt, Flask-SQLAlchemy |
| Deployment | Ubuntu, systemd, Nginx reverse proxy |

## 2. Architecture

### 2.1 Component Roles

| Component | Role | Entry Point | Lines |
|---|---|---|---|
| Capture App | Photo capture, QR registration, parameter updates | `asset_capture_app_dev/app.py` | 999 |
| Dashboard | Analytics, monitoring, FLS management, dictionary editing | `Dashboard/Asset_portal_dashboard.py` | 2,616 |
| Review ME | Mechanical review and approval | `review/.../asset_plate_reviewer.py` | â€” |
| Review BF | Backflow review and approval | `review/.../asset_plate_reviewer_bf.py` | â€” |
| Review EL | Electrical review and approval | `review/.../Asset_dashboard_EL.py` | â€” |
| SDI Process | Packaging, Planon export, archive management | `SDI_process/app.py` | 1,276 |
| Extraction ME | Mechanical AI extraction | `API/API_interface_ME_ver00.py` | â€” |
| Extraction BF | Backflow AI extraction | `API/API_interface_BF_ver00.py` | â€” |
| Extraction EL | Electrical AI extraction | `API/API_interface_EL_ver00.py` | â€” |
| DB Sync | JSON â†’ DB synchronization | `API/updating_process_database.py` | â€” |
| Shared Validators | Cross-discipline validation | `API/validators_shared.py` | â€” |
| Auth Service | Shared authentication | `auth_service/` | â€” |
| Dictionary | Classification taxonomy | `dictionary/mechanical_dictionary.py` | â€” |

### 2.2 Data Flow Diagram

```
Field Tech        Capture App        Extraction API       Review Apps
    â”‚                  â”‚                   â”‚                   â”‚
    â”œâ”€â”€â”€â”€ Scan QR â”€â”€â”€â”€â–¶â”‚                   â”‚                   â”‚
    â”œâ”€â”€â”€ Take Photo â”€â”€â–¶â”‚                   â”‚                   â”‚
    â”‚                  â”œâ”€â”€ Save Photo â”€â”€â”€â”€â–¶â”‚ (filesystem)      â”‚
    â”‚                  â”œâ”€â”€ Upsert DB â”€â”€â”€â”€â”€â–¶â”‚ (QR_codes)        â”‚
    â”‚                  â”‚                   â”‚                   â”‚
    â”‚                  â”‚    Dashboard â”€â”€â”€â”€â”€â–¶â”‚ Launch AI+Sync    â”‚
    â”‚                  â”‚                   â”œâ”€â”€ OCR + LLM â”€â”€â”€â”€â”€â–¶â”‚
    â”‚                  â”‚                   â”œâ”€â”€ Write JSON â”€â”€â”€â”€â”€â–¶â”‚
    â”‚                  â”‚                   â”œâ”€â”€ Sync DB â”€â”€â”€â”€â”€â”€â”€â”€â–¶â”‚
    â”‚                  â”‚                   â”‚                   â”‚
    â”‚                  â”‚                   â”‚     Reviewer â”€â”€â”€â”€â”€â–¶â”‚ Correct+Approve
    â”‚                  â”‚                   â”‚                   â”œâ”€â”€ Sync SDI Tables
    â”‚                  â”‚                   â”‚                   â”‚
    â”‚                  â”‚         SDI Process â—€â”€â”€â”€ Package + Export to Planon
```

### 2.3 Shared State Model

| Store | Type | Purpose |
|---|---|---|
| `qr_code_db` | PostgreSQL | Operational state for all services (VM `127.0.0.1:5433`; SQLite `QR_codes.db` is the frozen rollback) |
| `Capture_photos_upload/` | Filesystem | Raw captured images |
| `Output_jason_api/` | Filesystem | JSON payloads + elapsed-time artifacts |
| `User_control.db` | SQLite | Auth and user management |
| `mechanical_dictionary.py` | Python/JSON | Classification taxonomy |

## 3. Database Schema

### 3.1 Core Tables

#### `QR_codes`
QR-level state for one asset identity.

| Column | Type | Description |
|---|---|---|
| `QR_code_ID` | TEXT | Normalized QR identifier (primary) |
| `Building Code` | TEXT | Building code reference |
| `asset_type` | TEXT | ME, BF, or EL |
| `Location` | TEXT | Physical location |
| `Space` | TEXT | Space identifier |
| `Floor` | TEXT | Floor name |
| `Floor_Code` | TEXT | Floor code |
| `Approved` | INTEGER | Approval state |
| `ai_status` | INTEGER | AI processing status |
| `sdi` | INTEGER | SDI exclusion flag (1 = excluded) |
| `elapsetime` | TEXT | Capture elapsed time |
| `capture_latitude` | TEXT | Per-capture device GPS latitude (added 2026-06-16) |
| `capture_longitude` | TEXT | Per-capture device GPS longitude (added 2026-06-16) |
| `capture_coord_source` | TEXT | GPS provenance: `device` \| `building` \| `''` (added 2026-06-16) |
| `GPS Coordinates (lat,long)` | TEXT | Merged display coordinate pair (added 2026-06-23) |
| `capture_notes` | TEXT | Optional field-tech note, clamped to 200 chars (added 2026-07-06) |
| `installation_date` | TEXT | Optional asset installation date, ISO `YYYY-MM-DD`, set only after explicit ✓ confirm (added 2026-07-06) |

#### `QR_code_assets`
Per-photo and process-placement table.

| Column | Type | Description |
|---|---|---|
| `code_assets` | TEXT | File base identifier (unique) |
| `Col_process` | INTEGER | Process placement (2 = Manual Entry) |
| `api_int` | INTEGER | API processing flag |
| `user` | TEXT | Authenticated username who captured |
| `date_hour` | TEXT | ISO 8601 timestamp of capture |

#### `json_files`
Extraction summary and synchronization table.

Key columns: QR identity, asset type, approval state, completeness rollups, `Avg_ai_conf`.

#### `sdi_dataset`
Curated approved ME and BF rows.

Key columns: `QR Code`, `Building`, `Manufacturer`, `Model`, `Serial`, `UBC Tag`, `Technical Safety BC`, `Diameter`, `Year`, `Asset Group`, `Attribute`, `Description`, `Approved`, `Flagged`, `Avg_ai_conf`.

#### `sdi_dataset_EL`
Curated approved EL rows.

Key columns: `QR Code`, `Building`, `UBC Asset Tag`, `Equipment ID`, `Equipment Type`, `Branch Panel`, `Amperage Rating`, `Ampere`, `Supply From`, `Volts`, `Location`, `Asset Group`, `Attribute`, `Description`, `Approved`, `Flagged`, `Avg_ai_conf`.

`Amperage Rating` is the canonical curated EL amperage field in `sdi_dataset_EL`. `Ampere` remains a compatibility mirror for the review JSON/UI and downstream consumers that have not been renamed yet. Both fields store the integer amperage only; the unit is handled separately.

`Equipment ID` is the canonical curated EL source for Planon `Equipment ID`. In this phase it remains auto-derived from the curated EL `UBC Asset Tag` value rather than being edited directly in the review UI.

`Equipment Type` is the canonical curated EL source for Planon `Equipment Type`. It is auto-derived from the EL `Equipment ID` or `UBC Asset Tag` prefix using the current electrical mapping, then preserved through package/archive flows.

`Power Rating` and `Power Rating (UoM)` are now populated by the EL AI extraction flow and stored in `sdi_dataset_EL`. The extraction rule only accepts `KVA`, `KW`, or `VA` when the unit is immediately paired with a whole number, so voltage text such as `600V-208Y/120V` is not treated as power rating.

#### `sdi_print_out` / `sdi_print_out_arch`
Active and archived SDI package rows.

For EL rows, package tables preserve `Equipment ID` and `Equipment Type` so Planon export reads the stored curated fields instead of recomputing them only at final export time.

#### `new_device`
FLS asset tracking with Planon checklist columns.

| Column | Type | Description |
|---|---|---|
| `Request Open` | INTEGER | Planon request status |
| `Request Date` | TEXT | Date of request |
| `Elapsed Time` | INTEGER | Time elapsed |
| `Complete` | INTEGER | Completion status |
| `Ticket Number` | TEXT | Planon ticket reference |

Auto-migrated at Dashboard startup via `_ensure_new_device_columns()`.

FLS Control Panel Code/Description is not stored here; Dashboard derives it from `"UBC - Asset Data Master Info"` by selected building property code and flags multi-match lookups.

#### `Buildings_with_SpaceUID`
Building and location lookup for capture workflows.

| Column | Type | Description |
|---|---|---|
| `Code` | TEXT | Building code |
| `Location` | TEXT | Location name |

## 4. Technical Contracts

### 4.1 QR Identity Contract

A valid QR code must remain consistent across:
- Image filenames (`<QR> <Building> <Type> - <Seq>.jpg`)
- JSON filenames (`<QR>_<TYPE>_<Building>.json`)
- `QR_codes.QR_code_ID`
- `QR_code_assets.code_assets`
- Curated dataset rows (SDI tables)
- SDI package rows

### 4.2 Completeness Contract

| Discipline | Fields | Count |
|---|---|---|
| ME | Manufacturer, Model, Serial Number, Year, UBC Tag | 5 (+ Technical Safety BC when seq -3 exists = 6) |
| BF | Manufacturer, Model, Serial Number, Diameter | 4 |
| EL | UBC Asset Tag, Ampere, Supply From | 3 |

EL note: the completeness contract above still refers to extraction/review payload keys. In curated `sdi_dataset_EL` rows, amperage is stored canonically in `Amperage Rating`.

Extra Photo note: the optional Extra Photo sequence (ME `-4`, BF `-3`, EL `-3`) is not part of completeness for any discipline. It is excluded from the pipeline `VALID_SUFFIXES` and never reaches the LLM, so it owns no field values and cannot influence completeness.

### 4.3 AI Confidence Contract

- Blank final fields must not retain stale non-zero confidence
- `Avg_ai_conf` is discipline-aware
- EL excludes `Volts`, `Location`, and `Branch Panel`
- ME includes `Technical Safety BC` only when seq `-3` exists

### 4.4 Review / SDI Contract

- Save and approval sync curated rows into SDI dataset tables
- Manual Entry means SDI exclusion, not only alternate review placement
- SDI unpackaged rows are built from approved curated rows plus QR-level exclusion state

### 4.5 Atomic Rename Contract

Parameter changes must update atomically:
1. Image filenames in `Capture_photos_upload/`
2. JSON filenames and interior fields in `Output_jason_api/`
3. DB rows in `QR_codes`, `QR_code_assets`, SDI tables
4. Roll back on any failure

## 5. API Endpoints Reference

### 5.1 Capture App (Port 5001)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | GET | Yes | Start page |
| `/capture` | GET/POST | Yes | Capture interface |
| `/submit` | POST | Yes | Submit photos |
| `/api/check-qr` | GET | Yes | Check QR, return current params |
| `/api/update-parameters` | POST | Yes | Atomic parameter update |
| `/api/locations` | GET | Yes | Location list by building |
| `/api/get-temp-code` | POST | Yes | Get temporary QR code |
| `/delete-upload` | POST | Yes | Delete uploaded photo |
| `/health` | GET | No | Health check |

### 5.2 Dashboard (Port 8002)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | GET | Yes | Main dashboard |
| `/run/<task_key>` | POST | Yes | Launch extraction task |
| `/log-status/<name>` | GET | Yes | Check log status |
| `/logs` | GET | Yes | List all logs |
| `/read-log` | GET | Yes | Read log content |
| `/chart/approval` | GET | Yes | Approval chart image |
| `/chart/completeness` | GET | Yes | Completeness chart |
| `/chart/ai-confidence` | GET | Yes | AI confidence chart |
| `/chart/data-quality` | GET | Yes | Data quality comparison |
| `/chart/operational-cost` | GET | Yes | Operational cost chart |
| `/chart/sdi-flow` | GET | Yes | SDI flow chart |
| `/fls-charts` | GET | Yes | FLS charts (Altair) |
| `/add-fls-assets` | POST | Yes | Add FLS asset |
| `/delete-fls-assets` | POST | Yes | Delete FLS asset |
| `/bulk-update-assets` | POST | Yes | Bulk update FLS assets |
| `/api/asset-photo/<qr>` | GET | Yes | Serve asset photo |
| `/change-password` | POST | Yes | Change password |

### 5.3 SDI Process (Port 5005)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | GET/POST | Yes | Dashboard |
| `/export-to-sdi` | POST | Yes | Create SDI package |
| `/export-to-planon` | POST | Yes | Planon-formatted export |
| `/exclude-package` | POST | Yes | Exclude package |
| `/move-to-archive` | POST | Yes | Move to archive |
| `/retrieve-from-archive` | POST | Yes | Retrieve from archive |
| `/validation-logs` | GET | Yes | List validation logs |
| `/validation-log/<file>` | GET | Yes | View specific log |

## 6. Chained AI+DB Sync

### 6.1 Architecture

The Dashboard uses `run_ai_and_sync.sh` to chain extraction and DB sync:

```
Dashboard â”€â”€â–¶ run_ai_and_sync.sh â”€â”€â–¶ API_interface_XX_ver00.py
                                   â”€â”€â–¶ updating_process_database.py
```

### 6.2 Log Output

Both AI processing and DB sync output merge into a single log file at `Dashboard/logs/<script>.<timestamp>.log`.

### 6.3 Log Summarizer

The Dashboard includes an enhanced log summarizer that:
- Parses `Successfully processed and saved asset QR: <id> (Completeness: XX% | Avg Conf: YY%)`
- Looks up QR metadata from DB for building and asset type
- Groups processing into summary blocks

## 7. Parameter Update Service

### 7.1 Architecture

`utils/parameter_update_service.py` provides:
- `execute_parameter_update(db_path, upload_dir, qr_code, old_params, new_params)` â†’ `(success, message)`
- `get_current_params(conn, qr, upload_dir)` â†’ `dict`
- `get_current_asset_type(upload_dir, qr)` â†’ `str`
- `detect_parameter_changes(old_params, new_params)` â†’ `dict`

### 7.2 Update Sequence

1. Validate target parameters and check for collisions
2. Rename image files
3. Rename JSON files and update interior fields
4. Update DB rows within transaction
5. Commit on success, roll back on failure

## 8. FLS Asset Management

### 8.1 `new_device` Table

Planon checklist columns are auto-migrated at startup:
- `Request Open` (INTEGER DEFAULT 0)
- `Request Date` (TEXT)
- `Elapsed Time` (INTEGER DEFAULT 0)
- `Complete` (INTEGER DEFAULT 0)
- `Ticket Number` (TEXT)

FLS `Attribute Set` defaults and normalizes to `FireAlarmDevice`. A populated `Planon Code` blocks delete and bulk selection but still allows Edit for metadata corrections.

### 8.2 Dashboard CRUD

| Operation | Route | Description |
|---|---|---|
| Add/Edit | `/add-fls-assets` | Insert or update an FLS asset record |
| Delete | `/delete-fls-assets` | Remove FLS asset record; Planon-coded rows are blocked |
| Bulk Update | `/bulk-update-assets` | Update multiple FLS asset fields; Planon-coded rows are not selectable |

Control Panel Code/Description in the FLS UI is derived from `"UBC - Asset Data Master Info"` and remains outside the `new_device` schema.
| Update Field | `/update-fls-asset-field` | Update single field |

## 9. Dictionary Management

### 9.1 Architecture

The Dashboard reads and writes `dictionary/mechanical_dictionary.py`:

**Read**: `ast.parse()` extracts `ASSET_DICTIONARY` assignment â†’ `ast.literal_eval()` evaluates safely.

**Write**: `json.dumps(sorted_data, indent=4)` produces deterministic output written as `ASSET_DICTIONARY = <json>`.

### 9.2 Safety Contract

- Never use `eval()`
- Never execute dictionary file contents
- Always preserve valid Python syntax
- Sorted output for deterministic diffs

## 10. Planon Export

### 10.1 Export Functions

| Function | Description |
|---|---|
| `export_to_planon()` | Generate Planon-formatted export |
| `parse_ubc_tag_info(tag)` | Parse UBC tag structure into components |
| `format_year_to_date(year_str)` | Convert year to Planon date format |

### 10.2 Validation

- Validation logs generated in `SDI_process/sdi_json_output/`
- Accessible via `/validation-logs` and `/validation-log/<filename>`

## 11. Operational Integrity

### 11.1 Key Constraints

- Placeholder QR IDs (`None`, `nan`, blank) must be rejected from all joins
- SDI enrichment must use normalized string keys, not numeric coercion
- Chart modules should return valid empty states, not broken responses
- Manual Entry requires aligned state: `Col_process = 2`, JSON `ExcludeSDI`, `QR_codes.sdi = 1`
- `QR_code_assets` audit: `user` and `date_hour` recorded on capture

### 11.2 File Naming Convention

- Images: `<QR> <Building> <Type> - <Seq>.jpg`
- JSON: `<QR>_<TYPE>_<Building>.json`
- Elapsed time: `<QR>_et.json`
- Logs: `<script_stem>.<timestamp>.log`
