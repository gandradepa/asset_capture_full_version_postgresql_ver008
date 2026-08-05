# Review App Rules

Current documentation refresh: 2026-07-25.

## Purpose

The review applications are the human-in-the-loop correction layer for ME, BF, and EL.

## Standard Claude Code Skills

Use the project-standard plugins (enabled in `.claude/settings.json`; see `01_GLOBAL_RULES.md` → "Standard Claude Code Skills") when changing the review apps:

- **Frontend Design** (`frontend-design`) — invoke for any review-app UI change: page templates, dashboards, image viewer, building / asset-group filters, action buttons, CSS/JS.
- **Context7** — fetch current DataTables / Bootstrap / Flask documentation via the Context7 MCP tools before relying on remembered APIs.
- **Superpowers** — use its planning and systematic-debugging skills for multi-file changes, especially ones touching the byte-identical multi-copy files described below.

## Core Backend Rules

- Keep the `before_request` sync hooks active.
- Read and write JSON with `encoding='utf-8'`.
- Set `modified=true` when a save changes persisted JSON content.
- Save operations must resync curated data into the SDI tables.
- Save actions: `save` / `save_next` / `save_prev` navigate after saving; `save_toggle` (Pending/Approved pill) and `save_stay` (Save button beside the pill, 2026-07-07) save and return to the same review page. All three apps implement the same action set.

## Approval Rules

- Approval toggles update both the JSON payload and DB state.
- Approval must not wipe dictionary-derived fields such as `Asset Group`, `Attribute`, `Description`, or discipline-specific derivatives.
- Package-locked rows must remain approved in review source state. If a QR appears in `sdi_print_out` or `sdi_print_out_arch`, automated ME/BF/EL JSON sync coerces review JSON `structured_data.Approved` to `"True"` and writes source approval as `"1"` (`sdi_dataset` for ME/BF, `sdi_dataset_EL` for EL).
- SDI Retrieve and Exclude actions also preserve package-source approval; Exclude returns approved, non-manual package rows to Unpackaged Assets after deleting the active package row.
- Do not add `Approved` to `sdi_print_out` or `sdi_print_out_arch`; package tables provide package state and `id_print_out`, while approval remains sourced from review JSON plus `sdi_dataset` / `sdi_dataset_EL`.
- All three review tabs (New / Update / Manual) default the `Review Status` filter to **All Statuses**.

## Dictionary Auto-Fill and Manual-Override Rules

- ME/BF `apply_dictionary_rules` and EL's tag-dictionary derive re-run on every save, JSON→DB sync, and render; they fill `Asset Group` / `Attribute` (and ME `Main Asset`) from `dictionary/mechanical_dictionary.py` by UBC tag.
- Reviewer overrides are recorded as persisted flags in the structured JSON: `asset_group_manual` and `attribute_manual` in ME/BF (2026-07-07), `asset_group_manual` in EL. `"1"` = manual; absent or `"0"` = dictionary-controlled.
- Dictionary re-application must never overwrite a flagged, non-blank field — on any path, including after a UBC Tag change.
- Flags are computed server-side on save (`_update_manual_field_flags`): a non-blank submission differing from the current dictionary derivation sets `"1"`; a blank submission or one matching the dictionary resets to `"0"` (field returns to dictionary control). The flag keys are excluded from the generic form merge.
- ME `Main Asset` stays dictionary-owned (read-only in the form). EL `Attribute` is read-only in its UI and defaults to `Electrical`. BF `Description` keeps its blank-only dictionary fill.
- Legacy JSONs without the flag keys behave as dictionary-controlled.

## Manual Entry and SDI Rules

- Manual Entry is also an SDI exclusion concept.
- The following should remain aligned:
  `QR_code_assets.Col_process = 2`, JSON `ExcludeSDI`, and `QR_codes.sdi = 1`.
- A record must not appear in Manual Entry while remaining eligible for SDI packaging.

## Review Dashboard Rules

- The visible quick filters are based on current UI behavior, not on older archived controls.
- The Electrical dashboard defaults to a landing page to segment "General" vs "Distribution" assets.
- The General/Distribution split is data-driven (2026-08-04): an asset lands in the Distribution view when its `Asset Group` matches an `Asset_Group` row with `elec_dist_setup = 'Y'`; `'N'` (the default) keeps it in the General (`/review-all`) view. The EL dashboard loads the 'Y' set via `get_distribution_asset_groups()` (60s TTL cache) and passes it to the XLSX export and amp-warning gating; the SLD Switch Over query reads it via `_distribution_asset_groups()` in `sld_blueprint.py`. The static `EL_DISTRIBUTION_ASSET_GROUPS` frozenset in `excel_export.py` (mirrored as a tuple in `sld_blueprint.py`) is only the fallback when the column or DB is unavailable (e.g. the frozen local SQLite copy) — keep those fallbacks in sync. Moving a group between views is now an audited DB data change (`UPDATE "Asset_Group" SET elec_dist_setup = ...`), not a code change; column + seed migrations: `scripts/migrations/2026-08-04_asset_group_elec_dist_setup*.sql`.
- The "Update Existing" tab and "Main Asset" column are deprecated/hidden specifically in the Electrical dashboard UI.
- Confidence slicers must filter rows consistently on the frontend and backend.
- `Avg AI Conf` display and coloring should use the same value the backend exposes.
- `Package Number` is display-only on ME / BF / EL listing tables (New / Update / Manual). It appears immediately before `AI Status` (between `Main Asset` and `AI Status` in ME), displays the row's `package_id` from `sdi_print_out.id_print_out` or `sdi_print_out_arch.id_print_out`, and stays blank when no package exists.
- The styled XLSX review export (`excel_export.py` → `build_workbook`, shared identical copy in each review app) carries a matching `Package Number` column for ME / BF / EL, keyed on the same `package_id` and placed immediately before `Avg AI Conf`. Keep the export column in sync whenever the listing-table column changes.
- ME listing tables expose `QR_codes.capture_notes` as a display-only `Notes` icon immediately after `Capture Date`: green when a nonblank note exists and muted/faint when empty. The per-asset ME review page shows the same QR-level note in a read-only box beside the thumbnail strip. This is not posted back to JSON, SDI, Planon, or audit tables.
- In the EL dashboards, the global building selector is page scope and **single-select** (one building at a time). The filter `Reset` button must preserve the current `building` / `filter_building` parameters while clearing table-level filters so users stay on the same building scope and route. See "Building Filter Rules" below.

## Building Filter Rules (searchable dropdown; ME/BF multi-select, EL single-select)

Current documentation refresh: 2026-07-14.

The dashboards' Building filter is a checkbox dropdown with a type-to-filter search box, mirroring the Life Cycle Assessment dashboard's building filter (`makeBuildingSelect` in `Dashboard/life_cycle/static/js/dashboard.js`); the Life Cycle copy itself is untouched. **ME and BF run it in multi-select mode** ("Select all" / "Clear" actions; toggle label "All Buildings" / the single selection's label / "N selected"). **EL runs the same component in single-select mode** (`{single: true}`): one building at a time, checking a building replaces the selection and auto-closes/commits, "Select all" is hidden ("Clear" remains), `setValues()` keeps only the first code. **The Dashboard app's FLS Devices `Property` filter reuses the same component in multi-select mode** over property names (see `dashboard.rules.md` → "FLS Asset CRUD Rules").

- **Component:** `review_asset_templates/static/building-multiselect.js` — `window.BuildingMultiselect`: `create(root, {allLabel, single, emptyLabel})` → instance `values()` / `setOptions([{value,label}])` / `setValues(codes)` / `clear()` / `onChange(fn)` / `onClose(fn(changed))`, plus helpers `buildColumnRegex(codes)` / `parseColumnRegex(str)` — and `building-multiselect.css` (self-contained "stitch"-styled `.ms*` classes). Keep both `[hidden]` guards in the CSS: `.ms-panel` / `.ms-option` set `display:flex`, which otherwise beats the `hidden` attribute (the bug fixed on the Life Cycle dashboard on 2026-07-14). Option-row rules are specificity-hardened (`.ms-options .ms-option` prefix + explicit margin/font/text-transform resets) so host `label` styling — e.g. the Dashboard's `.filter-container label` (display:block, uppercase) — cannot break the checkbox/text flex alignment; keep that prefix when editing.
- **FOUR-COPY RULE:** both files exist byte-identically in the ME, BF, and EL review apps **and** in `Dashboard/static/` (the Dashboard copy powers the FLS Devices `Property` filter) — same rule as `image-viewer.js` / `review_buttons.py`. Edit all four together; `git diff --no-index` between copies must stay empty. Behavior differences between consumers are expressed only through `create()` options, never by forking the file.
- **URL contract:** `building` / `filter_building` carry a **comma-joined code list** (`A,B`) in ME/BF; a single code has no comma, so old bookmarks and the Dashboard portal's single-building links stay valid. EL sends a single code. Each app's `_parse_filter_values()` (renamed from `_parse_building_codes()` on 2026-07-30; shared with the Asset Group filter) splits and dedupes, and every server-side building filter is **set membership**, not equality (`get_filtered_data_and_counts`, `_get_card_scope_data`, `export_review_xlsx`; EL also `scope_counts_to_selected_building`) — EL's `_render_dashboard_view` additionally truncates to the **first** code so EL stays single-building even on legacy multi URLs. Export filenames join codes with `_` (capped at 3, then `CODE_plusN`).
- **ME / BF (per-tab, client-side, multi):** each tab's `#filter-building-<tab>` root hosts one instance, registered on `window.buildingFilters` and read via `buildingFilterValue(tab)` (used by `updateDashboardQuery`, `updateReviewLinks`, `exportFilteredXlsx`, `resetFilters`). Selection filters rows through a DataTables anchored OR-regex on the Building column (`^(A|B)$` via `buildColumnRegex`); empty selection = all buildings; filtering stays client-side (no reload). The building facet list is rebuilt from **all rows of the tab** (`{search:'none'}`) — not the applied set — otherwise the building column's own filter would hide every unchecked building after the first pick and a second selection would be impossible. Captured By faceting still uses `{search:'applied'}` (the Asset Group facet moved to `{search:'none'}` with its own multi-select — see "Asset Group Filter Rules" below). Saved DataTables column searches rehydrate via `parseColumnRegex` (legacy single `^A$` states accepted); URL params win over saved state.
- **EL (page scope, server-side, single):** the v2 command bar hosts one global instance (`#global-building-ms`, exposed as `window.globalBuildingMs`, created with `{allLabel: 'Select a building', single: true}`), seeded from `buildings_list` / `selected_building_codes` (`window.EL_BUILDINGS` / `window.EL_SELECTED_BUILDINGS`). Picking a building closes the panel and the `onClose(changed)` handler reloads with the new `filter_building`, preserving the active tab, dropping per-tab filter params, and clearing DataTables saved state (same behavior as the old `<select>`); Clear + close returns to the "Please select a building" empty states. The SLD tab needs no multi-building gating — single-select restores the inherent one-building scope, so `sld.js` always sees a bare code (`sld.js` also reads the component for display names when one building is selected).

## Asset Group Filter Rules (searchable dropdown; ME/BF multi-select, EL simple select)

Current documentation refresh: 2026-07-30.

The ME and BF dashboards' per-tab Asset Group filter reuses the Building filter's checkbox dropdown with a type-to-filter search box ("Select all" / "Clear" actions; toggle label "All Groups" / the single selection's label / "N selected"; empty facet shows "No asset groups"). EL keeps its simple per-tab `<select>` — only EL's server-side parsing was generalized. Asset Group stays a **per-tab client-side filter**: KPI cards are never group-scoped and the dashboard index still renders with `apply_client_filters=False`.

- **Component:** the same shared `BuildingMultiselect` as the Building filter, created per tab with `{allLabel: 'All Groups', emptyLabel: 'No asset groups'}`. `emptyLabel` (added 2026-07-30) is a generic `create()` option for the "no options" row; it defaults to `"No buildings"`, so the Building, EL single-select, and Dashboard Property consumers are unchanged.
- **FOUR-COPY RULE:** unchanged — `building-multiselect.js` / `.css` stay byte-identical across the ME / BF / EL review apps and `Dashboard/static/`; consumer behavior differs only via `create()` options, never by forking the file.
- **URL contract:** `filter_group` carries an **ordered, de-duplicated comma-joined list** (`Air Handling Units,Chillers`); a single value has no comma, so legacy `filter_group=X` URLs stay valid; empty = all groups. All three reviewers parse via `_parse_filter_values()` and filter by **exact case-sensitive set membership** in `get_filtered_data_and_counts` (so review prev/next honors the multi-value filter). `save_review` round-trips the raw string through `dashboard_query` / `filter_args`, and the review pages' `dashboard_query` allow-lists already carry `filter_group` — the value is opaque to them.
- **ME / BF (per-tab, client-side, multi):** each tab's `#filter-group-<tab>` root hosts one instance, registered on `window.groupFilters` and read via `assetGroupFilterValue(tab)` (used by `updateDashboardQuery`, `updateReviewLinks`, `resetFilters`). Selection filters rows through a DataTables anchored OR-regex on the Asset Group column (`^(A|B)$` via `buildColumnRegex`); empty selection = all groups; filtering stays client-side (no reload). The group facet list is rebuilt from **all rows of the tab** (`{search:'none'}`) with the same `setOptions` self-heal + one-shot re-apply guard as Building. Saved DataTables column searches rehydrate via `parseColumnRegex` (legacy single `^X$` states accepted); URL params win over saved state. The XLSX export payload is unchanged — group filtering reaches the export implicitly through the visible-rows `qr_codes` list.
- **EL (simple select, server generalized):** EL keeps its per-tab Asset Group `<select>` (single value written to `filter_group`); only the server-side parse/apply moved to the shared list/set-membership form. EL's `distribution_mode` Distribution split is untouched.

## Building Column Tooltip Rules (Listing Tables)

Refresh: 2026-07-20.

The `Building` column cell on each listing table (New / Update / Manual tabs in ME / BF / EL) renders the bare building **code** in a `.badge.bg-secondary` capsule. On hover it shows a Bootstrap tooltip with the building's full name in the form `<Name> (<Code>)` — e.g. hovering `017` shows `Old Administration Building (017)`.

- **Server context var:** each reviewer passes `building_name_map` (a `{Code: Name}` dict) into `dashboard.html`. ME (`asset_plate_reviewer.py`) and BF (`asset_plate_reviewer_bf.py`) already exposed it for the filter dropdown labels; EL (`Asset_dashboard_EL.py`) gained a matching `_get_buildings_name_map()` helper (2026-07-20) that reads `SELECT "Code", "Name" FROM "Buildings"` via the backend-agnostic `db` layer and returns `{}` on any failure. The client-side `window.BUILDING_NAME_MAP` used by the filter facets is the same data.
- **Template markup:** the Building `<td>` looks up the name with `{% set bname = (building_name_map | default({})).get(item.building) %}` and attaches `data-bs-toggle="tooltip" title="{{ bname }} ({{ item.building }})"` **only when a name is found**. Codes with no matching `Buildings` row render the badge with no tooltip (graceful fallback) — never a bare `(code)` or empty title.
- **Repeated three times per app:** the Building cell markup appears once per tab pane (New / Update / Manual). Any change must be applied to all three occurrences in each app (use a replace-all edit).
- **Tooltip init:** relies on each dashboard's existing DOMContentLoaded pass that instantiates `bootstrap.Tooltip` over every `[data-bs-toggle="tooltip"]`; the listing rows are server-rendered by Jinja so they are present at init. No new JS was added.
- **Not under the four-copy byte-identical rule:** the three `dashboard.html` files are per-app copies that already diverge (per-discipline columns), so this edit is applied to each independently — unlike `building-multiselect.js` / `image-viewer.js` / `review_buttons`.

## Current Confidence UI Rules

- `Avg AI Conf` is part of the review dashboards for ME, BF, and EL.
- Confidence thresholds and slicer labels must match the active UI behavior.
- Any column-order changes require a check of DataTables column index references.

## Confidence and Completeness Gauge Rules

Both the "Avg AI Conf" and "Comp Score" columns on the listing tables (New / Update / Manual tabs in ME / BF / EL) use the same compact semantic score capsule. Each capsule presents a bold percentage, a colored status dot, and a five-segment rail; the headers use Bootstrap Icons `bi-stars` and `bi-check-circle`. Shared CSS class family: `.score-header-label`, `.score-meter`, `.score-value-row`, `.score-status-dot`, `.score-segments`, and `.score-segment`.

Semantic bands:

- Low: score below 70, red `#dc2626`.
- Medium: score from 70 through 79.99, amber `#d97706`.
- High: score from 80 through 100, green `#059669`.
- The five discrete 20-point segments activate at raw scores 1, 21, 41, 61, and 81. A zero score therefore shows a red status dot with no active segments; a missing score remains neutral `N/A`.

Constraints:

- Both columns and all three disciplines use the same component and thresholds for consistent row scanning.
- Each cell retains its raw numeric `data-order` value so DataTables sorting remains numeric; the score capsule is presentation-only.
- The percentage text is the existing localized display value. Tooltips remain `AI confidence: <value>` and `Completeness score: <value>`.
- Each capsule uses ARIA `role="meter"` with numeric bounds/value and a text alternative that includes Low, Medium, or High. A visually hidden level label ensures the semantic state is not color-only.
- `N/A` uses the existing muted fallback and never renders a meter.
- The obsolete green-gradient `.ai-conf-*` and unused `.avg-ai-conf-pill` styles were removed during the 2026-07-14 redesign.
- This redesign does not insert or reorder columns, so DataTables indexes and saved-state versions must remain unchanged.
- **`Review Status` always defaults to "All Statuses" on load** (ME / BF / EL). Server-side `approved_filter` defaults to `""`, and the listing JS forces the `#filter-approved` dropdown to `""` whenever there is no explicit `?approved=` / `?filter_approved=` URL param — it deliberately does **not** restore a persisted Approved/Pending status filter from saved DataTables state (other column filters still persist). An explicit `?approved=True|False` deep-link is still honored. Implemented by using `if (approvedParam === null)` (instead of `if (!hasApprovedState && approvedParam === null)`) in `initAssetTable`'s state-restore block. **EL extra gate:** the landing-page cards (`landing.html`) previously hardcoded `approved='False', filter_approved='False'` in the `review_all` / `review_distribution` links, which forced Pending on entry; those params were removed so EL also opens at All Statuses.
- **Listing tables default-sort by `Capture Date`, newest first** (ME / BF / EL). DataTables `order: [[colCaptureDate, 'desc']]` sets the default for fresh state; `stateLoadParams` injects the same order only when a saved state has no explicit sort, so the default also reaches existing browser state without clearing other saved filters, and a user's manual sort on another column is preserved. `Capture Date` (`date_set[:16]`, ISO `YYYY-MM-DD HH:MM`) sorts chronologically as text, so no `data-order` helper is needed.
- **Review detail pages show capture GPS as read-only metadata** (ME / BF / EL). The field label is `GPS Coordinates (lat,long)` and the value is read from `QR_codes."GPS Coordinates (lat,long)"` by `QR_code_ID`. It is not posted back to JSON, `sdi_dataset`, or `sdi_dataset_EL`; SDI Planon export reads the same QR-level metadata directly from `QR_codes` for the template GPS column.

### Avg AI Conf

- Sourced from `Avg_ai_conf` on each item dict (built by `_load_items_for_process` and equivalents). Backend pulls `_normalize_avg_ai_conf(_extract_avg_ai_conf(raw))`.
- The confidence range slider (`?conf_min` / `?conf_max`) filters rows by this field; the gauge replacement did not change that contract.

### Comp Score

- Sourced from `Comp_score` on each item dict, populated from the JSON top-level `completeness_score` field (written by `API/API_interface_*_ver00.py` during AI extraction).
- The same `_normalize_avg_ai_conf()` helper is reused as a generic 0-100 → "XX,YY%" normalizer; do not refactor it into a separate helper without coordinating all three review apps.
- If the JSON pre-dates the completeness writeback (old assets), the cell renders as `N/A`. Re-running `./run_ai_and_sync.sh <discipline> <qr_code>` populates the field.
- Column position: inserted immediately after "Avg AI Conf" on all three review-app listing pages. This insertion shifted post-column DataTables indices (`colApproved`, `colAction`, plus EL's `colAssetGroup`) — see "Listing-Page Column Visibility Rules" for the current index map.

## Listing-Page Column Visibility Rules

The review-app listing pages (`review_asset_templates/dashboard.html`) may hide fixed nameplate columns from the New / Update / Manual tab tables to keep the listing scannable. This is a presentation-only constraint: the underlying JSON payloads, `QR_code_assets`, `sdi_dataset` / `sdi_dataset_EL`, the SDI export, the Planon export, the AI extraction pipeline, and the per-asset `review.html` page all still carry every field.

Current hidden nameplate columns (0-based DataTables column index on the listing tables):

- **ME listing tables** — Manufacturer (6), Model (7), Serial Number (8), Technical Safety BC (10)
- **EL Distribution listing only** (`/review-distribution`) — Amperage Rating (7), Volts (8), Location (9)

Mechanism:

- ME uses the table `initComplete` callback to hide its fixed listing columns.
- EL Distribution gates the hide with `isDistributionDashboard`, adds the three columns to DataTables `columnDefs`, corrects restored state in `stateLoadParams`, then forces `table.column(idx).visible(false, false)` after initialization. This keeps a previously saved browser DataTables state from restoring the columns.

Constraints to preserve:

- The `<th>` and `<td>` markup for the hidden columns must stay in place; only their display is suppressed. Removing the cells would shift every downstream column index (`colTag`, `colApproved`, `colAction`, etc.) and break search / filter / bulk-action logic that references those indices.
- The per-asset review page (opened via the row's Review button) is a separate template and must continue to render every field. In particular, EL `review.html` still shows and edits `Ampere` / Amperage Rating, `Volts`, and `Location`.
- EL `Ampere` is the editable amperage source. `Amperage Rating` is the Planon-facing alias synchronized from `Ampere`; helper logic must prefer `Ampere` when both keys are present, and a submitted blank `Ampere` must clear both keys instead of falling back to a stale alias.
- EL tag-derived voltage is a default, not an override for human review. When a reviewer saves a non-derived `Volts` value, `volts_manual_override=1` preserves that value through JSON render/save and `sdi_dataset_EL` sync.
- EL rating values are stored bare (`208/120`, `100`) with the unit in the `(UoM)` columns (2026-07-08). `VLT`/`AMP` are the intentional Planon UoM codes on `sdi_dataset_EL` — do not change them; `electrical_building_schema` stores display units `V`/`A` (2026-07-09). Save paths strip unit letters from values; display layers append/map the units.
- EL `Supply From` normalization is an AI/default cleanup, not permission to erase a reviewer-entered value. When a reviewer saves a non-normalized non-empty `Supply From`, `supply_from_manual_override=1` preserves the displayed/stored text, while `Fed From Equipment ID` remains the normalized lookup/export value.
- EL `Fed From Amperage Rating` (+ UoM) is SLD-derived display/export data, not reviewer-editable input (2026-06-12). It resolves from the building's active SLD rows (`electrical_building_schema`, `new_draw='TRUE'`) by matching the normalized `Supply From` against the SLD `Equipment ID`; it stays blank when the building has no SLD data, with no fallback to sibling captured assets. The dashboard required-fields hover checklist counts both fields only for buildings that have active SLD data.
- EL Distribution review shows an advisory amperage warning when both ratings are valid and `Amperage Rating` is greater than `Fed From Amperage Rating`. The warning recalculates live, renders below the positioned amperage input wrapper so it cannot overlap the UoM pill, does not block save/approval, and must not write the generic `Flagged` field or any derived warning state to JSON/DB. The EL review XLSX and SLD Switch Over XLSX red-highlight the Amperage cell for affected rows without exporting a separate flag column; the SLD worksheet also hides gridlines. ME/BF export behavior remains unchanged.
- EL review saves must keep top-level JSON quality metadata (`completeness_score`, `confidence_scores`, `Avg_ai_conf`) aligned with reviewer edits. If this metadata remains stale after a required-field edit, the EL AI checker can reprocess the JSON and erase the human correction.
- The EL General listing (`/review-all`) must continue to show those three fields unless a separate requirement changes that view.
- If a listing-table column order changes, update the DataTables index variables and hidden-column arrays in the same change.

## Photo Column Rules (Listing Tables)

- The Photo column on each dashboard table renders the required-photo ratio (`{present}/{required}`) inside a `.v2-photo-pill` capsule (red for missing, green for complete).
- When the item dict's `Extra Photo` flag is truthy, a small `.v2-photo-extra-chip` shows `+1` next to the pill with tooltip "Extra Photo present".
- The `Extra Photo` flag is populated in each reviewer's item-build path (`asset_plate_reviewer.py`, `asset_plate_reviewer_bf.py`, `Asset_dashboard_EL.py`) by calling `find_image(qr, building, <extra-seq>)` — `-4` for ME, `-3` for BF/EL.
- The chip must never roll into `Photos Summary` itself, since that fraction feeds the red/green pill semantics and the "Missed Photo" KPI. Keep the chip purely cosmetic.
- The chip markup is repeated in all three tab panes (New / Update / Manual) — the templates render the Photo cell three times per discipline. Any change to chip styling or logic must be applied to all three occurrences.
- The Photo value cells are left-aligned (`text-start`) in all New / Update / Manual listing tables for ME, BF, and EL. Keep the Photo header and adjacent action/status columns on their existing alignment unless the table layout is intentionally redesigned.

## Asset Review Sheet (Per-Asset PDF + Self-Contained HTML Export)

ME, BF, and EL each expose a per-asset **Asset Review Sheet** — a one-page, self-contained record of a single reviewed asset, reached from two buttons in the `review.html` header (placed before the approve toggle).

- **PDF** -> `GET /review/<doc_id>/print` (`review_print`): renders the sheet inline with an auto-print script (`{% if auto_print %}`) so the reviewer can Save-as-PDF from the browser. Opens in a new tab (`target="_blank"`).
- **Export** -> `GET /review/<doc_id>/export` (`review_export`): renders the same sheet with `auto_print=False` and returns it as a downloaded, fully self-contained `.html` file named `Asset_Review_<ME|BF|EL>_<safe_qr>_<safe_building>.html` (`send_file`, `as_attachment=True`).
- Both routes reuse **one** shared context builder (ME and BF: `_build_review_sheet_context(doc_id)`; EL: `_build_el_sheet_context(doc_id)`) and **one** `review_asset_templates/review_print.html` template, toggled only by the `auto_print` flag. Both are gated by the same per-app viewer permission as `review()` (`reviewer_mechanical` / `reviewer_backflow` / `reviewer_electrical`); a denied check returns `access_denied_response(...)`.
- **Self-contained invariant.** Every image (the 4 photo slots + the UBC logo) is inlined as a base64 `data:` URI via `_file_data_uri()` (Pillow EXIF-aware downscale to <=1400px JPEG, raw-bytes fallback, `None` when the file is missing). The rendered HTML must contain no `http(s)://`, `url_for`, `/static/`, `/images/`, or CDN references — the exported file opens with no network and no running server. Anything new added to the sheet must follow the same inline-data-URI rule.
- **Building shown as its Name, not its code.** The sheet header shows the building *description* `Buildings."Name"` — ME and BF via `_get_buildings_name_map()`, EL via `_el_building_name(code)` (`SELECT "Name" FROM "Buildings" WHERE "Code" = ?`, parameterized; falls back to the raw code when not found). Name only — do **not** append the "| Owner Rep" suffix that `get_building_display()` adds.
- The sheet is **read-only**. It renders the same resolved values as `review()` (dictionary-first tag resolution, description resolve, discipline default Attribute, AI-confidence display, capture info) but writes nothing to JSON, `sdi_dataset` / `sdi_dataset_EL`, or the DB.
- **Installation Date + Capture Notes (2026-07-10).** The sheet renders two QR-level values read-only: `QR_codes.installation_date` (display `DD/MM/YYYY` via `get_installation_date`) as a field row directly below **Year** (ME: Identity column; BF: Classification column) or below **Main Asset** in Identity for EL (which has no Year field — mirrors the EL review page, where the input closes the Identity card); and a **Capture Notes** section (`QR_codes.capture_notes` via `get_qr_capture_notes()`) placed between the two-column block (Identity/Classification for ME/BF; Identity/Technical Details for EL) and the next section (User Activity Log for ME/BF; Single Line Diagram for EL). An empty note renders the muted placeholder "No capture note"; note text keeps its line breaks (`white-space: pre-wrap`).
- **Discipline isolation.** ME, BF, and EL each own their own copy of the two routes, the context builder, and `review_print.html`. There is no shared module — a change in one discipline must not touch the others.

### EL-only: embedded Single Line Diagram strip

When the reviewed EL asset is present in the active SLD data (`electrical_building_schema`, `new_draw='TRUE'`), the EL sheet embeds a server-reconstructed **end-to-end branch** of the diagram as inline SVG — built from the DB, **not** cropped from the source PDF.

- `_get_sld_branch_tree(building, *tags)` builds the lineage tree: upstream ancestors (climbing `Supply From` toward the root) -> the reviewed asset -> its full downstream subtree (every asset it ultimately supplies). The asset's **siblings are intentionally excluded** (they are not on the asset's own feeder path). Matching is `UPPER(TRIM(...))` on `Building` + `Equipment ID` (then `Supply From` for the descent), `new_draw='TRUE'` only.
- `_build_sld_branch_svg()` renders the tree as a **horizontal "ladder"** (tree depth -> x, leaf order -> y) sized to fit one page (height capped at ~95mm in the template so it never spills to page 2). Nodes are colored by Equipment-ID prefix (`SLD_TYPE_COLORS`: CDP/MDP/MDC navy, SWBD, ATS, TX, PNL, SPL, default), carry an equipment-type glyph icon on a white chip (the same glyphs as the SLD-chart legend), the QR code above, and the rating below (`Voltage | Amperage | Power`, via `_sld_rating_text`).
- Two pennant **flags** mark the lineage: **red = Current Asset**, **blue = Supply From** (the immediate parent). No "This asset" / "Supply From" text labels on the boxes — the flags carry that meaning.
- `_sld_legend_html()` renders a legend below the diagram: the red + blue flags and the equipment-type icons (Panel / Transformer / ATS / PNL / Splitter / Switchboard). It is emitted into the context (`sld_legend_html`) only when an SLD strip exists.
- When the asset is on no active diagram, the sheet shows a short **"No Single Line Diagram available for this asset."** note instead.
- Bounds (truncation is flagged in-tree): `SLD_MAX_ANCESTORS=6`, `SLD_MAX_DESC_DEPTH=4`, `SLD_MAX_CHILDREN_PER=10`, `SLD_MAX_NODES=40`.

### Sheet layout

- **EL:** Description -> Identity (UBC Asset Tag / Asset Group / Attribute / Main Asset / Installation Date) / Technical Details (Amperage, Voltage, Power Rating, Supply From, Fed From, Equipment ID, Equipment Type, Power Type) -> Capture Notes -> Single Line Diagram -> Asset Photos (hero = Panel Schedule `-2`; thumbnails `-0` / `-1` / `-3`; grey "Missing" placeholder when absent) -> User Activity Log (captured-by, date, hour, GPS) -> footer (generated-on + user).
- **ME:** Description -> Identity (Manufacturer / Model / Serial # / Year / Installation Date) + Classification (UBC Asset Tag / Technical Safety BC / Asset Group / Attribute / Main Asset) -> Capture Notes -> User Activity Log -> Asset Photos (hero = Main Picture `-2`; thumbnails `-0` / `-1` / `-3` / `-4`). No SLD section.
- **BF:** Description -> Identity (Manufacturer / Model / Serial # / Diameter) + Classification (UBC Tag / Year / Installation Date / Application / Asset Group / Attribute) -> Capture Notes -> User Activity Log -> Asset Photos (hero = Main Asset `-2`; thumbnails `-0` / `-1` / `-3`). No SLD section. Building Name via `_get_buildings_name_map()`; permission `reviewer_backflow`.
- All three sheets put **Description first**, above Identity.

## Review Action Button Rules (ME / BF / EL)

Current documentation refresh: 2026-07-09.

The action buttons on the single-asset review page (`review.html`) are rendered from a **canonical registry**, not hand-coded HTML.

- **Registry:** `review_buttons.py` at each app root — a frozen `ReviewButton` dataclass plus the `REVIEW_BUTTONS` list recording every button's label, position (`top`/`footer`), left-to-right order, kind (`submit`/`js`/`link`), form `action` value, endpoint key, DOM id, CSS classes, and lock behavior.
- **Renderer:** `review_asset_templates/macros/review_buttons.html` — `render_review_buttons(buttons, position, ctx)`, called once for the top bar and once for the footer. `ctx` carries `doc_id`, `approved`, `package_locked`, `review_locked`, `package_lock_message`, `endpoints`, `embedded` (`g` is not visible inside the macro import, so `g.embedded` is evaluated at the call site).
- **THREE-COPY RULE:** both files exist byte-identically in the ME, BF, and EL apps (same rule as `image-viewer.js`). Edit all three together; `git diff --no-index` between copies must stay empty.
- **Per-app endpoints are NOT in the registry.** Each app passes a `review_endpoints` dict to `render_template`: ME/BF use `{"print": "review_print", "export": "review_export", "dashboard": "index"}`; EL uses `main.`-namespaced names and a **dynamic dashboard target** (`dashboard=base_route`, i.e. `main.review_all` or `main.review_distribution`).
- **Canonical order** — top: Save (save-stay), Pending/Approved toggle, PDF, Export, Dashboard; footer: Prev, Save Changes, Save & Next (relabels to "Next" when locked), Skip, Save (save-stay), Pending/Approved toggle. BF and EL were intentionally reordered to this ME order on 2026-07-09 (they previously led with PDF/Export) and their `d-none d-sm-inline` responsive label spans were dropped (labels always visible).
- **DOM-id JS contract** (the inline `<script>` in each `review.html` queries these ids — never change them): `saveStayNav`, `approveToggleNav`, `printPdfTop`, `exportHtmlTop`, `backTop`, `saveNextLabel`, `saveStayFooter`, `approveToggleFooter`.
- **Lock semantics:** `lock="package"` renders `disabled aria-disabled="true"` (plus the package-lock message as `title` on the toggle) when `package_locked`; `lock="data-lock-save"` emits `data-lock-save="true"`, which the existing inline JS disables when `review_locked`.
- All `submit`/`js` buttons post the single `#reviewForm` to `save_review` with `action` = `save_prev` / `save` / `save_next` / `save_stay` / `save_toggle`; behavior of those actions is unchanged and lives in each app's `save_review` handler.
- EL's former header chrome links — "Main Dashboard" (embedded mode) and "Sign out" (standalone) — were **removed 2026-07-09** so EL's review-page top bar matches ME/BF exactly (only the canonical five buttons plus the shared `User: <name>` label). They are not part of the `review_button` set. (Log out / return to the EL landing from the dashboard listing instead.)

## Review Image Viewer Rules

- ME, BF, and EL `review.html` pages use the same image-viewer behavior for source-photo inspection.
- The visible controls are zoom in, zoom out, rotate clockwise, and reset image. Keep `title` and `aria-label` values on these buttons.
- The image stage is keyboard-focusable and supports `+`, `-`, `0`, `R`, and arrow-key panning.
- Mouse wheel zoom is scoped to `.main-stage`; dragging pans only the active image and must not interfere with form fields, thumbnails, Dashboard iframe scrolling, or the ME map overlay.
- Thumbnail changes reset zoom, rotation, and pan state before showing the new image.
- Each review app serves its own `review_asset_templates/static/image-viewer.js`. Keep the three copies behaviorally identical unless a deliberate shared static asset route is added.

## Bulk Action Rules (ME + BF + EL)

The ME, BF, and EL review-app tab tables (New / Update / Manual) expose **bulk Manual** and **bulk Approved** master checkboxes inside each table's column header (`<th>`). BF and EL were added to match the existing ME behavior.

- The bulk checkboxes drive a client-side queue that calls the existing per-row endpoints `POST /toggle_sdi/<doc_id>` and `POST /toggle_approved/<doc_id>`. Do not introduce a parallel bulk endpoint.
- Confirmation goes through the shared Bootstrap `#confirmModal` and the `showConfirm(title, message, onConfirm)` helper, with a `window.confirm()` fallback if the modal is unavailable.
- All three review-app modals (`#confirmModal`, `#infoModal`, `#planonModal`) use `modal-dialog modal-dialog-centered` so the dialog is vertically centered in the viewport. This matters in embedded mode: a top-aligned modal (plain `modal-dialog`) has its header clipped by the Dashboard's sticky top bar inside the iframe. Keep `modal-dialog-centered` on every modal in `dashboard.html`.
- Operate only on rows currently visible to DataTables (`rows({ search: 'applied' }).nodes()`); filters and pagination must scope the action.
- Manual / Approved header labels use the `.review-bulk-header` wrapper and table headers use vertical middle alignment so the label and checkbox stay centered.
- BF bulk header checkboxes render only when `can_edit` is true. EL relies on the existing endpoint-enforced permission behavior.
- Client-side safety filters that must remain in place:
  - Bulk-Manual skips rows where the Approved cell shows `data-search="True"`.
  - Bulk-Approved uncheck calls `GET /check_sdi/<qr_code>` first and skips rows that already exist in an SDI package.
  - ME, BF, and EL skip any package-locked row for both bulk Manual and bulk Approved. Package-locked means the QR exists in either `sdi_print_out` or `sdi_print_out_arch`.
  - Rows where the current cell state already matches the requested header state are skipped silently.
- Process rows sequentially, update each changed Manual / Approved cell in place from the endpoint response, invalidate the affected DataTables row, and redraw the current table after the queue drains.

## Rename Rules

- Only temporary QR codes may be renamed to a permanent code.
- Renames must update JSON, images, processed logs, DB rows, and review navigation context.

## Cross-App Consistency Rules

- When changing one review app, evaluate whether the same rule applies to the other two.
- Discipline-specific exceptions are valid, but they should be intentional and documented.

## AI Status Reprocess Workflow (ME / BF / EL)

Current documentation refresh: 2026-06-25.

Toggling **AI Status** to `0` (off) in the review dashboard listing table triggers a full re-extraction of the asset on the next cron cycle, subject to a protection hierarchy. The `toggle_ai_status` route handles all protection checks and the JSON file move. The feature is identical across ME, BF, and EL; see discipline-specific notes below for minor implementation differences.

### Protection hierarchy

| Condition | Response code | Forceable? | Resolution |
|---|---|---|---|
| Asset is in active or archived SDI package (`sdi_print_out` / `sdi_print_out_arch`) | `409 Conflict` | No | Retrieve asset from package first |
| JSON `structured_data.Approved == "True"` | `reprocess_blocked` | No | Un-approve the asset first |
| `QR_code_assets.Col_process = "2"` (Manual Entry) | `manual_entry_locked` | No | Not applicable for AI reprocess |
| JSON `content["modified"] = True` (human-edited) | `reprocess_blocked` (forceable) | **Yes** | "Force re-run AI?" confirm dialog |
| Fresh AI result (no protection flag) | — | n/a | JSON moved aside; re-extraction on next cron cycle (≤ 2 min) |

### Guard semantics

- All blocked responses return **HTTP 200** with `{"success": false, "code": "...", "error": "...", "forceable": bool}`. They do **not** use `4xx`, so the dashboard `.done()` callback surfaces the specific human-readable message. A `4xx` would invoke `.fail()` and show only a generic "Server Error".
- `forceable: true` causes the dashboard to show a **"Force re-run AI?"** confirm dialog. If the reviewer confirms, the dashboard re-posts with `force=1`, which bypasses the `modified` guard **only**.
- `approved` and `manual_entry` blocks are never forceable from the UI — they require an explicit un-approve or represent intentional Manual Entry state that should not be silently discarded.

### File move mechanics

- When reprocess is permitted (fresh or forced), `toggle_ai_status` moves `Output_jason_api/<QR>_<TYPE>_<Building>.json` to `Output_jason_api/<QR>_<TYPE>_<Building>.json.bak_<YYYYMMDDHHMMSSz>` **after** `conn.commit()` to prevent crash-time orphan state (file gone, DB transaction not committed).
- A path-traversal guard (`os.path.realpath()` + `os.path.commonpath()`) prevents a crafted `doc_id` from escaping the JSON directory.
- The response includes `"reprocess_moved": "<backup filename>"` when a file was moved.

### Skip-if-exists guard interaction

The extraction pipeline returns `STATUS_SKIPPED_EXISTS` when `Output_jason_api/<QR>_<TYPE>_<Building>.json` already exists on disk, regardless of `ai_status`. Setting `ai_status = 0` alone is **not sufficient** to trigger re-extraction — the JSON file must be absent. The reprocess feature clears this guard by moving the JSON before the next cron run.

### Helper: `reset_me_asset.py` (ME only)

For one-off maintenance when the dashboard reprocess button is impractical:

```bash
python /home/developer/API/reset_me_asset.py <qr_code>          # dry run
python /home/developer/API/reset_me_asset.py <qr_code> --apply  # executes
```

Requires PostgreSQL backend; refuses SQLite unless `--allow-sqlite`.

### Audit and discipline differences

- **ME and BF:** reprocess action is logged to `audit_trail` via `_audit_log_change` with `source="human"`. Forced actions also use `source="human"` to distinguish from normal AI runs.
- **EL:** no `_audit_log_change` helper is available; the reprocess/force action is logged via `print()` to the Gunicorn journal (`[reprocess] EL FORCED reprocess for <QR> by <user>`).

## AI Status Auto-Refresh (ME / BF / EL)

The dashboard listing tables poll a read-only endpoint so the **AI Status** ✅/☐ cells reflect extraction progress without a manual page reload.

- Endpoint: `GET /api/ai_status_map` in each review app returns `{"success": true, "statuses": {"<QR>": "0"|"1", ...}}` from `QR_codes.ai_status`. It is strictly read-only — it must never write DB or JSON state and never trigger extraction.
- Client: each `dashboard.html` runs `refreshAiStatusCells()` on a 60 s `setInterval` (`AI_STATUS_POLL_MS`). It targets `td[data-ai-cell]` — a dedicated marker attribute on the AI Status `<td>`, independent of the click-binding `ai-status-cell` class (which BF renders only for editors) — extracts the QR as the first `_`-separated token of `data-docid`, updates `data-val` and the cell glyph only when the value changed, then invalidates affected DataTables rows and redraws with `draw(false)`.
- The poller skips cycles while the browser tab is hidden (`document.hidden`) and never overlaps requests (`aiStatusPollBusy` guard).
- Keep the poller block and endpoint behaviorally identical across ME, BF, and EL (same rule as `image-viewer.js`). Permission decorators follow each app's local convention (ME: `reviewer_mechanical` viewer; BF: `login_required` only, matching `/check_sdi`; EL: `reviewer_electrical` viewer).

## Validation Checklist

- Save, approve, AI-status toggle, and SDI toggle all persist correctly.
- Manual Entry records do not leak into SDI when excluded.
- Navigation preserves dashboard filters after save / next / previous actions.
- Image viewer controls work standalone and embedded: buttons, wheel zoom, drag pan, reset, rotate, thumbnail reset, and keyboard shortcuts.
- AI Status toggle on a fresh asset moves the JSON and returns `reprocess_requested: true`.
- AI Status toggle on an approved asset returns `success: false, code: "reprocess_blocked"` without moving any file.
- AI Status toggle on a human-edited (modified) asset returns `success: false, forceable: true` and shows the confirm dialog.
- Forcing reprocess on a modified asset moves the JSON and re-extracts on next cron cycle (≤ 2 min).
- AI Status cells refresh automatically (≤ 60 s after `ai_status` changes in the DB) without a page reload; the poller performs no writes.

## Embedded Mode Rules (`?embedded=true`)

The review apps run both standalone (their own subdomain) and embedded (inside the central Dashboard's iframe). Embedded mode is detected once per request and used by templates to suppress sub-app chrome.

### Detection

- Each app's `before_request` hook (on `app` for ME / BF, on `main_bp` for EL) sets:
  ```python
  g.embedded = request.args.get('embedded', '').lower() == 'true'
  ```
- The hook must run for the static / image / API endpoints too so `g.embedded` is always defined when templates render. Place the assignment before any early `return` for excluded endpoints.

### Known auth-flow gotcha

- The current `auth_bp` login form (`review_asset_templates/login.html`, action `{{ url_for('auth.login') }}`) does **not** carry the `next` query parameter into the POST. The handler reads `request.args.get('next')` on POST and gets `None`, falling back to `url_for('index')`. Net effect: a user landing on `/?embedded=true` who is redirected to login is sent to plain `/` after authentication, and `g.embedded` evaluates `False` on that subsequent request.
- Practical consequence: do not depend on `?embedded=true` (or any URL flag) surviving a fresh-login round-trip in these three review apps. If a feature needs to behave differently in the iframe and is gated server-side, either fix the login template to preserve `next` (`{{ url_for('auth.login', next=request.args.get('next')) }}` or a hidden `<input name="next">`), or rely on client-side detection (`window.self !== window.top`, document referrer, etc.).
- This does not break the cross-subdomain shared-cookie flow: a user already logged in on `dashboardprod` reaches the iframe with `?embedded=true` intact, because no login redirect happens. The gotcha is specific to the fresh-authentication path (incognito, expired session, password reset, etc.).

### Cookie configuration

- Every review app must set `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, plus the matching `REMEMBER_COOKIE_*` triple.
- These are required for cross-subdomain cookie delivery from the Dashboard iframe; without them the sub-app sees no session and immediately redirects to login.

### Template rules

- The top user-nav (`.user-nav`), brand header, page-header logo block, and user dropdown are wrapped in `{% if not g.embedded %}` so they do not render inside the iframe.
- Functional controls (building selector, archive toggle, "User Activity" button, meta-pills, approve toggle, "Dashboard" back-to-list button) must remain visible in embedded mode. Do not hide the entire toolbar.
- The body element receives `embedded-mode` as a class when `g.embedded` is true. CSS uses this class as a belt-and-suspenders fallback.
- Internal back-to-list links (`url_for('index')` for ME / BF, `url_for(base_route)` for EL, `url_for('main.dashboard')` for SDI) must append `?embedded=true` when `g.embedded` is true so embedded state survives back-navigation.

### Link propagation script

- Each sub-app template includes a small bottom-of-body script that intercepts `<a>` clicks. If `?embedded=true` is in the current URL it appends `?embedded=true` (or `&embedded=true`) to internal links before the browser navigates.
- The script ignores anchor-only (`#`), absolute (`http://`), and `mailto:` hrefs.
- DataTables-rendered links must still be intercepted; the listener is attached to `document` so it works regardless of when rows are rendered.

### Cross-origin "back to main" navigation

- The "Dashboard" button that navigates back to the central main view lives in the central Dashboard's process-view-header, not inside the sub-app. Sub-apps do not need their own button for this.
- Optional cross-origin notification uses `window.parent.postMessage({action:'go-to-main'}, 'https://dashboardprod.assetcap.facilities.ubc.ca')`. The target origin must match the central Dashboard's actual hostname exactly; mismatched origins cause silent message drops.

### EL-specific extras

- EL has three templates that need embedded treatment: `landing.html`, `dashboard.html` (review-all / review-distribution), and `review.html`.
- EL `dashboard.html` must include `_shell.html` in the `<head>` like ME and BF. The shared shell script suppresses itself inside iframe / `?embedded=true` contexts and adds `acshell-host` spacing only for standalone pages.
- EL's standalone `Main Dashboard` button inside the toolbar links to `url_for('main.landing')?embedded=true`, returning the user to the EL hub page within the iframe.
- EL required-field checklist popovers (`.el-required-popover`) are appended to `body` and must sit above the shared shell sidebar (`.acshell-sidebar` uses `z-index: 9998`). Keep the popover layer above that value (`--bs-popover-zindex` / `z-index: 10050`) so hover cards are not clipped by the shell.

## Archive visibility, SLD dropdown, and approval UI (2026-06-01)

- **Archive toggle = persistent pressed state (ME / BF / EL, 2026-06-01).** State is `archive_filter_active = (request.args.get("archive") != 'false')`; "archived" means the QR appears in `sdi_print_out_arch` (`get_archived_qrs()`). The review header button **always reads "Show Archive"**; when archived rows are shown (`archive_filter_active` is false) it carries the `v2-header-btn--archive-on` class and is shaded cream (`#fff8dc`, inset shadow) to read as "pressed". The pressed state is server-driven from the `archive` URL param, so it survives reloads and clears only on un-press (click again) or filter **Reset** (`resetFilters()` deletes `archive`). All three review apps share this; EL additionally widens its building selector (`.v2-building-select`) to 480px max / 400px min.
- **Filters preserve each other's params.** `updateDashboardQuery()` no longer deletes `archive` on a Review Status / tab change, and the archive button carries the current `approved` filter (`url_for(..., approved=approved_filter_raw)`). Consequence: an *Approved + Show Archive* view holds across reloads — previously each control reset the other's URL param. Applies to EL, ME, and BF.
- **EL SLD building dropdown is archive-aware.** `get_buildings(include_archived=False)` in `sld_blueprint.py` lists a building only if it has at least one `sdi_dataset_EL` QR not in `sdi_print_out_arch`; a building whose QRs are all archived is hidden. `GET /sld/api/buildings?include_archived=true` returns the unfiltered list. EL-specific.
- **Bulk Approve surfaces errors.** The `select-all-approved` handler records per-row failures and shows a summary modal (auth/session/server) at the end instead of silently no-op'ing. EL-specific.
- **ME/BF/EL package lock for status fields.** `AI Status`, `Manual`, and `Approved` are read-only when the QR appears in active package table `sdi_print_out` or archive table `sdi_print_out_arch`; `/check_sdi/<qr>` reports both tables and the three per-row toggle endpoints return HTTP `409` before writing DB or JSON state.
- **Package approval guard.** Automated review JSON sync must not clear approval for any QR already in `sdi_print_out` or `sdi_print_out_arch`; it rewrites JSON approval to `"True"` when needed and persists source-table approval as `"1"`.
- **Flow integrity audit + remediation.** `scripts/audit_sdi_flow_integrity.py` (read-only) flags assets stuck in the approve→package→archive flow; `special_processes/sdi_flow_remediation.md` documents per-asset remediation using the app endpoints (`/toggle_sdi`, `/toggle_approved`, `/review`) — never raw DB/JSON edits except approved, backed-up production migrations with a timestamped report.

## Review Navigation — Archive Persistence and Dashboard-Ordered Sequence (ME / BF / EL) (2026-06-24)

The per-asset review page's prev/next sequence and the "Show Archive" toggle now follow the dashboard view the reviewer actually had. These rules apply identically to **all three** review apps (ME, BF, EL); each keeps its own copy of the routes/templates (discipline isolation), but the behavior must stay in sync.

### The Review-link selector is `a.v2-btn-review`

The per-row **Review** button in `dashboard.html` is `<a class="v2-btn-review">`. **All JavaScript that rewrites or reads those links must select `a.v2-btn-review`** — `updateReviewLinks`, the localStorage order capture, and the approve-toggle href refresh. Selecting the old `a.btn-primary` matches nothing (that class is only the modal OK button), which silently drops every filter/sort/archive param from the Review links and detaches the review sequence from the dashboard. This was the root cause of the dashboard-vs-review count mismatch (e.g. dashboard "111" vs review "57"); do **not** reintroduce `a.btn-primary` for review links.

### "Show Archive" survives the review round-trip

The Show Archive state set on the dashboard must persist when the reviewer opens an asset and returns (via the Dashboard back-button, Save & Next/Prev, or reload). Four mechanisms keep it aligned:

- `TRANSIENT_DASHBOARD_QUERY_KEYS` in `json_persistence.py` is now **empty** — `archive` is no longer stripped from the back-navigation query by `normalize_dashboard_query`.
- `buildDashboardQuery()` in `review.html` preserves `archive` (it is in the explicit carry-over list, no longer deleted).
- `save_review`'s `filter_args` includes `archive`, so the prev/next neighbor scope respects the archived/active view.
- The three server-rendered Review links carry `archive=('true' if archive_filter_active else 'false')` directly, and `updateReviewLinks` re-applies the page's current `archive` param — so the scope is correct even before the DataTables `draw` fires and if JS fails.

### Prev/next follows the dashboard's filtered + column-sorted order

The review sequence (Save & Prev / Save & Next, the "Showing X of Y" counter, and the Next Asset preview) follows exactly what the reviewer saw on the dashboard, **including any column sort**:

- On every `draw`, `updateReviewLinks` captures the table's visible rows in `dt.rows({ search: 'applied', order: 'applied' })` order and writes the `doc_id` list to `localStorage('reviewOrder')`, keyed by tab (`new` / `update` / `manual`).
- `review.html` reads `reviewOrder` for the current tab, finds the current `doc_id`, sets the hidden `nav_prev` / `nav_next` fields, updates `#navCounter`, and drives the Next Asset preview rail. The list persists across page hops, so repeated Save & Next keeps following the same order.
- `save_review`'s neighbor resolver honors the client `nav_next` / `nav_prev` form fields first (validating the target JSON exists), and falls back to the server order only when they are absent or stale.
- The server fallback sort in `get_filtered_data_and_counts` is **Capture Date, newest first** (was `doc_id`), matching the dashboard's default so the no-JS path stays sensible. The building and asset-group filters are sourced robustly in `updateReviewLinks` (multi-select registry accessor → URL param).

### Preview endpoint

`GET /api/asset-preview/<doc_id>` (per app, `@login_required` + the app's `viewer` permission) returns lightweight JSON — `qr_code`, `ubc_tag`, `location`, and thumbnail `images[{url,label}]` — for an arbitrary asset, so the client can render the "Next Asset" rail for whatever the next item in the visible order is. Returns `{error}` with 400 / 403 / 404 on bad id / no permission / missing JSON. It is read-only.

## EL Legacy Flow Rules (2026-07-28)

EL buildings captured before the standard tag scheme (`CDP-6-N-1-L-1`) use free-text lamacoid labels (`PANEL EPH`, `MCC2`, `ATS`, `FED FROM SD-E4 RM. 407`). The standard-flow derivations (`_derive_volts_loc`, `derive_electrical_equipment_type`, `derive_electrical_power_type`, `normalize_el_supply_from_tag`) either return nothing or actively misparse these labels (e.g. `normalize_el_supply_from_tag` strips the word "MAIN" and cannot distinguish `MDC` from `DCC`; the standard system-code parser misreads `PNL-UPS/CM` as Standby). The **legacy flow** is a parallel, gated rule set that fills every field it legitimately can from a legacy label without ever touching the standard code paths.

### Gate: `Buildings.Process`

- `review/Asset_dashboard_browser_EL/legacy_flow.py` is the single source of truth for all legacy rules (ident parsing, ratings, supply-from normalization, equipment metadata, corroborated power type, X-tag composition). It has no Flask imports and is shared by the EL review app and the SLD extractor.
- `get_building_process(conn, building_code)` reads `"Buildings"."Process"` for the capture's building: `'Standard'` → existing code path, byte-identical, untouched. `'Legacy'` → the rules below via `apply_legacy_rules(data)`. Blank/NULL/missing row → raises `BuildingProcessError` — **stop that record's processing and warn; never silently default to Standard.**
- In a Flask request handler, the warning is a `flash(str(exc), ...)` plus a redirect back to the referring/reload page. In a non-request context (dashboard list-building loop, batch/sync paths), the warning is a `print("[WARN] ...")` and the current record is skipped (`continue`) — same invariant, no page to flash into.
- `apply_legacy_rules(data)` mutates `data` in place, blanks only, and never overwrites a non-blank field; it also respects `volts_manual_override == "1"` (invariant 6 — never erase a human override).
- **Upstream derivations are also gated, not just the final dictionary-priority call.** At every call site, `Buildings.Process` is looked up once per record and reused throughout: for the read/display paths (`load_json_items`, `_build_el_sheet_context`, `review()`), `_compute_el_upstream_fields(data, process)` computes Equipment ID / Equipment Type / Power Type / Supply From / Fed From Equipment ID via the legacy functions (not the standard helpers) whenever `process == 'Legacy'`, so those fields are never pre-filled/force-set with standard-derived values before `apply_legacy_rules` gets to see them — Equipment ID in particular is otherwise always non-blank (it's just the trimmed tag), which would permanently block the legacy ident-normalization rule. In `save_review`, the force-overwrite block that keeps Equipment ID/Equipment Type/Power Type/Supply From/Fed From Equipment ID in sync on every save runs only for `'Standard'`; for `'Legacy'` those fields are left for `apply_legacy_rules` to blank-fill (never overwrites a non-blank reviewer value; corroborated Power Type fills blank only), and Supply From is kept exactly as submitted (never run through the standard normalizer, which strips "MAIN" and destroys the MDC/DCC discriminator).
- **The DB write path (`sdi_dataset_EL` / Planon export) is gated too.** `_sync_db_from_structured(..., process=None)` re-derives Equipment ID/Equipment Type/Power Type/Supply From/Fed From Equipment ID with the standard helpers immediately before the upsert by default (`None`/`'Standard'`, byte-identical) — this runs from `save_review`/`toggle_approved` *after* `apply_legacy_rules`, so without gating it would silently overwrite the correct legacy values in the DB row even though the reviewed JSON held them correctly. All call sites that have a building code in scope pass `process=_process` (a per-record hoisted lookup, reusing one connection across a loop where applicable): `save_review`'s four sites, `toggle_approved`'s one site (which also gates its own upstream `structured["Supply From"]`/`structured["Fed From Equipment ID"]` force-set the same way `save_review` does — a JSON API endpoint with no template/redirect, so a `BuildingProcessError` returns a JSON error tuple instead of flash+redirect), and `sync_json_directory_to_db_el`'s batch loop (the load-bearing batch path — `force_resync` can replay every EL JSON through it, e.g. after a schema migration, so a missing per-record gate there would clobber Legacy DB rows on every bulk resync; a blank/missing Process logs a warning and skips just that record, matching `load_json_items`). For `'Legacy'` the sync reads Equipment ID/Equipment Type/Power Type/Supply From/Fed From Equipment ID back from the structured data as-is instead of re-deriving them. The image-placeholder bootstrap (`sync_image_directory_to_db_el`) intentionally keeps the default and is NOT gated: it always calls with `sd={}`, and every one of the five gated fields is a proven no-op on blank input on either branch (see the code comment at its call site), so `process=` could not change its outcome.
- **Ratings rules (Field-Gap Map rules 2–3) are no longer dormant for newly-extracted legacy JSONs (updated 2026-07-29).** `apply_legacy_rules` reads `data.get("label_text")` for the raw ratings line (`"120/208V - 3PH - 4W 100A"`). The extraction-side gate (see "Extraction-side gate (2026-07-29)" below) now populates `structured_data.label_text` with the verbatim plate transcription for every Legacy-building QR processed through `_process_legacy_asset`, so the Volts/Ampere/Voltage Rating/Amperage Rating fills genuinely activate the first time a reviewer opens/saves one of those records. This does **not** retroactively help pre-feature Legacy JSONs that predate the extraction gate (no top-level `"process"` key, `label_text` still absent) — see the operational caveat in that subsection. `compose_x_tag(prefix, voltage, system, sequence)` (Field-Gap rule 10's display-aid X-tag, e.g. `DCC-2XXD1`) also gained a caller on the extraction side: `legacy_structured_from_raw` composes it directly for `MDC`/`DCC`/`MCC`/`SWBD`/`SPL`/`CDP`/`MDP` identities once voltage or sequence is known, and that composed value is what gets stored as `UBC Asset Tag` — but `apply_legacy_rules` (the review-app blank-fill pass) still never calls it, so it remains without a review-side consumer. The `breaker` flag returned by `legacy_ratings_from_label` (rule 3's `BRK` context, intended for the Description) is still dormant — `apply_legacy_rules` continues to ignore it. Do not delete unused pieces as dead code.
- **SLD extraction limitation (legacy PDFs).** The extractor's PDF text-layer harvest (`extract_candidate_ids_from_text`) only matches hyphenated/standard tag shapes (`PNL-…`, `CDP-…`, `TX-…`, `MDC`, …); it does **not** yet harvest bare legacy labels such as `PANEL U` or `MCC2`, so on a legacy drawing those IDs reach the pipeline only when the model itself emits them (the `legacy_dictionary_shim` then accepts/normalizes them — it widens validation, it does not widen candidate discovery).

### Deploy sequence (do these in order)

1. **Schema migration.** The gate requires `"Buildings"."Process"` to exist. Run `scripts/migrate_buildings_process_column.py` against the target database before deploying this flow (idempotent; backfills every blank row to `'Legacy'`, the current fleet default). Without it, `get_building_process` raises `UndefinedColumn` (500) on every request. Blank/NULL `Process` values after migration are the intended stop-and-warn state, not a bug — see above.
2. **Re-classify buildings BEFORE any reviewer opens the app.** ⚠️ The migration backfills **every blank row to `'Legacy'` — all 327 buildings on the current fleet**, which flips every existing standard-tagged EL record onto the legacy code path on day one — the standard force-sets stop running, Supply From stops being normalized, and the legacy blank-fill rules start touching records they were never meant for. That is a fleet-wide behavior change, not a no-op default. Classify each building by the tag shapes already present in its EL JSONs and set `Process='Standard'` wherever standard tags dominate; leave `'Legacy'` only where the free-text lamacoid shapes dominate. Mixed buildings: pick the dominant shape, then spot-check the minority records after cutover (there is no per-record override — the gate is per building). A one-liner to produce the classification (run from the repo root, then review before writing):

   ```bash
   python - <<'PY'
   import collections, glob, json, os, re
   NAME = re.compile(r'^[A-Za-z0-9]+_EL_(\d+(?:-\d+)?)\.json$')          # JSON_NAME_RE
   STD  = re.compile(r'^(CDP|PNL|SWBD|ATS|TX|SPL|MDP|GEN)[- ]?[26][NESU]', re.I)  # standard tag head
   shapes = collections.defaultdict(collections.Counter)
   for p in glob.glob('Output_jason_api/*_EL_*.json'):
       m = NAME.match(os.path.basename(p))
       if not m:
           continue
       with open(p, encoding='utf-8') as f:
           sd = (json.load(f) or {}).get('structured_data') or {}
       tag = str(sd.get('UBC Asset Tag') or sd.get('Branch Panel') or '').strip()
       shapes[m.group(1)]['standard' if STD.match(tag) else ('legacy' if tag else 'blank')] += 1
   for b, c in sorted(shapes.items()):
       print(b, dict(c), '->', 'Standard' if c['standard'] >= c['legacy'] else 'Legacy')
   PY
   ```

   Run against the 182 EL JSONs present on 2026-07-28 this prints four buildings — `217` (97 standard / 21 legacy / 2 blank), `314-1` (23/5/1), `459` (5/1), `750` (19/6/2) — i.e. **every EL building currently captured classifies as `Standard`**, and all four would have been silently flipped to the legacy path by the backfill alone. That is the concrete reason this step is not optional.

   Apply the reviewed result with parameterized SQL, e.g. `UPDATE "Buildings" SET "Process" = %s WHERE "Code" = %s` per building (never string-format the codes in — invariant 1).
3. **Pre-deploy data checks.** Both are cheap and both catch silent-disappearance failures:
   - **Every distinct building code in `Output_jason_api` EL filenames must join to `"Buildings"."Code"`.** Filenames are `{QR}_EL_{building}.json` (`JSON_NAME_RE`). A code with no `Buildings` row raises `BuildingProcessError` in `load_json_items`, which logs `print("[WARN] Skipping ...")` and `continue`s — so those assets **vanish from the review dashboard with nothing but a line in the server log**. Diff the two sets before deploying and insert any missing `Buildings` rows (with a deliberate `Process`).
   - **Audit the distinct tag shapes per building** (the script in step 2 already prints them). A building whose counter shows both shapes in similar numbers is a re-classification judgement call, not an automatic pick; a building showing only `blank` has nothing to classify from and should be set deliberately rather than left on the backfilled default.
4. **Deploy, then verify** one Legacy building and one Standard building end-to-end (review page → save → `sdi_dataset_EL` row) before letting reviewers loose.

### Field-Gap Map (the specification)

Legend: ✅ printed on legacy lamacoids · ⚠️ partially derivable · ❌ not available in legacy.

| # | Field (sdi_dataset_EL / electrical_building_schema) | Standard-flow source | In legacy? | Rule to address the gap |
|---|---|---|---|---|
| 1 | `Equipment ID` / `UBC Asset Tag` | printed standard tag (`CDP-6-N-1-L-1`) | ✅ identity name (`PANEL U`, `NPH`, `MCC2`, `ATS`) | Normalize identity: `PANEL X`→`PNL-X`, bare `NPH` stays only if prefixed on label; store normalized identity (`PNL-U`, `PNL-NPH`, `MCC2`, `ATS`) as Equipment ID. The X-composed standard tag is a **display aid only**, never the stored ID (it must match SLD `ID_check`). |
| 2 | `Voltage Rating` (+`UoM`) / `Volts` | tag voltage code `2`/`6` via dictionary | ✅ ratings line (`120/208V`) | Parse from label text, NOT from ident. Store bare, higher-first (`208/120`), UoM `VLT`. Skip if `volts_manual_override`. |
| 3 | `Amperage Rating` (+`UoM`) / `Ampere` | nameplate/AI extraction | ✅ (`100 A BRK`) | Parse from the **lamacoid** only; bare value (`100`), UoM `AMP`. `BRK` noted as breaker-rating context in Description. **Never harvest a manufacturer nameplate's winding-current table** (2026-08-04): `legacy_ratings_from_label` rejects candidates preceded by a `COURANT`/`CURRENT` header, a `%` tap row, or an `H.T.`/`B.T.`/`H.V.`/`L.V.` winding label, alongside the existing `RM`/`ROOM`/`PANEL`/`PNL`/`#` rejection. A transformer plate prints per-tap per-winding currents, not a service rating — a transformer contributes **no** amperage at all. |
| 4 | `Equipment Type` | shared `ELECTRICAL_EQUIPMENT_TYPE_MAP` prefix match | ✅ prefix word | Legacy-only map = shared map **plus** `MDC` → `Main Distribution Centre`, `DCC` → `Distribution Centre`. Implemented in `legacy_flow.py`; `electrical_equipment_rules.py` unchanged. |
| 5 | `Power Type` | system code from tag `parts[1]` — **misfires on legacy** (`PNL-UPS/CM` → "S" Standby) | ⚠️ hints only | Use `panel_legacy.system_hints` (leading `N`→N, `E`/`EM`→E, `UPS`→ no Planon code → blank) **only when corroborated** by normalized Supply From (`SD-N4`→N, `SD-E4`/via `ATS`/generator→E). Uncorroborated or conflicting → blank. Never call the shared derivation on the legacy branch. |
| 6 | `Location` | tag location code | ❌ (room refs on label are the **source's** room) | Leave blank for capture/review to fill. Never derive from ident or fed-from room text (mirrors the standard flow's `_clear_legacy_tag_derived_location` philosophy). |
| 7 | `Branch Panel` | tag/AI | ✅ the ident itself | `Branch Panel` = bare ident (`U`, `NPH`, `EPH`). |
| 8 | `Supply From` / `Fed From Equipment ID` | `normalize_el_supply_from_tag` — **strips the word "MAIN"**, so it cannot distinguish MDC vs DCC | ✅ free text | Legacy normalizer (order matters): `MAIN DIST(RIBUTION)?. CENTRE` → `MDC`; `DIST. CTRE.`/`DISTRIBUTION CENTRE` (no MAIN) → `DCC`; trailing `#n` kept as unit; `VIA ATS` → immediate feeder `ATS`; `SD-<x><n>` verbatim; `GENERATOR` verbatim; room refs (`RM. 0060`) stripped from the ID but preserved in `Supply From` raw text. `Fed From Equipment ID` gets the normalized ID; `Supply From` keeps printed text. |
| 9 | `Fed From Amperage Rating` (+UoM) | upstream record | ❌ | Leave blank (future cross-record lookup out of scope — YAGNI). |
| 10 | tag `system`/`sequence` segments | tag segments | ⚠️ trailing digit = unit # (`MCC2`→2, `EM3`→3) | Trailing digit of ident → sequence slot of the composed X-tag; system slot filled only per rule 5, else `X`. |
| 11 | SLD `Equipment ID` validation (`is_dictionary_id`) | SLD dictionary variant — legacy idents **rejected** ("not modeled", extract_electrical_schema.py:1621) | ❌ | Legacy branch shim: accept `panel_legacy` idents and normalize `PANEL X`→`PNL-X` so SLD-vs-SDI `ID_check` matching works. SLD variant file itself unchanged. |
| 12 | `Hierarchy` (SLD) | diagram edges | ✅ derivable from fed-from chain | Build from normalized fed-from (`MDC`/`DCC` → `ATS` → panels). No new code needed beyond rule 8 (hierarchy uses Supply From). |
| 13 | `Power Rating` (+`UoM`) | standard prompt, transformer-only (`TX-*`) | ⚠️ manufacturer nameplate only (EL-0), never the lamacoid | **Added 2026-08-04.** Transformer-only, gated by `is_legacy_transformer()`. Parsed by `legacy_nameplate_specs()` from `nameplate_text` (the EL-0 plate), never from `label_text` — that separation is what stops a panel inheriting the upstream feeder kVA its lamacoid quotes (`THROUGH TRANS. "T1" 112.5 K.V.A.`). Take the **base self-cooled** rating: prefer the declared impedance base (`SUR/ON 1500 KVA`), else the smallest explicit kVA not on a forced-air (`AFN`/`FA`/`ONAF`) line. Store a bare **integer** (`1500`) with UoM `KVA` — `normalize_power_rating_pair` rejects decimals and truncates thousands separators (`1,500` → `500`), so strip separators first and truncate fractional plate sizes toward zero (`112.5` → `112`, matching the `112` already stored in `sdi_dataset_EL`). The kVA regexes need a `(?<![\d.])` lookbehind: a bare `\b(\d{1,6})` reads `112.5 KVA` as **5**, a valid-looking pair that survives every downstream normalizer into Planon. Blank-fill only (`_set_if_blank`), never overriding a reviewer value. |

### Review-app integration (`_apply_dictionary_priority` call sites)

`_apply_dictionary_priority(data, tag)` (Standard-only, tag-code-based Volts derivation) has five call sites in `Asset_dashboard_EL.py`. Each is gated independently because building code and DB connection availability differ per site. `Buildings.Process` is looked up **once per record** and the result (`_process`) is reused for every downstream decision in that function — both the upstream field computation and the final dictionary-priority/apply_legacy_rules call:

| Call site | Context | Building code / conn available? | Gated? |
|---|---|---|---|
| `_apply_tag_dictionary_first()` (internal call) | Shared helper with signature `(data, asset_type)` only, called from 6+ places (list loader, sheet builder, review page, save) | Neither `building` nor `conn` is a parameter of this helper | **No** — gating would require changing this shared helper's signature and threading building context through every caller, which is out of scope and risks the "no changes to Standard behavior" invariant. Left byte-identical. |
| `load_json_items()` (dashboard list loader) | Non-request/batch path; iterates `Output_jason_api/` | `building` in scope (from filename); no open `conn`, opened fresh via `qrdb.get_connection()` | **Yes** — `BuildingProcessError` is caught inline, logs `print("[WARN] Skipping ...")`, and `continue`s to the next JSON file (no page to flash into). |
| `_build_el_sheet_context()` (print/export sheet builder) | Shared helper feeding `review_print`/`review_export`; already returns `(ctx, err)` tuples for error cases | `building` in scope; no open `conn` | **Yes** — reuses the existing `(None, (message, status))` error contract (same as the function's pre-existing `"Not found"`/`"Bad ID"` returns) instead of flash+redirect, since a redirect back to the review page does not fit a print-preview/export response. |
| `review()` (`GET /review/<doc_id>`) | Genuine request handler | `building` in scope; no open `conn` | **Yes** — `flash(str(exc), "warning")` + `redirect(request.referrer or url_for("main.review_all"))` (the app has no `main.index` endpoint — `/` is `landing`). |
| `save_review()` (`POST /review/<doc_id>`) | Genuine request handler | `building` in scope; no open `conn` | **Yes** — `flash(str(exc), "danger")` + `redirect(reload_url)`, matching this handler's existing flash+redirect precedent (e.g. `RevisionConflictError`, SDI package-lock checks). The early return still runs the enclosing `finally: json_sync_lock.release()`. |

At every gated site, the `'Standard'` branch calls `_apply_dictionary_priority(data, tag)` (or `(structured, tag_for_group)`) exactly as before — no behavior change for Standard buildings. The `'Legacy'` branch calls `legacy_flow.apply_legacy_rules(data)` instead. The three read/display sites (`load_json_items`, `_build_el_sheet_context`, `review()`) additionally route the *upstream* Equipment ID/Equipment Type/Power Type/Supply From/Fed From Equipment ID computation through `_compute_el_upstream_fields(data, process)` rather than the standard `_get_el_*` helpers when `process == 'Legacy'` (see the gate bullet list above) — otherwise those fields would already be non-blank/misfired by the time `apply_legacy_rules` ran, and its blank-fill-only semantics could never correct them.

### Extraction-side gate (2026-07-29)

The AI extraction API (`API/API_interface_EL_ver00.py`) is now gated the same way the review app and SLD extractor already are, closing the last open path where a Legacy-building nameplate reached a human only after the standard-flow extraction logic had already reshaped it.

- **Gate location.** `AssetProcessor.__init__` loads `self.building_process_map` once per run via `_load_building_process_map()` (`SELECT "Code", "Process" FROM "Buildings"`, a parameterization-clean whole-table read through `qrdb.get_connection`). `process_single_asset(qr, info)` checks `self.building_process_map.get(building, "")` as the very first thing after `building = info["building"]` — before the skip-if-exists guard, before hybrid OCR, before any OpenAI call — so a Legacy or blank-Process building never burns a paid LLM call down the standard path.
- **Semantics** (never silently defaults to `'Standard'` at any level):

  | `Buildings.Process` | `process_single_asset` outcome |
  |---|---|
  | `'Standard'` | falls through the pre-existing body, byte-identical |
  | `'Legacy'` | routes to `_process_legacy_asset(qr, info)` |
  | blank / `NULL` / any other value | that QR is skipped: `logging.warning(...)`, returns `"SKIPPED (No Process value for building ...)"` — no JSON written, `ai_status` untouched |
  | `Buildings` table unreachable at startup (`_load_building_process_map()` raises) | logged as an error; the method returns `{}`, so every building then looks up as `""` and **every QR in the run** hits the blank-skip branch above until the table is reachable again — a DB outage skips the whole run rather than defaulting anything to Standard |

- **Legacy path (`_process_legacy_asset`).** Uses the same model-selection plan as the standard path (`get_llm_model_plan`, `role_for_position` — which models to try and in what role), but **not** the standard path's per-model retry-profile budget: `Config.MAX_LLM_ATTEMPTS_PER_MODEL` and the `retry_profiles` sweep that the standard loop slices on (`API_interface_EL_ver00.py`'s `process_single_asset`) are never referenced in `_process_legacy_asset` — it makes exactly one prompt attempt per model in the plan, advancing to the next model only on failure, and stops early once `best_score >= Config.FALLBACK_MIN_SCORE`. It prompts with `EL_LEGACY_PROMPT` (verbatim transcription only — "Copy verbatim — never reformat, never add prefixes, never normalize"), parses into `ELLegacyStructuredExtraction` (a raw-preserving Pydantic model with plain string coercion, deliberately carrying none of the standard flow's normalizing field validators), and scores completeness against `EL_LEGACY_SCORING_FIELDS = ("UBC Asset Tag", "Volts", "Ampere", "Supply From")` instead of the standard field list. The raw model output is post-processed through `el_legacy_flow.legacy_structured_from_raw(raw_payload)` before scoring/storage. That function is a Task-1 addition (commit `cb593fb`) living in the same `legacy_flow.py` module the review app and SLD extractor already use, but it is not itself called by either: the review app's gated call sites use `apply_legacy_rules` and (separately) `normalize_legacy_supply_from`, and the SLD extractor uses `get_building_process`/`legacy_dictionary_shim`. `legacy_structured_from_raw`'s only caller today is `_process_legacy_asset` (plus tests).
- **No-`PNL-` fabrication.** `_process_legacy_asset` never calls `_apply_tag_formatting`, the standard-flow helper that (among other things) prefixes a digit-leading tag with `PNL-`. That helper was the root cause of QR 0000186130's `"DIST. CTRE. #1"` being recorded as `"PNL-DIST.CTRE.1"`; the legacy prompt and `ELLegacyStructuredExtraction` route around it entirely, and `legacy_structured_from_raw`'s own identity parsing only ever normalizes recognized legacy shapes (`PANEL U` → `PNL-U`), leaving anything it can't parse stored verbatim.
- **Reconciling with Field-Gap Map row 1.** Row 1 (above) says the X-composed standard tag is "a display aid only, never the stored ID" — that characterization is about `Equipment ID`, which stays the plain identity (`DCC-1`, `MCC2`, `PNL-U`) and is never overwritten with the X-composed form. `UBC Asset Tag` is different: `legacy_structured_from_raw` (and the underlying `compose_x_tag`) does populate `UBC Asset Tag` with the X-composed value for `MDC`/`DCC`/`MCC`/`SWBD`/`SPL`/`CDP`/`MDP` identities once voltage or sequence is known (e.g. `DCC-2XXD1`), and that is what gets stored in `structured_data["UBC Asset Tag"]`.
- **Identifier and Description normalization (user rules, 2026-07-29; `EL_LEGACY_RULE_VERSION` 2).** Numbered MDC/DCC units use the hyphen form everywhere: `Equipment ID = "DCC-1"` (never `"DCC #1"` — the old `#` form still parses and normalizes forward on read). `Supply From` and `Fed From Equipment ID` mean the same thing (the upstream feeder) and carry the same hyphenated identifier (`DCC-1`, `DCC-8`); `Supply From` may additionally carry a printed `via ...` qualifier. `Description = "<type word> - <Equipment ID>"`, where a DCC/MDC is described as a `Distribution` (the dictionary type-D word its printed name verifies): `"Distribution - DCC-1"` — the identifier is the Equipment ID, not the X-tag.
- **UBC Asset Tag stores the equipment identity (user rule, 2026-07-30; `EL_LEGACY_RULE_VERSION` 3).** `UBC Asset Tag = "DCC-1"` / `"PNL-U"` / `"MCC2"` — the same identity as `Equipment ID`. The X-composed standard structure (`DCC-2XXD1`, via `compose_x_tag`) remains the dictionary-decode/display structure — its segment codes still map voltage/system/type through the standard `label_schema` — but it is never stored as the tag. (This supersedes the "Reconciling with Field-Gap Map row 1" note above insofar as `UBC Asset Tag` no longer stores the X-composed value either.)
- **`Location` is never written by extraction.** Neither `EL_LEGACY_PROMPT` nor `legacy_structured_from_raw` produces a `Location` value — `structured_data["Location"]` is always `""` coming out of the extraction path (Field-Gap rule 6: Location is capture/review owned), matching `apply_legacy_rules` on the review-app side, which likewise never touches it.
- **Legacy JSON envelope.** On top of the standard top-level keys, a Legacy JSON adds:
  - `"process": "Legacy"` — the marker every Legacy-aware consumer downstream (`_existing_el_legacy_output_needs_rescore`, the review app, the SLD extractor) checks.
  - `"el_legacy_rule_version": EL_LEGACY_RULE_VERSION` (currently `1`) — a staleness key independent of `Config.EXTRACTION_RULE_VERSION` (still stamped unchanged for compatibility, but never used to key Legacy rescore decisions). Bump it when `legacy_structured_from_raw`'s composition rules change in a way that should force a rescore of previously-written Legacy JSONs.
  - `structured_data.label_text` — the verbatim plate transcription. This is the field `apply_legacy_rules` has read since 2026-07-28 but that no production JSON carried until now — see the updated dormant-features note above.
- **Staleness and overwrite protections.** The skip-if-exists check on the Legacy path routes a payload carrying `"process": "Legacy"` through `_existing_el_legacy_output_needs_rescore()` rather than the standard `_existing_el_output_needs_rescore()` — the standard function recomputes completeness/confidence with the standard flow's tag-dependent scoring rules (`_el_completeness_score` / `_el_scoring_fields_for_tag` / `_classify_el_asset_group` / tag-formatting normalization), which don't apply to a 4-field Legacy payload and would flag every legitimate Legacy JSON stale forever — a self-perpetuating, billable re-extraction loop. Both rescore functions honor the same human-review protections (`modified is True`, `supply_from_manual_override == "1"`, `volts_manual_override == "1"`) before comparing rule versions. Independently, and before any billing work, `_process_legacy_asset` also refuses to overwrite an existing Legacy JSON that is human-reviewed or field-level manually overridden regardless of `EL_OVERWRITE_EXISTING_JSON` (invariant 6).
- **Operational caveat: pre-feature Legacy JSONs do not self-upgrade.** An existing EL JSON for a building later reclassified to `'Legacy'` that predates this gate has no top-level `"process"` key. `_process_legacy_asset` detects that (`existing_payload.get("process") == "Legacy"` is false) and falls back to the *standard* `_existing_el_output_needs_rescore()` for one rescore pass — a multi-condition check (rule-version comparison, standard completeness/confidence recomputation, tag-decimal normalization), not a hardcoded result, but it currently evaluates as "not stale" for all such payloads in the present fleet, so they are skipped indefinitely rather than ever being re-extracted through the Legacy path. As of this writing that is not a live problem: the only buildings holding existing EL records (`217`, `314-1`, `459`, `750`, per the classification in "Deploy sequence" above) are all classified `'Standard'`. But if a building holding existing Standard-flow JSONs is ever reclassified to `'Legacy'`, its existing JSONs need a one-time `EL_OVERWRITE_EXISTING_JSON=true` re-run to pick up the Legacy prompt/envelope/`label_text` — otherwise they sit indefinitely in the old Standard-shaped envelope despite the building now being Legacy.

### Legacy write-path guards added post-deploy (2026-07-29 hotfix, commit `0f2143b`)

The first production sync of real Legacy rows exposed two pre-existing standard-flow write paths that the per-call-site gating above did not cover. Both are now process-gated; treat them as part of the Legacy rules:

- **`_ensure_el_fed_from_equipment_id_column` skips Legacy-building rows.** Unlike the sibling ensure-backfills (blank-fill only), this one force-rewrites every `sdi_dataset_EL` row on every `_sync_db_from_structured` call to keep `Fed From Equipment ID` in lockstep with the STANDARD derivation from `Supply From` — which mangles legacy-composed values (observed live: `MDC` → `MDC-VIA`, `DCC #8` → `DCC`). It now loads the Legacy building set from `Buildings."Process"` (metadata-probed first so a missing column can never abort a PostgreSQL transaction) and skips those rows; Standard and blank-Process buildings keep the pre-existing lockstep behavior byte-identical. On the Legacy path, `Fed From Equipment ID` is owned by the legacy flow (extraction + `apply_legacy_rules`) and synced as-is. Behavioral test: `test/test_el_fed_from_backfill_legacy_guard.py`.
- **SLD swift-save / reconcile are gated.** `sld_blueprint.py`'s `swift_save_asset` and `reconcile_asset` now resolve `Buildings.Process` once per request via `el_legacy_flow.get_building_process` (blank/NULL → HTTP 409 stop-and-warn, the same semantic as `toggle_approved`), keep a Legacy building's `Supply From` exactly as submitted/reconciled (never `_apply_el_supply_from_formatting`, which strips `MAIN` and destroys the MDC/DCC discriminator), and pass `process=` to all four `_sync_db_from_structured` call sites (including both rollback re-syncs). AST guards: `test/test_el_sync_legacy_gate.py::SldBlueprintProcessGateTests` — any new `_sync_db_from_structured` call site in `sld_blueprint.py` must pass `process=`.

# Optional Installation Date

- ME, BF, and EL read `QR_codes.installation_date` by `QR_code_ID`; PostgreSQL is the source of truth.
- Review display/input is `DD/MM/YYYY`, optional and editable while the normal review lock permits edits. Blank explicitly clears the value; future or invalid dates are rejected before review JSON mutation.
- Storage and audit values use `YYYY-MM-DD`. Never add this field to extraction JSON or discipline completeness/confidence calculations.
- The Asset Review Sheet (PDF/Export) renders the value read-only — below Year for ME/BF, below Main Asset (Identity) for EL. See "Asset Review Sheet" above.
