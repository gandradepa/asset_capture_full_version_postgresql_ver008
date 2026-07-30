# Incident - 2026-06-11 Silent Loss of Mobile Capture Registrations (PostgreSQL DDL Privilege Rollback)

## Summary

Every mobile `POST /submit` between the 2026-06-08 PostgreSQL cutover and the 2026-06-11 fix
saved its photos and `{qr}_et.json` to disk but **silently lost all database writes** — no
`QR_codes` row, no `QR_code_assets` rows, no `audit_trail` rows. The field user saw the normal
success page. Root cause: the `/submit` handler ran schema DDL (`CREATE UNIQUE INDEX`) inside every
request transaction; on PostgreSQL the app role lacks table ownership, so the statement raised
`insufficient_privilege` and aborted the whole transaction, and the `except` block reduced the
failure to an unlogged `flash()` toast. Same failure **class** as the 2026-06-09 incident
(post-cutover PG behavior + swallow-and-default handler), different bug.

## Impact

- 24 QR codes captured 2026-06-09/10 were registered nowhere in the database (the
  `0000187918`–`0000187987` batch, `0000184666`/`67`/`68`, `0000084657`).
- Review apps showed these assets (via JSON auto-register) with blank **Capture Date / Space /
  Captured by / Date / Hour** — every field sourced from the missing `QR_codes` / `QR_code_assets`
  rows.
- 100% of mobile capture registrations failed for ~3 days; the last successful `app_name='mobile'`
  audit row before the fix was 2026-06-03.
- `Location`/`Space` for the 24 was **unrecoverable** — it existed only in the rolled-back form
  POST (photos carry no EXIF/GPS; gunicorn kept no access log). Those assets require field
  re-verification of location.

## Root Cause

`asset_capture_app_dev/app.py` ran schema DDL **inside every** `/submit` request, in
`insert_into_assets`:

```python
conn.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_QR_code_assets_code_assets" ON "QR_code_assets" ("code_assets")')
```

- On PostgreSQL 16, `CREATE INDEX` requires **table ownership** — even with `IF NOT EXISTS`
  (the privilege check precedes the existence short-circuit; reproduced live:
  `ERROR: must be owner of table QR_code_assets`).
- The app connects as `assetcap_app` (DML grants only); the tables are owned by `developer`.
- The statement failed on **every** submit, aborting the single transaction holding the
  `QR_codes` upsert, the per-photo `QR_code_assets` inserts, and the audit rows (one `commit()`
  at the end of the block).
- The `except` handler reduced the failure to a `flash()` toast with **no server-side logging**,
  then rendered the success page. Photos and `_et.json` are written outside the transaction, so
  they survived.

Under SQLite this DDL was harmless (the file owner can always create indexes), which is why the
bug only surfaced at the cutover. Same-class latent DDL existed in
`utils/parameter_update_service.py` (`ensure_column` / `ALTER TABLE`).

### Compounding factors

1. **OpenAI quota exhaustion** (2026-06-09 → 2026-06-11 ~09:50) prevented the AI pipeline from
   producing JSONs that would have surfaced the gap sooner.
2. **`updating_process_database.py` skips orphan photos** (no `QR_codes` row → no `date_set`
   backfill), and the ME reviewer's `_auto_register_qr_code` creates a bare `QR_code_assets` row
   (no `user`/`date_hour`) — so the assets were half-visible with blank capture metadata, not
   obviously broken.
3. **No gunicorn access log** plus the swallow-handler meant `journalctl` showed nothing.

## Fix

1. **Code** (`asset_capture_app_dev/app.py`, `utils/parameter_update_service.py`):
   - All DDL removed from request paths; schema changes are owner-run migrations.
   - `/submit` failure now does `app.logger.error(..., exc_info=True)`, flashes a `danger` banner
     ("Database registration FAILED … this capture was NOT saved"), and redirects to the start
     page — **no false success page**.
   - PG dedupe uses `ON CONFLICT DO NOTHING` backed by the one-time index.
2. **Migration:** `scripts/migrations/2026-06-11_ux_qr_code_assets.sql` — the unique index
   created once as the `developer` role (peer auth via the port-5433 cluster's `/tmp` socket;
   `psql -h /tmp -p 5433 -d qr_code_db`). The 5433 cluster runs as the `developer` Unix user, so
   owner DDL needs no sudo.

## Backfill & Recovery

- 24 `QR_codes` rows + 69 `QR_code_assets` rows + `audit_trail` entries
  (`app_name='manual_repair'`); capture user attributed to `jwong112`, `date_hour` from photo
  mtimes, building from the photo filename. `Location` left blank (unrecoverable — field
  re-verification needed).
- `updating_process_database.py` re-run: `date_set` filled (changed=24), `json_files` refreshed.
- `ai_status` set to `'1'` for the already-extracted QRs (the cron watcher extracted the rest);
  final state: **24/24** registered, AI-extracted, and tracked in `json_files`.

## Validation

- End-to-end mobile capture confirmed working by two real submits on 2026-06-11:
  - **New QR** `0000086733` (bldg 641) — `Location` stored, **`Space` derived `0047`**,
    `Floor`/`Space Details`/`Floor Code` derived, 3 `QR_code_assets` rows (user `gandrade`),
    15 `mobile` audit rows — the first successful mobile registration since 2026-06-03.
  - **Overwrite/location change** `0000086732` — clean overwrite flow re-registered the row,
    `Space` re-derived `0047`.
- The new red failure banner was itself observed working during the secondary incident below.

### Secondary incident — identity-sequence collision (2026-06-11 11:36, fixed)

The first backfill inserted **explicit** `id`/`ID` values into `audit_trail` and `QR_code_assets`.
Both columns are `GENERATED BY DEFAULT AS IDENTITY` — but `information_schema.column_default` is
**empty** for identity columns (the giveaway is `is_identity='YES'`), so the explicit ids were
accepted while the underlying sequences stayed behind. The app's next auto-generated id then
collided with a backfilled row (`duplicate key value violates unique constraint "pk_audit_trail"`),
surfacing as a `danger` banner to the field user. The failed attempts consumed sequence values
until they passed the backfilled range; both sequences were then verified to sit exactly at their
table max (`audit_trail_id_seq` and `"QR_code_assets_ID_seq"`). **Lesson:** after any explicit-id
insert into an identity table, run `setval(pg_get_serial_sequence(...), MAX(id))`.

## VM Deployment

- 2026-06-11 ~10:41 PT: `app.py` + `utils/parameter_update_service.py` scp'd to the VM (normalized
  sha256 verified identical), `py_compile` gate passed, `assetcap-app` gunicorn master `HUP`'d
  (workers re-forked, `curl` 302 green). Backups: VM `*.bak_20260611_104052` + local snapshot
  `.deploy_backups/capture_registration_fix_20260611_104052/`.
- OpenAI key switched platform-wide the same morning: all three `API/API_interface_*_ver00.py`
  now load `API/OpenAI_key_giba.env` (was `OpenAI_key_bryan.env`); both files were deployed and
  the `.agent` docs updated. Snapshot `.deploy_backups/openai_key_switch_20260611_094704/`.

## Operational Notes / Follow-up

- **No DDL in request paths.** PostgreSQL privilege separation makes owner-only statements fail at
  runtime, and a failed statement poisons the whole PG transaction even when the exception is
  swallowed. Schema changes belong in owner-run migrations under `scripts/migrations/`.
- **Trigger overrides `ai_status` on INSERT.** `qr_autofill_ins` (BEFORE INSERT on `QR_codes`)
  sets `ai_status` to `'1'` only when the QR already exists in `sdi_dataset`/`sdi_dataset_EL`,
  else `'0'`; it also stamps `date_set`, derives `Space`/`Floor`/`Space Details`/`Floor Code`
  from `Location`, and defaults `sdi`. Backfill scripts must UPDATE `ai_status` after INSERT.
- **`ai_check.sh` still gates on the frozen SQLite file.** The 2-minute cron watcher queries
  `sqlite3 "$DB_PATH" … ai_status='0'` instead of PostgreSQL — post-cutover drift; it currently
  always finds "pending" rows and launches the EL/BF/ME sweep every cycle. Harmless today, but
  worth repointing at PG.
- Swallow-and-default `except` blocks remain the platform's recurring blind spot (third incident
  in this class); continue the logging-retrofit audit started 2026-06-09.
