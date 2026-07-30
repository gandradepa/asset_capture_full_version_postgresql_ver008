# UBC Asset Capture Documentation

Current documentation refresh: 2026-04-28.

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

## Current High-Impact Behaviors

- Discipline-specific completeness and confidence scoring are active.
- EL extraction and review use stricter source rules for amperage, voltage, power rating, location, and upstream equipment identifiers.
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

## Mirrors

`.agent_app/` mirrors the core orchestration docs for agent workflows. Update this folder first, then sync matching files into `.agent_app/` when root documentation changes.
