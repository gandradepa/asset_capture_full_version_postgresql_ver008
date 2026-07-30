# AssetCap Server Setup and Maintenance Manual

Current documentation refresh: 2026-07-27.

## Server Baseline

- OS: Ubuntu 22.04 LTS
- Linux user: `developer`
- SSH: `ssh developer@142.103.68.1`
- Reverse proxy: Nginx 1.24
- App server: Gunicorn through systemd
- TLS: Let's Encrypt / Certbot
- Database: PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`, via `db.py` / `DB_BACKEND=postgres`); SQLite `QR_codes.db` is rollback/reference only and is no longer used by live QR-code workflows. Auth DB stays SQLite.
- Python isolation: one `venv` per app

## Production Services

| App | Port | Domain | systemd service | Path | Gunicorn target |
| --- | ---: | --- | --- | --- | --- |
| Asset Capture App | 8000 | `appprod.assetcap.facilities.ubc.ca` | `assetcap-app.service` | `/home/developer/asset_capture_app_dev` | `app:app` |
| ME Review | 8001 | `reviewme.assetcap.facilities.ubc.ca` | `assetcap-reviewme` | `/home/developer/review/Asset_dasboard_browser_ME` | `asset_plate_reviewer:app` |
| Dashboard | 8002 | `dashboardprod.assetcap.facilities.ubc.ca` | `assetcap-dashboard` | `/home/developer/Dashboard` | `Asset_portal_dashboard:app` |
| SDI Process | 8003 | `sdiprocess.assetcap.facilities.ubc.ca` | `sdi_process` | `/home/developer/SDI_process` | `app:app` |
| BF Review | 8004 | `reviewbf.assetcap.facilities.ubc.ca` | `assetcap-bf` | `/home/developer/review/Asset_dasboard_browser_BF` | `asset_plate_reviewer_bf:app` |
| EL Review | 8005 | `reviewel.assetcap.facilities.ubc.ca` | `assetcap-el` | `/home/developer/review/Asset_dashboard_browser_EL` | `Asset_dashboard_EL:app` |
| Auth Service | not mapped | not mapped | not mapped | `/home/developer/auth_service` | scripts/helpers |

## Repository Layout on Server

```text
/home/developer/
  asset_capture_app_dev/      # capture Flask app (PostgreSQL-backed; no live QR_codes.db writes)
  API/                        # ME/BF/EL extraction workers and DB sync
  Dashboard/                  # operations dashboard and charts
  review/
    Asset_dasboard_browser_ME/
    Asset_dasboard_browser_BF/
    Asset_dashboard_browser_EL/
  SDI_process/                # SDI package and Planon export app
  dictionary/                 # standalone dictionary tooling
  auth_service/               # shared auth model and user scripts
  Capture_photos_upload/      # captured photos
  Output_jason_api/           # extraction/review JSON payloads
  logs/                       # operational logs
```

The `Output_jason_api` spelling is historical and must not be renamed without a coordinated code/data migration.

## Shared Data

- Main DB: PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`, via `db.py` / `DB_BACKEND=postgres`); legacy SQLite `/home/developer/asset_capture_app_dev/data/QR_codes.db` is rollback/reference only and is no longer used by live QR-code workflows
- Capture images: `/home/developer/Capture_photos_upload`
- JSON payloads: `/home/developer/Output_jason_api`
- SDI validation logs/output: `/home/developer/SDI_process/sdi_json_output`
- Auth database: configured through `/home/developer/auth_service.env`

### PostgreSQL logout safeguard

The AssetCap PostgreSQL service is `qr-postgres.service`. It runs as OS user `developer` from `/home/developer/QR_database/pgdata`, listens on port 5433, and places its Unix socket in `/tmp`.

Because the cluster uses POSIX dynamic shared memory, production keeps user lingering enabled:

```bash
loginctl show-user developer -p Linger
stat /var/lib/systemd/linger/developer
```

Expected: `Linger=yes`. Do not disable it while `qr-postgres.service` retains this ownership/configuration; otherwise systemd-logind's default `RemoveIPC=yes` can remove live `/dev/shm/PostgreSQL.*` objects after the last logout.

Health checks must execute SQL, not only `pg_isready`:

```bash
psql -X -h /tmp -p 5433 -U developer -d qr_code_db \
  -v ON_ERROR_STOP=1 -c 'SELECT 1;'
```

See `Markdowns_documentation/ubuntu_server_runbook.md` for the error signature and recovery procedure.

## Common Commands

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl status assetcap-dashboard --no-pager
sudo journalctl -u assetcap-dashboard -n 100 --no-pager
sudo ss -ltnp | grep ':8002'
```

Restart all application services:

```bash
sudo systemctl restart assetcap-app.service
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-dashboard
sudo systemctl restart sdi_process
```

## Deployment Flow

```bash
cd /home/developer/<app-folder>
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart <service>
sudo journalctl -u <service> -n 50 --no-pager
```

Use the service inventory table to pick the correct folder and service name.

## Scheduled Jobs

The known production automation uses the `developer` crontab:

- `run_update_db.sh` writes DB sync logs to `/home/developer/logs/update_db.log`
- `ai_check.sh` runs AI extraction checks

Recommended concurrency check:

```bash
grep -nE 'flock|lock|pidfile|pgrep|kill -0|already running' /home/developer/ai_check.sh
pgrep -af ai_check.sh
```

If overlap is possible, add `flock` at the cron line rather than relying only on worker-level concurrency limits.

## Current Application Notes

- Capture app writes authenticated `user` and `date_hour` audit values into `QR_code_assets`.
- Capture app writes elapsed-time JSON artifacts named `*_et.json`.
- Dashboard launches extraction through `API/run_ai_and_sync.sh`, which runs AI extraction and database sync together.
- Dashboard serves `/api/asset-photo/<qr_code>` for captured image lookup.
- Dashboard manages FLS assets in `new_device` and ensures Planon checklist columns at startup.
- FLS assets default and normalize `new_device."Attribute Set"` to `FireAlarmDevice`, backed by `"Attribute"` label `Electrical/FLS - Fire Alarm Device`.
- FLS assets with a populated `Planon Code` remain editable for corrections, but delete and bulk selection are blocked.
- Dashboard derives FLS Control Panel Code/Description from `"UBC - Asset Data Master Info"` by building property code; values are display-only and multi-match properties are flagged.
- SDI Process packages from curated DB rows and excludes rows where `QR_codes.sdi = 1`.
- SDI Process supports package archive, retrieval, exclusion, validation log browsing, and Planon export.
- Dashboard hosts the ME, BF, EL, and SDI apps inside iframe panels. Each sub-app keeps its own port, domain, and systemd service; the embedded view is reached by visiting the sub-app domain with `?embedded=true` from inside the Dashboard iframe.

## Cross-Subdomain Iframe Configuration

The Dashboard embeds sub-apps in iframes loaded from different subdomains. Two pieces of configuration must be in place for the embedded view to work end-to-end.

### 1. Flask cookie configuration (every app)

Every Flask app (Dashboard, ME, BF, EL, SDI) sets these alongside `SESSION_COOKIE_DOMAIN`:

```python
app.config['SESSION_COOKIE_SAMESITE']  = 'None'
app.config['SESSION_COOKIE_SECURE']    = True
app.config['SESSION_COOKIE_HTTPONLY']  = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'None'
app.config['REMEMBER_COOKIE_SECURE']   = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
```

`SameSite=None; Secure` is mandatory for the browser to send the shared session cookie inside a cross-subdomain iframe. `Secure` requires HTTPS, which is satisfied by Let's Encrypt in production.

### 2. Nginx `frame-ancestors` CSP (every site)

Each sub-app site (`assetcap-reviewme`, `reviewbf`, `assetcap-el`, `sdi_process`) carries this header inside its `server { }` block:

```nginx
add_header Content-Security-Policy "frame-ancestors 'self' https://dashboardprod.assetcap.facilities.ubc.ca;" always;
add_header X-Content-Type-Options "nosniff" always;
```

The Dashboard site (`dashboardprod`) carries:

```nginx
add_header Content-Security-Policy "frame-ancestors 'none';" always;
add_header X-Content-Type-Options "nosniff" always;
```

The `always` flag is required so headers survive 302 login redirects.

After editing any `/etc/nginx/sites-available/*` file:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 3. Restart sub-app + Dashboard services

Cookie config changes are loaded only on Gunicorn restart, and template changes are read on restart by the cached Jinja loader. Restart both ends after any change to embedded behavior:

```bash
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
```

### 4. Verification

```bash
# Cookie attributes
curl -si -c /tmp/c.txt -d "username=USER&password=PASS" https://reviewme.assetcap.facilities.ubc.ca/login | grep -i set-cookie
# Expected: SameSite=None; Secure; HttpOnly

# CSP header on each sub-app
for d in reviewme reviewbf reviewel sdiprocess; do
    echo "=== $d ==="
    curl -si https://$d.assetcap.facilities.ubc.ca/ | grep -i content-security-policy
done

# CSP header on Dashboard
curl -si https://dashboardprod.assetcap.facilities.ubc.ca/ | grep -i content-security-policy
# Expected: frame-ancestors 'none'
```

In the browser, `Ctrl+Shift+R` after every Dashboard template change is required to force a fresh download of the cached HTML/JS.

## Auth Service

User management and Role-Based Access Control (RBAC) are primarily handled through the **User Administration** tab within the Dashboard UI. A Dashboard Admin can Create Users, Reset Passwords, and manage permissions (Viewer, Editor, Admin) for all integrated processes directly from the browser.

The **Current Users** table supports ascending and descending sorting on **ID**, **Username**, and **Name**; Username is ascending by default and blank names remain last. Sorting preserves in-progress Name edits. In the permission matrix, **Section / Item** fits its longest displayed label while None, Viewer, and Editor share the remaining table width.

Password persistence uses the shared `auth_service/users.db` `user.password_hash` column. Admin reset and self-service change-password routes must call `User.set_password()`; assigning `current_user.password` creates an unmapped in-memory attribute and does not survive logout.

For manual terminal intervention (e.g., creating the initial admin user or resetting a locked account), use the `/home/developer/auth_service` scripts:

```bash
cd /home/developer/auth_service
source venv/bin/activate
python3 init_db.py <username> <email>
python reset_password.py <username> <new_password>
deactivate
```

## Health Checklist

- `sudo systemctl status <service> --no-pager`
- `sudo journalctl -u <service> -n 100 --no-pager`
- `sudo ss -ltnp | grep ':<port>'`
- `sudo nginx -t`
- Browser check for the mapped domain
