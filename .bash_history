
Each app runs inside its own `venv`.

Common:
- `flask`, `gunicorn`, `python-dotenv`

SDI Process:
- `pandas`, `openpyxl`

Other apps may include:
- `openai`, `opencv-python`, `pillow`, `pytesseract`, `numpy`, plus dependencies (anyio/httpx/pydantic/etc.)

Typical update flow:
```bash
cd /path/to/app
source venv/bin/activate
pip install -r requirements.txt   # if present
deactivate
sudo systemctl restart <service>
```

---

## 7) Nginx Reverse Proxy & TLS (CONFIRMED)

### 7.1 Configuration layout
- Active vhosts: `/etc/nginx/sites-enabled/` (symlinks)
- Available vhosts: `/etc/nginx/sites-available/`
- `/etc/nginx/conf.d/` exists but is empty / not used

### 7.2 Active vhosts (domain → port → cert path)

| Domain | Port | Enabled link | Source file | Let’s Encrypt cert path |
|---|---:|---|---|---|
| `appprod.assetcap.facilities.ubc.ca` | 8000 | `/etc/nginx/sites-enabled/assetcap-app` | `/etc/nginx/sites-available/assetcap-app` | `/etc/letsencrypt/live/appprod.assetcap.facilities.ubc.ca/` |
| `reviewel.assetcap.facilities.ubc.ca` | 8005 | `/etc/nginx/sites-enabled/assetcap-el` | `/etc/nginx/sites-available/assetcap-el` | `/etc/letsencrypt/live/reviewel.assetcap.facilities.ubc.ca/` |
| `reviewme.assetcap.facilities.ubc.ca` | 8001 | `/etc/nginx/sites-enabled/assetcap-reviewme` | `/etc/nginx/sites-available/assetcap-reviewme` | `/etc/letsencrypt/live/reviewme.assetcap.facilities.ubc.ca/` |
| `dashboardprod.assetcap.facilities.ubc.ca` | 8002 | `/etc/nginx/sites-enabled/dashboardprod` | `/etc/nginx/sites-available/dashboardprod` | `/etc/letsencrypt/live/dashboardprod.assetcap.facilities.ubc.ca/` |
| `reviewbf.assetcap.facilities.ubc.ca` | 8004 | `/etc/nginx/sites-enabled/reviewbf` | `/etc/nginx/sites-available/reviewbf` | `/etc/letsencrypt/live/reviewbf.assetcap.facilities.ubc.ca/` |
| `sdiprocess.assetcap.facilities.ubc.ca` | 8003 | `/etc/nginx/sites-enabled/sdi_process` | `/etc/nginx/sites-available/sdi_process` | `/etc/letsencrypt/live/sdiprocess.assetcap.facilities.ubc.ca/` |

Notes:
- All domains have `listen 80` and `listen 443 ssl` blocks.
- `dashboardprod` uses `listen 443 ssl http2;`
- The HTTP (80) server blocks also proxy to the app (not only redirect), per current configs.

### 7.3 Nginx commands
```bash
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl restart nginx
```

### 7.4 Certbot
Renewal test:
```bash
sudo certbot renew --dry-run
```

---

## 8) systemd Services (Gunicorn)

### 8.1 Common commands
```bash
sudo systemctl status assetcap-dashboard --no-pager
sudo systemctl restart assetcap-dashboard
sudo systemctl enable assetcap-dashboard
sudo journalctl -u assetcap-dashboard -n 100 --no-pager
```

### 8.2 Inspect exact unit config (recommended for troubleshooting)
```bash
sudo systemctl show assetcap-dashboard -p FragmentPath,User,WorkingDirectory,ExecStart --no-pager
sudo systemctl cat assetcap-dashboard
```

After editing any unit file:
```bash
sudo systemctl daemon-reload
sudo systemctl restart <service>
```

---

## 9) Logs & Debugging

### 9.1 Service logs (systemd/journald)
Live logs:
```bash
sudo journalctl -u sdi_process -f
```

Last 100 lines:
```bash
sudo journalctl -u assetcap-dashboard -n 100 --no-pager
```

### 9.2 Check listening ports
```bash
sudo ss -ltnp | grep ':8000'
sudo ss -ltnp | grep ':8001'
sudo ss -ltnp | grep ':8002'
sudo ss -ltnp | grep ':8003'
sudo ss -ltnp | grep ':8004'
sudo ss -ltnp | grep ':8005'
```

### 9.3 Nginx logs
```bash
sudo tail -n 100 /var/log/nginx/error.log
sudo tail -n 100 /var/log/nginx/dashboardprod.error.log
```

---

## 10) Database (SQLite)

Current production database usage: **SQLite**

SQLite checkpoint script:
```bash
bash /home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
```

---

## 11) Deploy Flow (after code updates)

Example (SDI Process):
```bash
cd /home/developer/SDI_process
source venv/bin/activate
pip install -r requirements.txt   # if present
deactivate
sudo systemctl restart sdi_process
sudo journalctl -u sdi_process -n 50 --no-pager
```

Post-deploy checklist:
- `sudo systemctl status <service> --no-pager`
- `sudo journalctl -u <service> -n 100 --no-pager`
- `sudo ss -ltnp | grep :<port>`
- Validate domain in browser

---

## 12) Scheduled Automation (Cron + systemd timers)

### 12.1 Cron (user: developer) — runs every 2 minutes

Developer crontab entries:
1) DB update (with log):
- Schedule: `*/2 * * * *`
- Command:
  `*/2 * * * * /home/developer/run_update_db.sh >> /home/developer/logs/update_db.log 2>&1`

2) AI check:
- Schedule: `*/2 * * * *`
- Command:
  `*/2 * * * * /home/developer/ai_check.sh`

Notes:
- There is **no root crontab** (confirmed).
- If `ai_check.sh` output needs auditing, redirect stdout/stderr to a log file.

Useful commands:
```bash
crontab -l
sudo grep CRON /var/log/syslog | tail -n 100
tail -n 200 /home/developer/logs/update_db.log
```

### 12.2 Concurrency: internal workers vs cron overlap

Logs show runtime profiles such as `*_MAX_WORKERS=1` (ME/EL/BF) which indicates **internal concurrency control** inside a single run.

However, `*/2 * * * *` can still start a **second run** if the previous run takes longer than 2 minutes, **unless** the script implements a lock/pidfile or the cron line enforces one.

How to confirm lock protection exists:
```bash
grep -nE 'flock|lock|pidfile|pgrep|kill -0|already running' /home/developer/ai_check.sh
pgrep -af ai_check.sh
```

Defence-in-depth option (if desired): enforce lock at cron level with `flock`:
- Example (AI check + log):
  `*/2 * * * * flock -n /tmp/ai_check.lock /home/developer/ai_check.sh >> /home/developer/logs/ai_check.log 2>&1`
- Example (DB update):
  `*/2 * * * * flock -n /tmp/run_update_db.lock /home/developer/run_update_db.sh >> /home/developer/logs/update_db.log 2>&1`

### 12.3 systemd timers (OS defaults)

System timers present (examples):
- `certbot.timer` → certificate renewal
- `apt-daily.timer` / `apt-daily-upgrade.timer` → package maintenance
- `logrotate.timer` → log rotation
- `systemd-tmpfiles-clean.timer` → temp cleanup
- `dpkg-db-backup.timer` → dpkg database backup
- `fstrim.timer` → SSD trim (where applicable)
- `auditd-rotate.timer` → audit rotation (if auditd in use)

Commands:
```bash
systemctl list-timers --all
systemctl status certbot.timer --no-pager
sudo journalctl -u certbot.service -n 100 --no-pager
```

---

## 13) Auth Service (new)

Location: `/home/developer/auth_service`

Create venv + initialize DB/user:
```bash
cd /home/developer/auth_service
python3 -m venv venv
source venv/bin/activate
python3 init_db.py joao joao@example.com
deactivate
```

Reset password:
```bash
cd /home/developer/auth_service
source venv/bin/activate
python reset_password.py <username> <new_password>
deactivate
```

Note:
- Port/domain/systemd service for `auth_service` is not documented yet (not mapped in Nginx inventory above).

---

## 14) Maintenance

Disk:
```bash
df -h
```

Clean pip cache:
```bash
rm -rf /home/developer/.cache/pip
```

---

## 15) Appendix — Quick Reference

Service logs (example):
```bash
sudo journalctl -u assetcap-dashboard -n 100 --no-pager
```

Check a specific port:
```bash
sudo ss -ltnp | grep ':8003'
```

List relevant services:
```bash
systemctl list-units --type=service --all | grep -E "assetcap|sdi|auth" || true
```
EOF

# Verify it's not empty
ls -l /home/developer/assetcap_setup_manual.md
wc -l /home/developer/assetcap_setup_manual.md
head -n 5 /home/developer/assetcap_setup_manual.md
sudo systemctl restart assetcap-reviewme
clear
sudo systemctl restart assetcap-dashboard
clar
clear
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-app.service
sudo systemctl restart assetcap-dashboard
clear
sudo systemctl restart assetcap-bf
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-bf
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-reviewme
clear
[200~sudo systemctl restart assetcap-reviewme~sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-reviewme
exit
clear
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-reviewme
clear
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-reviewme
clear
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-bf
pg_dump -U developer -h localhost qr_code_db > backup_qr_code.sql
tar -czvf assetcap_backup.tar.gz /home/developer
exit
sudo apt update
sudo apt upgrade -y
sudo reboot
sudo systemctl status amazon-ssm-agent --no-pager
snap list | grep amazon-ssm-agent
snap services amazon-ssm-agent
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent --no-pager
sudo tail -n 200 /var/log/amazon/ssm/amazon-ssm-agent.log
ps aux | egrep 'apt|dpkg|unattended' | grep -v egrep
sudo lsof /var/lib/dpkg/lock-frontend || true
systemctl list-timers | grep -E 'apt|unattended' || true
sudo apt update
sudo apt install -y python3-apt
sudo fuser -v /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock || true
sudo lsof /var/lib/dpkg/lock-frontend || true
clear
sudo apt autoremove --dry-run
sudo apt autoremove -y
sudo apt -f install
test -f /var/run/reboot-required && cat /var/run/reboot-required || echo "No reboot required"
systemctl list-timers | grep -E 'apt-daily|unattended' || true
systemctl is-enabled unattended-upgrades 2>/dev/null || true
clear
who | grep migration_admin
id migration_admin
who | grep migration_admin
sudo userdel -r migration_admin
id migration_admin
clear
sudo systemctl restart assetcap-dashboard
sudo systemctl restart sdi_process
clear
sudo systemctl assetcap-reviewme
sudo systemctl restart assetcap-reviewme
sudo systemctl restart sdi_process
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-dashboard
clear
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-el
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl status assetcap-app.service assetcap-el sdi_process --no-pager
clear
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "
SELECT 'sdi_dataset_EL', COUNT(*),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating\",'')) != '' THEN 1 ELSE 0 END),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating (UoM)\",'')) != '' THEN 1 ELSE 0 END)
FROM sdi_dataset_EL
UNION ALL
SELECT 'sdi_print_out', COUNT(*),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating\",'')) != '' THEN 1 ELSE 0 END),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating (UoM)\",'')) != '' THEN 1 ELSE 0 END)
FROM sdi_print_out
UNION ALL
SELECT 'sdi_print_out_arch', COUNT(*),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating\",'')) != '' THEN 1 ELSE 0 END),
       SUM(CASE WHEN TRIM(COALESCE(\"Amperage Rating (UoM)\",'')) != '' THEN 1 ELSE 0 END)
FROM sdi_print_out_arch;
"
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el sdi_process
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl status assetcap-app.service assetcap-el sdi_process --no-pager
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl status assetcap-app.service assetcap-el
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
cat >/home/developer/electrical_equipment_rules.py <<'PY'
import re

ELECTRICAL_EQUIPMENT_TYPE_MAP = {
    "MDP": "Main Distribution Panel",
    "CDP": "Central Distribution Panel",
    "SPL": "Splitter",
    "MCC": "Motor Control Center",
    "PNL": "Panel",
    "SWBD": "Switchboard",
    "ATS": "Automatic Transfer Switch",
    "TX": "Transformer",
}

ELECTRICAL_POWER_TYPE_MAP = {
    "N": "Normal",
    "E": "Emergency",
    "S": "Standby",
    "ES": "Emergency & Standby",
    "NE": "Normal & Emergency",
    "NES": "Normal, Emergency, & Standby",
    "NS": "Normal & Standby",
}

_SYSTEM_CODE_RE = re.compile(r"NES|NE|NS|ES|N|E|S")

def _normalize_tag_text(tag_value):
    return str(tag_value or "").strip().upper()

def derive_electrical_equipment_type(tag_value):
    tag_upper = _normalize_tag_text(tag_value)
    if not tag_upper:
        return ""
    for code, name in sorted(ELECTRICAL_EQUIPMENT_TYPE_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if tag_upper.startswith(code):
            return name
    return ""

def derive_electrical_power_type(tag_value):
    tag_upper = _normalize_tag_text(tag_value)
    if not tag_upper:
        return ""

    parts = tag_upper.split("-")
    if len(parts) <= 1:
        return ""

    match = _SYSTEM_CODE_RE.search(parts[1])
    if not match:
        return ""

    system_code = match.group(0)
    return system_code if system_code in ELECTRICAL_POWER_TYPE_MAP else ""

def parse_electrical_equipment_metadata(tag_value):
    tag_upper = _normalize_tag_text(tag_value)
    if not tag_upper:
        return "", ""
    return derive_electrical_power_type(tag_upper), derive_electrical_equipment_type(tag_upper)
PY

sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl status assetcap-app.service assetcap-el sdi_process --no-pager
clear
sudo systemctl restart assetcap-el
sudo systemctl stop sdi_process assetcap-el 
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service 
sudo systemctl start assetcap-el sdi_process
sudo systemctl status assetcap-el sdi_process --no-pager
sudo systemctl start assetcap-app.service 
sudo systemctl restart assetcap-el
clear
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl status assetcap-app.service assetcap-el sdi_process --no-pager
clear
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl restart assetcap-el
clear
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
sudo systemctl status assetcap-app.service assetcap-el sdi_process --no-pager
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
sudo systemctl stop sdi_process assetcap-el assetcap-app.service
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py
python3 /home/developer/scripts/backfill_amperage_columns_sqlite.py --dry-run
sudo systemctl start assetcap-app.service assetcap-el sdi_process
clear
sudo systemctl restart assetcap-el
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
sudo systemctl restart sdi_process
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-reviewme assetcap-bf
sudo systemctl restart assetcap-el
sudo systemctl stop assetcap-app.service
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart assetcap-el
sudo apt update
sudo apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx
mkdir -p /home/developer/review/Asset_dashboard_browser_EL
cd /home/developer/review/Asset_dashboard_browser_EL
sudo systemctl cat assetcap-el
clear
ls -l /home/developer/review/Asset_dashboard_browser_EL
ls -l /home/developer/review/Asset_dashboard_browser_EL/venv/bin/gunicorn
ls -l /home/developer/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py
cd /home/developer/review/Asset_dashboard_browser_EL
source venv/bin/activate
pip install -r requirements.txt
gunicorn --bind 127.0.0.1:8005 Asset_dashboard_EL:app
sudo systemctl status assetcap-el --no-pager
sudo ss -ltnp | grep 8005
curl -I http://127.0.0.1:8005
source venv/bin/activate
pip install PyMuPDF
python -c "import fitz; print(fitz.__doc__)"
deactivate
sudo systemctl restart assetcap-el
sudo journalctl -u assetcap-el -n 100 --no-pager
echo "PyMuPDF" >> /home/developer/review/Asset_dashboard_browser_EL/requirements.txt
cd /home/developer/review/Asset_dashboard_browser_EL
source venv/bin/activate
pip install PyMuPDF
python -c "import fitz; print('fitz OK')"
deactivate
sudo systemctl restart assetcap-el
sudo journalctl -u assetcap-el -n 50 --no-pager
sed -n '1,40p' /home/developer/review/Asset_dashboard_browser_EL/electrical.dictionary.py
/home/developer/review/Asset_dashboard_browser_EL/electrical.dictionary.py
find /home/developer/review -type f | grep -Ei 'electrical.*dictionary|dictionary.*electrical'
grep -Rni "Failed to load electrical dictionary" /home/developer/review/Asset_dashboard_browser_EL
grep -Rni "electrical.dictionary" /home/developer/review/Asset_dashboard_browser_EL
clear
ls -l /home/developer/dictionary/electrical.dictionary.py
nl -ba /home/developer/dictionary/electrical.dictionary.py | sed -n '1,30p'
python3 -m py_compile /home/developer/dictionary/electrical.dictionary.py
nano /home/developer/dictionary/electrical.dictionary.py
python3 -m py_compile /home/developer/dictionary/electrical.dictionary.py
sudo systemctl restart assetcap-el
sudo systemctl status assetcap-el --no-pager
sudo journalctl -u assetcap-el -n 50 --no-pager
clear
sudo journalctl -u assetcap-el -f
sudo journalctl -u assetcap-el --since "2026-04-17 15:06:46" --no-pager
clear
sudo journalctl -u assetcap-el -f
clear
curl -I http://127.0.0.1:8005
sudo nginx -t
sudo systemctl status nginx --no-pager
curl -I http://reviewel.assetcap.facilities.ubc.ca
curl -Ik https://reviewel.assetcap.facilities.ubc.ca
clear
cd /home/developer/review/Asset_dashboard_browser_EL
source venv/bin/activate
pip install openai pydantic Pillow PyMuPDF
python -c "import fitz, openai, pydantic, PIL; print('ok')"
deactivate
sudo systemctl restart assetcap-el
sudo journalctl -u assetcap-el -n 50 --no-pager
clear
sudo systemctl status assetcap-el --no-pager
curl -I http://127.0.0.1:8005
sudo journalctl -u assetcap-el --since "5 minutes ago" --no-pager
clear
sudo journalctl -u assetcap-el -f
curl -I http://127.0.0.1:8005
curl -Ik https://reviewel.assetcap.facilities.ubc.ca
clear
cp /home/developer/auth_service.env /home/developer/auth_service.env.bak
nano /home/developer/auth_service.env
set -a
source /home/developer/auth_service.env
/home/developer/review/Asset_dashboard_browser_EL/venv/bin/python -c "import os; print('OPENAI_API_KEY set' if os.getenv('OPENAI_API_KEY') else 'missing')"
set +a
sudo systemctl restart assetcap-el
sudo systemctl status assetcap-el --no-pager
sudo journalctl -u assetcap-el -f
sudo systemctl edit assetcap-el
clear
sudo systemctl daemon-reload
sudo systemctl restart assetcap-el
sudo systemctl status assetcap-el --no-pager
sudo journalctl -u assetcap-el -f
cp /home/developer/auth_service.env /home/developer/auth_service.env.bak
nano /home/developer/auth_service.env
sudo systemctl restart assetcap-el
sudo systemctl status assetcap-el --no-pager
grep -q '^OPENAI_API_KEY=' /home/developer/auth_service.env && echo "OPENAI_API_KEY found"
tail -n 200 /home/developer/logs/app.log | grep -i sld
sudo tail -f /var/log/nginx/error.log
clear
home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
gilberto$andade
sudo systemctl restart assetcap-el
clear
clear
sudo systemctl edit assetcap-el
sudo systemctl daemon-reload
sudo systemctl restart assetcap-el
sudo systemctl status assetcap-el --no-pager
sudo systemctl cat assetcap-el
sudo cp /etc/nginx/sites-available/reviewel.assetcap.facilities.ubc.ca /etc/nginx/sites-available/reviewel.assetcap.facilities.ubc.ca.bak
clear
sudo nano /etc/nginx/sites-available/reviewel.assetcap.facilities.ubc.ca
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl status nginx --no-pager
sudo systemctl cat assetcap-el
curl -Ik https://reviewel.assetcap.facilities.ubc.ca
sudo journalctl -u assetcap-el -f
sudo tail -f /var/log/nginx/error.log
clear
sudo nginx -T | grep -n -A20 -B5 "server_name reviewel.assetcap.facilities.ubc.ca"
sudo nginx -T | grep -n -A15 -B3 "location /sld/api/process"
ls -l /etc/nginx/sites-enabled/assetcap-el
readlink -f /etc/nginx/sites-enabled/assetcap-el
clear
sudo cp /etc/nginx/sites-available/assetcap-el /etc/nginx/sites-available/assetcap-el.bak
sudo nano /etc/nginx/sites-available/assetcap-el
sudo nginx -t
sudo systemctl reload nginx
sudo journalctl -u assetcap-el -f
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
ssh developer@142.103.68.1
clear
nano /home/developer/auth_service.env
cat /home/developer/auth_service.env | grep OPENAI_API_KEY
sudo systemctl restart assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-app.service
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart sdi_process
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-el.service
clar
clear
sudo systemctl restart assetcap-el.service
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el.service
systemctl restart assetcap-el
sudo systemctl restart assetcap-el.service
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
cd /home/developer/asset_capture_full_version_ver001
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
clear
sudo systemctl restart assetcap-el assetcap-bf assetcap-reviewme
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-el assetcap-bf assetcap-reviewme
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-reviewme   # ME
sudo systemctl restart assetcap-bf         # BF
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-me
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
clear
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-reviewme
sudo systemctl restart assetcap-bf
gilbeto$andrade
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-reviewme
clear
sudo nginx -t && sudo systemctl reload nginx
sudo nano /etc/nginx/sites-available/reviewel.assetcap.facilities.ubc.ca
clear
sudo nano /etc/nginx/sites-available/reviewel.assetcap.facilities.ubc.ca
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-dashboard
sudo systemctl status assetcap-el assetcap-dashboard --no-pager
curl -si https://reviewel.assetcap.facilities.ubc.ca/ | grep -i content-security-policy
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-el
clear
sudo nano /etc/nginx/sites-available/reviewel.assetcap.facilities.ubc.ca
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-el assetcap-dashboard
systemctl is-active assetcap-el assetcap-dashboard
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-el assetcap-dashboard
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-dashboard
sudo systemctl restart sdi_process assetcap-dashboard
sudo systemctl restart assetcap-el assetcap-dashboard
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-el assetcap-reviewme assetcap-bf
clear
sudo systemctl restart assetcap-el
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-el
sudo systemctl restart assetcap-dashboard assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart sdi_process
sudo systemctl restart assetcap-dashboard assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-dashboard assetcap-el
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-dashboard assetcap-el
clear
df -h
clear
sudo systemctl restart assetcap-dashboard assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-app.service
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-app.service
clear
sudo systemctl restart assetcap-app.service
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl stop assetcap-el && systemctl is-active assetcap-el; echo "exit=$?"
curl -s http://127.0.0.1:8005/sld/api/buildings
systemctl is-active assetcap-el; echo "---"; curl -s -o /dev/null -w "HTTP %{http_code} | redirect=%{redirect_url}\n" http://127.0.0.1:8005/sld/api/buildings
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-dashboard assetcap-el
clear
sudo systemctl restart assetcap-dashboard assetcap-el
clear
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
# Confirm they all came back up
for svc in assetcap-app assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard; do     printf "%-22s " "$svc:"; sudo systemctl is-active "$svc"; done
clear
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
sudo systemctl restart assetcap-el
ssh developer@142.103.68.1
cd /home/developer/review/Asset_dashboard_browser_EL
git log -1 --oneline
cd /home/developer/review/Asset_dashboard_browser_EL
git log -1 --oneline
git status
clear
scp review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py developer@142.103.68.1:/home/developer/review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py
clear
sudo systemctl restart assetcap-el
clear
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
sudo systemctl restart assetcap-bf
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
# 1. Copy both files
scp auth_service.env developer@assetcap.facilities.ubc.ca:/home/developer/auth_service.env
scp Dashboard/Asset_portal_dashboard.py developer@assetcap.facilities.ubc.ca:/home/developer/Dashboard/Asset_portal_dashboard.py
# 2. Restart ONLY the portal service (BF app already has correct cookie settings)
sudo systemctl restart assetcap-dashboard
clear
scp auth_service.env developer@assetcap.facilities.ubc.ca:/home/developer/auth_service.env
scp Dashboard/Asset_portal_dashboard.py developer@assetcap.facilities.ubc.ca:/home/developer/Dashboard/Asset_portal_dashboard.py
cat >> /home/developer/auth_service.env << 'EOF'


/home/developer/auth_service.env
clear
cat >> /home/developer/auth_service.env << 'EOF'

# Required for cross-site iframe cookie sharing (HTTPS only)
SESSION_COOKIE_SAMESITE='None'
SESSION_COOKIE_SECURE='True'
EOF

tail -5 /home/developer/auth_service.env
python3 << 'EOF'
path = '/home/developer/Dashboard/Asset_portal_dashboard.py'
old = "app.config['SESSION_COOKIE_DOMAIN'] = os.getenv('SESSION_COOKIE_DOMAIN')"
new = """app.config['SESSION_COOKIE_DOMAIN'] = os.getenv('SESSION_COOKIE_DOMAIN')

_samesite = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
_secure_raw = os.getenv('SESSION_COOKIE_SECURE', 'False')
_secure = _secure_raw.strip().lower() in ('true', '1', 'yes')
app.config['SESSION_COOKIE_SAMESITE'] = _samesite
app.config['SESSION_COOKIE_SECURE']   = _secure
app.config['REMEMBER_COOKIE_SAMESITE'] = _samesite
app.config['REMEMBER_COOKIE_SECURE']   = _secure"""
content = open(path).read()
if "_samesite" in content:
    print("Already patched — skipping.")
elif old not in content:
    print("ERROR: target line not found — check the file manually.")
else:
    open(path, 'w').write(content.replace(old, new, 1))
    print("Done.")
EOF

sudo systemctl restart assetcap-dashboard
sudo systemctl status assetcap-dashboard --no-pager | head -20
grep -r "x-frame-options\|frame-ancestors\|content-security-policy" /etc/nginx/ 2>/dev/null | grep -i "reviewbf\|bf\|backflow" || grep -r "X-Frame\|frame-ancestors\|Content-Security" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -20
clear
grep -r "x-frame-options\|frame-ancestors\|content-security-policy" /etc/nginx/ 2>/dev/null | grep -i "reviewbf\|bf\|backflow" || grep -r "X-Frame\|frame-ancestors\|Content-Security" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -20
clear
grep -n "main-dashboard-content\|style.*display.*none\|visibility\|embedded" /home/developer/review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html | grep -v "^.*{" | head -20
curl -sk "https://reviewbf.assetcap.facilities.ubc.ca/?embedded=true"   -H "Cookie: $(grep -o 'session=[^ ]*' /proc/$(pgrep -f 'asset_plate_reviewer_bf' | head -1)/environ 2>/dev/null || echo 'session=test')"   | grep -c "ubc-facilities_logo"
clear
curl -sk "https://reviewbf.assetcap.facilities.ubc.ca/?embedded=true"   -H "Cookie: $(grep -o 'session=[^ ]*' /proc/$(pgrep -f 'asset_plate_reviewer_bf' | head -1)/environ 2>/dev/null || echo 'session=test')"   | grep -c "ubc-facilities_logo"
clear
# On your local machine, copy to server:
scp "review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html"     developer@bops-assetcap-er8p:/home/developer/review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html
# Then restart the BF service:
sudo systemctl restart asset-reviewer-bf
sudo systemctl list-units --type=service | grep -iE "bf|backflow|reviewer"
sudo systemctl restart assetcap-bf
clear
scp Dashboard/Asset_portal_dashboard.py     developer@bops-assetcap-er8p:/home/developer/Dashboard/Asset_portal_dashboard.py
scp Dashboard/templates/dashboard.html     developer@bops-assetcap-er8p:/home/developer/Dashboard/templates/dashboard.html
# Restart only the dashboard service (BF service not needed):
sudo systemctl restart assetcap-dashboard
# Check it started cleanly:
sudo journalctl -u assetcap-dashboard -n 30 --no-pager
clear
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
sudo systemctl restart assetcap-dashboard assetcap-bf
ssh developer@142.103.68.1 "sudo systemctl restart assetcap-dashboard"
sudo systemctl restart assetcap-dashboard
clear
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-mobile
sudo systemctl restart assetcap-app.service
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el sdi_process assetcap-dashboard
clear
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
sudo systemctl restart assetcap-dashboard
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
# 1. Back up the production DB FIRST (the migration's DROP is irreversible)
cd /home/developer/asset_capture_app_dev/data
ts=$(date +%Y%m%d_%H%M%S)
cp QR_codes.db "QR_codes.bak_pre_id_check_generated_$ts.db"
ls -la QR_codes.bak_pre_id_check_generated_*.db | tail -1   # confirm it exists
# 2. Pull and restart
cd /home/developer/review/Asset_dashboard_browser_EL
git pull
sudo systemctl restart assetcap-el
# 3. Verify migration ran and service is healthy
sudo journalctl -u assetcap-el -n 80 --no-pager
sudo systemctl status assetcap-el --no-pager
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "SELECT filename, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 3;"
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "SELECT name, hidden FROM pragma_table_xinfo('electrical_building_schema') WHERE name = 'ID_check';"
ls -la /home/developer/review/Asset_dashboard_browser_EL/migrations/ | grep 2026_05_11
git -C /home/developer/review/Asset_dashboard_browser_EL log --oneline -3
git -C /home/developer/review/Asset_dashboard_browser_EL branch --show-current
cd /home/developer/review/Asset_dashboard_browser_EL
git pull
ls -la migrations/ | grep 2026_05_11        # confirm file present
sudo systemctl restart assetcap-el
sudo journalctl -u assetcap-el -n 200 --no-pager | grep -E '\[migrate\]|\[sld\]'
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "SELECT filename, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 3;"
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "SELECT name, hidden FROM pragma_table_xinfo('electrical_building_schema') WHERE name = 'ID_check';"
clear
sudo systemctl restart assetcap-el
sudo journalctl -u assetcap-el -n 200 --no-pager | grep -E '\[migrate\]|\[sld\]'
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "SELECT filename, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 3;"
sqlite3 /home/developer/asset_capture_app_dev/data/QR_codes.db "SELECT name, hidden FROM pragma_table_xinfo('electrical_building_schema') WHERE name = 'ID_check';"
sudo systemctl restart assetcap-el
sudo journalctl -u assetcap-el -n 40 --no-pager | tail -20
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
sudo systemctl restart assetcap-el
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
clear
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
sudo systemctl restart assetcap-dashboard sdi_process assetcap-bf assetcap-el assetcap-reviewme
clear
sudo systemctl restart assetcap-dashboard sdi_process assetcap-bf assetcap-el assetcap-reviewme
clear
sudo systemctl restart assetcap-dashboard sdi_process assetcap-bf assetcap-el assetcap-reviewme
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-el
sudo systemctl restart assetcap-dashboard sdi_process assetcap-bf assetcap-el assetcap-reviewme
clear
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-el
sudo systemctl restart assetcap-dashboard sdi_process assetcap-bf assetcap-el assetcap-reviewme
sudo systemctl restart assetcap-reviewme assetcap-bf assetcap-el
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el 
sudo systemctl restart assetcap-dashboard assetcap-reviewme
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el 
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
clear
sudo systemctl restart assetcap-app.service assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard sdi_process
sudo systemctl restart assetcap-dashboard
clar
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
cd /home/developer/asset_capture_full_version_ver003/developer  # or your VM path
git checkout new_design_for_mobile
git pull origin new_design_for_mobile
clear
cd /home/developer/asset_capture_app_dev
git remote -v
git branch -a
git log --oneline -3
clear
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
git branch --show-current
git log -1 --oneline
git status
ls -la PRODUCT.md DESIGN.md 2>&1 | head -3
clear
cd /home/developer/asset_capture_app_dev
# 1) Backup the 5 files I'm about to overwrite (so you can restore if needed)
mkdir -p ~/backup_pre_warm_$(date +%Y%m%d_%H%M%S)
BACKUP=~/backup_pre_warm_$(date +%Y%m%d_%H%M%S)
cp templates/base.html templates/start.html templates/capture.html templates/success.html static/css/styles.css "$BACKUP/"
echo "Backed up to: $BACKUP"
ls -la "$BACKUP"
# 2) Download my 8 files from GitHub (branch: new_design_for_mobile)
BASE="https://raw.githubusercontent.com/gandradepa/asset_capture_app_dev/new_design_for_mobile"
wget -q -O templates/base.html      "$BASE/templates/base.html"
wget -q -O templates/start.html     "$BASE/templates/start.html"
wget -q -O templates/capture.html   "$BASE/templates/capture.html"
wget -q -O templates/success.html   "$BASE/templates/success.html"
wget -q -O static/css/styles.css    "$BASE/static/css/styles.css"
wget -q -O PRODUCT.md               "$BASE/PRODUCT.md"
wget -q -O DESIGN.md                "$BASE/DESIGN.md"
wget -q -O _design_preview.html     "$BASE/_design_preview.html"
echo "Downloaded. Verify file sizes:"
wc -l templates/base.html templates/start.html templates/capture.html templates/success.html static/css/styles.css PRODUCT.md DESIGN.md
# 3) Find out how Flask is running so you know how to restart it
ps aux | grep -E "(python.*app\.py|gunicorn|flask)" | grep -v grep
sudo systemctl restart assetcap-app.service
clear
cd /home/developer/asset_capture_app_dev
# Find the most recent backup
BACKUP=$(ls -dt ~/backup_pre_warm_* | head -1)
echo "Restoring from: $BACKUP"
ls "$BACKUP"
# Restore the 5 files
cp "$BACKUP"/base.html       templates/base.html
cp "$BACKUP"/start.html      templates/start.html
cp "$BACKUP"/capture.html    templates/capture.html
cp "$BACKUP"/success.html    templates/success.html
cp "$BACKUP"/styles.css      static/css/styles.css
# Remove the 3 new files I added (harmless but tidies up)
rm -f PRODUCT.md DESIGN.md _design_preview.html
# Verify the restore (sizes should match the original VM versions you had earlier)
wc -l templates/base.html templates/start.html templates/capture.html templates/success.html static/css/styles.css
# Find how Flask is running so you know what to restart
ps aux | grep -E "(python|gunicorn|uwsgi|supervisor)" | grep -v grep
sudo systemctl restart assetcap-app.service
clear
cd /home/developer/asset_capture_app_dev
tar czf ~/current_prod_templates.tar.gz   templates/base.html   templates/start.html   templates/capture.html   templates/success.html   static/css/styles.css
ls -la ~/current_prod_templates.tar.gz
cd /home/developer/asset_capture_app_dev
# Backup
cp templates/success.html templates/success.html.bak_typo
# Apply: just removes the extra "l" in both occurrences
sed -i 's/Upload Successfull/Upload Successful/g' templates/success.html
# Verify the change
diff templates/success.html.bak_typo templates/success.html
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# 1. Did the file actually change?
grep -n "Successful" templates/success.html
# 2. Is the file still wrong? (this should return NOTHING if patch worked)
grep -n "Successfull" templates/success.html
# 3. Is Flask running and how was it started?
ps aux | grep -E "(python|gunicorn|uwsgi)" | grep -v grep
clear
cd /home/developer/asset_capture_app_dev
# Backup
cp templates/start.html templates/start.html.bak_year
# Apply — ultra-specific match to avoid hitting any other "2025"
sed -i 's/2025 University of British Columbia/2026 University of British Columbia/' templates/start.html
# Verify (should show 2026)
grep -n "University of British Columbia" templates/start.html
# Reload gunicorn workers
kill -HUP 1181594
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_3a
# Append new classes (uses fallback values so it works with current cool palette OR warm palette later)
cat >> static/css/styles.css << 'EOF'

/* === Patch 3a: Welcome strip + Header icon button (warm redesign components) === */
.welcome-strip {
  padding: 0.5rem 1rem 0.5rem;
  font-size: 0.8125rem;
  color: var(--color-text-muted, #64748b);
  border-bottom: 1px solid var(--color-border-subtle, #e2e8f0);
  background-color: var(--color-surface, #f8fafc);
}
.welcome-strip .container {
  max-width: none;
}
.welcome-strip strong {
  color: var(--color-text-secondary, #334155);
  font-weight: 600;
}

.header-icon-btn {
  display: inline-grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  color: var(--color-primary, #2563eb);
  background: var(--color-primary-soft, #e0e7ff);
  text-decoration: none;
  transition: background 150ms ease, transform 150ms ease, color 150ms ease;
  flex-shrink: 0;
}
.header-icon-btn:hover {
  background: var(--color-primary, #2563eb);
  color: white;
  transform: translateY(-1px);
}
.header-icon-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring, 0 0 0 3px rgba(37, 99, 235, 0.4));
}
.header-icon-btn svg {
  width: 18px;
  height: 18px;
}
EOF

# Verify the additions are at the end
tail -45 static/css/styles.css
# Static CSS doesn't need a gunicorn restart, but bust any nginx/browser cache
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp templates/base.html templates/base.html.bak_3b
# Write the patch file
cat > /tmp/patch_3b.diff << 'EOF'
--- templates/base.html
+++ templates/base.html
@@ -75,10 +75,17 @@
           {% block header_right_extra %}{% endblock %}
 
           {% if current_user.is_authenticated %}
-          <div class="flex items-center gap-2 sm:gap-4 text-xs sm:text-sm">
-            <span class="hidden sm:inline text-gray-600">Welcome, <strong>{{ username or 'User' }}</strong>!</span>
-            <a href="{{ url_for('auth.change_password') }}" class="text-blue-600 hover:underline whitespace-nowrap"
-              title="Change Password">Change Password</a>
+          <div class="flex items-center gap-2 sm:gap-3 text-xs sm:text-sm">
+            <a href="{{ url_for('auth.change_password') }}" class="header-icon-btn"
+              title="Change Password" aria-label="Change Password">
+              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
+                stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
+                <circle cx="8" cy="15" r="4"/>
+                <line x1="10.85" y1="12.15" x2="19" y2="4"/>
+                <line x1="18" y1="5" x2="20" y2="7"/>
+                <line x1="15" y1="8" x2="17" y2="10"/>
+              </svg>
+            </a>
             <a href="{{ url_for('auth.logout') }}"
               class="btn-secondary text-white py-1.5 px-2 sm:px-3 rounded text-xs sm:text-sm font-semibold"
               style="min-height: 36px;">Logout</a>
@@ -90,6 +97,14 @@
     </div>
   </header>
 
+  {% if current_user.is_authenticated %}
+  <div class="welcome-strip">
+    <div class="container mx-auto px-4">
+      Welcome, <strong>{{ username or 'User' }}</strong>
+    </div>
+  </div>
+  {% endif %}
+
   <main id="main-content" class="container mx-auto px-4 py-6 sm:py-8">
     <div class="max-w-2xl mx-auto">
       {% block content %}{% endblock %}
EOF

# Dry-run first (validates the patch will apply cleanly without changing anything)
patch --dry-run -p0 < /tmp/patch_3b.diff
# If dry-run succeeded ("checking file templates/base.html" then "Hunk #1 succeeded" + "Hunk #2 succeeded"), apply for real:
patch -p0 < /tmp/patch_3b.diff
# Reload gunicorn workers
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp templates/capture.html templates/capture.html.bak_4
# Write the patch
cat > /tmp/patch_4.diff << 'EOF'
--- templates/capture.html
+++ templates/capture.html
@@ -25,9 +25,8 @@
 
   <!-- Header -->
   <div class="mb-6 px-4 pt-2">
-    <!-- Header Removed by user request -->
     <p id="allPhotosState" class="hidden alert alert-success mt-3 mb-0">
-      All required photos are captured. Ready to submit.
+      All set. You can save when ready.
     </p>
   </div>
 
@@ -56,27 +55,25 @@
 
     <!-- Status Card (Replacement for disabled select) -->
     <div
-      class="bg-blue-50 border border-blue-100 rounded-xl p-4 mb-8 flex items-start gap-4 shadow-sm relative overflow-hidden">
-      <!-- Decorative background accent -->
-      <div class="absolute -right-6 -top-6 w-24 h-24 bg-blue-100 rounded-full opacity-50"></div>
+      class="bg-blue-50 border border-blue-100 rounded-xl p-4 mb-8 flex items-start gap-4 shadow-sm">
 
-      <div class="bg-white p-2.5 rounded-full text-blue-600 shadow-sm z-10">
+      <div class="bg-white p-2.5 rounded-full text-blue-600 shadow-sm">
         <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none"
           aria-hidden="true" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
           <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
           <polyline points="22 4 12 14.01 9 11.01"></polyline>
         </svg>
       </div>
-      <div class="flex-1 z-10">
-        <p class="text-xs font-bold text-blue-600 uppercase tracking-wide mb-1">Current Process</p>
-        <p class="text-gray-900 font-semibold text-lg leading-tight whitespace-nowrap overflow-hidden text-ellipsis">
-          {% if capture_process=='0' %}New Asset <span class="text-sm font-normal text-gray-500 inline">(QR
-            Required)</span>
-          {% elif capture_process=='1' %}Existing Asset <span class="text-sm font-normal text-gray-500 inline">(QR
-            Required)</span>
-          {% elif capture_process=='2' %}Replacing / Checking <span class="text-sm font-normal text-gray-500 inline">(No
+      <div class="flex-1">
+        <p class="text-xs font-medium text-blue-600 mb-1">You're working on</p>
+        <p class="text-gray-900 font-semibold text-lg leading-tight">
+          {% if capture_process=='0' %}A new asset <span class="text-sm font-normal text-gray-500">(QR
+            required)</span>
+          {% elif capture_process=='1' %}An existing asset <span class="text-sm font-normal text-gray-500">(QR
+            required)</span>
+          {% elif capture_process=='2' %}A replacement or check <span class="text-sm font-normal text-gray-500">(no
             QR)</span>
-          {% else %}Unknown Process{% endif %}
+          {% else %}Unknown process{% endif %}
         </p>
       </div>
 
@@ -159,7 +156,7 @@
                 <circle cx="8.5" cy="8.5" r="1.5"></circle>
                 <polyline points="21 15 16 10 5 21"></polyline>
               </svg>
-              <span class="text-xs">No photo</span>
+              <span class="text-xs">Tap to add</span>
             </div>
 
             <!-- Upload/Camera Button -->
@@ -171,7 +168,7 @@
                   <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                   <circle cx="12" cy="13" r="4"></circle>
                 </svg>
-                <span id="text_{{ name }}">Take Photo</span>
+                <span id="text_{{ name }}">Tap to snap</span>
               </div>
               <input type="file" name="{{ name }}" id="{{ name }}" accept="image/*"
                 {% if optional %}data-optional="true"{% endif %}
@@ -215,8 +212,8 @@
           Change
         </a>
         <button type="submit" id="submitBtn"
-          class="flex-1 btn btn-success text-sm sm:text-base font-bold shadow-md shadow-green-500/20 py-3 px-1 whitespace-nowrap overflow-hidden text-ellipsis">
-          Submit
+          class="flex-1 btn btn-success text-sm sm:text-base font-semibold shadow-md py-3 px-1 whitespace-nowrap overflow-hidden text-ellipsis">
+          Save and continue
         </button>
       </div>
     </div>
@@ -915,7 +912,7 @@
         <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
         <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
       </svg>
-      Submitting...
+      Saving...
     `;
     submitBtn.disabled = true;
   });
EOF

# Dry-run (must succeed before applying for real)
patch --dry-run -p0 < /tmp/patch_4.diff
# Apply only if dry-run reports all 6 hunks succeeded
patch -p0 < /tmp/patch_4.diff
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp templates/start.html templates/start.html.bak_5
# Write the patch
cat > /tmp/patch_5.diff << 'EOF'
--- templates/start.html
+++ templates/start.html
@@ -24,7 +24,7 @@
 <!-- Asset Setup Section -->
 <div class="card mb-4">
   <div class="mb-4 pb-2 border-b border-gray-200">
-    <h3 class="text-lg font-bold text-gray-800">Asset Setup</h3>
+    <h3 class="text-lg font-semibold text-gray-800">Let's find your asset</h3>
   </div>
 
   <form method="POST" action="{{ url_for('capture') }}" id="startForm">
@@ -45,20 +45,19 @@
 
     <!-- Capture Process -->
     <div class="field-group">
-      <label for="capture_process" class="block text-sm font-semibold mb-2">Capture Process</label>
+      <label for="capture_process" class="block text-sm font-medium mb-2">What are you doing?</label>
       <select name="capture_process" id="capture_process" required>
-        <option value="" {% if not capture_process %}selected{% endif %}>-- Select Capture Process --</option>
-        <option value="0" {% if capture_process=='0' %}selected{% endif %}>New Asset (QR Required)</option>
-        <option value="1" {% if capture_process=='1' %}selected{% endif %}>Existing Ones (QR Required)</option>
-        <option value="2" {% if capture_process=='2' %}selected{% endif %}>Replacing | Checking Asset (NO QR Required)
+        <option value="" {% if not capture_process %}selected{% endif %}>Pick a workflow</option>
+        <option value="0" {% if capture_process=='0' %}selected{% endif %}>Add a new asset (scan QR)</option>
+        <option value="1" {% if capture_process=='1' %}selected{% endif %}>Update an existing asset (scan QR)</option>
+        <option value="2" {% if capture_process=='2' %}selected{% endif %}>Replace or check an asset (no QR needed)
         </option>
       </select>
-      <span class="helper-text">Choose the type of asset capture workflow</span>
     </div>
 
     <!-- Select Building -->
     <div class="field-group field-with-icon">
-      <label for="building_code" class="block text-sm font-semibold mb-2">Select Building</label>
+      <label for="building_code" class="block text-sm font-medium mb-2">Which building?</label>
       <span class="field-icon">
         <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
           <path
@@ -66,35 +65,33 @@
         </svg>
       </span>
       <select name="building_code" id="building_code" required>
-        <option value="">-- Select Building --</option>
+        <option value="">Pick a building</option>
         {% for b in building_options %}
         <option value="{{ b.code }}" {% if building_code and b.code==building_code %}selected{% endif %}>{{ b.name }}
         </option>
         {% endfor %}
       </select>
-      <span class="helper-text">Choose the property where the asset is located</span>
     </div>
 
     <!-- Location -->
     <div class="field-group">
-      <label for="location" class="block text-sm font-semibold mb-2">Location</label>
-      <input type="text" id="location_search" class="w-full mb-2" placeholder="Type to filter locations"
+      <label for="location" class="block text-sm font-medium mb-2">Where in the building?</label>
+      <input type="text" id="location_search" class="w-full mb-2" placeholder="Start typing a room or space"
         autocomplete="off" />
       <noscript><input type="text" name="location_fallback" class="w-full mb-2"
-          placeholder="Enter location manually"></noscript>
+          placeholder="Type the location"></noscript>
       <div id="location_suggestions" class="w-full"
         style="max-height: 160px; overflow-y: auto; border: 1px solid #e5e7eb; border-radius: 6px; background:#fff; position: relative; z-index: 5;">
       </div>
       <div id="location_skeleton" class="skeleton skeleton-input" style="display: none;"></div>
       <select name="location" id="location">
-        <option value="">-- Select First the Property --</option>
+        <option value="">Pick a building first</option>
       </select>
-      <span class="helper-text">Select the specific space or room</span>
     </div>
 
     <!-- Asset Type -->
     <div class="field-group field-with-icon">
-      <label for="asset_type" class="block text-sm font-semibold mb-2">Asset Type</label>
+      <label for="asset_type" class="block text-sm font-medium mb-2">What kind of asset?</label>
       <span class="field-icon" id="asset_type_icon">
         <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
           <path
@@ -106,12 +103,10 @@
         <option value="Electrical" {% if asset_type=='Electrical' %}selected{% endif %}>Electrical</option>
         <option value="Backflow" {% if asset_type=='Backflow' %}selected{% endif %}>Backflow</option>
       </select>
-      <span class="helper-text">Type of equipment being captured</span>
     </div>
 
-    <!-- Identify Asset Section -->
+    <!-- Identify Asset Section (header removed — the section's purpose is self-evident) -->
     <div class="bg-gray-50 rounded-xl border border-gray-200 p-4 mb-4">
-      <h3 class="text-center font-bold text-gray-800 mb-4">Identify Asset</h3>
 
       <!-- QR Scanner Area (Box) -->
       <div id="qr-reader" class="mb-4"
@@ -134,7 +129,7 @@
         <button type="button" id="startScanner"
           class="btn btn-primary w-full py-2 flex flex-col gap-1 items-center justify-center bg-blue-600 hover:bg-blue-700 text-white shadow-md transition-all">
           <span class="text-xl">▣</span>
-          <span id="scannerBtnText" class="font-bold text-sm uppercase tracking-wide">TAP TO SCAN QR CODE</span>
+          <span id="scannerBtnText" class="font-semibold text-sm">Scan the QR code</span>
         </button>
         <div id="torch-container" class="hidden">
           <button type="button" id="torchToggle" class="btn btn-outline w-full mt-2" title="Toggle flashlight">Toggle
@@ -151,24 +146,24 @@
           <div class="w-full border-t border-gray-300"></div>
         </div>
         <div class="relative flex justify-center">
-          <span class="bg-gray-50 px-2 text-sm text-gray-500">or enter manually</span>
+          <span class="bg-gray-50 px-2 text-sm text-gray-500">or type the code</span>
         </div>
       </div>
 
       <!-- Inline Manual Entry -->
       <div class="manual-entry-row">
         <div class="manual-entry-input-wrapper">
-          <input type="text" id="manual_qr" class="manual-entry-input" placeholder="10-digit asset code"
+          <input type="text" id="manual_qr" class="manual-entry-input" placeholder="10-digit code"
             pattern="[0-9]{10}" inputmode="numeric" maxlength="10" autocomplete="off"
-            aria-label="Enter 10-digit QR code manually" />
+            aria-label="Enter 10-digit code" />
         </div>
-        <button type="button" id="useManual" class="btn btn-secondary">Verify</button>
+        <button type="button" id="useManual" class="btn btn-secondary">Use this code</button>
       </div>
     </div>
 
     <!-- Submit Button (Sticky) -->
     <div class="sticky-actions">
-      <button type="submit" id="submitBtn" class="btn btn-success btn-large btn-full font-bold uppercase tracking-wide">
+      <button type="submit" id="submitBtn" class="btn btn-success btn-large btn-full font-semibold">
         Continue
       </button>
     </div>
@@ -178,7 +173,7 @@
 <!-- Recent scans sidebar -->
 <div class="card mt-4">
   <div class="mb-2 pb-2 border-b border-gray-200">
-    <h3 class="text-md font-bold text-gray-700">Recent Scans</h3>
+    <h3 class="text-md font-semibold text-gray-700">Your recent scans</h3>
   </div>
   <div id="recent_scans" class="space-y-2 text-sm text-gray-600">
     <div id="recent_scans_skeleton" class="flex gap-3 overflow-hidden">
@@ -186,7 +181,7 @@
       <div class="skeleton skeleton-card"></div>
       <div class="skeleton skeleton-card"></div>
     </div>
-    <div id="no-recent-scans" class="text-center italic text-gray-400 py-2 hidden">No recent scans</div>
+    <div id="no-recent-scans" class="text-center italic text-gray-400 py-2 hidden">Your scans will show up here</div>
   </div>
 </div>
 
@@ -1068,7 +1063,7 @@
       container.innerHTML = '';
       container.className = 'recent-scans-carousel';
       if (!recentScans.length) {
-        container.innerHTML = '<div class="text-gray-500 text-sm" style="padding: 8px;">No recent scans</div>';
+        container.innerHTML = '<div class="text-gray-500 text-sm" style="padding: 8px;">Your scans will show up here</div>';
         container.className = '';
         return;
       }
EOF

# Dry-run first (note: this file has mixed CRLF/LF line endings;
# --ignore-whitespace makes patch tolerate that)
patch --dry-run -p0 --ignore-whitespace < /tmp/patch_5.diff
# Apply only if all 12 hunks succeeded in dry-run
patch -p0 --ignore-whitespace < /tmp/patch_5.diff
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# 1. Did the patch actually change the file? Look for one of the new strings:
grep -n "Let's find your asset" templates/start.html
# 2. Or is the old string still there?
grep -n "Asset Setup" templates/start.html
# 3. If you still have the patch dry-run / apply output in your terminal scrollback,
#    just paste it. Otherwise, re-run the dry-run only:
patch --dry-run -p0 --ignore-whitespace < /tmp/patch_5.diff 2>&1 | head -40
cd /home/developer/asset_capture_app_dev
# Normalize line endings to LF (matches the patch's line endings)
# This is non-destructive: Flask doesn't care, browsers don't care, your backup is still there
sed -i 's/\r$//' templates/start.html
# Verify line endings are now all LF
file templates/start.html
# Now dry-run the patch
patch --dry-run -p0 < /tmp/patch_5.diff 2>&1 | head -20
# If dry-run shows "Hunk #1 succeeded" through "Hunk #9 succeeded", apply for real:
patch -p0 < /tmp/patch_5.diff
# Verify the file actually changed
grep -n "Let's find your asset" templates/start.html
grep -n "Asset Setup" templates/start.html  # should return nothing
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp templates/success.html templates/success.html.bak_6
# Normalize line endings (avoids the same CRLF/LF issue we hit in Patch 5)
sed -i 's/\r$//' templates/success.html
# Write the patch
cat > /tmp/patch_6.diff << 'EOF'
--- templates/success.html
+++ templates/success.html
@@ -33,7 +33,7 @@
   <div class="w-full flex items-center justify-center mb-2">
     <h2 class="text-3xl font-bold text-center" style="color: var(--color-success, #16a34a);">Upload Successful!</h2>
   </div>
-  <p class="text-gray-600 mb-6">Your photos have been saved and are ready for review.</p>
+  <p class="text-gray-600 mb-6">Your photos are filed. You can capture another or take a look at what you just saved.</p>
 
   <!-- Asset Summary -->
   <div class="asset-summary mb-6">
@@ -65,7 +65,7 @@
         <line x1="12" y1="5" x2="12" y2="19"></line>
         <line x1="5" y1="12" x2="19" y2="12"></line>
       </svg>
-      Capture Another Asset
+      Capture another
     </a>
     <a href="{{ url_for('capture', qr_code=qr_code, building_code=building_code, asset_type=asset_type) }}"
       class="btn btn-outline btn-lg btn-full">
@@ -74,7 +74,7 @@
         <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
         <circle cx="12" cy="12" r="3"></circle>
       </svg>
-      Review Photos
+      Look at the photos
     </a>
   </div>
 </div>
EOF

# Dry-run (expect "Hunk #1 succeeded" through "Hunk #3 succeeded")
patch --dry-run -p0 < /tmp/patch_6.diff
# Apply
patch -p0 < /tmp/patch_6.diff
# Verify
grep -n "Your photos are filed" templates/success.html
grep -n "Capture another" templates/success.html
grep -n "Look at the photos" templates/success.html
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_7
# Normalize line endings to LF (same reason as Patch 5)
sed -i 's/\r$//' static/css/styles.css
# Write the patch
cat > /tmp/patch_7.diff << 'EOF'
--- static/css/styles.css
+++ static/css/styles.css
@@ -25,39 +25,56 @@
   --space-xl: 2rem;
   /* 32px */
 
-  /* Color Palette (preserving existing scheme) */
-  --color-primary: #2563eb;
-  /* blue-600 */
-  --color-primary-hover: #1d4ed8;
-  /* blue-700 */
-  --color-success: #16a34a;
-  /* green-600 */
-  --color-success-hover: #15803d;
-  /* green-700 */
-  --color-secondary: #475569;
-  /* slate-600 */
-  --color-secondary-hover: #334155;
-  /* slate-700 */
-  --color-warning: #eab308;
-  /* yellow-500 */
-  --color-warning-bg: #fef3c7;
-  /* yellow-100 */
-  --color-danger: #dc2626;
-  /* red-600 */
-  --color-danger-bg: #fee2e2;
-  /* red-100 */
-
-  /* Neutrals */
-  --color-gray-50: #f8fafc;
-  --color-gray-100: #f1f5f9;
-  --color-gray-200: #e2e8f0;
-  --color-gray-300: #cbd5e1;
-  --color-gray-700: #334155;
-  --color-gray-800: #1e293b;
-  --color-gray-900: #0f172a;
+  /* Color Palette — Warm & Approachable (per DESIGN.md)
+     Names preserved for compatibility; values shifted toward warm. */
+
+  /* Primary — institutional blue, warmed slightly toward indigo */
+  --color-primary: #3b5fd9;        /* was #2563eb */
+  --color-primary-hover: #2e4ec2;  /* was #1d4ed8 */
+  --color-primary-soft: #e6ebfa;   /* NEW — tint for backgrounds, focus rings */
+
+  /* Success — sage-leaning green */
+  --color-success: #4d9c5e;        /* was #16a34a */
+  --color-success-hover: #3e8b4f;  /* was #15803d */
+  --color-success-soft: #e8f3eb;   /* NEW */
+
+  /* Secondary — warm gray instead of cool slate */
+  --color-secondary: #5a554d;      /* was #475569 */
+  --color-secondary-hover: #423e37;/* was #334155 */
+
+  /* Warning — slightly warmer yellow/amber */
+  --color-warning: #d4a821;        /* was #eab308 */
+  --color-warning-bg: #fdf3d8;     /* was #fef3c7 */
+
+  /* Danger — slightly warmer red */
+  --color-danger: #d44a3e;         /* was #dc2626 */
+  --color-danger-bg: #fde6e2;      /* was #fee2e2 */
+
+  /* Accent — soft amber, NEW. Used sparingly for earned moments */
+  --color-accent: #d99a4b;
+  --color-accent-hover: #c0843d;
+  --color-accent-soft: #fcf0dd;
+
+  /* Surfaces — warm off-white instead of clinical cool slate */
+  --color-surface: #fbfaf7;          /* NEW — page background */
+  --color-surface-elevated: #ffffff; /* NEW — cards on warm surface */
+  --color-surface-sunken: #f4f1ec;   /* NEW — input wells, recessed areas */
+
+  /* Borders — warm-tinted, softer than slate */
+  --color-border-subtle: #ece6da;    /* NEW */
+  --color-border-default: #dbd2c2;   /* NEW */
+
+  /* Neutrals — warm-leaning grays (was cool slate scale) */
+  --color-gray-50:  #fbfaf7;
+  --color-gray-100: #f4f1ec;
+  --color-gray-200: #e8e3da;
+  --color-gray-300: #d4ccbf;
+  --color-gray-700: #4a4239;
+  --color-gray-800: #2f2a23;
+  --color-gray-900: #1f1c17;
   --color-text-primary: var(--color-gray-900);
   --color-text-secondary: var(--color-gray-700);
-  --color-text-muted: #64748b;
+  --color-text-muted: #7a6f60;
 
   /* Typography */
   --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
@@ -91,7 +108,7 @@
   --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
   --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);
   --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1);
-  --focus-ring: 0 0 0 3px rgba(37, 99, 235, 0.4);
+  --focus-ring: 0 0 0 3px rgba(59, 95, 217, 0.35);  /* matches warmed --color-primary */
   --transition-fast: 150ms ease;
   --transition-normal: 200ms ease;
 }
@@ -1835,12 +1852,13 @@
    Dark Mode Support
    --------------------------------------------------------------------------- */
 :root {
-  --dm-bg-primary: #1a1a2e;
-  --dm-bg-secondary: #16213e;
-  --dm-bg-card: #1f2942;
-  --dm-text-primary: #ffffff;
-  --dm-text-secondary: #a0aec0;
-  --dm-border: #2d3748;
+  /* Warm dark palette (matches the warm light palette above) */
+  --dm-bg-primary: #1e1a16;     /* was #1a1a2e — cool indigo → warm dark */
+  --dm-bg-secondary: #26211c;   /* was #16213e */
+  --dm-bg-card: #2c2620;        /* was #1f2942 */
+  --dm-text-primary: #f5f0e8;   /* was #ffffff — warm white instead of pure */
+  --dm-text-secondary: #b4a895; /* was #a0aec0 — warm muted */
+  --dm-border: #3a342d;         /* was #2d3748 */
 }
 
 /* Auto dark mode based on system preference */
EOF

# Dry-run (expect 3 hunks to succeed)
patch --dry-run -p0 < /tmp/patch_7.diff
# Apply if dry-run succeeded
patch -p0 < /tmp/patch_7.diff
# Verify the new primary color is in place
grep -n "color-primary: #3b5fd9" static/css/styles.css
grep -n "color-accent: #d99a4b" static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
clear
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix1
# Write the patch
cat > /tmp/fix_1.diff << 'EOF'
--- static/css/styles.css
+++ static/css/styles.css
@@ -1644,8 +1644,9 @@
 .field-icon {
   position: absolute;
   left: 12px;
-  top: 50%;
-  transform: translateY(-50%);
+  top: auto;        /* override centering on the whole field-group (label + select) */
+  bottom: 6px;      /* center on the 48px select: (48 - 36) / 2 = 6px */
+  transform: none;  /* override the old translateY(-50%) */
   width: 36px;
   height: 36px;
   display: flex;
EOF

# Dry-run
patch --dry-run -p0 < /tmp/fix_1.diff
# Apply
patch -p0 < /tmp/fix_1.diff
# Verify
grep -A 4 "^\.field-icon {" static/css/styles.css | head -6
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix3
# Replace the hardcoded #d1d5db in .border-gray-300 with the warm border-subtle token
# (Surgical — targets only the .border-gray-300 rule, not anything else)
sed -i '/^\.border-gray-300 {/,/^}/{s|border-color: #d1d5db;|border-color: var(--color-border-subtle, #ece6da);|;}' static/css/styles.css
# Verify the change
grep -A 2 "^\.border-gray-300" static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix2
# Append the fine-tune CSS
cat >> static/css/styles.css << 'EOF'

/* === Fine-tune 2: scanner area warm bg + dashed border, scan button rounder === */
/* !important needed to override the inline style="background-color: #e5e7eb" on #qr-reader in start.html */
#qr-reader {
  background-color: var(--color-surface-sunken, #f4f1ec) !important;
  border: 2px dashed var(--color-border-default, #dbd2c2);
}

/* Slightly more rounded Scan QR button to match the warm/friendlier mock */
#startScanner {
  border-radius: var(--radius-lg, 0.75rem);
}
EOF

# Verify (should show the new rules)
tail -15 static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix4
# Append the three changes
cat >> static/css/styles.css << 'EOF'

/* === Fine-tune 4 === */

/* (a) More rounded Scan QR button (was 12px from Fix 2, now ~16px to match desired look) */
#startScanner {
  border-radius: 1rem;
}

/* (b) "Point at the QR sticker" hint text below the QR icon in the placeholder.
   Added via pseudo-element so we don't need to touch start.html. */
.qr-placeholder::after {
  content: "Point at the QR sticker";
  font-size: 0.875rem;
  color: var(--color-text-muted, #7a6f60);
  margin-top: 12px;
  text-align: center;
  pointer-events: none;
}

/* (c) Make the "or type the code" divider lines much more subtle (Fix 3 wasn't soft enough) */
.border-gray-300 {
  border-color: rgba(74, 66, 57, 0.12) !important;
}
EOF

# Verify additions are at the end
tail -22 static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix5
# Append the two fixes
cat >> static/css/styles.css << 'EOF'

/* === Fine-tune 5 === */

/* (a) Hide the ▣ square icon inside the Scan button — it makes the button stack
       vertically (icon on top, text below) which looks stadium-pill-shaped.
       Without the icon, the button is single-line and the rounding looks right. */
#startScanner > .text-xl {
  display: none;
}

/* (b) Bring back visibility on the "or type the code" divider lines.
       Fix 4 used 0.12 alpha which was too faint. 0.30 is visible-but-soft. */
.border-gray-300 {
  border-color: rgba(74, 66, 57, 0.30) !important;
}
EOF

# Verify
tail -15 static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix6
# Append a darker override (later rule wins in CSS cascade)
cat >> static/css/styles.css << 'EOF'

/* === Fine-tune 6: darker divider lines (Fix 5 at 0.30 alpha was still too faint) === */
.border-gray-300 {
  border-color: rgba(74, 66, 57, 0.50) !important;
}
EOF

# Verify (should show the new rule at the end)
tail -6 static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
clear
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix7
# Append both adjustments
cat >> static/css/styles.css << 'EOF'

/* === Fine-tune 7 === */

/* (a) Scan QR button: rectangle with slight rounding (was 1rem/16px, now 0.5rem/8px) */
#startScanner {
  border-radius: 0.5rem;
}

/* (b) Divider lines 100% darker than Fix 6 (0.50 alpha → 1.0 alpha = fully solid warm gray) */
.border-gray-300 {
  border-color: rgba(74, 66, 57, 1) !important;
}
EOF

# Verify
tail -12 static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
cd /home/developer/asset_capture_app_dev
# Backup
cp static/css/styles.css static/css/styles.css.bak_fix8
# Append: 6px radius (matches the subtle rounding in your reference screenshot)
cat >> static/css/styles.css << 'EOF'

/* === Fine-tune 8: Scan button corner radius matches reference (6px, subtle) === */
#startScanner {
  border-radius: 0.375rem;  /* 6px — was 0.5rem/8px in Fix 7 */
}
EOF

# Verify
tail -6 static/css/styles.css
# Reload gunicorn
kill -HUP 1181594
sudo systemctl restart assetcap-app.service
sudo systemctl restart assetcap-dashboard
clear
sudo systemctl restart assetcap-bf assetcap-el
# 1) Install openpyxl in the ME and BF venvs (EL already has it)
cd /home/developer/review/Asset_dasboard_browser_ME && source venv/bin/activate && pip install -r requirements.txt && deactivate
cd /home/developer/review/Asset_dasboard_browser_BF && source venv/bin/activate && pip install -r requirements.txt && deactivate
# 2) Restart the three services
sudo systemctl restart assetcap-el assetcap-reviewme assetcap-bf
# 3) Quick health check
sudo systemctl status assetcap-el assetcap-reviewme assetcap-bf --no-pager
sudo journalctl -u assetcap-el -u assetcap-reviewme -u assetcap-bf -n 30 --no-pager
sudo systemctl restart assetcap-el assetcap-reviewme assetcap-bf
clear
sudo systemctl restart assetcap-el assetcap-reviewme assetcap-bf
cd /home/developer/review/Asset_dasboard_browser_BF
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart assetcap-el assetcap-reviewme assetcap-bf
systemctl restart assetcap-reviewme assetcap-bf assetcap-el assetcap-dashboard
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
sudo systemctl restart assetcap-dashboard
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
sudo journalctl -u assetcap-el
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
sudo systemctl restart assetcap-el
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
sudo systemctl restart assetcap-reviewme assetcap-bf
sudo journalctl -u assetcap-reviewme -n 20 --no-pager
sudo journalctl -u assetcap-bf -n 20 --no-pager
sudo systemctl restart sdi_process
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
sudo systemctl status assetcap-el assetcap-reviewme assetcap-bf --no-pager
sudo journalctl -u assetcap-el -n 30 --no-pager
sudo systemctl status assetcap-el assetcap-reviewme assetcap-bf --no-pager
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
/home/developer/asset_capture_app_dev/data/sqlite_checkpoint.sh
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
cd /home/developer/staging_api
DB_BACKEND=postgres QR_PG_DSN="host=127.0.0.1 port=5433 dbname=qr_code_db user=developer" PYTHONPATH=/home/developer   ./run_ai_and_sync.sh ME 0000184869   # any ME QR you want
exit
clear
cd /home/developer/staging_api
DB_BACKEND=postgres QR_PG_DSN="host=127.0.0.1 port=5433 dbname=qr_code_db user=developer" PYTHONPATH=/home/developer   ./run_ai_and_sync.sh ME 0000184869   # any ME QR you want
ssh -L 9002:localhost:9002 developer@142.103.68.1
ssh -L 9001:localhost:9001 developer@142.103.68.1
clear
sudo systemctl restart sdi_process assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
exit
clear
ssh -L 9001:localhost:9001 -L 9002:localhost:9002 -L 9003:localhost:9003 -L 9004:localhost:9004 -L 9005:localhost:9005 developer@142.103.68.1
exit
clear
ssh -L 9001:localhost:9001 -L 9002:localhost:9002 -L 9003:localhost:9003     -L 9004:localhost:9004 -L 9005:localhost:9005 developer@142.103.68.1
exit
clear
ssh -L 9001:localhost:9001 -L 9002:localhost:9002 -L 9003:localhost:9003     -L 9004:localhost:9004 -L 9005:localhost:9005 developer@142.103.68.1
ssh -L 9001:localhost:9001 developer@142.103.68.1
ssh -L 9005:localhost:9005 developer@142.103.68.1
clear
ssh -L 9005:localhost:9005 developer@142.103.68.1
exit
ssh -L 9003:localhost:9003 developer@142.103.68.1
Overall view of the server: df -h
df -h
clear
df -h
exit
sudo systemctl list-units --type=service --all | grep -i postgres
sudo systemctl status postgresql --no-pager
sudo journalctl -u postgresql --since "2 hours ago" --no-pager | tail -n 100
sudo systemctl status qr-postgres --no-pager -l
sudo journalctl -u qr-postgres --since "24 hours ago" --no-pager | tail -n 200
sudo -u postgres pg_isready -h 127.0.0.1 -p 5433
clear
sudo -u postgres pg_isready -h 127.0.0.1 -p 5433
sudo systemctl restart qr-postgres
sudo -u postgres pg_isready -h 127.0.0.1 -p 5433
grep -R '^[[:space:]]*RemoveIPC'   /etc/systemd/logind.conf   /etc/systemd/logind.conf.d 2>/dev/null
getent passwd postgres
sudo systemctl restart sdi_process assetcap-app.service assetcap-reviewme assetcap-bf assetcap-dashboard assetcap-el
