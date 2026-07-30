# UBC Asset Capture Application â€“ Server Documentation (New Server Manual)

Current documentation refresh: 2026-04-28.

## 1. Introduction

This document provides deployment and server configuration instructions for the UBC Asset Capture Application platform. The platform is a distributed monolith deployed on Ubuntu with Gunicorn and Nginx, using a shared PostgreSQL `qr_code_db` operational database (VM `127.0.0.1:5433`, via `db.py` / `DB_BACKEND=postgres`; legacy SQLite `QR_codes.db` is the frozen rollback) and filesystem stores. The auth DB remains SQLite.

## 2. Platform Architecture

### 2.1 Application Components

| Service | Port | Entry Point | Description |
|---|---|---|---|
| Capture App | 5001 | `asset_capture_app_dev/app.py` | Mobile-first photo capture, QR registration, parameter update service |
| Dashboard | 8002 | `Dashboard/Asset_portal_dashboard.py` | Operational analytics, charts, FLS management, dictionary editing |
| Review ME | 5002 | `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` | Mechanical review and approval |
| Review BF | 5003 | `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` | Backflow review and approval |
| Review EL | 5004 | `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` | Electrical review and approval |
| SDI Process | 5005 | `SDI_process/app.py` | SDI packaging, Planon export, archive management |
| Auth Service | â€” | `auth_service/` | Shared authentication (Flask-Login, bcrypt, SQLAlchemy) |

### 2.2 Production URLs

| Service | URL |
|---|---|
| Capture App | `https://appprod.assetcap.facilities.ubc.ca` |
| Review ME | `https://reviewme.assetcap.facilities.ubc.ca` |
| Review BF | `https://reviewbf.assetcap.facilities.ubc.ca` |
| Review EL | `https://reviewel.assetcap.facilities.ubc.ca` |
| SDI Process | `https://sdiprocess.assetcap.facilities.ubc.ca` |
| Dashboard | Accessed via authenticated portal |

### 2.3 Shared State

- **Database**: PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`); legacy SQLite `asset_capture_app_dev/data/QR_codes.db` is the frozen rollback
- **Auth DB**: `auth_service/User_control.db`
- **Photo Store**: `Capture_photos_upload/`
- **JSON Store**: `Output_jason_api/`
- **Dictionary**: `dictionary/mechanical_dictionary.py`

## 3. Server Requirements

### 3.1 System Requirements

- **OS**: Ubuntu 22.04+ LTS
- **Python**: 3.10+
- **Memory**: Minimum 4 GB RAM
- **Disk**: Minimum 50 GB (image and JSON storage grows over time)

### 3.2 Python Dependencies

Core packages:
- Flask, Flask-Login, Flask-SQLAlchemy
- gunicorn
- python-dotenv
- Pillow (image processing)
- Tesseract OCR + pytesseract
- OpenAI SDK
- pandas
- markupsafe
- altair (for FLS charts)
- bcrypt

Install via: `pip install -r requirements.txt`

### 3.3 External Services

- **OpenAI API** â€“ Used by extraction scripts for LLM-based nameplate data extraction
- **Tesseract OCR** â€“ Must be installed system-wide (`apt install tesseract-ocr`)

## 4. Environment Configuration

### 4.1 Environment Files

- `.env` or `auth_service.env` in the project root
- Required variables:
  - `SECRET_KEY` â€“ Flask session secret
  - `DATABASE_URI` â€“ SQLAlchemy URI for auth DB (e.g., `sqlite:////home/developer/auth_service/User_control.db`)
  - `SESSION_COOKIE_DOMAIN` â€“ Cookie domain for cross-app session sharing
  - `OPENAI_API_KEY` â€“ API key for extraction

### 4.2 Path Configuration

Production paths (Ubuntu):
- Project root: `/home/developer/`
- Photo uploads: `/home/developer/Capture_photos_upload/`
- JSON output: `/home/developer/Output_jason_api/`
- Database: PostgreSQL `qr_code_db` (operational, VM `127.0.0.1:5433`); legacy SQLite rollback at `/home/developer/asset_capture_app_dev/data/QR_codes.db`
- Auth DB: `/home/developer/auth_service/User_control.db`
- Dictionary: `/home/developer/dictionary/mechanical_dictionary.py`
- Log output: `/home/developer/Dashboard/logs/`

## 5. Deployment Steps

### 5.1 Initial Setup

```bash
# Clone repository
cd /home/developer
git clone <repository-url> asset_capture_full_version

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install Tesseract
sudo apt install tesseract-ocr

# Create required directories
mkdir -p Capture_photos_upload Output_jason_api Dashboard/logs

# Set up environment file
cp auth_service.env.example .env
# Edit .env with appropriate values
```

### 5.2 Gunicorn Configuration

Each service runs as a separate Gunicorn process, managed by systemd.

Example systemd unit for Capture App:
```ini
[Unit]
Description=Asset Capture App
After=network.target

[Service]
Type=simple
User=developer
WorkingDirectory=/home/developer/asset_capture_app_dev
ExecStart=/home/developer/venv/bin/gunicorn -w 4 -b 0.0.0.0:5001 app:app
Restart=always
Environment="PYTHONIOENCODING=utf-8"
Environment="PYTHONUTF8=1"

[Install]
WantedBy=multi-user.target
```

### 5.3 Nginx Configuration

Nginx acts as a reverse proxy for all services. Example configuration:

```nginx
server {
    listen 443 ssl;
    server_name appprod.assetcap.facilities.ubc.ca;
    
    ssl_certificate /etc/ssl/certs/assetcap.crt;
    ssl_certificate_key /etc/ssl/private/assetcap.key;
    
    client_max_body_size 30M;
    
    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 6. AI Extraction Services

### 6.1 Extraction Scripts

- `API/API_interface_ME_ver00.py` â€“ Mechanical extraction
- `API/API_interface_BF_ver00.py` â€“ Backflow extraction
- `API/API_interface_EL_ver00.py` â€“ Electrical extraction
- `API/updating_process_database.py` â€“ JSON-to-DB sync
- `API/validators_shared.py` â€“ Shared validation utilities

### 6.2 Chained Execution

AI extraction now uses chained execution via `API/run_ai_and_sync.sh`:

```bash
#!/bin/bash
# run_ai_and_sync.sh - Chains AI extraction â†’ DB sync
python3 "$1"
python3 /home/developer/API/updating_process_database.py
```

The Dashboard launches this script instead of separate AI and DB sync tasks.

### 6.3 Dashboard Launchers

The Dashboard provides launch buttons for:
- AI Interpreter â€“ Mechanical (chained)
- AI Interpreter â€“ Backflow (chained)
- AI Interpreter â€“ Electrical (chained)

Log files: `Dashboard/logs/<script_stem>.<timestamp>.log`

## 7. Database Schema

### 7.1 Core Tables

| Table | Purpose |
|---|---|
| `QR_codes` | QR-level state (approval, AI status, SDI exclusion, location) |
| `QR_code_assets` | Per-photo records with process placement, `user`, `date_hour` audit |
| `json_files` | Extraction sync and JSON summary |
| `sdi_dataset` | Curated ME and BF approved rows |
| `sdi_dataset_EL` | Curated EL approved rows |
| `sdi_print_out` | Active SDI packages |
| `sdi_print_out_arch` | Archived SDI packages |
| `new_device` | FLS asset tracking with Planon checklist columns |
| `UBC - Asset Data Master Info` | FLS Control Panel Code/Description lookup by building property code |
| `Buildings_with_SpaceUID` | Building and location lookup |
| `temp_code` | Temporary QR code management |

### 7.2 Schema Migrations

The Dashboard performs automatic schema migration at startup:
- `_ensure_new_device_columns()` adds Planon checklist columns to `new_device`
- FLS Control Panel Code/Description is display-only, derived from `"UBC - Asset Data Master Info"`, and not added to `new_device`
- Capture app auto-creates `user` and `date_hour` columns in `QR_code_assets`

## 8. New Features Reference

### 8.1 FLS Asset Management
- CRUD operations via Dashboard (`/add-fls-assets`, `/delete-fls-assets`, `/bulk-update-assets`)
- `new_device` table with Planon checklist columns
- `Attribute Set` defaults to `FireAlarmDevice`; Planon-coded rows remain editable but cannot be deleted or bulk-selected
- `"UBC - Asset Data Master Info"` table for FLS Control Panel lookup
- FLS charts (Altair-based) for visualization

### 8.2 Dictionary Management
- Dashboard UI for AST-safe editing of `dictionary/mechanical_dictionary.py`
- Read: `ast.parse()` + `ast.literal_eval()`
- Write: `json.dumps()` with sorted keys

### 8.3 Planon Export
- SDI Process exports via `export_to_planon()`
- UBC tag parsing: `parse_ubc_tag_info()`
- Year formatting: `format_year_to_date()`
- Validation logs in `SDI_process/sdi_json_output/`

### 8.4 Parameter Update Service
- Capture App uses `utils/parameter_update_service.py`
- Atomic rename across files, JSON, and DB
- Rollback on failure

### 8.5 Photo Viewing API
- Dashboard provides `/api/asset-photo/<qr_code>` for captured photo viewing

## 9. Troubleshooting

### 9.1 Common Issues

| Issue | Solution |
|---|---|
| "No module named 'auth_model'" | Ensure `auth_service/` is on `sys.path` |
| Tesseract not found | Install: `sudo apt install tesseract-ocr` |
| Database locked / connection errors | Operational DB is PostgreSQL `qr_code_db` (:5433) — check the PG service/connections; "database locked" applies only to the SQLite rollback or the SQLite auth DB. Restart services if needed |
| Charts not loading | Check Altair installation for FLS charts |
| FLS charts unavailable | Install `altair`: `pip install altair` |
| Dictionary file not found | Ensure `dictionary/` directory exists with `mechanical_dictionary.py` |

### 9.2 Logs

- Dashboard logs: `Dashboard/logs/`
- AI check log: `/home/developer/ai_check.log`
- Gunicorn logs: systemd journal (`journalctl -u <service-name>`)
- SDI validation logs: `SDI_process/sdi_json_output/`
