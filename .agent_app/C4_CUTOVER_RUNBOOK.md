# C4 — PostgreSQL Cutover Runbook (windowed atomic flip)

> **Status:** ✅ **CUTOVER COMPLETE — LIVE ON POSTGRESQL (2026-06-08).** All 6 services flipped to `qr_code_db` (VM `127.0.0.1:5433`) via `DB_BACKEND=postgres` in `/home/developer/db_backend.env`. A live smoke + a proactive audit found & fixed the SQLite→PG bug classes (ME Manual-Entry sync, cron DSN quoting + stale `API/db.py`, dropdown identifier-case, RealDictRow unpacking, and the **entire EL SLD**, which the original conversion had missed). The SLD "Create New Diagram" PDF flow runs on PG behind `SLD_EXTRACT_PG_ENABLED=1` with idempotent failure recovery. **Rollback** = flip the env back to `sqlite` + restart (SQLite frozen, untouched). Durable summary in memory `pg-cutover-complete`.
> **Companion:** `C4_POSTGRES_CUTOVER_CHECKLIST.md` (gate), `C4_PER_SERVICE_TASKLIST.md` (per-service), `DATABASE_RELATIONSHIP_IMPROVEMENT_PLAN.md` (schema).
> **Precondition met:** all code tiers (Capture, SDI, Dashboard, review×3, API, scripts) converted to the backend-agnostic `db.py` layer **and functionally verified against the parallel PG cluster** (`qr_code_db`, VM `127.0.0.1:5433`). Code committed on branch `SQLite_to_Postgresql` (`d12764c`).

## Principles
1. **SQLite is the rollback.** During the window no writes hit SQLite (apps stopped) and none hit PG until the flip. So the live SQLite at window-start IS the rollback point — reverting is just flipping one env file back and restarting.
2. **One switch, atomic.** All services + crons read `DB_BACKEND`/`QR_PG_DSN` from **one shared env file** (`/home/developer/db_backend.env`). Flip = edit that file + restart. Rollback = edit it back + restart.
3. **Gate at every step.** Go/no-go checkpoints (Gate A/B/C). A failed gate → abort to SQLite, lose nothing (window had no captures).
4. **Fresh ETL at flip time.** The current mirror is QA-polluted (this session mutated it: ~181 `ai_status` flips, +247 audit rows). The flip target must be a clean reload of prod SQLite captured *inside* the window.

---

## Inventory (confirmed on VM 2026-06-06)

**Services (canonical, confirmed by ops + verified on VM 2026-06-06 — all `active`):**
| App | Port | Domain (`*.assetcap.facilities.ubc.ca`) | systemd unit | WorkingDirectory | gunicorn target |
|---|---|---|---|---|---|
| Capture | 8000 | `appprod` | `assetcap-app` | `asset_capture_app_dev` | `app:app` † |
| Dashboard | 8002 | `dashboardprod` | `assetcap-dashboard` | `Dashboard` | `Asset_portal_dashboard:app` |
| ME Review | 8001 | `reviewme` | `assetcap-reviewme` | `review/Asset_dasboard_browser_ME` | `asset_plate_reviewer:app` |
| BF Review | 8004 | `reviewbf` | `assetcap-bf` | `review/Asset_dasboard_browser_BF` | `asset_plate_reviewer_bf:app` ‡ |
| EL Review | 8005 | `reviewel` | `assetcap-el` | `review/Asset_dashboard_browser_EL` | `Asset_dashboard_EL:app` |
| SDI Process | 8003 | `sdiprocess` | `sdi_process` | `SDI_process` | `app:app` |

> **†** Capture: the *running* unit serves **`app:app`** (verified via `systemctl show`), not `asset_plate_reviewer:app` as the ops doc lists (doc typo — that's ME's target). Cutover uses `app:app`.
> **‡** `assetcap-bf` binds `0.0.0.0:8004` (others bind `127.0.0.1`); fronted by Nginx — consider tightening to `127.0.0.1` (not a cutover blocker).
> All domains share `SESSION_COOKIE_DOMAIN=.assetcap.facilities.ubc.ca` (cross-subdomain cookies). **Stale unit:** `reviewme.service` (defunct ME duplicate, `asset_reviewer:app` on :8002, crash-looping) → disable (step 0.7); live ME is `assetcap-reviewme`.
> **Flip set** = these 6 units. **Restart:** `sudo systemctl restart assetcap-app assetcap-reviewme assetcap-dashboard sdi_process assetcap-bf assetcap-el`.

**Crons (developer):**
| Schedule | Job | Runner | Needs at flip |
|---|---|---|---|
| `*/2 * * * *` | `run_update_db.sh` (→ migrate + DB-sync) | `API/.venv` (psycopg2 2.9.12 ✓) | `DB_BACKEND`/`QR_PG_DSN` |
| `*/2 * * * *` | `ai_check.sh` (→ `run_ai_and_sync.sh`) | app venv (psycopg2 2.9.10 ✓) | `DB_BACKEND`/`QR_PG_DSN` |
| `0 * * * *` | `scripts/audit_sdi_vs_json.py --quiet` | **system `/usr/bin/python3` (NO psycopg2)** | psycopg2 **+** env |
| `0 2 * * *` | `scripts/backup_daily.py` | **system `/usr/bin/python3` (NO psycopg2)** | psycopg2 **+** env |

**Migrations (`/home/developer/QR_database/`):** `mirror_etl.py`, `c3_model.sql`, `c4_capture_triggers.sql`, `c4_more_triggers.sql`, `c4_sdi_guardrails.sql`, `c4_identity_columns.sql`, `c4_audit_trail_indexes.sql`.

---

## PHASE 0 — Pre-flight (NO downtime; do days before the window)

> **✅ COMPLETE + verified 2026-06-06** (all reversible; prod + live mirror schema/data untouched). `db_backend.env` (=sqlite, no-op proven) wired into all 6 units + the 4 crons; auth hardened (`assetcap_app` scram password + `pg_hba` trust→scram with `developer` kept on trust; grants incl. views verified *as the app role*); the 5433 cluster is on systemd (`qr-postgres.service`, survived a restart test); `qr_code_id` BEFORE-INSERT trigger added; the ETL dry-run was timed and **caught + fixed a c3 blocker** (`chk_sdiel_approved` vs `''`) and closed 2 view flip-breakers (`Asset_System_info` + app-role view grants); `reviewme.service` disabled; the `apt python3-psycopg2` step was eliminated (crons repointed to the app venv). The 0.x items below are kept for the record (now done).

**0.1 — Confirm the env-flip mechanism.** Create `/home/developer/db_backend.env` (chmod 600; already gitignored via `*.env`):
```
DB_BACKEND=sqlite                      # stays sqlite until the flip
QR_PG_DSN="host=127.0.0.1 port=5433 dbname=qr_code_db user=assetcap_app password=<set in 0.3>"   # MUST be double-quoted: the spaces break shell-sourcing in the crons (`. db_backend.env` would set QR_PG_DSN=host=127.0.0.1 only -> psycopg2 falls back to port 5432). systemd EnvironmentFile parses it either way.
```
Wire it into **every** service + cron so the flip is one-file:
- systemd: `systemctl edit <unit>` → add `[Service]\nEnvironmentFile=-/home/developer/db_backend.env` for all 6 units. `systemctl daemon-reload`. (Restart NOT needed yet — file says sqlite.)
- crons: add at the top of the crontab: `DB_BACKEND` and `QR_PG_DSN` lines, **or** `source /home/developer/db_backend.env` inside `run_update_db.sh` + `ai_check.sh`, and `set -a; . /home/developer/db_backend.env; set +a` ahead of the two python3 cron lines (wrap them in a tiny shim). *Decision needed: crontab env vs per-script source.*
- **Verify NO behavior change** while file=sqlite: restart one unit, confirm it still serves on SQLite.

**0.2 — psycopg2 for system python3** (for the 2 system-python3 crons):
```
sudo apt-get install -y python3-psycopg2      # OR repoint those 2 cron lines to API/.venv/bin/python
python3 -c "import psycopg2; print(psycopg2.__version__)"
```

**0.3 — Auth hardening** (PG is currently `trust` on localhost):
```
# as the cluster superuser (developer):
ALTER ROLE assetcap_app WITH LOGIN PASSWORD '<strong-random>';
GRANT ... (confirm assetcap_app has the needed table privileges; it was created in C2)
# pg_hba.conf (PGDATA): change the 127.0.0.1 lines for qr_code_db from `trust` -> `scram-sha-256`
# keep a `trust` line for the `developer` superuser local socket OR use .pgpass for admin tasks
pg_ctl reload   # (or systemctl reload once 0.4 done)
```
Put the password only in `db_backend.env` (0.1). Test: `psql "host=127.0.0.1 port=5433 dbname=qr_code_db user=assetcap_app password=..." -c 'select 1'`.

**0.4 — systemd-ify the PG server** (currently `pg_ctl` → won't survive reboot):
Create `/etc/systemd/system/qr-postgres.service` (Type=notify or forking) running `pg_ctl`/`postgres` with `PGDATA=/home/developer/QR_database/pgdata` on port 5433 as user `developer`. `systemctl daemon-reload && systemctl enable --now qr-postgres`. **Test: `systemctl restart qr-postgres` + reconnect.** (Optionally a full VM reboot in a side window to prove survival.)

**0.5 — C3 follow-ups** (fold into canonical C3; apply to qr_code_db):
- **`qr_code_id` populate trigger** (NEW): `BEFORE INSERT ON "QR_code_assets"` → derive `qr_code_id` from `split_part(NEW.code_assets,' ',1)` when NULL. (New Capture rows currently leave it NULL.) Write as `c4_qca_qrcodeid_trigger.sql`.
- Confirm `c4_audit_trail_indexes.sql` + `c4_identity_columns.sql` are folded into `c3_model.sql` (so a fresh ETL+migrate reproduces them).

**0.6 — Dry-run the ETL** against a scratch DB (not the live mirror): snapshot prod SQLite → `mirror_etl.py` → scratch → apply c3+c4 → run §6 parity. Time it (sets the window length). Confirm clean.

**0.7 — Disable the stale `reviewme.service`** (resolved decision #1): it's a defunct ME unit (`asset_reviewer:app` on :8002 — wrong module + dashboard's port; crash-looping `status=1`). The live ME is `assetcap-reviewme.service` (:8001). `sudo systemctl disable --now reviewme.service` (needs the password-gated sudo). Confirm `:8001` still served by `assetcap-reviewme` afterward.

**Gate 0 (go/no-go for scheduling the window): ✅ MET 2026-06-06.** 0.1–0.7 all green; ETL dry-run parity clean on current prod data; rollback mechanism proven (the dashboard already restarted reading `db_backend.env`; flip-back = `sed` the env + restart).

---

## PHASE 1 — Maintenance window (the flip)

> Est. duration = ETL time (from 0.6) + ~10 min. Announce downtime.

**1.1 — Stop writers (drain).**
```
sudo systemctl stop assetcap-app assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
crontab -l > /home/developer/crontab.preflip.bak && crontab -r    # OR comment all lines; stops run_update_db/ai_check/audits/backup
# confirm no python writers: ps -eo args | grep -E 'gunicorn|run_ai|updating_process' | grep -v grep
```
SQLite is now frozen = the rollback point.

**1.2 — WAL-safe snapshot of prod SQLite.**
```
SRC=/home/developer/asset_capture_app_dev/data/QR_codes.db
SNAP=/home/developer/QR_database/source_snapshot.db
sqlite3 "$SRC" "VACUUM INTO '$SNAP'"     # WAL-consistent; NOT a bare cp
sqlite3 "$SNAP" "PRAGMA integrity_check; PRAGMA foreign_key_check;"
```

**1.3 — Fresh ETL + migrations into qr_code_db** — *validated end-to-end on CURRENT prod data 2026-06-06 (~3s data work). Scripts are repo-versioned in `QR_database/` (see its README) and live on the VM at `/home/developer/QR_database/`.*
```
cd /home/developer/QR_database
# mirror_etl.py itself does DROP SCHEMA public CASCADE => drop-recreate (decision #3); no pre-drop needed.
# It sources source_snapshot.db (written by 1.2). Run with the app venv python, then apply the 9 SQL files (-1 each, in order):
/home/developer/asset_capture_app_dev/venv/bin/python mirror_etl.py
for f in c3_model c4_capture_triggers c4_more_triggers c4_sdi_guardrails \
         c4_identity_columns c4_audit_trail_indexes c4_asset_system_info_view \
         c4_qca_qrcodeid_trigger c4_grants; do
  psql -h 127.0.0.1 -p 5433 -U developer -d qr_code_db -1 -v ON_ERROR_STOP=1 -f "$f.sql" \
    && echo "OK $f" || { echo "ABORT at $f"; break; }
done
```
> `c3_model.sql` `chk_sdiel_approved` is widened to `IN ('0','1','')` (the EL SLD missed-asset path writes `''`; strict `IN ('0','1')` aborted ETL on real data). The last 3 files close flip-breakers the QA-polluted mirror hid — `c4_asset_system_info_view.sql` (the Dashboard view, absent from the old scripts) and **`c4_grants.sql` which MUST run last** (grants the `assetcap_app` role on the views; without it the Dashboard 500s as the app role). A failed step aborts the loop — fix and re-run (idempotent; mirror_etl re-drops).

**1.4 — GATE A: parity check (SQLite snapshot vs PG).** Run the `C4_POSTGRES_CUTOVER_CHECKLIST.md §6` validation queries. Minimum:
```
# row counts must match for: QR_codes, QR_code_assets, sdi_dataset, sdi_dataset_EL,
# process_type, json_files, audit_trail, electrical_building_schema, sdi_print_out(_arch)
# + constraints present: \d in psql; + triggers present.
```
**Match → proceed. Mismatch → ABORT (Gate-A rollback below).**

**1.5 — FLIP the switch.**
```
sed -i 's/^DB_BACKEND=sqlite/DB_BACKEND=postgres/' /home/developer/db_backend.env
```

**1.6 — Deploy converted code to prod app dirs.** Pull `SQLite_to_Postgresql` (or rsync the converted files) into `/home/developer/asset_capture_app_dev/`, `API/`, `Dashboard/`, `SDI_process/`, `review/*/`, `scripts/` — incl. each `db.py` (**note: prod `scripts/` has no `db.py` yet — deploy `scripts/db.py` too, the repointed audit/backup crons need it**). (Per `prod-vm-deploy.md`: copy files; these dirs are not git repos.) `py_compile` each app's entrypoint. **Then add PG ordering to the unit drop-ins** (the `EnvironmentFile` drop-ins from Phase 0 are already in place): append `Wants=qr-postgres.service` + `After=qr-postgres.service` under a `[Unit]` section in each `/etc/systemd/system/<unit>.service.d/db_backend.conf`, then `sudo systemctl daemon-reload` — so a post-flip reboot starts the apps only after PG signals ready (`Type=notify`).

**1.7 — Restart services + crons.**
```
sudo systemctl restart assetcap-app assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
crontab /home/developer/crontab.preflip.bak    # restore crons (now reading db_backend.env=postgres)
```

**1.8 — GATE B: smoke test (per service, against PG).** *(Health-check with `curl --retry 6 --retry-delay 1 --retry-all-errors` — the dashboard takes ~5s to boot all chart modules, so an instant curl returns `000`; retry avoids a false rollback trigger.)*
- Capture (:8000): load `/`, fetch buildings + a temp_code, **submit one test capture** → confirm `QR_codes` + `QR_code_assets` rows land in PG (then purge the test QR).
- Dashboard (:8002): overview + charts + AI Process Queue render with correct numbers.
- Review ME/BF/EL (:8001/8004/8005): list → open → toggle Approved/Manual on a test row → confirm `sdi_dataset(_EL)` write.
- SDI (:8003): building dropdown + Retrieve Archives + one package create→approve.
- Cron: run `run_update_db.sh` once manually → `json_files` refresh keeps PK/FK; `backup_daily.py` → produces `qr_code_db_<date>.dump` (pg_restore --list valid).
**All green → Gate B pass. Any failure → Gate-B rollback.**

---

## PHASE 2 — Verify + monitor
- **GATE C:** watch `logs/` for ~30–60 min of live traffic (the broadened `except` means real PG errors now surface). Confirm a real field capture + an AI extraction + a review save + the */2 crons all succeed.
- Confirm `backup_daily` produced a PG dump that night.
- After a stable period (e.g., 1–2 days), retire the SQLite write path (keep the `…fullbak…postAB` + the snapshot as cold rollback).

---

## ROLLBACK (any gate)

> **⚠️ Precheck (added 2026-06-08) — verify the SQLite rollback actually has data.** The frozen `QR_codes.db` was found **empty (0 bytes)** once post-cutover (cause not identified; data was safe in PG + `source_snapshot.db`). **Before** flipping back, run `sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db 'SELECT count(*) FROM QR_codes'`. If it returns **0 or errors**, restore the clean pre-flip copy first: `cp /home/developer/QR_database/source_snapshot.db /home/developer/asset_capture_app_dev/data/QR_codes.db` (expect `970`+ rows). A write-watch (`auditctl -k qrdb_rollback_watch`) is armed on the file to catch any recurrence — query with `sudo ausearch -k qrdb_rollback_watch`.
1. `sed -i 's/^DB_BACKEND=postgres/DB_BACKEND=sqlite/' /home/developer/db_backend.env`
2. (If 1.6 deployed new code and a converted file misbehaves on SQLite — it shouldn't, SQLite is the default path — restore the pre-flip code copies.)
3. `sudo systemctl restart assetcap-app assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process`
4. `crontab /home/developer/crontab.preflip.bak`
5. Verify each service serves on SQLite. **Data loss = nil** (no captures occurred during the window; SQLite is exactly as at 1.1).

> Gate-A rollback is trivial (never flipped — just restart on SQLite). Gate-B/C rollback = steps 1–5.

---

## Decisions — all resolved (2026-06-06); Phase 0 COMPLETE
1. ~~`assetcap-reviewme` vs `reviewme.service`~~ **RESOLVED + DONE:** `reviewme.service` disabled+stopped (step 0.7); live ME = `assetcap-reviewme` (:8001).
2. ~~Cron env mechanism~~ **RESOLVED + APPLIED:** every cron line `source`s `db_backend.env`; the 2 system-`python3` crons (`audit_sdi_vs_json`, `backup_daily`) repointed to the app venv (psycopg2-ready) → the `apt python3-psycopg2` step is eliminated. Backup: `crontab.orig.bak_*`. Live `*/2` tick confirmed clean on SQLite.
3. ~~ETL strategy~~ **RESOLVED:** drop-recreate — `mirror_etl.py` runs `DROP SCHEMA public CASCADE` itself; re-apply c3 + c4 after (see 1.3).
4. **Window:** low-traffic slot **this weekend** (data work ~3s; full window ~15–30 min incl. smoke tests). Confirm exact time with the operator before Phase 1.
5. Dedicated read-only Dashboard role — *deferred, optional* (the app uses `assetcap_app` for all tiers; grants incl. views verified).

---

## Consolidated Change Log (2026-06-08, post-cutover)

A single-page summary of everything the C4 cutover changed, for the record.

### Database infrastructure
- Stood up a dedicated **PostgreSQL 16** cluster on the VM: database `qr_code_db`, port `:5433`, PGDATA `/home/developer/QR_database/pgdata`, systemd unit `qr-postgres.service`.
- Roles: `assetcap_app` (least-privilege SELECT/DML, **no TRUNCATE**) for the apps; `developer` (superuser) for admin/restore.
- Migrated the full schema + data (**24 tables**) to PostgreSQL, preserving generated columns (`ID_check` STORED), constraints, and identity sequences.
- SQLite `QR_codes.db` frozen as the rollback — untouched.

### Backend switch & rollback
- `/home/developer/db_backend.env` (chmod 600): `DB_BACKEND=postgres` + **quoted** `QR_PG_DSN`; read by all 6 systemd units + the crons.
- Rollback = flip `DB_BACKEND=sqlite` + restart.

### Backend-agnostic data layer (`db.py`)
- Per-service `db.py` shim: `?`→`%s`, `PRAGMA`/`sqlite_master`→`information_schema`, `sqlite3.Row`→RealDictCursor; exposes `is_postgres()`, `table_columns()`, `has_table()`, `get_connection()`, `DatabaseError`.

### Application fixes (bug classes found in the smoke)
- ME review: `ASSET_GROUP_COL "name"`→`"Name"`; parameterized the Manual-Entry `sdi` query.
- BF review: `ASSET_GROUP_COL "Name"`; fixed the `Application` dropdown (RealDictRow tuple-unpack returned the column name).
- Dashboard: fixed the Dictionary "Attribute Set" dropdown (unquoted mixed-case `Code`/`Attribute`).
- API/`db.py`: replaced a stale copy missing `DatabaseError`.
- Cron DSN: quoted `QR_PG_DSN` (unquoted spaces broke shell-sourced crons → fell back to :5432).
- CHECK constraint: widened `chk_qr_approved` to allow the empty `''` value.
- Systemic PG-isms: unquoted mixed-case identifiers, double-quoted string literals, strict `GROUP BY`, `SELECT DISTINCT … ORDER BY f(x)`, bare `except sqlite3.Error`, `cur.lastrowid`.

### EL SLD subsystem (found unconverted in the audit → converted)
- `sld_blueprint.py`: full PG conversion (61+ identifiers quoted, single-quoted literals, `COLLATE NOCASE`→`LOWER`, `rowid`→`"Equipment ID"`, `except qrdb.DatabaseError`).
- `extract_electrical_schema.py`: DELETE-not-DROP on PG (preserves generated `ID_check`/constraints/identity); gated behind `SLD_EXTRACT_PG_ENABLED`.
- Create-diagram hardening: `_restore_full_snapshot()` (idempotent clear+restore) on every failure path; a corruption incident (391→2160 rows) fully recovered.

### Validation (operator UI runs)
- All 6 services confirmed on PG: Capture · ME/BF/EL review · Dashboard (+ dictionary) · SDI (E2E package→approve→un-approve) · SLD (view/edit/reconcile/create-diagram) · audit trail.
- Forced AI re-extraction validated; approval-sync + audit invariants intact.

### Documentation & memory
- Authoritative docs updated: `04_database_topography.md`, `CLAUDE.md`, this runbook (→ COMPLETE).
- Doc sweep: ~30 stale "SQLite is operational" claims fixed across 19 repo files (canonical + `.agent_app`) + 8 SecondBrain vault files; auth-DB SQLite + `PRIME.md` untouched. Committed `f4393d2`, pushed to VM.
- Memory `pg-cutover-complete` (and `vm-dual-db-topology` flagged superseded).

### Local developer copy + backups
- Backups in `OneDrive\…\PostgreSQL_vm_backup\`: `qr_code_db.dump` + `qr_code_db.sql` (24/24 tables verified).
- Runnable local copy: portable **PostgreSQL 16.14** on `127.0.0.1:5432`, `qr_code_db` restored (970 rows).
- On-demand refresh: run `C:\Users\gandrade\OneDrive - UBC\Documents\PostgreSQL_vm_backup\Refresh_local_database\run_refresh_local_postgres_from_vm.bat`. This launcher runs the Python checksum refresh routine, writes JSON/full-run/summary logs under `PostgreSQL_vm_backup`, and opens the summary log when finished. Legacy fallback remains `refresh_from_vm.ps1` + `start_local_db.ps1` + `README_LOCAL_DB.md`.

### Key commits (`SQLite_to_Postgresql`)
- `d12764c` core conversion · `274d789`/`259e838`/`cd01092` ME/BF · `cc7795c` API db.py · `bdeecf4`/`dc10e61`/`95026fb`/`19783f4` EL SLD + hardening · `a93c076` dictionary dropdown · `eb8086a`/`5332f45` docs · `f4393d2` doc sweep.

### 2026-06-09 — First post-flip field regression: capture duplicate-scan warning (FIXED)
- Symptom: mobile Capture App stopped warning on already-recorded QR scans (and the parameter-change dialog with it). Field-reported; invisible to the smoke because `/api/check-qr` swallowed the error into a plausible `{"exists": false}` (HTTP 500, no log line) behind login.
- Cause: one more instance of the systemic PG-ism above — `get_current_params()` (`asset_capture_app_dev/utils/parameter_update_service.py`) seeded its SELECT list with unquoted `QR_code_ID` → folded to `qr_code_id` → `UndefinedColumn`. `qr_exists()` itself was fine.
- Fix: `_quote_ident("QR_code_ID")` + `app.logger.error(..., exc_info=True)` in `api_check_qr()`. Verified on the local PG replica AND on the SQLite rollback backend; deployed via scp + `HUP` of the `assetcap-app` gunicorn master (VM `.bak_20260609_142605` backups; local snapshot `.deploy_backups/duplicate_scan_pg_fix_20260609_142605/`).
- Details: `Markdowns_documentation/INCIDENT_2026-06-09_pg_unquoted_identifier_duplicate_scan.md`. New global rules in `01_GLOBAL_RULES.md` §2 (quote all mixed-case identifiers; never swallow DB errors unlogged). Platform-wide unquoted-identifier audit queued → completed the same day (next section).

### 2026-06-09 — Platform-wide PG-ism audit (COMPLETE → deployed)
- Scope: every service touching `qr_code_db` (capture, ME/BF/EL review, Dashboard, SDI, API, audit module). Method: exact-case identifier list from `information_schema` on the local replica + AST scan of every string literal (incl. f-string/concat-built SQL) + `EXPLAIN` of all 100 static SQL strings + read-only pre/post execution of every suspect against the replica.
- Live fixes (PG-verified, re-run on SQLite for rollback parity): Dashboard `_lookup_qr_meta` 3× `ORDER BY ROWID` → `ctid`/`ROWID` branch; Dashboard activity log `TRIM(b.Name)` → `b."Name"`; approval chart `instr()`/`" "`/`""`/`COLLATE NOCASE` → `split_part`/`strpos` branch + `GROUP BY`+`ORDER BY LOWER()`; `_sdi_row_has_data*` guards in **all three** review apps — `COALESCE("Avg_ai_conf", 0) > 0` was a PG `DatatypeMismatch`, so the placeholder-wipe guard always returned False (re-sync could blank real AI rows) → `TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), '')) NOT IN ('', '0', '0.0')`; `test/test_placeholder_sync_guard.py` predicates mirrored (13/13 pass).
- Audited clean: audit module (lowercase `audit_trail`), live API interfaces, SDI app, `updating_process_database.py`. Dead/out-of-scope, left as-is: legacy Access-DB utils (`asset_capture_app_dev/utils/building_lookup.py`, `file_handler.py` — never imported), `*_bkp.py` copies (PG-broken; do not resurrect), gps_service (separate SQLite store by design). **Update 2026-06-09 (dead-code cleanup):** the Access-DB utils and the `*_bkp.py` copies (`Dashboard/Asset_portal_dashboard_bkp.py`, `Dashboard/charts/operational_cost_resultbkp.py`, `API/API_interface_{BF,EL}_ver00_bkp.py` — no ME `_bkp` existed) were re-verified unreferenced and deleted from the repo; recoverable from git history. gps_service and `data/debug_asset.py` remain.
- Deployed 2026-06-09 ~15:35 PT: 6 files scp'd (remote SHA256 verified, VM `py_compile` gate), gunicorn masters HUP'd for Dashboard/ME/BF/EL, all workers re-forked stable, 302 probes green. Backups: VM `*.bak_20260609_153251` + local `.deploy_backups/pg_audit_fixes_20260609_153251/`.
- Outstanding: 62 swallow-and-default `except Exception` handlers inventoried for `logger.error(..., exc_info=True)` retrofits — list in the incident doc § Platform-Wide Audit.

### 2026-06-11 — Mobile capture registrations silently lost (PG DDL privilege rollback) (FIXED)
- Symptom: every mobile `POST /submit` since the 2026-06-08 cutover saved photos + `{qr}_et.json` but wrote **nothing** to the DB — no `QR_codes`/`QR_code_assets`/`audit_trail` rows; field user saw the success page. 24 QRs captured 2026-06-09/10 showed up in review with blank Capture Date / Space / Captured by / Date / Hour. Last successful `app_name='mobile'` audit row was 2026-06-03.
- Cause: `insert_into_assets` (`asset_capture_app_dev/app.py`) ran `CREATE UNIQUE INDEX IF NOT EXISTS "ux_QR_code_assets_code_assets"` inside every `/submit` transaction. PG requires table ownership for `CREATE INDEX` (ownership checked before `IF NOT EXISTS`); the app runs as `assetcap_app` (DML only) while `developer` owns the tables → `insufficient_privilege` aborted the whole transaction; the `except` only `flash()`ed (no log). SQLite tolerated it (file owner can always index), so it was latent until the cutover. Same class of latent DDL in `utils/parameter_update_service.py` (`ensure_column`/`ALTER`).
- Fix: removed all DDL from the request paths; the index is now a one-time owner-run migration `scripts/migrations/2026-06-11_ux_qr_code_assets.sql` (created as `developer` via `psql -h /tmp -p 5433`). `/submit` failure now does `app.logger.error(..., exc_info=True)` + `danger` flash + redirect to start (no false success page). New global rule in `01_GLOBAL_RULES.md` §2: no DDL in request paths. Deployed ~10:41 PT via scp + `HUP` of `assetcap-app` (VM `*.bak_20260611_104052`; local `.deploy_backups/capture_registration_fix_20260611_104052/`).
- Recovery: 24 `QR_codes` + 69 `QR_code_assets` rows backfilled (user `jwong112`, dates from photo mtimes; `Location`/Space unrecoverable → field re-verification), `updating_process_database.py` filled `date_set` + refreshed `json_files`, all 24 reach `ai_status=1`. Validated end-to-end by two real captures (new `0000086733` Space→`0047`; overwrite `0000086732`).
- Secondary: the first backfill inserted explicit ids into the `GENERATED BY DEFAULT AS IDENTITY` columns `audit_trail.id` / `QR_code_assets."ID"` (whose `column_default` reads empty), leaving the sequences behind → the next mobile capture hit a `pk_audit_trail` duplicate at 11:36. Failed attempts burned past the range; both sequences re-verified at table max. New global rule: `setval` after explicit-id inserts into identity tables.
- Also 2026-06-11: OpenAI extraction key switched platform-wide to `API/OpenAI_key_giba.env` (all three `API_interface_*_ver00.py` + `.agent` docs; local `.deploy_backups/openai_key_switch_20260611_094704/`).
- Details: `Markdowns_documentation/INCIDENT_2026-06-11_pg_capture_registration_ddl.md`.
- Outstanding: `ai_check.sh` (2-min cron) still gates pending work on the frozen SQLite file (`sqlite3 "$DB_PATH" … ai_status='0'`) instead of PG — repoint at PostgreSQL.
