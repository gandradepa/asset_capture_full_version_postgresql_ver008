# SDI Process Rules

Current documentation refresh: 2026-06-23.

## Purpose

The SDI Process app stages approved assets for packaging and Planon export.

## Database Backend Rules

- Active operational data is PostgreSQL `qr_code_db`, not the legacy SQLite file.
- Production VM database: `127.0.0.1:5433`, app DSN from `/home/developer/db_backend.env`, app role `assetcap_app`.
- Local development / DBeaver database: PostgreSQL `qr_code_db` on `127.0.0.1:5432`, normally accessed as user `postgres`.
- Legacy `asset_capture_app_dev/data/QR_codes.db` is the frozen rollback/reference copy only. Do not treat it as the SDI Process source of truth.
- Owner-run schema changes must be applied as the PostgreSQL owner role (`developer` on VM, `postgres` locally) through `scripts/migrations/`; SDI request handlers must not create or alter tables.

## Dataset Rules

- `build_sdi_dataset()` reads from:
  `sdi_dataset` and `sdi_dataset_EL`
- Approved source rows are necessary but not sufficient for SDI packaging.
- `sdi_dataset` carries ME and BF.
- `sdi_dataset_EL` carries EL.
- EL nameplate columns (2026-08-07): `sdi_dataset_EL` stores General-asset `Manufacturer`, `Model`, `Serial Number`, `Year` (blank on Distribution rows). `Manufacturer`/`Model`/`Year` flow into packages unchanged because they are already `MASTER_COLS` package columns; `build_sdi_dataset()` renames the EL `Serial Number` column to the package column name `Serial` (next to the `UBC Asset Tag` -> `UBC Tag` rename) — without that rename the EL serial is silently dropped at the `PRINT_OUT_COLS` projection. Export renames `Serial` back to Planon `Serial Number` as before; archive/retrieve transfer the four values via the existing `PRINT_OUT_COLS` list with no schema change.
- EL Capacity pair (2026-08-07 follow-up): `sdi_dataset_EL`, `sdi_print_out`, and `sdi_print_out_arch` carry `Capacity` + `Capacity (UoM)` (owner-run migration `2026-08-07_el_capacity_columns.sql`; `_ensure_package_amperage_columns` raises with that migration name if a package table is missing them on PostgreSQL). The pair is in `PACKAGE_ONLY_COLS`, so packaging, archive, and retrieve carry it automatically; ME/BF rows fill blank. Export lands the values in the Planon template columns `Capacity` (CH) / `Capacity UoM` (CI) via the punctuation-insensitive header match — no `COLUMN_RENAME_MAP` entry. `ATTRIBUTE_SETS.py` lists `Capacity` and `Capacity UoM` under `Electrical` so the attribute-set filter keeps them for EL rows (that file mirrors Planon's configuration). No UoM default-fill at export: capacity units vary (kVA, kW, HP, ...) unlike the hardcoded `AMP`/`VLT`, so values export exactly as stored.
- Once a QR exists in `sdi_print_out` or `sdi_print_out_arch`, it is considered in the SDI process. Review sync must preserve/coerce source approval to `"1"` and review JSON approval to `"True"`; package tables do not carry their own `Approved` column.

## Unpackaged-Asset Rules

- Exclude any QR already present in `sdi_print_out` or `sdi_print_out_arch`.
- Exclude any QR with `QR_codes.sdi = 1`.
- Only Review **New Assets** (`QR_code_assets.Col_process = 0`) feed Unpackaged Assets for ME, BF, and EL. Review **Update Existing** (`1`) and **Manual Entry** (`2`) stop in review and must not be packaged, even if a curated source row remains approved.
- Location enrichment from `QR_codes` must use normalized string QR keys.
- Capture GPS enrichment from `QR_codes."GPS Coordinates (lat,long)"` must use normalized string QR keys. Package tables may stage the value via owner-run migration `scripts/migrations/2026-06-23_sdi_package_gps_coordinates.sql`, but `QR_codes` remains the source of truth for the Planon GPS column.
- Placeholder QR IDs such as blank, `None`, `nan`, and `null` must not participate in joins.
- One QR code may contribute at most one enrichment row to the Unpackaged Assets view.

## QR Integrity Rules

- Placeholder `QR_code_ID` values are invalid.
- Valid QR codes should be unique after normalization.
- SDI joins must not depend on numeric coercion for alphanumeric QR codes.

## Packaging Rules

- `sdi_print_out` is the active package table.
- `sdi_print_out_arch` is the archive table.
- Package IDs come from the SDI sequence generator and must stay unique.
- Repackaging flows must cleanly move rows between active and archive tables.
- Package creation is user-facing as **Create SDI Package**. The `/export` route is retained for compatibility but must not be described as Planon export.
- Package creation preflights active/archive QR state before writing; candidate QRs already in archive are blocked, and active duplicates require the existing force-replace flow.
- Owner-run PostgreSQL migrations and route preflights enforce core package invariants: no duplicate normalized QR inside active/archive, no active/archive overlap, nonblank `"QR Code"` and `id_print_out`, valid `QR_codes` parent, approved source state, and no unexported archive inserts. The historical SQLite guardrail script is rollback/reference material only after the C4 cutover.
- Because overlap triggers reject transient active/archive duplication, Archive and Retrieve must move rows as delete-then-insert inside one transaction using rows fetched before the delete. Do not return to copy-then-delete ordering.
- The DB guardrail phase intentionally uses indexes and triggers only; do not rebuild package tables or add foreign keys without a separate migration plan.

## Export Rules

- Export code should use the Planon template mapping rather than ad hoc column names.
- The Planon template header is `GPS Coordinates (lat,long)` with no space after the comma. Export must populate that column from `QR_codes."GPS Coordinates (lat,long)"` for the row's `QR Code` when available.
- Validation output should be written to `SDI_process/sdi_json_output/`.
- No-data or validation-error paths should fail clearly without corrupting package state.

## Validation Checklist

- A duplicate QR does not appear twice in Unpackaged Assets.
- Manual-entry / excluded assets do not appear in SDI packaging.
- Packaged and archived assets are excluded from the Unpackaged tab.
- Placeholder or malformed QR rows are rejected or ignored safely.

## Planon Export Rules

- `export_to_planon()` generates Planon-ready export files.
- UBC tag information is parsed by `parse_ubc_tag_info()` to extract structured identifiers.
- Year fields are formatted by `format_year_to_date()` for Planon compatibility.
- Export validation uses the Planon template mapping rather than ad hoc column names.
- No-data or validation-error paths should fail clearly without corrupting package state.

## Validation Log Rules

- Validation logs are generated during package creation and export.
- Logs are accessible through the SDI Process UI via `get_validation_logs()` and `get_validation_log(filename)`.
- Validation output is written to `SDI_process/sdi_json_output/`.

## Archive Management Rules

- `sdi_print_out` is the active package table; `sdi_print_out_arch` is the archive.
- `move_to_archive()` transfers packages from active to archive only when every selected package row has `print_out = 1`.
- `retrieve_from_archive()` moves packages back from archive to active and preserves source/review approval.
- `exclude_package()` removes individual packages from the active queue after preserving source/review approval so non-manual rows return to Unpackaged Assets.
- Package IDs from the SDI sequence generator must stay unique across operations.
- Package archive/retrieve movement must not be used as a reason to clear the source approval of a packaged QR. Approval source remains `sdi_dataset` / `sdi_dataset_EL` plus review JSON.
- Package actions must write `audit_trail` rows with action, user, package ID, row count, QR list, source table, and target table.
- `scripts/audit_sdi_package_integrity.py` is the read-only health check for package overlap, duplicate QRs, blank package IDs/QRs, missing guardrail indexes/triggers, unexported archive rows, and approval drift.
- Archive retrieval is global: the page-level **Retrieve Archives** action reads `sdi_print_out_arch` without requiring a selected building, so archive-only buildings can be restored.
- After a retrieve, a single restored building redirects to that building's Packaged Assets view; multiple restored buildings return to the unselected dashboard with an informational flash.
- SDI Package, active-package Archive, Exclude, and Planon Export remain building-scoped and require the building-filter workspace to be loaded.

## Embedded Mode Rules (`?embedded=true`)

SDI Process runs both standalone and embedded inside the central Dashboard iframe. Embedded mode follows the same pattern as the review apps.

### Detection

- A `@main_bp.before_request` hook sets `g.embedded = request.args.get('embedded','').lower() == 'true'`.
- The hook is registered separately from any `@login_required`-wrapped hook so `g.embedded` is always available to templates.

### Cookie configuration

- SDI Process sets `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, and the matching `REMEMBER_COOKIE_*` triple alongside `SESSION_COOKIE_DOMAIN`.

### Template rules

- `template/dashboard.html` wraps `<nav class="user-nav">`, `<header class="ubc-navbar">`, and `<div class="ubc-top-strip">` in `{% if not g.embedded %}` so they do not render inside the iframe.
- Functional controls (building filter, tabs for Unpackaged / Packaged / Validation Log, modals) remain visible in embedded mode.
- A body class `embedded-mode` plus matching CSS provides fallback chrome suppression.

### Link propagation

- A bottom-of-body script preserves `?embedded=true` on internal `<a>` clicks (ignoring `#`, `http://`, and `mailto:`).
- Form-based POST navigation in SDI (e.g. `export_to_sdi`, `move_to_archive`, `retrieve_from_archive`) is same-origin, so embedded state is preserved through the redirect chain by virtue of the URL parameter being on the form action targets when needed.

### Nginx CSP

- `/etc/nginx/sites-available/sdi_process` carries `Content-Security-Policy: frame-ancestors 'self' https://dashboardprod.assetcap.facilities.ubc.ca;` with the `always` flag so the header survives 302 login redirects.
