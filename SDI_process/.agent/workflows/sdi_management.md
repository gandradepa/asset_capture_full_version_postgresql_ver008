---
description: An end-to-end operational guide on managing the SDI asset packaging lifecycle via the web dashboard.
---

# SDI Database Management Workflow

Current documentation refresh: 2026-04-28.

This workflow documents the lifecycle of packaging, validating, and submitting structured assets to Planon.

## Prerequisites
- Successful login via the `auth_service` authentication screen.
- Access to the `/` Dashboard route.

---

## 1. Unpackaged Assets Tab

This view connects dynamically to the `sdi_dataset` and `sdi_dataset_EL` tables, listing all assets classified under process 0 or process 2 by reviewers that have not yet been placed in an SDI Print package.

1. **Select Building**: Use the dropdown filter at the top for package creation and active package operations. The application groups extraction and metadata verification strictly by building.
2. **Review Data Completeness**: Check that the `Asset Group`, `Attribute`, and `Description` columns are populated. If they possess missing values, the system will actively reject a package creation attempt.
3. **Assemble Package**: Click the **Create Package** (or **Export to SDI**) button. The application assigns a unique sequence (e.g., `SDI-00005`) and moves the rows into `sdi_print_out`.

---

## 2. Packaged Assets Tab

This view lists all assets grouped within the staging table `sdi_print_out`. 

1. **Select Package Filter**: Use the "SDI Print Control" dropdown.
2. **Validation and Planon Export**: Select a specific package ID, then hit **Export to Planon**. 
    - The Flask Backend maps SQL columns into CMMS column names (`Asset_Group` -> "Asset Group", `Technical Safety BC` -> "Previous (OLD) ID").
    - The server dynamically invokes the standalone `asset_template_validation_ver00.py` algorithm on the generated file.
3. **Download File**: A formatted `.xlsx` (matching `Import Assets-TEMPLATE*.xlsx`) will be generated.

---

## 3. Package Lifecycle Management

In the Packaged Assets view, administrators have three critical toggle buttons linked to the active package constraint:

- **Exclude Package**: Deletes the package sequence reference (`SDI-XXXXX`). Assets are removed from the staging `sdi_print_out` table, effectively returning them to the "Unpackaged Assets" tab for rework.
- **Move to Archive**: Confirms the Excel upload was successful. Assets are migrated from `sdi_print_out` directly to `sdi_print_out_arch`. They vanish from the dashboard.
- **Retrieve from Archive**: Permits an administrator to rescue an archived package. Moves rows back from `sdi_print_out_arch` directly to `sdi_print_out` for re-export handling. Retrieval is available from the page-level **Retrieve Archives** button even before a building is selected, so archive-only buildings can be restored.

---

## 4. Handling Validation Errors 

If the UI displays a failure via the Flash system:
1. Examine the JSON logs inside `/home/developer/SDI_process/sdi_json_output/`.
2. Look for the `errors_SDI_...json`. 
3. Locate the error message. Common causes: `Length exceeds limit`, `Invalid Boolean`, or `Incomplete Location`.
4. Fix the source data via the Review App for that specific QR Code, then Exclude the Package and repackage.
