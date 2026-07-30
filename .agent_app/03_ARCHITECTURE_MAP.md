# Current Architecture Design for `asset_capture_full_version`

> **🐘 Database backend: PostgreSQL (C4 cutover complete, 2026-06-08).** The platform now runs on PostgreSQL (`qr_code_db`, VM `127.0.0.1:5433`) via a backend-agnostic `db.py` layer, switched by `DB_BACKEND=postgres` in `/home/developer/db_backend.env`. The SQLite `QR_codes.db` referenced throughout is now the **frozen rollback** (flip the env back to `sqlite` + restart to revert). See `Markdowns_documentation/special_processes/04_database_topography.md`, `C4_CUTOVER_RUNBOOK.md`, and the `pg-cutover-complete` memory.

Current documentation refresh: 2026-06-11.

## Overview

The repository is a workflow-driven platform composed of multiple Flask applications and worker scripts around one shared operational data core.

It is best described as:

`a distributed monolith with shared database and shared filesystem state`

## Primary Characteristics

- separate web apps for capture, review, dashboard, and SDI packaging
- separate worker scripts for AI extraction
- shared PostgreSQL `qr_code_db` workflow database (operational, VM `127.0.0.1:5433`; SQLite `QR_codes.db` is the frozen rollback)
- shared filesystem naming conventions for images and JSON artifacts
- direct cross-component integration through DB rows, filenames, and review sync
- Dashboard acts as a unified shell that embeds the ME / BF / EL / SDI apps in iframe panels while preserving each sub-app's domain and systemd service

## Component Roles

### Capture App

Owns:

- QR registration
- image upload
- initial location and asset-type capture
- parameter updates and atomic rename entry points

### Extraction API

Owns:

- OCR and LLM extraction
- default single-model policy: ME and BF use `gpt-5.4-mini`; EL and SLD use `gpt-5.4`; model fallback is disabled unless explicitly re-enabled with the existing environment overrides
- discipline-aware validation via shared validators (`validators_shared.py`)
- JSON artifact creation
- AI status updates
- chained AI+DB sync execution via `run_ai_and_sync.sh`
- JSON-to-DB sync helpers

### Review Apps

Own:

- human correction of extraction output
- discipline-specific dictionary application
- approval toggles
- AI / manual / SDI inclusion toggles
- QR replacement for temporary tags
- curated SDI table sync

### Dashboard

Owns:

- operational launch surface
- logs and chart views (approval, completeness, AI confidence, Data Quality Comparison, FLS Altair charts, building map, SDI flow)
- data quality and workflow analytics
- dictionary management UI with AST-safe read/write
- FLS asset CRUD (add, delete, bulk update) against the `new_device` table
- FLS Attribute Set default and normalization use `FireAlarmDevice` from `"Attribute"."Code"`
- FLS Control Panel display lookup against `"UBC - Asset Data Master Info"` by selected building property code; values are derived, not persisted to `new_device`
- photo viewing API for captured asset images
- some maintenance and admin flows

### SDI Process

Owns:

- approved-asset staging
- package grouping
- validation handoff and validation log viewer
- Planon-oriented export output with UBC tag parsing and year formatting
- package archive management (active/archive/exclude)

## Shared State Model

The platform spreads state across:

- PostgreSQL `qr_code_db` (operational workflow database; legacy `QR_codes.db` is rollback/reference only)
- JSON files in `Output_jason_api/`
- image filenames in `Capture_photos_upload/`
- elapsed-time JSON artifacts (`*_et.json` in `Output_jason_api/`)
- `new_device` table for FLS asset tracking with Planon checklist columns
- package staging tables and validation output

That means correctness often depends on keeping multiple representations aligned.

## Current State Boundaries

### Raw capture state

- image files
- `QR_codes`
- `QR_code_assets`

### AI extraction state

- JSON files in `Output_jason_api/`
- `ai_status` in `QR_codes`

### Human-reviewed state

- edited JSON payloads
- synced curated SDI rows

### Export state

- `sdi_dataset` / `sdi_dataset_EL`
- `sdi_print_out`
- `sdi_print_out_arch`

## High-Risk Integration Points

### 1. Completeness and confidence drift

The platform now uses discipline-specific formulas. Any doc, chart, or sync routine that assumes one shared formula becomes stale quickly.

### 2. Manual Entry versus SDI exclusion

Manual Entry is not only a review-tab concept. It must stay synchronized with SDI exclusion state, especially between:

- `QR_code_assets.Col_process`
- JSON `ExcludeSDI`
- `QR_codes.sdi`

### 3. SDI enrichment joins

SDI packaging uses QR-based enrichment from `QR_codes`. These joins must use normalized string QR keys and must reject placeholder IDs such as `None` or blank values.

### 4. Atomic rename flows

QR replacements and parameter changes touch DB rows, JSON filenames, image filenames, and processed logs. Partial success is not acceptable.

## Current Discipline Rules Summary

- ME:
  seq-owned extraction with dynamic completeness based on seq `-3`
- BF:
  curated through the shared `sdi_dataset`
- EL:
  curated through `sdi_dataset_EL`, with confidence and completeness rules that intentionally exclude some display fields from scoring

## Current Dashboard Architecture Note

Operational Performance Analysis exposes a combined `Data Quality Comparison` chart that compares completeness and AI confidence by asset type on one shared scale. Historical route and permission identifiers may still contain `cost`; the visible shell/sidebar label is `Performance Analysis`.

The Dashboard also provides:
- FLS charts (Altair-based) for FLS asset management visualization
- Map chart for assets by building distribution
- SDI flow chart for flow quantity metrics
- Dictionary management UI for AST-safe editing of the mechanical dictionary
- FLS asset CRUD against the `new_device` table with Planon checklist columns and normalized `Attribute Set = FireAlarmDevice`; populated `Planon Code` blocks delete and bulk selection, but not Edit.
- FLS New Device Flow table hides Asset Group, Space, and Details in the primary flow view; the magnifying-glass details modal keeps those fields visible.
- Control Panel Code/Description in FLS forms, details modal, and table rows comes from `"UBC - Asset Data Master Info"` by `Property code`; multiple matches display the first Code and a warning flag.
- Photo viewing API (`/api/asset-photo/<qr_code>`) for captured asset images
- Chained AI+DB sync launchers that automatically run DB sync after extraction

## Unified Dashboard Shell Architecture

The Dashboard hosts the four process apps inside `<iframe>` panels rather than opening them in separate browser tabs. This pattern preserves every sub-app's independent domain, port, and systemd service while presenting a single navigation surface to the user.

### Shell components

- Central Dashboard at `https://dashboardprod.assetcap.facilities.ubc.ca` defines hash-routed views: `#review-me-view`, `#review-bf-view`, `#review-el-view`, `#sdi-view`.
- Each view contains a `process-view-header` strip ("Asset Reviewer — X" + `Dashboard` (back to main) + `Open full page` buttons) and an `<iframe>` whose `data-src` is set lazily on first activation.
- `IFRAME_VIEW_MAP` in `Dashboard/templates/dashboard.html` maps each view ID to its iframe element. `handleViewSwitch()` calls `maybeLoadIframe(target)` to set `iframe.src` once.
- `<main>` toggles between `.container` (constrained), `.container-fluid-fls` (full-width with padding for FLS), and `.container-iframe-view` (full-width, edge-to-edge for iframe panels) depending on the active view.

### Cross-subdomain session sharing

- Every Flask app loads `SECRET_KEY` and `SESSION_COOKIE_DOMAIN=.assetcap.facilities.ubc.ca` from `/home/developer/auth_service.env`.
- All apps now set `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, plus the matching `REMEMBER_COOKIE_*` triple. This is mandatory for cross-subdomain iframe cookie delivery (Chrome 80+).
- One login at the central Dashboard authenticates the user across every embedded sub-app.

### Embedded mode (sub-app side)

- Each sub-app's `before_request` hook (on `app` for ME / BF, on `main_bp` for EL / SDI) sets `g.embedded = request.args.get('embedded','').lower() == 'true'`.
- Templates wrap their own top navbars, brand headers, and user dropdowns in `{% if not g.embedded %}` so they render as standalone but suppress chrome inside the iframe.
- A `<body>` class `embedded-mode` provides CSS-level fallback suppression.
- A small JS block at the bottom of each sub-app template intercepts internal `<a>` clicks and appends `?embedded=true` so the embedded state persists across navigation inside the iframe.

### Cross-origin parent navigation

- The iframe and the parent Dashboard are on different subdomains, so direct `window.top.location` access is blocked by the same-origin policy.
- The "Dashboard" button lives in the central Dashboard's process-view-header (parent frame) and calls `handleViewSwitch()` directly to reset the hash. No iframe-side button is needed.
- An optional `window.parent.postMessage({action:'go-to-main'}, 'https://dashboardprod.assetcap.facilities.ubc.ca')` channel is implemented for sub-app initiated navigation; the parent listener verifies `event.origin` against an explicit allowlist before acting.

### Nginx CSP

- Each sub-app's Nginx config sets `Content-Security-Policy: frame-ancestors 'self' https://dashboardprod.assetcap.facilities.ubc.ca;` with the `always` flag (so the header survives 302 login redirects).
- The central Dashboard sets `Content-Security-Policy: frame-ancestors 'none';` to prevent it from being embedded anywhere else.

## Life Cycle Assessment Blueprint

The Life Cycle Assessment feature is an in-process Flask Blueprint (`life_cycle`) mounted **inside** the existing Dashboard app (`Asset_portal_dashboard.py`) at `url_prefix="/life-cycle"`. It is not a separate service or port; it runs as part of the `assetcap-dashboard` systemd service (gunicorn, port 8002, `dashboardprod.assetcap.facilities.ubc.ca`). Registration is wrapped in `try/except` so a missing dependency degrades to "feature absent" without crashing the portal.

It shows asset age / life-cycle data for the Mechanical asset group `ME.91.902.4817.5956` (Heating Water Storage Tanks). Rows split into Complete / Incomplete tabs by data completeness, summarised by an Age Classification **donut chart** (Good `<=8` yrs, Caution 8-10 yrs, Critical `>=10` yrs, Unknown no installation date) plus a companion **Life-cycle Expiry** bar chart (X = installation year + 10, the year an asset reaches 10 yrs of service; Y = years in service with a dashed 10-yr line; bars coloured by age band). A row is "Complete" only when Make, Space Number, Serial Number **and** Installation Date are all present; otherwise Incomplete.

### Code layout

- `Dashboard/life_cycle/` package: `__init__.py` (exports `life_cycle_bp`), `blueprint.py` (routes + data access), `excel_export.py` (styled XLSX export), `static/css/styles.css`, `static/js/dashboard.js`, `static/img/ubc-facilities_logo.jpg`, `templates/life_cycle/dashboard.html`.
- `life_cycle_pipeline/` package (sibling of `Dashboard`; on the VM at `/home/developer/life_cycle_pipeline`): `track_assets.py` (builds a DataFrame from the Excel workbook, joins Floor Name from `SpaceUID`), `load_life_cycle.py` (loads into PostgreSQL), `"UBC - Asset Basic Info.xlsx"` (default source workbook), `__init__.py`.

### Routes (all under the `/life-cycle` prefix)

- `GET /life-cycle/` — HTML page; requires login + `lifecycle_assessment` viewer permission.
- `POST /life-cycle/export` — styled XLSX of the visible rows.
- `POST /life-cycle/refresh` — uploads an Excel workbook and rebuilds the tables. **Destructive** (drops/rebuilds `life_cycle` + `space_floor`); requires DDL privileges for the `assetcap_app` DB user.
- `GET /life-cycle/health` — open, no auth (liveness probe).
- Blueprint static served at `/life-cycle/static/...`.

### Permissions (RBAC)

A new key — section `operations`, item `lifecycle_assessment` — was added to `auth_service/app_registry.py`. Routes enforce it via `has_permission` / `require_permission` (same model as FLS Devices: enforced server-side; the sidebar link itself is not visibility-gated). Granted per-user (viewer/editor) through the Dashboard User Admin screen.

### Navigation and standalone page

- A "Life Cycle Assessment" item in the shared shell sidebar (`Dashboard/static/shell/shell.js`) under the "Operations" group, directly below "FLS Devices", icon `activity`. It links to `https://dashboardprod.assetcap.facilities.ubc.ca/life-cycle/` (the standalone full page).
- The nav entry + breadcrumb (Home / Operations / Life Cycle Assessment) were propagated to all five `shell.js` copies (Dashboard, ME / BF / EL reviewers, SDI Process); shell assets are cache-busted via the `?v=` query in each app's `_shell.html`.
- The standalone `/life-cycle/` page also mounts the shared shell (`acshell-active "life"`). Because the page is same-origin within the Dashboard app, it is a full page rather than an embedded iframe panel.

### Data model

- `life_cycle` — main table, **rebuilt on every "Update Database" run** via pandas `to_sql(if_exists="replace")`. All columns `TEXT` except `years` (float) and `months` (integer). Built from the Excel workbook filtered to Asset Group Code `ME.91.902.4817.5956`, with Floor Name joined from the `SpaceUID` table.
- `space_floor` — deduplicated reference table (Property Code, Space Number -> Floor Name) built from `SpaceUID` with a PRIMARY KEY; rebuilt on every load. `life_cycle` carries a composite FK `life_cycle_space_floor_fkey` referencing `space_floor`.
- `life_cycle_meta` — small key/value table that survives the `life_cycle` rebuild; stores the `last_loaded` timestamp shown in the dashboard footer.
- At read time the page derives a "Captured" flag (QR present in `QR_codes` with a `date_set` — i.e. field-captured) and a "Capture Date" (`QR_codes.date_set`) per row. Those existing tables are read-only inputs.

### DB configuration

The connection is derived from a single source — env var `LIFE_CYCLE_DSN` (libpq DSN) if set, else the portal's `QR_PG_DSN`, else the dev sandbox default. The SQLAlchemy URL `LIFE_CYCLE_SA_DSN` (used by `track_assets.py` and `load_life_cycle.py`) is derived from that libpq DSN at blueprint import; `load_life_cycle.py`'s formerly hardcoded `DB_URL` now reads `LIFE_CYCLE_SA_DSN`. The footer "Source: `<db>.life_cycle`" shows the actual DB name parsed from the live DSN (`qr_code_db` in prod, `qr_code_db_sandbox` on `:5432` in dev). The blueprint + pipeline add `pandas`, `numpy`, `sqlalchemy`, `openpyxl`, `psycopg2(-binary)`, and `Pillow` to `Dashboard/requirements.txt`.

Integrated and deployed to production 2026-06-23.

## Documentation Strategy

- Root docs explain platform-wide behavior.
- `rules/` define implementation constraints.
- `workflows/` define operational flow.
- `special_processes/` explain logic that is easy to break.
- Service-local `.agent` docs capture service-specific details.
