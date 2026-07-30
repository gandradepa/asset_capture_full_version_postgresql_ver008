# PRIME.md — UBC Asset Capture Platform

> Fast-start primer for AI coding assistants, developers, and reviewers entering this repository.
> This is **not** a replacement for the full documentation under `Markdowns_documentation/` and the per-module `.agent` folders — it is a minimal map to orient quickly and avoid breaking critical workflows.

---

## 1. Project Purpose

This repository contains the **UBC Facilities Asset Capture workflow platform**. It supports the end-to-end lifecycle of facilities asset records: QR-based asset capture in the field, image upload, OCR/AI extraction of nameplate data, human review and correction, dashboard analytics, SDI (Standard Data Interchange) packaging, and Planon export.

It is used operationally by UBC Facilities to register and maintain Mechanical, Electrical, and Backflow assets across the campus building portfolio.

---

## 2. System Summary

The platform is a **workflow-driven distributed monolith** made up of multiple Flask applications and worker scripts. Although the services run independently, they share critical state:

- One operational **PostgreSQL database** (`qr_code_db`)
- One **image capture folder** (`Capture_photos_upload/`)
- One **JSON extraction / review output folder** (`Output_jason_api/`)
- A **shared authentication and session configuration** (`auth_service`)
- **Discipline-specific workflows** for Mechanical (ME), Electrical (EL), and Backflow (BF) assets

Each discipline has its own completeness rules, confidence calculations, review UI, and SDI table targets. Code that treats them as interchangeable will break the platform.

---

## 3. Core Workflow

```
Capture App  →  Extraction API  →  Review Apps (ME / BF / EL)  →  SDI Process  →  Planon Export
```

The central **Dashboard** sits across this pipeline as the **operational control plane**: it surfaces approval status, launches AI extraction, embeds the review apps, and links to dictionary and SDI tooling.

---

## 4. Main Runtime Modules

| Module | Responsibility | Main Entry Point | Default Port |
|---|---|---|---|
| Capture App | Field capture, QR intake, photo upload | `asset_capture_app_dev/app.py` | 5001 |
| ME Review | Mechanical nameplate review and approval | `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` | 5002 |
| BF Review | Backflow device review and approval | `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` | 5004 |
| EL Review | Electrical asset review and approval | `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` | 8005 |
| Dashboard | Unified operational shell, analytics, launchers | `Dashboard/Asset_portal_dashboard.py` | 8002 |
| SDI Process | SDI validation, packaging, archive | `SDI_process/app.py` | 8003 |

Note: the folder spellings `Asset_dasboard_browser_ME` and `Asset_dasboard_browser_BF` are intentional historical names. EL uses the correctly-spelled `Asset_dashboard_browser_EL`. Preserve both spellings verbatim.

Review pages for ME, BF, and EL share a mouse-friendly image viewer pattern: buttons still zoom / rotate, and the image stage also supports mouse-wheel zoom, drag-to-pan, double-click reset/detail zoom, keyboard shortcuts (`+`, `-`, `0`, `R`, arrows), and a reset button. The reusable frontend controller is `review_asset_templates/static/image-viewer.js` in each review app; keep the three copies behaviorally identical unless a shared static route is intentionally introduced.

---

## 5. Shared State and Key Paths

| Purpose | Path |
|---|---|
| Operational DB | PostgreSQL `qr_code_db` |
| VM DB address | `127.0.0.1:5433`, data dir `/home/developer/QR_database/pgdata`, env file `/home/developer/db_backend.env` |
| Local DB address | `127.0.0.1:5432`, data dir `C:\Users\gandrade\PostgreSQL\qr_data` |
| Local DB backups / refresh scripts | `C:\Users\gandrade\OneDrive - UBC\Documents\PostgreSQL_vm_backup` |
| Frozen SQLite rollback | `asset_capture_app_dev/data/QR_codes.db` |
| Captured images | `Capture_photos_upload/` |
| Extracted / review JSON | `Output_jason_api/` |
| SDI validation output | `SDI_process/sdi_json_output/` |
| Shared auth configuration | `auth_service.env` (`DATABASE_URI`, `SECRET_KEY`, session cookie domain) |
| Auth service code | `auth_service/` |

**Caution:** the folder name `Output_jason_api` is a historical spelling (`jason` instead of `json`). It is referenced from many places — DB rows, scripts, logs, downstream tools. **Do not rename it casually.** A rename requires coordinated changes across all services and is out of scope for routine work.

---

## 6. Important Database Tables

Stored in PostgreSQL `qr_code_db` (operational). The legacy SQLite `QR_codes.db` is a frozen rollback copy, not the live workflow database. Read `.agent_app/01_GLOBAL_RULES.md` and `02_SYSTEM_MAP.md` before modifying schemas or queries.

- `QR_codes` — QR-level state: approval, AI status, SDI exclusion, location.
- `QR_code_assets` — per-asset process state, image relationships, tab placement, audit fields (`user`, `date_hour`).
- `sdi_dataset` — curated SDI records for **Mechanical and Backflow**.
- `sdi_dataset_EL` — curated SDI records for **Electrical** (canonical Planon-facing fields live here).
- `sdi_print_out` — active SDI packages prepared for Planon export.
- `sdi_print_out_arch` — archived SDI packages.
- `json_files` — JSON sync and maintenance bookkeeping.
- `new_device` — FLS / new device records with Planon ticket columns (request open, request date, elapsed time, complete, ticket number); `Attribute Set` defaults to `FireAlarmDevice`; populated `Planon Code` rows remain editable but cannot be deleted or bulk-selected.
- `Buildings_with_SpaceUID` — location and SpaceUID lookup.
- `life_cycle` — main table for the Life Cycle Assessment feature. **Rebuilt on every "Update Database" run** (`to_sql(if_exists="replace")`) from the Excel workbook filtered to Asset Group Code `ME.91.902.4817.5956`, with Floor Name joined from SpaceUID. All columns TEXT except `years` (float) and `months` (integer).
- `space_floor` — deduplicated `(Property Code, Space Number) -> Floor Name` reference table with a PRIMARY KEY, rebuilt on every load; `life_cycle` carries a composite FK (`life_cycle_space_floor_fkey`) referencing it.
- `life_cycle_meta` — small key/value table that survives the `life_cycle` rebuild; stores the `last_loaded` timestamp shown in the dashboard footer.

---

## 7. Discipline-Specific Rules

Mechanical, Electrical, and Backflow do **not** share completeness, confidence, review, or SDI rules. Treat them independently.

### Mechanical (ME)

- Sequence **`-0`** owns: Manufacturer, Model, Serial Number, Year.
- Sequence **`-1`** owns: UBC Tag.
- Sequence **`-3`** owns: Technical Safety BC.
- Sequence **`-4`** is the optional **Extra Photo** slot. Captured and displayed but excluded from completeness, AI confidence, AI extraction (`VALID_SUFFIXES`), and "Missed Photo".
- Do **not** borrow values across sequences. If the owning image is missing or has no evidence, leave the field blank.

### Backflow (BF)

- Uses the shared `sdi_dataset` table.
- Core completeness fields: Manufacturer, Model, Serial Number, Diameter.
- Sequence **`-3`** is the optional **Extra Photo** slot — same exclusion rules as ME `-4`.

### Electrical (EL)

- Uses the dedicated `sdi_dataset_EL` table.
- Core extraction / review fields: UBC Asset Tag, Ampere, Supply From.
- Canonical **Planon-facing** fields: Amperage Rating, Voltage Rating, Equipment ID, Equipment Type, Fed From Equipment ID, Power Type.
- **Rating storage convention (2026-07-08/09):** rating values are stored bare (`208/120`, `100`); units live in the `(UoM)` columns — `VLT`/`AMP` are the intentional Planon UoM codes on the SDI tables, while `electrical_building_schema` stores display units `V`/`A`. Write paths strip unit letters from values; display layers add the units.
- Confidence averages exclude `Volts`, `Location`, and `Branch Panel`.
- Sequence **`-3`** is the optional **Extra Photo** slot — same exclusion rules as ME `-4`.
- **Single Line Diagram (SLD) panel** is part of the EL Distribution view. It renders rows from `electrical_building_schema` (the diagram side) alongside their matching `sdi_dataset_EL` row (the captured-asset side) and exposes an inline "Swift Over" editor. The rightmost **Reconciliation** column flags rows where the two sides disagree on `Building | Equipment ID | Supply From` (the composite key `ID_check`).
- **`ID_check` is a PostgreSQL `GENERATED ALWAYS AS (...) STORED` column on both `sdi_dataset_EL` and `electrical_building_schema`.** Writes to it are rejected by the DB — the column always reflects the live composite of `Building | UBC Asset Tag (or Equipment ID) | Supply From`. Code that previously maintained `ID_check` by hand was removed in May 2026; do not reintroduce manual writes. The old SQLite `VIRTUAL` form is rollback/reference history only.
- **Reconciliation workflow** — clicking a red `Reconciliation` cell opens a modal with three choices: *Diagram is correct* (pushes the SLD `Supply From` into `sdi_dataset_EL` + the captured-asset JSON), *Captured asset is correct* (pulls the SDI value back into the SLD), or *Custom* (writes a user-entered value to both). Endpoint: `POST /sld/api/assets/<row_id>/reconcile`. Each side that changes emits an `audit_trail` row (`source="human"`, `description="reconcile:<choice>"`). See `Markdowns_documentation/workflows/03_review_and_approve.md` for the full flow.

---

## 8. Do Not Break These Rules

These are high-risk invariants. Violating them has caused real production issues.

1. **Use parameterized SQL only.** No string-formatted query construction.
2. **Stay portable between Windows dev and Ubuntu production paths.** Do not hardcode OS-specific separators or absolute paths beyond existing conventions.
3. **Do not assume one completeness formula fits all disciplines.** ME, BF, and EL each have their own rules.
4. **Review approval must sync to the correct SDI table.** ME/BF → `sdi_dataset`; EL → `sdi_dataset_EL`.
5. **Packaged assets must remain approved in review source state.** If a QR exists in `sdi_print_out` or `sdi_print_out_arch`, automated ME/BF/EL sync must preserve/coerce JSON `Approved = "True"` and source-table `Approved = "1"`; do not add `Approved` to package tables.
6. **SDI package route moves must preserve package integrity.** Retrieve and Exclude preserve source/JSON approval, Archive is allowed only after Planon export (`print_out = 1` for every selected row), and package actions must write audit rows.
7. **SDI package database guardrails are mandatory after migration.** `scripts/migrate_sdi_package_db_guardrails.py` installs unique normalized QR indexes and triggers that block duplicate/overlapping package QRs, blank package keys, unapproved packaged source state, unexported archive inserts, and deletion/unapproval of packaged QR rows. Full foreign-key table rebuild is intentionally deferred.
8. **Manual Entry must align with SDI exclusion state.** `QR_code_assets.Col_process = 2`, JSON `ExcludeSDI`, and `QR_codes.sdi = 1` must remain consistent — Manual Entry is a state, not just a visual tab.
9. **QR rename and parameter update flows must be atomic** across DB rows, JSON filenames, image filenames, and processed logs. On failure, roll back rather than leaving the system in a split state.
10. **Do not erase human review overrides.** Re-running AI extraction must not silently overwrite values a reviewer has confirmed.
11. **Never use `eval()` for dictionary editing.** Use `ast.parse()` / `ast.literal_eval()` for reads; `json.dumps()` for writes.
12. **Chained AI extraction must go through `API/run_ai_and_sync.sh`.** It guarantees the DB sync step runs after a successful extraction. The standalone "update DB" launcher has been removed.
13. **Dashboard tables and charts are read-only views, not the source of truth.** When in doubt, trust the curated DB tables and JSON files over what the Dashboard renders.
14. **Respect environment separation.** Local development paths and Ubuntu production paths differ — do not collapse them.

---

## 9. Dashboard Architecture Notes

The Dashboard is the **unified shell**. It embeds the ME, BF, EL, and SDI sub-apps inside iframe panels and routes between views using hash-based navigation (`#review-me-view`, `#review-bf-view`, `#review-el-view`, `#sdi-view`).

- All four sub-apps (ME, BF, EL, SDI) and the Dashboard itself detect `?embedded=true` and suppress their own navbar/brand/user-dropdown chrome when embedded. Functional controls (building selector, filters, approve toggle, back-to-list) remain visible. The `before_request` hook sets `g.embedded` on every request and the body element gains an `embedded-mode` class as a CSS fallback.
- Internal links inside a sub-app propagate `?embedded=true` via a bottom-of-body click interceptor so DataTables links and dynamic rows keep embedded state.
- Cross-frame navigation back to the Dashboard main view uses `window.parent.postMessage({action:'go-to-main'}, 'https://dashboardprod.assetcap.facilities.ubc.ca')`. The target origin must match exactly or the message is silently dropped.

### Life Cycle Assessment (in-process blueprint)

Life Cycle Assessment is a **Dashboard sub-feature**, not a separate service or port. It is an in-process Flask Blueprint (`life_cycle`) mounted inside `Asset_portal_dashboard.py` at `url_prefix` `/life-cycle`, and runs as part of the `assetcap-dashboard` service (Gunicorn, port 8002). It shows asset age / life-cycle data for the Mechanical asset group `ME.91.902.4817.5956` (Heating Water Storage Tanks), split into Complete / Incomplete tabs with an Age Classification **donut chart** (Good <=8 yrs, Caution 8-10 yrs, Critical >=10 yrs, Unknown for no installation date) plus a companion **Life-cycle Expiry** bar chart (X = installation year + 10, the year an asset reaches 10 yrs of service; Y = years in service with a dashed 10-yr line; bars coloured by age band).

- Code lives in the `Dashboard/life_cycle/` package (`__init__.py`, `blueprint.py`, `excel_export.py`, blueprint static + `templates/life_cycle/dashboard.html`). The data pipeline (`track_assets.py`, `load_life_cycle.py`, source workbook) lives in the sibling `life_cycle_pipeline/` package.
- Routes under `/life-cycle`: `GET /` (HTML page; requires login + `lifecycle_assessment` viewer permission), `POST /export` (styled XLSX of the visible rows), `POST /refresh` (uploads an Excel workbook and rebuilds the tables — **destructive: drops/rebuilds `life_cycle` + `space_floor`, needs DDL privileges**), `GET /health` (open liveness probe, no auth). Blueprint static is served at `/life-cycle/static/...`.
- Registration is wrapped in try/except so a missing dependency degrades to "feature absent" without crashing the portal. The blueprint + pipeline add `pandas`, `numpy`, `sqlalchemy`, `openpyxl`, `psycopg2(-binary)`, and `Pillow` on top of Flask.
- A row is **Complete** only when Make, Space Number, Serial Number, AND Installation Date are all present; otherwise Incomplete. At read time the page derives a "Captured" flag (QR present in `QR_codes` with a `date_set` — i.e. field-captured) and a "Capture Date" (`QR_codes.date_set`) per row from those read-only tables.
- The DB connection derives from `LIFE_CYCLE_DSN` if set, else `QR_PG_DSN`, else the dev sandbox default; the SQLAlchemy URL `LIFE_CYCLE_SA_DSN` (used by the pipeline) is derived from that libpq DSN at blueprint import.

### RBAC and navigation for Life Cycle Assessment

- RBAC: a new section `operations` / item `lifecycle_assessment` was added to `auth_service/app_registry.py`. Routes enforce it server-side via `has_permission` / `require_permission` (same model as FLS Devices; the sidebar link itself is not visibility-gated). Granted per-user (viewer/editor) through the Dashboard User Admin screen.
- Navigation: a "Life Cycle Assessment" item was added to the shared shell sidebar (`Dashboard/static/shell/shell.js`) under the **Operations** group, directly below "FLS Devices" (icon `activity`), linking to the standalone `/life-cycle/` page. The nav entry + breadcrumb (Home / Operations / Life Cycle Assessment) were propagated to all five `shell.js` copies (Dashboard, ME/BF/EL reviewers, SDI Process); shell assets are cache-busted via the `?v=` query in each app's `_shell.html`.

### Review-app bulk actions (BF + EL)

The BF and EL review-app tab tables (New / Update / Manual) carry **bulk Manual** and **bulk Approved** master checkboxes in the column headers. Implementation rules:

- The checkboxes drive a client-side queue that calls the existing per-row endpoints `POST /toggle_sdi/<doc_id>` and `POST /toggle_approved/<doc_id>` — there is no dedicated bulk endpoint.
- Confirmation goes through a shared Bootstrap `#confirmModal` (`showConfirm(title, message, cb)` helper), not the native browser `confirm()`.
- The `#confirmModal` and the other review-app modals (`#infoModal`, `#planonModal`) use `modal-dialog-centered` so the dialog is vertically centered and not clipped by the Dashboard's sticky top bar when embedded.
- Client-side safety filters:
  - Bulk-Manual **skips rows where `Approved = True`** (cannot Manual-flag an already-approved row).
  - Bulk-Approved **un**check skips rows where `Manual = 1` (`data-val="1"`) or `ai_status` is `exported` / `2`.
- Header checkboxes resync on every DataTables `draw.dt` so they reflect the actual state of currently visible rows after filtering or pagination.
- **ME does not yet have the bulk-checkbox UI.** Its `/toggle_sdi` and `/toggle_approved` endpoints exist, so a future cross-app parity change is possible without backend work.

---

## 10. Authentication Notes

All apps share authentication through the `auth_service` module:

- Shared environment file (`auth_service.env`) supplies `SECRET_KEY`, `DATABASE_URI`, and `SESSION_COOKIE_DOMAIN`.
- Session and remember cookies are issued with `SameSite=None; Secure` so they can be shared across the `assetcap.facilities.ubc.ca` subdomains in production.
- Login, logout, and session validation behavior is common across services — do not reimplement per-app.

---

## 11. Documentation Rules

- The **canonical documentation** lives under `Markdowns_documentation/`. Start with `00_README.md`, then `01_GLOBAL_RULES.md`, `02_SYSTEM_MAP.md`, `03_ARCHITECTURE_MAP.md`.
- `.agent_app/` is a **synchronized mirror** of the canonical set, kept aligned by hand.
- Each module also has a service-local agent folder (`.agent/`, `.auth_agent/`, or `.agent_dictionary/`) with its own `AGENT.md`, `RULES.md`, and `skills/` + `workflows/` subdirectories.
- **When code behavior changes, the matching documentation should be updated in the same workstream.** Do not let docs drift.

---

## 12. How an AI Assistant Should Work in This Repository

Practical guidance for any future AI coding agent picking up a task here:

1. **Start by reading `README.md`** and the relevant files under `Markdowns_documentation/`.
2. **Read the service-local `.agent` documentation** for the module you are about to touch.
3. **Do not make broad refactors** unless the user has explicitly asked for one.
4. **Preserve existing paths, filenames, database tables, and field names** unless the task explicitly requires a migration.
5. **When changing database logic**, check downstream effects on Dashboard, Review Apps, SDI Process, and Planon export.
6. **When changing extraction logic**, check discipline-specific completeness and confidence rules.
7. **When changing review logic**, verify both JSON sync and curated SDI table sync.
8. **When changing Dashboard behavior**, verify iframe / embedded behavior and shared authentication.
9. **Prefer small, traceable changes** — one workflow, one PR.
10. **Clearly document assumptions and the steps you used to test the change.** If you cannot test something end-to-end locally, say so explicitly rather than claiming success.

---

## 13. Local / Production Awareness

This project runs in two environments:

- **Local development** on Windows (current working directory paths).
- **Production** on Ubuntu, behind subdomains under `assetcap.facilities.ubc.ca`, with services separated by module / domain / port.

Avoid hardcoding paths unless an existing convention already does so, and respect the production service separation by module, domain, and port. When you are unsure how a change will manifest in production, surface that uncertainty rather than guessing.

---

## 14. VM / Production Application Settings

The production VM hosts every Flask app behind Nginx with Gunicorn under systemd. The canonical reference is `.agent_app/assetcap_setup_manual.md` (also surfaced via `Markdowns_documentation/`).

### Server baseline

- Host: `142.103.68.1` — `ssh developer@142.103.68.1`
- OS: Ubuntu 22.04 LTS
- Linux user: `developer`
- Reverse proxy: Nginx 1.24
- App server: Gunicorn via systemd
- TLS: Let's Encrypt / Certbot
- Database: PostgreSQL `qr_code_db` (single operational DB shared across services)
- Python isolation: one `venv` per app

### Production service inventory

| App | Service | Port | Domain | Server Path | Gunicorn Target |
|---|---|---:|---|---|---|
| Capture App | `assetcap-app.service` | 8000 | `appprod.assetcap.facilities.ubc.ca` | `/home/developer/asset_capture_app_dev` | `app:app` |
| ME Review | `assetcap-reviewme` | 8001 | `reviewme.assetcap.facilities.ubc.ca` | `/home/developer/review/Asset_dasboard_browser_ME` | `asset_plate_reviewer:app` |
| Dashboard | `assetcap-dashboard` | 8002 | `dashboardprod.assetcap.facilities.ubc.ca` | `/home/developer/Dashboard` | `Asset_portal_dashboard:app` |
| SDI Process | `sdi_process` | 8003 | `sdiprocess.assetcap.facilities.ubc.ca` | `/home/developer/SDI_process` | `app:app` |
| BF Review | `assetcap-bf` | 8004 | `reviewbf.assetcap.facilities.ubc.ca` | `/home/developer/review/Asset_dasboard_browser_BF` | `asset_plate_reviewer_bf:app` |
| EL Review | `assetcap-el` | 8005 | `reviewel.assetcap.facilities.ubc.ca` | `/home/developer/review/Asset_dashboard_browser_EL` | `Asset_dashboard_EL:app` |
| Auth Service | not mapped | — | — | `/home/developer/auth_service` | scripts / helpers |

**Local vs production ports differ for some apps.** Capture App (local 5001 / prod 8000), ME Review (local 5002 / prod 8001), and BF Review (local 5004 / prod 8004) are remapped in production. EL Review (8005), Dashboard (8002), and SDI Process (8003) keep the same port in both environments. When debugging routing or logs, confirm which environment's port you are looking at.

### Database conventions

- VM operational database: PostgreSQL `qr_code_db` on `127.0.0.1:5433`
- VM PostgreSQL data directory: `/home/developer/QR_database/pgdata`
- VM backend env file: `/home/developer/db_backend.env`
- VM app DSN convention: `QR_PG_DSN="host=127.0.0.1 port=5433 dbname=qr_code_db user=assetcap_app password=<password>"`
- Local development database: PostgreSQL `qr_code_db` on `127.0.0.1:5432`
- Local PostgreSQL data directory: `C:\Users\gandrade\PostgreSQL\qr_data`
- Local PostgreSQL binaries: `C:\Users\gandrade\PostgreSQL\pgsql\bin`
- Local backup / refresh folder: `C:\Users\gandrade\OneDrive - UBC\Documents\PostgreSQL_vm_backup`
- Local connect command: `C:\Users\gandrade\PostgreSQL\pgsql\bin\psql.exe -h 127.0.0.1 -p 5432 -U postgres -d qr_code_db`
- Preferred local refresh / verification launcher: `C:\Users\gandrade\OneDrive - UBC\Documents\PostgreSQL_vm_backup\Refresh_local_database\run_refresh_local_postgres_from_vm.bat`
- Versioned source routine: `python scripts\refresh_local_postgres_from_vm.py`
- Frozen SQLite rollback: `/home/developer/asset_capture_app_dev/data/QR_codes.db` on the VM; `asset_capture_app_dev/data/QR_codes.db` in the repo.

### Key server paths

- Operational DB: PostgreSQL `qr_code_db` on `127.0.0.1:5433`
- Frozen SQLite rollback DB: `/home/developer/asset_capture_app_dev/data/QR_codes.db`
- Captured images: `/home/developer/Capture_photos_upload`
- Extraction / review JSON: `/home/developer/Output_jason_api`
- SDI validation output: `/home/developer/SDI_process/sdi_json_output`
- Operational logs: `/home/developer/logs/`
- Shared auth config: `/home/developer/auth_service.env`

### Common admin commands

```bash
# Status / logs for a single service
sudo systemctl status assetcap-dashboard --no-pager
sudo journalctl -u assetcap-dashboard -n 100 --no-pager
sudo ss -ltnp | grep ':8002'

# Validate and reload Nginx after config changes
sudo nginx -t && sudo systemctl reload nginx

# Restart all application services
sudo systemctl restart \
  assetcap-app.service assetcap-reviewme assetcap-bf \
  assetcap-el assetcap-dashboard sdi_process
```

### Deployment flow (per app)

```bash
cd /home/developer/<app-folder>
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart <service>
sudo journalctl -u <service> -n 50 --no-pager
```

### Cross-subdomain iframe configuration

The Dashboard embeds the sub-apps in iframes from different subdomains. Two configuration layers must remain in place — break either and the embedded views silently fail to authenticate:

1. **Flask cookie config (every app):** `SESSION_COOKIE_SAMESITE='None'`, `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, and the equivalent `REMEMBER_COOKIE_*` triple. `SameSite=None; Secure` is mandatory for the browser to send the shared session cookie inside a cross-subdomain iframe.
2. **Nginx CSP `frame-ancestors` header (every site):** each sub-app site allows `https://dashboardprod.assetcap.facilities.ubc.ca`; the Dashboard site itself uses `frame-ancestors 'none'`. The `always` flag is required so headers survive 302 login redirects.

Cookie or template changes are only picked up on Gunicorn restart, and templates need a hard browser refresh (`Ctrl+Shift+R`) after Dashboard HTML/JS edits.

### Scheduled jobs

Production automation runs from the `developer` crontab — notably `run_update_db.sh` (logs to `/home/developer/logs/update_db.log`) and `ai_check.sh`. If you touch either, add `flock` at the cron line to prevent overlap before relying on worker-level concurrency limits.

### Auth user management

User management and Role-Based Access Control (RBAC) are primarily handled through the **User Administration** tab inside the Dashboard UI. A Dashboard Admin can create users, reset passwords, and assign permissions (Viewer / Editor / Admin) per integrated process directly in the browser.

The terminal scripts under `/home/developer/auth_service` remain available as a fallback for bootstrapping the initial admin account or recovering a locked-out user:

```bash
cd /home/developer/auth_service
source venv/bin/activate
python3 init_db.py <username> <email>
python  reset_password.py <username> <new_password>
deactivate
```

> **Do not assume production ports, paths, or services match this table without checking.** Verify against `.agent_app/assetcap_setup_manual.md` and the live `systemctl` state before scripting against them.

---

*This file is a primer, not a specification. For anything load-bearing, verify against the canonical documentation in `Markdowns_documentation/` and the live code.*
