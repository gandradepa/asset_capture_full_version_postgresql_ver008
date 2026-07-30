# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Repository Overview

This is the **UBC Facilities Asset Capture workflow platform**, a distributed monolith composed of multiple Flask applications and worker scripts that share critical state: one operational PostgreSQL database (`qr_code_db`), a captured image store (`Capture_photos_upload/`), and JSON extraction/review payloads (`Output_jason_api/`).

The platform supports the end-to-end lifecycle of facilities asset records: QR-based field capture, image upload, OCR/AI extraction of nameplate data (via OpenAI vision models), human review and correction, dashboard analytics, SDI (Standard Data Interchange) packaging, and Planon export. It is used operationally by UBC Facilities to manage Mechanical (ME), Electrical (EL), and Backflow (BF) assets across the campus building portfolio.

## Core Workflow

```
Capture App → Extraction API → Review Apps (ME/BF/EL) → SDI Process → Planon Export
                                      ↑
                                  Dashboard (operational control plane)
```

**Critical invariant:** Each discipline (ME, BF, EL) has its own completeness rules, confidence calculations, review UI, and SDI table targets. Code that treats them as interchangeable will break the platform.

## Architecture & Shared State

### Runtime Modules

| Module | Entry Point | Local Port | Prod Port |
|--------|-------------|-----------|-----------|
| Capture App | `asset_capture_app_dev/app.py` | 5001 | 8000 |
| ME Review | `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` | 5002 | 8001 |
| BF Review | `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` | 5004 | 8004 |
| EL Review | `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` | 8005 | 8005 |
| Dashboard | `Dashboard/Asset_portal_dashboard.py` | 8002 | 8002 |
| SDI Process | `SDI_process/app.py` | 8003 | 8003 |

**Note:** Folder spellings `Asset_dasboard_browser_ME` and `Asset_dasboard_browser_BF` (with "dasboard") are intentional historical names. EL uses the correctly-spelled `Asset_dashboard_browser_EL`. Preserve both spellings verbatim.

### Shared Data Stores

- **Operational DB:** PostgreSQL `qr_code_db` (VM `127.0.0.1:5433`, via `db.py` and `DB_BACKEND=postgres` in `/home/developer/db_backend.env`). The legacy SQLite `asset_capture_app_dev/data/QR_codes.db` is a frozen rollback/reference file only and is no longer part of live QR-code processes.
  - `QR_codes` — QR-level state: approval, AI status, SDI exclusion, location
  - `QR_code_assets` — per-asset process state, image relationships, tab placement, audit fields
  - `sdi_dataset` — curated records for ME/BF
  - `sdi_dataset_EL` — curated records for EL (canonical Planon-facing fields)
  - `sdi_print_out` / `sdi_print_out_arch` — active/archived SDI packages
  - `json_files` — JSON sync bookkeeping
  - `new_device` — FLS (Facilities Lifecycle System) asset tracking
  - `Buildings_with_SpaceUID` — location/SpaceUID lookup
  - `audit_trail` — human and system change logs
  - `electrical_building_schema` — SLD (Single Line Diagram) asset records (EL only)

- **Auth DB:** `auth_service/users.db` (shared across all apps)
- **Captured Images:** `Capture_photos_upload/`
- **Extracted/Review JSON:** `Output_jason_api/` (note: intentional "jason" spelling, do not rename)
- **SDI Validation Output:** `SDI_process/sdi_json_output/`

### Shared Authentication

All apps load auth from `/home/developer/auth_service.env`:

```
SECRET_KEY=<shared-long-random-key>
DATABASE_URI=sqlite:////path/to/users.db
SESSION_COOKIE_DOMAIN=.assetcap.facilities.ubc.ca
```

Session and remember cookies use `SameSite=None; Secure` for cross-subdomain sharing in production.

## Key Tech Stack

- **Framework:** Flask 2.0+
- **Database:** PostgreSQL primary (`qr_code_db`) via the shared backend-agnostic `db.py` layer; SQLite remains only for auth-service storage and the frozen QR rollback/reference file.
- **Auth:** Flask-Login, bcrypt, custom RBAC via `UserAccess`
- **AI/ML:** OpenAI vision models, pytesseract OCR fallback
- **Image Processing:** OpenCV headless, Pillow, NumPy
- **Data/Export:** Pandas, openpyxl (XLSX for Planon)
- **Frontend:** Bootstrap, DataTables, Altair charts, custom image-viewer JS
- **Production:** Gunicorn + Nginx reverse proxy on Ubuntu 22.04 LTS with systemd

## High-Risk Invariants

**Do not violate these without explicit task scope:**

1. **Parameterized SQL only.** No string-formatted queries.
2. **Discipline isolation:** ME, BF, EL do NOT share completeness, confidence, review, or SDI rules.
3. **Review approval sync:** ME/BF → `sdi_dataset`; EL → `sdi_dataset_EL`. Mistakes silently break Planon export.
4. **Manual Entry alignment:** `QR_code_assets.Col_process = 2`, JSON `ExcludeSDI`, and `QR_codes.sdi = 1` must stay consistent.
5. **Atomicity:** QR rename, parameter update, image rename, and DB updates must be atomic. Never leave system in split state.
6. **Do not erase human overrides:** Re-running AI must not overwrite reviewed values. Check `Col_process` and `manual_override` flags.
7. **AST-safe dictionary editing:** Use `ast.parse()` / `ast.literal_eval()` for reads; `json.dumps()` for writes. Never `eval()`.
8. **Chained AI+DB sync:** Use `API/run_ai_and_sync.sh`. The standalone DB-sync launcher has been removed.
9. **Dashboard is read-only.** Trust curated DB tables and JSON files, not chart views.
10. **Environment separation:** Local paths differ from Ubuntu production. Respect both conventions.

## Discipline-Specific Rules

### Mechanical (ME)
- Sequence `-0` owns: Manufacturer, Model, Serial Number, Year
- Sequence `-1` owns: UBC Tag
- Sequence `-3` owns: Technical Safety BC
- Sequence `-4` is the optional **Extra Photo** slot — captured/displayed but excluded from completeness, AI confidence, AI extraction (`VALID_SUFFIXES`), and "Missed Photo"
- Do not borrow values across sequences.

### Backflow (BF)
- Uses `sdi_dataset` table
- Core completeness: Manufacturer, Model, Serial Number, Diameter
- Sequence `-3` is the optional **Extra Photo** slot — captured/displayed but excluded from completeness, AI extraction (`VALID_SUFFIXES`), and "Missed Photo"

### Electrical (EL)
- Uses `sdi_dataset_EL` table
- Canonical Planon-facing fields: Amperage Rating, Voltage Rating, Equipment ID, Equipment Type, Fed From Equipment ID, Power Type
- **Rating storage convention (2026-07-08/09):** rating values are stored bare (`208/120`, `100`); the unit lives in the `(UoM)` columns — `VLT`/`AMP` are the intentional Planon UoM codes on the SDI tables (do not "fix" them), while `electrical_building_schema` stores display units `V`/`A`. Write paths strip unit letters from values; display layers add the units (`withRatingUnit()` in sld.js, `_sld_rating_text()` in the review report).
- Confidence averages exclude `Volts`, `Location`, `Branch Panel`
- Sequence `-3` is the optional **Extra Photo** slot — captured/displayed but excluded from completeness, AI extraction (`VALID_SUFFIXES`), and "Missed Photo"
- **SLD (Single Line Diagram):** Diagram-side `electrical_building_schema` vs captured `sdi_dataset_EL` with "Swift Over" inline editor
- **`ID_check` is GENERATED ALWAYS AS (...) STORED** on both tables (read-only to code; PostgreSQL has no `VIRTUAL` generated columns)
- **Reconciliation:** `POST /sld/api/assets/<row_id>/reconcile` with `{choice: "sld"|"sdi"|"custom", value?, reason?}`. Atomic dual-write with audit trail.

## Dashboard as Unified Shell

The Dashboard embeds ME, BF, EL, and SDI sub-apps in iframe panels with hash-based navigation (`#review-me-view`, etc.).

- All apps detect `?embedded=true` and suppress own navbar via `before_request` hook setting `g.embedded=True`
- Internal links propagate `?embedded=true` via click interceptor
- Cross-frame navigation: `window.parent.postMessage({action:'go-to-main'}, 'https://dashboardprod.assetcap.facilities.ubc.ca')`
- **Bulk actions (BF+EL):** Master checkboxes drive client-side queue calling per-row endpoints. No dedicated bulk endpoint. Safety filters prevent illegal state transitions.
- **ME:** Endpoints exist; UI not yet implemented.

## Review App Image Viewer

ME, BF, EL share mouse-friendly image viewer. Reusable controller: `review_asset_templates/static/image-viewer.js` in each app (keep three copies behaviorally identical).

**Supported:** Mouse wheel zoom, drag pan, double-click detail zoom, keyboard (`+`, `-`, `0`, `R`, arrows), buttons (zoom/rotate), reset button.

## Extraction API & AI Processing

### API Modules

- `API/API_interface_ME_ver00.py` — Mechanical (complex multimodal chains)
- `API/API_interface_BF_ver00.py` — Backflow
- `API/API_interface_EL_ver00.py` — Electrical
- `API/validators_shared.py` — Discipline-agnostic normalization and completeness scoring
- `API/updating_process_database.py` — DB sync after extraction

### Validators (`validators_shared.py`)

Provides: `normalize_year()`, `normalize_manufacturer()`, `normalize_serial()`, `normalize_model()`, `normalize_ubc_tag()`, `normalize_diameter()`, `normalize_ampere()`, `normalize_volts()`, `normalize_supply_from()`, `normalize_el_supply_from_tag()`, `normalize_power_rating_pair()`, `extract_explicit_power_rating_candidates()`, `completeness_score()`, `looks_like_date_misread_serial()` (rejects date-shaped serials incl. upside-down misreads like `8102/90` = `09/2018` rotated; used by ME/BF serial gates)

### Execution

**Always use chained script:**

```bash
./run_ai_and_sync.sh <discipline> <qr_code>
```

This ensures:
1. AI extraction runs (OpenAI vision + OCR fallback)
2. JSON written to `Output_jason_api/`
3. DB sync runs (updates `ai_status`, `Col_process`, curated SDI rows)
4. Logs written to `logs/`

**Never run extraction standalone.** DB sync is mandatory.

### Configuration

- **Determinism:** `TEMPERATURE=0.0`, `SEED=42` where supported
- **Force reprocess:** `FORCE_REPROCESS=True` in config bypasses DB `ai_status` check
- **Version:** Currently v30 "The Direct Mapper" (merged explicit JSON key-mapping with table-geometry rules)

## Audit Trail & Change Logging

`audit/` module (sibling package at root) logs all significant changes:

- `audit.logger.log_change(qr_code, table, operation, old_data, new_data, source, description)`
- `audit.diff.diff_dicts(before, after)` for changelog generation
- Every DB write and JSON edit should emit `audit_trail` row with:
  - `source` = `"system"` or `"human"`
  - `description` = operation summary
  - `user` = logged-in username
  - `timestamp` = UTC datetime

## Running Services Locally

### Prerequisites

- Python 3.12+
- Tesseract OCR (`brew install tesseract` / `apt install tesseract-ocr`)
- `.env` file with `OPENAI_API_KEY`

### Start Individual Services

```bash
# Capture App
cd asset_capture_app_dev
source venv/bin/activate
python app.py
# http://localhost:5001

# ME Review
cd review/Asset_dasboard_browser_ME
source venv/bin/activate
python asset_plate_reviewer.py
# http://localhost:5002

# Dashboard
cd Dashboard
source venv/bin/activate
python Asset_portal_dashboard.py
# http://localhost:8002

# SDI Process
cd SDI_process
source venv/bin/activate
python app.py
# http://localhost:8003
```

Each module has its own `venv`. Do not share across modules.

### Install Dependencies

```bash
cd <module-dir>
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

## Common Development Tasks

### Query the Database

```python
import sys
sys.path.insert(0, "asset_capture_app_dev")
import db

conn = db.get_connection()
try:
    cursor = conn.execute("""
        SELECT "QR_code_ID", ai_status, "Approved"
        FROM "QR_codes"
        WHERE ai_status = 1
        LIMIT 10
    """)
    for row in cursor.fetchall():
        print(row)
finally:
    conn.close()
```

### Extract and Test AI

```bash
cd API
export FORCE_REPROCESS=True
python API_interface_ME_ver00.py <qr_code>
```

### Edit Mechanical Dictionary

**Dashboard UI:** Login as Admin → "Dictionary Management" → edit inline
**Direct file:** `dictionary/mechanical_dictionary.py` (AST-safe)

Both use `ast.literal_eval()` for reads and `json.dumps()` for writes.

### Manual DB Sync After Extraction

```bash
cd API
python updating_process_database.py
```

Or use chained script (preferred):

```bash
./run_ai_and_sync.sh ME <qr_code>
```

### View Logs

```bash
tail -f logs/ai_extraction.log
ls /home/developer/sld_extract_feedback/sld_*.jsonl  # SLD runs
# System Logs available in Dashboard UI
```

### Database Migration

Before modifying schema:

1. Read `Markdowns_documentation/01_GLOBAL_RULES.md`
2. Check downstream effects on Dashboard, Review Apps, SDI Process, Planon export
3. Always use parameterized SQL
4. Backup first with PostgreSQL tooling, for example `pg_dump "$QR_PG_DSN" > backup_qr_code_db_$(date +%s).sql` on the VM. Do not use `QR_codes.db` for live QR-code changes.

Example safe migration:

```python
import sys
sys.path.insert(0, "asset_capture_app_dev")
import db

conn = db.get_connection()
cursor = conn.cursor()
try:
    columns = set(db.table_columns(conn, "sdi_dataset"))
    if 'new_field' not in columns:
        # Run DDL only as the PostgreSQL owner role, not from request handlers.
        cursor.execute('ALTER TABLE "sdi_dataset" ADD COLUMN "new_field" TEXT')
        conn.commit()
        print("Added new_field to sdi_dataset")
finally:
    conn.close()
```

## Testing

Minimal formal test coverage. Existing tests:

- `test/test_placeholder_sync_guard.py` — integration test
- `scripts/` — diagnostic/maintenance tools (not tests)

Before submitting changes:

1. Manually test in local dev environment
2. Verify DB consistency: `scripts/audit_trail_health.py`, `scripts/audit_sdi_vs_json.py`
3. Check downstream effects on Dashboard and review apps
4. If modifying AI, test with sample QR and verify JSON + DB sync
5. If modifying SDI, verify Planon export format

## Important Paths & Conventions

- Local dev: Windows or Ubuntu paths; use `os.path.normpath()`
- Production: `/home/developer/` (Ubuntu)
- JSON folder: `Output_jason_api/` (note spelling)
- Logs: `logs/` at project root
- Backups: `*.bak_YYYYMMDD_HHMMSS` convention

## Documentation

**Canonical** in `Markdowns_documentation/`:

- `00_README.md` — Top-level index
- `01_GLOBAL_RULES.md` — Cross-cutting constraints
- `02_SYSTEM_MAP.md` — Runtime modules and data flow
- `03_ARCHITECTURE_MAP.md` — State boundaries and integrations
- `assetcap_setup_manual.md` — Production server setup
- `ubuntu_server_runbook.md` — Production operations

**Service-local** under each module's `.agent`, `.auth_agent`, or `.agent_dictionary` folder.

**When code behavior changes, update matching documentation in the same workstream.** Do not let docs drift.

`.agent_app/` mirrors the root orchestration documentation. Update canonical `Markdowns_documentation/` first, then sync mirrors.

## External System Integration

### Planon Export

SDI Process exports approved assets with:
- UBC tag parsing (`derive_electrical_equipment_type()`, `parse_electrical_equipment_metadata()`)
- Year-to-date formatting
- EL canonical fields: Amperage Rating, Voltage Rating, Equipment ID, Equipment Type, Fed From Equipment ID, Power Type

### OpenAI API

- Used in `API/API_interface_*_ver00.py` for multimodal nameplate extraction
- Deterministic: `temperature=0.0`, `seed=42`
- Tesseract fallback on API failure
- Set `OPENAI_API_KEY` in `.env`

### GPS Service (Experimental)

BF Reviewer has optional GPS service (`review/Asset_dasboard_browser_BF/gps_service.py`).

## Debugging Tips

1. **DB corruption:** `scripts/audit_trail_health.py`
2. **Stuck extraction:** Check `logs/ai_extraction.log` and `Output_jason_api/` for incomplete JSON
3. **Sync failures:** `scripts/audit_sdi_vs_json.py`
4. **Session/auth:** Verify `auth_service.env` readable, `SECRET_KEY` matches, `SESSION_COOKIE_DOMAIN` set
5. **Missing images:** Check `Capture_photos_upload/` filename format: `{QR} {building} {asset_type} - {index}.jpg`
