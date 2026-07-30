# Documentation Refresh Checklist

Last refresh pass: 2026-07-30.

Tracked refresh for project-authored markdown documentation.

## Scope Rules

- Include root docs, root `rules/`, root `workflows/`, root `special_processes/`, root `skills/`, and service-local `.agent` docs.
- Exclude `venv/`, `site-packages/`, and third-party license markdowns.
- Treat root docs and service-local docs as canonical.
- Update `.agent_app/` mirrors only after canonical docs are finished.

## P0

- [x] `README.md`
- [x] `00_README.md`
- [x] `01_GLOBAL_RULES.md`
- [x] `02_SYSTEM_MAP.md`
- [x] `03_ARCHITECTURE_MAP.md`
- [x] `rules/asset_capture_app.rules.md`
- [x] `rules/asset_dictionary.rules.md`
- [x] `rules/asset_extraction_api.rules.md`
- [x] `rules/dashboard.rules.md`
- [x] `rules/review_apps.rules.md`
- [x] `rules/sdi_process.rules.md`
- [x] `special_processes/01_atomic_rename_operations.md`
- [x] `special_processes/02_completeness_guard.md`
- [x] `special_processes/03_dictionary_ast_parsing.md`
- [x] `special_processes/04_database_topography.md`
- [x] `special_processes/05_life_cycle_assessment.md`

## P1

- [x] `workflows/01_capture_to_json.md`
- [x] `workflows/02_run_extraction_me_el_bf.md`
- [x] `workflows/03_review_and_approve.md`
- [x] `workflows/04_dashboard_ops.md`
- [x] `workflows/05_sdi_packaging_and_planon_export.md`
- [x] `workflows/06_parameter_update_atomic_rename.md`
- [x] `design/DESIGN.md`
- [x] `design/PRODUCT.md`
- [x] `skills/ubc_asset_platform/SKILL.md`
- [x] `schemas/response_schema_notes.md`
- [x] `API/.agent/api_comparison_matrix.md`
- [x] `Dashboard/.agent/api_comparison_matrix.md`
- [x] `asset_capture_app_dev/.agent/QR_codes_db_schema.md`

## P1 Service-Local Canonical Docs

- [x] `asset_capture_app_dev/.agent/*`
- [x] `API/.agent/*`
- [x] `Dashboard/.agent/*`
- [x] `review/.agent/*`
- [x] `SDI_process/.agent/*`
- [x] `dictionary/.agent_dictionary/*`
- [x] `auth_service/.auth_agent/*`

## P2 Mirror and Long-Form Docs

- [x] `.agent_app/00_README.md`
- [x] `.agent_app/01_GLOBAL_RULES.md`
- [x] `.agent_app/02_SYSTEM_MAP.md`
- [x] `.agent_app/rules/*`
- [x] `.agent_app/special_processes/*`
- [x] `.agent_app/workflows/*`
- [x] `.agent_app/skills/*`
- [x] `.agent_app/schemas/*`
- [x] `.agent_app/agents/*`
- [x] `.agent_app/UBC_Asset_Capture_Application_Documentation.md`
- [x] `.agent_app/UBC_Asset_Technical_Documentation.md`
- [x] `UBC_Asset_Capture_Application_Documentation.md`
- [x] `UBC_Asset_Technical_Documentation.md`
- [x] `assetcap_setup_manual.md`

## Current Refresh Themes

- discipline-specific completeness and AI-confidence rules
- `Avg_ai_conf` handling in review, dashboard, and DB sync
- merged `Data Quality Comparison` chart
- review dashboard confidence slicer behavior
- SDI duplicate-row prevention and normalized QR joins
- Manual Entry versus SDI exclusion synchronization
- approved-row flow into SDI staging
- FLS charts (Altair-based), FLS asset CRUD with `new_device` table, and FLS Control Panel lookup from `"UBC - Asset Data Master Info"`
- Dashboard dictionary management UI with AST-safe parsing
- Map chart and SDI flow chart additions
- Chained AI+DB sync via `run_ai_and_sync.sh` (removed manual `update_db` task)
- Planon export with UBC tag parsing and year formatting
- Validation log viewer in SDI Process
- Package archive management (active/archive/exclude)
- User and timestamp tracking in `QR_code_assets`
- Elapsed-time JSON artifacts in capture workflow
- Photo viewing API in Dashboard
- Parameter update service (`parameter_update_service.py`) in Capture App
- Shared validators (`validators_shared.py`) in extraction API
- Root and setup README files were corrected for current service targets and ASCII-safe formatting.
- Unified Dashboard shell embedding ME / BF / EL / SDI as iframe panels with `?embedded=true`, cross-subdomain `SameSite=None; Secure` cookies, Nginx `frame-ancestors` CSP, and central postMessage / hash-route navigation.
- Optional **Extra Photo** capture slot added to ME (`-4`), BF (`-3`), and EL (`-3`). Visible in capture and review but excluded from `VALID_SUFFIXES`, completeness, AI confidence, and "Missed Photo"; renders as a `+1` chip in dashboard Photo columns.
- EXIF Orientation transpose applied in `save_image_file()` so phone photos with an EXIF Orientation tag are physically rotated to portrait on disk. No backfill of historical files.
- BF and EL review dashboards now match ME-style Manual / Approved header bulk toggles, scoped to filtered DataTables rows and using existing per-row endpoints.
- EL Distribution listing hides Amperage Rating, Volts, and Location only in the dashboard table; `review.html` and all backend/export data remain complete.
- EL dashboard standalone shell behavior now matches ME/BF, and required-field popovers are layered above the shared shell sidebar.
- Review-app modals (`#confirmModal` / `#infoModal` / `#planonModal`) and the Dashboard dictionary `#deleteModal` switched to `modal-dialog-centered` so dialogs render vertically centered instead of clipped by the embedded Dashboard top bar.
- EL SLD building dropdown (`get_buildings()` / `GET /sld/api/buildings`, `sld_blueprint.py`) lists only buildings with at least one displayable (non-archived) QR code; a building is hidden only when it has QR codes and all of them are in `sdi_print_out_arch`. Hidden escape hatch `?include_archived=true` returns the unfiltered list (2026-06-01).
- Review-app archive toggle label corrected (was inverted) — reads "Show Archive" when archived rows are hidden and "Hide Archive" when shown. The Review Status filter and the archive button now preserve each other's query params (`archive`, `approved`), so "Approved + Show Archive" can be active together; previously each control reset the other on reload (EL/ME/BF, 2026-06-01).
- EL bulk "Approve all" (`select-all-approved`) surfaces per-row failures (auth/session/server) in a summary modal instead of silently swallowing them (2026-06-01).
- New read-only `scripts/audit_sdi_flow_integrity.py` audits the approve→package→archive flow: exclusion-pair consistency (`QR_codes.sdi=1` ⇔ `QR_code_assets.Col_process=2`), approved-but-unarchivable worklist, cross-store approval mismatch, and blank-identity. Companion runbook `special_processes/sdi_flow_remediation.md` documents case-by-case remediation via app endpoints (`/toggle_sdi`, `/toggle_approved`, `/review`) — never raw DB/JSON edits (2026-06-01).
- SDI package database guardrails added: `scripts/migrate_sdi_package_db_guardrails.py` installs normalized package QR unique indexes and lifecycle triggers; `scripts/audit_sdi_package_integrity.py` verifies the guardrail objects and reports historical unexported archive rows as warnings (2026-06-02).
- Dashboard and SDI self-service password-change routes now use `User.set_password()` and update `password_hash`; incident note added at `INCIDENT_2026-06-03_password_change_persistence.md` (2026-06-03).
- Per-asset **Asset Review Sheet** added to ME and EL review apps (2026-06-24): two header buttons on the single-asset review page — **PDF** (`GET /review/<doc_id>/print`, inline auto-print -> Save-as-PDF) and **Export** (`GET /review/<doc_id>/export`, downloaded fully self-contained `.html` with photos/logo inlined as base64 via `_file_data_uri`). One shared context builder (`_build_review_sheet_context` / `_build_el_sheet_context`) + one `review_print.html` per app, toggled by `auto_print`; same viewer permission as `review()`; read-only. The sheet shows the building **Name** (`Buildings."Name"`) instead of the code. **EL-only:** when the asset is in `electrical_building_schema` (`new_draw='TRUE'`), the sheet embeds a DB-reconstructed **end-to-end SLD branch** (upstream lineage + asset + full downstream subtree, siblings excluded) as an inline-SVG horizontal ladder with red flag = Current Asset, blue flag = Supply From, equipment-type icons, and a legend (`_get_sld_branch_tree` / `_build_sld_branch_svg` / `_sld_legend_html`); otherwise a "No Single Line Diagram available" note. Bounds `SLD_MAX_ANCESTORS=6` / `SLD_MAX_DESC_DEPTH=4` / `SLD_MAX_CHILDREN_PER=10` / `SLD_MAX_NODES=40`.
- BF Asset Review Sheet added (2026-06-25), bringing **all three** review apps (ME / BF / EL) to parity: same `review_print`/`review_export` routes, `_build_review_sheet_context` + `_file_data_uri`, self-contained `review_print.html`, PDF/Export header buttons, building **Name**, `reviewer_backflow` viewer gate. BF fields: Identity = Manufacturer / Model / Serial / Diameter; Classification = UBC Tag / Year / Application / Asset Group / Attribute; hero photo = Main Asset (`-2`); no SLD section. All three sheets order **Description above Identity** (ME report reordered 2026-06-25 to match the EL/BF layout).
- Review-app navigation parity across ME / BF / EL (2026-06-24): the per-asset review **prev/next sequence now follows the dashboard's filtered + column-sorted order** (dashboard writes the visible `dt.rows({search:'applied',order:'applied'})` `doc_id` list to `localStorage('reviewOrder')` per tab; `review.html` sets hidden `nav_prev`/`nav_next`, the `#navCounter`, and drives the Next Asset rail via new `GET /api/asset-preview/<doc_id>`; `save_review` honors the client nav fields; server fallback sort changed `doc_id`→Capture-Date-desc), and the **"Show Archive" toggle persists** through the review round-trip (`TRANSIENT_DASHBOARD_QUERY_KEYS` emptied, `buildDashboardQuery` keeps `archive`, `save_review` `filter_args` + server-rendered Review links carry `archive`). **Root cause** of the prior dashboard-vs-review count mismatch (e.g. 111 vs 57): the filter-propagation JS (`updateReviewLinks`, order capture, approve-toggle refresh) targeted the dead selector `a.btn-primary`; the Review buttons are `a.v2-btn-review`. Fixed in all three apps.
- Life Cycle Assessment integrated as an in-process Flask Blueprint (`life_cycle`) mounted in the Dashboard app at `/life-cycle` (registration wrapped in try/except so a missing dependency degrades to feature-absent); runs under the `assetcap-dashboard` gunicorn service. Backed by the `life_cycle_pipeline/` package (`track_assets.py`, `load_life_cycle.py`) which builds and loads PostgreSQL tables `life_cycle` (rebuilt each "Update Database" run), deduplicated `space_floor` reference table, and surviving `life_cycle_meta` key/value table. New RBAC key `operations` / `lifecycle_assessment` in `auth_service/app_registry.py` enforced server-side via `has_permission` / `require_permission`. "Life Cycle Assessment" nav item added under the "Operations" group (below "FLS Devices") across all five `shell.js` copies. DB connection derived from `LIFE_CYCLE_DSN` (libpq) with SQLAlchemy `LIFE_CYCLE_SA_DSN` derived from it (2026-06-23).
- **Siemens `Product No.` serial extraction** added to `API/API_interface_ME_ver00.py` (2026-06-25): Siemens nameplates label the serial field as `Product No.` (not `S/N`/`Serial`); model numbers may be purely numeric. `ME_NUMERIC_MODEL_MANUFACTURERS = {"Siemens"}` set controls which manufacturers get the relaxed model-code and product-no cue logic. `_has_serial_label_evidence()` now accepts `manufacturer_hint` so the `PRODUCT|PROD NO` cue is manufacturer-gated. `_clean_labeled_serial_value()` preserves `NNN-NNNNNN` shapes (e.g. `599-0335`). LLM prompts updated with Siemens-specific override (use `Product No.` as Serial when no explicit S/N field is present). `_evaluate_llm_candidate()` passes manufacturer hint to `_is_model_code_candidate()` so numeric Siemens models are not discarded as weak.
- **Serial date-misread defense (ME + BF)** (2026-07-06): new shared validator `looks_like_date_misread_serial()` rejects date-shaped serials including upside-down misreads (`8102/90` = `09/2018` rotated 180°; incident QR `0000261040`, Rheem/Ruud ST120). Wired into ME `_is_serial_candidate()` / `_serial_acceptable` / final guardrails (rejection auto-triggers the reread + multi-rotation OCR rescue) and BF candidate handling (blank → existing heavier-model fallback with OCR context). New `serial_date_misread_suspected` manual-review reason code; Serial confidence capped at 65 when suspected. Rotated-plate + never-a-date rules added to ME/BF LLM prompts (incl. Rheem/Ruud letter-prefixed serial shape); 180° rotation added to ME/BF `_ocr_text_variants()`. EL unchanged. Same-day follow-ups: Rheem/Ruud targeted-reread serials matching the family shape (`_matches_rheem_ruud_serial_shape()`) are trusted without OCR corroboration and OCR rescue never overwrites a reread-accepted serial; UBC Tag prompts accept placard-style identifiers (`DST-4`, `EF-1`) alongside `<PREFIX> NO.` plates; ORDER NO. / PRODUCT NO. demoted to low-confidence fallback for Serial Number (labeled SERIAL field always wins; fallback must score <70 → manual review; incident QR `0000083767`). Serials without OCR label evidence are corroborated against the targeted reread — mismatch caps confidence at 65 + `serial_unverified` reason code. Manufacturer stopword catch-22 fixed: corporate suffixes (`LTD`/`INC`/`CO`/…) moved to `ME_MANUFACTURER_CORPORATE_TOKENS` so unknown-but-legitimate names (`Enermax Fabricators Ltd`) survive. See `rules/asset_extraction_api.rules.md` § "Serial Date-Misread Defense".
- **UBC Tag prefix widened to 6 letters** (2026-07-07): `_parse_ubc_tag_from_text()` and `Config.UBC_TAG_PATTERNS` capped tag prefixes at `[A-Z]{1,4}`, truncating 5-letter placard tags (QR `0000081480`: `CHWBT-W-4` → `W-4`). Now `[A-Z]{1,6}`; existing formats regression-verified. See `rules/asset_extraction_api.rules.md`.
- **Taco pressure-vessel serial exception** (2026-07-13): Taco assets still require a labeled `SERIAL` / `S/N` value by default. For a tank label with CRN plus pressure-vessel evidence, a targeted high-detail, full-plate reread may use the digits-only identifier stamped in the top/header border. The value is capped at 65 confidence and marked `pressure_vessel_unlabeled_serial` for manual review; Part/Order/CRN/date/pressure identifiers remain forbidden. Incident QR `0000086593`; see `rules/asset_extraction_api.rules.md`.
- **AquaPLEX tank manufacturer recovery** (2026-07-13): standalone AquaPLEX branding now canonicalizes to `AquaPLEX`, and short suffix model `L 600A-TR` is accepted. For existing unreviewed ME JSON with that exact confirmed model and no Manufacturer, the extractor safely repairs only Manufacturer to `AquaPLEX`, preserving all other values and recalculating review metadata. A focused seq-0 logo/header vision reread remains the general fallback. Incident QR `0000260587`.
- **AI Status reprocess + force feature** added to ME / BF / EL review dashboards (2026-06-25): toggling AI Status to `0` now moves the JSON aside (`.bak_<YYYYMMDDHHMMSSz>`) after `conn.commit()` so the extraction cron finds no file and re-extracts on the next cycle. Protection hierarchy: packaged → `409`; approved → blocked (non-forceable); Manual Entry → blocked (non-forceable); human-edited (`modified=True`) → blocked but **forceable** via "Force re-run AI?" confirm dialog posting `force=1`. All blocked responses return HTTP 200 with `{success:false, code, error, forceable}` (not 4xx). Path-traversal guard on JSON path. ME/BF audit in `audit_trail` (`source="human"`); EL uses `print()`. Backup JSON always recoverable. `reset_me_asset.py` added as a CLI helper for one-off ME resets (dry-run by default, `--apply` executes).
- **Capture App optional Notes + Installation Date fields** (capture deployed 2026-07-07; review/SDI extended 2026-07-10): values persist to `QR_codes.capture_notes` / `installation_date` and `{qr}_et.json`; blank capture resubmits never erase. ME/BF/EL review editors can update or clear Installation Date in `DD/MM/YYYY`; PostgreSQL stores and SDI exports `YYYY-MM-DD`. The field remains outside extraction JSON.
- **AI Status auto-refresh poller** added to ME / BF / EL review dashboards (2026-07-10): each dashboard polls a new read-only `GET /api/ai_status_map` endpoint every 60 s and updates the ✅/☐ AI Status cells in place (`td[data-ai-cell]` marker, `data-docid` QR prefix, DataTables row invalidate + `draw(false)`), so the flag flips without a manual page reload once the extraction cron sets `ai_status=1`. Strictly display-layer: no writes, no extraction triggers. See `rules/review_apps.rules.md` → "AI Status Auto-Refresh".
- **Building multi-select filter** rolled out to the ME / BF / EL review dashboards (2026-07-14), mirroring the Life Cycle Assessment building filter (checkbox dropdown + type-to-filter search + Select all/Clear). New byte-identical three-copy static pair `review_asset_templates/static/building-multiselect.js` / `.css` in each app. `building`/`filter_building` params now carry comma-joined code lists (single code = legacy form); servers filter by set membership via `_parse_building_codes()` (listing, card KPIs, review prev/next, XLSX export). ME/BF filter client-side via a DataTables anchored OR-regex with the facet list built from all tab rows ({search:'none'}); EL keeps page scope and commits on panel close (true multi-building server rendering), with the Single Line Diagram gated to exactly one building (server coerces `?tab=sld`, disabled tab, pane notice). See `rules/review_apps.rules.md` → "Building Filter Rules".
- **EL building selector reverted to single-select** (2026-07-14, same day): EL keeps the new searchable dropdown UI but runs the shared component in `{single: true}` mode — one building at a time, checking a building replaces the selection and auto-closes/commits, "Select all" hidden. Shared `building-multiselect.js` gained the `single` option (three copies still byte-identical; ME/BF multi-select unchanged). EL server truncates `filter_building` to the first code (`_render_dashboard_view`), the SLD multi-building gating (tab disable + `?tab=sld` coercion + pane notice) was removed as unreachable, and `selected_building_display` is again name-or-empty.
- **FLS Devices Property multi-select filter** (2026-07-14): the FLS Devices `Property` filter in the Dashboard app now uses the shared searchable multi-select component (`BuildingMultiselect`), matching the ME/BF review dashboards' Building filter. New byte-identical fourth copy of `building-multiselect.js`/`.css` in `Dashboard/static/` (three-copy rule extended to FOUR-COPY across ME/BF/EL/Dashboard). Values are property names; empty selection = all; `assetMatchesFilters` uses `propertyValues.includes(...)`; `updatePropertyFilterOptions` keeps except-property faceting with checked values unioned in. See `rules/dashboard.rules.md` → "FLS Asset CRUD Rules".
- **Asset Group multi-select filter** rolled out to the ME / BF review dashboards (2026-07-30), reusing the shared `BuildingMultiselect` component per tab (`{allLabel: 'All Groups', emptyLabel: 'No asset groups'}`; instances on `window.groupFilters`, read via `assetGroupFilterValue(tab)`). The component gained a generic `emptyLabel` create() option (default `"No buildings"`; four copies re-synced, JS cache version bumped to `20260730-1` in all four consumer templates). `filter_group` now carries an ordered de-duplicated comma-joined list (single value = legacy form, still valid; empty = all groups); all three reviewers filter by exact case-sensitive set membership via `_parse_filter_values()` (renamed from `_parse_building_codes`). EL keeps its simple per-tab Asset Group `<select>` — server parsing generalized only. Asset Group stays a per-tab client-side filter (KPI cards not group-scoped); XLSX export unchanged (group reaches it via the visible-rows `qr_codes` payload). See `rules/review_apps.rules.md` → "Asset Group Filter Rules".
