# Dashboard Rules

Current documentation refresh: 2026-07-14.

## Purpose

The dashboard is the operational control plane for charts, workflow visibility, extraction launchers, and related maintenance views.

## Backend Rules

- Use `render_template()` for pages, `jsonify()` for APIs, and PNG `Response` objects for chart endpoints.
- Chart modules in `Dashboard/charts/` should export `render_chart_png() -> bytes`.
- Return a valid image empty state for no-data chart cases when the UI expects an image.

## Data Quality Rules

- The current Operational Performance Analysis chart is `Data Quality Comparison`.
- It compares completeness and AI confidence by asset type on a shared `0-100` scale.
- The chart respects `process_scope=all|open`.
- `All` is the default scope on first load.
- Historical route/module identifiers may still include `cost` for backward compatibility; visible UI labels should read `Performance Analysis`.

## Completeness Rules

- Dashboard completeness analytics must follow the same current discipline rules as the extraction layer.
- Do not reconstruct completeness using stale fixed field counts when the underlying rules have changed.

## Confidence Rules

- Use top-level `Avg_ai_conf` when available.
- Use the discipline-aware fallback only when top-level confidence is absent.
- Do not count excluded EL fields in the dashboard average logic.

## Frontend Rules

- Keep dashboard interactions framework-free unless the existing page already uses a specific client library.
- Preserve current SPA-like navigation behavior inside `dashboard.html`.
- If a chart filter persists in the URL or local page state, the rendered chart must honor the same state after reload.

## Review-Dashboard Integration Rules

- Review dashboards now expose `Avg AI Conf`.
- Confidence slicer behavior must stay synchronized between frontend filtering and backend filtering.
- UI changes that alter column order must update any DataTables index logic that still relies on column positions.

## Validation Checklist

- Building and process-scope filters refresh the intended chart.
- Chart image endpoints never break into a browser broken-image icon on valid empty states.
- Analytics for ME, BF, and EL match the current completeness and confidence rules.

## FLS Chart Rules

- FLS charts use Altair for interactive visualization.
- The `fls_chart` module is imported with fallback between local and package imports.
- FLS chart availability depends on the `altair` package being installed.
- If Altair is unavailable, FLS features degrade gracefully and flag `FLS_CHARTS_AVAILABLE = False`.

## Map and SDI Flow Chart Rules

- The `map_chart` module visualizes assets distributed by building location.
- The `flow_quantity_chart` module shows SDI flow quantity analytics.
- SDI Live Pipeline review-state cards count distinct base QR codes from `QR_code_assets.Col_process` (`0` New Assets, `1` Update Existing, `2` Manual Entry).
- The SDI Live Pipeline flow intentionally omits discipline cards; it shows `SDI Queue`, `Requested`, and `Into Planon`.
- Dashboard should embed the SDI Live Pipeline iframe without an extra card wrapper; the chart template owns the only framed surface.
- The SDI Live Pipeline iframe should be left-aligned with the dashboard content instead of auto-centered inside its iframe.
- The lower SDI flow stage cards use captions only, not progress bars.
- Both are optional imports; unavailability should not crash the Dashboard.

## FLS Asset CRUD Rules

- FLS assets are managed through the `new_device` table.
- Dashboard provides add, delete, and bulk update routes for FLS asset records.
- FLS assets store `new_device."Attribute Set" = FireAlarmDevice`; the corresponding `"Attribute"` row label is `Electrical/FLS - Fire Alarm Device`.
- Rows with a populated `Planon Code` must remain editable, while delete actions and bulk row selection stay disabled.
- Planon checklist columns (`Request Open`, `Request Date`, `Elapsed Time`, `Complete`, `Ticket Number`) are auto-migrated at startup via `_ensure_new_device_columns()`.
- FLS Control Panel Code/Description is derived from `"UBC - Asset Data Master Info"` by selected building property code and is not persisted to `new_device`.
- If multiple Control Panel rows exist for one property code, show the lowest Code row and flag the multi-match state in the table and forms.
- The New FLS Device Flow table hides Asset Group, Space, and Details; Edit and magnifying-glass detail views still expose them.
- The FLS `Property` filter is the shared searchable **multi-select** (`BuildingMultiselect`; `Dashboard/static/building-multiselect.js`/`.css` — a byte-identical copy of the review apps' component, see `review_apps.rules.md` → "Building Filter Rules"). One instance (`#property-filter`, JS `propertyFilterMs`) is shared across the FLS tabs. Values are property **names**; matching is `filters.propertyValues.includes(asset.property)` in `assetMatchesFilters`; empty selection = all properties. `updatePropertyFilterOptions` keeps the existing except-property faceting and unions in the checked values so a selection never vanishes from the list (2026-07-14).

## Life Cycle Assessment Rules

- Life Cycle Assessment is an in-process Flask Blueprint (`life_cycle_bp`, package `Dashboard/life_cycle/`) mounted inside the Dashboard app at `url_prefix="/life-cycle"`. It is not a separate service or port.
- Blueprint registration is wrapped in `try/except` so a missing dependency degrades to "feature absent" without crashing the portal.
- Routes are gated by the `operations` / `lifecycle_assessment` permission (added to `auth_service/app_registry.py`) enforced server-side via `has_permission` / `require_permission`, the same model as FLS Devices. The sidebar link itself is not visibility-gated; access is granted per-user (viewer/editor) through the Dashboard User Admin screen.
- The shared shell sidebar (`Dashboard/static/shell/shell.js`) exposes a `Life Cycle Assessment` item in the `Operations` group directly below `FLS Devices` (icon `activity`); it links to the `/life-cycle/` standalone full page. Propagate this nav entry and breadcrumb to all five `shell.js` copies and cache-bust via the `?v=` query in each app's `_shell.html`.
- The DB connection is single-source: env var `LIFE_CYCLE_DSN` (libpq DSN) if set, else the portal's `QR_PG_DSN`, else the dev sandbox default. The SQLAlchemy URL `LIFE_CYCLE_SA_DSN` (used by `track_assets.py` and `load_life_cycle.py`) is derived from that libpq DSN at blueprint import.
- `POST /life-cycle/refresh` is destructive: it drops and rebuilds `life_cycle` + `space_floor` and requires DDL privileges for the `assetcap_app` DB user. `GET /life-cycle/health` is open (no auth) as a liveness probe; `GET /life-cycle/` and `POST /life-cycle/export` require login plus the `lifecycle_assessment` viewer permission.

## Dictionary Management Rules

- Dictionary editing from the Dashboard uses `read_dictionary()` / `save_dictionary()`.
- Read uses `ast.parse()` and `ast.literal_eval()` â€” never `eval()`.
- Save produces sorted, deterministic JSON output written with `encoding='utf-8'`.
- The mechanical dictionary file at `dictionary/mechanical_dictionary.py` is the target.
- The delete-confirmation dialog (`#deleteModal`) and other dictionary-management modals use `modal-dialog modal-dialog-centered` so the dialog renders centered in the viewport rather than clipped at the top.

## Photo API Rules

- `/api/asset-photo/<qr_code>` serves captured asset photos from `Capture_photos_upload/`.
- Photo path resolution supports both Windows development and Ubuntu production paths.

## Chained AI Launcher Rules

- AI extraction tasks now use `run_ai_and_sync.sh` which chains AI â†’ DB sync automatically.
- The separate `update_db` manual task has been removed from the Dashboard launcher.
- Log output for chained tasks includes both AI processing and DB sync output.

## Structured AI Check Log Viewer Rules

- `GET /logs/read?name=ai_check.log` defaults to `mode=runs`; `mode=summary` and `mode=raw` remain supported.
- The Runs view is produced by the deterministic `Dashboard/ai_check_log_parser.py` parser. It must not call OpenAI or transmit operational log content to an external model.
- Parse only the configured recent window (`AI_CHECK_RAW_WINDOW_HOURS`, default 72 hours), group accepted wrapper routines, and preserve EL/BF/ME stage boundaries.
- Interleaved `Previous ai_check run still active ... Skipping this cycle` notices belong to the active routine and must not create a new routine.
- Routine statuses are `Success`, `Needs attention`, `Failed`, `No work`, `Running`, and `Incomplete`.
- Warnings, missing images, and quota errors require attention but do not mark a stage failed when its exit code is zero. Nonzero stage exits and wrapper failures are failures.
- Deduplicate repeated timestamped/plain messages only in structured fields. Routine raw output remains unchanged and chronological; full-log Download remains byte-for-byte unchanged.
- Runs are ordered newest first, paginated at 20 per page, and support `page`, `status`, and `q` query parameters. Search covers QR codes, model names, hosts, PIDs, and messages.
- Keep the first visible routine expanded. Keep configuration/diagnostics and routine raw output collapsed until requested.
- Non-AI logs continue using the generic Summary/Raw viewer.

## Embedded Sub-App Shell Rules

The Dashboard hosts ME, BF, EL, and SDI as embedded iframe panels. Treat each tab as an isolated browsing context that shares only the auth session.

### View routing

- Process tabs are hash-routed: `#review-me-view`, `#review-bf-view`, `#review-el-view`, `#sdi-view`.
- Every iframe-backed view ID must be present in the `views` array, the `viewZoom` map, and `IFRAME_VIEW_MAP` in `dashboard.html`.
- `IFRAME_VIEW_MAP` must be defined after the iframe `<div>` panels so `document.getElementById(...)` resolves at script execution time.

### Lazy loading

- iframes carry `data-src` instead of `src` at render time, and `src` is set to `about:blank`.
- `maybeLoadIframe(viewId)` sets `iframe.src = data-src` once per session and tracks loaded views in `iframeLoaded` (a `Set`). Iframes do not reload when the user revisits the tab.
- Call `maybeLoadIframe(target)` at the end of `handleViewSwitch()`.

### `<main>` width switching

- Default views render inside Bootstrap `.container` (constrained width).
- FLS view applies `.container-fluid-fls` (full-width with 2rem padding).
- Iframe views apply `.container-iframe-view` (full-width, edge-to-edge, zero horizontal padding) so the iframe fills the viewport like the standalone sub-app does.
- Use `mainEl.classList.toggle(...)` based on whether `target` is in `IFRAME_VIEW_MAP` or equal to `'fls-assets-view'`.

### Process-view-header

- Each iframe view starts with a `process-view-header` strip containing the page title, a `Dashboard` button (back to main view), and an `Open full page` button (target=`_blank`).
- The `Dashboard` button uses an `id="<prefix>-back-to-main"` selector. Wire all such buttons through one `querySelectorAll(...)` loop that calls `history.replaceState(null,'',pathname)` plus `handleViewSwitch()`.

### Cross-origin parent navigation

- The Dashboard listens for `message` events whose `data.action === 'go-to-main'` and validates `event.origin` against an explicit `allowedOrigins` array of every embedded sub-app subdomain. Always extend this array when adding a new embedded app.

### Launch-App card behavior

- The `apps` loop must route the `Launch App` button for embedded keys (`review_me`, `review_bf`, `review_el`, `sdi_process`) to the in-page hash view rather than `target="_blank"`.
- Non-embedded apps (e.g. `capture`) keep `target="_blank"` to the external domain.

### Cookie + CSP coupling

- Dashboard must set `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, and the matching remember-cookie triple.
- Nginx for the Dashboard sets `Content-Security-Policy: frame-ancestors 'none';` to prevent third-party embedding.

### Cache invalidation

- After any change to `Dashboard/templates/dashboard.html`, restart `assetcap-dashboard` and instruct the user to hard-refresh (`Ctrl+Shift+R`). Browsers cache the rendered HTML/JS aggressively and `postMessage` listeners will not be installed without a fresh download.
