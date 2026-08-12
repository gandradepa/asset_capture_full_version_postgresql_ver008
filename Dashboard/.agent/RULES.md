# Dashboard Coding Rules & Standards

Current documentation refresh: 2026-04-28.

## Python (Backend)

### Flask Routes
- **Every route** under `main_bp` must have `@login_required` decorator
- Use `request.args.get('param', 'default')` for query params with safe defaults
- Return JSON via `jsonify()` for API endpoints, `render_template()` for pages
- Return chart images via `Response(data, mimetype='image/png')`

### Chart Modules
- Place new modules in `charts/` with `__init__.py` already in place
- Export a public `render_chart_png()` function returning `bytes`
- Wrap the import in `Asset_portal_dashboard.py` with `try/except` and a `*_AVAILABLE` boolean flag
- Use `matplotlib.use("Agg")` for headless rendering â€” never call `plt.show()`
- Always call `plt.close(fig)` after saving to buffer to prevent memory leaks

### Database Access
- Always use `sqlite3.connect()` with `with` context manager or explicit `conn.close()`
- Never hardcode paths â€” use `os.getenv("DASHBOARD_DB_PATH", default_path)`
- Handle both Linux (`/home/developer/...`) and Windows paths via the path-resolution block at app startup
- Use parameterized queries (`?` placeholders) to prevent SQL injection
- Never expose raw SQL errors to the client â€” catch and return generic error messages

### Error Handling
- Wrap all data-loading functions in `try/except`
- Log errors with `print(f"WARNING: ...")` or `print(f"CRITICAL: ...")`
- Return empty bytes (`b''`) or empty lists (`[]`) on failure â€” never crash the app

### Disposed Assets (`disposed_assets_service`)
- **Permission gate**: reads (`lookup`, register list, detail) require only `@login_required`; **both mutations** (dispose, restore) require `@require_permission(*DISPOSAL_PERM)` = `("operations", "disposed_assets", "editor")`. The RBAC key lives in `auth_service/app_registry.py`
- **Use `editor`, never `admin`, as a required level** (2026-08-12 fix): the User Admin matrix and `api_admin_permissions_put` accept only `viewer`/`editor`, so an `admin` requirement cannot be granted through the UI and locks everyone out — the `is_admin` flag is a role for an item, never a wildcard
- **Reason codes** live in `DISPOSAL_REASONS` and are mirrored by `chk_disposed_reason`; widen both together or the transaction aborts at COMMIT
- **Service layer**: transaction logic lives in `Dashboard/disposed_assets_service.py`, deliberately free of Flask imports so it can be exercised directly. Routes pass `modified_by` and own the `commit()`
- **Graceful degradation**: the import is wrapped in `try/except` (`DISPOSAL_AVAILABLE`) like the chart modules, and every query is `qrdb.has_table`-guarded, so a server without the migration returns a clear 503 instead of failing
- **One transaction, no file writes**: snapshot -> `INSERT disposed_assets` -> `DELETE` the curated row (rowcount asserted) -> normalize `ai_status` -> audit -> commit. Photos and `Output_jason_api` payloads are never moved or deleted; that is what keeps disposal atomic and Restore possible
- **Audit in-transaction**: use `audit.logger.log_change` directly with `app_name="dashboard_disposed"`, NOT `_log_dictionary_audit()` (which opens its own connection and swallows errors)
- **Never write `ID_check`** when restoring an EL row - it is a generated column. Restore intersects snapshot keys with the live `table_columns` and drops it
- **Frontend must not use `fetchAdminJson`**: that helper is rendered only inside `{% if is_dashboard_admin %}`, so an RBAC-granted non-allowlist user would hit "fetchAdminJson is not defined". The tool ships its own `apiJson`
- **New SPA views must be added to the default-hidden CSS rule** at the top of `dashboard.html` (`#main-view, #analytics-view, ... { display: none; }`), or the view renders over the dashboard until the view-switch JS runs

### Life Cycle Assessment (`life_cycle` blueprint)
- **Permission gate**: routes enforce the RBAC key section `operations`, item `lifecycle_assessment` (added to `auth_service/app_registry.py`) via `has_permission` / `require_permission` (same model as FLS Devices - enforced server-side; the sidebar link itself is not visibility-gated). Granted per-user (viewer/editor) through the Dashboard User Admin screen. `/life-cycle/health` is the only open route (no auth)
- **Env-driven DB DSN**: the connection is derived from a single source - env var `LIFE_CYCLE_DSN` (libpq DSN) if set, else the portal's `QR_PG_DSN`, else the dev sandbox default. The SQLAlchemy URL `LIFE_CYCLE_SA_DSN` (used by `track_assets.py` and `load_life_cycle.py`) is derived from that libpq DSN at blueprint import - never hardcode `DB_URL`
- **Destructive refresh**: `POST /life-cycle/refresh` drops/rebuilds `life_cycle` + `space_floor` and requires DDL privileges for the `assetcap_app` DB user. `life_cycle_meta` survives the rebuild. Treat as a destructive operation
- **Graceful registration**: the blueprint is registered in `Asset_portal_dashboard.py` inside `try/except` so a missing dependency degrades to "feature absent" without crashing the portal
- **Shell sidebar nav**: the "Life Cycle Assessment" item lives in the shared shell sidebar (`Dashboard/static/shell/shell.js`) under the "Operations" group, directly below "FLS Devices", icon `activity`. The nav entry + breadcrumb (Home / Operations / Life Cycle Assessment) must stay in sync across all five `shell.js` copies (Dashboard, ME/BF/EL reviewers, SDI Process); cache-bust shell assets via the `?v=` query in each app's `_shell.html`

---

## HTML / CSS (Frontend)

### Template Structure
- `dashboard.html` is a **single-page application** â€” do NOT create separate pages for dashboard subsections
- New views: add a `<div id="new-view">` and register in the sidebar via `showView('new-view')`
- **View Hierarchy**: All views must be top-level siblings within the main container. **Never nest** one view `div` inside another.
- Use semantic HTML5 elements where appropriate (`<section>`, `<article>`, `<nav>`)

### CSS Rules
- **Use CSS custom properties** from `responsive-design.css`:
  - Colors: `var(--ubc-blue)`, `var(--ubc-blue-light)`, `var(--primary-dark)`, `var(--primary-accent)`
  - Spacing: `var(--space-sm)` through `var(--space-xxl)`
  - Shadows: `var(--shadow-sm)`, `var(--shadow-md)`, `var(--shadow-lg)`
  - Radius: `var(--radius-sm)` through `var(--radius-xl)`
- **Never use inline styles** except for dynamic values (e.g., computed widths)
- **Mobile-first**: Write base styles for mobile, then add `@media (min-width: ...)` overrides
- Use existing class patterns: `.pipeline-card`, `.analytics-card`, `.section-title`, `.ubc-btn`
- Charts/images inside cards must have `max-width: 100%; height: auto;`

### Responsive Breakpoints
| Token | Width |
|-------|-------|
| Mobile | < 576px |
| Tablet | â‰¥ 576px |
| Desktop | â‰¥ 992px |
| Large | â‰¥ 1200px |

---

## JavaScript (Frontend)

### General
- **Vanilla ES6+ only** â€” no frameworks (React, Vue, etc.)
- Use `fetch()` for API calls, always handle errors with `.catch()`
- Use `async/await` pattern for cleaner async code
- Access viewport utilities via `window.ResponsiveManager`

### View Navigation
- Use `showView('view-id')` â€” never manipulate `display` directly
- Update the sidebar active state when switching views

### Data Tables
- Use `document.createElement` or template literals for dynamic rows
- Always sanitize user-provided text before inserting into HTML (prevent XSS)
- For large tables, implement client-side pagination or virtual scrolling

### Export Functions
- PDF: Use `jsPDF` + `jspdf-autotable`
- Excel: Build CSV or use SheetJS if available
- Always add proper headers and formatting

---

## File Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Chart module | `snake_case.py` | `completeness_score.py` |
| Template | `snake_case.html` | `map_new_assets_by_building.html` |
| CSS file | `kebab-case.css` | `responsive-design.css` |
| JS file | `kebab-case.js` | `responsive-utils.js` |
| Log files | Timestamped `YYYYMMDD_HHMMSS_type.log` | `20260219_120000_ME.log` |

---

## Git & Deployment

- Never commit `venv/`, `__pycache__/`, `.env` files, or `logs/*.log`
- Keep `requirements.txt` updated when adding new Python packages
- Test on both Windows (local dev) and Ubuntu (production server)
- Production runs via `gunicorn` behind a reverse proxy; dev uses `app.run(debug=False)`

---

## Security Checklist

- [ ] All routes under `main_bp` have `@login_required`
- [ ] No raw SQL concatenation â€” use parameterized queries
- [ ] No sensitive data (API keys, passwords) in source code â€” use `.env`
- [ ] User input is sanitized before database operations
- [ ] File paths from user input are validated via `_safe_log_path()` pattern
- [ ] CSRF protection enabled via Flask-WTF or manual tokens for POST routes
