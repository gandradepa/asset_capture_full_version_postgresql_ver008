# EL General "Electrical Assets" — ME-style Nameplate Review Form (Design)

**Date:** 2026-08-07
**Status:** Approved (user-validated decisions; implemented same day)

## Problem

The EL review app serves two views split by `Asset_Group.elec_dist_setup`: General
"Electrical Assets" (`/review-all`, `'N'`) and Distribution (`/review-distribution`,
`'Y'` — exactly 7 groups). One review form (electrical tech cards: Ampere/Volts/
Power Rating/Supply From/derived fields) served both. General assets are ordinary
equipment where nameplate identity (Manufacturer, Model, Serial Number, Year)
matters more than feeder data, which the old form could not capture.

## Decisions (user-approved)

1. **Field composition ("ME set + Location"):** General assets review through an
   ME-style form — QR Code (temp-QR rename, unchanged) → Identity: Manufacturer,
   Model, Serial Number, Year, Installation Date → Classification: UBC Asset Tag,
   Asset Group select, readonly `Electrical` Attribute, Main Asset → Location →
   Description (auto, EL logic) → User Activity Log. Dropped: Technical Safety BC
   (no EL photo sequence) and all electrical tech cards. Equipment ID / Equipment
   Type / Power Type are still derived server-side and stored (canonical Planon
   fields). The Distribution variant stays byte-identical to the previous form.
2. **Storage:** `sdi_dataset_EL` gains `Manufacturer`, `Model`, `Serial Number`,
   `Year` TEXT columns (names = JSON keys; migration
   `scripts/migrations/2026-08-07_sdi_dataset_el_nameplate_columns.sql`).
   Populated only for General rows; Distribution rows blank by contract.
3. **AI extraction in scope:** `API/API_interface_EL_ver00.py` reads the four
   fields from the `-0` Asset Plate for ALL EL assets (standard + legacy) —
   General cannot be classified pre-extraction (tag-derived groups are all
   Distribution). Only the General form/scoring/DB sync surfaces them.
4. **SDI/Planon in scope (nearly free):** Manufacturer/Model/Year are already
   `MASTER_COLS` package columns; `build_sdi_dataset()` renames EL
   `Serial Number` → `Serial` so the serial survives the `PRINT_OUT_COLS`
   projection. EL General rows export to Make / Model / Serial Number /
   Date Of Manufacture Or Construction. No package-table schema change;
   archive/retrieve/locking untouched.

## Key design points

- **Variant selection is server-side + data-driven:** `_el_form_variant(asset_group)`
  (General iff group non-blank and not in `get_distribution_asset_groups()`).
  Blank/unknown → Distribution. `base_route` stays pagination-only.
- **One template, conditional sections** in `review.html` (shared CSS/JS/lock/
  dictionary machinery; Distribution renders byte-identical output). Hidden
  `form_variant` input is a diagnostic echo in `ignored_form_fields`; no save
  derivation branches on it (`merge_form_into_structured` never clears keys
  absent from the POST, so reclassified assets keep their other-variant JSON).
- **No extraction rule-version bump; confidences project only when non-blank** —
  otherwise the whole EL corpus is flagged stale and re-extracted (billable).
  Backfill is targeted per-QR `FORCE_REPROCESS`.
- **Manufacturer whitelist guard:** `normalize_manufacturer` is a BF whitelist;
  EL wiring falls back to `_clean_raw_manufacturer` — never blank a non-empty read.
- **Scoring:** General review scoring = UBC Asset Tag + the four nameplate fields
  (`EL_REVIEW_GENERAL_SCORING_FIELDS`); stored group beats tag-derived group.
  Dashboard hover checklist adds the nameplate fields for General groups.

## Rules documentation

Canonical details live in `Markdowns_documentation/rules/review_apps.rules.md`
→ "EL Review Form Variants (2026-08-07)", `rules/sdi_process.rules.md` (Serial
rename), `special_processes/04_database_topography.md` (columns), and
`attributes_changes.md` (changelog). CLAUDE.md carries the summary bullet.

## Addendum (2026-08-07, same-day follow-up): Capacity pair + General listing columns

User-approved decisions: Capacity takes the **full Planon path**; Capacity is
**optional** (never scored).

- **Capacity + Capacity (UoM)** join the General Identity card as a dual-grid
  form-floating pair (modeled on the Distribution Power Rating pair). Bare
  value + unit as printed; NO unit whitelist (kVA, kW, HP, A, BTU, ...) and no
  hardcoded UoM code, unlike AMP/VLT.
- **Storage:** migration `2026-08-07_el_capacity_columns.sql` adds both TEXT
  columns to `sdi_dataset_EL` AND `sdi_print_out` / `sdi_print_out_arch`
  (packaging INSERTs every PRINT_OUT_COLS column). General rows only;
  Distribution rows blank. `_ensure_package_amperage_columns` raises with the
  migration name when a package table misses them on PostgreSQL.
- **Scoring isolation:** the review app keeps the pair in `EL_CAPACITY_FIELDS`,
  deliberately OUT of `EL_NAMEPLATE_FIELDS` / scoring / checklist / traffic
  light. The API-side `Config.EL_NAMEPLATE_FIELDS` DOES include the pair so it
  inherits the retry/fallback-gate exclusions, legacy raw-copy, and
  non-blank-only confidence projection (still no rule-version bump).
- **Extraction:** `_normalize_el_capacity_pair` splits a combined "75 kVA"
  reading into value + unit; a bare unit with no value is dropped. Prompt rules
  (standard + legacy): EL-0 only, number in `Capacity`, unit verbatim in
  `Capacity (UoM)`, never derived from voltage strings or the winding table.
- **SDI/Planon:** pair added to `PACKAGE_ONLY_COLS`; `ATTRIBUTE_SETS.py` lists
  `Capacity` and `Capacity UoM` under `Electrical` (the file mirrors Planon's
  configuration — without this the attribute-set filter clears the values for
  EL rows). The punctuation-insensitive template header match lands
  `Capacity (UoM)` in Planon column `Capacity UoM` (CI) with no rename entry.
- **General listing (`/review-all`) column drop:** hides Supply From (7),
  Amperage Rating (8), Volts (9), Location (10) via the same three-site
  DataTables mechanism the Distribution view already used
  (`hiddenColumns = isDistributionDashboard ? distributionHiddenColumns :
  generalHiddenColumns`). Headers stay in the DOM; the dashboard XLSX export
  keeps every column.
