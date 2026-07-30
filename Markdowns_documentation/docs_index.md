# UBC Asset Capture Documentation

Current documentation refresh: 2026-07-09.

This folder is the maintained Markdown documentation set for the UBC Asset Capture platform. It reflects the current repository layout and the active workflow across capture, AI extraction, review, dashboard operations, SDI packaging, and Planon export.

## Current Platform Snapshot

The project is a workflow-driven Flask platform with shared PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`; SQLite `QR_codes.db` is the frozen rollback) and filesystem state:

- `asset_capture_app_dev/` handles QR intake, photo upload, authenticated capture metadata, elapsed-time JSON, and parameter updates.
- `API/` handles ME, BF, and EL extraction with OCR, OpenAI vision calls, shared validators, JSON output, AI status updates, and database synchronization.
- `review/` contains discipline-specific review apps for ME, BF, and EL correction/approval workflows.
- `Dashboard/` provides the operational dashboard, charting, extraction launchers, logs, dictionary editing, FLS asset CRUD, FLS Control Panel lookup, map views, SDI flow views, and asset-photo API.
- `SDI_process/` creates SDI packages, supports archive/retrieve/exclude operations, validates package output, and exports Planon spreadsheets.
- `dictionary/` stores standalone dictionary tooling and source dictionaries.
- `auth_service/` stores shared login, password hashing, and user-management helpers.

## Canonical Docs

Read these first:

- `00_README.md` - documentation index and responsibility map
- `01_GLOBAL_RULES.md` - platform-wide implementation rules
- `02_SYSTEM_MAP.md` - runnable modules, ports, shared stores, and table usage
- `03_ARCHITECTURE_MAP.md` - architecture, state model, and high-risk integration points
- `assetcap_setup_manual.md` - production setup and maintenance manual
- `ubuntu_server_runbook.md` - production runbook and migration records
- `DOC_REFRESH_CHECKLIST.md` - documentation refresh scope and tracked themes

## Operational Workflow Docs

- `workflows/01_capture_to_json.md`
- `workflows/02_run_extraction_me_el_bf.md`
- `workflows/03_review_and_approve.md`
- `workflows/04_dashboard_ops.md`
- `workflows/05_sdi_packaging_and_planon_export.md`
- `workflows/06_parameter_update_atomic_rename.md`

## Rule Docs

- `rules/asset_capture_app.rules.md`
- `rules/asset_extraction_api.rules.md`
- `rules/review_apps.rules.md`
- `rules/dashboard.rules.md`
- `rules/sdi_process.rules.md`
- `rules/asset_dictionary.rules.md`

## High-Risk Process Docs

- `special_processes/01_atomic_rename_operations.md`
- `special_processes/02_completeness_guard.md`
- `special_processes/03_dictionary_ast_parsing.md`
- `special_processes/04_database_topography.md`
- `special_processes/05_life_cycle_assessment.md`

## Current High-Impact Behaviors

- Operational database migrated from SQLite to PostgreSQL (`qr_code_db`, `127.0.0.1:5433`) in the C4 cutover; apps reach it through the backend-agnostic `db.py` layer (`DB_BACKEND=postgres` in `/home/developer/db_backend.env`). The table/column model is unchanged; the old SQLite `QR_codes.db` is the frozen rollback (flip `DB_BACKEND` back to `sqlite`). Auth (`User_control.db`) stays SQLite (2026-06-08).
- Discipline-specific completeness and confidence scoring are active.
- EL extraction and review use stricter source rules for amperage, voltage, power rating, location, and upstream equipment identifiers.
- EL `Fed From Amperage Rating` (+ UoM) derives from the building's active SLD (`electrical_building_schema`), not sibling captured assets; blank when no SLD exists, and the dashboard required-fields checklist skips both fields for buildings without SLD data (2026-06-12).
- The SLD xlsx export uses a 14-column layout with own-asset and fed-from blocks. The own `Amperage` cell is red-highlighted when it exceeds the feeder rating; no separate flag column is exported. Worksheet gridlines are hidden and the Expand-All diagram is embedded below the table (2026-06-12).
- Dashboard Operational Performance Analysis centers on the combined `Data Quality Comparison` chart.
- FLS asset management uses the `new_device` table with Planon checklist columns.
- FLS Attribute Set defaults to `FireAlarmDevice` for New FLS Device Flow records.
- Planon-coded FLS rows remain editable, while delete and bulk selection stay blocked.
- FLS New Device Flow derives Control Panel Code/Description from `"UBC - Asset Data Master Info"` by building property code and flags multi-match lookups.
- Dashboard dictionary edits use AST-safe parsing and deterministic writes.
- Extraction launchers use `API/run_ai_and_sync.sh` to run AI processing and DB sync together.
- SDI Process excludes `QR_codes.sdi = 1`, prevents duplicate packages, supports archive/retrieve/exclude, and exposes validation logs.
- SDI Retrieve Archives is global and available before building selection, allowing archive-only buildings to be restored; package creation, active archive/exclude, and Planon export remain building-scoped.
- Planon export uses stored canonical EL fields and falls back to compatibility mirrors only when needed.
- EL SLD reconciliation (`POST /sld/api/assets/<row_id>/reconcile`) resolves `Supply From` divergence between `electrical_building_schema` and `sdi_dataset_EL` with atomic dual-write and audit trail.
- `electrical_building_schema.ID_check` and `sdi_dataset_EL.ID_check` are PostgreSQL `GENERATED ALWAYS AS (...) STORED` columns — do not write to them manually. The old SQLite `VIRTUAL` form is rollback/reference history only.
- Dashboard SLD Extraction Runs log surfaces every SLD run from `/home/developer/sld_extract_feedback/sld_*.jsonl`; admin-only re-run via `POST /sld-logs/runs/<run_id>/rerun`.
- `DASHBOARD_ADMIN_USERS` env var (in `auth_service.env`) is the single source of truth for admin-gated re-run access across Dashboard and EL Reviewer.
- All significant DB writes and JSON edits emit `audit_trail` rows (`source`, `description`, `user`, `timestamp`).
- EL SLD building dropdown (`get_buildings()` / `GET /sld/api/buildings`, `sld_blueprint.py`) lists only buildings with at least one displayable (non-archived) QR code; a building is hidden only when it has QR codes and all of them are in `sdi_print_out_arch`. Hidden escape hatch `?include_archived=true` returns the unfiltered list (2026-06-01).
- Review-app archive toggle label corrected (was inverted) — reads "Show Archive" when archived rows are hidden and "Hide Archive" when shown. The Review Status filter and the archive button now preserve each other's query params (`archive`, `approved`), so "Approved + Show Archive" can be active together; previously each control reset the other on reload (EL/ME/BF, 2026-06-01).
- EL bulk "Approve all" (`select-all-approved`) surfaces per-row failures (auth/session/server) in a summary modal instead of silently swallowing them (2026-06-01).
- New read-only `scripts/audit_sdi_flow_integrity.py` audits the approve→package→archive flow: exclusion-pair consistency (`QR_codes.sdi=1` ⇔ `QR_code_assets.Col_process=2`), approved-but-unarchivable worklist, cross-store approval mismatch, and blank-identity. Companion runbook `special_processes/sdi_flow_remediation.md` documents case-by-case remediation via app endpoints (`/toggle_sdi`, `/toggle_approved`, `/review`) — never raw DB/JSON edits (2026-06-01).

- SDI package database guardrails added: `scripts/migrate_sdi_package_db_guardrails.py` installs normalized package QR unique indexes and lifecycle triggers; `scripts/audit_sdi_package_integrity.py` verifies the guardrail objects and reports historical unexported archive rows as warnings (2026-06-02).
- Dashboard and SDI self-service password changes now persist through `User.set_password()` instead of assigning an unmapped `current_user.password` attribute (2026-06-03).
- Capture app duplicate-QR existence check (`/api/check-qr`) and parameter-update lookups quote `"QR_code_ID"`: the unquoted identifier raised `UndefinedColumn` on PostgreSQL and silently suppressed the duplicate-scan warning. New global rules: double-quote all mixed-case identifiers; never swallow DB errors into defaulted responses without logging. See `INCIDENT_2026-06-09_pg_unquoted_identifier_duplicate_scan.md` (2026-06-09).
- Platform-wide PG-ism audit completed and deployed (2026-06-09): EXPLAIN-verified sweep of every service fixed Dashboard `ORDER BY ROWID` fallbacks and an unquoted `b."Name"`, approval-chart `instr()`/`""`/`COLLATE NOCASE`, and the `COALESCE("Avg_ai_conf", 0)` type error in all three review apps' placeholder-wipe guards (`_sdi_row_has_data*`; `test/test_placeholder_sync_guard.py` updated in sync). 62 swallow-and-default `except Exception` handlers inventoried for logging retrofits. See the incident doc's Platform-Wide Audit section (2026-06-09).
- Capture app `/submit` no longer runs schema DDL in the request path: `CREATE UNIQUE INDEX`/`ALTER TABLE` need table ownership on PostgreSQL (the app role `assetcap_app` has DML grants only), and a failed statement aborts the whole transaction — every mobile capture from the 2026-06-08 cutover to 2026-06-11 silently lost its DB rows (photos saved, registration rolled back, error swallowed into a flash toast). The DDL is now a one-time owner-run migration (`scripts/migrations/2026-06-11_ux_qr_code_assets.sql`); `/submit` failures log with `exc_info` and show a `danger` banner + redirect (no false success). 24 orphan QRs backfilled (`Location`/Space unrecoverable — needs field re-verification). New global rule: no DDL in request paths. See `INCIDENT_2026-06-11_pg_capture_registration_ddl.md` (2026-06-11).
- OpenAI extraction key switched platform-wide to `API/OpenAI_key_giba.env` (was `OpenAI_key_bryan.env`) across all three `API/API_interface_*_ver00.py` and their `.agent` docs (2026-06-11).
- Mobile Capture App records per-asset GPS into `QR_codes.capture_latitude` / `capture_longitude` with provenance `capture_coord_source` (`device` / `building`). Precise device GPS (browser Geolocation — best-effort, secure-context only, prompt cannot be removed) wins; a building-centroid fallback from `"UBC - All Properties List with GPS Coordinates"` fills when GPS is denied, and a stored device fix is never downgraded. The Start screen primes the one-time permission grant. Owner-run migrations `scripts/migrations/2026-06-16_qr_codes_capture_coords.sql` + `2026-06-16_qr_codes_capture_coord_source.sql`; coverage in `test/test_capture_coordinates.py` (2026-06-16).
- Mobile Capture App pre-submit screen gains optional Notes and Installation Date fields (2026-07-06, deployed 2026-07-07), persisted to `QR_codes.capture_notes` / `installation_date` and `{qr}_et.json`. Capture blank resubmits do not erase values. Since 2026-07-10, ME/BF/EL review editors can update or clear Installation Date in `DD/MM/YYYY`; SDI reads the QR-level value and exports `YYYY-MM-DD`. Installation Date remains outside extraction JSON.
- ME manufacturer canonicalization silently wipes multi-token makers absent from `ME_MANUFACTURER_REGEX_RULES` (two+ tokens with no legal suffix / `&` / hyphen fail the guarded fallback). Wipe signature: blank `Manufacturer` with confidence 0 while sibling seq `-0` fields (Model/Serial/Year) are confident — a dictionary gap, not a vision failure. `Spirax Sarco` + `Siemens` and the `ME_NUMERIC_MODEL_MANUFACTURERS` numeric-model whitelist added 2026-06-22; `Gardner Denver` added after the QR `0000186422` miss. The same investigation found VM-side extraction improvements in no git commit and reconciled the VM file into git (`7fc7b2f`). See `INCIDENT_2026-07-08_me_manufacturer_whitelist_vm_drift.md` (2026-07-08).

## Mirrors

`.agent_app/` mirrors the core orchestration docs for agent workflows. Update this folder first, then sync matching files into `.agent_app/` when root documentation changes.
