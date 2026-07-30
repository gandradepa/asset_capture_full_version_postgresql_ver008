# Dashboard Agent â€” AI Assistant Instructions

Current documentation refresh: 2026-04-29.

## Application Identity

The **Asset Management Dashboard** is a Flask web application (port **8002**) that serves as the central monitoring, analytics, and management hub for UBC Facilities' new-asset onboarding pipeline. It consolidates data from QR code scanning, AI-driven plate extraction (ME/EL/BF), reviewer approval workflows, and SDI label printing into a single unified portal.

**Production URL**: `https://dashboardprod.assetcap.facilities.ubc.ca`
**Local URL**: `http://127.0.0.1:8002`

## Unified Shell Architecture

The Dashboard hosts the four process apps (ME, BF, EL, SDI) as embedded iframe panels rather than launching them in new browser tabs. Each sub-app keeps its own port, domain, and systemd service.

- Hash-routed iframe views: `#review-me-view`, `#review-bf-view`, `#review-el-view`, `#sdi-view`.
- `IFRAME_VIEW_MAP` in `templates/dashboard.html` links each view ID to its iframe element. `maybeLoadIframe(target)` lazily sets `iframe.src` once per session.
- `<main>` toggles `.container-iframe-view` (full-width, edge-to-edge) for iframe tabs and `.container-fluid-fls` for the FLS view.
- The `process-view-header` for each iframe view exposes a `Dashboard` button (returns to main view via `handleViewSwitch()`) and an `Open full page` button (sub-app standalone in new tab).
- A `message` listener with an explicit `allowedOrigins` array handles `postMessage({action:'go-to-main'})` from sub-apps.
- Cookie config: `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, plus matching `REMEMBER_COOKIE_*`. Required for cross-subdomain cookie delivery.
- Nginx CSP for the Dashboard: `frame-ancestors 'none'` (cannot be embedded anywhere).

See `Markdowns_documentation/rules/dashboard.rules.md` for the full embedded-shell rule set.

---

## Architecture Overview

```
Dashboard/
â”œâ”€â”€ Asset_portal_dashboard.py      # Main Flask app (2400+ lines, 75 functions)
â”œâ”€â”€ charts/                        # Python chart-rendering modules
â”‚   â”œâ”€â”€ approval.py                # Gauge + bar + pie charts (Matplotlib)
â”‚   â”œâ”€â”€ completeness_score.py      # Gradient thermometer charts (Matplotlib)
â”‚   â”œâ”€â”€ operational_cost_result.py # Combo chart + KPI cards (Matplotlib)
â”‚   â”œâ”€â”€ fls_chart.py               # Status & workflow charts (Altair â†’ HTML)
â”‚   â”œâ”€â”€ flow_quantity_chart.py     # SDI pipeline flow data builder
â”‚   â”œâ”€â”€ map_chart.py               # Asset-by-building data loader
â”‚   â””â”€â”€ ai_status_table_new_version.py  # AI processing status tracker
â”œâ”€â”€ templates/
â”‚   â”œâ”€â”€ dashboard.html             # Main SPA template (4000+ lines)
â”‚   â”œâ”€â”€ login.html                 # Authentication page
â”‚   â”œâ”€â”€ index_dictionary.html      # Asset dictionary management
â”‚   â”œâ”€â”€ map_new_assets_by_building.html  # Map view of assets
â”‚   â”œâ”€â”€ sdi_label.html             # SDI label flow chart
â”‚   â”œâ”€â”€ fls_charts_container.html  # FLS Altair chart embed
â”‚   â”œâ”€â”€ logs.html / log_read.html  # Log browser UI
â”‚   â””â”€â”€ change_password.html       # Password management
â”œâ”€â”€ static/
â”‚   â”œâ”€â”€ style.css                  # Base styles
â”‚   â”œâ”€â”€ responsive-design.css      # Mobile-first responsive framework (1300 lines)
â”‚   â”œâ”€â”€ responsive-utils.js        # JS viewport/touch/performance utilities
â”‚   â””â”€â”€ logos/                     # UBC branding assets
â”œâ”€â”€ dashboard.env                  # Environment config
â”œâ”€â”€ requirements.txt               # Python dependencies
â””â”€â”€ start_portal.bat               # Windows launcher script
```

### Blueprints
| Blueprint | Prefix | Purpose |
|-----------|--------|---------|
| `auth`    | `/`    | Login, logout, password change (Flask-Login + bcrypt) |
| `main`    | `/`    | All dashboard views, API endpoints, chart routes |
| `life_cycle` | `/life-cycle` | Life Cycle Assessment dashboard (asset age / life-cycle data). Registered in-process inside `Asset_portal_dashboard.py`, wrapped in `try/except` so a missing dependency degrades to "feature absent" without crashing the portal |

### Authentication
- Uses a shared `auth_service` module (external to Dashboard)
- Models: `auth_model.py` â†’ `User` table in SQLite with bcrypt-hashed passwords
- All `main` routes require `@login_required`

---

## Dashboard Views (SPA â€” Single Page Application)

The main `dashboard.html` is a single-page application. Views are toggled via JavaScript `showView(viewId)`. Each view is a `<div>` that is shown/hidden.

| View ID | Name | Description |
|---------|------|-------------|
| `main-view` | **Main Dashboard** | Application launcher cards (links to external apps), weather widget, pipeline summary |
| `analytics-view` | **Reviewer Analysis** | Approval gauge/bar/pie charts, building filter, status filter, interactive bar hitboxes with hover details |
| `qr-pending-view` | **QR Pending / AI Status** | Table of assets pending AI processing, building-grouped summary, status tracking |
| `operational-cost-view` | **Operational Cost** | Daily operation combo chart, KPI cards (avg duration, AI cost efficiency), year/month/metric filters |
| `fls-assets-view` | **FLS Assets** | Full CRUD table for `new_device` records, inline editing, bulk update, Planon checklist columns, add/edit/delete modals; Planon-coded rows remain editable but are locked from delete/bulk selection |
| `user-activity-view` | **User Activity** | QR code scan activity by user, date range filtering, reviewer statistics |

---

## Data Layer

### Primary Database
**Operational DB**: PostgreSQL `qr_code_db` (VM `127.0.0.1:5433`) through `db.py`; `QR_codes.db` is rollback/reference only

Key tables:
| Table | Purpose |
|-------|---------|
| `QR_codes` | Master QR code registry (QR_code_ID, Location, date_set, sdi, ai_status, elapsetime) |
| `QR_code_assets` | Asset data captured per QR code (ME/BF/EL fields, user, timestamp) |
| `Buildings` | Building lookup (Code â†’ Name) |
| `sdi_dataset` | SDI dataset for Mechanical/Backflow assets |
| `sdi_dataset_EL` | SDI dataset for Electrical assets |
| `sdi_print_out` | Assets with printed SDI labels |
| `sdi_print_out_arch` | Archived printed SDI labels |
| `new_device` | FLS new-device tracking with Planon checklist fields and normalized `Attribute Set = FireAlarmDevice` |
| `json_files` | Tracks which JSON output files have been processed |
| `dictionary_assets` | Asset dictionary entries |

### JSON Data Source
- **Directory**: `/home/developer/Output_jason_api/`
- Contains `<QR>_<TYPE>_<Building>.json` files produced by ME/EL/BF extraction scripts
- Read by `completeness_score.py` for score calculations

### Authentication Database
- Separate SQLite managed by `auth_service` with `User` table

---

## Chart Modules

Each chart module in `charts/` follows a consistent pattern:

| Module | Rendering | Public API |
|--------|-----------|------------|
| `approval.py` | Matplotlib PNG â†’ bytes | `render_chart_png(building, chart_type, status)`, `building_options()`, `render_bar_hitboxes(building, status)` |
| `completeness_score.py` | Matplotlib PNG â†’ bytes | `render_chart_png(building)` |
| `operational_cost_result.py` | Matplotlib PNG â†’ bytes | `render_chart_png(chart_type, building, year, month, metric)` |
| `fls_chart.py` | Altair â†’ HTML files in `charts/static/` | `generate_charts()`, `fls_df()` |
| `flow_quantity_chart.py` | Returns dict records | `build_asset_workflow(db_path)` |
| `map_chart.py` | Returns DataFrame | `map_new_assets_all(db_path)` |
| `ai_status_table_new_version.py` | Returns DataFrames | `get_pending_assets()`, `update_ai_status_in_db(assets_df, db_path)` |

---

## Task Runner

The dashboard can launch extraction scripts as detached background processes:

| Task Key | Script | Asset Type |
|----------|--------|------------|
| `run_me` | `API_interface_ME_ver00.py` | Mechanical |
| `run_el` | `API_interface_EL_ver00.py` | Electrical |
| `run_bf` | `API_interface_BF_ver00.py` | Backflow |

**Route**: `POST /run/<task_key>` â†’ launches script via `_launch_cmd_detached()`
**Log Status**: `GET /log-status/<name>` â†’ returns last 200 lines of the task log

---

## Life Cycle Assessment Feature

A dashboard feature showing asset age / life-cycle data for the Mechanical asset group `ME.91.902.4817.5956` (Heating Water Storage Tanks). Rows split into Complete / Incomplete tabs by data completeness, with an Age Classification **donut chart** (Good <=8 yrs, Caution 8-10 yrs, Critical >=10 yrs, Unknown no installation date) plus a companion **Life-cycle Expiry** bar chart (X = installation year + 10, the year an asset reaches 10 yrs of service; Y = years in service with a dashed 10-yr line; bars coloured by age band). A row is **Complete** only when Make, Space Number, Serial Number AND Installation Date are all present; otherwise Incomplete.

It is an in-process Flask Blueprint (`life_cycle`) mounted inside the Dashboard app at prefix `/life-cycle` - not a separate service or port. It runs as part of the `assetcap-dashboard` systemd service (gunicorn, port 8002).

### Package Layout

```
Dashboard/life_cycle/
  __init__.py                       # exports life_cycle_bp
  blueprint.py                      # routes + data access
  excel_export.py                   # styled XLSX export
  static/css/styles.css
  static/js/dashboard.js
  static/img/ubc-facilities_logo.jpg
  templates/life_cycle/dashboard.html

life_cycle_pipeline/                # sibling of Dashboard (VM: /home/developer/life_cycle_pipeline)
  __init__.py
  track_assets.py                   # builds a DataFrame from the Excel workbook, joins Floor Name from SpaceUID
  load_life_cycle.py                # loads into PostgreSQL
  UBC - Asset Basic Info.xlsx       # default source workbook
```

### Routes (all under `/life-cycle`)

| Route | Method | Purpose |
|-------|--------|---------|
| `/life-cycle/` | GET | HTML page; requires login + `lifecycle_assessment` viewer permission |
| `/life-cycle/export` | POST | Styled XLSX of the visible rows |
| `/life-cycle/refresh` | POST | Uploads an Excel workbook and rebuilds the tables (destructive) |
| `/life-cycle/health` | GET | Open, no auth - liveness probe |

Blueprint static is served at `/life-cycle/static/...`.

### PostgreSQL Tables

`qr_code_db` (`127.0.0.1:5433`, production) / `qr_code_db_sandbox` (`:5432`, dev).

| Table | Purpose |
|-------|---------|
| `life_cycle` | Main table, **rebuilt** on every "Update Database" run via `to_sql(if_exists="replace")`. All columns TEXT except `years` (float) and `months` (integer). Built from the Excel workbook filtered to Asset Group Code `ME.91.902.4817.5956`, with Floor Name joined from SpaceUID |
| `space_floor` | Deduplicated reference table (Property Code, Space Number -> Floor Name) built from SpaceUID with a PRIMARY KEY; rebuilt on every load. `life_cycle` carries a composite FK `life_cycle_space_floor_fkey` referencing it |
| `life_cycle_meta` | Small key/value table that survives the `life_cycle` rebuild; stores the `last_loaded` timestamp shown in the dashboard footer |

At read time the page also derives a "Captured" flag (QR present in `QR_codes` with a `date_set` — i.e. field-captured) and a "Capture Date" (`QR_codes.date_set`) per row. Those existing tables are read-only inputs.

---

## External Integrations

| Integration | Usage |
|-------------|-------|
| **OpenAI API** | AI cost estimation calculations in operational cost module |
| **Matplotlib** | Server-side chart rendering (approval, completeness, operational cost) |
| **Altair** | Interactive HTML charts for FLS status/workflow |
| **jsPDF + AutoTable** | Client-side PDF/Excel export from dashboard tables |
| **Bootstrap 5.3** | UI framework (modals, grid, utilities) |
| **Font Awesome 6** | Icons throughout the interface |

---

## Key Conventions

1. **Views are SPA-style**: Never create separate HTML pages for dashboard sections â€” add new `<div>` views and register with `showView()`
2. **Charts return PNG bytes**: New Matplotlib chart modules must return `bytes` from `render_chart_png()` served via `Response(data, mimetype='image/png')`
3. **Graceful imports**: Chart module imports are wrapped in `try/except` with `*_AVAILABLE` flags; the app must start even if a chart module fails
4. **Path resolution**: Code must handle both Linux (`/home/developer/...`) and Windows paths via `os.getenv()` fallbacks
5. **Responsive design**: All new UI must use the CSS custom properties from `responsive-design.css` and be tested at mobile breakpoints
