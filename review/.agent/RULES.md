# Asset Plate Review â€” Coding Rules & Standards

Current documentation refresh: 2026-06-24.

## Python (Backend)

### Flask Routes
- **Every route** must have `@login_required` decorator (except `/login`, `/logout`, `/health`)
- Use `request.args.get('param', 'default')` for query params with safe defaults
- Return JSON via `jsonify()` for API endpoints, `render_template()` for pages
- Serve images via `send_from_directory()` or `send_file()` â€” never expose raw file paths

### Directory Sync
- Sync functions (`sync_image_directory_to_db`, `sync_json_directory_to_db`) run on `before_request` â€” never remove this hook
- Use processed log files (e.g., `processed_images.log`) to avoid re-processing on every request
- Use `Lock()` for thread-safe sync operations in multi-worker deployments

### JSON File Operations
- Always read JSON with `encoding='utf-8'`
- Write back with `ensure_ascii=False, indent=4` for readable diffs
- Set `content["modified"] = True` whenever any field value changes
- Do NOT overwrite a JSON file with lower-quality data â€” check completeness before writing

### Database Access
- Always use `sqlite3.connect()` with `with` context manager
- Never hardcode paths â€” use `os.getenv()` with fallback candidates
- Handle both Linux (`/home/developer/...`) and Windows paths via path-resolution at startup
- Use parameterized queries (`?` placeholders) to prevent SQL injection
- Check `_connectable()` before any DB operation â€” never crash on missing DB
- Never expose raw SQL errors to the client â€” catch and return generic messages

### Error Handling
- Wrap all data-loading functions in `try/except`
- Log errors with `print(f"Error ...")` or `print(f"Warning ...")`
- Return empty dicts/lists on failure â€” never crash the app
- The `index()` route must have a top-level try/except returning a 500 with traceback

---

## Filtering Rules

### 1. Dual Tag Key Check
When filtering by tag, ALWAYS check both `UBC Asset Tag` and `UBC Tag`:
```python
if filter_tag:
    data = [i for i in data 
            if filter_tag in (i.get("UBC Asset Tag") or i.get("UBC Tag") or "").upper()]
```

### 2. Context-Aware Approved Defaults
- **Process 0 (New) / Process 1 (Update)**: Default `approved_filter` to `"False"` (show Pending)
- **Process 2 (Manual Entry)**: Default `approved_filter` to `""` (show All)

```python
if approved_arg is not None:
    approved = approved_arg
else:
    approved = "" if str(process_target) == "2" else "False"
```

### 3. Navigation Persistence
`save_review()` must:
1. Extract `dashboard_query` from the POST form, parse into `saved_params`, and pass as kwargs to `url_for()` in the redirect so dashboard filters (and `archive`) carry over. `archive` is no longer transient — `TRANSIENT_DASHBOARD_QUERY_KEYS` is empty so it survives the round-trip.
2. Honor the client `nav_next` / `nav_prev` POST fields for Save & Next / Save & Prev (validating the target JSON exists) **before** falling back to the server order. These carry the dashboard's visible, filtered + column-sorted order — the dashboard writes that order to `localStorage('reviewOrder')` and `review.html` sets the hidden fields. The server fallback sort is **Capture Date desc** (was `doc_id`).
3. The per-row Review link is `<a class="v2-btn-review">`; all link-rewriting JS must select `a.v2-btn-review`, never `a.btn-primary` (the latter matches only the modal OK button and silently drops filters/sort/archive).

See `review/.agent/AGENT.md` ("Review Navigation") and `Markdowns_documentation/rules/review_apps.rules.md` for the full mechanics, including the `GET /api/asset-preview/<doc_id>` preview endpoint.

### 5. AI Status Reprocess Guard

`toggle_ai_status(doc_id)` now moves the JSON aside and triggers re-extraction, subject to a protection hierarchy enforced in this order:

1. **Packaged** (`sdi_print_out` / `sdi_print_out_arch`) → `409 Conflict`. Cannot be overridden.
2. **Approved** (`structured_data.Approved == "True"`) → `200 {success: false, code: "reprocess_blocked", forceable: false}`.
3. **Manual Entry** (`QR_code_assets.Col_process = "2"`) → `200 {success: false, code: "manual_entry_locked", forceable: false}`.
4. **Human-edited** (`content["modified"] == True`) → `200 {success: false, code: "reprocess_blocked", forceable: true}`.
5. **Fresh AI result** → JSON moved to `.bak_<UTCstamp>` (AFTER `conn.commit()`), `ai_status` set to `0`, `200 {success: true, reprocess_requested: true}`.

Force bypass: posting `force=1` skips guard #4 only. Guards #2 and #3 are never forceable from the dashboard.

File move must always happen **after** `conn.commit()` to prevent crash-time orphan state (file gone, DB not committed). Path-traversal guard (`os.path.realpath()` + `os.path.commonpath()`) required on the derived `json_path`.

Use `_reprocess_json_protected(json_path)` helper to classify the current protection state before any DB write.

### 4. Archive Filtering
- By default, hide assets whose QR codes appear in `sdi_print_out_arch`.
- Only show archived assets when `?archive=false` is explicitly passed.
- The "Show Archive" state set on the dashboard **persists through the review page** (back-button, Save & Next/Prev, reload): the Review links carry `archive` server-side, `buildDashboardQuery()` preserves it, and `save_review`'s `filter_args` includes it.

### 6. Multi-Value Filter Params (Building & Asset Group)
- `building` / `filter_building` and `filter_group` carry **ordered, de-duplicated comma-joined lists** (single value = legacy form, still valid; empty = no filter).
- Parse with each app's `_parse_filter_values()` (renamed from `_parse_building_codes` on 2026-07-30) and filter by **exact case-sensitive set membership** — never string equality:
```python
filter_groups = _parse_filter_values(query_args.get("filter_group"))
if filter_groups:
    group_set = set(filter_groups)
    data = [i for i in data if (i.get("Asset Group") or "") in group_set]
```
- EL keeps single-value UI for both (building `[:1]` truncation; simple Asset Group select), but its server accepts the list form.

---

## QR Code Operations

### Renaming Rules
- Only temporary QR codes (starting with `T`) can be renamed
- Validate: new code must NOT start with `T`, must be alphanumeric only
- Check for conflicts: existing JSON file, `QR_codes` table, `QR_code_assets` table
- Atomic rename: JSON file â†’ processed log â†’ images â†’ all DB tables
- On any failure, flash an error and abort â€” never leave partial state

### DB Tables to Update on Rename
All of: `QR_codes`, `QR_code_assets`, `sdi_dataset`, `sdi_dataset_EL`, `sdi_print_out`, `sdi_print_out_arch`, `process_type`, `json_files`

---

## HTML / CSS (Frontend)

### Template Structure
- `dashboard.html` is a tabbed SPA â€” New, Update, Manual Entry tabs with server-rendered data
- `review.html` is the single-asset edit form with image viewer, editable fields, navigation controls
- `login.html` is the authentication form
- Use Jinja2 macros from `macros/` for reusable components

### Image Viewer Rules
- ME, BF, and EL review pages use the same `review_asset_templates/static/image-viewer.js` controller.
- Keep wheel zoom, drag-to-pan, double-click detail/reset, zoom/rotate/reset buttons, and keyboard shortcuts (`+`, `-`, `0`, `R`, arrows`) behaviorally aligned across the three apps.
- Thumbnail switches must reset zoom, pan, and rotation state.
- Scope wheel and pointer events to `.main-stage` so the form, thumbnails, iframe scrolling, and map overlay keep their expected behavior.

### CSS Rules
- Use existing class patterns from the template's `<style>` blocks
- Never use inline styles except for dynamic values (e.g., computed widths)
- Ensure tables are responsive on mobile (horizontal scroll or stacked layout)
- Status badges should use consistent colors: green for approved, yellow for pending, red for flagged

### JavaScript Rules
- **Vanilla ES6+ only** â€” no frameworks (React, Vue, etc.)
- Use `fetch()` for AJAX calls (toggle approved, toggle SDI, toggle AI status)
- Always handle errors with `.catch()`
- Use `confirm()` for destructive actions (e.g., QR code rename)

---

## File Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Python main file | `asset_plate_reviewer*.py` or `Asset_dashboard_*.py` | `asset_plate_reviewer_bf.py` |
| Templates | `snake_case.html` | `dashboard.html`, `review.html` |
| JSON output | `<QR>_<TYPE>_<Building>.json` | `0000123456_ME_MAIN.json` |
| Images | `<QR> <Building> <TYPE> - <SEQ>.<ext>` | `0000123456 MAIN ME - 0.jpg` |
| Processed logs | `processed_*.log` | `processed_images_bf.log` |

---

## Consistency Across ME / EL / BF

When modifying any review app:
1. **Check if the change applies to all three** â€” filter logic, toggle endpoints, navigation, and template structure should stay in sync
2. **Use the same function names** for equivalent operations across apps
3. **Keep dictionary lookup priority identical**: exact composite â†’ prefix composite â†’ legacy simple key
4. **Keep image sequence tags consistent across apps**:
   - **ME** required `-0..-3` (Asset Plate / UBC Tag / Main Asset Photo / Technical Safety BC) + optional `-4` Extra Photo
   - **BF** required `-0..-2` (Asset Plate / Asset Plate (additional) / Main Photo) + optional `-3` Extra Photo
   - **EL** required `-0..-2` (Asset Plate / UBC Asset Tag / Panel Schedule) + optional `-3` Extra Photo
   - The Extra Photo seq is always present in `SEQ_SHOW` / `ALL_SHOW` (so it renders in thumbnail strips and pagination) but absent from `SEQ_CHECK` / `REQUIRED` (so it never affects "Missed Photo" or completeness). The item dict carries an `Extra Photo` boolean for the `+1` chip in the Photo column.
5. **Never add features to one app without considering the other two**

---

## Security Checklist

- [ ] All routes (except login/logout/health) have `@login_required`
- [ ] No raw SQL concatenation â€” use parameterized queries
- [ ] No sensitive data (API keys, passwords) in source code â€” use `.env`
- [ ] User input is sanitized before database operations (`_sanitize_qr_value()`)
- [ ] File paths from user input are validated before file operations
- [ ] Session cookies configured with secure domain via `auth_service.env`

---

## Git & Deployment

- Never commit `venv/`, `__pycache__/`, `.env` files, or `*.log` files
- Keep `requirements.txt` updated when adding new Python packages
- Test on both Windows (local dev) and Ubuntu (production server)
- Production runs via `gunicorn` behind Nginx reverse proxy; dev uses `app.run(debug=True)`
