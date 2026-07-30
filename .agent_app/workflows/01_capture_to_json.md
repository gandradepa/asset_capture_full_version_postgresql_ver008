# Workflow: Capture to JSON

Current documentation refresh: 2026-07-07.

## Purpose

Capture photos, create or update QR identity records, and leave a clean input set for extraction.

## Inputs

- user session in `asset_capture_app_dev`
- building, asset type, and location context
- one or more captured photos per asset sequence

## Main Steps

1. Launch the capture app and authenticate.
2. Start a capture session for the selected building and asset type.
3. Capture the required photos plus, optionally, an **Extra Photo** (sequence `-4` for ME, `-3` for BF/EL). The Extra Photo tile is rendered with `data-optional="true"` and is excluded from the "all required captured" check.
4. Save photos into `Capture_photos_upload/` using the `<QR> <Building> <Type> - <Seq>.jpg` pattern. `save_image_file()` applies `ImageOps.exif_transpose()` before writing, so phone photos with an EXIF Orientation tag are physically rotated to portrait on disk.
5. Upsert `QR_codes` with the QR-level state and location context, plus the optional `capture_notes` / `installation_date` fields when provided.
6. Upsert `QR_code_assets` with the per-photo `code_assets` rows.
7. If the QR is temporary, keep the temporary code until the atomic rename workflow runs.

## GPS Capture (added 2026-06-16)

The Capture App records where each asset was captured into `QR_codes`:

- **Precise device GPS** — `capture.html` calls the browser Geolocation API (`navigator.geolocation.getCurrentPosition`) at save time; the Start screen also primes the one-time permission grant early (only when the permission is still undecided). The OS/browser permission prompt cannot be removed (a hard browser rule), but after the user allows it once ("Allow While Using App") the read is silent.
- **Building-centroid fallback (no prompt)** — if precise GPS is denied or unavailable, `/submit` fills the selected building's centroid from `"UBC - All Properties List with GPS Coordinates"` so a coordinate is still recorded.
- **Provenance** — `QR_codes.capture_coord_source` records `device`, `building`, or `''`. A stored `device` fix is never downgraded to a `building` centroid on a later re-submit.
- Coordinates require a secure context (HTTPS in production, or `localhost`); they are best-effort and never block a save. See `rules/asset_capture_app.rules.md` and `special_processes/04_database_topography.md`.

## Notes & Installation Date (added 2026-07-06)

The pre-submit screen offers two **optional** capture-detail fields, both best-effort (never block a save):

- **Notes** — a multi-line textarea (`maxlength=200`, live character counter). Trimmed and clamped to 200 chars server-side in `/submit` and again in `upsert_qr_codes()`.
- **Installation Date** — a native date picker capped at today. The picker itself is not submitted: the value is copied into a hidden `installation_date` input only when the user taps the ✓ confirm button (prevents iOS from silently saving an auto-filled "today"); ✕ clears it. `/submit` validates with `normalize_iso_date()` (strict `YYYY-MM-DD`); invalid values are logged and dropped.
- Persisted to `QR_codes.capture_notes` / `QR_codes.installation_date` (migration `scripts/migrations/2026-07-06_qr_codes_capture_notes_install_date.sql`) and to the `_et.json` payload. Latest non-empty capture submission wins; an empty capture resubmission never erases a stored value. Review editors can subsequently update or clear Installation Date in ME/BF/EL. It remains outside extraction JSON, while SDI reads it directly from `QR_codes`.

## Outputs

- normalized image set in `Capture_photos_upload/`
- PostgreSQL `qr_code_db` rows in `QR_codes` and `QR_code_assets`
- elapsed-time JSON (`_et.json`) in `Output_jason_api/`
- per-capture GPS in `QR_codes.capture_latitude` / `capture_longitude` (+ `capture_coord_source` provenance), when available
- optional capture details in `QR_codes.capture_notes` / `installation_date`, when provided
- a complete extraction input set for ME, BF, or EL

## Guardrails

- reject placeholder QR IDs such as `None`, `nan`, or blank strings
- keep QR identity unique after normalization
- do not partially rename files or DB rows outside the atomic rename workflow
- keep `QR_codes` and `QR_code_assets` aligned on the same QR and building context
- `QR_code_assets` rows must include `user` and `date_hour` audit columns

## Verification

- confirm images exist for the intended QR and sequence numbers
- if an Extra Photo was captured, confirm the corresponding `-4` (ME) or `-3` (BF/EL) file is present and is excluded from any AI/extraction logging
- confirm `QR_codes` has the expected building, type, location, and `sdi` default
- confirm `QR_code_assets` has the expected `code_assets` rows, process assignment, `user`, and `date_hour`
- confirm elapsed-time JSON exists with correct `qr_code`, `building_number`, `asset_type`, `elapsetime`, `capture_notes`, and `installation_date` (the last two are empty strings when the tech left them blank)
- if a Notes/Installation Date value was entered, confirm it landed in `QR_codes.capture_notes` / `installation_date`, and that a later blank resubmission does not erase it
- confirm a freshly captured portrait photo is stored portrait-side-up on disk (open with any image viewer, or check `exiftool` reports `Image Size: W×H` with `H > W` and no `Orientation` tag)
