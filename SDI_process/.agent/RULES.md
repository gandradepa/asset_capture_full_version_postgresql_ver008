# SDI Process Coding Rules & Standards

Current documentation refresh: 2026-04-28.

## Python Backend

### 1. Database Connections
- Always use `with sqlite3.connect(DB_PATH, timeout=10) as conn:` to ensure locks are released promptly.
- Use `table_exists()` checks aggressively, as the archive tables might not exist on a fresh database.
- Use parameterized queries (`?` placeholders) when injecting IDs like `sdi_control_id`.

### 2. Multi-Table Exclusions
When building the "Unpackaged Assets" dataset, an asset is considered *Packaged* if its QR code is present in EXITHER `sdi_print_out` OR `sdi_print_out_arch`. 
- **Rule**: Always perform a `.union()` of `get_codes_in_print_out_table()` and `get_codes_in_archive_table()` before filtering unpackaged data.

### 3. File System Safety
- Use `_safe_filename()` to scrub building codes and SDI IDs before using them to save `.xlsx` or `.json` files.
- Ensure the `JSON_OUTPUT_DIR` has `os.makedirs(..., exist_ok=True)` checks to prevent crashing when the network drive (`S:\`) is unavailable on the local Windows environments.

### 4. Excel Template Manipulation
- `export_to_planon` uses Pandas rather than `openpyxl` directly when possible, renaming columns to match Planon header constraints via `COLUMN_RENAME_MAP`.
- Asset Group translation: When `export_to_planon` finds "Panels", it must hardcode `EL.21.306.4067`. Otherwise, it joins against the `Asset_Group` table to inject the "Full Classification" code.

---

## Validation Script Rules (`asset_template_validation_ver00.py`)

### 1. File Handling
- Use `pd.read_excel(file_path, header=None)` to load raw data without unwanted type inferences that might mangle CMMS codes.

### 2. Output Handling
- **Errors**: Generates `errors_{base_name}.json`.
- **Clean**: Generates `{base_name}.json`.
- The `return_summary=True` flag is mandatory when invoking this script as an imported module from another workflow so that UI layers can render statistics.

### 3. Cross-Field Dependency Enforcement
If adding new rules, they go in segment `# --- 3. Cross-Field Validation ---`. Example:
If `Space.Floor.Property` is present, `Space.Floor.Floor` and `Space.Space number` cannot be blank.

---

## Frontend / HTML Rules

- `dashboard.html` uses DataTables.js extensively.
- Use Bootstrap Flash Messaging categories: `success`, `info`, `warning`, `danger`.
- Special category: `confirmation` and `planon_confirmation`. These trigger SweetAlert/Modal dialogs on the frontend to warn the user about duplicate QR codes or previously exported Planon packages before allowing a forced override.
