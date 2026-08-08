# Review App (EL) Agent

Current documentation refresh: 2026-08-07.

## Purpose
Electrical asset review for engineers validating panel, transformer, and distribution data against extracted JSON and source images.

## Scope
- In scope: EL review dashboard, review form, electrical field editing, approval state, `sdi_dataset_EL` sync.
- Out of scope: ME-only schema logic and BF-only image rules.

## Inputs
- `<QR>_EL_<Building>.json` payloads
- Electrical and mechanical dictionary data used together during classification fallback

## Outputs
- Updated EL JSON payload
- `QR_codes` approval updates
- `sdi_dataset_EL` synchronization for approved and edited EL assets
- `electrical_building_schema` updates from the SLD panel's inline editor (Swift Over) and Reconcile workflow
- `audit_trail` rows for every reviewer edit and every Reconcile choice (one per side that actually changed)

## Critical Conventions
- Local and production port: `8005`.
- The General (`/review-all`) vs Distribution (`/review-distribution`) split is data-driven (2026-08-04): assets whose `Asset Group` has `Asset_Group.elec_dist_setup = 'Y'` belong to Distribution. Loaded by `get_distribution_asset_groups()` (60s TTL cache); the static `EL_DISTRIBUTION_ASSET_GROUPS` frozenset / `sld_blueprint.py` tuple is fallback only. Move groups between views with an audited flag `UPDATE`, never by editing code.
- The review page renders one of two server-selected form variants (2026-08-07) via `_el_form_variant(asset_group)` — never via the client-supplied `base_route`. **General** assets get the ME-style nameplate form (Manufacturer / Model / Serial Number / Year + Installation Date; Classification card; Location kept; no TSBC, no electrical tech cards) scored against `EL_REVIEW_GENERAL_SCORING_FIELDS`; **Distribution** keeps the electrical tech-card form unchanged. Nameplate values live in `sdi_dataset_EL` columns named after the JSON keys, written only for General rows; Equipment ID / Equipment Type / Power Type stay derived+stored for both variants. See `rules/review_apps.rules.md` → "EL Review Form Variants".
- EL derives `Attribute`, `Asset Group`, and `Main Asset` through `_apply_tag_dictionary_first()` with mechanical fallback where required.
- Approval toggles must not bypass the derived-field pipeline. Persisted JSON and `sdi_dataset_EL` must remain classification-complete after approval changes.
- Keep tag normalization case-insensitive and process-aware.
- EL `dashboard.html` includes `_shell.html` like ME/BF so standalone sidebar/topbar behavior is consistent; shell JS suppresses itself in iframe / `?embedded=true` contexts.
- EL bulk Manual / Approved header checkboxes use existing per-row endpoints and currently rely on endpoint-enforced permissions.
- EL `Volts` may be tag-derived by default, but reviewer-entered non-derived values are preserved with `volts_manual_override=1` through render, save, and `sdi_dataset_EL` sync.
- EL `Supply From` may be AI-normalized by default, but reviewer-entered non-normalized values are preserved with `supply_from_manual_override=1`; `Fed From Equipment ID` remains the normalized lookup/export value.
- EL `Fed From Amperage Rating` (+ UoM) resolves from the building's active SLD (`electrical_building_schema`, `new_draw='TRUE'`), not from sibling captured assets; blank when no SLD exists. The dashboard required-fields checklist counts both fields only for buildings with SLD data.
- Review saves must refresh top-level JSON quality metadata after required-field edits so the EL AI checker does not reprocess and overwrite human corrections.

## Validation Checklist
- [ ] `Main Asset`, `Attribute`, and `Asset Group` still derive correctly after save and approval changes.
- [ ] Approval changes remain synchronized into `sdi_dataset_EL`.
- [ ] Tag lookup behavior remains case-safe and composite-key aware.
- [ ] Image hooks still validate the EL sequence set (`-0..-3`, with `-3` being the optional **Extra Photo** included in `ALL_SHOW` but absent from `REQUIRED`).
- [ ] The Photo column renders a `+1` chip when seq `-3` is present; the chip never triggers "Missed Photo".
- [ ] Bulk Manual skips Approved rows, and bulk Approved uncheck skips exported / Planon-locked rows after `/check_sdi/<qr_code>`.
- [ ] Distribution listing hides Amperage Rating, Volts, and Location only in `dashboard.html`; `review.html` renders every field of the asset's form variant (Distribution assets still show/edit Ampere, Volts, and Location).
- [ ] A General asset renders the nameplate form and its save populates `Manufacturer` / `Model` / `Serial Number` / `Year` on `sdi_dataset_EL`; a Distribution asset's row keeps those columns blank.
- [ ] Reviewer-entered `Volts` values survive `Save & Next` and remain visible after the next render.
- [ ] Reviewer-entered non-normalized `Supply From` values survive `Save & Next`; parent lookup uses the normalized `Fed From Equipment ID`.
- [ ] After editing required EL fields, `completeness_score` / `confidence_scores` / `Avg_ai_conf` are aligned and the scheduled AI checker does not rerun the asset.

## Embedded Mode
Runs both standalone and inside the central Dashboard iframe (`?embedded=true`). A `before_request` hook sets `g.embedded`; templates wrap user-nav, brand header, and user dropdown in `{% if not g.embedded %}` while keeping all functional controls visible. Cookie config: `SameSite=None; Secure`. Internal `<a>` clicks have `?embedded=true` re-appended by a small JS script. See `rules/review_apps.rules.md`.

## Single Line Diagram (SLD) Panel — EL Distribution

The Distribution view (`page_title` contains "Distribution") includes the SLD panel, which renders rows from `electrical_building_schema` alongside their matching `sdi_dataset_EL` row.

- **Code:** `sld_blueprint.py`, `review_asset_templates/sld/sld_panel.html`, `review_asset_templates/static/sld/sld.js`, `review_asset_templates/static/sld/sld.css`.
- **Distribution listing:** the dashboard DataTables hide Amperage Rating, Volts, and Location only for `/review-distribution`; the cells stay in markup/data so column indexes and review-form editing remain intact.
- **Required-field popovers:** `.el-required-popover` must remain above the shared shell sidebar z-index (`10050` vs shell `9998`) so QR hover cards are not covered.
- **Inline editor (Swift Over):** posts to `POST /sld/api/assets/<row_id>/swift-save`. Updates `electrical_building_schema` first, then JSON + `sdi_dataset_EL` under `json_sync_lock` with rollback on failure.
- **Rating value/UoM conventions (2026-07-08/09):** rating values are stored bare — `_strip_voltage_unit_letters` removes embedded unit letters on schema-table saves. `electrical_building_schema` stores display units (`V`/`A`): schema-table writes map Planon codes via `_schema_display_uom` (`VLT` -> `V`, `AMP` -> `A`), while SDI-bound payloads keep the raw UoM (`sdi_dataset_EL` intentionally keeps `VLT`/`AMP`). Display always shows `V`/`A`: `withRatingUnit()` in sld.js and `_sld_rating_text()` in the review-report SVG.
- **Reconciliation column** (was "Check"): driven by `id_check_match`, computed by `_enrich_asset_display_fields` from each table's `ID_check` column. `ID_check` is a PostgreSQL `GENERATED ALWAYS AS (Building | Equipment ID/UBC Asset Tag | Supply From) STORED` column on both tables — do not write to it from Python. The old SQLite `VIRTUAL` form is rollback/reference history only; previously-manual ID_check writes have been removed from `swift_save_asset`.
- **Reconcile endpoint:** `POST /sld/api/assets/<row_id>/reconcile` with `{choice: "sld"|"sdi"|"custom", value?, reason?}`. Writes both sides atomically; each side that changes emits an `audit_trail` row (`source="human"`, `description="reconcile:<choice>"` plus optional reason). Returns `{status: "noop"}` when both sides already agree.
- **Orphan JSON case:** if `sdi_dataset_EL` has a row but `Output_jason_api/<QR>_EL_<Building>.json` is missing, both `swift_save_asset` and `reconcile_asset` fail with `500 {"error": "Failed to sync captured asset: JSON not found ..."}`. Restore the JSON (or remove the orphan SDI row) before retrying.
