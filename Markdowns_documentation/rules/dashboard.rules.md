# Dashboard Rules

Current documentation refresh: 2026-08-11.

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

## Disposed Assets Rules (2026-08-11)

- The **Disposed** tool (Operations & Monitoring tile `#show-disposed-btn`, SPA view `#disposed-view`, hash `#disposed`) withdraws a QR from the capture → review → SDI pipeline: it archives a full snapshot into `disposed_assets`, deletes the curated `sdi_dataset` / `sdi_dataset_EL` row, and marks the QR as disposed. See `special_processes/04_database_topography.md` → `disposed_assets` for the schema.
- **"Disposed" is membership in `disposed_assets` with `status='disposed'`** — not a flag on `QR_codes`. There is exactly one active disposal per QR, enforced by the partial unique index `ux_disposed_assets_active`; `dispose → restore → dispose` keeps every event as history.
- **Disposal gate (all four checks must pass, re-verified inside the transaction):** the QR exists; it has no active disposal; it is **not approved**; and it is in no SDI package (conservative all-column scan of `sdi_print_out` / `sdi_print_out_arch`). A **reason is always required** — `Decommissioned`, `Duplicated`, `Wrong Asset`, or `User Request`, mirrored by `chk_disposed_reason`. The list is served from `DISPOSAL_REASONS` in `disposed_assets_service.py` and rendered into both the dispose dropdown and the register filter; adding a reason means widening the CHECK constraint in the same workstream (`Wrong Asset` was added 2026-08-12 by `scripts/migrations/2026-08-12_disposed_reason_wrong_asset.sql`), otherwise the disposal transaction aborts at COMMIT. The checks are returned to the UI as a list and rendered verbatim as the eligibility checklist; a refusal returns 409 with the same list.
- **Approval is judged from the curated row** (`sdi_dataset` for ME/BF, `sdi_dataset_EL` for EL) **and the review JSON's `structured_data.Approved` — deliberately NOT from `QR_codes."Approved"`** (2026-08-11 correction). `QR_codes."Approved"` carries stale legacy values that wrongly blocked disposal in production; the curated tables are the authoritative approval source. The failure message names the exact table (`This asset is approved in sdi_dataset. …`).
- The JSON approval check is load-bearing, not belt-and-braces: the review apps write the JSON *before* the database, so an in-flight approval can briefly show `Approved="True"` in the JSON before the curated row lands.
- **No file is ever touched.** Photos and `Output_jason_api` payloads stay on disk, which keeps the whole disposal inside one PostgreSQL transaction and lets Restore rebuild the curated row without regenerating anything. `QR_code_assets` rows are snapshotted but never deleted — removing them would let the reviewers' auto-register path recreate the QR from its JSON on the next page load.
- **`ai_status` normalization:** disposing an unprocessed asset (`ai_status='0'`) sets it to `'1'` inside the same transaction. Otherwise `ai_check.sh`'s pending gate would fire forever and the Dashboard pending badge would never clear. The pre-disposal value is preserved in `qr_codes_row_json` and written back by Restore.
- **Restore** is the counter-transaction, admin-only: it re-inserts the curated row from `sdi_row_json` (columns intersected with the live table, `ID_check` excluded because it is generated, `"Main Asset"` `''` → `NULL` for the FK, `"Approved"` coerced off `'1'`), restores `ai_status`, and flips the history row to `status='restored'`. It refuses with 409 when the QR no longer exists in `QR_codes` (purged) or when a curated row already exists. If the review JSON is gone from disk the restore still succeeds but the response warns that the asset will not reappear in the reviewer until the file is regenerated.
- **Endpoints** (`main_bp`): `GET /api/disposed-assets/lookup/<qr>` (preview + checklist), `POST /api/disposed-assets` (dispose), `GET /api/disposed-assets` (register), `GET /api/disposed-assets/<id>` (detail with snapshots and photo URLs), `POST /api/disposed-assets/<id>/restore`. Reads require only `@login_required` — a deliberate deviation from the FLS viewer-permission precedent so any signed-in user can consult the register; **both mutations require `@require_permission(*DISPOSAL_PERM)` = `("operations", "disposed_assets", "editor")`**.
- **The required level is `editor`, not `admin` (2026-08-12 fix).** The User Admin permission matrix offers only `none` / `viewer` / `editor` and `api_admin_permissions_put` rejects anything else, so an `admin` requirement is ungrantable through the platform's own UI — it locked every user out of disposal, including site administrators, because `has_permission()` treats the `is_admin` flag as a role for an item and never as a wildcard. `editor` is the house mutate level (dictionary, FLS Devices); the grant is still handed out only to administrators. Any new gate must pick a level the matrix can actually grant.
- **Overview charts subtract the disposed set (2026-08-12).** The curated-side numbers (KPI bars, gauge, donut approval split) drop a disposal on their own because the curated row is deleted, but the capture-side readers count `QR_code_assets`, so they need the filter: `charts/approval.py::_scan_capture_universe()` skips disposed QRs — which covers the Manual Entry slice, the chip and `integrity_snapshot()` — and `charts/flow_quantity_chart.py::_review_state_counts()` / `_flow_counts()` do the same for the Pipeline Overview cards, the Total QR pill and the SDI flow steps. **Both modules must subtract the same set**: `integrity_snapshot()`'s reconciliation identity (pipeline − bars) balances only if they agree. Each module keeps its own `_disposed_qrs()` helper (`has_table`-guarded, error-swallowing) so the charts still render on a database without the migration. Without this filter a disposed QR reads as an *orphan* — captured, active, no curated row — and raises a false "unsynced" warning.
- Still counted elsewhere (known, by design for now): the JSON-driven charts behind the Analytics and Performance Analysis views — `charts/ai_status_table_new_version.py`, `completeness_score.py`, `operational_cost_result.py` — enumerate `Output_jason_api/`, which disposal deliberately never touches. Cost analysis arguably *should* keep the spent API calls; revisit per chart.
- Transaction logic lives in `Dashboard/disposed_assets_service.py`, kept free of Flask imports so it can be exercised directly; the route layer passes `modified_by` and owns the `commit()`. The import is wrapped in `try/except` (`DISPOSAL_AVAILABLE`) like the chart modules, and every query is `has_table`-guarded, so a server without the migration degrades to a clear 503 instead of failing.
- **Two entry points:** the Operations & Monitoring tile `#show-disposed-btn` and a `Disposed Assets` item in the shared shell sidebar's **Operations** group (`shell.js`, key `disposed`, icon `archiveX`, directly below `Life Cycle Assessment`), linking to `…/#disposed` — `handleViewSwitch` resolves that hash to `disposed-view`. The sidebar entry lives in all five byte-identical `shell.js` copies (Dashboard, ME / BF / EL reviewers, SDI Process) and the `?v=` cache-bust in every `_shell.html` must be bumped with it (`20260812-1`). Like every other shell link it is not visibility-gated — the server enforces the grant (2026-08-12).
- **Confirmation is OK/Cancel, not type-to-confirm (2026-08-12).** `#disposed-confirm-modal` asks `Dispose asset <QR>?`, restates QR / discipline / building / reason, and posts on `OK`. This deliberately departs from the dictionary delete dialog's retype-the-tag pattern: disposal is reversible by Restore and the eligibility checklist already gates it, so the extra typing bought nothing. `openConfirm()` enables the button; `submitDisposal()` disables it while the POST is in flight.
- The tool's JS defines its own `apiJson` helper rather than reusing `fetchAdminJson`: that helper is rendered only inside the `{% if is_dashboard_admin %}` block, so reusing it would break the page for exactly the RBAC-granted users the tool is for. New SPA views must also be added to the default-hidden CSS rule at the top of `dashboard.html`, or they render over the dashboard until the view-switch JS runs.
- `find_photos_for_qr()` matches the **first space-delimited filename token exactly**. Do not copy `find_photo_for_qr()`'s `startswith` matching, which makes QR `123` pick up `1234 BUCH ME - 2.jpg`.

### Disposed Assets Audit Rules (2026-08-11)

- Disposal and restore audit **inside the caller's transaction** via `audit.logger.log_change` — not through `_log_dictionary_audit()`, which opens its own connection and swallows errors. The audit row and the mutation must land together or not at all.
- Call shape: `app_name="dashboard_disposed"`, `source="human"`, `qr_code=<QR>`, `modified_by=<logged-in user>`.
- Rows written on dispose: `INSERT` on `disposed_assets`, `DELETE` on the curated table carrying the deleted row's old values (only when a curated row existed), and `UPDATE` on `QR_codes.ai_status` when it changed. Restore mirrors this with `INSERT` on the curated table and `UPDATE` on `disposed_assets`.
- `op_type` stays within `INSERT` / `UPDATE` / `DELETE`; there is no `DISPOSE` op type (the `audit_trail` CHECK rejects it).

## Dictionary Management Rules

- Dictionary editing from the Dashboard uses `read_dictionary()` / `save_dictionary()`.
- Read uses `ast.parse()` and `ast.literal_eval()` â€” never `eval()`.
- Save produces sorted, deterministic JSON output written with `encoding='utf-8'`.
- The mechanical dictionary file at `dictionary/mechanical_dictionary.py` is the target.
- The delete-confirmation dialog (`#deleteModal`) and other dictionary-management modals use `modal-dialog modal-dialog-centered` so the dialog renders centered in the viewport rather than clipped at the top.
- Editing is **modal-based**, not inline: `#assetModal` handles add and edit; `#deleteModal` is a type-to-confirm delete (the reviewer must retype the UBC tag before Delete enables). There is no in-row editing.

### Dictionary Page Design and Filters (2026-08-10)

- The page uses the Dashboard's **"Deep Blue & Clean Slate"** token block (the same `:root` as `dashboard.html`: `--primary-dark #002145`, `--primary-accent #0055b7`, `--secondary-bg`, semantic bg/text pairs, `--card-radius`, `--btn-radius`, shadow scale) with Inter as the only typeface. The former `#0066ff`/`#00ffff` "Tech Innovation" palette and Space Grotesk are gone; do not reintroduce a page-local palette that diverges from the Dashboard shell.
- **Filter toolbar + chips** sits above the table: global search, UBC Tag text filter, an Asset Type segmented control (Bootstrap `btn-check` radio group, All/ME/EL/BF), Attribute Set and Asset Group multi-selects, and Reset. Below it, active filters render as removable chips with a live `Showing X of Y entries` count. Filterable columns are exactly UBC Tag, Asset Type, Attribute Set and Asset Group; Main Asset, Description and Actions are deliberately not filterable.
- Attribute Set and Asset Group reuse the shared **`BuildingMultiselect`** component (`Dashboard/static/building-multiselect.js`/`.css`). Consume it only through its documented markup contract and `create(root, {allLabel, emptyLabel})` options and load it with a `?v=` cache-bust — **never fork it** (four-copy byte-identical rule, see `review_apps.rules.md` → "Building Filter Rules").
- Facet options are computed from the loaded rows against every **other** active filter, with the currently checked values unioned back in so a selection never disappears from its own list; when `setOptions()` reports the selection self-healed, the filter pass re-runs once. Same pattern as the FLS `updatePropertyFilterOptions`.
- The filter card and its ancestors must stay free of `transform`/`filter`/`will-change`: `.ms-panel` is `position: fixed`, and a transformed ancestor would become its containing block and misplace the dropdown.
- Sticky `thead` and the sticky Actions column need **opaque** backgrounds for every row state (base, zebra, hover) or cells smear over content during horizontal scroll.
- Sort headers keep `aria-sort` in sync (`ascending`/`descending`/`none`) alongside the chevron icon.
- All client-side interpolation goes through `escapeHtml()` — table cells, `data-key` attributes, aria-labels, chip labels and the load-failure alert; toast bodies use `textContent`. Missing values render an em dash, never the string `undefined`.
- Asset Type is constrained to **ME / EL / BF** both in the modal `<select>` and server-side via `DICTIONARY_ALLOWED_TYPES` (400 on anything else). The old `BP` option was wrong — the platform's backflow code is `BF`.
- `dictionary_index()` passes `can_edit` (the `dictionary/dictionary` **editor** permission) to the template. Viewers get a read-only page: no Add button, no Actions column, no modals in the DOM. This is a UI affordance only — the `@require_permission` decorators on the API remain the authority.
- The route also passes `acshell_active='dict'`. The shared `shell.js` `APPS` map has no `dict` entry yet, so the sidebar still falls back to highlighting `Dashboard`; adding that entry is a five-copy `shell.js` change and is tracked separately.

### Dictionary Audit Rules (2026-08-10)

- `POST /api/assets` and `POST /api/assets/delete` write `audit_trail` rows through `_log_dictionary_audit()`.
- The dictionary is a **file**, not a DB row, so there is no caller transaction to join: the helper opens its own short-lived connection, calls `audit.logger.log_change`, commits, and closes. The whole body is wrapped in `try/except` — the file write has already succeeded by then, so an audit failure is logged and swallowed, never surfaced as a request error.
- Call shape: `qr_code=None` (a dictionary key is not a QR code), `app_name="dashboard_dictionary"`, `table_name="mechanical_dictionary"`, `record_pk="<TAG|TYPE>"`, `source="human"` (`modified_by` resolves to the logged-in user).
- `op_type` is `INSERT` / `UPDATE` / `DELETE` only — the `audit_trail` CHECK constraint rejects anything else.
- Audited fields are `attribute_set`, `asset_group`, `main_asset`, `description`, `asset_type` plus a synthetic `dictionary_key` field. The legacy duplicated `type` key is excluded (it mirrors `asset_type` and would double every row). `dictionary_key` records renames and guarantees an INSERT always writes at least one row.
- On edit, the previous entry must be snapshotted **before** the legacy-key migration loop in `save_dictionary_asset()` — that pass deletes the original key and the old values become unrecoverable.

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

### Key Performance Indicators section (Overview)

- The Overview's first section header reads `Key Performance Indicators` (renamed from `Applications`, 2026-08-05). The four app launch cards were removed; sub-apps are launched from the shared shell sidebar (`static/shell/shell.js`).
- The section renders three **Chart.js 4** canvases (2026-08-05 redesign) inside `.overview-kpi-grid` (three columns at >=992px, single column below), in this order: `QR Codes by Asset Type` (`#kpi-qr-types`), `Performance Control KPI` (`#kpi-gauge`), `Overall Approval` (`#kpi-donut`). Each `.analytics-card` has an HTML title row (`.overview-kpi-head`); only the QR-types card has an `.overview-kpi-chip` context chip. Titles are **Title Case** (never uppercase-transformed) and use the `SDI Live Pipeline` heading style: Arial bold 18px in `var(--primary-accent)` `#0055b7` (the `font-family` needs `!important` because the global h1-h6 rule forces Inter with `!important`). The gauge title alone is centered by `.overview-kpi-head--gauge` to match its approved reference. Chart.js is pinned from CDN (`chart.js@4.4.9` UMD on cdn.jsdelivr.net, same host as Bootstrap).
- Data comes from `GET /api/overview/kpis` (`@login_required`, `building`/`user` query params, `Cache-Control: no-store`), backed by `overview_kpi_payload()` in `Dashboard/charts/approval.py` — shape `{"qr_types": {"ME","EL","BF"}, "approval": {"approved","not_approved"}, "manual_excluded": n}`, plain ints only (numpy ints break `jsonify`). Its error body is the generic `{"error": "data unavailable"}` — do not echo exception text.
- **Universe split (2026-08-06, user decision):** the bar chart and donut cover the **full capture universe** (curated + Manual Entry, matching the pipeline Total QR); the gauge covers **reviewable assets only** (curated tables), because Manual Entry is never approved through this pipeline and would otherwise make the 90% target unreachable. Concretely: payload `qr_types` = curated counts + `_manual_excluded_by_type()` per discipline (the matplotlib `qr_types` fallback adds the same); the donut gains a third slice `Manual Entry` (`MANUAL_TONE #8fa3b8`, only when nonzero) with center total = grand total and percentages over the grand total; the gauge and `approval` payload stay curated-only.
- The bar-chart chip reads `"1,577 QR codes · 15 manual"` with a tooltip noting the gauge tracks reviewable assets only. `manual_excluded` = distinct QRs whose max `Col_process` is 2 with no curated row (`_manual_excluded_by_type()` / `_count_manual_excluded()`); it respects the `building`/`user` filters and the chip suffix/donut slice hide when zero.
- `QR Codes by Asset Type` is a vertical bar chart of **distinct QR codes per discipline**: `_prepare_qr_type_counts()` counts `sdi_dataset` rows as BF when `Asset Group` contains `backflow` (case-insensitive) else ME — the same inference as the reviewer-analysis hover CTE — and all `sdi_dataset_EL` rows as EL. Do not change the inference in one place without the other.
- **Legend mapping is fixed**: ME = `Mechanical`, EL = `Electrical`, BF = `Backflow` (`TYPE_NAMES` in the dashboard.html init script); axis ticks stay ME/EL/BF. Y-axis ticks use one uniform pattern — `0.00, 0.50K, 1.00K, 1.50K, ...` when the axis max reaches 1,000, plain integers below that — never a mixed `500 / 1k / 1.5k` scale. Tones are the validated navy family `{ME: #002145, EL: #0055b7, BF: #5b9bd5}` — re-stepped 2026-08-05 after the dataviz palette validator flagged the old EL/BF pair (dE 11.5 < 15); adjacent pairs now pass at dE >= 21. `QR_TYPE_COLORS` (approval.py) and `TONES` (dashboard.html) must stay identical.
- Charts animate on load (900ms ease-out) and honor `prefers-reduced-motion` (animation disabled). Value labels and gauge/donut overlays are inline Chart.js plugins in dashboard.html.
- The gauge mirrors the approved reference via `gaugeReferenceOverlay`: a thin blue outer semicircle; `0`, midpoint and total scale labels; navy/light approval arc; black needle and hub; external blue triangle plus `Target: 90%`; and bold `approved / total` with `Approved Assets` beneath the arc. The large center percentage and header target chip are intentionally absent. A gauge-specific 280px canvas reserves vertical space for its bottom legend, and the value/caption are anchored above the measured legend edge. Compact 10px legend labels with `labels.padding: 12` keep both entries readable; 84px of right layout padding reserves room for the target label, which starts after the triangle's rightmost vertex and remains width-clamped inside the canvas. The donut legend shows counted entries (`Approved · n` / `Not Approved · n`). Light track swatches get a `#d4dde7` 1px border so they read on the white card.
- The donut card has **no context chip** — the total lives only in the ring center — and percent labels are drawn on the segments by the `donutCenterAndPercents` inline plugin (white on navy, ink on track; slivers under ~8.6 degrees are skipped).
- **Ring band width:** `cutout: '61%'` (band = 0.39R). Widened 2026-08-12 from the original `68%` / 0.32R (+~22%) at the user's request; it also lines up with the matplotlib `pie` fallback's `wedgeprops={"width": 0.4}`. The band may not grow further without re-measuring: at 61% the inner disc still clears the center total and its `total assets` caption in the worst measured case (5-digit total in a 260px-wide card, ~7px clearance), and the percent labels ride the band mid-radius, so a thicker band pulls them inward, not outward.
- **Fallback rule:** each canvas carries `data-fallback-src` pointing at the matplotlib image (`/chart/approval.png?chart_type=qr_types|gauge|pie&fmt=svg`). If `window.Chart` is undefined or the API call fails, the init script swaps every canvas for its `<img>` fallback. The matplotlib `qr_types` renderer and the route's `fmt=png|svg` support must therefore not be removed.
- The analytics page (`#analytics-view`) must stay on server-rendered PNG: its bar-chart hover hitboxes are pixel-based against the PNG raster.
- The section must stay gated by `{% if chart_enabled %}` with the same `Chart unavailable` alert fallback used by `#analytics-view`.
- The Reviewer KPIs page (`#analytics-view`) keeps its own copies of gauge and donut with the User Name / Building filters; the Overview charts are unfiltered (`building=selected_building`, no `user` param).
- `APPS` / `apps=` remains in `Asset_portal_dashboard.py`'s render context but is no longer consumed by the live template.
- Tests: `test/test_overview_qr_type_chart.py` (counting, BF inference, building/user filters, SVG/PNG formats, `overview_kpi_payload` shape/ints/filters) and `test/test_dashboard_kpi_gauge_template.py` (live reference geometry, centered title, zero-safe ratio, bottom legend).

### KPI totals vs SDI Live Pipeline totals (expected to differ)

The two Overview sections count **different universes**, and the difference is not a bug:

| Section | Source | Counts |
|---|---|---|
| `Key Performance Indicators` | `sdi_dataset` ∪ `sdi_dataset_EL` | QRs curated into the review datasets |
| `SDI Live Pipeline` (`Total QR`) | `QR_code_assets.Col_process` | every captured QR, any state |

- **Since 2026-08-06 the bar chart and donut include Manual Entry** (as added counts / an own slice), so their totals equal the pipeline Total QR. The **gauge remains curated-only**: Manual Entry QRs never enter `sdi_dataset` (`Col_process = 2` + `QR_codes.sdi = 1`, per the Manual Entry / SDI invariant) and are never approved through this pipeline.
- A bar-chart total that differs from the pipeline Total QR now means curated rows are genuinely missing (Manual Entry no longer explains any gap). Diagnose by set-differencing the two universes on distinct QR (the 2026-07-29 sync incident showed up exactly this way — see the JSON Sync Retry Guard rules in `review_apps.rules.md`). **The `integrity` block below names every legitimate reason for a gap; anything left over is a defect.**

### Integrity guardrails (2026-08-06)

Detection of the 2026-07-29 silent-loss class already existed and already fired — `audit_sdi_vs_json.py` ran hourly and logged 8,259 `row_missing` anomalies that nobody read, because its log was not listed in the Dashboard UI, cron has no `MAILTO`, and `--quiet` suppressed healthy-run output so a stale log looked identical to a passing one. **The gap was signal delivery, not detection.** These rules close it.

**The accounting identity (the invariant).** `pipeline Total QR − sum(KPI bars)` is fully explained by four named channels:

```
pipeline − bars == orphan_total + manual_unknown − reverse_total − curated_double_counted
```

Pinned by `test/test_pipeline_kpi_equivalence.py`, which imports `flow_quantity_chart` and `approval` against one fixture. **No divergence may hide in an unnamed remainder** — if a new divergence channel appears, that test must gain a term rather than be loosened. Verified live on 2026-08-06: pipeline 1585 − bars 1581 = 4 = the 4 in-flight pending captures.

**`payload["integrity"]`** — built by `approval.integrity_snapshot()`, appended to `/api/overview/kpis` inside a `try/except` so a failure degrades to `{"error": ...}` and never breaks the charts. Scope is deliberately **global** (no building/user filter) because the invariant is system-wide; the chip tooltip says so, since the chip's own totals *are* filtered. Keys: `orphans` (per discipline + `unknown`), `orphan_total` / `orphan_stranded` / `orphan_pending`, `manual_unknown`, `reverse_total`, `curated_double_counted`, `unclassified`, `grace_hours`, sample lists (sorted, capped), `scope`.

**Grace period — do not remove.** A capture gains its curated row only when a reviewer app next serves a request (the JSON sync runs from `before_request`), so a recent capture is *legitimately* curated-less. Production check on 2026-08-06 found 4 such QRs, all under 6 hours old, all of which resolved on the next reviewer request. Only captures older than `ORPHAN_GRACE_HOURS` (96h — clears a long weekend with no reviewer traffic) count as **stranded**; the rest are **pending** and must not raise an alarm. A capture with no `QR_codes.date_set` is never excused as recent, since a missing row is itself a defect. `orphan_total` stays the full set because it is the identity term; the **chip warns on `orphan_stranded`**, not `orphan_total`. Rationale: alarming on normal in-flight work is precisely how the 8,259 findings came to be ignored.

**Chip warn state.** `.overview-kpi-chip--warn` (amber `#fff3cd`, never red — the displayed numbers are correct, just incomplete) plus a `⚠ N unsynced` suffix, when `orphan_stranded + reverse_total + manual_unknown + curated_double_counted > 0`. The tooltip lists per-discipline counts, sample QRs, the pending count as informational, and points at System Logs. Keep the existing Manual Entry tooltip appended, not overwritten.

**Scheduled auditors and the `[AUDIT]` marker grammar.** Three read-only auditors run hourly from the `developer` crontab, staggered (`:00` `audit_sdi_vs_json.py`, `:15` `audit_capture_vs_curated.py`, `:30` `audit_sdi_flow_integrity.py`). Severity is uniform: exit `0` clean / `1` DRIFT (or anything under `--strict`) / `2` setup error.

- **Never run these with `--quiet` in cron.** A clean run must still write its `[AUDIT] OK` + `RUN_AT` line, or the log tail cannot distinguish "fixed" from "still failing" from "job stopped". This is the single change that makes the signal deliverable; the `--quiet` flag remains only for interactive use.
- Trailer grammars differ per auditor and the parser handles all three: `RUN_AT=<iso> SCANNED=n FINDINGS=n FAILING=n`, `RUN_AT=<iso> FINDINGS=n FAILING=n`, and `RUN_AT=<iso> ANOMALIES=n`. `SCANNED` is a scope, never a count. (A 2026-08-06 regression read `ANOMALIES=14` as OK because only `FINDINGS=` was parsed; covered by `test/test_audit_log_status.py`.)

**System Logs page.** `AUDIT_LOG_PATHS` (env-overridable) lists the three audit logs beside `ai_check.log`; `_system_log_paths()` filters absent files rather than failing (dev machines have none), and `_safe_log_path()` allowlists them by name before the `LOG_DIR` containment fallback. `_audit_log_status()` reads only the last 8 KB (`sdi_audit.log` is ~1.7 MB), takes the **last** marker so a clean run supersedes an earlier bad one, caches on `(path, mtime_ns, size)`, and reports `stale` when the file has not been written for over `AUDIT_STALE_AFTER_SECONDS` (2h) — a silent auditor is itself a finding. Badges reuse the existing Bootstrap classes: `bg-success` OK / `bg-warning text-dark` findings / `bg-secondary` stale.

**Read-only.** Every integrity path reads; none of it writes to the operational tables. The Dashboard remains read-only per global rule 9.

### Cookie + CSP coupling

- Dashboard must set `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, and the matching remember-cookie triple.
- Nginx for the Dashboard sets `Content-Security-Policy: frame-ancestors 'none';` to prevent third-party embedding.

### Cache invalidation

- After any change to `Dashboard/templates/dashboard.html`, restart `assetcap-dashboard` and instruct the user to hard-refresh (`Ctrl+Shift+R`). Browsers cache the rendered HTML/JS aggressively and `postMessage` listeners will not be installed without a fresh download.
