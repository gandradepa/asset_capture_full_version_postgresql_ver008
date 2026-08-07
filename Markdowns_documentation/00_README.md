# UBC Facilities Agent Pack

> **🐘 Database backend: PostgreSQL (C4 cutover complete, 2026-06-08).** The platform now runs on PostgreSQL (`qr_code_db`, VM `127.0.0.1:5433`) via a backend-agnostic `db.py` layer, switched by `DB_BACKEND=postgres` in `/home/developer/db_backend.env`. The SQLite `QR_codes.db` referenced throughout is now the **frozen rollback** (flip the env back to `sqlite` + restart to revert). See `Markdowns_documentation/special_processes/04_database_topography.md`, `C4_CUTOVER_RUNBOOK.md`, and the `pg-cutover-complete` memory.

Current documentation refresh: 2026-08-03.

This document is the top-level index for the maintained documentation set in this repository.

## Purpose

Use this pack to understand:

- what each application owns
- which database and filesystem stores are shared
- where workflow state is persisted
- which documents are canonical versus mirrored

## Canonical Documentation Layers

- Root governance docs:
  `README.md`, `00_README.md`, `01_GLOBAL_RULES.md`, `02_SYSTEM_MAP.md`, `03_ARCHITECTURE_MAP.md`
- Root operational sets:
  `rules/`, `workflows/`, `special_processes/`, `skills/`
- Service-local docs:
  `asset_capture_app_dev/.agent/`, `API/.agent/`, `review/.agent/`, `Dashboard/.agent/`, `SDI_process/.agent/`, `dictionary/.agent_dictionary/`, `auth_service/.auth_agent/`

## Mirrored Documentation

`.agent_app/` mirrors the root orchestration set. Update the canonical `Markdowns_documentation/` files first, then sync the mirrored copies afterward.

## Module Responsibility Map

| Module | Responsibility |
| --- | --- |
| Capture App | QR intake, image upload, capture metadata, parameter update service, elapsed-time tracking, user/timestamp audit |
| Extraction API | OCR, LLM extraction, JSON output, AI status updates, chained AI+DB sync, shared validators |
| Review Apps | Human correction, approval, SDI inclusion / exclusion, QR replacement, confidence slicer |
| Dashboard | Operations, charts (approval, completeness, confidence, Data Quality, FLS, map, SDI flow), extraction launchers, dictionary management UI, FLS asset CRUD, FLS Control Panel lookup, photo API |
| SDI Process | Approved-asset packaging, Planon export, validation log viewer, archive management |
| Dictionary | AST-safe dictionary editing and taxonomy management (also editable from Dashboard) |
| Auth Service | Shared login model, password hashing, session management |

## Handoff Flow

`Capture App -> Extraction API -> Review Apps -> SDI Process -> Planon Export`

The Dashboard sits across that pipeline as the operational control plane.

## Current Cross-Cutting Behaviors

- Review approval writes to SDI source tables.
- Manual Entry should also imply SDI exclusion.
- SDI Process feeds Unpackaged Assets only from review **New Assets** (`QR_code_assets.Col_process = 0`) for ME, BF, and EL. Review **Update Existing** (`1`) and **Manual Entry** (`2`) stop in review and must not be packaged.
- SDI Process also filters on approved source rows, excludes package/archive rows, and excludes `QR_codes.sdi = 1`.
- Local development / DBeaver database is PostgreSQL `qr_code_db` on `127.0.0.1:5432` (`postgres` user). Production VM uses PostgreSQL `qr_code_db` on `127.0.0.1:5433` via `/home/developer/db_backend.env`; SQLite `QR_codes.db` is rollback only.
- Dashboard data-quality analytics use discipline-specific completeness and AI-confidence rules.
- Chained AI+DB sync (`run_ai_and_sync.sh`) automates database synchronization after extraction.
- ME sequence `-1` UBC tags use a bounded hybrid consensus: the normal low-detail `gpt-5.4-mini` read remains primary, local OCR validates the placard, and one independent `gpt-5.6-terra` original-detail judge call is permitted only for a suspicious tag. See `rules/asset_extraction_api.rules.md` and `workflows/02_run_extraction_me_el_bf.md`.
- Dictionary editing from the Dashboard UI uses AST-safe parsing, same as the standalone dictionary app.
- FLS asset management spans Dashboard CRUD and the `new_device` table with Planon checklist columns.
- FLS devices use `Attribute.Code = FireAlarmDevice` (`Electrical/FLS - Fire Alarm Device`); New FLS Device Flow defaults and normalizes `new_device."Attribute Set"` to that code.
- Planon-coded FLS rows remain editable for metadata corrections, but delete and bulk selection stay blocked once `Planon Code` exists.
- FLS New Device Flow derives display-only Control Panel Code/Description from `"UBC - Asset Data Master Info"` by building property code and flags multi-match lookups.
- Planon export in SDI Process includes UBC tag parsing and year-to-date formatting.
- All three disciplines support one optional **Extra Photo** capture slot (ME `-4`, BF `-3`, EL `-3`). The slot is reviewer-context only and excluded from completeness, AI extraction, and the "Missed Photo" count; it renders as a `+1` chip in the dashboard Photo column.
- Capture-side image saves apply `ImageOps.exif_transpose()` so phone photos with EXIF Orientation are stored upright on disk; historical files predating 2026-05-25 were not backfilled.
- Dashboard and SDI self-service password changes must call `User.set_password()` so the shared auth `password_hash` column is updated; see `INCIDENT_2026-06-03_password_change_persistence.md`.

## Start Here For Changes

- Cross-platform behavior:
  `01_GLOBAL_RULES.md`
- Runtime ownership and ports:
  `02_SYSTEM_MAP.md`
- Architecture and state model:
  `03_ARCHITECTURE_MAP.md`
- Targeted implementation constraints:
  matching file in `rules/`
- Operational flow:
  matching file in `workflows/`
- High-risk logic:
  matching file in `special_processes/`
