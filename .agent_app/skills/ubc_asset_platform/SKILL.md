# Skill: UBC Asset Platform

Current documentation refresh: 2026-05-25.

## Use This Skill When

- a task spans capture, extraction, review, dashboard, and SDI behavior
- you need to trace one asset across images, JSON, DB rows, and downstream packaging
- you need to update docs or rules that affect multiple services

## Core Mental Model

1. images in `Capture_photos_upload/` are the raw source
2. JSON in `Output_jason_api/` is the extraction and review payload source
3. curated PostgreSQL `qr_code_db` rows support review dashboards and SDI packaging
4. SDI Process packages approved curated rows, not raw extraction guesses

## Current Cross-Cutting Rules

- completeness and AI confidence are discipline-specific
- ME seq ownership matters: `-0` plate, `-1` UBC tag, `-3` Technical Safety BC
- Extra Photo slot (ME `-4`, BF `-3`, EL `-3`) is optional, owns no fields, and is excluded from `VALID_SUFFIXES`, completeness, AI confidence, and "Missed Photo"
- review approval must preserve curated classification fields
- Manual Entry and SDI exclusion state must stay aligned
- `.agent_app/` is a mirror layer, not an independent source of truth
- chained AI+DB sync (`run_ai_and_sync.sh`) auto-runs DB sync after extraction
- FLS assets are managed through Dashboard CRUD against `new_device`; Attribute Set defaults to `FireAlarmDevice`; Control Panel Code/Description is display-only from `"UBC - Asset Data Master Info"` by building property code; Planon-coded rows remain editable but cannot be deleted or bulk-selected
- dictionary editing from Dashboard uses AST-safe read/write, never `eval()`
- Planon export includes UBC tag parsing and year formatting
- parameter update service provides atomic rename across files and DB
- capture-side image saves apply `ImageOps.exif_transpose()` so phone photos are stored upright on disk (post-2026-05-25 uploads)

## Preferred Investigation Order

1. identify the owning service
2. inspect the source-of-truth artifact for that state change
3. verify DB synchronization behavior
4. verify downstream consumers such as review dashboards, Operational Performance Analysis, and SDI Process
5. update the matching canonical docs and then the mirrors

## Deliverables

- code fix or verified explanation
- affected files and tables
- verification steps and any remaining risk
