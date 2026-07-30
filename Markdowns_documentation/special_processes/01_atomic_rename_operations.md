# Special Process: Atomic Rename and Parameter Update

Current documentation refresh: 2026-04-28.

## Why It Exists

The platform derives identity from filenames, JSON payloads, and DB rows. A QR or building change is therefore not a simple field edit. It is a coordinated rename across multiple stores.

## Stores That Must Stay Aligned

- image filenames in `Capture_photos_upload/`
- JSON filenames in `Output_jason_api/`
- JSON interior fields such as `qr_code`
- DB rows in `QR_codes`, `QR_code_assets`, SDI tables, and related tracking tables
- processed-log references when the platform keeps filename-based sync logs

## Required Flow

1. Resolve the full set of files and DB rows affected by the rename.
2. Validate the target QR / building values and check for collisions.
3. Rename files first in a reversible sequence or stage them with rollback context.
4. Update DB rows within a transaction.
5. Update JSON interior fields and mark the JSON as modified when needed.
6. Commit only after the whole set succeeds.
7. Roll back on any failure.

## Common Failure Modes

- JSON renamed but images not renamed
- DB updated but old filename remains on disk
- temporary QR replacement collides with an existing permanent QR
- review navigation and processed logs still point at the old filename

## Current Rule

Do not treat a rename as a single-table update. It is a multi-store transaction.
