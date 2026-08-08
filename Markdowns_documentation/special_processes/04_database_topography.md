# Database Topography and Relationships

Current documentation refresh: 2026-07-27.

> **🐘 DATABASE BACKEND — PostgreSQL (C4 cutover COMPLETE, 2026-06-08).** Operational workflow state now lives in **`qr_code_db` on PostgreSQL** (VM `127.0.0.1:5433`), reached through a backend-agnostic `db.py` layer switched by `DB_BACKEND=postgres` in `/home/developer/db_backend.env`. The SQLite `QR_codes.db` named below is now the **frozen rollback** (flip the env back to `sqlite` + restart to revert). The table/column model is identical on both engines. Details: `C4_CUTOVER_RUNBOOK.md` + memory `pg-cutover-complete`.

## Overview

Operational workflow state is held in **PostgreSQL `qr_code_db`** as of the 2026-06-08 cutover (the legacy SQLite `asset_capture_app_dev/data/QR_codes.db` is now the frozen rollback, not the live store). Authentication/user state remains in its own SQLite database (`User_control.db` / `auth_service/users.db`), unchanged by the cutover. All application tiers reach `qr_code_db` through the shared `db.py` layer.

**PostgreSQL engine nuances** (the schema is otherwise identical to the SQLite original): generated columns such as `ID_check` are `GENERATED ALWAYS AS (...) STORED` (PostgreSQL has no `VIRTUAL` generated columns); boolean-ish flags such as `new_draw` are TEXT `'TRUE'` / `'FALSE'` (single-quoted string literals on PG); quoted identifiers are case-sensitive (`"Building"`, `"Name"`, `"sdi_dataset_EL"`).

## PostgreSQL `qr_code_db` Core Tables

### `QR_codes`

QR-level state, including:

- `QR_code_ID`
- approval state
- AI status
- SDI exclusion state (`sdi`)
- location and space context
- per-capture GPS (added 2026-06-16): `capture_latitude`, `capture_longitude` (decimal-degree TEXT), and `capture_coord_source` provenance (`device` precise fix | `building` centroid fallback | `''`). Written by the mobile Capture App `/submit`; a stored `device` fix is never overwritten by a `building` centroid.
- optional capture details (added 2026-07-06): `capture_notes` (free text from the field tech, server-clamped to 200 chars) and `installation_date` (ISO `YYYY-MM-DD` TEXT, set only after an explicit ✓ confirm in the UI). Written by the mobile Capture App `/submit`; the latest non-empty submission wins and an empty resubmission never erases a stored value. Columns created by owner-run migration `scripts/migrations/2026-07-06_qr_codes_capture_notes_install_date.sql`.

### `QR_code_assets`

Process-placement and capture-tracking state, including:

- `code_assets`
- `Col_process`
- user / timestamp context

### `sdi_dataset`

Curated ME and BF records for approved assets.

Important columns include:

- `QR Code`
- `Building`
- curated asset fields
- `Approved`
- `Flagged`
- `Avg_ai_conf`

### `sdi_dataset_EL`

Curated EL records for approved assets.

Note: `ID_check` is a `GENERATED ALWAYS AS (...) STORED` column derived from `Building | UBC Asset Tag | Supply From` (PostgreSQL has no `VIRTUAL` generated columns; the SQLite original used `VIRTUAL`). Do not write to it from Python — the DB rejects the write. The column is used to detect divergence against `electrical_building_schema.ID_check` (see below) and surface the result in the SLD panel's Reconciliation column.

Nameplate columns (2026-08-07, owner-run migration `scripts/migrations/2026-08-07_sdi_dataset_el_nameplate_columns.sql`): `Manufacturer`, `Model`, `Serial Number`, `Year` — all `TEXT`, named after the structured-JSON keys (deliberately not ME's `Serial` DB-column rename). Populated by the EL review sync **only for General (non-distribution) assets**; Distribution rows are kept blank by contract. Exported to Planon via the SDI package (Make / Model / Serial Number / Date Of Manufacture Or Construction); `SDI_process/app.py::build_sdi_dataset` renames `Serial Number` -> `Serial` to match the package column.

### `electrical_building_schema`

Diagram-side table for Electrical Distribution. Each row represents a node parsed out of a building's Single Line Diagram PDF by the SLD extraction pipeline. Active rows have `new_draw = "TRUE"`; superseded rows (replaced by a re-extraction) are archived in-place with `new_draw = "FALSE"`.

Important columns include:

- `row_id` (PRIMARY KEY)
- `Building`, `Equipment ID`, `Supply From` (and the wider Voltage / Amperage / Power / Wire Rating pairs)
- `new_draw` — "TRUE" for the currently-displayed extraction, "FALSE" for archived
- `ID_check` — `GENERATED ALWAYS AS (TRIM(COALESCE("Building",'')) || ' | ' || TRIM(COALESCE("Equipment ID",'')) || ' | ' || TRIM(COALESCE("Supply From",''))) STORED` (was `VIRTUAL` on the SQLite original). Mirrors the same expression used on `sdi_dataset_EL`; matching the two strings is how the EL Review SLD panel decides whether the diagram and the captured asset agree.
- `sld_extract_run_id`, `sld_ai_extract_payload` — feedback-corpus join keys (the run that produced the row, and a JSON snapshot of the AI-output fields for diff vs human correction)
- `matching_check` — derived flag refreshed by `recompute_matching_check`

Reconciliation between this table and `sdi_dataset_EL` is the responsibility of the EL Review SLD panel; the Reconcile endpoint (`POST /sld/api/assets/<row_id>/reconcile`) writes both sides atomically when a reviewer resolves a `Supply From` divergence.

### `sdi_print_out`

Active SDI package staging.

### `sdi_print_out_arch`

Archived SDI packages.

### `SpaceUID`

Reference locations keyed by `Property.Property code` and `Space number`. The table has five nullable `TEXT` columns: `Space number`, `Property.Property code`, `Floor Code`, `Space Name`, and `Floor Name`. It has no primary key or unique constraint, so maintenance writes must explicitly guard the `(Property.Property code, Space number)` pair.

On 2026-07-21, owner-run migration `scripts/migrations/2026-07-21_spaceuid_special_locations.sql` seeded three fallback locations for each of the 765 distinct nonblank property codes then present: `Z01Rooftop` (`RT`), `Z02Notfound_Room` (`NF`), and `Z03External_building` (`EB`). Each uses `Space Name = -` and `Floor Name = Floor: -`. The underscore in `Z02Notfound_Room` is intentional because QR location parsing treats the first space as the end of the Space number. A same-day data correction changed the seeded `Space Name` from `No Room Identification` to `-` and wrote one field-level audit entry per row.

The migration is transactional and idempotent, fails on conflicting existing values, and writes field-level `audit_trail` rows atomically. It is a snapshot seed, not a trigger: future property codes receive these rows only when the migration is deliberately rerun after review. `Buildings_with_SpaceUID` exposes the seeded locations only for property codes that also exist in `Buildings`.

### `life_cycle`

Main table for the Life Cycle Assessment feature (the in-process `life_cycle` Blueprint mounted at `/life-cycle`). REBUILT on every "Update Database" run via pandas `to_sql(if_exists="replace")`. Built from the source Excel workbook ("UBC - Asset Basic Info.xlsx") filtered to Asset Group Code = `ME.91.902.4817.5956` (Heating Water Storage Tanks), with `Floor Name` joined from the `SpaceUID` table.

Notes:

- All columns are `TEXT` except `years` (float) and `months` (integer).
- Carries a composite foreign key `life_cycle_space_floor_fkey` referencing `space_floor` (Property Code, Space Number).
- Installation Date is the only Complete / Incomplete criterion: present is Complete and missing is Incomplete. Make, Space Number, and Serial Number do not affect this tab split.
- At read time the page also derives a "Captured" flag (QR present in `QR_codes` with a `date_set` — i.e. field-captured) and a "Capture Date" (`QR_codes.date_set`) per row. Those tables are read-only inputs.

### `space_floor`

Deduplicated reference table mapping (Property Code, Space Number) -> Floor Name, built from `SpaceUID` with a PRIMARY KEY on the composite key. Rebuilt on every load. Referenced by `life_cycle` via `life_cycle_space_floor_fkey`.

### `life_cycle_meta`

Small key/value table that survives the `life_cycle` rebuild. Stores the `last_loaded` timestamp shown in the dashboard footer.

### Supporting Tables

- `json_files`
- `process_type`
- sequence / lookup tables used by SDI export or dashboard features

### `Buildings`

Operational building lookup keyed by nullable `TEXT` column `Code` (protected by
`ux_buildings_code`). `QR_codes."Building Code"` references `Buildings."Code"`
with `ON UPDATE CASCADE` and `ON DELETE RESTRICT`.

On 2026-07-27, owner-run migration
`scripts/migrations/2026-07-27_buildings_property_metadata.sql` added nine
nullable `TEXT` metadata columns: `Alternative Name(s)`, `Address`,
`Postal Code`, `Zone`, `FM`, `Geo Zone`, `GPS Coordinates`, `Area (Gross)`,
and `Year`. The migration populated all 327 existing building rows by trimmed
`Code` match from `Sheet 1` of
`UBC - All Properties List with GPS Coordinates.xlsx` (SHA-256
`678c6e472a6f0d37bd81e1e0bdddc8e69e33570140ded97877ae9cfee6b4334f`).
It ignored the workbook's 1,687 codes that were not already in `Buildings`,
converted blank cells to `NULL`, and wrote 2,644 field-level `audit_trail`
records atomically.

This is a one-time snapshot import, not an ongoing synchronization mechanism.
The existing `Sorted by name` and `Buildings_with_SpaceUID` views retain their
original explicit column lists and do not expose the nine metadata columns.

### `Asset_System_info` and Asset Master Lookup

`Asset_System_info` is a view, not a base table. It joins:

- `"UBC - Asset Data Master Info"` as `master`
- `"SUST - System List"` as `system`

The view's `"Code"` column is sourced from `"UBC - Asset Data Master Info"."Code"`. As of the 2026-06-03 VM migration, that source column is `TEXT` and every non-null value is stored as a 10-character zero-padded code, for example `0000054137` and `0000154409`.

Historical `QR_codes*.db` backup files may still show `"Code"` as numeric/integer without leading zeros. Do not use those backup files to validate current production state.

### `Asset_Group`

`Asset_Group` is the asset-classification lookup. Its `elec_dist_setup`
column is a required one-character Y/N flag: `N` is the default for existing
and newly inserted groups, and the table constraint rejects any other value.
The owner-run, transactional migration is
`scripts/migrations/2026-08-04_asset_group_elec_dist_setup.sql`.

The following electrical distribution names are seeded to `Y` by
`scripts/migrations/2026-08-04_asset_group_elec_dist_setup_values.sql`:
Panels (both matching rows), Other Service and Distribution, Interior
Distribution Transformers, Main Transformers, Motor Control Centers,
Enclosed Circuit Breakers, and Automatic Transfer Switches. All other rows
remain `N` unless explicitly changed later.

As of 2026-08-04 the application consumes this flag as the single source of
the EL General vs Distribution split: the EL review dashboard loads the `Y`
names via `get_distribution_asset_groups()` (60-second TTL cache) for the
`/review-all` vs `/review-distribution` views, the XLSX export, and the
amperage-warning gating, and the SLD Switch Over query filters by it via
`_distribution_asset_groups()` in `sld_blueprint.py`. The hard-coded
`EL_DISTRIBUTION_ASSET_GROUPS` frozenset in `excel_export.py` (tuple mirror
in `sld_blueprint.py`) is only the fallback when the column or database is
unavailable (e.g. the frozen local SQLite copy).

## Relationship Notes

- Review apps sync JSON corrections into the SDI dataset tables.
- SDI Process reads the curated SDI tables, not raw extraction JSON, for package staging.
- Manual Entry and SDI exclusion rely on QR-level state in `QR_codes` plus process state in `QR_code_assets`.

## Current Integrity Rules

- valid QR IDs should be unique after normalization
- placeholder QR IDs must not be treated as real assets
- `Col_process = 2` and `sdi = 1` should stay aligned for manual / excluded assets

## Architectural Caution

The schema is operationally shared across many modules, but not every module uses the same subset of tables. Changes to QR identity, approval, SDI state, or curated field schemas have platform-wide impact.
