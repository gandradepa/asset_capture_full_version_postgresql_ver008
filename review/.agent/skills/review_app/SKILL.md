---
name: review_app
description: Developer skill guide for the UBC Asset Plate Review applications (ME, EL, BF). Covers project structure, Flask routes, JSON data lifecycle, directory sync, dictionary lookup, QR code management, filtering, toggle patterns, GPS service, and common development tasks.
---

# Asset Plate Review Application Skill

Current documentation refresh: 2026-06-25.

## Use this skill when
- Adding, modifying, or debugging Flask routes in any of the three Review Apps (ME, EL, BF)
- Modifying the frontend UI (`dashboard.html`, `review.html`), filtering logic, or tag dictionary application
- Updating the directory sync loops (`sync_json_directory_to_db`, `sync_image_directory_to_db`)
- Debugging the atomic QR rename logic

## Do not use this skill when
- Modifying the core API extraction scripts (refer to `API/.agent` instead)
- Modifying the Dashboard or Capture apps (refer to their respective `.agent` folders)

## Instructions
Review the project structure and routing catalog before implementing feature changes. Remember that changes made to one review app (e.g., ME) must often be manually replicated to the other two apps (EL, BF) to maintain structural parity.

## Project Structure (Per App)

Each review app follows the same pattern. Using ME as the reference:

```
Asset_dasboard_browser_ME/
â”œâ”€â”€ asset_plate_reviewer.py         # Flask app entry point
â”œâ”€â”€ gps_service.py                  # GPS coordinate blueprint
â”œâ”€â”€ requirements.txt                # Python deps (Flask)
â”œâ”€â”€ review_asset_templates/         # Jinja2 templates
â”‚   â”œâ”€â”€ dashboard.html              # Main tabbed dashboard (~95K)
â”‚   â”œâ”€â”€ review.html                 # Single asset edit form (~43K)
â”‚   â”œâ”€â”€ login.html                  # Authentication form (~5K)
â”‚   â”œâ”€â”€ macros/                     # Reusable Jinja2 macros
â”‚   â””â”€â”€ static/                     # CSS, JS, images
â””â”€â”€ venv/                           # Virtual environment
```

### Script Mapping

| App | Main Script | Port |
|-----|-------------|------|
| ME  | `asset_plate_reviewer.py` | 5002 |
| BF  | `asset_plate_reviewer_bf.py` | 5003 |
| EL  | `Asset_dashboard_EL.py` | 5004 |

---

## Flask Route Catalog

### Authentication Routes (`auth` Blueprint)

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET/POST | `/login` | `login()` | Login form + credential validation |
| GET | `/logout` | `logout()` | End session, redirect to login |

### Main Application Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/` | `index()` | Dashboard with 3 tabs: New, Update, Manual Entry |
| GET | `/review/<doc_id>` | `review(doc_id)` | Single-asset review page with images + form |
| POST | `/review/<doc_id>` | `save_review(doc_id)` | Save edits, QR rename, next/prev navigation |
| GET | `/review/<doc_id>/print` | `review_print(doc_id)` | Print-optimized Asset Review Sheet (auto-print -> Save-as-PDF) |
| GET | `/review/<doc_id>/export` | `review_export(doc_id)` | Download the Asset Review Sheet as a self-contained `.html` file |
| GET | `/api/asset-preview/<doc_id>` | `asset_preview(doc_id)` | Lightweight JSON (QR, tag, location, thumbnails) for the client-driven Next Asset preview rail |

### Toggle API Routes

| Method | Path | Function | Returns |
|--------|------|----------|---------|
| POST | `/toggle_approved/<doc_id>` | `toggle_approved()` | `{"success": true, "new_value": "True"/""}` |
| POST | `/toggle_ai_status/<doc_id>` | `toggle_ai_status()` | `{"success": true, "new_value": "0"/"1"}` |
| POST | `/toggle_sdi/<doc_id>` | `toggle_sdi()` | `{"success": true, "new_value": 0/1}` |

### Data API Routes

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET | `/check_sdi/<qr_code>` | `check_sdi()` | Check SDI print-out existence |
| GET | `/api/ai_status_map` | `ai_status_map()` | Read-only `{QR: ai_status}` map polled by the dashboard to auto-refresh AI Status cells |
| GET/POST | `/api/user-activity` | `get_user_activity()` | User scan activity data |
| GET | `/api/building_location/<code>` | `api_building_location()` | GPS coordinates |
| GET | `/health` | `health()` | Health check |
| GET | `/images/<filename>` | `serve_image()` | Serve asset photos |

---

## Asset Review Sheet (PDF / Export) — ME / BF / EL

The review header has **PDF** and **Export** buttons (all three apps) that produce a one-page, self-contained **Asset Review Sheet** for the open asset.

- `review_print` renders `review_print.html` with `auto_print=True` (inline, opens the browser print dialog → Save-as-PDF); `review_export` renders it with `auto_print=False` and `send_file`s it as a download (`Asset_Review_<ME|BF|EL>_<qr>_<building>.html`).
- One shared context builder per app (`_build_review_sheet_context` for ME and BF, `_build_el_sheet_context` for EL) + one `review_print.html`, toggled by `auto_print`. Same `viewer` permission as `review()`; **read-only**.
- **Self-contained:** photos and the UBC logo are inlined as base64 `data:` URIs via `_file_data_uri()` — no `url_for` / `/static/` / `/images/` / CDN refs. Building shown as `Buildings."Name"`.
- Layout: **Description first (above Identity)** on all three. EL adds Technical Details + the SLD strip; ME/BF show their nameplate fields. Hero photo = the discipline's main image (`-2`).
- QR-level extras (2026-07-10): read-only **Installation Date** (`QR_codes.installation_date`, `DD/MM/YYYY`) below Year (ME Identity / BF Classification; EL: below Main Asset in Identity), and a read-only **Capture Notes** section (`QR_codes.capture_notes`) between the two-column block (Identity/Classification for ME/BF; Identity/Technical Details for EL) and the next section ("No capture note" when blank).

---

## JSON Data Lifecycle

### 1. Creation (by API Extraction Scripts)
```
API script â†’ Output_jason_api/<QR>_<TYPE>_<Building>.json
```

### 2. Sync (before_request)
```python
sync_json_directory_to_db()   # Register new JSON files in QR_code_assets
sync_image_directory_to_db()  # Register new images in QR_codes
```

### 3. Loading (index route)
```python
load_json_items(process_target="0")
    â†’ Read JSONs from Output_jason_api/
    â†’ Filter by _is_me_filename (or _is_bf/_is_el)
    â†’ Apply dictionary rules
    â†’ Resolve description
    â†’ Enrich with DB lookups (location, SDI, dates, ai_status)
    â†’ Filter by Col_process matching process_target
```

### 4. Filtering (dashboard)
```python
get_filtered_data_and_counts(query_args, process_target)
    â†’ Apply: flagged, modified, missed, building, approved, archive, QR, date, tag, group
    â†’ Return (filtered_data, base_data)
```

### 5. Review & Save (review page)
```python
save_review(doc_id)
    â†’ Read JSON â†’ Apply form edits â†’ Re-apply dictionary rules
    â†’ Handle QR rename (if applicable)
    â†’ Write JSON â†’ Upsert sdi_dataset â†’ Navigate next/prev
```

---

## Directory Sync Logic

### Image Sync Pattern
```python
def sync_image_directory_to_db():
    # 1. Read processed log (set of already-processed filenames)
    # 2. Scan IMG_DIR for files matching IMG_NAME_RE
    # 3. For each new file: register QR in QR_codes + QR_code_assets
    # 4. Append to processed log
```

### JSON Sync Pattern
```python
def sync_json_directory_to_db():
    # 1. Read processed JSON log (dict of filename â†’ timestamp)
    # 2. Scan JSON_DIR for files matching type filter
    # 3. For each new file: auto-register QR code
    # 4. Update processed log
```

### Filename Regex Patterns
| App | Image Pattern | JSON Filter |
|-----|---------------|-------------|
| ME | `^([A-Za-z0-9]+)\s+(.+?)\s+ME\s+-\s+[0-4]\.(?:jpe?g\|png)$` | `_is_me_filename()` checks `_ME_` in name |
| BF | `^([A-Za-z0-9]+)\s+(.+?)\s+BF\s+-\s+[0-3]\.(?:jpe?g\|png)$` | `_is_bf_filename()` |
| EL | `^(\d+)\s+(.+?)\s+EL\s+-\s+[0-3]\.(?:jpe?g\|png)$` | `_is_el_filename()` |

The widened ranges (ME `[0-4]`, EL `[0-3]`) accommodate the optional **Extra Photo** sequence captured by the mobile app. Sequence `SEQ_SHOW` / `ALL_SHOW` includes the extra index so the file renders in the review thumbnail strip; `SEQ_CHECK` / `REQUIRED` is unchanged so the missing-photo logic ignores it.

---

## Dictionary Lookup Patterns

### Mechanical Dictionary (ME, BF)
```python
apply_dictionary_rules(data, asset_type="ME")
    # Priority:
    # 1. Exact composite key match: "AHU-100|ME"
    # 2. Composite prefix match: "AHU|ME"
    # 3. Legacy simple key match: "AHU"
    # Sets: Asset Group, Attribute, Description
```

### Electrical Dictionary (EL)
```python
_apply_tag_dictionary_first(data, asset_type="EL")
    # Uses label_schema for Volts/Location parsing
    # Uses mechanical dictionary for Asset Group/Attribute fallback

_derive_volts_loc(tag)
    # Parses hyphenated (CDP-6-N-1-...) and compressed (PNL-2N3L1) formats
    # Returns (volts, location) tuple
```

### Description Resolution
```python
_resolve_description(asset_group, ubc_tag, existing_desc, asset_type)
    # Priority:
    # 1. Keep existing non-empty description
    # 2. Dictionary description (composite â†’ prefix â†’ legacy)
    # 3. Fall back to Asset Group
    # 4. Fall back to UBC tag
    # Format: "Description - Tag"
```

---

## QR Code Management

### Conflict Check
```python
_qr_conflicts(new_qr, asset_type, building)
    # Checks: JSON file exists? QR_codes table? QR_code_assets table?
```

### Atomic Rename
```python
# 1. Validate: temp code (T*), alphanumeric, no conflicts
# 2. Rename JSON file
# 3. Update processed log
# 4. Rename all image files
# 5. Update all DB tables via _replace_qr_in_db()
```

### DB Tables Updated on Rename
`QR_codes` â†’ `QR_code_assets` â†’ `sdi_dataset` â†’ `sdi_dataset_EL` â†’ `sdi_print_out` â†’ `sdi_print_out_arch` â†’ `process_type` â†’ `json_files`

---

## Tab Navigation & Filtering

### Process Tab Mapping
| Tab | URL Param | `Col_process` | Default Approved |
|-----|-----------|---------------|------------------|
| New | `?process=0` | `0` | `"False"` (Pending) |
| Update | `?process=1` | `1` | `"False"` (Pending) |
| Manual Entry | `?process=2` | `2` | `""` (All) |

### Filter Parameters (query string)
| Param | Type | Description |
|-------|------|-------------|
| `building` / `filter_building` | string | Building code filter, matched by set membership. ME/BF: comma-joined list from the multi-select (e.g. `122-1,633`); single code = legacy form, still valid. EL: single code (single-select; server keeps the first code only) |
| `approved` / `filter_approved` | `True`/`False`/`""` | Review status filter |
| `flagged` | `true` | Show only flagged assets |
| `modified` | `true` | Show only modified assets |
| `missed` | `true` | Show only assets with missing photos |
| `archive` | `false` | Show archived assets (default: hidden) |
| `filter_qr` | string | QR code search (substring match) |
| `filter_date` | string | Capture date prefix match |
| `filter_tag` | string | UBC tag search (dual-key, uppercase) |
| `filter_group` | string | Asset Group filter, matched by exact case-sensitive set membership. ME/BF: comma-joined list from the multi-select (e.g. `Air Handling Units,Chillers`); single value = legacy form, still valid. EL: single value from the simple select |

### Navigation Persistence in save_review()
The prev/next sequence follows the **dashboard's visible, filtered + column-sorted order**. The dashboard captures `dt.rows({search:'applied', order:'applied'})` doc_ids into `localStorage('reviewOrder')` (per tab); `review.html` reads it and sets the hidden `nav_prev` / `nav_next` fields. `save_review()` honors those first, falling back to the server order (Capture Date desc) only when absent.
```python
# 1. Read dashboard_query (archive + filters preserved; archive is NOT transient)
dq = request.form.get("dashboard_query", "")
saved_params = normalize_dashboard_query(dq)[1]

# 2. Honor the client-supplied neighbor (dashboard order) before any server fallback
client_nav = request.form.get("nav_next" if action == "save_next" else "nav_prev") or ""
if client_nav and os.path.exists(os.path.join(JSON_DIR, f"{client_nav}.json")):
    return redirect(url_for("review", doc_id=client_nav, **saved_params))

# 3. Fallback: server order (Capture Date desc) within the active filter
filtered_before = get_filtered_data_and_counts(FilterArgs(filter_args), proc_param)
nav_ids = [i['doc_id'] for i in filtered_before]
return redirect(url_for("review", doc_id=nav_ids[idx+1], **saved_params))
```
**Selector + preview:** the per-row Review link is `<a class="v2-btn-review">` — link-rewriting JS must select `a.v2-btn-review` (never `a.btn-primary`, which matches only the modal OK button). `GET /api/asset-preview/<doc_id>` returns `{qr_code, ubc_tag, location, images[]}` for the client-driven Next Asset rail. The "Show Archive" toggle persists across the review round-trip.

---

## GPS Service

Identical `gps_service.py` across all apps:

| Route | Purpose |
|-------|---------|
| `/api/building_location/<code>` | Returns `{success, data: {lat, lng, name}}` |
| `/gps/diagnostics` | HTML page testing DB connection and showing sample GPS data |

**Data Source**: `UBC - All Properties List with GPS Coordinates` table in PostgreSQL `qr_code_db`

---

## Common Development Tasks

### Adding a New Field to the Review Form
1. Add the field to `review.html` template (form input)
2. Handle the field in `save_review()` (form â†’ structured_data)
3. Add default in `load_json_items()` via `data.setdefault("NewField", "")`
4. If filterable, add to `get_filtered_data_and_counts()` and `dashboard.html`
5. If displayed on dashboard, add column to the table in `dashboard.html`
6. Apply to all three apps (ME, EL, BF) if the field is common

### Adding a New Toggle Endpoint
1. Create route: `@app.route("/toggle_newfield/<doc_id>", methods=["POST"])`
2. Read current value from JSON or DB
3. Toggle and write back
4. Return `jsonify({"success": True, "new_value": new_val})`
5. Add JavaScript click handler in `dashboard.html` and/or `review.html`

### Modifying Filter Logic
1. Add the new filter param to `get_filtered_data_and_counts()`
2. Add form/UI control in `dashboard.html`
3. Include in `saved_params` extraction in `save_review()` for navigation persistence
4. **Remember**: dual-key check for tag filters, context-aware approved defaults
