---
description: Start and operate the UBC Asset Management Dashboard locally (Windows) or on production (Ubuntu).
---

# Run Dashboard

Current documentation refresh: 2026-04-28.

## Prerequisites

- Python 3.10+ with `venv`
- PostgreSQL `qr_code_db` accessible through `db.py` / `DB_BACKEND=postgres`; `QR_codes.db` is rollback/reference only
- `.env` file with `SECRET_KEY`, `DATABASE_URI`, and `OPENAI_API_KEY`
- Shared `auth_service` module available in the parent directory

---

## Local Development (Windows)

### 1. Activate the virtual environment
```powershell
cd Dashboard
.\venv\Scripts\Activate.ps1
```

### 2. Install dependencies (first time or after changes)
```powershell
pip install -r requirements.txt
```

### 3. Start the Flask server
```powershell
python Asset_portal_dashboard.py
```

### 4. Open the dashboard
Navigate to `http://127.0.0.1:8002` in your browser.

### 5. Login
Use credentials stored in the `auth_service` User table (bcrypt-hashed).

---

## Production (Ubuntu Server)

### 1. SSH into the server
```bash
ssh developer@<server-ip>
```

### 2. Activate the virtual environment
```bash
cd /home/developer/Dashboard
source venv/bin/activate
```

### 3. Start with Gunicorn
```bash
gunicorn -w 4 -b 0.0.0.0:8002 Asset_portal_dashboard:app
```

### 4. Verify via systemd (if configured as a service)
```bash
sudo systemctl status dashboard.service
sudo systemctl restart dashboard.service
```

### 5. Check logs
```bash
ls -la /home/developer/Dashboard/logs/
tail -f /home/developer/Dashboard/logs/<latest_log>.log
```

---

## Quick Launch (Windows Batch)

The `start_portal.bat` script launches the Dashboard plus related apps:
```powershell
.\start_portal.bat
```

This starts:
- Asset Capture App (port 5001)
- Asset Plate Reviewer ME (port 5002)
- Asset Plate Reviewer BF (port 5003)

> Note: The Dashboard itself is NOT included in `start_portal.bat` â€” start it separately.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: auth_model` | Ensure `auth_service` directory exists in the parent of `Dashboard/` |
| Charts not rendering | Check `CHARTS_AVAILABLE` and `CHARTS_IMPORT_ERROR` in the startup logs |
| Database path errors | Verify `DATABASE_URI` in `.env` matches your OS paths |
| Port 8002 in use | Change port in `app.run(port=XXXX)` at the bottom of the main file |
| FLS charts blank | Run `python charts/fls_chart.py` manually to regenerate HTML files |
