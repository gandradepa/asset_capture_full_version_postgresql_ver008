# Workflow: Parameter Update and Atomic Rename

Current documentation refresh: 2026-04-28.

## Purpose

Update QR capture parameters without leaving the platform in a split state.
Building/location changes are metadata renames inside the same discipline.
Asset-type changes are discipline migrations and must retire stale review JSON
instead of relabeling one discipline's payload as another.

## Inputs

- QR code plus old/new building, location, and asset type
- related JSON payload, image files, DB rows, and processed logs

## Main Steps

1. Validate the QR and compare old/new parameters.
2. Compute the full rename set across images, JSON, processed logs, and DB tables.
3. For building/location-only changes, rename/update the matching active review JSON.
4. For asset-type changes, rename photos and `QR_code_assets`, retire active same-QR review JSON to `.bak_*_param_update`, clear processed logs, reset `QR_codes.ai_status` to `0`, and retire stale curated SDI rows so the chained AI run can rebuild the correct table.
5. Apply the change as one logical operation.
6. If any step fails, roll back the already-applied pieces.
7. Rebuild any cached or derived state that depends on the QR code.

## Assets That Must Stay Aligned

- `Capture_photos_upload/*.jpg`
- `Output_jason_api/*.json`
- processed log files and automation traces
- `QR_codes` and `QR_code_assets`
- curated SDI dataset rows if they already exist

## Guardrails

- do not run ad-hoc partial renames
- preserve building and asset-type identity during the rename
- do not carry JSON payloads across ME/BF/EL discipline schemas by filename-only conversion
- reject asset-type changes for QRs already present in active or archived SDI packages
- verify review navigation and SDI state still resolve against the renamed QR

## Verification

- confirm there are no leftover files under the old QR code
- confirm database rows match the new building/type and stale curated rows are gone after an asset-type change
- confirm processed log files no longer pin old filenames
- confirm review, dashboard, and SDI pages resolve the asset correctly after rename
