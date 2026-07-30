---
description: Start and operate any of the three Asset Plate Review apps (ME, EL, BF) locally (Windows) or on production (Ubuntu).
---

# Run Review App

Current documentation refresh: 2026-05-25.

## Prerequisites

- Python 3.10+ with `venv`
- PostgreSQL `qr_code_db` accessible through `db.py` / `DB_BACKEND=postgres`; `QR_codes.db` is rollback/reference only
- `auth_service.env` file with `SECRET_KEY` and `SESSION_COOKIE_DOMAIN`
- Shared `auth_service` module available in the project tree
- Image directory (`Capture_photos_upload/`) and JSON directory (`Output_jason_api/`) accessible

---

## Port Mapping

| App | Script | Default Port |
|-----|--------|-------------|
| ME  | `asset_plate_reviewer.py` | 5002 |
| BF  | `asset_plate_reviewer_bf.py` | 5003 |
| EL  | `Asset_dashboard_EL.py` | 5004 |

---

## Local Development (Windows)

### 1. Navigate to the app directory
```powershell
cd review\Asset_dasboard_browser_ME    # or _BF or _EL
```

### 2. Activate the virtual environment
```powershell
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies (first time or after changes)
```powershell
pip install -r requirements.txt
```

### 4. Start the Flask server
```powershell
# ME:
python asset_plate_reviewer.py

# BF:
python asset_plate_reviewer_bf.py

# EL:
python Asset_dashboard_EL.py
```

### 5. Open in browser
Navigate to `http://127.0.0.1:<port>` (see port mapping above).

### 6. Login
Use credentials stored in the shared `auth_service` User table (bcrypt-hashed).

---

## Production (Ubuntu Server)

### 1. SSH into the server
```bash
ssh developer@<server-ip>
```

### 2. Navigate and activate
```bash
cd /home/developer/review/Asset_dasboard_browser_ME
source venv/bin/activate
```

### 3. Start with Gunicorn
```bash
# ME:
gunicorn -w 4 -b 0.0.0.0:5002 asset_plate_reviewer:app

# BF:
gunicorn -w 4 -b 0.0.0.0:5003 asset_plate_reviewer_bf:app

# EL:
gunicorn -w 4 -b 0.0.0.0:5004 Asset_dashboard_EL:app
```

### 4. Verify via systemd (if configured)
```bash
sudo systemctl status review-me.service
sudo systemctl restart review-me.service
```

### 5. Check Nginx proxy
Ensure Nginx forwards the correct subdomain to the expected port.

---

## Starting All Review Apps (via Dashboard batch)

The Dashboard's `start_portal.bat` launches ME and BF automatically:
```powershell
cd Dashboard
.\start_portal.bat
```
This starts:
- Asset Capture App (port 5001)
- ME Review (port 5002)
- BF Review (port 5003)

> **Note**: EL Review is NOT included in `start_portal.bat` â€” start it separately.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Flask session secret (loaded from `auth_service.env`) |
| `SESSION_COOKIE_DOMAIN` | Yes | Cookie domain for SSO |
| `DB_BACKEND` / `QR_PG_DSN` | No | Operational PostgreSQL backend and libpq DSN. `QR_CODES_DB_PATH` is legacy rollback-only. |
| `OUTPUT_JSON_DIR` | No | Override JSON output directory |
| `UPLOAD_PHOTOS_DIR` | No | Override image upload directory |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: auth_model` | Ensure `auth_service` directory is accessible from the app's parent path |
| Cannot connect to operational DB | Check `DB_BACKEND=postgres`, `QR_PG_DSN`, and PostgreSQL service health. `QR_CODES_DB_PATH` is rollback-only. |
| Images not loading | Verify `IMG_DIR` path in startup logs; check file permissions |
| JSON files not showing | Ensure `Output_jason_api/` exists and contains files matching the type filter |
| Login fails | Check `auth_service.env` has correct `SECRET_KEY` and `SESSION_COOKIE_DOMAIN` |
| Port already in use | Change port at bottom of script: `app.run(port=XXXX)` |
| GPS service errors | Check GPS diagnostics at `/gps/diagnostics` |
