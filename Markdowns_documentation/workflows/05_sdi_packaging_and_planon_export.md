# Workflow: SDI Packaging and Planon Export

Current documentation refresh: 2026-06-02.

## Purpose

Package approved curated assets for SDI printing and Planon-facing export workflows.

## Inputs

- approved rows in `sdi_dataset` and `sdi_dataset_EL`
- SDI Process application state
- `QR_codes.sdi` exclusion flag and related manual-entry state

## Main Steps

1. Open SDI Process and filter by building for package creation, active package review, archive, and Planon export actions.
2. Load unpackaged approved assets from the curated SDI tables.
3. Exclude any QR where `QR_codes.sdi = 1`.
4. Review the unpackaged list and use **Create SDI Package** to generate an active package.
5. Write package rows into `sdi_print_out`; the Packaged Assets tab reads this active table.
6. Use the validation log to confirm package integrity before downstream export.
7. Export to Planon using `export_to_planon()` with UBC tag parsing and year formatting; successful export sets `sdi_print_out.print_out = 1`.
8. Review validation logs through the SDI Process UI.
9. Archive only fully Planon-exported packages, retrieve archive rows back to `sdi_print_out`, or exclude active packages back to Unpackaged Assets.
10. Use the page-level Retrieve Archives action to restore archived packages even when their building is not currently listed in the building dropdown.

## Current SDI Rules

- only approved curated rows are eligible for packaging
- manual-entry / excluded assets must not appear in the package queue
- QR enrichment joins must use normalized string keys, not numeric coercion
- placeholder QR IDs must never participate in SDI joins or package staging
- optional `QR_codes.installation_date` is joined as `Installation Date`, normalized to `YYYY-MM-DD`, preserved through active/archive package transfers, and exported blank when absent or invalid
- Retrieve and Exclude preserve approval in source tables, `QR_codes`, and review JSON
- Archive is hard-blocked unless every selected row has `print_out = 1`
- package actions write `audit_trail` rows with package ID, row count, QR list, source table, and target table
- PostgreSQL owner-run migrations plus route preflights enforce package uniqueness, active/archive separation, approved source state, valid QR parents, exported-only archive inserts, and packaged-row approval/delete protection. The old SQLite guardrail script is rollback/reference material only
- Retrieve Archives is global: it reads `sdi_print_out_arch` without requiring a selected building, while SDI Package, Archive, Exclude, and Planon Export remain building-scoped.

## Outputs

- packaged SDI print-control rows
- package history and validation state
- export-ready dataset for downstream systems (Planon-formatted)
- validation logs in `SDI_process/sdi_json_output/`

## Verification

- confirm no duplicate QR rows appear in unpackaged assets
- confirm manual-entry assets are absent when excluded
- confirm package creation moves rows out of the unpackaged queue
- confirm Planon export generates correctly formatted output
- confirm validation logs are accessible and accurate
- confirm archive operations correctly move packages between active and archive tables
- confirm `scripts/audit_sdi_package_integrity.py` reports installed guardrail objects and no active/archive overlap, duplicate QRs, blank package IDs/QRs, missing QR parent rows, or approval drift
