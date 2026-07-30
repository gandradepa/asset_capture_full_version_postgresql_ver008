# UBC Asset Capture Application â€“ Server Documentation

Current documentation refresh: 2026-05-05.

## 1. Overview

The UBC Asset Capture Application is an end-to-end platform for capturing, extracting, reviewing, and packaging asset data for downstream Planon CMMS integration. The platform operates as a distributed monolith with shared PostgreSQL `qr_code_db` operational state (VM `127.0.0.1:5433`; legacy SQLite `QR_codes.db` is the frozen rollback) and filesystem stores.

## 2. System Architecture

### 2.1 Component Overview

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Capture App â”‚â”€â”€â”€â”€â–¶â”‚ Extraction   â”‚â”€â”€â”€â”€â–¶â”‚ Review Apps  â”‚
â”‚  (Port 5001) â”‚     â”‚ API (Batch)  â”‚     â”‚ (ME/BF/EL)   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚                    â”‚                     â”‚
       â–¼                    â–¼                     â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚              qr_code_db (PostgreSQL; operational)     â”‚
â”‚   QR_codes â”‚ QR_code_assets â”‚ json_files â”‚ sdi_*      â”‚
â”‚   new_device â”‚ Buildings_with_SpaceUID â”‚ temp_code    â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚                    â”‚                     â”‚
       â–¼                    â–¼                     â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Dashboard   â”‚     â”‚ SDI Process  â”‚     â”‚  Auth Service â”‚
â”‚  (Port 8002) â”‚     â”‚  (Port 5005) â”‚     â”‚   (Shared)    â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### 2.2 Data Flow

1. **Capture** â†’ Raw photos saved to `Capture_photos_upload/`, QR metadata to DB
2. **Extract** â†’ AI processes photos into JSON in `Output_jason_api/`, syncs to DB
3. **Review** â†’ Human correction and approval, curated rows synced to SDI tables
4. **Package** â†’ SDI Process packages approved rows, exports to Planon format
5. **Monitor** â†’ Dashboard provides analytics, FLS management, dictionary editing

### 2.3 Shared State Model

- `qr_code_db` (PostgreSQL, VM `127.0.0.1:5433`) â€” single operational database; legacy SQLite `QR_codes.db` is the frozen rollback
- `Capture_photos_upload/` â€” Raw captured images
- `Output_jason_api/` â€” Extracted JSON payloads and elapsed-time artifacts
- `User_control.db` â€” Authentication and user management
- `dictionary/mechanical_dictionary.py` â€” Classification taxonomy

## 3. Service Details

### 3.1 Capture App

**Port**: 5001 | **Entry**: `asset_capture_app_dev/app.py` (999 lines)

**Features**:
- Mobile-first photo capture with HTML5 Camera API
- QR code scanning and validation
- Building/location selection from `Buildings_with_SpaceUID` table
- Elapsed-time tracking and JSON artifact creation
- User and timestamp audit in `QR_code_assets`
- Parameter update service (`utils/parameter_update_service.py`) for atomic rename
- Temporary QR code management

**Key API Endpoints**:
| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Start page with building/location selection |
| `/capture` | GET/POST | Photo capture interface |
| `/submit` | POST | Submit captured photos |
| `/api/check-qr` | GET | Check QR existence, return current parameters |
| `/api/update-parameters` | POST | Atomic parameter update |
| `/api/locations` | GET | Location list for building |
| `/api/get-temp-code` | POST | Get next temporary QR code |

### 3.2 Dashboard

**Port**: 8002 | **Entry**: `Dashboard/Asset_portal_dashboard.py` (2,616 lines)

**Features**:
- Operational status and AI process queue monitoring
- AI Process Queue → System Logs → **SLD Extraction Runs** table:
  - Lists every SLD AI extraction with status (Success / Error / Timeout / Running), duration, asset count, model-call count, building, source PDF, run_id
  - Drilldown page (`/sld-logs/runs/<run_id>`) shows the run summary, the rows that were inserted into `electrical_building_schema`, and per-`model_call` telemetry (model, latency, ok/error)
  - "Open in EL Reviewer" deep-links into the embedded EL iframe with the run's building pre-selected (via `?building=<code>`)
  - "Re-run extraction" (admin-only) calls a server-side reverse-proxy endpoint that POSTs to the EL Reviewer's `/sld/api/rerun/<run_id>` over loopback, forwarding the user's session cookies — no CORS, no new dependency
- Chart modules:
  - Approval charts
  - Completeness score charts
  - AI confidence charts
  - Data Quality Comparison (merged chart)
  - Operational Performance Analysis
  - FLS charts (Altair-based)
  - Map chart (assets by building)
  - SDI flow chart (flow quantity)
- Chained AI+DB sync launchers (`run_ai_and_sync.sh`)
- FLS asset management (add, delete, bulk update against `new_device` table, plus display-only Control Panel lookup from `"UBC - Asset Data Master Info"`)
- Dictionary management UI (AST-safe read/write)
- Photo viewing API
- Log viewer and summarizer

**Chart Module Availability Flags**:
| Flag | Module | Dependency |
|---|---|---|
| `CHARTS_AVAILABLE` | `charts.approval` | â€” |
| `AI_STATUS_AVAILABLE` | `charts.ai_status_table_new_version` | â€” |
| `COMPLETENESS_CHART_AVAILABLE` | `charts.completeness_score` | â€” |
| `AI_CONFIDENCE_CHART_AVAILABLE` | `charts.ai_confidence_score` | â€” |
| `DATA_QUALITY_CHART_AVAILABLE` | `charts.data_quality_comparison` | â€” |
| `OPERATIONAL_COST_CHART_AVAILABLE` | `charts.operational_cost_result` | â€” |
| `FLS_CHARTS_AVAILABLE` | `charts.fls_chart` | `altair` |
| `MAP_CHART_AVAILABLE` | `charts.map_chart` | â€” |
| `FLOW_CHART_AVAILABLE` | `charts.flow_quantity_chart` | â€” |

### 3.3 Review Apps

**ME Port**: 5002 | **BF Port**: 5003 | **EL Port**: 5004

**Features**:
- JSON + photo review for human correction
- Curated classification field preservation during save and approval
- Confidence slicer filtering
- Manual Entry (SDI exclusion) state management
- Approval sync to SDI dataset tables

### 3.4 SDI Process

**Port**: 5005 | **Entry**: `SDI_process/app.py` (1,276 lines)

**Features**:
- Unpackaged asset loading from curated SDI tables
- Building-scoped filtering
- SDI package creation with auto-incrementing package IDs
- Planon export with UBC tag parsing and year formatting
- Validation log generation and viewer
- Archive management (move, retrieve, exclude)
- Export to Excel/CSV

**Key Functions**:
| Function | Description |
|---|---|
| `export_to_sdi()` | Generate SDI package |
| `export_to_planon()` | Generate Planon-formatted export |
| `move_to_archive()` | Transfer packages to archive |
| `retrieve_from_archive()` | Retrieve packages from archive |
| `exclude_package()` | Exclude individual packages |
| `parse_ubc_tag_info()` | Parse UBC tag structure |
| `format_year_to_date()` | Format year values for Planon |
| `get_validation_logs()` | List validation logs |

### 3.5 Extraction API

**Entry**: `API/API_interface_ME_ver00.py`, `API_interface_BF_ver00.py`, `API_interface_EL_ver00.py`

**Features**:
- OCR + LLM-based nameplate data extraction
- Discipline-specific completeness and confidence rules
- Shared validators (`validators_shared.py`)
- Chained execution via `run_ai_and_sync.sh` (AI â†’ DB sync)
- JSON-to-DB synchronization via `updating_process_database.py`

### 3.6 Auth Service

**Entry**: `auth_service/`

**Features**:
- Flask-Login + bcrypt authentication
- Shared across all services via `sys.path` injection
- User management via `User_control.db`
- Session cookie domain sharing

## 4. Database Reference

### 4.1 Table Summary

| Table | Description |
|---|---|
| `QR_codes` | QR-level state: approval, AI status, SDI exclusion, location |
| `QR_code_assets` | Per-photo records with process placement, `user`, `date_hour` |
| `json_files` | JSON sync/summary table |
| `sdi_dataset` | Curated approved ME/BF rows |
| `sdi_dataset_EL` | Curated approved EL rows |
| `sdi_print_out` | Active SDI packages |
| `sdi_print_out_arch` | Archived SDI packages |
| `new_device` | FLS assets with Planon checklist columns |
| `UBC - Asset Data Master Info` | FLS Control Panel Code/Description lookup by building property code |
| `Buildings_with_SpaceUID` | Building/location lookup |
| `temp_code` | Temporary QR code pool |
| `Buildings` | Building master data |

### 4.2 Key Integrity Constraints

- QR identity must be consistent across images, JSON, and DB
- `Col_process = 2` means Manual Entry / SDI exclusion
- `QR_codes.sdi = 1` means excluded from SDI packaging
- SDI joins must use normalized string keys, not numeric coercion
- Placeholder QR IDs (`None`, `nan`, blank) must be rejected

## 5. Discipline-Specific Rules

### 5.1 Completeness Rules

| Discipline | Required Fields |
|---|---|
| ME | Manufacturer, Model, Serial Number, Year, UBC Tag (+ Technical Safety BC when seq -3 exists) |
| BF | Manufacturer, Model, Serial Number, Diameter |
| EL | UBC Asset Tag, Ampere, Supply From |

EL note: extractor and review payload completeness still uses `Ampere`, while curated `sdi_dataset_EL` rows now store canonical amperage in `Amperage Rating` and mirror it to `Ampere` for compatibility.

### 5.2 AI Confidence Rules

| Discipline | Exclusions |
|---|---|
| ME | Include Technical Safety BC only when seq -3 exists |
| EL | Exclude Volts, Location, Branch Panel |
| BF | No exclusions |

## 6. Operational Monitoring

### 6.1 Dashboard Analytics

- **Data Quality Comparison**: Merged chart comparing completeness and AI confidence by asset type
- **Scope Toggle**: `All` (includes approved) vs `Open Process` (excludes approved)
- **Building Filter**: Scopes all analytics to selected building

### 6.2 FLS Asset Management

- `new_device` table Planon checklist columns: `Request Open`, `Request Date`, `Elapsed Time`, `Complete`, `Ticket Number`
- Auto-migrated at startup via `_ensure_new_device_columns()`
- FLS `Attribute Set` defaults to `FireAlarmDevice`; rows with `Planon Code` remain editable but are locked against delete and bulk selection.
- FLS Control Panel values are derived display fields; the first lowest-Code match is shown and multi-match properties are flagged.
- Dashboard CRUD: add, delete, bulk update

### 6.3 Dictionary Management

- AST-safe editing from Dashboard UI
- Read: `ast.parse()` + `ast.literal_eval()`
- Write: `json.dumps()` sorted
- Target: `dictionary/mechanical_dictionary.py`
