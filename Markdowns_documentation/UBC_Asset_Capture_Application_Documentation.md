# UBC Asset Capture Application Documentation

Current documentation refresh: 2026-08-03.

## Overview

This repository supports the end-to-end UBC asset data workflow:

1. capture raw photos and QR metadata
2. run ME, BF, and EL extraction
3. review and approve curated asset data
4. monitor operations in the dashboard
5. package approved assets through SDI Process

## Main Applications

### Capture App

Owns raw image capture and QR registration.

Primary outputs:

- `Capture_photos_upload/*.jpg`
- `QR_codes`
- `QR_code_assets`

### Extraction API

Owns JSON creation and extraction-level DB synchronization.

Primary outputs:

- `Output_jason_api/*.json`
- `json_files`
- initial curated dataset rows

### Review Apps

Own the human-in-the-loop correction layer for ME, BF, and EL.

Primary responsibilities:

- review JSON against photos
- preserve curated dictionary fields
- approve rows for SDI
- align Manual Entry with SDI exclusion state

### Dashboard

Owns process monitoring, operational analytics, FLS asset management, and dictionary editing.

Primary responsibilities:

- AI queue monitoring
- review analytics
- Operational Performance Analysis with the merged `Data Quality Comparison` chart
- FLS charts (Altair-based) for FLS asset management
- map chart for assets by building distribution
- SDI flow chart for flow quantity analytics
- dictionary management UI with AST-safe read/write
- FLS asset CRUD (add, delete, bulk update) against `new_device` table
- FLS Control Panel display lookup from `"UBC - Asset Data Master Info"` in New/Edit/detail views
- photo viewing API for captured asset images
- chained AI+DB sync launchers

### SDI Process

Owns packaging of approved curated rows and Planon export.

Primary rules:

- package approved assets only
- exclude QR codes where `QR_codes.sdi = 1`
- use normalized string QR joins to avoid duplicate fan-out
- export to Planon with UBC tag parsing and year formatting
- validation logs generated and accessible through UI
- archive management (active/archive/exclude)

## Discipline Rules

### Mechanical (ME)

- completeness uses `Manufacturer`, `Model`, `Serial Number`, `Year`, `UBC Tag`
- `Technical Safety BC` is only counted when seq `-3` exists
- seq ownership matters: `-0` plate, `-1` UBC, `-3` TSBC
- seq `-4` is the optional **Extra Photo** slot — captured/displayed but excluded from completeness, AI confidence, AI extraction, and "Missed Photo"
- seq `-1` tags use hybrid consensus: `gpt-5.4-mini` low-detail extraction is primary; local OCR and, only when challenged, one independent `gpt-5.6-terra` original-detail judge resolve the prefix/core by two-source agreement. Unresolved conflicts preserve the primary value, cap confidence, and route to review.

### Backflow (BF)

- completeness uses `Manufacturer`, `Model`, `Serial Number`, `Diameter`
- seq `-3` is the optional **Extra Photo** slot — captured/displayed but excluded from completeness, AI extraction, and "Missed Photo"

### Electrical (EL)

- completeness uses `UBC Asset Tag`, `Ampere`, `Supply From` in extractor/review JSON
- curated EL DB rows store canonical amperage in `sdi_dataset_EL."Amperage Rating"` and keep `Ampere` as a compatibility mirror
- AI confidence excludes `Volts`, `Location`, and `Branch Panel`
- `Fed From` and `Fed` normalize to `Supply From`
- seq `-3` is the optional **Extra Photo** slot — captured/displayed but excluded from completeness, AI extraction, and "Missed Photo"

## Shared Data Rules

- raw images are the source of truth for capture
- JSON is the source of truth for extracted and reviewed payload content
- curated DB rows support review dashboards and SDI packaging
- `.agent_app/` is a mirror of canonical docs, not an independent source

## Current Operational Rules

- review save and approval must preserve curated classification fields
- Manual Entry requires aligned state across `QR_code_assets.Col_process`, JSON `ExcludeSDI`, and `QR_codes.sdi`
- `Avg_ai_conf` must match discipline-aware rules and downstream display
- dashboard analytics must use the current discipline rules, not stale field counts
- chained AI+DB sync auto-runs DB sync after extraction (manual `update_db` removed)
- FLS assets tracked in `new_device` table with Planon checklist columns
- FLS `Attribute Set` defaults and normalizes to `FireAlarmDevice`; rows with `Planon Code` remain editable but cannot be deleted or bulk-selected
- FLS Control Panel Code/Description is display-only and derived from the selected building property code; multi-match lookups show the lowest Code and a flag
- dictionary editing from Dashboard uses AST-safe approach
- capture events record `user` and `date_hour` audit columns
- elapsed-time JSON written after capture submission
- parameter update service provides atomic rename across files and DB

## Verification Checklist

- one asset can be traced from image set to JSON to curated DB row to SDI eligibility
- approved assets appear in curated tables
- manual-entry excluded assets do not appear in SDI packaging
- operational charts render valid results for both `All` and `Open Process`
- FLS charts render when Altair is available
- dictionary edits persist correctly
- Planon export generates correctly formatted output
- validation logs are accessible and accurate
