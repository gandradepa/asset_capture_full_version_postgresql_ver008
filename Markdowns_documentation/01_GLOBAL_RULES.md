# Global Rules and Conventions

> **🐘 Database backend: PostgreSQL (C4 cutover complete, 2026-06-08).** The platform now runs on PostgreSQL (`qr_code_db`, VM `127.0.0.1:5433`) via a backend-agnostic `db.py` layer, switched by `DB_BACKEND=postgres` in `/home/developer/db_backend.env`. The SQLite `QR_codes.db` referenced throughout is now the **frozen rollback** (flip the env back to `sqlite` + restart to revert). See `Markdowns_documentation/special_processes/04_database_topography.md`, `C4_CUTOVER_RUNBOOK.md`, and the `pg-cutover-complete` memory.

Current documentation refresh: 2026-07-25.

## 0. Standard Claude Code Skills

Three Claude Code plugins are project-standard (enabled in `.claude/settings.json`: `superpowers`, `context7`, and `frontend-design`, all from `claude-plugins-official`). Use them as follows when working in this repository:

- **Superpowers** — for any non-trivial change (schema migrations, cross-module work, new features, incident debugging), use its workflow skills (brainstorming, plan writing, systematic debugging) to structure the work before implementing.
- **Context7** — when code touches an external library (Flask, psycopg2, Pandas, openpyxl, Bootstrap, DataTables, OpenAI SDK, ...), fetch current documentation through the Context7 MCP tools (`resolve-library-id` → `get-library-docs`) instead of relying on model memory.
- **Frontend Design** — invoke the `frontend-design` skill for any user-facing UI work: templates, CSS/JS, dashboard panels, review-app screens.

## 1. State Ownership

- Images in `Capture_photos_upload/` are the raw capture source.
- JSON files in `Output_jason_api/` are the extraction and review payload source.
- Review apps may correct JSON and then sync curated values into SDI tables.
- Dashboard tables are views, not the source of truth.
- SDI Process packages from curated DB rows, not directly from raw extraction output.

## 2. Shared Database Discipline

- The main operational database is PostgreSQL `qr_code_db` (VM `127.0.0.1:5433`, via `db.py` / `DB_BACKEND=postgres`); legacy SQLite `asset_capture_app_dev/data/QR_codes.db` is rollback/reference only and is no longer used by live QR-code workflows.
- The local development / DBeaver database is PostgreSQL `qr_code_db` on `127.0.0.1:5432`, normally accessed as user `postgres`; keep it refreshed from the VM backup workflow before local data verification.
- Use parameterized SQL only.
- Double-quote every mixed-case SQL identifier (`"QR_code_ID"`, `"Building Code"`, `"sdi_dataset_EL"`, ...). PostgreSQL folds unquoted identifiers to lowercase; SQLite tolerated the unquoted form, so any unquoted mixed-case reference is a latent post-cutover breakage class (see `INCIDENT_2026-06-09_pg_unquoted_identifier_duplicate_scan.md`).
- Never let an `except Exception` handler swallow a DB error into a defaulted API response without logging it (`logger.error(..., exc_info=True)`). That pattern made the 2026-06-09 incident invisible: the endpoint kept returning a plausible `{"exists": false}` payload while failing on every call.
- The breakage class is broader than identifier case — other SQLite-isms fail identically on PostgreSQL: `ROWID` (use `"ctid" if db.is_postgres() else "rowid"`, the SDI_process convention), `INSTR()` (use a `split_part`/`strpos` branch), `COLLATE NOCASE` (use `GROUP BY` + `ORDER BY LOWER(...)`; PG rejects `DISTINCT` + `ORDER BY f(x)`), `"..."`/`""` double-quoted string literals (identifiers on PG — use `'...'`), `[bracket]` quoting, and loose typing such as `COALESCE(text_col, 0)` (`DatatypeMismatch` — wrap the column in `CAST(... AS TEXT)`).
- Platform-wide audit completed and deployed 2026-06-09 (AST scan of every service + `EXPLAIN` verification of all static SQL against the PG replica): fixed Dashboard `ROWID` fallbacks and unquoted `b.Name`, approval-chart `instr()`/`""`/`COLLATE NOCASE`, and the `COALESCE("Avg_ai_conf", 0)` type error in all three review apps' placeholder-wipe guards (`_sdi_row_has_data*`; regression test updated in sync). The remaining swallow-and-default logging worklist (62 handlers) is inventoried in the incident doc's Platform-Wide Audit section.
- Path resolution must work on both Windows development and Ubuntu production.
- Schema drift is expected; code should tolerate missing columns and add them when designed to do so.
- **No DDL in request paths.** Never run `CREATE INDEX`/`ALTER TABLE`/`CREATE TABLE` inside a web request handler. PostgreSQL requires **table ownership** for these (even `CREATE INDEX IF NOT EXISTS` checks ownership before the existence short-circuit), and the apps connect as `assetcap_app` (DML grants only; tables owned by `developer`). A failed DDL statement also aborts the entire transaction, so it silently rolls back the legitimate INSERTs alongside it. This caused the 2026-06-11 incident: a per-`/submit` `CREATE UNIQUE INDEX` made every mobile capture lose its registration for 3 days. Put schema changes in owner-run migrations under `scripts/migrations/` (apply as the `developer` role via `psql -h /tmp -p 5433`). See `INCIDENT_2026-06-11_pg_capture_registration_ddl.md`.
- **Identity columns need a `setval` after explicit-id inserts.** `audit_trail.id`, `QR_code_assets."ID"`, and similar are `GENERATED BY DEFAULT AS IDENTITY`. `information_schema.column_default` is **empty** for them (check `is_identity='YES'`, not the default), so a backfill that supplies explicit ids is accepted but does **not** advance the sequence — the app's next auto-id then collides (`duplicate key … violates pk_*`). After any explicit-id insert into an identity table: `SELECT setval(pg_get_serial_sequence('"Table"','id'), (SELECT MAX("id") FROM "Table"))`.

## 3. Completeness Rules Are Discipline-Specific

- Never assume one completeness formula fits all disciplines.
- ME completeness:
  `Manufacturer`, `Model`, `Serial Number`, `Year`, `UBC Tag`, plus `Technical Safety BC` only when seq `-3` exists.
- BF completeness:
  `Manufacturer`, `Model`, `Serial Number`, `Diameter`.
- EL completeness for extraction and review payloads:
  `UBC Asset Tag`, `Ampere`, `Supply From`.
- EL curated DB rows in `sdi_dataset_EL` store canonical amperage in `Amperage Rating`.
  `Ampere` remains a compatibility mirror during the transition.

## 4. Confidence Rules Are Discipline-Specific

- `Avg_ai_conf` must be derived from discipline-aware field sets.
- Blank final fields must not retain non-zero confidence.
- EL confidence averages exclude `Volts`, `Location`, and `Branch Panel`.
- ME should only count `Technical Safety BC` in completeness and AI confidence when seq `-3` exists.

## 5. ME Sequence Ownership

- Seq `-0` owns `Manufacturer`, `Model`, `Serial Number`, and `Year`.
- Seq `-1` owns `UBC Tag`.
- Seq `-3` owns `Technical Safety BC`.
- Seq `-4` is the optional **Extra Photo** slot and owns no fields — it is captured for reviewer context only and never feeds completeness, AI confidence, AI extraction, or the "Missed Photo" count.
- If the owning sequence is absent or not evidenced, leave the field blank instead of borrowing from another image.

## 5a. Extra Photo Slot (All Disciplines)

- Each discipline supports one optional **Extra Photo** slot: ME `-4`, BF `-3`, EL `-3`.
- The slot is captured and displayed alongside the required photos but excluded from:
  - completeness scoring (`02_completeness_guard.md`)
  - `Avg_ai_conf` averaging
  - AI extraction — its sequence is intentionally absent from each pipeline's `VALID_SUFFIXES`
  - the "Missed Photo" dashboard count
- In the review dashboards' Photo column it renders as a `+1` chip next to the existing required-photo ratio (e.g., `3/3 +1`). The chip is purely informational; its absence is never an error state.

## 6. Approval and SDI Integrity

- Toggling `Approved` must not erase dictionary-derived fields.
- Review approval must sync into `sdi_dataset` for ME/BF and `sdi_dataset_EL` for EL.
- If a QR is present in `sdi_print_out` or `sdi_print_out_arch`, it is in the SDI process and must remain approved in review source state: JSON `structured_data.Approved = "True"` and source-table `Approved = "1"`.
- Do not add an approval column to `sdi_print_out` or `sdi_print_out_arch`; package tables hold package state and `id_print_out`, while approval remains in review JSON and the source SDI dataset tables.
- SDI package route moves must preserve package integrity: Retrieve and Exclude preserve source/JSON approval, Archive requires `print_out = 1` for every selected row, and package actions write audit-trail rows.
- SDI package database guardrails must be installed with `scripts/migrate_sdi_package_db_guardrails.py`: unique normalized package QR indexes plus triggers block duplicate/overlapping package rows, blank package keys, unapproved packaged source state, unexported archive inserts, and deletion/unapproval of packaged QR rows. Full foreign-key table rebuild is deferred.
- Manual Entry is an SDI exclusion state, not just a visual tab assignment.
- `QR_code_assets.Col_process = 2`, JSON `ExcludeSDI`, and `QR_codes.sdi = 1` should remain aligned.
- SDI Process Unpackaged Assets is fed only by review New Assets (`QR_code_assets.Col_process = 0`) for ME, BF, and EL. Update Existing (`1`) and Manual Entry (`2`) stop in review and must not be packaged, even when a curated row remains approved.

## 7. Atomic Rename Integrity

- QR and parameter updates must be treated as one logical transaction across:
  JSON filename, image filenames, processed logs, and DB rows.
- On failure, roll back rather than leaving the platform in a split state.

## 8. Human Review Overrides

- Completeness guards apply to extraction workers, not to human review saves.
- Review apps are allowed to preserve human edits even when the result is less complete than a prior AI guess.
- When a human saves, mark the JSON as modified and resync the DB representation.

## 9. Dashboard and Analytics Rules

- Analytics should recompute from current JSON or current curated state when possible, not trust stale historical percentages.
- Operational Performance Analysis uses a merged `Data Quality Comparison` chart and supports `All` and `Open Process` scope.
- Review dashboards use server-side and client-side confidence filtering; both layers must stay consistent.

## 10. Documentation Rules

- Root docs are canonical for platform-wide behavior.
- Service-local `.agent` docs are canonical for service-specific behavior.
- `.agent_app/` is a synchronized mirror, not an independent source.
- When behavior changes in code, update the matching root or service doc in the same workstream.

## 11. FLS Asset Management Rules

- FLS assets are tracked in the `new_device` table with Planon checklist columns.
- FLS `new_device."Attribute Set"` is normalized to the stored Attribute code `FireAlarmDevice`; the label in `"Attribute"` is `Electrical/FLS - Fire Alarm Device`.
- Dashboard CRUD (add, delete, bulk update) is the primary management surface.
- FLS rows with a populated `Planon Code` remain editable, but delete actions and bulk row selection are blocked.
- Planon checklist columns: `Request Open`, `Request Date`, `Elapsed Time`, `Complete`, `Ticket Number`.
- Schema migration (`_ensure_new_device_columns()`) runs at Dashboard startup to add missing columns.
- FLS Control Panel Code/Description is derived at display time from `"UBC - Asset Data Master Info"` by selected building `Property code`; it is not stored in `new_device`.
- When a building has multiple matching Control Panel rows, Dashboard displays the lowest `Code` row and flags that multiple matches exist.

## 12. Dashboard Dictionary Editing Rules

- Dictionary editing from the Dashboard uses the same AST-safe read/write approach as the standalone dictionary app.
- Use `ast.parse()` and `ast.literal_eval()` for reading; write with `json.dumps()` for sorted, deterministic output.
- Never use `eval()` or execute dictionary file contents.

## 13. User and Timestamp Tracking

- Capture events record the authenticated `user` and `date_hour` in `QR_code_assets`.
- Elapsed-time JSON artifacts (`_et.json`) are written to `Output_jason_api/` after capture submission; since 2026-07-06 the payload also carries the optional capture fields `capture_notes` and `installation_date` (empty strings when unset).

## 14. Chained AI+DB Sync

- AI extraction launches now use `run_ai_and_sync.sh`, which chains AI processing to DB sync automatically.
- The separate `update_db` manual task has been removed from the Dashboard launcher.

## 15. Planon Export Rules

- SDI Process exports to Planon using UBC tag parsing (`parse_ubc_tag_info`) and year formatting (`format_year_to_date`).
- Export validation logs are available for review through the SDI Process UI.
- Package archive management supports move, retrieve, and exclude operations.
