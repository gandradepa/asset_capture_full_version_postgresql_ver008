# Workflow: Run Extraction for ME, EL, and BF

Current documentation refresh: 2026-07-08.

## Purpose

Process captured images into discipline-specific JSON payloads and sync summary state into the database.

## Inputs

- images in `Capture_photos_upload/`
- current extraction scripts in `API/`
- PostgreSQL `qr_code_db` via `db.py` / `DB_BACKEND=postgres`; legacy `asset_capture_app_dev/data/QR_codes.db` is rollback/reference only

## Main Steps

1. Run the extraction worker for the target discipline or the full extraction chain.
   - **Recommended**: Use `run_ai_and_sync.sh`, which chains AI extraction to DB sync automatically.
   - The separate manual `update_db` task has been removed from the Dashboard launcher.
2. Discover candidate assets from the image filenames.
3. Read the sequence-specific photos for each QR.
4. Apply OCR and LLM extraction.
5. Normalize the response into the discipline schema using shared validators (`validators_shared.py`).
6. Apply completeness and confidence rules before writing the final JSON.
7. Write the payload to `Output_jason_api/<QR>_<TYPE>_<Building>.json`.
8. DB sync runs automatically (via chained script) to update `json_files`, `sdi_dataset`, and `sdi_dataset_EL` as appropriate.

## OpenAI Model Policy

- ME uses `gpt-5.4-mini`.
- BF uses `gpt-5.4-mini`.
- EL uses `gpt-5.4`.
- Each asset uses one model and one API attempt by default: model fallback is disabled, `MAX_LLM_ATTEMPTS_PER_ASSET=1`, and `MAX_LLM_ATTEMPTS_PER_MODEL=1`.
- Existing discipline-specific environment variables remain available as explicit overrides.
- SLD extraction is a separate EL Review subprocess and uses `gpt-5.4` with an empty fallback-model list by default.

## Discipline Rules

- ME completeness: `Manufacturer`, `Model`, `Serial Number`, `Year`, `UBC Tag`, plus `Technical Safety BC` only when seq `-3` exists.
- BF completeness: `Manufacturer`, `Model`, `Serial Number`, `Diameter`.
- EL completeness: `UBC Asset Tag`, `Ampere`, `Supply From`.
- EL confidence averages exclude `Volts`, `Location`, and `Branch Panel`.
- ME confidence includes `Technical Safety BC` only when seq `-3` exists.
- Optional **Extra Photo** sequences (ME `-4`, BF `-3`, EL `-3`) are excluded from each pipeline's `VALID_SUFFIXES` and never reach the LLM. The pipeline's `FILENAME_PATTERN` regex was widened (ME `[0-4]`, BF `[0-3]`, EL `[0-3]`) so discovery sees the file and logs it as `invalid_seq` rather than `name_mismatch`, but the file is not added to `info["images"]`.
- ME manufacturer names are canonicalized against `ME_MANUFACTURER_REGEX_RULES` first; multi-token makers absent from the table (no legal suffix, `&`, or hyphen) are silently wiped to blank by the guarded fallback — see `rules/asset_extraction_api.rules.md` ("ME Manufacturer Canonicalization") for the wipe signature and fix procedure. All-numeric ME model codes survive only for makers in `ME_NUMERIC_MODEL_MANUFACTURERS` (currently `Siemens`).

## Skip-If-Exists Guard and Reprocess Trigger

Current documentation refresh: 2026-06-25.

The extraction pipeline short-circuits with `STATUS_SKIPPED_EXISTS` when `Output_jason_api/<QR>_<TYPE>_<Building>.json` already exists on disk, regardless of `ai_status`. Setting `ai_status = 0` in the database alone is **not sufficient** to re-trigger extraction — the JSON file must be absent.

The review-app **AI Status reprocess feature** (toggling AI Status off in the ME / BF / EL dashboards) clears this guard by moving the JSON to a `.bak_<YYYYMMDDHHMMSSz>` backup before the next cron cycle runs. See `Markdowns_documentation/rules/review_apps.rules.md` ("AI Status Reprocess Workflow") for the full protection hierarchy and force-reprocess mechanics.

### `reset_me_asset.py` (ME only CLI helper)

For one-off manual resets outside the dashboard:

```bash
cd /home/developer/API
python reset_me_asset.py <qr_code>          # dry run — shows what would happen
python reset_me_asset.py <qr_code> --apply  # executes: moves JSON + sets ai_status=0
```

- Requires PostgreSQL backend (`DB_BACKEND=postgres`); refuses SQLite unless `--allow-sqlite`.
- Moves `Output_jason_api/<qr>_ME_*.json` to `*.bak_<UTCstamp>`.
- Sets `ai_status = '0'` via `db.get_connection()`.

## Outputs

- JSON payloads in `Output_jason_api/`
- synced rows in `json_files`
- updated AI status and curated dataset rows in PostgreSQL `qr_code_db`

## Verification

- confirm each JSON has the expected top-level keys and discipline fields
- confirm blank fields do not retain non-zero confidence
- confirm `Avg_ai_conf` and `completeness_score` follow the current discipline rules
- confirm database sync completes without duplicate or placeholder QR rows
