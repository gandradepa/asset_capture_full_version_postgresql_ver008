# Capture App Rules

Current documentation refresh: 2026-08-11.

## Purpose

The capture app owns QR intake, image upload, initial metadata capture, and parameter-update entry points.

## Backend Rules

- Require `@login_required` on non-auth routes.
- Use `_open_db()` or the app's DB resolver rather than hardcoded paths.
- Use parameterized SQL only.
- Validate missing input before building any QR or asset payload.
- Never convert missing values into placeholder strings such as `None`, `nan`, or `null`.

## QR and Temporary-Code Rules

- Permanent QR codes and temporary QR codes are both used in the platform.
- Temporary codes are retrieved through `/api/get-temp-code`.
- Temporary QR replacement is allowed later in the review stage, but capture must still store files and DB rows under the temporary code consistently.

## Disposed QR Rules (2026-08-11)

- A QR withdrawn by the Dashboard's **Disposed** tool cannot be captured again. `qr_is_disposed(conn, qr)` (`_has_table`-guarded, so it is inert before the `disposed_assets` migration) is the single check.
- `GET /api/check-qr` returns `"disposed": true` plus a `disposed_message` for such a QR. `"exists"` keeps its existing meaning — a disposed QR still exists — so the flag is additive and the capture form can warn before the operator photographs an asset the save will reject.
- `/submit` refuses a disposed QR with a flash and a redirect to start, placed **after** `_capture_validation_errors` but **before** any file is written or the overwrite cleanup runs, so a rejected submit leaves no photos and no DB rows behind. A failure of the check itself is logged and ignored: it must never block ordinary captures.
- Restoring the asset from the Dashboard re-enables capture; there is no capture-side override.

## File Rules

- Image filenames follow:
  `<QR> <Building> <Type> - <Seq>.<ext>`
- Sequence ranges per asset type:
  - ME: `-0` Asset Plate, `-1` UBC Tag, `-2` Main Asset Photo, `-3` Technical Safety BC, `-4` Extra Photo (optional)
  - BF: `-0` Asset Plate, `-1` Asset Plate (additional), `-2` Main Photo, `-3` Extra Photo (optional)
  - EL: `-0` Asset Plate (optional), `-1` UBC Asset Tag, `-2` Full Interior Panel, `-3` Extra Photo (optional)
- Elapsed-time JSON and other capture artifacts must keep QR, type, and building aligned with the captured image group.
- File and DB updates that change QR or building must be treated as one logical rename operation.

## Extra Photo Slot Rules

- The Extra Photo tile is rendered in `capture.html` with a `data-optional="true"` attribute on its `<input type="file">`.
- The completion-state JS (`updateCompletionState()`) filters out optional inputs, so the green "all required captured" toast fires once the required tiles are filled — submit is allowed without the Extra Photo.
- The submit loop iterates seqs `0..4` for ME and `0..3` for BF/EL; missing files in any slot are skipped by the existing `continue` guard, so the loop is safe regardless of which tiles the user filled.

## Image Save Rules

- `save_image_file()` in `app.py` re-encodes JPEGs through Pillow and applies `ImageOps.exif_transpose()` before writing.
- This physically rotates phone photos that arrive as landscape pixels with an EXIF Orientation tag (e.g., Orientation = 6) into upright portrait, then drops the orientation tag.
- The transpose is a no-op for images with no EXIF Orientation, with Orientation = 1, or for formats without EXIF (PNG, GIF). The existing raw-bytes fallback at the bottom of `save_image_file()` still catches corrupt-image cases.
- Historical files captured before 2026-05-25 were not backfilled; only new uploads benefit from the rotation.

## Parameter-Update Rules

- Any update that changes QR, building, asset type, or file naming context must update:
  image filenames, JSON filenames, DB rows, and processed logs.
- Building/location-only changes keep the same discipline and may rename/update the matching active review JSON.
- Asset-type changes are discipline migrations: rename the photos and `QR_code_assets`, retire active same-QR review JSON files to `.bak_*_param_update`, clear processed-log entries, reset `QR_codes.ai_status` to `0`, and retire stale curated rows so the chained AI+DB sync can rebuild the correct discipline table.
- Do not carry a ME/BF/EL JSON payload across disciplines by only changing its filename; the schemas and completeness rules are discipline-specific.
- Roll back on failure. Do not leave partial renames behind.

## Database Rules

- `QR_codes` is the QR-level source for capture location, approval, AI status, and SDI-exclusion state.
- `QR_code_assets` tracks process placement and capture-related workflow state.
- Mobile capture exposes only the new-asset workflow. The server ignores submitted `capture_process` values and always writes `QR_code_assets.Col_process = 0`; historical values `1` and `2` remain valid for existing records and downstream reporting.
- Existing-QR detection, parameter comparison, and overwrite confirmation remain active even though the workflow selector is hidden.
- Every mobile submission must include a newly selected `image_0` or `image_1`. Either one satisfies the minimum; existing stored photos and uploads in `image_2` or later do not.
- Enforce the primary-photo rule in the browser and again at the start of `/submit`, before overwrite cleanup can delete existing files or database rows.
- Capture must not create malformed QR rows such as placeholder IDs.

## Cross-Platform Rules

- The app must resolve paths for both Windows development and Ubuntu production.
- Do not assume `/home/developer/...` is always available.

## Validation Checklist

- A new capture writes the expected image files.
- Submission is blocked when both `image_0` and `image_1` are missing, including overwrite requests.
- The QR appears in `QR_codes`.
- The asset appears in `QR_code_assets`.
- Missing-field submission does not create placeholder DB rows.
- Parameter updates do not orphan files or DB rows.

## Elapsed Time and User Tracking Rules

- After capture submission, an elapsed-time JSON (`_et.json`) is written to `Output_jason_api/`.
- The JSON includes `qr_code`, `building_number`, `asset_type`, `elapsetime`, `capture_notes`, and `installation_date` (the last two added 2026-07-06; always present, empty string when unset — the file reflects the latest submission).
- `QR_code_assets` rows include `user` (authenticated username) and `date_hour` (ISO 8601 timestamp).
- Missing `user` and `date_hour` columns are auto-created via `ALTER TABLE` if absent.

## GPS / Location Capture Rules

- The Capture App records where each asset was captured into `QR_codes`: `capture_latitude`, `capture_longitude`, merged display field `"GPS Coordinates (lat,long)"`, and provenance `capture_coord_source` (`device` | `building` | `''`). GPS columns are created by owner-run PostgreSQL migrations (`scripts/migrations/2026-06-16_qr_codes_capture_coords.sql`, `2026-06-16_qr_codes_capture_coord_source.sql`, and `2026-06-23_qr_codes_gps_coordinates.sql`) — never via DDL in the request path.
- Coordinates are **best-effort**: a denied or unavailable GPS must never block a save.
- The browser Geolocation API requires a **secure context** (HTTPS in production, or `localhost`), and the OS/browser permission prompt cannot be suppressed by app code. `start.html` primes the grant once on page load, only when the permission state is undecided.
- Precise device GPS (`source = device`) wins. If absent, `/submit` fills the building centroid from `"UBC - All Properties List with GPS Coordinates"` (`source = building`).
- **Never downgrade** a stored `device` fix to a `building` centroid on a re-submit. The precedence lives in `resolve_capture_coordinates()` (`API/validators_shared.py`); `upsert_qr_codes()` writes coordinates only as a complete pair (`coords_ok` gate).
- `capture_latitude` / `capture_longitude` / `"GPS Coordinates (lat,long)"` are discipline-agnostic capture metadata for review display. SDI Planon export may read `"GPS Coordinates (lat,long)"` directly from `QR_codes` for the template GPS column; it is still not a curated discipline field in `sdi_dataset` or `sdi_dataset_EL`.

## Notes & Installation Date Capture Rules (added 2026-07-06)

- The pre-submit capture screen offers two **optional** fields: **Notes** (multi-line textarea, `maxlength=200` with live counter) and **Installation Date** (native date picker capped at today). Both are best-effort metadata — an empty field must never block a save.
- Notes are trimmed and clamped to **200 characters** server-side in `/submit` and again in `upsert_qr_codes()` (client `maxlength` is not trusted).
- The Installation Date picker carries **no `name`**: the submitted value lives in a hidden `installation_date` input that is populated only when the user taps the ✓ confirm button. This prevents iOS Safari from silently saving an auto-filled "today" the moment the calendar opens. The ✕ button clears both picker and hidden value.
- `/submit` validates the date with `normalize_iso_date()` (strict `YYYY-MM-DD` calendar date); an invalid value is logged and dropped — never a 400.
- Persistence: `QR_codes.capture_notes` and `QR_codes.installation_date` (owner-run migration `scripts/migrations/2026-07-06_qr_codes_capture_notes_install_date.sql`; `upsert_qr_codes()` feature-detects the columns and no-ops if absent). The latest non-empty submission wins; an **empty resubmission never erases** a stored value (Invariant #6) — clearing is a review-layer job.
- Both keys are also written to the `_et.json` payload (see Elapsed Time rules). `capture_notes` is displayed read-only in the ME review dashboard/detail page, but neither key is propagated to the AI-extraction JSON, BF/EL review dashboards, or `sdi_dataset` / `sdi_dataset_EL`.

## Parameter Update Service Rules

- `utils/parameter_update_service.py` provides atomic parameter change operations.
- The service exports: `execute_parameter_update`, `get_current_params`, `get_current_asset_type`, `detect_parameter_changes`.
- The `/api/update-parameters` route delegates to this service.
- All parameter changes must update image filenames, JSON filenames, DB rows, and processed logs atomically.
- For asset-type changes, active review JSON is retired instead of converted across discipline schemas; rerun `API/run_ai_and_sync.sh <discipline> <qr>` to regenerate the new discipline payload.
- Roll back on failure â€” do not leave partial renames.
- The `/api/check-qr` route returns current parameters for comparison when the QR code already exists.

## Design System & Copy Voice Rules

- The Capture App follows the warm design system defined in [DESIGN.md](../design/DESIGN.md) and the product personality defined in [PRODUCT.md](../design/PRODUCT.md).
- All color variables are defined in OKLCH inside the CSS root parameters. Avoid clinical cool grays, pure whites (`#fff`), or pure blacks (`#000`).
- Text elements must adhere to the warm, conversational second-person voice:
  - Use "Let's find your asset" instead of "Asset Setup"
  - Use "Which building?" instead of "Select Building"
  - Use "Where in the building?" instead of "Location"
  - Use "What kind of asset?" instead of "Asset Type"
  - Use "Scan the QR code" instead of "TAP TO SCAN QR CODE"
  - Use "or type the code" instead of "or enter manually"
  - Use "Use this code" instead of "Verify"
  - Use "Continue" / "Save and continue" instead of "Submit"
  - Use "Saved. Nice work." instead of "Upload Successful"
- Interactive elements must maintain minimum comfortable touch target sizes (at least 48px height for inputs/buttons, 52px for primary CTAs) for easy operation by field workers.
- Avoid using exclamation marks in user-facing toasts or instructions to maintain a calm, professional tone.
