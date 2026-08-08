# Workflow: Review and Approve

Current documentation refresh: 2026-06-24.

## Purpose

Allow human reviewers to correct extraction output, preserve curated fields, and push approved data into SDI staging tables.

All three review sheets also expose optional **Installation Date** from `QR_codes.installation_date`. The form uses `DD/MM/YYYY`; saves reject invalid/future dates, store `YYYY-MM-DD`, allow an explicit blank clear, and emit a human `audit_trail` change. This QR-level field is not copied into extraction JSON and does not affect approval, completeness, or confidence.

## Inputs

- extraction JSON in `Output_jason_api/`
- review apps for ME, BF, and EL
- PostgreSQL `qr_code_db` curated dataset tables

## Main Steps

1. Open the correct review app for the discipline. (For Electrical, choose 'General' or 'Distribution' scope first on the landing page; an asset belongs to the Distribution scope when its `Asset Group` has `elec_dist_setup = 'Y'` in the `Asset_Group` lookup — see `special_processes/04_database_topography.md`. The EL review page renders a variant matched to the asset's group (2026-08-07): General assets get the ME-style nameplate form (Manufacturer / Model / Serial Number / Year), Distribution assets keep the electrical tech-card form — see `rules/review_apps.rules.md` → "EL Review Form Variants".)
2. Let the `before_request` sync reconcile new JSON and image state into the DB.
3. Use the dashboard tabs to select New, Update (hidden in Electrical), or Manual Entry assets.
4. Open the review page, compare the JSON against the photos, and edit values as needed.
5. Save changes back to the JSON payload and mark the file `modified=true` when content changes.
6. Sync the curated record into `sdi_dataset` for ME/BF or `sdi_dataset_EL` for EL.
7. Toggle `Approved` when the record is ready for downstream packaging.
8. (Optional) Click the per-tab **Excel** button (next to Reset in the filter row) to download the currently visible rows as a styled `.xlsx` — useful for QA spreadsheets, hand-off to Planon, or sharing with stakeholders without dashboard access. See `review/.agent/AGENT.md` for the route contract and column lists.
9. (Optional, ME / BF / EL) From the single-asset review page, use the **PDF** or **Export** header button to produce a one-page **Asset Review Sheet** for that asset (see "Asset Review Sheet export" below).

## Current Review Rules

- the single-asset review page's action buttons are canonical across ME/BF/EL and rendered from the `review_buttons.py` registry + `macros/review_buttons.html` macro (three byte-identical copies, one per app) — top bar: **Save**, **Pending/Approved**, **PDF**, **Export**, **Dashboard**; footer: **Prev**, **Save Changes**, **Save & Next** (reads "Next" when locked), **Skip**, **Save**, **Pending/Approved**; see `rules/review_apps.rules.md` → "Review Action Button Rules"
- approval must not wipe `Asset Group`, `Attribute`, `Description`, or other curated dictionary fields
- review-page photos can be inspected with the shared image viewer: wheel zoom, drag pan, zoom/rotate/reset buttons, double-click detail/reset, and keyboard shortcuts
- ME/BF/EL review dashboards expose `Avg AI Conf` and confidence slicers
- Manual Entry is not only a visual tab; it must align `Col_process = 2`, JSON `ExcludeSDI`, and `QR_codes.sdi = 1`
- confidence filters and display values must stay aligned between backend and frontend
- the dashboard Photo column shows the required-photo ratio (`{present}/{required}`) inside the red/green pill, plus a small `+1` chip when the optional **Extra Photo** is present (ME `-4`, BF `-3`, EL `-3`); the chip is informational only, its absence never sets "Missed Photo", and the Photo value cell is left-aligned in New / Update / Manual listing tables
- the single-asset review page exposes the Extra Photo as a labeled "Extra Photo" thumbnail in the strip; an absent Extra Photo renders the neutral "Missing" placeholder with no red/error styling

## Outputs

- updated JSON payloads
- updated curated dataset rows in PostgreSQL `qr_code_db`
- approved rows ready for SDI Process unless explicitly excluded from SDI

## Verification

- confirm review-page edits persist after reload
- confirm source-photo inspection works in standalone and Dashboard iframe views: wheel zoom, drag pan, rotate, reset, thumbnail reset, and keyboard shortcuts
- confirm dashboard filters survive save / next / previous navigation
- confirm approved rows appear in the correct curated dataset table
- confirm manual-entry assets do not leak into SDI packaging when excluded

## Bulk Manual / Approved actions (ME + BF + EL)

The ME, BF, and EL review dashboards expose a master checkbox in the **Manual** and **Approved** column headers of every tab table (New / Update / Manual). BF and EL use the same client-side batching pattern as ME.

### Steps

1. Filter or paginate the tab table to the rows you want to act on. The bulk action only touches DataTables-visible rows.
2. Click the master Manual or Approved checkbox in the column header.
3. Confirm the action in the `#confirmModal` dialog, or in the `window.confirm()` fallback if the modal is unavailable. Cancelling reverts the checkbox. The `#confirmModal` uses `modal-dialog-centered`, so the dialog is vertically centered and not clipped by the Dashboard top bar when the review app is embedded.
4. The dashboard fans out per-row calls to `POST /toggle_sdi/<doc_id>` or `POST /toggle_approved/<doc_id>` in a client-side queue and re-renders affected cells in place.

### Client-side filters

- **Bulk-Manual** skips rows where Approved is already `True` — Manual-flagging an approved row is not allowed.
- **Bulk-Approved un-check** first calls `GET /check_sdi/<qr_code>` and skips rows that already exist in an SDI package.
- Rows where the current value already matches the target are skipped silently (no-op).
- BF only renders the header checkboxes when `can_edit` is true. EL keeps the existing endpoint-enforced permission behavior.

### Verification

- confirm canceling the modal leaves all rows unchanged.
- confirm filter scope is respected — hidden rows are never modified.
- confirm Approved uncheck with an exported row leaves that row unchanged.
- confirm row cells update in place and the current DataTable redraws after the queue drains.

## EL Distribution listing view

The "Review Electrical Assets - Distribution" dashboard keeps the same data model as the general EL dashboard but hides three listing-table columns for scanability: **Amperage Rating**, **Volts**, and **Location**. The hide is frontend-only and scoped to `/review-distribution`; `review.html`, `/review-all`, JSON, DB rows, SDI packaging, and Planon export still carry the fields.

Which assets appear here is data-driven (2026-08-04): the view lists assets whose `Asset Group` matches an `Asset_Group` row with `elec_dist_setup = 'Y'`; all other groups stay in `/review-all`. The set refreshes within ~60 seconds of a flag change (see `rules/review_apps.rules.md`).

The EL dashboard also loads the shared `_shell.html` partial so the standalone sidebar/topbar behavior matches ME and BF. Required-field checklist popovers use a higher `.el-required-popover` z-index than the shell sidebar so QR hover cards render above the shell.

## Asset Review Sheet export (ME / BF / EL)

The single-asset review page for ME, BF, and EL carries two header buttons that produce a one-page, self-contained **Asset Review Sheet** for the currently open asset.

### Steps

1. Open the asset's review page (`/review/<doc_id>`).
2. Click **PDF** to open a print-optimized view in a new tab (`GET /review/<doc_id>/print`). It auto-invokes the browser print dialog; choose *Save as PDF*.
3. Or click **Export** to download a fully self-contained `.html` file (`GET /review/<doc_id>/export`, `Asset_Review_<ME|BF|EL>_<qr>_<building>.html`). Photos, the UBC logo, and any EL SLD strip are inlined as base64 data URIs, so the file opens later with no server and no network.

### What the sheet shows

- Header: QR code, **building Name** (`Buildings."Name"`, not the code), location/space, AI confidence, approval badge, UBC logo.
- EL: Description, Identity, then by form variant (2026-08-07) either Technical Details (Amperage / Voltage / Power Rating / Supply From / Fed From / Equipment ID / Equipment Type / Power Type — Distribution assets) or Nameplate (Manufacturer / Model / Serial Number / Year — General assets), the SLD strip (if any), Asset Photos, and the User Activity Log (captured-by / date / hour / GPS). ME and BF show the equivalent nameplate fields (ME: Manufacturer / Model / Serial / Year + TSBC; BF: Manufacturer / Model / Serial / Diameter + Application) and omit the SLD section. All three put **Description first**, above Identity.
- All three also render two read-only QR-level values (2026-07-10): **Installation Date** (`QR_codes.installation_date`, `DD/MM/YYYY`) below Year (ME Identity / BF Classification) or below Main Asset in EL's Identity, and a **Capture Notes** section (`QR_codes.capture_notes`) between the two-column block (Identity/Classification for ME/BF; Identity/Technical Details for EL) and the next section ("No capture note" when blank).
- **EL SLD strip:** when the asset appears in `electrical_building_schema` (`new_draw='TRUE'`), the sheet renders the asset's **end-to-end branch** (upstream lineage + the asset + its full downstream subtree; siblings excluded) as an inline SVG ladder, with a **red flag = Current Asset** and **blue flag = Supply From**, equipment-type icons, and a legend. When the asset is on no diagram, a short "No Single Line Diagram available for this asset." note is shown. The strip is reconstructed from the SLD table — it is not a crop of the source PDF. Node rating lines always render display units (`V`/`A`): `_sld_rating_text()` maps Planon UoM codes (`VLT`/`AMP`) carried by rows copied from the SDI side (2026-07-09).

### Notes / constraints

- Both buttons are read-only — generating a sheet never edits JSON, `sdi_dataset_EL`, or the DB.
- Both routes share one context builder and one `review_print.html` per app, toggled by an `auto_print` flag; both enforce the same viewer permission as `review()`.
- See `Markdowns_documentation/rules/review_apps.rules.md` ("Asset Review Sheet") for the full rule set.

## Review navigation: archive + dashboard-ordered sequence (ME / BF / EL)

When a reviewer opens an asset from a listing table and pages through it, the review page now mirrors the dashboard view they came from:

- **Show Archive persists.** If the reviewer had "Show Archive" on, it stays on after returning via the **Dashboard** back-button, **Save & Next/Prev**, or a reload. (Previously it reset to hidden.)
- **The sequence follows the dashboard's filters and column sort.** Save & Prev / Save & Next, the "Showing X of Y" counter, and the Next Asset preview walk the exact order shown on the dashboard — including any column sort — not an internal `doc_id` order. The dashboard stores the visible order in `localStorage('reviewOrder')`; the review page reads it.

### Verification

- On a listing tab, turn on **Show Archive**, apply a column sort, open an asset → confirm Save & Next/Prev follow the sorted order and the "X of Y" count matches the dashboard.
- Click **Dashboard** to return → confirm Show Archive is still on and the sort/filters are preserved.
- Hard-refresh (`Ctrl+Shift+R`) after any dashboard template change, since the dashboard JS is inline.

See `Markdowns_documentation/rules/review_apps.rules.md` ("Review Navigation — Archive Persistence and Dashboard-Ordered Sequence") for the implementation rules, including the `a.v2-btn-review` selector requirement and the `GET /api/asset-preview/<doc_id>` endpoint.

## EL Single Line Diagram (SLD) Panel

The EL Distribution view has a dedicated SLD panel (`sld_blueprint.py`, `sld_panel.html`, `sld.js`) that operates on `electrical_building_schema` (the diagram-side table) alongside the matched `sdi_dataset_EL` row.

### Diagram PDF export

The **Download Single Line Diagram as PDF** button exports the current diagram view as an A4 landscape report. The diagram PDF uses the same dynamic `#sld-legend-bar` legend items shown on the page, composited above the tree by `composeLegendOntoDiagramPng`, then places the legend + diagram image top-aligned inside a rounded white report board with the UBC Facilities logo, building name, export date, and a footer showing the user and exact creation time. The Switch Over table PDF path remains separate and unchanged when the table-format toggle is active.

### Import a new SLD PDF

The "Create a New Diagram - Single Line Diagram" flow is building-driven, not filename-driven:

1. The current EL/SLD building selector provides `building_code`.
2. The selected file must be a PDF. The frontend blocks non-PDF files and `/sld/api/upload` rejects non-PDF uploads server-side.
3. The PDF filename no longer needs to start with a 3-digit building code. The backend no longer derives the building from the filename.
4. After file selection, the modal renders a client-side PDF.js preview from the local `File` object before upload.
5. The preview supports page navigation, zoom in/out, rotate clockwise, and mouse drag-to-pan inside the preview stage.
6. `Upload & Process` is the user's final confirmation. It sends multipart form data containing the file and selected `building_code`, then calls `/sld/api/process` with `{building_code, filename, replace: true}`.

If the selected building already has an active SLD, the preview modal shows an inline warning that the existing diagram will be archived. There is no additional "Replace & Process" prompt after upload; the preview confirmation is the replacement confirmation.

### Inline editor (Swift Over)

The panel renders rows in an editable table. Each row's Save button posts to `POST /sld/api/assets/<row_id>/swift-save`, which:

1. Updates `electrical_building_schema` for the row in a single local transaction.
2. If the row has a matched `sdi_dataset_EL` entry (`matched_qr` present), merges the patch into `<QR>_EL_<Building>.json` under `json_sync_lock`, then calls `_sync_db_from_structured` to upsert `sdi_dataset_EL`.
3. Recomputes `matching_check` and returns the enriched row for client-side row replacement.

Failures inside the JSON / SDI sync trigger a rollback of both the SLD update and the JSON write.

### Reconciliation column

The rightmost column (header **Reconciliation**, previously "Check") flags whether the SLD row and its `sdi_dataset_EL` counterpart agree on the composite key `Building | Equipment ID/UBC Asset Tag | Supply From`. The status comes from `id_check_match`, a boolean computed by `_enrich_asset_display_fields` from each table's `ID_check` column.

- **Green ✓** — both sides match.
- **Red ✗ (clickable)** — the two sides disagree but a matching QR exists; clicking opens the Reconcile modal (see below).
- **Red ✗ (static)** — no matching SDI row for this SLD entry at all (no reconciliation possible from this UI).

`ID_check` is a `GENERATED ALWAYS AS (TRIM(COALESCE(Building,'')) || ' | ' || TRIM(COALESCE("Equipment ID"|"UBC Asset Tag",'')) || ' | ' || TRIM(COALESCE("Supply From",''))) STORED` column on both `electrical_building_schema` and `sdi_dataset_EL` (PostgreSQL has no `VIRTUAL` generated columns; the SQLite original used `VIRTUAL`). Do not write to it from Python — the DB rejects the write and the column tracks `Building` / `Equipment ID` / `Supply From` automatically.

### Reconcile modal flow

Clicking a red Reconciliation cell on a QR-matched row opens a modal with three choices:

- **Diagram is correct** — write the SLD's `Supply From` to `sdi_dataset_EL` + the JSON file.
- **Captured asset is correct** — write `sdi_dataset_EL`'s `Supply From` back to `electrical_building_schema`.
- **Custom** — write a user-entered value to both sides.

Submission posts to `POST /sld/api/assets/<row_id>/reconcile` with `{choice, value?, reason?}`. The endpoint:

1. Resolves the SLD active row + matching SDI row.
2. Updates each side only if its current value differs from the target.
3. Writes one `audit_trail` row per changed side (`table_name="electrical_building_schema"` or `"sdi_dataset_EL"`, `source="human"`, `description="reconcile:<choice>"` plus the optional reason).
4. Rolls back atomically on JSON or SDI failure (SLD update restored, JSON file restored from backup).
5. Returns the updated enriched row for in-place client refresh.

Idempotent: a second call with the same target value returns `{status: "noop"}`.

### Orphan JSON case

`swift_save_asset` (and `reconcile_asset`) require the matched SDI row's JSON file to exist on disk at `Output_jason_api/<QR>_EL_<Building>.json`. If the JSON is missing while the SDI row is present, the request returns `500 {"error": "Failed to sync captured asset: JSON not found for QR ..."}`. Either restore the JSON or remove the orphaned SDI row.

## AI Status Reprocess (ME / BF / EL)

Current documentation refresh: 2026-06-25.

When a reviewer needs to discard AI output and re-run extraction on a specific asset, they toggle **AI Status** to `0` (off) in the dashboard listing table. This triggers the reprocess workflow rather than just clearing a flag.

### Steps

1. Locate the asset in any listing tab (New, Update, or Manual).
2. Click its **AI Status** cell to toggle it off.
3. The backend (`POST /toggle_ai_status/<doc_id>`) checks the protection hierarchy, then either:
   - **Permits reprocess**: moves the JSON to a `.bak_<timestamp>` backup, sets `ai_status = 0`, responds `{"success": true, "reprocess_requested": true}`.
   - **Blocks**: responds `{"success": false, "code": "...", "error": "...", "forceable": bool}`.
4. Within ≤ 2 minutes the extraction cron (`ai_check.sh`) finds no JSON for the asset, runs full re-extraction, and sets `ai_status` back to `1`.
5. The dashboard's AI Status cell flips back to ✅ automatically within ≤ 60 s of the DB change — each dashboard polls the read-only `GET /api/ai_status_map` endpoint (see `rules/review_apps.rules.md` → "AI Status Auto-Refresh"); no manual page reload is needed.

### Protection hierarchy

| Asset state | Blocked? | Forceable? |
|---|---|---|
| Asset in active or archived SDI package | **Yes** | No — retrieve from package first |
| JSON `Approved = True` | **Yes** | No — un-approve the asset first |
| Manual Entry (`Col_process = 2`) | **Yes** | No — Manual Entry assets are not AI-extracted |
| Human-edited (`modified = True`) | **Yes** | **Yes** — confirm dialog appears |
| Fresh AI result (no protection flag) | No | — |

### Force reprocess (human-edited assets)

When the dashboard shows:

> *"This asset has manual edits; reprocessing is blocked to protect your corrections."*

a **Force re-run AI?** confirm dialog is displayed:

> *"This will DISCARD your manual edits to this asset and re-extract it with AI. The current values are backed up and recoverable. Continue?"*

Confirming re-posts the same endpoint with `force=1`. The backup JSON is always recoverable at `Output_jason_api/<QR>_<TYPE>_<Building>.json.bak_<timestamp>` on the VM.

### Verification

- Toggling AI Status on a fresh AI asset should show the cell change to `☐` immediately; re-extraction completes within ≤ 2 minutes, and the cell returns to ✅ automatically (≤ 60 s poller) without reloading the page.
- Toggling AI Status on an approved asset should show the error message with no file change.
- Toggling AI Status on a modified asset should show the blocked message with a **Force re-run AI?** button. Confirming should move the JSON and trigger re-extraction.
- Toggling AI Status on a packaged asset should return `409` and show the package-lock message.

## Update 2026-06-01 — archive-aware listing and approval UI

- **SLD building dropdown** (EL Distribution) lists only buildings with at least one displayable (non-archived) QR; fully-archived buildings drop off. Hidden escape hatch: `GET /sld/api/buildings?include_archived=true`.
- **Archive toggle / filters**: the "Show/Hide Archive" label is now action-correct, and the Review Status filter and archive button preserve each other's query params, so an "Approved + Show Archive" view holds across reloads (EL/ME/BF).
- **Bulk Approve** (EL "approve all") now reports failures in a modal rather than silently doing nothing.
- **Flow integrity**: `scripts/audit_sdi_flow_integrity.py` detects assets that cannot complete approve→package→archive (e.g., approved but SDI-excluded, or a cross-store approval mismatch); remediate per `special_processes/sdi_flow_remediation.md`. Approval is consumed/regenerated by re-capture + the nightly rebuild, so an archived asset can read `Approved=blank` in the live row while its prior approval persists in the archived package (`id_print_out`).
