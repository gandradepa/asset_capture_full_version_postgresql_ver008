# Incident - 2026-06-09 Duplicate-Scan Warning Suppressed by Unquoted Identifier (PostgreSQL)

## Summary

One day after the C4 PostgreSQL cutover (2026-06-08), the mobile Capture App stopped warning when an already-recorded QR code was scanned. The "⚠️ This Asset already exists. Would you like to continue and replace existing data?" dialog (and the parameter-change dialog) never appeared; every scanned QR was treated as new. Reported by field test on 2026-06-09.

## Impact

- Duplicate-capture protection was effectively disabled: users could re-capture over existing records with no prompt.
- The `/api/update-parameters` flow ("Update Parameters" after re-scanning a QR from a different building/location) was broken by the same root cause, because both code paths call `get_current_params()`.
- The failure was completely silent: the endpoint returned HTTP 500 with `{"exists": false, ...}`, the frontend treats any `exists: false` as "new QR", and the exception handler logged nothing.
- Latent before the flip: the bug existed in the code for as long as the function has, but SQLite's case-insensitive identifier handling masked it. The 2026-06-08 smoke missed it because the failure presents as a plausible response shape behind a login-gated endpoint.

## Root Cause

`asset_capture_app_dev/utils/parameter_update_service.py` `get_current_params()` seeded its SELECT column list with an unquoted identifier:

```python
select_cols = ["QR_code_ID"]   # every other identifier in the query was quoted
```

PostgreSQL folds unquoted identifiers to lowercase, so the query referenced `qr_code_id`, which does not exist (the migrated schema preserves exact case: `"QR_code_ID"`). psycopg2 raised `UndefinedColumn`.

Failure chain in `GET /api/check-qr` (`asset_capture_app_dev/app.py`, `api_check_qr()`):

1. `qr_exists()` correctly returned `True` (its own SQL was fully quoted).
2. `get_current_params()` raised `psycopg2.errors.UndefinedColumn`.
3. The route's `except Exception` returned `{"exists": False, "error": str(e)}` with HTTP 500 and **no log line**.
4. The frontend `checkExists()` parsed the JSON body regardless of status, saw `exists: false`, and skipped both warning dialogs.

## Fix

Two changes, deployed 2026-06-09:

- `asset_capture_app_dev/utils/parameter_update_service.py` — `get_current_params()` now seeds the column list with `_quote_ident("QR_code_ID")` (plus a comment documenting the PostgreSQL folding constraint). This repairs both `/api/check-qr` and the `/api/update-parameters` lookup.
- `asset_capture_app_dev/app.py` — `api_check_qr()`'s exception handler now emits `app.logger.error(f"QR existence check failed for '{qr}': ...", exc_info=True)` before responding, so this failure class can no longer be invisible.

Local repository paths match the VM byte-for-byte:

- `asset_capture_app_dev/app.py` SHA256 `d4cb8c80f6a7fd1f3f0d761172dc04784f9c2119ca1bdb13b0008302e6eb2dbb`
- `asset_capture_app_dev/utils/parameter_update_service.py` SHA256 `466524acbdfb7d45fa6a5b980c1ccbce3160f2147c7a8c9eed3d27845dd9cd77`

## Validation

Reproduced and verified against the local PostgreSQL replica (`qr_code_db`, `127.0.0.1:5432`, refreshed from the VM) using the app's own `db.py` layer and real data (QR `0000183702`):

- Pre-fix: `qr_exists()` → `True`, then `get_current_params()` → `UndefinedColumn` (exact production failure).
- Post-fix: full `api_check_qr` logic returns `{"exists": true, "current_building": "314-1", "current_location": "B125 Storage Floor: B1", "current_asset_type": "ME"}`.
- Non-existing QR still returns `exists: false` cleanly (new-capture path intact).
- SQLite rollback backend re-verified with the same code: identical results (double-quoted identifiers are valid SQLite), so the frozen-rollback guarantee is preserved.

## VM Deployment

- Drift check before overwrite: VM copies of both files hashed byte-identical to the local pre-fix `HEAD` versions.
- Backups created on the VM: `app.py.bak_20260609_142605`, `parameter_update_service.py.bak_20260609_142605` (same directories as the originals).
- Local pre-deploy snapshot of the VM originals: `.deploy_backups/duplicate_scan_pg_fix_20260609_142605/`.
- Files copied via `scp`; remote SHA256 verified against the fixed local versions.
- `assetcap-app` Gunicorn master (PID 1063795) reloaded with `HUP` because passworded `sudo systemctl restart` was unavailable over non-interactive SSH. All 3 workers re-forked at 14:27:15 PT; local VM HTTP probes on port 8000 (`/` and `/api/check-qr`) returned the expected `302` login redirects.

## Follow-up

- No dedicated regression test was added in this workstream; verification was live-DB reproduction on both backends. A platform-wide audit for further unquoted mixed-case identifiers (review apps, Dashboard, SDI Process, API, audit module) is queued as a background task — the C4 runbook already classifies "unquoted mixed-case identifiers" as a systemic PG-ism found during the smoke, and this incident confirms instances survived it. **Completed 2026-06-09 — see "Platform-Wide Audit" below.**
- `01_GLOBAL_RULES.md` Section 2 now carries the identifier-quoting rule and the swallow-and-default logging rule.

## Platform-Wide Audit (completed 2026-06-09)

All services touching the operational DB were swept with an AST-based scanner (every string literal incl. f-string/concat-built SQL, checked against the exact-case identifier list from `information_schema` on the local replica) plus `EXPLAIN`-verification of every complete static SQL string against PostgreSQL. 100 static SQL strings EXPLAINed, 334 dynamic SQL builders traced to their interpolation sources, ~3.5k raw identifier occurrences triaged.

**Live bugs found, fixed, and verified against the replica (and SQLite for rollback parity):**

| Site | PG-ism | Silent failure mode |
|---|---|---|
| `Dashboard/Asset_portal_dashboard.py` `_lookup_qr_meta` (3 queries) | `ORDER BY ROWID DESC` — no ROWID on PG | building/asset-type fallback lookups dead (`except: pass`); fixed with `ctid`/`ROWID` branch (SDI_process convention) |
| `Dashboard/Asset_portal_dashboard.py` activity log | `TRIM(b.Name)` unquoted | Activity Hours 500s whenever a building filter is applied; fixed `b."Name"` |
| `Dashboard/charts/approval.py` `_qrs_for_user` | SQLite `instr()` + `" "` string-literal-as-identifier | user-filtered approval charts silently empty; fixed with `split_part`/`strpos` PG branch |
| `Dashboard/charts/approval.py` `users_with_data` | `!= ""` (zero-length identifier on PG) + `COLLATE NOCASE` | user dropdown silently collapses to "All"; fixed with `<> ''` + `GROUP BY`/`ORDER BY LOWER()` |
| `_sdi_row_has_data*` in **all three** review apps (BF static, ME/EL f-string twins) | `COALESCE("Avg_ai_conf", 0) > 0` — text vs integer `DatatypeMismatch` (column is TEXT on PG) | placeholder-wipe guard always returned False → re-sync could overwrite real AI-captured rows with blanks; fixed with `TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), '')) NOT IN ('', '0', '0.0')` (valid on both backends, fails toward protecting data). `test/test_placeholder_sync_guard.py` predicates updated in sync; 13/13 pass. |

**Audited clean:** `audit/` module (audit_trail is all-lowercase), live `API/API_interface_{ME,BF,EL}_ver00.py` (all interpolations quoted; only the dead `_bkp` copies carry unquoted `Approved`), `API/updating_process_database.py`, `SDI_process/app.py` (already PG-adapted: `ctid` row addressing, `CAST AS TEXT`, quoted column generators), EL `Asset_Group` constants, capture-app `temp_code` queries (lowercase `status` is the real column).

**Out of scope / dead code (not fixed):** `asset_capture_app_dev/utils/{building_lookup,file_handler}.py` (legacy **Access-DB** utilities via pyodbc, never imported), `asset_capture_app_dev/data/debug_asset.py` (standalone SQLite diagnostic), `Dashboard/Asset_portal_dashboard_bkp.py` + `Dashboard/charts/operational_cost_resultbkp.py` + `API/API_interface_*_bkp.py` (dead backup copies, many PG-broken statements — do not resurrect without fixing), `gps_service.py` ×3 (separate GPS SQLite file via raw `sqlite3`, bypasses `db.py` by design), `sld_blueprint.ensure_sld_schema` ID_check ALTER (dormant on PG — guarded by column existence; SQLite-only `VIRTUAL` syntax inside the guard).

**Update (2026-06-09, dead-code cleanup):** the never-imported dead files above were deleted from the repo to remove the post-cutover resurrection risk: `utils/building_lookup.py`, `utils/file_handler.py`, `Dashboard/Asset_portal_dashboard_bkp.py`, `Dashboard/charts/operational_cost_resultbkp.py`, `API/API_interface_BF_ver00_bkp.py`, `API/API_interface_EL_ver00_bkp.py` (no ME `_bkp` copy existed). Each was re-verified unreferenced first (static imports, dynamic `importlib`/`spec_from_file_location` loaders, package `__init__`s, shell scripts, `__pycache__`); all six were git-tracked, so they remain recoverable from history. `data/debug_asset.py`, `gps_service.py`, and the SLD schema guard remain untouched.

**Update (2026-06-10, VM-side cleanup):** the same six files were retired on the production VM (`/home/developer`) — renamed in place to `*.bak_20260610_083839` (the VM tree has no git history, so rename-not-delete keeps them recoverable while taking them out of the Python import path) after VM-side re-verification found zero references (code/script grep, `crontab -l`, `/etc/cron.d`, systemd units, no stale `__pycache__`); all six gunicorn ports (8000–8005) answered `302` before and after.

**Swallow-and-default inventory:** 62 `except Exception` blocks in live files wrap DB work and return defaulted payloads without `logger.error` (20 with no output at all — notably `approval.py:97,115`, capture `app.py:390,1189`, `parameter_update_service.py:893`, ME reviewer ×6, EL dashboard ×4, BF reviewer ×4, `sld_blueprint.py:1321,1743`). Candidates for the `logger.error(..., exc_info=True)` pattern this incident established. Full machine-readable scan: `scan_report.json` (session artifact).

### VM Deployment (audit fixes, 2026-06-09 ~15:35 PT)

- Drift check before overwrite: all six VM files hashed byte-identical to local pre-fix `HEAD` (the `Asset_dashboard_EL.py` hash difference was CRLF-vs-LF line endings only — confirmed content-identical after normalization).
- Backups: VM-side `*.bak_20260609_153251` next to each original; local snapshot of the VM originals in `.deploy_backups/pg_audit_fixes_20260609_153251/`.
- Deployed via `scp`, remote SHA256 verified against the fixed local versions, then `python3 -m py_compile` passed on the VM for all six files:
  `Dashboard/Asset_portal_dashboard.py`, `Dashboard/charts/approval.py`, `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py`, `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py`, `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`, `test/test_placeholder_sync_guard.py`.
- Gunicorn masters HUP'd (PIDs 606230 Dashboard:8002, 2833410 ME:8001, 3974042 BF:8004, 2351998 EL:8005); all 3 workers per service re-forked and remained stable; `curl` probes on all four ports returned the expected `302` login redirects. Capture app (8000) and SDI (8003) were not touched — no changed files in those services.
- Rollback: restore the `*.bak_20260609_153251` files and HUP the same four masters.
- In-app spot checks worth doing: Dashboard → Approval chart with a user filter selected (user dropdown should list real users again), Activity Hours filtered by building, and any review-app placeholder re-sync against a QR with existing AI data (row must NOT be blanked).

## Operational Note

Final end-to-end confirmation is a mobile re-scan of an already-recorded QR: the overwrite warning (or the parameter-change dialog when building/location differ) should appear. Rollback if needed: restore the two `.bak_20260609_142605` files on the VM and `HUP` the `assetcap-app` Gunicorn master again.
