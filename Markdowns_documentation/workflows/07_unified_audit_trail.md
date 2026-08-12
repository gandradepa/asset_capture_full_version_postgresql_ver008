# Workflow: Unified Audit Trail

Current documentation refresh: 2026-06-08.

## Purpose

Capture a complete per-field history of every modification to asset data
across all Asset Capture apps (Mobile Capture, ME / BF / EL Reviewers, SDI
Process, API extraction pipeline) into a single queryable table indexed by
QR code, with explicit attribution of human vs. AI vs. system writes.
Replaces the previous "User Activity Log" panel which only recorded photo
upload events.

## Inputs

- the shared operational DB — PostgreSQL `qr_code_db` (VM `127.0.0.1:5433`, via `db.py`) post the 2026-06-08 cutover; legacy SQLite `QR_codes.db` is the frozen rollback
- the running Flask apps and their save endpoints
- the `audit/` shared package at `/home/developer/audit/`
- the Dashboard auth user table (`User.username`, `User.name`) for
  display-name enrichment in the User Activity Log

## Schema

Single table `audit_trail` in the operational DB (`qr_code_db`):

| Column | Type | Notes |
| --- | --- | --- |
| id | INTEGER PK | Auto-increment, monotonic event ordering |
| qr_code | TEXT | The QR Code the change applies to (nullable for QR-less rows) |
| description | TEXT | Free-text label of the calling site (e.g., "POST /review") |
| modification_date | TEXT NOT NULL | YYYY-MM-DD, server local time (America/Vancouver) |
| modification_time | TEXT NOT NULL | HH:MM:SS, server local time |
| modified_by | TEXT NOT NULL | Username from `flask_login.current_user`, or `ai-pipeline`, or `system` |
| source | TEXT NOT NULL | `human`, `ai:gpt-5.5`, `ai:dictionary`, or `system` |
| app_name | TEXT NOT NULL | `mobile`, `reviewer_me`, `reviewer_bf`, `reviewer_el`, `sdi`, `api_pipeline`, `dashboard` |
| table_name | TEXT NOT NULL | The DB table whose row was changed |
| record_pk | TEXT NOT NULL | String form of the row's primary key |
| op_type | TEXT NOT NULL | `INSERT`, `UPDATE`, or `DELETE` |
| field_name | TEXT | The column whose value changed |
| old_value | TEXT | Pre-change value (NULL for INSERT) |
| new_value | TEXT | Post-change value (NULL for DELETE) |
| created_at | TEXT NOT NULL | DB server timestamp the row was inserted |

Indexes: `qr_code`, `modification_date`, `modified_by`, `(app_name, table_name)`, `(table_name, record_pk)`.

Granularity: one row per changed field per save. A save that touches three
columns produces three audit rows that share the same date / time /
modified_by.

## Architecture

### Shared logger

`/home/developer/audit/logger.py` exposes a single function:

```python
log_change(conn, *, qr_code, app_name, table_name, record_pk,
           op_type, field_changes, modified_by=None,
           source="human", description="", now_utc=None) -> int
```

- Accepts `sqlite3.Connection` or a SQLAlchemy session.
- Auto-detects `flask_login.current_user.username` when `modified_by`
  is not given.
- Skips no-op fields where the normalized old and new values are equal.
- **Does not commit.** The audit rows participate in the caller's
  transaction so a failed write rolls back the audit too.
- Uses naive local time (server tz `America/Vancouver`) so timestamps
  match the rest of the system.

Companion helper `audit/diff.py` exposes `diff_dicts(before, after)`
which is the standard way to compute the field-changes dict.

### Dashboard read model

`Dashboard/Asset_portal_dashboard.py` exposes `GET /api/user-activity`.
The endpoint reads `audit_trail`, joins `QR_codes` / `Buildings` for
building-scoped filtering, and enriches each row with the display `name`
from User Administration by matching `audit_trail.modified_by` to
`User.username`.

Response shape:

- `data`: newest-first activity rows with `qr_code`, `user`, `name`,
  `date`, `hour`, `app_name`, `op_type`, `field_name`, `old_value`,
  `new_value`, and `source`
- `users`: distinct `modified_by` values for the User filter
- `sources`: distinct `source` values for the Source filter
- `building`, `limit`, `total`: request metadata

The `name` value is a read-time convenience only; it is not stored in
`audit_trail`. If a matching User Administration record has no name, or
if the audit username has no matching auth user, the Dashboard returns an
empty `name`.

### Save-site pattern

Every save endpoint follows this shape:

```python
before = dict(conn.execute('SELECT * FROM "T" WHERE pk=?', (pk,)).fetchone() or {})
do_actual_write(...)
after  = dict(conn.execute('SELECT * FROM "T" WHERE pk=?', (pk,)).fetchone())
log_change(conn, qr_code=qr, app_name='mobile', table_name='T',
           record_pk=pk, op_type='INSERT' if not before else 'UPDATE',
           field_changes=diff_dicts(before, after),
           source='human', description='POST /submit')
conn.commit()
```

For DELETE: snapshot before delete, emit one row per non-null field with
`new=None`, `op_type='DELETE'`.

### AI vs human attribution

| Site | source value |
| --- | --- |
| Mobile capture (`asset_capture_app_dev`) | `human` |
| Reviewer ME / BF form-submitted fields | `human` |
| Reviewer ME / BF dictionary-auto-filled fields | `ai:dictionary` |
| Reviewer EL human edit overriding the AI baseline | two rows: `ai:gpt-5.5` (anchor) + `human` (override) |
| Reviewer EL human edit on a non-AI value | `human` |
| Reviewer EL Reconcile (Supply From divergence resolved) | one `human` row per changed side; `description="reconcile:<choice>"` |
| BF `toggle_ai_status` (operator metadata flag) | `system` |
| API extraction pipeline writes | `ai:gpt-5.5` (modified_by `ai-pipeline`) |
| Dashboard Disposed tool (dispose / restore) | `human` (app_name `dashboard_disposed`) |
| Image-sync placeholder upserts (no payload yet) | `system` |
| JSON-sync upserts after AI extraction | `ai:gpt-5.5` |

The Reviewer ME / BF source map is computed per-save: form keys submitted
by the human stay `human`; columns whose value comes from
`apply_dictionary_rules` get `ai:dictionary`. Since 2026-07-07, reviewer
overrides of `Asset Group` / `Attribute` persist via the
`asset_group_manual` / `attribute_manual` JSON flags, so a `human`-labeled
value for those columns is genuinely the reviewer's (previously the
dictionary could overwrite it after labeling). The Reviewer EL rule lives in
`_emit_human_corrections_for_row` and compares the pre-edit value against
`sld_ai_extract_payload`.

## Files Wired

### New (audit/ + scripts/)

- `audit/__init__.py`, `audit/logger.py`, `audit/diff.py`
- `scripts/migrate_create_audit_trail.py` — idempotent DDL
- `scripts/backfill_audit_trail_from_qr_code_assets.py` — synthesizes
  INSERT rows from the legacy `user` / `date_hour` columns
- `scripts/audit_trail_health.py` — sanity / smoke check, exits non-zero
  if schema is missing
- `scripts/cleanup_api_pipeline_date_set_noise.py` — one-shot purge of
  bogus churn rows (kept for reference, no longer needed at steady state)

### Modified

- `asset_capture_app_dev/app.py` — `/submit`, `/api/update-parameters`,
  `/delete-upload`, plus the overwrite path
- `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` — the
  `_db_upsert_sdi_dataset` helper takes audit kwargs; the three toggle
  routes log directly
- `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` — same
  pattern as ME, plus `upsert_approved_in_db`, `_db_toggle_qr_sdi`,
  `toggle_ai_status`
- `review/Asset_dashboard_browser_EL/sld_blueprint.py` — refactored
  `_emit_human_corrections_for_row` to feed `log_change`; `create_asset`
  and `delete_asset` audited. The Reconcile endpoint (`reconcile_asset`)
  emits one `human` row per changed side — `table_name` is either
  `electrical_building_schema` or `sdi_dataset_EL`, `description`
  starts with `reconcile:<choice>` and includes the optional `reason`
- `API/updating_process_database.py` — pipeline writes tagged
  `ai:gpt-5.5`, with dedupe + orphan skip so the cron doesn't churn
- `API/API_interface_BF_ver00.py` — bulk `ai_status` writes audited
- `Dashboard/Asset_portal_dashboard.py` — `/api/user-activity` rewritten
  against `audit_trail`, with `source` and `user` query filters plus
  User Administration `name` enrichment
- `Dashboard/templates/dashboard.html` — User Activity panel extended
  with Name / App / Action / Field / Old / New / Source columns, a Source
  filter dropdown, a required-filter empty state, and a full-width
  no-wrap table layout
- `run_update_db.sh` — runs the migration before the existing pipeline

## Main Steps

### Initial deploy (one time)

1. `scp` the `audit/` package and the four scripts to
   `/home/developer/audit/` and `/home/developer/scripts/`.
2. `scp` the modified app files into their existing locations.
3. Back up the production DB:
   `cp QR_codes.db QR_codes.bak_pre_audit_trail_$(date +%Y%m%d_%H%M%S).db`.
4. Run `python3 /home/developer/scripts/migrate_create_audit_trail.py`.
5. Optionally run
   `python3 /home/developer/scripts/backfill_audit_trail_from_qr_code_assets.py`
   to seed the panel with prior `user` / `date_hour` history.
6. Restart all gunicorn services so they reload the new `audit` import:
   ```bash
   sudo systemctl restart assetcap-app.service assetcap-reviewme \
       assetcap-bf assetcap-el sdi_process assetcap-dashboard
   ```
7. Hard-refresh the Dashboard in the browser (Ctrl+Shift+R) to bust the
   cached template.

### Day-to-day operation

The system writes audit rows automatically. Operators interact with the
data through the Dashboard's User Activity Log panel
(`/api/user-activity`) — filter by user, source, QR, or field. The cron
job `run_update_db.sh` keeps the migration idempotent across deploys.

The panel requires at least one filter before rows are displayed. The
visible table columns are QR Code, User, Name, Date, Time, App, Action,
Field, Old Value, New Value, and Source. The `Name` column is resolved
from User Administration at read time. Table text does not wrap; the User
Activity view uses full-width dashboard space so the table can expand
horizontally.

## Outputs

- `audit_trail` rows that grow with every save event
- Dashboard "User Activity Log" panel with per-field history and AI vs.
  human attribution, including User Administration display names when
  available
- Health-check script output for ops verification

## Guardrails

- The shared logger never commits — always inside the caller's
  transaction so audit and data succeed or roll back together.
- The logger refuses `op_type` outside `{INSERT, UPDATE, DELETE}`.
- No-op fields (where normalized old equals normalized new) are dropped
  before the INSERT, so saves that change nothing produce zero rows.
- Orphan QR-without-row writes from the API pipeline are skipped to
  prevent garbage rows every cron cycle.
- Local time is used everywhere (server tz `America/Vancouver`) — do
  not introduce UTC anywhere in the audit path or the dashboard hour
  display will drift.

## Verification

### Schema health

```bash
python3 /home/developer/scripts/audit_trail_health.py
```

Expects `Schema status: OK` (15 columns, 5 indexes), a non-zero row
total, and the QR-code lookup index plan showing
`USING INDEX ix_audit_trail_qr`.

### End-to-end smoke

1. Submit one mobile capture for a fresh QR. Health check should show a
   new `mobile / human / INSERT` row for `QR_codes` plus one row per
   uploaded photo for `QR_code_assets`.
2. Edit a single field in the Mechanical or Backflow reviewer. Health
   check should show one `reviewer_me` (or `reviewer_bf`) `UPDATE` row
   per changed column. Dictionary-filled columns get `ai:dictionary`,
   form-submitted columns get `human`.
3. Edit an SLD field whose pre-edit value matched
   `sld_ai_extract_payload[field]`. Two rows should appear: one
   `ai:gpt-5.5` anchoring the AI baseline, one `human` for the override.
4. Run `bash /home/developer/run_update_db.sh` twice in a row. The
   second run should report `changed=0, unchanged=N, orphan=M` and
   produce zero new audit rows.
5. Open the Dashboard User Activity Log. Confirm no rows display until
   at least one filter is set. Select a user or source and confirm the
   table includes `Name` immediately after `User`; the value should match
   User Administration when that username has a populated name.

### Drill-down on a single QR

```bash
python3 /home/developer/scripts/audit_trail_health.py --qr 0000185031
```

Returns the full per-field history for that QR sorted newest-first.

## Maintenance

- **Health check**: run weekly (or after any deploy that touches save
  endpoints) — `python3 /home/developer/scripts/audit_trail_health.py`.
- **Backups**: full DB backup includes `audit_trail`. The pre-deploy
  backup created during the initial rollout is at
  `QR_codes.bak_pre_audit_trail_<timestamp>.db`.
- **Code-side backups** of the original (pre-audit) Python files are at
  `/home/developer/.deploy_backups/audit_trail_<timestamp>/` and can be
  restored if a rollback is needed.
- **Rollback**: copy the `.deploy_backups/...` files back over the
  current ones, restart the six services, and (optionally) drop the
  `audit_trail` table. The migration script is idempotent so the table
  can also stay in place during a rollback without harm.
- **Trimming**: the `audit_trail` table grows monotonically. If size
  becomes a concern, archive rows older than N months into a
  `audit_trail_archive` table or export to cold storage. There is no
  automatic retention policy.
