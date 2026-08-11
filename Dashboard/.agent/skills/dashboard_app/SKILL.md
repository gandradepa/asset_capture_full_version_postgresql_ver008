---
name: dashboard_app
description: Developer skill guide for the UBC Asset Management Dashboard. Covers project structure, Flask routes, chart modules, templates, responsive framework, database schema, and common development tasks.
---

# Dashboard Application Skill

Current documentation refresh: 2026-04-28.

## Use this skill when
- Adding, modifying, or debugging Flask routes in the Dashboard application
- Creating new chart modules or interactive front-end views
- Modifying the Single Page Application (SPA) structure in `dashboard.html`

## Do not use this skill when
- Modifying the core API extraction scripts (refer to `API/.agent` instead)

## Instructions
Review the project structure and routing catalog before implementing feature changes. Ensure all new components follow the Responsive Design Framework and RESTful JSON patterns outlined below.

## Project Structure

```
Dashboard/
â”œâ”€â”€ Asset_portal_dashboard.py   # Flask app entry point (port 8002)
â”œâ”€â”€ charts/                     # Chart rendering modules
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ approval.py             # Approval gauge/bar/pie (Matplotlib)
â”‚   â”œâ”€â”€ completeness_score.py   # Data completeness thermometer (Matplotlib)
â”‚   â”œâ”€â”€ operational_cost_result.py  # Cost/duration combo + cards (Matplotlib)
â”‚   â”œâ”€â”€ fls_chart.py            # FLS status/workflow (Altair â†’ HTML)
â”‚   â”œâ”€â”€ flow_quantity_chart.py  # SDI pipeline flow aggregator (returns dicts)
â”‚   â”œâ”€â”€ map_chart.py            # Asset-by-building data loader (returns DataFrame)
â”‚   â””â”€â”€ ai_status_table_new_version.py  # Pending AI status tracker (returns DataFrame)
â”œâ”€â”€ templates/
â”‚   â”œâ”€â”€ dashboard.html          # Main SPA (4000+ lines: HTML + CSS + JS)
â”‚   â”œâ”€â”€ login.html              # Login form
â”‚   â”œâ”€â”€ index_dictionary.html   # Asset dictionary CRUD
â”‚   â”œâ”€â”€ map_new_assets_by_building.html  # Map page
â”‚   â”œâ”€â”€ sdi_label.html          # SDI label flow chart
â”‚   â”œâ”€â”€ fls_charts_container.html       # Altair chart iframe
â”‚   â”œâ”€â”€ logs.html               # Log list
â”‚   â”œâ”€â”€ log_read.html           # Log content viewer
â”‚   â””â”€â”€ change_password.html    # Password form
â”œâ”€â”€ static/
â”‚   â”œâ”€â”€ style.css               # Base dashboard styles
â”‚   â”œâ”€â”€ responsive-design.css   # Mobile-first responsive framework (1300 lines)
â”‚   â”œâ”€â”€ responsive-utilities.css # Additional responsive utilities
â”‚   â”œâ”€â”€ responsive-utils.js     # JS viewport/touch/performance manager (577 lines)
â”‚   â”œâ”€â”€ logos/                  # UBC branding (6 files)
â”‚   â””â”€â”€ *.html                  # Plotly/chart embeds (device admin, priority, etc.)
â”œâ”€â”€ charts/static/              # Generated Altair HTML outputs
â”œâ”€â”€ logs/                       # Runtime log directory
â”œâ”€â”€ dashboard.env               # Environment variables
â”œâ”€â”€ requirements.txt            # Python deps: flask, pandas, numpy, matplotlib, openai, etc.
â”œâ”€â”€ start_portal.bat            # Windows batch launcher
â””â”€â”€ venv/                       # Python virtual environment
```

---

## Flask Route Catalog

### Authentication Routes (`auth` Blueprint)

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET/POST | `/login` | `login()` | Login form + credential validation |
| GET | `/logout` | `logout()` | Ends session, redirects to login |

### Main Dashboard Routes (`main` Blueprint)

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/` | `index()` | Renders `dashboard.html` with apps list, weather, charts, FLS data |

### Dictionary Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/dictionary` | `dictionary_index()` | Dictionary management page (passes `can_edit`) |
| GET | `/api/assets` | `get_dictionary_assets()` | JSON list of dictionary entries |
| POST | `/api/assets` | `save_dictionary_asset()` | Create/update dictionary entry (audited) |
| POST | `/api/assets/delete` | `delete_dictionary_asset()` | Delete dictionary entry (audited) |
| GET | `/api/main-assets` | `get_main_assets_dropdown()` | Dropdown options for Main Asset |
| GET | `/api/asset-groups` | `get_asset_groups_dropdown()` | Dropdown options for Asset Group |
| GET | `/api/attributes` | `get_attributes_dropdown()` | Dropdown options for Attributes |

The dictionary API paths are un-namespaced (`/api/assets`, not `/api/dictionary/assets`) — an artifact of the blueprint being registered without a `url_prefix`. Page access needs `dictionary/dictionary` viewer; writes need editor. Asset Type is limited to ME/EL/BF (`DICTIONARY_ALLOWED_TYPES`).

### Map Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/map-new-assets` | `map_new_assets_page()` | Map template page |
| GET | `/api/map-new-assets` | `api_map_new_assets()` | JSON asset data for map |

### User Activity Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/api/user-activity` | `get_user_activity()` | JSON user scan activity with filtering |

### Reviewer Analysis Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/api/reviewer-bar-hitboxes` | `reviewer_analysis_bar_hitboxes()` | Chart hitbox coordinates for interaction |
| GET | `/api/reviewer-analysis-hover` | `reviewer_analysis_hover()` | Asset details on chart hover |

### Photo Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/api/asset-photo/<qr_code>` | `api_asset_photo()` | Find photo filename for QR code |
| GET | `/photos/<filename>` | `get_asset_photo_file()` | Serve photo file |

### Chart Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/chart/approval` | `approval_chart()` | Approval chart PNG (params: `building`, `type`, `status`) |
| GET | `/chart/completeness` | `completeness_chart()` | Completeness score PNG (param: `building`) |
| GET | `/chart/operational-cost` | `operational_cost_chart()` | Operational cost PNG (params: `type`, `building`, `year`, `month`, `metric`) |
| GET | `/chart/sdi-flow` | `sdi_flow_chart()` | SDI label flow chart page |
| GET | `/chart/fls` | `fls_charts()` | FLS Altair charts page |

### FLS Asset CRUD Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/api/fls-assets` | `get_fls_asset_data()` | Full FLS asset table data as JSON |
| POST | `/api/fls-assets/add` | `add_fls_assets()` | Insert or upsert a new device record; enforces `Attribute Set = FireAlarmDevice` |
| POST | `/api/fls-assets/delete` | `delete_fls_assets()` | Delete a device record; Planon-coded rows are blocked |
| POST | `/api/fls-assets/bulk-update` | `bulk_update_assets()` | Bulk update selected assets; Planon-coded rows are not selectable |
| POST | `/api/fls-assets/update-field` | `update_fls_asset_field()` | Inline single-field update |

### Task Runner Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| POST | `/run/<task_key>` | `run_task()` | Launch extraction script (me/el/bf) |
| GET | `/log-status/<name>` | `log_status()` | Tail last 200 lines of task log |

### Log Viewer Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/logs` | `list_logs()` | List all log files with summaries |
| GET | `/log-read` | `read_log()` | View full log content |
| GET | `/log-download` | `download_log()` | Download log file |

### Other Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET/POST | `/change-password` | `change_password()` | Change user password |

---

## Chart Module Patterns

### Matplotlib Modules (approval, completeness_score, operational_cost_result)

All follow this pattern:
```python
def render_chart_png(building: str = "All", **filters) -> bytes:
    """Renders chart and returns PNG bytes."""
    # 1. Load data from PostgreSQL / JSON
    # 2. Filter by building and other params
    # 3. Create Matplotlib figure
    # 4. Save to BytesIO buffer
    # 5. Return buffer.getvalue()
```

Flask route serves them:
```python
@main_bp.route('/chart/example')
@login_required
def example_chart():
    building = request.args.get('building', 'All')
    data = example_mod.render_chart_png(building=building)
    return Response(data, mimetype='image/png')
```

### Altair Module (fls_chart)

```python
def generate_charts():
    """Generates HTML chart files saved to charts/static/."""
    df = fls_df()
    # Create Altair charts and save as .html
```

### Data-Only Modules (flow_quantity_chart, map_chart, ai_status_table_new_version)

Return Python objects (dicts, DataFrames) consumed directly by the Flask route or template rendering logic.

---

## Database Schema (Key Tables)

| Table | Key Columns | Used By |
|-------|-------------|---------|
| `QR_codes` | QR_code_ID, Location, date_set, sdi, ai_status, elapsetime | approval, operational_cost, flow_quantity, ai_status |
| `QR_code_assets` | QR_code_ID, asset_type, building_number, Approved, completeness_score, user | approval, completeness, user_activity |
| `Buildings` | Code, Name | All modules (building filter) |
| `sdi_dataset` | QR Code, Building, Approved | flow_quantity (ME/BF pipeline) |
| `sdi_dataset_EL` | QR Code, Building, Approved | flow_quantity (EL pipeline) |
| `sdi_print_out` | QR Code, Building | flow_quantity (ticket requested) |
| `sdi_print_out_arch` | QR Code, Building | flow_quantity (archived) |
| `new_device` | tag, building, status, trade, `Attribute Set`, `Planon Code`, + Planon checklist cols | FLS assets CRUD, fls_chart; `Attribute Set` defaults to `FireAlarmDevice` |
| `dictionary_assets` | main_asset, asset_group, attribute | dictionary management |
| `json_files` | code | ai_status tracking |

---

## Responsive Design Framework

### CSS (`responsive-design.css`)

**Breakpoints**:
| Name | Range |
|------|-------|
| Mobile | < 576px |
| Tablet | 576px â€“ 991px |
| Desktop | 992px â€“ 1199px |
| Large Desktop | â‰¥ 1200px |

**Key CSS Custom Properties** (defined in `:root`):
- Colors: `--ubc-blue`, `--ubc-blue-light`, `--ubc-blue-dark`, `--light-grey`, `--success-green`, `--error-red`
- Spacing: `--space-xs` through `--space-xxl` (fluid `clamp()` values)
- Typography: `--font-size-body` through `--font-size-h1` (fluid `clamp()` values)
- Radius: `--radius-sm` through `--radius-xl`
- Shadows: `--shadow-sm` through `--shadow-lg`
- Transitions: `--transition-fast`, `--transition-normal`, `--transition-slow`

### JavaScript (`responsive-utils.js`)

Exposes `window.ResponsiveManager` with:
- `init()` â€” auto-called on DOMContentLoaded
- `getInfo()` â€” returns current viewport state
- `subscribe(callback)` â€” listen for resize/orientation events
- `getViewportName()` â€” returns `"mobile"`, `"tablet"`, `"desktop"`, `"large"`

Also includes `TouchManager`, `PerformanceMonitor`, `ImageOptimizer`, `makeChartsResponsive()`, `makeTablesResponsive()`.

---

## Common Development Tasks

### Adding a New Dashboard View
1. Add a `<div id="new-view">` section to `dashboard.html`
   - **Important**: Ensure this div is a direct child of `<main id="main">` (or sibling to other views). **Do not nest views** inside other view containers.
2. Add a sidebar navigation item calling `showView('new-view')`
3. Add CSS styles following existing `analytics-card` / `pipeline-card` patterns
4. Add Flask route(s) for any API data the view needs
5. Add JavaScript fetch logic to populate the view

### Adding a New Chart Module
See workflow: `workflows/add_chart_module.md`

### Modifying Filters
- Building filter options come from `approval.building_options()` or direct DB queries
- Year/Month filters are generated client-side from data
- Status filters use `_normalize_status_filter()` pattern

### Modifying FLS Asset Columns
1. Add the column to the `new_device` table via `_ensure_new_device_columns()`
2. Add the column to the `add_fls_assets()` INSERT/UPDATE query
3. Add the column header to the FLS table in `dashboard.html`
4. Add the column to `get_fls_asset_data()` response
5. Update the add/edit modal form if needed
6. Preserve the Planon Code rule: populated `Planon Code` rows remain editable but are blocked from delete and bulk selection
