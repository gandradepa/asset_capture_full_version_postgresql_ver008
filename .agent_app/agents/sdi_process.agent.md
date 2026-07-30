# SDI Process Application â€” Agent Instructions

Current documentation refresh: 2026-04-29.

## Embedded Mode

SDI Process runs both standalone (`sdiprocess.assetcap.facilities.ubc.ca`) and embedded inside the central Dashboard's iframe at `#sdi-view`. Embedded mode is detected by a `@main_bp.before_request` hook that sets `g.embedded = request.args.get('embedded','').lower() == 'true'`. The dashboard template wraps `<nav class="user-nav">`, `<header class="ubc-navbar">`, and `<div class="ubc-top-strip">` in `{% if not g.embedded %}`. Cookie config: `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, plus the matching `REMEMBER_COOKIE_*`. Nginx CSP: `frame-ancestors 'self' https://dashboardprod.assetcap.facilities.ubc.ca;`. See `Markdowns_documentation/rules/sdi_process.rules.md` for details.

## Application Identity

The **SDI Process** application is a Flask web app that serves as a staging and packaging manager for transferring structured asset data from the operational PostgreSQL database (`qr_code_db`) into the format required by the enterprise CMMS (Planon). 

It allows administrators to bundle "Unpackaged Assets" into "Print Control Packages", generate formatted Excel files for import, validate those Excel files against strict Planon constraints, and eventually archive the packages.

**Location**: `/home/developer/SDI_process/` (Production)

---

## Architecture Overview

```text
SDI_process/
â”œâ”€â”€ .agent/                             # This documentation directory
â”œâ”€â”€ app.py                              # Main Flask application
â”œâ”€â”€ asset_template_validation_ver00.py  # Standalone data validation script
â”œâ”€â”€ template/
â”‚   â”œâ”€â”€ dashboard.html                  # Tabbed UI for Unpackaged/Packaged
â”‚   â”œâ”€â”€ login.html                      # Authentication integration
â”‚   â”œâ”€â”€ Import Assets-TEMPLATE*.xlsx    # Base template for Planon exports
â”‚   â””â”€â”€ static/                         # CSS/JS and Images
â”œâ”€â”€ sdi_json_output/                    # Directory for validation output JSONs
â”œâ”€â”€ requirements.txt                    # Dependencies
â””â”€â”€ venv/                               # Python virtual environment
```

---

## Data Flow

```mermaid
graph TD
    A[PostgreSQL qr_code_db] -->|Read `sdi_dataset` (ME/BF) & `sdi_dataset_EL`| B(Unpackaged Assets Tab)
    B -->|Create Print Package| C[sdi_print_out table]
    C -->|View| D(Packaged Assets Tab)
    D -->|Export to Planon| E[Excel Tempalte Export]
    E -->|Upload to Check| F[asset_template_validation_ver00.py]
    F -->|Validation Result| G{Errors?}
    G -- Yes --> H[errors_SDI_...json]
    G -- No --> I[Valid SDI_...json]
    D -->|Move to Archive| J[sdi_print_out_arch table]
```

### Key Database Tables (PostgreSQL `qr_code_db`)
- `sdi_dataset` / `sdi_dataset_EL`: Source tables containing approved assets ready for packaging. This data originates from the Review Apps.
- `sdi_print_out`: Active staging table. Contains assets grouped by a generated `id_print_out` (e.g., `SDI-00005`).
- `sdi_print_out_arch`: Archive table for completed packages.
- `sdi_sequence`: Metadata table tracking the highest `id_print_out` sequence number to prevent collisions.

---

## Flask Route Catalog

| Method | Path | Function | Description |
|--------|------|----------|-------------|
| GET/POST | `/login` | `login()` | Auth via shared `auth_service` |
| GET | `/` | `dashboard()` | Main SPA with Unpackaged/Packaged tabs |
| POST | `/export` | `export_to_sdi()` | Moves unpackaged assets into a new `sdi_print_out` package |
| POST | `/export-planon` | `export_to_planon()` | Generates the `.xlsx` Planon import file |
| POST | `/exclude_package` | `exclude_package()` | Deletes a package, returning assets to unpackaged |
| POST | `/move_to_archive` | `move_to_archive()` | Moves a package from active to `_arch` table |
| POST | `/retrieve_from_archive`| `retrieve_from_archive()`| Restores packages from `_arch` back to active |

---

## Data Validation Layer

The `asset_template_validation_ver00.py` script ensures that before the team attempts to inject data into Planon, it adheres strictly to Planon constraints:
1. Validates Booleans (`true`, `false`, `1`, `0`, `yes`, `no`).
2. Validates string length limits based on row 8 of the Planon template.
3. Performs Cross-Field Validation (e.g., if one part of a Space Group is filled, all three must be filled).
4. Validates Power Type enums (`N`, `E`, `S`, `ES`, `NE`, `NES`, `NS`).
5. Generates JSON reports stored in `sdi_json_output/`.

---

## Key Conventions

1. **Authentication**: Uses the shared `auth_service`.
2. **Path Fallbacks**: The validation script contains legacy fallbacks to a mapped Windows network drive (`S:\MaintOpsPlan\...`). Ensure this fallback pattern is respected or logged correctly on the Ubuntu server.
3. **Sequence Alignment**: The `get_next_sdi_package_id()` function actively checks both `sdi_print_out` and `sdi_print_out_arch` to compute the next valid integer `SDI-XXXXX`. Never mutate the `sdi_sequence` table manually.
