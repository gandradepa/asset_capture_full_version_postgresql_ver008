# AssetCap Server Setup and Maintenance Manual

Current documentation refresh: 2026-04-28.

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
- SDI Process packages from curated DB rows and excludes rows where `QR_codes.sdi = 1`.
- SDI Process supports package archive, retrieval, exclusion, validation log browsing, and Planon export.

## Auth Service

Create or reset users from `/home/developer/auth_service`:

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
