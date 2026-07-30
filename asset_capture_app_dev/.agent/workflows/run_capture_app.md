---
description: Start and operate the UBC Asset Capture App locally (Windows) or on production (Ubuntu).
---

# Run Capture App

Current documentation refresh: 2026-04-28.

## Prerequisites

- Python 3.10+ with `venv`
- SQLite database `QR_codes.db` accessible at the configured path
- `.env` file with `SECRET_KEY` and `DATABASE_URI`
- Shared `auth_service` module available in the parent directory
- For camera testing: HTTPS or localhost (required by browser security)

---

## Local Development (Windows)

### 1. Activate the virtual environment
```powershell
cd asset_capture_app_dev
.\venv\Scripts\Activate.ps1
```

### 2. Install dependencies (first time or after changes)
```powershell
pip install -r requirements.txt
```

### 3. Start the Flask server
```powershell
python app.py
```

### 4. Open the app
Navigate to `http://127.0.0.1:5001` in your browser (mobile browser for camera testing).

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
cd /home/developer/asset_capture_app_dev
source venv/bin/activate
```

### 3. Start with Gunicorn
```bash
gunicorn -w 4 -b 0.0.0.0:5001 app:app
```

### 4. Verify via systemd (if configured as a service)
```bash
sudo systemctl status capture-app.service
sudo systemctl restart capture-app.service
```

### 5. Check photo upload directory
```bash
ls -la /home/developer/Capture_photos_upload/
```

### 6. Run WAL checkpoint (periodic maintenance)
```bash
bash /home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QR_CODES_DB_PATH` | `<app_root>/data/QR_codes.db` | Path to primary SQLite database |
| `JSON_OUTPUT_DIR` | `/home/developer/Output_jason_api` | Directory for elapsed-time JSON output |
| `PORT` | `5001` | Server port |
| `SECRET_KEY` | â€” | Flask session secret (required) |
| `DATABASE_URI` | â€” | Auth database connection string |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: auth_model` | Ensure `auth_service` directory exists in the parent of `asset_capture_app_dev/` |
| Camera not accessible | Ensure HTTPS or `localhost` â€” browser blocks camera over plain HTTP |
| `OperationalError: database is locked` | Run `sqlite_checkpoint.sh` to merge WAL; check for zombie connections |
| Upload directory not found | Create `/home/developer/Capture_photos_upload/` with write permissions |
| Port 5001 in use | Change port via `PORT` env var or in the `app.run()` call |
| Building list empty | Verify `Buildings` table has data in `QR_codes.db` |
| Photos not saving | Check disk space and write permissions on upload directory |
