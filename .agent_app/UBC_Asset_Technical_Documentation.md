# UBC Asset Technical Documentation

Current documentation refresh: 2026-08-03.

## Repository Architecture

### Data Sources

- `Capture_photos_upload/` for raw image capture
- `Output_jason_api/` for extracted and reviewed JSON payloads, plus elapsed-time JSON artifacts
- PostgreSQL `qr_code_db` (VM `127.0.0.1:5433`, via `db.py` / `DB_BACKEND=postgres`) for operational workflow state; legacy SQLite `asset_capture_app_dev/data/QR_codes.db` is the frozen rollback
- `auth_service` user data in `User_control.db` (SQLite, unchanged by the cutover)
- `dictionary/mechanical_dictionary.py` for AST-safe dictionary editing

### Main Services

- `asset_capture_app_dev/` capture app
- `API/` extraction workers and DB sync
- `review/` ME/BF/EL review apps
- `Dashboard/` operational analytics and process views
- `SDI_process/` packaging and validation
- `dictionary/` classification lookup sources
- `auth_service/` user authentication

## Core Technical Contracts

### QR Identity Contract

A valid QR code must remain consistent across:

- image filenames
- JSON filenames
- `QR_codes.QR_code_ID`
- `QR_code_assets.code_assets`
- curated dataset rows
- SDI package rows where applicable

### Completeness Contract

- ME: conditional 5 or 6 fields depending on seq `-3`
- BF: 4 fields
- EL: 3 fields

### Confidence Contract

- blank final fields must not retain stale non-zero confidence
- `Avg_ai_conf` is discipline-aware
- EL excludes `Volts`, `Location`, and `Branch Panel`
- ME includes `Technical Safety BC` only when seq `-3` exists
- A challenged ME sequence `-1` UBC tag is resolved independently by primary extraction, local OCR, and at most one Terra judge. A fully resolved quorum is at least 92 confidence; unresolved consensus preserves the primary tag, caps it at 65, and records manual-review metadata.

### Review / SDI Contract

- save and approval sync curated rows into SDI dataset tables
- Manual Entry means SDI exclusion, not only alternate review placement
- SDI unpackaged rows should be built from approved curated rows plus QR-level exclusion state

## Operational Integrity Notes

- placeholder QR IDs must be blocked from joins and staging views
- SDI enrichment must use normalized string keys, not numeric coercion
- chart modules should return valid image empty states instead of broken responses
- doc mirrors in `.agent_app/` must be refreshed after canonical doc changes

## Key DB Tables

- `QR_codes`
- `QR_code_assets` (with `user` and `date_hour` audit columns)
- `json_files`
- `sdi_dataset`
- `sdi_dataset_EL`
- `sdi_print_out`
- `sdi_print_out_arch`
- `new_device` (FLS asset tracking with Planon checklist columns)
- `UBC - Asset Data Master Info` (FLS Control Panel Code/Description display lookup by `Property code`)
- `Buildings_with_SpaceUID` (building and location lookup)

## New Feature Contracts

### Chained AI+DB Sync

- `run_ai_and_sync.sh` chains AI extraction â†’ DB sync automatically
- Manual `update_db` task removed from Dashboard launcher

### FLS Asset Management

- CRUD operations via Dashboard against `new_device` table
- Planon checklist columns: `Request Open`, `Request Date`, `Elapsed Time`, `Complete`, `Ticket Number`
- `Attribute Set` defaults to `FireAlarmDevice`; a populated `Planon Code` blocks delete and bulk selection while Edit remains allowed
- Schema auto-migrated at startup
- New FLS Device Flow derives Control Panel Code/Description from `"UBC - Asset Data Master Info"` by selected building property code; multiple matches show the lowest Code and a warning flag

### Dictionary Management

- AST-safe read/write from Dashboard UI
- Targets `dictionary/mechanical_dictionary.py`

### Parameter Update Service

- Atomic parameter changes via `utils/parameter_update_service.py`
- Updates filenames, JSON, DB rows atomically with rollback on failure

### Planon Export

- UBC tag parsing via `parse_ubc_tag_info()`
- Year formatting via `format_year_to_date()`
- Validation logs in `SDI_process/sdi_json_output/`

## Maintenance Guidance

- update root docs for platform-wide rules
- update service-local `.agent` docs for subsystem-specific behavior
- update `.agent_app` mirrors after canonical doc changes
- verify the checklist in `DOC_REFRESH_CHECKLIST.md` after major rule changes
- when adding new chart modules, register availability flags and provide graceful fallback
