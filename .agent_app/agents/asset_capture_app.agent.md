# Capture App Agent â€” AI Assistant Instructions

Current documentation refresh: 2026-05-25.

## Application Identity

The **Asset Capture App** is a mobile-first Flask web application (port **5001**) used by UBC Facilities field technicians to scan QR code asset tags, photograph nameplate data, and submit captures to the server for AI-driven extraction processing.

**Production URL**: `https://capture.assetcap.facilities.ubc.ca`
**Local URL**: `http://127.0.0.1:5001`

---

## Architecture Overview

```
asset_capture_app_dev/
â”œâ”€â”€ app.py                          # Flask backend (963 lines, 30+ functions)
â”œâ”€â”€ auth_routes.py                  # Authentication blueprint (login, logout, change password)
â”œâ”€â”€ templates/
â”‚   â”œâ”€â”€ base.html                   # Base template (CSS links, shared head)
â”‚   â”œâ”€â”€ start.html                  # Landing page â€” QR scan, building/location/type selection
â”‚   â”œâ”€â”€ capture.html                # Camera interface â€” photo capture + gallery
â”‚   â”œâ”€â”€ success.html                # Post-submission confirmation
â”‚   â”œâ”€â”€ index.html                  # Root redirect / welcome
â”‚   â”œâ”€â”€ login.html                  # Authentication form
â”‚   â”œâ”€â”€ change_password.html        # Password management
â”‚   â””â”€â”€ logo.html                   # UBC logo embed partial
â”œâ”€â”€ static/
â”‚   â”œâ”€â”€ css/
â”‚   â”‚   â”œâ”€â”€ styles.css              # Main stylesheet (utility-class approach)
â”‚   â”‚   â”œâ”€â”€ ui-components.css       # Additional component styles
â”‚   â”‚   â””â”€â”€ logo.css                # Logo-specific styles
â”‚   â”œâ”€â”€ js/
â”‚   â”‚   â””â”€â”€ start.js                # Start page JavaScript (35K, QR scanning, form logic)
â”‚   â”œâ”€â”€ img/                        # Static images
â”‚   â””â”€â”€ logos/                      # UBC branding assets
â”œâ”€â”€ utils/
â”‚   â””â”€â”€ parameter_update_service.py # Atomic parameter update engine (905 lines)
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ qr_code_db                 # PostgreSQL operational DB (via db.py); QR_codes.db is rollback only (8 MB, 19 tables)
â”‚   â”œâ”€â”€ User_control.db             # Authentication database
â”‚   â”œâ”€â”€ debug_asset.py              # Asset debugging utility
â”‚   â”œâ”€â”€ processed_images*.log       # Image processing tracking logs
â”‚   â”œâ”€â”€ processed_json*.log         # JSON processing tracking logs
â”‚   â””â”€â”€ sqlite_checkpoint.sh        # WAL checkpoint script
â”œâ”€â”€ logs/                           # Runtime log directory
â”œâ”€â”€ requirements.txt                # Python dependencies
â””â”€â”€ venv/                           # Python virtual environment
```

---

## Route Catalog

### Authentication (`auth` Blueprint â€” prefix `/auth`)

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET/POST | `/auth/login` | `login()` | Login form + credential validation |
| GET | `/auth/logout` | `logout()` | End session, redirect to login |
| GET/POST | `/auth/change-password` | `change_password()` | Password update form |

### Main App Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/` | `start()` | Landing page â€” QR scan, building/location/asset type selection |
| GET/POST | `/capture` | `capture()` | Camera interface â€” photo capture and gallery |
| POST | `/submit` | `submit()` | Process and save photos + DB records |
| GET | `/success` | `submit_success()` | Post-submission confirmation |
| GET | `/health` | `health()` | Health check endpoint |

### API Endpoints

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/api/locations` | `api_locations()` | Building locations lookup (filterable by building code) |
| GET | `/api/check-qr` | `api_check_qr()` | Check if QR code exists, return existing data |
| POST | `/api/update-parameters` | `api_update_parameters()` | Atomic parameter update (building/location/asset type) |
| GET | `/api/get-temp-code` | `api_get_temp_code()` | Generate temporary QR code from `temp_code` pool |
| GET | `/uploads/<filename>` | `uploaded_file()` | Serve uploaded photo files |

### Route to Function Pipeline

| POST `/submit` | Delete old files â†’ Delete old DB rows â†’ Save new photos â†’ Insert new DB rows â†’ Update QR_codes (incl. optional `capture_notes` / `installation_date`, added 2026-07-06) â†’ Write elapsed-time JSON |

---

## Data Layer

### Primary Database
**Operational DB**: PostgreSQL `qr_code_db` through `db.py`; `data/QR_codes.db` is rollback/reference only

Key tables used by the capture app:
| Table | Purpose |
|-------|---------|
| `QR_codes` | Master QR code registry (PK: `QR_code_ID`, stores location, building, asset type, timestamps) |
| `QR_code_assets` | Photo file records per QR (PK: `ID`, unique on `code_assets`, tracks user and timestamp) |
| `Buildings` | Building code â†’ name lookup (294 buildings) |
| `SpaceUID` | Space/floor/room lookup by building code (85K+ rows) |
| `sdi_dataset` | AI extraction results for Mechanical/Backflow assets |
| `sdi_dataset_EL` | AI extraction results for Electrical assets |
| `temp_code` | Pool of 20K pre-generated temporary QR codes |

> Full schema documentation: `.agent/QR_codes_db_schema.md`

### Authentication Database
**SQLite**: `data/User_control.db` â€” managed by shared `auth_service` module

### Photo Storage
**Directory**: `/home/developer/Capture_photos_upload/` (production) or local `static/uploads/`

Filename pattern: `<QR_code> <Building_Code> <AssetType_Abbrev> - <Sequence>.jpg`
Example: `0000177276 314-1 ME - 0.jpg`

Sequence ranges per discipline (`-N` suffix in the filename):

| Discipline | Sequences | Tile Labels |
|---|---|---|
| Mechanical (ME) | `-0` .. `-4` | Asset Plate / UBC Tag / Main Asset Photo / Technical Safety BC / **Extra Photo** (`-4`, optional) |
| Backflow (BF) | `-0` .. `-3` | Asset Plate / Asset Plate (additional) / Main Photo / **Extra Photo** (`-3`, optional) |
| Electrical (EL) | `-0` .. `-3` | Asset Plate (Optional) / UBC Asset Tag / Panel Schedule / **Extra Photo** (`-3`, optional) |

The **Extra Photo** slot is captured/displayed but excluded from completeness, AI confidence, AI extraction (`VALID_SUFFIXES`), and the "Missed Photo" count. In review dashboards it surfaces as a `+1` chip in the Photo column.

`save_image_file()` applies `PIL.ImageOps.exif_transpose()` before writing, so phone photos with EXIF Orientation are physically rotated to portrait on disk. Historical files captured before 2026-05-25 were not backfilled.

### JSON Elapsed-Time Output
**Directory**: `/home/developer/Output_jason_api/`

Writes `<QR>_et.json` after every submission. Payload keys: `qr_code`, `building_number`, `asset_type`, `elapsetime`, plus (since 2026-07-06) the optional capture fields `capture_notes` and `installation_date` (always present, empty string when the tech left them blank; the file reflects the latest submission).

---

## Core Modules

### `parameter_update_service.py` (905 lines)
Handles atomic updates when a user changes Building, Location, or Asset Type during a repeat photo capture:
1. **Detect changes** â€” compare old vs. new parameters
2. **Backup files** â€” create temp copies of existing photos
3. **Rename files** â€” update filename components atomically
4. **Update QR_codes table** â€” building code, location, asset type
5. **Update QR_code_assets table** â€” file record references
6. **Update sdi_dataset/sdi_dataset_EL** â€” if extraction data exists
7. **Update JSON files** â€” rename and edit JSON output files
8. **Rollback on failure** â€” restore backups if any step fails

### Legacy Access-DB Utils (removed 2026-06-09)
`file_handler.py` (upload handler) and `building_lookup.py` (building lookup) targeted the original Microsoft Access `.accdb` via pyodbc. Never imported by the live app and superseded by the SQLite logic in `app.py`, they were deleted as dead code after the 2026-06-09 PG-identifier audit (recoverable from git history).

---

## External Integrations

| Integration | Usage |
|-------------|-------|
| **HTML5 MediaDevices API** | Camera access via `navigator.mediaDevices.getUserMedia()` |
| **ImageCapture API** | High-quality photo capture (with canvas fallback) |
| **Flask-Login** | Session management and route protection |
| **Flask-SQLAlchemy** | ORM for auth database (User model with bcrypt) |
| **Pillow (required path)** | JPEG re-encode in `save_image_file()`; applies `ImageOps.exif_transpose()` for orientation correctness; EXIF metadata is dropped on re-encode because the pixels themselves now carry the correct orientation |
| **python-dotenv** | Environment variable loading from `.env` files |

---

## Key Conventions

1. **Mobile-first design**: All UI targets touch devices â€” minimum 44px tap targets, handle orientation changes
2. **HTTPS required**: Camera access requires HTTPS or `localhost` â€” never test over plain HTTP on remote devices
3. **Photos are server-named**: Filenames are computed server-side by `app.py`, not from the client-provided name
4. **Atomic parameter updates**: The `parameter_update_service.py` handles file renames + DB updates as a single transaction with rollback
5. **Dual-OS paths**: Code handles both Linux (`/home/developer/...`) and Windows paths via `os.getenv()` fallbacks
6. **Camera zoom**: Implemented via rotated range input in `capture.html` with cycle-through text control
7. **QR validation**: QR codes are validated to be exactly 10 digits; temporary codes use `TEMP-XXXXX` format
