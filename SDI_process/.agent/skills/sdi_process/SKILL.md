---
name: sdi_process
description: Developer skill guide for managing the SDI Process app. Covers the packaging lifecycle, CMMS planon data verification, Excel template manipulation, and SQL exclusion sequences.
---

# SDI Process App Skill

Current documentation refresh: 2026-04-28.

## Use this skill when
- Modifying the SDI packaging logic (`export_to_sdi`, `export_to_planon`, `move_to_archive`).
- Updating the standalone `asset_template_validation_ver00.py` constraints.
- Modifying the fields populated dynamically on the SDI Dashboard.
- Troubleshooting issues with packaged assets missing from queues or duplicated in imports.

## Do not use this skill when
- Modifying the source of the asset data extraction (see `API/.agent` or `review/.agent`).
- Adding entirely new types of data entry interfaces outside CMMS mapping.

## Instructions
When modifying the SDI process flow, you are strictly handling the **transition** of data. You are reading from PostgreSQL `qr_code_db` and formatting it to match the stringent requirements expected by Planon. Pay careful attention to the data exclusion rules (e.g. `get_codes_in_print_out_table().union(get_codes_in_archive_table())`) to ensure you do not inadvertently show packaged assets in the unpackaged view. 

## Project Navigation

```
SDI_process/
â”œâ”€â”€ app.py                      # Core CRUD endpoints and Dashboard logic
â”œâ”€â”€ asset_template_validation_ver00.py # Handles the strict rule validation logic
â”œâ”€â”€ template/
â”‚   â”œâ”€â”€ dashboard.html          # DataTables-driven UI
â”‚   â”œâ”€â”€ Import Assets-TEMPLATE-082923.xlsx # The canonical export mapped column target
```

## Creating & Validating Data Packages

1. **Packaging**: Assets reside in the `sdi_dataset` (ME/BF) or `sdi_dataset_EL` tables. Moving an asset to a package inserts its data into `sdi_print_out` and assigns it an `id_print_out` (e.g., `SDI-00005`).
2. **Exporting (Planon Tempate)**: Exports the `sdi_print_out` rows associated with a specific package into a `.xlsx` using pandas. Applies specific `COLUMN_RENAME_MAP` logic and forces specific categorical values (e.g., Panels -> `EL.21.306.4067`).
3. **Data Constraint Rules**: The `asset_template_validation_ver00.py` logic reads from the generated `.xlsx` to run rigorous quality checks (Booleans, Types, Mandatory, Strings, Cross Dependencies).
4. **Validation JSONs**: Depending on the validation outcome, JSON files are generated in `sdi_json_output/` (`errors_SDI...json` or clean `SDI...json`).

## Archiving
When `move_to_archive()` is executed on an SDI Print Control code, the package rows are atomically moved from the active table (`sdi_print_out`) into the archive table (`sdi_print_out_arch`). To maintain uniqueness, `get_next_sdi_package_id()` relies on a separate `sdi_sequence` tracker table, actively scanning both active and archive tables to prevent regression.
