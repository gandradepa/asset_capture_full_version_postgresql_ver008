#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Asset Management Dashboard – Flask app (Asset-portal-dashboard.py)

Run locally:
  python Asset-portal-dashboard.py

Open:
  http://127.0.0.1:8002
"""

import os
import sys
import re
import time
import subprocess
import sqlite3
import traceback
import ast  # [NEW] For parsing dictionary file
import json # [NEW] For saving dictionary file
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

# --- [CONFIGURATION] Python Path Setup ---
# Ensure the current directory and parent directory are in the python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from flask import (
    Flask, render_template, redirect, url_for, flash,
    request, abort, Response, jsonify, send_from_directory, Blueprint
)
from markupsafe import Markup

## Import authentication and environment variable libraries
from flask_login import login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from ai_check_log_parser import parse_ai_check_log

## Add the shared auth_service directory to Python's path
## Add the shared auth_service directory to Python's path
auth_service_path = os.path.join(parent_dir, 'auth_service')
if os.path.exists(auth_service_path):
    sys.path.append(auth_service_path)
else:
    sys.path.append('/home/developer/auth_service') 

from auth_model import db, bcrypt, User, UserAccess, ensure_user_name_column, ensure_user_access_table, ensure_user_active_column, ensure_user_is_admin_column, has_permission, is_admin as rbac_is_admin, require_permission, access_denied_response
import db as qrdb  # backend-agnostic QR_codes DB layer (aliased: 'db' above is Flask-SQLAlchemy from auth_model)
from auth_controller import login_manager

# Shared audit facility. Optional at import time so a dev box without the audit
# package (or without an audit_trail table) can still run the Dashboard.
try:
    from audit.logger import log_change as _audit_log_change
except Exception as _audit_import_exc:  # pragma: no cover - environment dependent
    _audit_log_change = None
    print(f"WARNING: audit logger unavailable, dictionary edits will not be audited: {_audit_import_exc}")

try:
    from app_registry import get_registry, is_valid_item
except ImportError:
    def get_registry(): return []
    def is_valid_item(s, i): return True

# Asset disposal service. Optional at import time like the chart modules: a box
# without the disposed_assets migration still boots, with the tool degrading to
# a clear "not available" response.
try:
    import disposed_assets_service as disposal_svc
    DISPOSAL_AVAILABLE = True
except Exception as _disposal_import_exc:  # pragma: no cover - environment dependent
    disposal_svc = None
    DISPOSAL_AVAILABLE = False
    print(f"WARNING: disposal service unavailable, the Disposed tool is disabled: {_disposal_import_exc}")


FLS_DEFAULT_ATTRIBUTE_CODE = "FireAlarmDevice"
FLS_DEFAULT_ATTRIBUTE_LABEL = "Electrical/FLS - Fire Alarm Device"


# ------------------ Chart Modules Import Section ------------------
CHARTS_AVAILABLE = False
AI_STATUS_AVAILABLE = False
COMPLETENESS_CHART_AVAILABLE = False
AI_CONFIDENCE_CHART_AVAILABLE = False
DATA_QUALITY_CHART_AVAILABLE = False
OPERATIONAL_COST_CHART_AVAILABLE = False
FLS_CHARTS_AVAILABLE = False
CHARTS_IMPORT_ERROR = ""
MAP_CHART_AVAILABLE = False
MAP_CHART_ERROR = ""

# 1. Try Import: Approval Charts
try:
    from charts import approval as approval_mod
    CHARTS_AVAILABLE = True
except Exception as _e:
    CHARTS_IMPORT_ERROR = str(_e)

# 2. Try Import: AI Status Table
try:
    from charts import ai_status_table_new_version as ai_status_table
    AI_STATUS_AVAILABLE = True
except Exception as _e:
    error_msg = str(_e)
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

# 3. Try Import: Completeness Score
try:
    from charts import completeness_score as completeness_mod
    COMPLETENESS_CHART_AVAILABLE = True
except Exception as _e:
    error_msg = f"Completeness Chart Error: {str(_e)}"
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

# 4. Try Import: Operational Cost
try:
    from charts import operational_cost_result as operational_cost_mod
    OPERATIONAL_COST_CHART_AVAILABLE = True
except Exception as _e:
    error_msg = f"Operational Cost Chart Error: {str(_e)}"
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

# 4b. Try Import: AI Confidence Score
try:
    from charts import ai_confidence_score as ai_confidence_mod
    AI_CONFIDENCE_CHART_AVAILABLE = True
except Exception as _e:
    error_msg = f"AI Confidence Chart Error: {str(_e)}"
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

# 4c. Try Import: Data Quality Comparison
try:
    from charts import data_quality_comparison as data_quality_mod
    DATA_QUALITY_CHART_AVAILABLE = True
except Exception as _e:
    error_msg = f"Data Quality Chart Error: {str(_e)}"
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

# 5. Try Import: FLS Charts (Altair Version - Robust Import)
print("--- Attempting to import FLS Charts ---")
try:
    # Check if altair is installed first
    import altair
    
    # Try importing locally first, then as package
    try:
        import fls_chart as fls_charts_mod
        print("SUCCESS: Imported 'fls_chart' locally.")
    except ImportError:
        from charts import fls_chart as fls_charts_mod
        print("SUCCESS: Imported 'fls_chart' from charts package.")

    FLS_CHARTS_AVAILABLE = True

except ImportError as e:
    FLS_CHARTS_AVAILABLE = False
    error_msg = f"Missing Dependency for FLS Charts (Altair or fls_chart): {e}"
    print(f"CRITICAL ERROR: {error_msg}")
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

except Exception as e:
    FLS_CHARTS_AVAILABLE = False
    error_msg = f"Error loading FLS Charts module: {e}"
    print(f"CRITICAL ERROR: {error_msg}")
    traceback.print_exc() 
    if CHARTS_IMPORT_ERROR: CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else: CHARTS_IMPORT_ERROR = error_msg

# 6. Try Import: Map Chart (assets by building)
try:
    from charts import map_chart
    MAP_CHART_AVAILABLE = True
except Exception as _e:
    MAP_CHART_ERROR = str(_e)
    print(f"WARNING: Could not import 'map_chart': {MAP_CHART_ERROR}")


    from charts import map_chart
    MAP_CHART_AVAILABLE = True
except Exception as _e:
    MAP_CHART_ERROR = str(_e)
    print(f"WARNING: Could not import 'map_chart': {MAP_CHART_ERROR}")

# 7. Try Import: SDI Flow Chart
try:
    from charts import flow_quantity_chart
    FLOW_CHART_AVAILABLE = True
except Exception as _e:
    FLOW_CHART_AVAILABLE = False
    print(f"WARNING: Could not import 'flow_quantity_chart': {_e}")


# ------------------ Flask app Configuration ------------------
env_path = os.path.join(parent_dir, '.env')
auth_env_path = os.path.join(parent_dir, 'auth_service.env')

if os.path.exists(env_path):
    load_dotenv(env_path)
elif os.path.exists(auth_env_path):
    load_dotenv(auth_env_path)
else:
    load_dotenv('/home/developer/.env')

app = Flask(__name__)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

# Fix DATABASE_URI for Windows if it is a Linux path
db_uri = os.getenv('DATABASE_URI')
if db_uri and db_uri.startswith('sqlite:////home/developer/'):
    # Extract the relative path part after /home/developer/
    rel_path = db_uri.replace('sqlite:////home/developer/', '')
    # Construct absolute Windows path
    # handle both 'auth_service/users.db' and other paths
    # We assume 'auth_service' is in parent_dir
    win_path = os.path.join(parent_dir, rel_path.replace('/', os.sep))
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{win_path}"
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri

app.config['SESSION_COOKIE_DOMAIN'] = os.getenv('SESSION_COOKIE_DOMAIN')

_samesite = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
_secure_raw = os.getenv('SESSION_COOKIE_SECURE', 'False')
_secure = _secure_raw.strip().lower() in ('true', '1', 'yes')
app.config['SESSION_COOKIE_SAMESITE'] = _samesite
app.config['SESSION_COOKIE_SECURE']   = _secure
app.config['REMEMBER_COOKIE_SAMESITE'] = _samesite
app.config['REMEMBER_COOKIE_SECURE']   = _secure

db.init_app(app)
bcrypt.init_app(app)
login_manager.init_app(app)

def _ensure_auth_user_name_column() -> None:
    """Ensure all optional auth schema columns exist on existing SQLite DBs."""
    try:
        with app.app_context():
            if ensure_user_name_column():
                print("[Auth DB] Added nullable User.name column.")
            ensure_user_access_table()
            if ensure_user_active_column():
                print("[Auth DB] Added User.active column.")
            if ensure_user_is_admin_column():
                print("[Auth DB] Added User.is_admin column.")
    except Exception as exc:
        print(f"[Auth DB] WARNING: Could not ensure auth schema: {exc}")

_ensure_auth_user_name_column()

@login_manager.unauthorized_handler
def handle_unauthorized():
    """
    Return JSON 401 for API/data endpoints so frontend fetch calls can handle
    expired sessions deterministically. Keep browser redirects for normal pages.
    """
    next_target = request.full_path.rstrip('?') or request.path or '/'
    login_url = url_for('auth.login', next=next_target)
    accept_header = (request.headers.get('Accept') or '').lower()
    is_api_call = request.path.startswith('/api/') or request.path.startswith('/data/') or 'application/json' in accept_header

    if is_api_call:
        return jsonify({
            "success": False,
            "error": "unauthorized",
            "message": "Authentication required.",
            "login_url": login_url
        }), 401

    return redirect(login_url)


# ------------------ Cards shown on the dashboard ------------------
APPS = [
    {"key": "capture",     "name": "Asset Capture Mobile App",           "url": "https://appprod.assetcap.facilities.ubc.ca"},
    {"key": "review_me",   "name": "Asset Reviewer - Mechanical",        "url": "https://reviewme.assetcap.facilities.ubc.ca"},
    {"key": "review_bf",   "name": "Asset Reviewer - Backflow Devices",  "url": "https://reviewbf.assetcap.facilities.ubc.ca"},
    {"key": "review_el",   "name": "Asset Reviewer - Electrical",        "url": "https://reviewel.assetcap.facilities.ubc.ca"},
    {"key": "sdi_process", "name": "SDI Process Application",            "url": "https://sdiprocess.assetcap.facilities.ubc.ca"},
]

# ------------------ Log directory ------------------
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
AI_CHECK_LOG_PATH = Path("/home/developer/ai_check.log")

# Scheduled integrity auditors (cron). These write [AUDIT] marker lines that
# _audit_log_status() parses into the Status badge on the System Logs page.
# Listing them here is the delivery channel the 2026-08-05 incident lacked:
# audit_sdi_vs_json.py had been reporting missing curated rows hourly for
# months into a log no operator surface displayed.
AUDIT_LOG_PATHS = (
    Path(os.getenv("CAPTURE_AUDIT_LOG_PATH", "/home/developer/logs/capture_audit.log")),
    Path(os.getenv("SDI_AUDIT_LOG_PATH", "/home/developer/logs/sdi_audit.log")),
    Path(os.getenv("SDI_FLOW_AUDIT_LOG_PATH", "/home/developer/logs/sdi_flow_audit.log")),
)

# An auditor that has not written for this long has almost certainly stopped
# running (all are scheduled hourly), which is itself a reportable state.
AUDIT_STALE_AFTER_SECONDS = 2 * 60 * 60

## --- Path to the SQLite DB --- ##
possible_db_path = os.path.join(parent_dir, 'asset_capture_app_dev', 'data', 'QR_codes.db')
if os.path.exists(possible_db_path):
    DB_PATH = possible_db_path
else:
    DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"

# Keep chart modules aligned with the resolved DB path (important on Windows/dev envs).
try:
    resolved_db_path = str(Path(DB_PATH).resolve())
    resolved_root_dir = str(Path(resolved_db_path).resolve().parents[2])

    if CHARTS_AVAILABLE:
        approval_mod.DB_PATH = resolved_db_path

    if OPERATIONAL_COST_CHART_AVAILABLE:
        operational_cost_mod.DB_PATH = resolved_db_path
        operational_cost_mod.OUTPUT_JSON_API_DIR = os.path.join(resolved_root_dir, "Output_jason_api")
        operational_cost_mod.ASSET_CAPTURE_DATA_DIR = os.path.join(resolved_root_dir, "asset_capture_app_dev", "data")

    if COMPLETENESS_CHART_AVAILABLE:
        completeness_mod.DB_PATH = resolved_db_path

    if AI_CONFIDENCE_CHART_AVAILABLE:
        ai_confidence_mod.DB_PATH = resolved_db_path

    if DATA_QUALITY_CHART_AVAILABLE:
        data_quality_mod.DB_PATH = resolved_db_path
except Exception:
    pass

## --- [NEW] Path to the Dictionary File --- ##
possible_dict_path = Path(parent_dir) / "dictionary" / "mechanical_dictionary.py"
if possible_dict_path.parent.exists():
    DICTIONARY_FILE_PATH = possible_dict_path
else:
    DICTIONARY_FILE_PATH = Path("/home/developer/dictionary/mechanical_dictionary.py")

# Discipline codes the dictionary accepts. These match the platform-wide codes
# used by the review apps, the extraction API and the SDI process.
DICTIONARY_ALLOWED_TYPES = ("ME", "EL", "BF")

# Fields audited on dictionary writes. The legacy duplicated "type" key mirrors
# "asset_type", so auditing it would double every row.
_DICT_AUDIT_FIELDS = ("attribute_set", "asset_group", "main_asset", "description", "asset_type")

# --- Path to captured asset photos (QR images) ---
_photo_dir_candidates = [
    Path("/home/developer/Capture_photos_upload"),
    Path(parent_dir) / "Capture_photos_upload",
    Path(current_dir) / "Capture_photos_upload",
]
PHOTO_UPLOAD_DIR = next((p for p in _photo_dir_candidates if p.exists()), _photo_dir_candidates[0])

# ------------------ [NEW] Dictionary Helpers ------------------
def _ensure_new_device_columns():
    """
    Checks if the 'new_device' table has the new columns for Planon checklist items.
    If not, adds them.
    """
    NEW_COLUMNS = {
        "Request Open": "INTEGER DEFAULT 0",
        "Request Date": "TEXT",
        "Elapsed Time": "INTEGER DEFAULT 0",
        "Complete": "INTEGER DEFAULT 0",
        "Ticket Number": "TEXT"
    }
    
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        cursor = conn.cursor()
        
        # Get existing columns (backend-agnostic)
        existing_cols = set(qrdb.table_columns(conn, "new_device"))
        
        for col_name, col_type in NEW_COLUMNS.items():
            if col_name not in existing_cols:
                print(f"Schema Migration: Adding column '{col_name}' to 'new_device' table...")
                try:
                    cursor.execute(f'ALTER TABLE new_device ADD COLUMN "{col_name}" {col_type}')
                    print(f"Successfully added column '{col_name}'.")
                except sqlite3.OperationalError as e:
                    print(f"Error adding column '{col_name}': {e}")
        
        conn.commit()
    except Exception as e:
        print(f"Schema Migration Error: {e}")
    finally:
        if conn:
            conn.close()

def read_dictionary():
    if not DICTIONARY_FILE_PATH.exists():
        # Create dummy file if missing, ensuring directory exists
        try:
            DICTIONARY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DICTIONARY_FILE_PATH.open('w', encoding='utf-8') as f: f.write('ASSET_DICTIONARY = {}')
        except Exception as e:
            print(f"Error creating dictionary file: {e}")
            return {}
        return {}
    try:
        with DICTIONARY_FILE_PATH.open('r', encoding='utf-8') as f:
            file_content = f.read()
        tree = ast.parse(file_content)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == 'ASSET_DICTIONARY':
                        return ast.literal_eval(node.value)
        return {}
    except Exception as e:
        print(f"Error reading dictionary: {e}")
        return {}

def save_dictionary(new_data):
    try:
        sorted_data = dict(sorted(new_data.items()))
        content = "ASSET_DICTIONARY = " + json.dumps(sorted_data, indent=4)
        
        # Ensure directory exists before saving
        DICTIONARY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        with DICTIONARY_FILE_PATH.open('w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"Error saving dictionary: {e}")
        return False

def _log_dictionary_audit(op_type, storage_key, field_changes, description):
    """Record a dictionary file write in audit_trail.

    The dictionary is a FILE, not a DB row, so there is no caller transaction to
    join: this opens its own short-lived connection and commits it. The file has
    already been written by the time we get here, so an audit failure must never
    fail the request - it is logged and swallowed.
    """
    if _audit_log_change is None or not field_changes:
        return
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        _audit_log_change(
            conn,
            qr_code=None,                        # a dictionary key is not a QR code
            app_name="dashboard_dictionary",
            table_name="mechanical_dictionary",  # logical name of the file-backed store
            record_pk=storage_key,               # e.g. "AHU|ME"
            op_type=op_type,                     # INSERT / UPDATE / DELETE only
            field_changes=field_changes,
            source="human",                      # modified_by resolves to current_user
            description=description,
        )
        conn.commit()
    except Exception as exc:
        print(f"[audit] dictionary audit failed ({op_type} {storage_key}): {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

# ------------------ Runner helpers (RESTORED FULLY) ------------------
def _windows_detached_flags() -> int:
    if sys.platform.startswith("win"):
        return 0x00000200 | 0x00000008
    return 0

def _cmd_script_path(cmd: List[str]) -> Optional[Path]:
    for part in cmd:
        if part.lower().endswith((".py", ".sh")):
            try:
                return Path(part).resolve()
            except Exception:
                return Path(part)
    return None

def _launch_cmd_detached(cmd: List[str], cwd: Optional[Path]) -> Path:
    timestamp = int(time.time())
    
    py_script_path = next((p for p in cmd if p.lower().endswith(".py")), None)
    if py_script_path:
        stem = Path(py_script_path).stem
    else:
        script_path = _cmd_script_path(cmd)
        stem = script_path.stem if script_path else "task"
        
    log_path = LOG_DIR / f"{stem}.{timestamp}.log"

    log_fp = open(log_path, "w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, stdout=log_fp,
        stderr=subprocess.STDOUT, shell=False,
        creationflags=_windows_detached_flags(), env=env
    )
    return log_path

# ------------------ Script locations ------------------
def _get_api_root() -> Path:
    possible_api = Path(parent_dir) / "API"
    if possible_api.exists():
        return possible_api
    return Path(os.environ.get("QR_API_ROOT", "/home/developer/API"))

def _build_tasks() -> Dict[str, Dict]:
    api_root = _get_api_root()
    
    # Standard wrapper (for simple tasks)
    wrapper_script = api_root / "run_interpreter.sh"
    
    # [NEW] Chained wrapper (Runs AI -> Then Runs Database Sync automatically)
    chained_wrapper = api_root / "run_ai_and_sync.sh"

    me_script = api_root / "API_interface_ME_ver00.py"
    bf_script = api_root / "API_interface_BF_ver00.py"
    el_script = api_root / "API_interface_EL_ver00.py"

    tasks: Dict[str, Dict] = {}
    
    # UPDATED: Use chained_wrapper for AI Interpreters so DB sync happens automatically
    tasks["qr_api_me"] = {"cmd": ["/bin/bash", str(chained_wrapper), str(me_script)], "cwd": api_root, "label": "AI Interpreter – Mechanical"}
    tasks["qr_api_bf"] = {"cmd": ["/bin/bash", str(chained_wrapper), str(bf_script)], "cwd": api_root, "label": "AI Interpreter – Backflow"}
    tasks["qr_api_el"] = {"cmd": ["/bin/bash", str(chained_wrapper), str(el_script)], "cwd": api_root, "label": "AI Interpreter – Electrical"}
    
    # update_db task removed - automated in backend
    
    return tasks

TASKS = _build_tasks()

def _validate_task_key(task_key: str) -> Dict:
    if task_key not in TASKS:
        abort(404, f"Unknown task: {task_key}")
    task = TASKS[task_key]
    
    py_script_path_str = next((p for p in task.get("cmd", []) if p.lower().endswith(".py")), None)
    if not py_script_path_str or not Path(py_script_path_str).exists():
        print(f"ERROR: Python script for task '{task_key}' not found at: {py_script_path_str}")
        raise FileNotFoundError(f"Python script for {task_key} not found.")
        
    return task

def _extract_ts_from_logname(name: str) -> Optional[str]:
    stem = Path(name).stem
    parts = stem.rsplit(".", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    return None

def _safe_log_path(name: str) -> Path:
    # Named allowlist first (absolute paths outside LOG_DIR), then the
    # directory-contained fallback. Names are unique across both sets.
    for allowed in (AI_CHECK_LOG_PATH, *AUDIT_LOG_PATHS):
        if name == allowed.name:
            if allowed.exists():
                return allowed
            abort(404, "Log not found")
    p = (LOG_DIR / name).resolve()
    if not p.exists() or p.parent != LOG_DIR.resolve():
        abort(404, "Log not found")
    return p

# ------------------ Chart helpers ------------------
def _get_building_options(user=None) -> List[str]:
    if not CHARTS_AVAILABLE: return ["All"]
    try: return approval_mod.building_options(user)
    except Exception: return ["All"]

def _get_users_with_data() -> List[str]:
    if not CHARTS_AVAILABLE: return ["All"]
    try: return approval_mod.users_with_data()
    except Exception: return ["All"]

# ------------------ Log UI Helpers ------------------
AUDIT_LOG_TITLES = {
    "capture_audit": "Capture vs Curated Audit",
    "sdi_audit": "SDI vs JSON Audit",
    "sdi_flow_audit": "SDI Flow Integrity Audit",
}


def _title_from_logname(name: str) -> str:
    if name == AI_CHECK_LOG_PATH.name or Path(name).stem == "ai_check":
        return "AI Check Log"
    audit_title = AUDIT_LOG_TITLES.get(Path(name).stem)
    if audit_title:
        return audit_title
    base = Path(name).stem.rsplit(".", 1)[0]
    is_interpreter = "API_interface" in base
    is_data_task = "updating_process_database" in base

    if is_data_task:
        return "Data Processing Task"
    
    kind = "AI Interpreter" if is_interpreter else "Task"
    
    suffix = ""
    base_upper = base.upper()
    if "ME" in base_upper: suffix = " – ME"
    elif "BF" in base_upper: suffix = " – BF"
    elif "EL" in base_upper: suffix = " – EL"
    return f"{kind}{suffix}"

def _when_from_ts(ts: Optional[str], path: Optional[Path] = None) -> str:
    try:
        if ts and ts.isdigit(): return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: pass
    if path is not None:
        try:
            return datetime.fromtimestamp(int(path.stat().st_mtime)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return "—"

def _system_log_paths() -> List[Path]:
    """Logs offered on the System Logs page: the AI check log plus every
    scheduled integrity auditor that has actually written a file. Absent paths
    are filtered rather than fatal (dev machines have none of them)."""
    paths = []
    for p in (AI_CHECK_LOG_PATH, *AUDIT_LOG_PATHS):
        try:
            if p.exists():
                paths.append(p)
        except OSError:
            continue
    return paths


# ------------------ Audit log status ------------------
# Read only the tail: sdi_audit.log is ~1.7 MB in production and this runs on
# every System Logs page load.
_AUDIT_TAIL_BYTES = 8192
_audit_status_cache: dict = {}
_audit_status_lock = Lock()


def _parse_audit_marker(line: str) -> dict:
    """Interpret one [AUDIT] marker line.

    Grammar shared by the three auditors:
        [AUDIT] OK - reconciled N QR code(s), no findings
        [AUDIT] FINDINGS: N across M QR code(s) (DRIFT=N)
        [AUDIT] ANOMALIES: N found across M scanned     (audit_sdi_vs_json.py)
        [AUDIT] RUN_AT=<iso> SCANNED=N FINDINGS=N FAILING=N
    """
    text = line.strip()
    for token in ("FINDINGS:", "ANOMALIES:"):
        if token in text:
            after = text.split(token, 1)[1].strip().split()
            count = int(after[0]) if after and after[0].isdigit() else 0
            return {"state": "findings" if count else "ok", "count": count}
    if "RUN_AT=" in text:
        # Trailers differ per auditor: audit_capture_vs_curated writes
        # SCANNED=/FINDINGS=/FAILING=, audit_sdi_flow_integrity FINDINGS=/FAILING=,
        # audit_sdi_vs_json ANOMALIES=. SCANNED is a scope, never a count.
        count = 0
        for part in text.split():
            for key in ("FINDINGS=", "ANOMALIES="):
                if part.startswith(key):
                    tail = part.split("=", 1)[1]
                    count = int(tail) if tail.isdigit() else 0
        return {"state": "findings" if count else "ok", "count": count}
    if text.startswith("[AUDIT] OK") or " OK -" in text:
        return {"state": "ok", "count": 0}
    if "ERROR" in text:
        return {"state": "unknown", "count": 0}
    return {}


def _audit_log_status(path: Path) -> dict:
    """{"state": "ok"|"findings"|"stale"|"unknown", "count": int} from a log tail.

    The LAST marker wins, so a clean run after a bad one reads clean. A log
    older than AUDIT_STALE_AFTER_SECONDS reads "stale" whatever it says — a
    silent auditor is itself a finding, which is the failure the old
    `--quiet` cron lines hid.
    """
    unknown = {"state": "unknown", "count": 0}
    try:
        stat = path.stat()
    except OSError:
        return dict(unknown)

    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _audit_status_lock:
        hit = _audit_status_cache.get(key)
    if hit is not None:
        return dict(hit)

    result = dict(unknown)
    try:
        with open(path, "rb") as fh:
            if stat.st_size > _AUDIT_TAIL_BYTES:
                fh.seek(-_AUDIT_TAIL_BYTES, os.SEEK_END)
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            if "[AUDIT]" not in line:
                continue
            parsed = _parse_audit_marker(line)
            if parsed:
                result = parsed
                break
    except OSError:
        return dict(unknown)

    if time.time() - stat.st_mtime > AUDIT_STALE_AFTER_SECONDS:
        result = {"state": "stale", "count": result.get("count", 0)}

    with _audit_status_lock:
        if len(_audit_status_cache) > 64:
            _audit_status_cache.clear()
        _audit_status_cache[key] = dict(result)
    return dict(result)


AUDIT_STATUS_BADGES = {
    "ok": ("bg-success", "OK"),
    "findings": ("bg-warning text-dark", "findings"),
    "stale": ("bg-secondary", "stale"),
    "unknown": ("bg-light text-dark", "—"),
}


def _audit_status_view(path: Path) -> Optional[dict]:
    """Badge data for the System Logs table, or None for non-audit logs."""
    if Path(path.name).stem not in AUDIT_LOG_TITLES:
        return None
    st = _audit_log_status(path)
    badge, label = AUDIT_STATUS_BADGES.get(st["state"], AUDIT_STATUS_BADGES["unknown"])
    if st["state"] == "findings":
        label = f"{st['count']} finding" + ("s" if st["count"] != 1 else "")
    text = {
        "ok": "Last run found no integrity issues.",
        "findings": "The last run reported integrity findings — open the log for the per-QR list.",
        "stale": "No output for over 2 hours; the scheduled job may have stopped.",
        "unknown": "No recognizable [AUDIT] marker in this log.",
    }[st["state"]]
    return {"badge": badge, "label": label, "tooltip": text, "state": st["state"]}


# ------------------ SLD Extraction Logs ------------------
SLD_FEEDBACK_DIR = Path(os.getenv("SLD_FEEDBACK_DIR", "/home/developer/sld_extract_feedback"))
SLD_REVIEW_BASE_URL = os.getenv("SLD_REVIEW_BASE_URL", "https://reviewel.assetcap.facilities.ubc.ca")
SLD_REVIEW_INTERNAL_BASE = os.getenv("SLD_REVIEW_INTERNAL_BASE", "http://127.0.0.1:8005")


def _sld_run_status_from_records(meta, summary, last_wrapper_event):
    """Derive a status string + short error message from the parsed events."""
    if last_wrapper_event:
        sub = (last_wrapper_event.get("subkind") or "").lower()
        if sub == "timeout":
            return "timeout", f"Timed out after {last_wrapper_event.get('timeout_s')}s"
        if sub == "non_zero_exit":
            tail = (last_wrapper_event.get("stderr_tail") or "").strip()
            return "error", (tail.splitlines()[-1] if tail else f"Exit code {last_wrapper_event.get('exit_code')}")
        if sub == "script_not_found":
            return "error", f"Script not found: {last_wrapper_event.get('script_path')}"
    if summary:
        if summary.get("exit_code") == 0:
            return "ok", None
        return "error", f"Exit code {summary.get('exit_code')}"
    if meta:
        return "running", None
    return "unknown", None


def _parse_sld_jsonl(path: Path):
    """Parse a single feedback JSONL. Returns (run_meta, run_summary, last_wrapper_event, model_call_count)."""
    meta = None
    summary = None
    last_wrapper = None
    model_calls = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                kind = rec.get("kind")
                if kind == "run_meta":
                    meta = meta or rec
                elif kind == "run_summary":
                    summary = rec
                elif kind == "wrapper_event":
                    last_wrapper = rec
                elif kind == "model_call":
                    model_calls += 1
    except OSError:
        pass
    return meta, summary, last_wrapper, model_calls


def _sld_runs_index(limit: int = 20) -> List[dict]:
    """List recent SLD extraction runs, newest first.

    Reads <SLD_FEEDBACK_DIR>/sld_*.jsonl (skipping corrections.jsonl), parses
    each file's run_meta + run_summary/wrapper_event, returns a list suitable
    for the AI Process Queue table.
    """
    if not SLD_FEEDBACK_DIR.exists():
        return []
    files = []
    try:
        for p in SLD_FEEDBACK_DIR.glob("sld_*.jsonl"):
            if p.name == "corrections.jsonl":
                continue
            try:
                files.append((p.stat().st_mtime, p))
            except OSError:
                continue
    except OSError:
        return []
    files.sort(key=lambda t: t[0], reverse=True)
    out = []
    for mtime, path in files[:limit]:
        meta, summary, last_wrapper, model_calls = _parse_sld_jsonl(path)
        if not meta:
            continue
        status, error_short = _sld_run_status_from_records(meta, summary, last_wrapper)
        when = "—"
        try:
            when = datetime.fromtimestamp(int(mtime)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        out.append({
            "run_id": meta.get("run_id") or path.stem,
            "ts": meta.get("ts"),
            "when": when,
            "building_code": meta.get("building_code") or "",
            "pdf_filename": meta.get("pdf_filename") or "",
            "status": status,
            "duration_s": (summary or {}).get("duration_s") or (last_wrapper or {}).get("duration_s"),
            "asset_count": (summary or {}).get("asset_count"),
            "model_calls": (summary or {}).get("model_calls", model_calls),
            "error_short": error_short,
            "jsonl_name": path.name,
        })
    return out


def _sld_run_detail(run_id: str) -> Optional[dict]:
    """Full read of one run: header + every model_call event + DB asset rows."""
    if not run_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", run_id)
    if not safe or safe != run_id:
        return None
    path = SLD_FEEDBACK_DIR / f"{safe}.jsonl"
    if not path.is_file():
        return None
    meta = None
    summary = None
    last_wrapper = None
    model_calls = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                kind = rec.get("kind")
                if kind == "run_meta":
                    meta = meta or rec
                elif kind == "run_summary":
                    summary = rec
                elif kind == "wrapper_event":
                    last_wrapper = rec
                elif kind == "model_call":
                    model_calls.append({
                        "label": rec.get("label"),
                        "model": rec.get("model"),
                        "latency_ms": rec.get("latency_ms"),
                        "ok": rec.get("ok"),
                        "error": (rec.get("error") or "")[:240],
                    })
    except OSError:
        return None
    if not meta:
        return None
    status, error_short = _sld_run_status_from_records(meta, summary, last_wrapper)
    rows = []
    try:
        conn = qrdb.get_connection(sqlite_path=str(_resolve_db_path()))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            'SELECT row_id, "Equipment ID", "Hierarchy", "Building", "Supply From", '
            '"Amperage Rating", "Amperage Rating (UoM)", "Voltage Rating", "Voltage Rating (UoM)", '
            'new_draw, source_pdf '
            'FROM electrical_building_schema WHERE sld_extract_run_id = ? '
            'ORDER BY "Hierarchy", "Equipment ID"',
            (safe,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as exc:
        print(f"[sld_run_detail] DB read failed for run {safe}: {exc}")
    return {
        "run_id": meta.get("run_id") or safe,
        "ts": meta.get("ts"),
        "building_code": meta.get("building_code") or "",
        "pdf_filename": meta.get("pdf_filename") or "",
        "pdf_path": meta.get("pdf_path") or "",
        "pdf_sha1": meta.get("pdf_sha1") or "",
        "script_sha1": meta.get("script_sha1") or "",
        "timeout_s": meta.get("timeout_s"),
        "feedback_file": meta.get("feedback_file") or str(path),
        "main_log": meta.get("main_log") or "",
        "status": status,
        "error_short": error_short,
        "duration_s": (summary or {}).get("duration_s") or (last_wrapper or {}).get("duration_s"),
        "asset_count_summary": (summary or {}).get("asset_count"),
        "hierarchy_count": (summary or {}).get("hierarchy_count"),
        "model_call_count": (summary or {}).get("model_calls", len(model_calls)),
        "wrapper_event": last_wrapper,
        "model_calls": model_calls,
        "asset_rows": rows,
        "jsonl_name": path.name,
    }


def _resolve_db_path() -> Path:
    """Best-effort resolver mirroring the rest of the app's conventions."""
    env_path = os.getenv("DB_PATH")
    if env_path:
        return Path(env_path)
    p = Path(parent_dir) / "asset_capture_app_dev" / "data" / "QR_codes.db"
    return p

def _extract_log_timestamp(line: str) -> Optional[datetime]:
    candidates = [
        (
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?",
            [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S,%f",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S,%f",
                "%Y-%m-%dT%H:%M:%S.%f",
            ],
        ),
        (r"\d{1,2}/\d{1,2}/\d{4}[ T]\d{2}:\d{2}:\d{2}", ["%m/%d/%Y %H:%M:%S"]),
        (r"\d{4}-\d{2}-\d{2}", ["%Y-%m-%d"]),
        (r"\d{1,2}/\d{1,2}/\d{4}", ["%m/%d/%Y"]),
    ]

    for pattern, formats in candidates:
        match = re.search(pattern, line)
        if not match:
            continue
        ts_text = match.group(0)
        for fmt in formats:
            try:
                return datetime.strptime(ts_text, fmt)
            except ValueError:
                continue
    return None

def _summarize_log(text: str = None, path: Path = None) -> str:
    """Efficiently summarizes a log file by reading line-by-line from a path."""
    if not path:
        lines = text.splitlines() if text else []
    else:
        lines = path.open("r", encoding="utf-8", errors="replace")

    db_conn = None

    def _normalize_asset_type(raw_value: str) -> str:
        token = re.sub(r"[^A-Za-z]", "", (raw_value or "")).upper()
        if token.startswith("ME"):
            return "ME"
        if token.startswith("EL"):
            return "EL"
        if token.startswith("BF"):
            return "BF"
        return token[:2] if token else ""

    qr_db_cache: Dict[str, Dict[str, str]] = {}

    def _lookup_qr_meta(qr: str) -> Dict[str, str]:
        nonlocal db_conn
        qr_key = (qr or "").strip()
        empty = {"building": "", "asset_type": ""}
        if not qr_key:
            return empty
        if qr_key in qr_db_cache:
            return qr_db_cache[qr_key]

        try:
            if db_conn is None:
                db_path = Path(DB_PATH)
                if not db_path.exists():
                    qr_db_cache[qr_key] = empty
                    return empty
                db_conn = qrdb.get_connection(sqlite_path=str(db_path))

            cursor = db_conn.cursor()
            cursor.execute(
                '''
                SELECT TRIM("Building Code"), TRIM("asset_type")
                FROM "QR_codes"
                WHERE TRIM("QR_code_ID") = TRIM(?)
                LIMIT 1
                ''',
                (qr_key,),
            )
            row = cursor.fetchone()

            if row is None:
                qr_no_zeros = qr_key.lstrip("0") or "0"
                cursor.execute(
                    '''
                    SELECT TRIM("Building Code"), TRIM("asset_type")
                    FROM "QR_codes"
                    WHERE LTRIM(TRIM("QR_code_ID"), '0') = ?
                    LIMIT 1
                    ''',
                    (qr_no_zeros,),
                )
                row = cursor.fetchone()

            meta = {
                "building": str(row[0] or "").strip() if row else "",
                "asset_type": _normalize_asset_type(str(row[1] or "").strip()) if row else "",
            }

            # PG has no ROWID; ctid is the physical-row tiebreaker (same
            # convention as SDI_process). SQLite keeps the legacy ROWID.
            rowid_order = "ctid" if qrdb.is_postgres() else "ROWID"

            if not meta["building"] or not meta["asset_type"]:
                cursor.execute(
                    f'''
                    SELECT TRIM("Building")
                    FROM "sdi_dataset_EL"
                    WHERE TRIM("QR Code") = TRIM(?)
                    ORDER BY {rowid_order} DESC
                    LIMIT 1
                    ''',
                    (qr_key,),
                )
                el_row = cursor.fetchone()
                if el_row:
                    if not meta["building"]:
                        meta["building"] = str(el_row[0] or "").strip()
                    if not meta["asset_type"]:
                        meta["asset_type"] = "EL"

            if not meta["building"]:
                cursor.execute(
                    f'''
                    SELECT TRIM("Building")
                    FROM "sdi_dataset"
                    WHERE TRIM("QR Code") = TRIM(?)
                    ORDER BY {rowid_order} DESC
                    LIMIT 1
                    ''',
                    (qr_key,),
                )
                sdi_row = cursor.fetchone()
                if sdi_row:
                    meta["building"] = str(sdi_row[0] or "").strip()

            if not meta["building"]:
                cursor.execute(
                    f'''
                    SELECT TRIM("Building")
                    FROM "sdi_print_out"
                    WHERE TRIM("QR Code") = TRIM(?)
                    ORDER BY {rowid_order} DESC
                    LIMIT 1
                    ''',
                    (qr_key,),
                )
                print_row = cursor.fetchone()
                if print_row:
                    meta["building"] = str(print_row[0] or "").strip()

            qr_db_cache[qr_key] = meta
            return meta
        except Exception:
            pass

        qr_db_cache[qr_key] = empty
        return empty

    try:
        blocks = []
        current = None
        last_seen_ts = None
        block_index = 0
        current_output_type = ""
        pending_assets = []
        qr_hints: Dict[str, Dict[str, str]] = {}
        recent_activity: List[str] = []

        summary_header_pattern = re.compile(r"---\s*SUMMARY\s*---", flags=re.IGNORECASE)
        summary_detail_pattern = re.compile(
            r"(Successfully saved:|Total assets processed(?: and saved)?:)\s*.+",
            flags=re.IGNORECASE,
        )
        summary_value_pattern = re.compile(
            r"(Successfully saved:|Total assets processed(?: and saved)?:)\s*(-?\d+)",
            flags=re.IGNORECASE,
        )
        output_for_pattern = re.compile(r"---\s*(?:START|END)?\s*Output for\s+([A-Za-z]+)\s*---", flags=re.IGNORECASE)
        success_qr_pattern = re.compile(
            r"Successfully processed and saved asset QR:\s*([A-Za-z0-9-]+)(?:\s*\(Completeness:\s*(\d+)%(?:\s*\|\s*Avg Conf:\s*(\d+)%)?\))?",
            flags=re.IGNORECASE,
        )
        processing_image_pattern = re.compile(
            r"Processing image\s+([T]?\d+)\s+(\d+(?:-\d+)?)\s+([A-Za-z]{2})\s*-\s*\d+",
            flags=re.IGNORECASE,
        )
        processing_asset_pattern = re.compile(
            r"Processing asset QR:\s*([A-Za-z0-9-]+)",
            flags=re.IGNORECASE,
        )
        recent_activity_pattern = re.compile(
            r"(START Output|END Output|Found \d+ pending items|Found \d+ new assets to process|"
            r"reflective feedback prepared|strategy '.*' succeeded|HTTP Request: POST|"
            r"Successfully processed and saved asset QR|Failed to process asset QR|No new assets found\. Exiting\.)",
            flags=re.IGNORECASE,
        )

        for raw in lines:
            line = raw.strip()
            line_ts = _extract_log_timestamp(line)
            if line_ts is not None:
                last_seen_ts = line_ts
            if recent_activity_pattern.search(line):
                recent_activity.append(line)
                if len(recent_activity) > 120:
                    recent_activity.pop(0)

            output_match = output_for_pattern.search(line)
            if output_match:
                current_output_type = _normalize_asset_type(output_match.group(1))
                continue

            process_image_match = processing_image_pattern.search(line)
            if process_image_match:
                qr_hint, building_hint, asset_type_hint = (
                    process_image_match.group(1),
                    process_image_match.group(2),
                    _normalize_asset_type(process_image_match.group(3)),
                )
                qr_hints[qr_hint] = {
                    "building": building_hint,
                    "asset_type": asset_type_hint,
                }
                continue

            process_asset_match = processing_asset_pattern.search(line)
            if process_asset_match:
                qr_hint = process_asset_match.group(1)
                hint = qr_hints.setdefault(qr_hint, {"building": "", "asset_type": ""})
                if not hint.get("asset_type") and current_output_type:
                    hint["asset_type"] = current_output_type
                continue

            success_match = success_qr_pattern.search(line)
            if success_match:
                qr_value = success_match.group(1).strip()
                hint = qr_hints.get(qr_value, {})
                db_meta = _lookup_qr_meta(qr_value)

                asset_type = (
                    _normalize_asset_type(hint.get("asset_type", ""))
                    or _normalize_asset_type(current_output_type)
                    or _normalize_asset_type(db_meta.get("asset_type", ""))
                    or "?"
                )
                building = (
                    str(hint.get("building") or "").strip()
                    or str(db_meta.get("building") or "").strip()
                    or "?"
                )
                event_ts = line_ts or last_seen_ts
                event_when = event_ts.strftime("%Y-%m-%d %H:%M:%S") if event_ts else "Unknown time"

                completeness_raw = success_match.group(2)
                completeness = f"{completeness_raw}%" if completeness_raw else ""
                
                try:
                    avg_conf_raw = success_match.group(3)
                except IndexError:
                    avg_conf_raw = None
                avg_conf = f"{avg_conf_raw}%" if avg_conf_raw else ""

                pending_assets.append(
                    {
                        "qr": qr_value,
                        "asset_type": asset_type,
                        "building": building,
                        "when": event_when,
                        "ts": event_ts,
                        "idx": len(pending_assets),
                        "completeness": completeness,
                        "avg_conf": avg_conf,
                    }
                )
                continue

            if summary_header_pattern.search(line):
                if current:
                    blocks.append(current)
                current = {
                    "header": "--- SUMMARY ---",
                    "details": [],
                    "ts": line_ts or last_seen_ts,
                    "idx": block_index,
                    "assets": list(pending_assets),
                    "show_values": False,
                }
                block_index += 1
                pending_assets = []
                continue

            if not summary_detail_pattern.search(line):
                continue

            if current is None:
                current = {
                    "header": "--- SUMMARY ---",
                    "details": [],
                    "ts": line_ts or last_seen_ts,
                    "idx": block_index,
                    "assets": list(pending_assets),
                    "show_values": False,
                }
                block_index += 1
                pending_assets = []

            if line not in current["details"]:
                current["details"].append(line)

            value_match = summary_value_pattern.search(line)
            if value_match:
                try:
                    if int(value_match.group(2)) >= 1:
                        current["show_values"] = True
                except Exception:
                    pass

            if current["ts"] is None:
                current["ts"] = line_ts or last_seen_ts
    finally:
        if hasattr(lines, 'close'):
            lines.close()
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass
            
    if current:
        blocks.append(current)

    if pending_assets:
        blocks.append(
            {
                "header": "--- SUMMARY ---",
                "details": [],
                "ts": pending_assets[-1].get("ts"),
                "idx": block_index,
                "assets": list(pending_assets),
                "show_values": False,
            }
        )

    if not blocks:
        return "No summary items found."

    blocks_with_ts = [block for block in blocks if block["ts"] is not None]
    blocks_without_ts = [block for block in blocks if block["ts"] is None]

    blocks_with_ts.sort(key=lambda block: (block["ts"], block["idx"]), reverse=True)
    blocks_without_ts.sort(key=lambda block: block["idx"], reverse=True)
    ordered_blocks = blocks_with_ts + blocks_without_ts

    output_lines = []
    for block in ordered_blocks:
        if not block.get("show_values"):
            continue

        header = block["header"] or "--- SUMMARY ---"
        output_lines.append(header)
        if block["details"]:
            output_lines.extend(block["details"])
        if block.get("assets"):
            if block["details"]:
                output_lines.append("")
            assets_sorted = sorted(
                block["assets"],
                key=lambda item: ((item.get("ts") is not None), item.get("ts"), item.get("idx", 0)),
                reverse=True,
            )
            for item in assets_sorted:
                comp = item.get('completeness', '')
                comp_suffix = f" | Completeness={comp}" if comp else ""
                
                conf = item.get('avg_conf', '')
                conf_suffix = f" | Avg Conf: {conf}" if conf else ""
                
                output_lines.append(
                    f"- QR: {item.get('qr', '?')} | {item.get('asset_type', '?')} | "
                    f"{item.get('when', 'Unknown time')} | Building: {item.get('building', '?')}"
                    f"{comp_suffix}{conf_suffix}"
                )
        output_lines.append("")

    if not output_lines and recent_activity:
        tail = list(reversed(recent_activity[-50:]))
        return (
            "No completed summary block found yet. "
            "The process may still be running.\n\n"
            "Display order: newest first.\n\n"
            "Recent activity:\n"
            + "\n".join(tail)
        )

    if not output_lines:
        return "No summary items found."

    while output_lines and output_lines[-1] == "":
        output_lines.pop()

    return "\n".join(output_lines)

def _order_log_text_desc(text: str) -> str:
    if not text:
        return text

    lines = text.splitlines()
    entries = []
    current = None

    for line in lines:
        ts = _extract_log_timestamp(line)
        if ts:
            if current:
                entries.append(current)
            current = {"ts": ts, "lines": [line]}
        else:
            if current is None:
                current = {"ts": None, "lines": [line]}
            else:
                current["lines"].append(line)

    if current:
        entries.append(current)

    with_ts = [entry for entry in entries if entry["ts"] is not None]
    without_ts = [entry for entry in entries if entry["ts"] is None]

    if with_ts:
        with_ts.sort(key=lambda entry: entry["ts"], reverse=True)
        ordered = with_ts + without_ts
    else:
        ordered = list(reversed(entries))

    return "\n".join("\n".join(entry["lines"]) for entry in ordered)


def _read_tail_text(path: Path, max_bytes: int) -> Tuple[str, int, int]:
    """Read at most `max_bytes` from the end of a text file and decode safely."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        total_size = f.tell()
        read_size = min(total_size, max_bytes)
        if read_size > 0:
            f.seek(-read_size, os.SEEK_END)
            data = f.read(read_size)
        else:
            data = b""

    text = data.decode("utf-8", errors="replace")
    # If truncated from the tail, drop partial first line for cleaner output.
    if read_size < total_size:
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
    return text, total_size, read_size


def _read_recent_log_window_text(path: Path, hours: int) -> str:
    """Read log entries newer than the given rolling time window."""
    cutoff = datetime.now() - timedelta(hours=hours)
    entries = []
    current = None
    entry_index = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            ts = _extract_log_timestamp(line)

            if ts is not None:
                if current and current.get("include"):
                    entries.append(current)
                current = {
                    "ts": ts,
                    "idx": entry_index,
                    "lines": [line],
                    "include": ts >= cutoff,
                }
                entry_index += 1
                continue

            if current is None:
                continue

            current["lines"].append(line)

    if current and current.get("include"):
        entries.append(current)

    if not entries:
        return (
            f"[Raw Window Mode] No log entries found in the last {hours} hours.\n\n"
            "Use the Download button to view the full log."
        )

    entries.sort(key=lambda entry: (entry["ts"], entry["idx"]), reverse=True)
    body = "\n".join("\n".join(entry["lines"]) for entry in entries)
    return (
        f"[Raw Window Mode] Showing only log entries from the last {hours} hours. "
        "Display order: newest first.\n\n"
        f"{body}"
    )


##-------------------------------------------------------------##
## Authentication Routes Blueprint                             ##
##-------------------------------------------------------------##
auth_bp = Blueprint('auth', __name__, template_folder='templates')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user, remember=request.form.get('remember'))
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        else:
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('auth.login'))
            
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

app.register_blueprint(auth_bp)


##-------------------------------------------------------------##
## Main Application Routes Blueprint                           ##
##-------------------------------------------------------------##
main_bp = Blueprint('main', __name__, template_folder='templates')

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _dashboard_admin_users() -> set:
    raw = os.getenv("DASHBOARD_ADMIN_USERS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}

def is_dashboard_admin() -> bool:
    if not current_user.is_authenticated:
        return False
    return str(current_user.username or "").strip().lower() in _dashboard_admin_users()

def dashboard_admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_dashboard_admin():
            return jsonify({"success": False, "message": "Administrator access required."}), 403
        return fn(*args, **kwargs)
    return wrapper

def _validate_admin_password(password: str, confirm_password: str) -> Optional[str]:
    if password != confirm_password:
        return "Passwords do not match."
    if len(password or "") < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password or ""):
        return "Password must contain at least one special character (e.g., !@#$%)."
    return None

def _validate_admin_name(name: str) -> Optional[str]:
    if not name:
        return "Name is required."
    if len(name) > 120:
        return "Name must be 120 characters or fewer."
    return None

def _serialize_auth_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name or "",
        "email": user.email,
        "active": getattr(user, "active", True),
        "is_admin": getattr(user, "is_admin", False),
    }

@main_bp.get("/")
@login_required
def index():
    building = request.args.get("building", "All")
    process_scope = request.args.get("process_scope", "all").strip().lower()
    if process_scope not in {"open", "all"}:
        process_scope = "all"
    selected_user = (request.args.get("user") or "All").strip() or "All"
    try:
        _users = User.query.order_by(User.username).all()
        user_options = [{"username": u.username, "display": (u.name or u.username)} for u in _users]
    except Exception:
        user_options = []
    _display_map = {u["username"]: u["display"] for u in user_options}
    _data_users = _get_users_with_data()
    user_options_for_filter = []
    for uname in _data_users:
        if uname == "All":
            user_options_for_filter.append({"username": "All", "display": "All"})
        else:
            user_options_for_filter.append({"username": uname, "display": _display_map.get(uname, uname)})
    _valid_usernames = {u["username"] for u in user_options_for_filter}
    if selected_user not in _valid_usernames:
        selected_user = "All"
    selected_user_display = next(
        (u["display"] for u in user_options_for_filter if u["username"] == selected_user),
        selected_user,
    )
    options = _get_building_options(None if selected_user == "All" else selected_user)
    if building not in options: building = "All"
    task_labels = {k: v.get("label", k) for k, v in TASKS.items()}
    ts = int(time.time())

    summary_data, details_data = None, None
    print("\n--- [Dashboard Load] ---")
    if FLOW_CHART_AVAILABLE:
        try:
            flow_quantity_chart.build_asset_workflow(db_path=DB_PATH)
        except Exception as e:
            print(f"WARNING: SDI flow chart refresh failed: {e}")
    if AI_STATUS_AVAILABLE:
        print("Attempting to fetch pending assets from 'ai_status_table' module...")
        try:
            summary, details = ai_status_table.get_pending_assets()
            if summary is not None and not summary.empty:
                summary_data = summary.to_dict(orient="records")
                print(f"SUCCESS: Loaded {len(summary_data)} summary rows.")
            else:
                 print("INFO: No summary data returned from module.")
            if details is not None and not details.empty:
                details_data = details.to_dict(orient="records")
                print(f"SUCCESS: Loaded {len(details_data)} detailed asset rows.")
            else:
                 print("INFO: No detailed data returned from module.")
        except Exception as e:
            print(f"CRITICAL ERROR fetching asset data: {e}")
    else:
        print("WARNING: 'ai_status_table' not available, skipping asset data fetch.")
    
    ai_pending_count = len(details_data) if details_data else 0

    recent_logs = []
    try:
        log_files = _system_log_paths()
        for p in log_files[:5]:
            ts_raw = _extract_ts_from_logname(p.name)
            recent_logs.append({
                "name": p.name,
                "when": _when_from_ts(ts_raw, path=p),
                "title": _title_from_logname(p.name),
            })
    except Exception as e:
        print(f"WARNING: Could not fetch recent logs: {e}")

    print("--- [Rendering Template] ---")

    return render_template(
        "dashboard.html", apps=APPS, task_labels=task_labels,
        chart_enabled=CHARTS_AVAILABLE, charts_error=CHARTS_IMPORT_ERROR,
        building_options=options, selected_building=building,
        user_options=user_options,
        user_options_for_filter=user_options_for_filter,
        selected_user=selected_user,
        selected_user_display=selected_user_display,
        ts=ts,
        selected_process_scope=process_scope,
        ai_status_summary=summary_data,
        ai_asset_details=details_data,
        ai_pending_count=ai_pending_count,
        completeness_chart_enabled=COMPLETENESS_CHART_AVAILABLE,
        ai_confidence_chart_enabled=AI_CONFIDENCE_CHART_AVAILABLE,
        data_quality_chart_enabled=DATA_QUALITY_CHART_AVAILABLE,
        operational_cost_chart_enabled=OPERATIONAL_COST_CHART_AVAILABLE,
        recent_logs=recent_logs,
        sld_runs=_sld_runs_index(),
        sld_review_base_url=SLD_REVIEW_BASE_URL,
        fls_default_attribute_code=FLS_DEFAULT_ATTRIBUTE_CODE,
        is_dashboard_admin=is_dashboard_admin(),
        current_user_is_site_admin=rbac_is_admin(current_user),
        username=current_user.username
    )

@main_bp.get("/api/admin/users")
@login_required
@dashboard_admin_required
def api_admin_users():
    users = User.query.order_by(User.username.asc()).all()
    return jsonify({"success": True, "users": [_serialize_auth_user(user) for user in users]})

@main_bp.post("/api/admin/users")
@login_required
@dashboard_admin_required
def api_admin_create_user():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    confirm_password = str(payload.get("confirm_password") or "")

    if not username:
        return jsonify({"success": False, "message": "Username is required."}), 400
    if not USERNAME_RE.fullmatch(username):
        return jsonify({"success": False, "message": "Username can contain only letters, numbers, underscores, periods, and hyphens."}), 400
    name_error = _validate_admin_name(name)
    if name_error:
        return jsonify({"success": False, "message": name_error}), 400
    if not email or not EMAIL_RE.fullmatch(email):
        return jsonify({"success": False, "message": "A valid email address is required."}), 400

    password_error = _validate_admin_password(password, confirm_password)
    if password_error:
        return jsonify({"success": False, "message": password_error}), 400

    existing_username = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if existing_username:
        return jsonify({"success": False, "message": f"Username '{username}' already exists."}), 409

    existing_email = User.query.filter(db.func.lower(User.email) == email.lower()).first()
    if existing_email:
        return jsonify({"success": False, "message": f"Email '{email}' already exists."}), 409

    new_user = User(username=username, name=name, email=email)
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"success": True, "message": f"User '{username}' created successfully.", "user": _serialize_auth_user(new_user)}), 201

@main_bp.put("/api/admin/users/<username>")
@login_required
@dashboard_admin_required
def api_admin_update_user(username):
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()

    name_error = _validate_admin_name(name)
    if name_error:
        return jsonify({"success": False, "message": name_error}), 400
    if not email or not EMAIL_RE.fullmatch(email):
        return jsonify({"success": False, "message": "A valid email address is required."}), 400

    user = User.query.filter(db.func.lower(User.username) == str(username or "").strip().lower()).first()
    if not user:
        return jsonify({"success": False, "message": f"User '{username}' was not found."}), 404

    existing_email = User.query.filter(
        db.func.lower(User.email) == email.lower(),
        User.id != user.id
    ).first()
    if existing_email:
        return jsonify({"success": False, "message": f"Email '{email}' already exists."}), 409

    user.name = name
    user.email = email
    db.session.commit()
    return jsonify({"success": True, "message": f"User '{user.username}' updated successfully.", "user": _serialize_auth_user(user)})

@main_bp.post("/api/admin/users/<username>/reset-password")
@login_required
@dashboard_admin_required
def api_admin_reset_password(username):
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    confirm_password = str(payload.get("confirm_password") or "")

    password_error = _validate_admin_password(password, confirm_password)
    if password_error:
        return jsonify({"success": False, "message": password_error}), 400

    user = User.query.filter(db.func.lower(User.username) == str(username or "").strip().lower()).first()
    if not user:
        return jsonify({"success": False, "message": f"User '{username}' was not found."}), 404

    user.set_password(password)
    db.session.commit()
    return jsonify({"success": True, "message": f"Password for '{user.username}' was reset successfully."})

@main_bp.patch("/api/admin/users/<username>/active")
@login_required
@dashboard_admin_required
def api_admin_toggle_active(username):
    user = User.query.filter(db.func.lower(User.username) == str(username or "").strip().lower()).first()
    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    new_val = payload.get("active")
    user.active = bool(new_val) if new_val is not None else not bool(getattr(user, "active", True))
    db.session.commit()
    return jsonify({"success": True, "active": user.active})

@main_bp.patch("/api/admin/users/<username>/admin")
@login_required
@dashboard_admin_required
def api_admin_toggle_admin(username):
    user = User.query.filter(db.func.lower(User.username) == str(username or "").strip().lower()).first()
    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    new_val = payload.get("is_admin")
    user.is_admin = bool(new_val) if new_val is not None else not bool(getattr(user, "is_admin", False))
    db.session.commit()
    return jsonify({"success": True, "is_admin": user.is_admin})

# ------------------ RBAC Permission Routes ------------------

@main_bp.get("/api/admin/registry")
@login_required
@dashboard_admin_required
def api_admin_registry():
    return jsonify(get_registry())

@main_bp.get("/api/admin/permissions/users")
@login_required
@dashboard_admin_required
def api_admin_permissions_users():
    users = User.query.order_by(User.username.asc()).all()
    result = []
    for u in users:
        grants = UserAccess.query.filter_by(user_id=u.id).all()
        result.append({
            "username": u.username,
            "name": u.name or "",
            "email": u.email,
            "grants": [
                {"section_key": g.section_key, "item_key": g.item_key, "access_level": g.access_level}
                for g in grants
            ],
        })
    return jsonify({"success": True, "users": result})

@main_bp.get("/api/admin/permissions/<username>")
@login_required
@dashboard_admin_required
def api_admin_permissions_get(username):
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404
    grants = UserAccess.query.filter_by(user_id=user.id).all()
    return jsonify({
        "success": True,
        "grants": [
            {"section_key": g.section_key, "item_key": g.item_key, "access_level": g.access_level}
            for g in grants
        ],
    })

@main_bp.put("/api/admin/permissions/<username>")
@login_required
@dashboard_admin_required
def api_admin_permissions_put(username):
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    grants = payload.get("grants", [])
    if not isinstance(grants, list):
        return jsonify({"success": False, "message": "grants must be an array."}), 400
    valid_levels = {"editor", "viewer"}
    for g in grants:
        if g.get("access_level") not in valid_levels:
            return jsonify({"success": False, "message": f"Invalid access_level '{g.get('access_level')}'."}), 400
    UserAccess.query.filter_by(user_id=user.id).delete()
    for g in grants:
        db.session.add(UserAccess(
            user_id=user.id,
            section_key=g.get("section_key", ""),
            item_key=g.get("item_key", ""),
            access_level=g["access_level"],
        ))
    db.session.commit()
    return jsonify({"success": True, "message": f"Permissions for '{username}' updated."})

@main_bp.delete("/api/admin/permissions/<username>")
@login_required
@dashboard_admin_required
def api_admin_permissions_delete(username):
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user:
        return jsonify({"success": False, "message": "User not found."}), 404
    UserAccess.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    return jsonify({"success": True, "message": f"All permissions for '{username}' cleared."})

# ------------------ [NEW] Dictionary Routes ------------------
@main_bp.route('/dictionary')
@login_required
def dictionary_index():
    if not has_permission(current_user, "dictionary", "dictionary", "viewer"):
        return access_denied_response("Dictionary")
    # Viewers get a read-only page: no Add button, no Actions column. The API
    # decorators remain the authority - this only stops the UI offering actions
    # that would fail.
    can_edit = has_permission(current_user, "dictionary", "dictionary", "editor")
    return render_template(
        'index_dictionary.html',
        can_edit=can_edit,
        asset_types=DICTIONARY_ALLOWED_TYPES,
        acshell_active='dict',
    )

@main_bp.route('/api/assets', methods=['GET'])
@login_required
@require_permission("dictionary", "dictionary", "viewer")
def get_dictionary_assets():
    data = read_dictionary()
    if not data:
        return jsonify([])
    
    assets_list = []
    for key, val in data.items():
        item = val.copy()
        # Split Key if Composite (Tag|Type)
        if '|' in key:
            code, atype = key.split('|', 1)
            item['asset_code'] = code
            item['asset_type'] = atype
        else:
            # Legacy Key Support
            item['asset_code'] = key
            item['asset_type'] = item.get('asset_type') or item.get('type') or ''
        
        item['unique_key'] = key  # Preserve actual dictionary key
        assets_list.append(item)
    return jsonify(assets_list)

@main_bp.route('/api/assets', methods=['POST'])
@login_required
@require_permission("dictionary", "dictionary", "editor")
def save_dictionary_asset():
    logs = []
    def log(msg):
        print(msg)
        logs.append(msg)
    
    asset = request.json
    
    # 1. INPUT PROCESSING
    raw_key = (asset.get('asset_code') or '').strip()
    raw_type = (asset.get('asset_type') or '').strip()
    
    if not raw_key or not raw_type:
        return jsonify({'success': False, 'message': 'Tag and Type are required'}), 400
    
    new_tag = raw_key.upper()
    new_type = raw_type.upper()

    if new_type not in DICTIONARY_ALLOWED_TYPES:
        return jsonify({
            'success': False,
            'message': f'Invalid asset type "{new_type}". Allowed types: {", ".join(DICTIONARY_ALLOWED_TYPES)}.'
        }), 400

    storage_key = f"{new_tag}|{new_type}"

    is_edit = bool(asset.get('is_edit'))
    original_key = asset.get('original_key')

    log(f"--- START SAVE ---")
    log(f"Target: {storage_key}")
    log(f"Mode: {'EDIT' if is_edit else 'ADD'}")

    # 2. LOAD DATA
    current_data = read_dictionary()
    if current_data is None:
        current_data = {}

    # Snapshot the entry being edited BEFORE the migration loop below drops it -
    # after that pass the original key is gone and the old values unrecoverable.
    old_entry = current_data.get(original_key) if (is_edit and original_key) else None

    # 3. MIGRATION & CLEANUP
    canonical_data = {}
    
    for k, v in current_data.items():
        # A. Remove old key if editing
        if is_edit and k == original_key:
            log(f"Removing original key: {original_key}")
            continue
        
        # B. Normalize/Migrate
        if '|' in k:
            canonical_key = k
            new_entry = v
        else:
            # Found a legacy key! Convert it.
            norm_tag = k.strip().upper()
            file_type = (v.get('asset_type') or v.get('type') or '').strip().upper()
            norm_type = file_type if file_type else 'ME'
            
            canonical_key = f"{norm_tag}|{norm_type}"
            log(f"Migrating Legacy Key: '{k}' -> '{canonical_key}'")
            
            new_entry = v.copy()
            new_entry['asset_type'] = norm_type
            new_entry['type'] = norm_type
        
        canonical_data[canonical_key] = new_entry
    
    current_data = canonical_data
    
    # 4. DUPLICATE CHECK
    if storage_key in current_data:
        log(f"ERROR: Key {storage_key} already exists.")
        return jsonify({'success': False, 'message': f'Error: The tag "{new_tag}" with type "{new_type}" already exists.', 'debug': logs}), 409
    
    # 5. SAVE NEW ENTRY
    saved_entry = {
        "attribute_set": asset.get('attribute_set', ''),
        "asset_group": asset.get('asset_group', ''),
        "main_asset": asset.get('main_asset', ''),
        "description": asset.get('description', ''),
        "asset_type": new_type,
        "type": new_type
    }
    current_data[storage_key] = saved_entry

    log(f"Saving to dictionary file...")

    if save_dictionary(current_data):
        log("Save Successful.")

        # 6. AUDIT. The logger drops unchanged pairs, so an edit records only the
        # fields that actually moved. "dictionary_key" carries renames and
        # guarantees an INSERT always writes at least one row.
        op_type = "UPDATE" if old_entry is not None else "INSERT"
        if old_entry is not None:
            field_changes = {f: (old_entry.get(f), saved_entry.get(f)) for f in _DICT_AUDIT_FIELDS}
            field_changes["dictionary_key"] = (original_key, storage_key)
        else:
            field_changes = {f: (None, saved_entry.get(f)) for f in _DICT_AUDIT_FIELDS}
            field_changes["dictionary_key"] = (None, storage_key)
        _log_dictionary_audit(
            op_type, storage_key, field_changes,
            f"Dictionary entry {'updated' if old_entry is not None else 'added'} via Dashboard: {storage_key}"
        )

        return jsonify({'success': True, 'message': 'Asset saved successfully', 'debug': logs})

    log("Save Failed (File I/O Error).")
    return jsonify({'success': False, 'message': 'Failed to write to file', 'debug': logs}), 500

@main_bp.route('/api/assets/delete', methods=['POST'])
@login_required
@require_permission("dictionary", "dictionary", "editor")
def delete_dictionary_asset():
    key = request.json.get('unique_key')
    data = read_dictionary()
    if data is None:
        return jsonify({'success': False, 'message': 'Failed to read dictionary file'}), 500
    
    if key in data:
        old_entry = data.get(key) or {}
        del data[key]
        if save_dictionary(data):
            field_changes = {f: (old_entry.get(f), None) for f in _DICT_AUDIT_FIELDS}
            field_changes["dictionary_key"] = (key, None)
            _log_dictionary_audit(
                "DELETE", key, field_changes,
                f"Dictionary entry deleted via Dashboard: {key}"
            )
            return jsonify({'success': True, 'message': 'Asset deleted successfully'})
        return jsonify({'success': False, 'message': 'Failed to save changes to dictionary file'}), 500
    return jsonify({'success': False, 'message': f'Asset not found in dictionary (key: {key})'}), 404

@main_bp.route('/api/main-assets', methods=['GET'])
@login_required
@require_permission("dictionary", "dictionary", "viewer")
def get_main_assets_dropdown():
    # Use global DB_PATH
    if not os.path.exists(DB_PATH):
        return jsonify([])
    conn = qrdb.get_connection(sqlite_path=DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT main_asset FROM main_asset_description ORDER BY main_asset ASC")
        rows = cursor.fetchall()
        options = [row[0] for row in rows if row[0]]
        return jsonify(options)
    except Exception as e:
        print(f"SQL Error (Main Assets): {e}")
        return jsonify([])
    finally:
        conn.close()

@main_bp.route('/api/asset-groups', methods=['GET'])
@login_required
@require_permission("dictionary", "dictionary", "viewer")
def get_asset_groups_dropdown():
    # Use global DB_PATH
    if not os.path.exists(DB_PATH):
        return jsonify([])
    conn = qrdb.get_connection(sqlite_path=DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT "Name" FROM "Asset_Group" ORDER BY "Name" ASC')
        rows = cursor.fetchall()
        options = [row[0] for row in rows if row[0]]
        return jsonify(options)
    except Exception as e:
        print(f"SQL Error (Asset Groups): {e}")
        return jsonify([])
    finally:
        conn.close()

@main_bp.route('/api/attributes', methods=['GET'])
@login_required
@require_permission("dictionary", "dictionary", "viewer")
def get_attributes_dropdown():
    # Ensure database exists
    if not os.path.exists(DB_PATH):
        return jsonify([])
    
    conn = qrdb.get_connection(sqlite_path=DB_PATH)
    try:
        cursor = conn.cursor()
        # Fetch distinct Codes from Attribute table
        cursor.execute('SELECT DISTINCT "Code" FROM "Attribute" ORDER BY "Code" ASC')
        rows = cursor.fetchall()
        # Flatten list and remove None/Empty values
        options = [row[0] for row in rows if row[0]]
        return jsonify(options)
    except Exception as e:
        print(f"SQL Error (Attributes): {e}")
        return jsonify([])
    finally:
        conn.close()

@main_bp.route('/map-new-assets', methods=['GET'])
@login_required
def map_new_assets_page():
    if not has_permission(current_user, "operations", "asset_map", "viewer"):
        return access_denied_response("Asset Map")
    return render_template('map_new_assets_by_building.html')

@main_bp.route('/api/map-new-assets', methods=['GET'])
@login_required
@require_permission("operations", "asset_map", "viewer")
def api_map_new_assets():
    if not MAP_CHART_AVAILABLE:
        return jsonify({"error": f"map_chart not available: {MAP_CHART_ERROR}"}), 500
    if not os.path.exists(DB_PATH):
        return jsonify({"error": f"DB not found at: {DB_PATH}"}), 404
    try:
        df = map_chart.map_new_assets_all(DB_PATH)
        df = df.fillna("")
        return jsonify(df.to_dict(orient="records"))
    except Exception as e:
        print(f"ERROR loading map-new-assets data: {e}")
        return jsonify({"error": f"Failed to load map assets: {e}"}), 500


@main_bp.get("/api/user-activity")
@login_required
@require_permission("operations", "user_activity", "viewer")
def get_user_activity():
    """
    Per-field audit trail of asset modifications across every Asset Capture
    app. Reads from `audit_trail` (replaces the old QR_code_assets-based
    activity log). Each row represents one field change with old/new values
    and an explicit Source ('human' | 'ai:<model>' | 'system').

    Query params:
        building : building name filter ("All" or omitted = no filter)
        limit    : max rows returned (1-500, default 250)
        source   : optional Source filter ('human', 'ai:gpt-5.5', 'ai:dictionary', 'system')
        user     : optional Modified_By filter
    """
    if not os.path.exists(DB_PATH):
        return jsonify({"error": "Database not found"}), 404

    building = (request.args.get("building") or "").strip()
    source_filter = (request.args.get("source") or "").strip()
    user_filter = (request.args.get("user") or "").strip()
    limit_raw = (request.args.get("limit") or "").strip()
    try:
        limit = int(limit_raw) if limit_raw else 250
    except (TypeError, ValueError):
        limit = 250
    limit = max(1, min(limit, 500))

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        cursor = conn.cursor()

        if not qrdb.has_table(conn, "audit_trail"):
            return jsonify({
                "error": "audit_trail table not found. Run scripts/migrate_create_audit_trail.py.",
                "data": [], "users": [], "sources": [],
                "building": building or "All", "limit": limit, "total": 0,
            }), 404

        where = ["1=1"]
        params: list = []
        if building and building.lower() != "all":
            # "Name" must be quoted: PG folds unquoted identifiers to lowercase.
            where.append('TRIM(b."Name") = TRIM(?)')
            params.append(building)
        if source_filter:
            where.append("a.source = ?")
            params.append(source_filter)
        if user_filter:
            where.append("a.modified_by = ?")
            params.append(user_filter)
        where_sql = " AND ".join(where)

        base_from = """
            FROM audit_trail a
            LEFT JOIN "QR_codes" q ON q."QR_code_ID" = a.qr_code
            LEFT JOIN "Buildings" b ON TRIM(b."Code") = TRIM(q."Building Code")
        """

        cursor.execute(f"SELECT COUNT(*) {base_from} WHERE {where_sql}", params)
        total = (cursor.fetchone() or [0])[0]

        data_sql = f"""
            SELECT
                a.qr_code, a.modified_by, a.modification_date, a.modification_time,
                COALESCE(q.asset_type, '') AS asset_type,
                a.field_name, a.old_value, a.new_value,
                a.source, a.app_name, a.op_type, a.description, a.id
            {base_from}
            WHERE {where_sql}
            ORDER BY a.id DESC
            LIMIT ?
        """
        cursor.execute(data_sql, [*params, limit])
        rows = cursor.fetchall()
        user_name_map = {
            str(user.username or "").strip().lower(): (user.name or "")
            for user in User.query.with_entities(User.username, User.name).all()
        }

        # Distinct users + sources for filter dropdowns
        cursor.execute(
            "SELECT DISTINCT modified_by FROM audit_trail "
            "WHERE modified_by IS NOT NULL AND modified_by <> '' ORDER BY modified_by"
        )
        users = [r[0] for r in cursor.fetchall()]
        cursor.execute(
            "SELECT DISTINCT source FROM audit_trail "
            "WHERE source IS NOT NULL AND source <> '' ORDER BY source"
        )
        sources = [r[0] for r in cursor.fetchall()]

        activity_data = []
        for row in rows:
            (qr_code, mod_by, date_part, time_full, asset_type,
             field_name, old_value, new_value,
             source, app_name, op_type, description, audit_id) = row
            time_part = (time_full or "")[:8]  # HH:MM:SS
            activity_data.append({
                "id": audit_id,
                "qr_code": qr_code or "",
                "asset_type": asset_type or "",
                "user": mod_by or "",
                "name": user_name_map.get(str(mod_by or "").strip().lower(), ""),
                "date": date_part or "",
                "hour": time_part,
                "field_name": field_name or "",
                "old_value": old_value if old_value is not None else "",
                "new_value": new_value if new_value is not None else "",
                "source": source or "",
                "app_name": app_name or "",
                "op_type": op_type or "",
                "description": description or "",
            })

        return jsonify({
            "data": activity_data,
            "users": users,
            "sources": sources,
            "building": building or "All",
            "limit": limit,
            "total": total,
        })

    except sqlite3.Error as e:
        print(f"Database error in get_user_activity: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@main_bp.get("/api/reviewer-analysis/bar-hitboxes")
@login_required
@require_permission("operations", "reviewer_kpis", "viewer")
def reviewer_analysis_bar_hitboxes():
    """
    Provides hitboxes for the Reviewer Analysis bar chart ("Assets by Group").
    Used client-side to map mouse position -> Asset Group / segment.
    """
    if not CHARTS_AVAILABLE:
        return jsonify({"error": "Chart module unavailable"}), 503

    building = (request.args.get("building") or "All").strip() or "All"
    status = (request.args.get("status") or "all").strip() or "all"
    user = (request.args.get("user") or "").strip() or None
    try:
        payload = approval_mod.render_bar_hitboxes(building=building, status=status, user=user)
        return jsonify(payload)
    except Exception as e:
        print(f"Error generating bar hitboxes for building '{building}': {e}")
        return jsonify({"error": str(e)}), 500


@main_bp.get("/api/reviewer-analysis/hover")
@login_required
@require_permission("operations", "reviewer_kpis", "viewer")
def reviewer_analysis_hover():
    """
    Hover payload for Reviewer Analysis charts.

    Returns a (limited) list of assets for the selected building with:
      - QR code
      - inferred asset type (ME/BF/EL) when not available elsewhere
      - latest tracked user + date (when available)
      - fallback date from QR_codes.date_set (when available)
    """
    if not os.path.exists(DB_PATH):
        return jsonify({"error": "Database not found"}), 404

    building = (request.args.get("building") or "").strip()
    asset_group = (request.args.get("asset_group") or "").strip()
    approved_raw = (request.args.get("approved") or request.args.get("status") or "").strip()

    approved_label = ""
    if approved_raw:
        normalized = approved_raw.lower().replace("_", " ").strip()
        if normalized in {"1", "approved", "yes", "true"}:
            approved_label = "Approved"
        elif normalized in {"0", "not approved", "unapproved", "no", "false"}:
            approved_label = "Not Approved"
    limit_raw = (request.args.get("limit") or "").strip()
    try:
        limit = int(limit_raw) if limit_raw else 1000
    except (TypeError, ValueError):
        limit = 1000
    limit = max(1, min(limit, 5000))

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        cursor = conn.cursor()

        building_filter_sql = ""
        params = []
        if building and building.lower() != "all":
            building_filter_sql = 'WHERE TRIM(b."Name") = TRIM(?)'
            params.append(building)

        asset_filters = []
        asset_params = []
        if asset_group:
            asset_filters.append("TRIM(asset_group) = TRIM(?)")
            asset_params.append(asset_group)
        if approved_label:
            asset_filters.append("approved_label = ?")
            asset_params.append(approved_label)

        assets_where_sql = ""
        if asset_filters:
            assets_where_sql = "WHERE " + " AND ".join(asset_filters)

        # SQLite uses INSTR(); PG uses strpos(). Pick the first space-separated token
        # of code_assets (= the QR prefix) with a backend-correct expression.
        first_word_expr = (
            "split_part(code_assets, ' ', 1)" if qrdb.is_postgres()
            else "SUBSTR(code_assets, 1, INSTR(code_assets || ' ', ' ') - 1)"
        )
        base_cte = (
            f"""
            WITH assets_raw AS (
                SELECT
                    TRIM("QR Code") AS qr_code,
                    TRIM("Building") AS building_code,
                    TRIM("Asset Group") AS asset_group,
                    TRIM("Approved") AS approved_raw,
                    CASE
                        WHEN LOWER(TRIM("Asset Group")) LIKE '%backflow%' THEN 'BF'
                        ELSE 'ME'
                    END AS inferred_type
                FROM "sdi_dataset"
                WHERE "QR Code" IS NOT NULL AND TRIM("QR Code") <> ''

                UNION ALL

                SELECT
                    TRIM("QR Code") AS qr_code,
                    TRIM("Building") AS building_code,
                    TRIM("Asset Group") AS asset_group,
                    TRIM("Approved") AS approved_raw,
                    'EL' AS inferred_type
                FROM "sdi_dataset_EL"
                WHERE "QR Code" IS NOT NULL AND TRIM("QR Code") <> ''
            ),
            assets AS (
                SELECT
                    qr_code,
                    building_code,
                    asset_group,
                    inferred_type,
                    CASE
                        WHEN approved_raw = '1' THEN 'Approved'
                        ELSE 'Not Approved'
                    END AS approved_label
                FROM assets_raw
            ),
            assets_filtered AS (
                SELECT
                    qr_code,
                    building_code,
                    asset_group,
                    inferred_type,
                    approved_label
                FROM assets
                """
            + assets_where_sql
            + f"""
            ),
            assets_dedup AS (
                SELECT
                    qr_code,
                    building_code,
                    asset_group,
                    approved_label,
                    MAX(inferred_type) AS inferred_type
                FROM assets_filtered
                GROUP BY qr_code, building_code, asset_group, approved_label
            ),
            parsed_activity AS (
                SELECT
                    "ID",
                    {first_word_expr} AS qr_code,
                    "user",
                    "date_hour"
                FROM "QR_code_assets"
                WHERE "user" IS NOT NULL AND TRIM("user") <> ''
            ),
            latest_activity AS (
                SELECT qr_code, MAX("ID") AS latest_id
                FROM parsed_activity
                GROUP BY qr_code
            ),
            latest_rows AS (
                SELECT p.qr_code, p."user", p."date_hour"
                FROM parsed_activity p
                JOIN latest_activity l ON l.qr_code = p.qr_code AND l.latest_id = p."ID"
            )
        """
        )

        count_sql = (
            base_cte
            + """
            SELECT COUNT(*)
            FROM assets_dedup a
            LEFT JOIN "Buildings" b ON TRIM(b."Code") = a.building_code
            """
            + building_filter_sql
        )
        cursor.execute(count_sql, [*asset_params, *params])
        total = (cursor.fetchone() or [0])[0]

        data_sql = (
            base_cte
            + """
            SELECT
                a.qr_code,
                COALESCE(NULLIF(TRIM(q.asset_type), ''), a.inferred_type, '') AS asset_type,
                COALESCE(lr."user", '') AS user,
                COALESCE(NULLIF(TRIM(lr."date_hour"), ''), NULLIF(TRIM(q."date_set"), ''), '') AS raw_date
            FROM assets_dedup a
            LEFT JOIN "QR_codes" q ON TRIM(q."QR_code_ID") = a.qr_code
            LEFT JOIN latest_rows lr ON TRIM(lr.qr_code) = a.qr_code
            LEFT JOIN "Buildings" b ON TRIM(b."Code") = a.building_code
            """
            + building_filter_sql
            + """
            ORDER BY
                (lr."date_hour" IS NOT NULL AND TRIM(lr."date_hour") <> '') DESC,
                lr."date_hour" DESC,
                q."date_set" DESC,
                a.qr_code ASC
            LIMIT ?
            """
        )
        cursor.execute(data_sql, [*asset_params, *params, limit])
        rows = cursor.fetchall()

        data = []
        for qr_code, asset_type, user, raw_date in rows:
            qr_code = (qr_code or "").strip()
            asset_type = (asset_type or "").strip()
            user = (user or "").strip()
            raw_date = (raw_date or "").strip()

            date_part = ""
            if raw_date:
                if "T" in raw_date:
                    date_part = raw_date.split("T", 1)[0]
                elif " " in raw_date:
                    date_part = raw_date.split(" ", 1)[0]
                else:
                    date_part = raw_date

            data.append(
                {
                    "qr_code": qr_code,
                    "asset_type": asset_type,
                    "user": user,
                    "date": date_part,
                }
            )

        return jsonify(
            {
                "data": data,
                "building": building or "All",
                "asset_group": asset_group or "",
                "approved": approved_label or "",
                "limit": limit,
                "total": total,
            }
        )

    except sqlite3.Error as e:
        print(f"Database error in reviewer_analysis_hover: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()


def find_photo_for_qr(qr_code: str) -> Optional[str]:
    """
    Locate a photo whose filename starts with the QR code and ends with '- 2' (before the extension).
    Returns the matching filename (not full path) or None if not found.
    """
    if not qr_code or not PHOTO_UPLOAD_DIR.exists():
        return None

    try:
        sanitized_qr = re.sub(r"[^A-Za-z0-9_-]", "", qr_code)
        if not sanitized_qr:
            return None

        for path in sorted(PHOTO_UPLOAD_DIR.glob(f"{sanitized_qr}*")):
            if not path.is_file():
                continue
            stem = path.stem.rstrip()
            if stem.lower().startswith(sanitized_qr.lower()) and stem.endswith("- 2"):
                return path.name
    except Exception as e:
        print(f"Error locating photo for QR '{qr_code}': {e}")
    return None


@main_bp.route('/api/asset-photo/<path:qr_code>', methods=['GET'])
@login_required
def api_asset_photo(qr_code):
    photo_name = find_photo_for_qr(qr_code)
    if not photo_name:
        return jsonify({"url": None, "message": "Photo not found"}), 404
    return jsonify({"url": url_for('main.get_asset_photo_file', filename=photo_name)})


@main_bp.route('/asset-photo/<path:filename>', methods=['GET'])
@login_required
def get_asset_photo_file(filename):
    if not PHOTO_UPLOAD_DIR.exists():
        abort(404)
    try:
        return send_from_directory(PHOTO_UPLOAD_DIR, filename)
    except FileNotFoundError:
        abort(404)


# ------------------ Disposed Assets ------------------
# Disposal withdraws a QR from the capture/review/SDI pipeline: it archives a
# full snapshot to disposed_assets, deletes the curated SDI row, and leaves
# every file on disk so the whole operation is one database transaction.
# Only unapproved assets can be disposed; the reason is always required.
# Reads are open to any signed-in user, mutations require the editor grant.
#
# The level is "editor", not "admin": the User Admin permission matrix only
# offers none/viewer/editor and api_admin_permissions_put rejects any other
# level, so an "admin" requirement is ungrantable through the platform's own UI
# and would lock everyone out of disposal (2026-08-12 fix). Editor is the house
# mutate level - same as dictionary and FLS Devices - and the grant is handed
# out only to administrators.

DISPOSAL_PERM = ("operations", "disposed_assets", "editor")


def _disposal_unavailable():
    return jsonify({
        "success": False,
        "error": "The Disposed tool is not available on this server.",
    }), 503


def _photo_payload(filenames):
    """Turn photo filenames into renderable URLs for the register."""
    payload = []
    for entry in filenames or []:
        name = entry.get("filename") if isinstance(entry, dict) else entry
        available = entry.get("available", True) if isinstance(entry, dict) else True
        if not name:
            continue
        payload.append({
            "filename": name,
            "available": bool(available),
            "url": url_for('main.get_asset_photo_file', filename=name) if available else None,
        })
    return payload


@main_bp.get("/api/disposed-assets/lookup/<path:qr_code>")
@login_required
def disposed_assets_lookup(qr_code):
    """Preview an asset and show whether it can be disposed."""
    if not DISPOSAL_AVAILABLE:
        return _disposal_unavailable()

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        qr_row = disposal_svc.lookup_qr_row(conn, qr_code)
        structured_name, structured = ("", {})
        sdi_row = None
        resolved_qr = ""
        asset_type = ""

        if qr_row is not None:
            resolved_qr = str(qr_row.get("QR_code_ID") or "").strip()
            structured_name, structured = disposal_svc.find_structured_json(resolved_qr)
            asset_type = disposal_svc.normalize_asset_type(str(qr_row.get("asset_type") or ""))
            if not asset_type and structured_name:
                match = re.match(r"^[^_]+_([A-Za-z]+)_", structured_name)
                if match:
                    asset_type = disposal_svc.normalize_asset_type(match.group(1))
            sdi_table = disposal_svc.sdi_table_for(asset_type)
            if sdi_table:
                sdi_row = disposal_svc.fetch_sdi_row(conn, sdi_table, resolved_qr)

        checks = disposal_svc.evaluate_eligibility(conn, qr_code, qr_row, structured, sdi_row)
        existing = disposal_svc.active_disposal(conn, resolved_qr) if resolved_qr else None

        if qr_row is None:
            return jsonify({
                "success": True, "found": False, "eligible": False,
                "checks": checks, "asset": None,
            })

        structured_data = (structured or {}).get("structured_data") or {}
        return jsonify({
            "success": True,
            "found": True,
            "eligible": disposal_svc.eligibility_passed(checks),
            "checks": checks,
            "already_disposed_id": existing.get("id") if existing else None,
            "asset": {
                "qr_code": resolved_qr,
                "asset_type": asset_type,
                "building_code": str(qr_row.get("Building Code") or "").strip(),
                "location": str(qr_row.get("Location") or "").strip(),
                "approved": str(qr_row.get("Approved") or "").strip(),
                "ai_status": str(qr_row.get("ai_status") or "").strip(),
                "has_sdi_row": sdi_row is not None,
                "structured_data": structured_data,
                "photos": _photo_payload(disposal_svc.find_photos_for_qr(resolved_qr)),
            },
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@main_bp.post("/api/disposed-assets")
@login_required
@require_permission(*DISPOSAL_PERM)
def disposed_assets_create():
    """Post a disposal. The whole operation is one transaction."""
    if not DISPOSAL_AVAILABLE:
        return _disposal_unavailable()

    payload = request.get_json(silent=True) or {}
    qr_code = str(payload.get("qr_code") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    notes = str(payload.get("notes") or "").strip()

    if not qr_code:
        return jsonify({"success": False, "error": "Enter a QR code."}), 400

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        result = disposal_svc.dispose_asset(
            conn, qr_code, reason, notes,
            modified_by=getattr(current_user, "username", None) or "system",
        )
        conn.commit()
        return jsonify({
            "success": True,
            "message": f"Asset {result['qr_code']} disposed.",
            "disposal": result,
        })
    except disposal_svc.DisposalError as e:
        if conn is not None:
            conn.rollback()
        return jsonify({"success": False, "error": e.message, "checks": e.checks}), e.status
    except Exception as e:
        if conn is not None:
            conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@main_bp.get("/api/disposed-assets")
@login_required
def disposed_assets_list():
    """The disposal register."""
    if not DISPOSAL_AVAILABLE:
        return _disposal_unavailable()

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        rows = disposal_svc.list_disposals(
            conn,
            search=request.args.get("q", ""),
            reason=request.args.get("reason", ""),
            asset_type=request.args.get("asset_type", ""),
            status=request.args.get("status", ""),
            date_from=request.args.get("date_from", ""),
            date_to=request.args.get("date_to", ""),
            limit=int(request.args.get("limit", 500) or 500),
        )
        return jsonify({
            "success": True,
            "rows": rows,
            "reasons": list(disposal_svc.DISPOSAL_REASONS),
            "can_dispose": bool(has_permission(current_user, *DISPOSAL_PERM)),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@main_bp.get("/api/disposed-assets/<int:disposal_id>")
@login_required
def disposed_assets_detail(disposal_id):
    """One archived disposal, snapshots expanded and photos re-probed."""
    if not DISPOSAL_AVAILABLE:
        return _disposal_unavailable()

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        detail = disposal_svc.get_disposal_detail(conn, disposal_id)
        if detail is None:
            return jsonify({"success": False, "error": "Disposal not found."}), 404
        detail["photos"] = _photo_payload(detail.get("photos"))
        return jsonify({
            "success": True,
            "disposal": detail,
            "can_dispose": bool(has_permission(current_user, *DISPOSAL_PERM)),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@main_bp.post("/api/disposed-assets/<int:disposal_id>/restore")
@login_required
@require_permission(*DISPOSAL_PERM)
def disposed_assets_restore(disposal_id):
    """Reverse a disposal, rebuilding the curated row from its snapshot."""
    if not DISPOSAL_AVAILABLE:
        return _disposal_unavailable()

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        result = disposal_svc.restore_asset(
            conn, disposal_id,
            modified_by=getattr(current_user, "username", None) or "system",
        )
        conn.commit()
        message = f"Asset {result['qr_code']} restored."
        if not result.get("review_json_present"):
            message += (" Its review file is no longer on disk, so it will not"
                        " reappear in the review dashboard until the file is regenerated.")
        return jsonify({"success": True, "message": message, "restore": result})
    except disposal_svc.DisposalError as e:
        if conn is not None:
            conn.rollback()
        return jsonify({"success": False, "error": e.message}), e.status
    except Exception as e:
        if conn is not None:
            conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@main_bp.get("/api/fls-assets")
@main_bp.get("/data/fls_assets")
@login_required
@require_permission("operations", "fls_devices", "viewer")
def get_fls_asset_data():
    """
    Provides all necessary data for the FLS Assets table view.
    ALL data is now sourced from QR_codes.db using sqlite3.
    """
    print("--- [START] get_fls_asset_data ---")
    
    # --- [NEW] Schema Migration Check ---
    # Ensure the new columns exist in the new_device table
    _ensure_new_device_columns()
    
    # 1. Initialize empty lists
    all_properties = [] 
    filter_properties = [] 
    spaces_by_prop = {}
    device_map = {}
    asset_group_options = []
    asset_group_lookup_map = {}
    asset_group_name_to_option_map = {}  # Maps simple name to full option
    existing_assets = []
    property_asset_tag_map = {}
    property_name_to_code_map = {}
    property_control_panel_map = {}
    conn = None

    try:
        print("Connecting to database...")
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        conn.row_factory = sqlite3.Row  # This allows accessing columns by name
        cursor = conn.cursor()
        print("Database connection successful.")

        # --- 1a. Get Property List for MODAL (from Buildings table) ---
        try:
            print("Executing query for 'Buildings' table...")
            # Fetch both Name and Code to build the name->code map
            cursor.execute('SELECT "Name", "Code" FROM "Buildings" ORDER BY "Name"')
            print("Query for 'Buildings' executed. Fetching rows...")
            for row in cursor.fetchall():
                all_properties.append(row['Name']) 
                property_name_to_code_map[row['Name']] = row['Code']
            print(f"Successfully fetched {len(all_properties)} properties from 'Buildings'.")
        except sqlite3.Error as e:
            print(f"WARNING: Could not query 'Buildings' table from QR_codes.db: {e}")
            all_properties = []

        # --- 1b. Get Property List for FILTER (from new_device table) ---
        try:
            print("Executing query for distinct 'Property' from 'new_device'...")
            cursor.execute('''
                SELECT DISTINCT "Property" FROM new_device 
                WHERE "Property" IS NOT NULL AND TRIM("Property") != '' 
                ORDER BY "Property"
            ''')
            print("Query for distinct 'Property' executed. Fetching rows...")
            for row in cursor.fetchall():
                filter_properties.append(row['Property'])
            print(f"Found {len(filter_properties)} distinct properties in new_device for filtering.")
        except sqlite3.Error as e:
            print(f"WARNING: Could not query 'new_device' for distinct properties: {e}")
            filter_properties = [] 

        # --- 2. Get Spaces by Property (from Buildings_with_SpaceUID view) ---
        spaces_by_prop = {}
        try:
            print("Executing query for 'Buildings_with_SpaceUID' view...")
            cursor.execute('''
                SELECT "Name", "Location" FROM "Buildings_with_SpaceUID"
                WHERE "Location" IS NOT NULL AND TRIM("Location") != ''
            ''')
            print("Query for 'Buildings_with_SpaceUID' executed. Fetching rows...")
            for row in cursor.fetchall():
                property_name = row['Name']
                space_location = row['Location']
                if property_name not in spaces_by_prop:
                    spaces_by_prop[property_name] = []
                spaces_by_prop[property_name].append(space_location)
            print(f"Successfully fetched spaces for {len(spaces_by_prop)} properties.")
        except sqlite3.Error as e:
            print(f"WARNING: Could not query 'Buildings_with_SpaceUID' view from QR_codes.db: {e}")
            spaces_by_prop = {}
        
        # --- 3. Get Asset Group Options AND Device Type Map from 'fls_asset_group' ---
        device_map = {}
        asset_group_options = []
        asset_group_lookup_map = {}
        try:
            print("Executing query for 'fls_asset_group' table...")
            # NOTE: "Device Address"/"Description" are NOT columns of fls_asset_group.
            # SQLite silently falls back to treating an unknown double-quoted identifier as a string
            # literal (quirk); PG correctly errors. Using single quotes makes the intent explicit and
            # works on both engines — same value the SQLite path returned.
            cursor.execute("""
                SELECT
                    "Full Classification",
                    "Device Type",
                    ? AS "Attribute Set",
                    'Device Address' AS "Device Address",
                    'Description' AS "Description",
                    CASE
                        WHEN "Name" IS NOT NULL AND "Full Classification" IS NOT NULL
                            THEN "Name" || ' | ' || "Full Classification"
                        WHEN "Name" IS NULL
                            THEN "Full Classification"
                        ELSE "Name"
                    END AS "AssetGroupOption"
                FROM "fls_asset_group"
                ORDER BY "AssetGroupOption"
            """, (FLS_DEFAULT_ATTRIBUTE_CODE,))
            print("Query for 'fls_asset_group' executed. Fetching rows...")
            for row in cursor.fetchall():
                row_dict = dict(row)
                asset_group_option = row_dict.get('AssetGroupOption')
                if not asset_group_option:
                    continue
                
                asset_group_option = asset_group_option.strip()
                asset_group_options.append(asset_group_option)

                full_classification = row_dict.get('Full Classification')
                device_type = row_dict.get('Device Type')

                if full_classification and device_type:
                    device_map[full_classification] = device_type

                asset_group_lookup_map[asset_group_option] = {
                    'description': row_dict.get('Description'),
                    'attribute_set': row_dict.get('Attribute Set'),
                    'device_address': row_dict.get('Device Address'),
                    'device_type': device_type
                }
                
                # Build reverse mapping: simple name -> full option
                # Extract the Name part (before the |) to map it to the full option
                if ' | ' in asset_group_option:
                    simple_name = asset_group_option.split(' | ')[0].strip()
                    asset_group_name_to_option_map[simple_name] = asset_group_option
                else:
                    asset_group_name_to_option_map[asset_group_option] = asset_group_option
            print(f"Successfully built asset group lookup map with {len(asset_group_lookup_map)} entries.")
            print(f"DEBUG: asset_group_name_to_option_map contains {len(asset_group_name_to_option_map)} entries")
            if len(asset_group_name_to_option_map) > 0:
                first_few = list(asset_group_name_to_option_map.items())[:3]
                print(f"DEBUG: First few mappings: {first_few}")

        except sqlite3.Error as e:
            print(f"WARNING: Could not query 'fls_asset_group' table from QR_codes.db: {e}")
            device_map = {}
            asset_group_options = []
            asset_group_lookup_map = {}

        # --- 4. Get Property to Asset Tag mapping from 'Asset_System_info' ---
        try:
            print("Executing query for 'Asset_System_info' view...")
            cursor.execute('SELECT "Property code", "Asset Tag" FROM "Asset_System_info"')
            print("Query for 'Asset_System_info' executed. Fetching rows...")
            for row in cursor.fetchall():
                if row['Property code'] and row['Asset Tag']:
                    property_asset_tag_map[row['Property code']] = row['Asset Tag']
            print(f"Successfully built property-to-asset-tag map with {len(property_asset_tag_map)} entries.")
        except sqlite3.Error as e:
            print(f"WARNING: Could not query 'Asset_System_info' view from QR_codes.db: {e}")
            property_asset_tag_map = {}

        # --- 4b. Get first Control Panel Code/Description by Property code ---
        # The asset master may contain more than one row per property. The UI
        # shows the lowest Code and flags the property when multiple rows exist.
        try:
            print("Executing query for 'UBC - Asset Data Master Info' control panel lookup...")
            cursor.execute('''
                SELECT "Property code", "Code", "Description"
                FROM "UBC - Asset Data Master Info"
                WHERE "Property code" IS NOT NULL
                  AND TRIM("Property code") != ''
                ORDER BY "Property code", "Code"
            ''')
            control_panel_counts = {}
            for row in cursor.fetchall():
                property_code = str(row['Property code']).strip()
                if not property_code:
                    continue
                control_panel_counts[property_code] = control_panel_counts.get(property_code, 0) + 1
                if property_code not in property_control_panel_map:
                    property_control_panel_map[property_code] = {
                        "code": row['Code'] or "",
                        "description": row['Description'] or "",
                        "match_count": 0,
                        "has_multiple": False
                    }

            for property_code, match_count in control_panel_counts.items():
                property_control_panel_map[property_code]["match_count"] = match_count
                property_control_panel_map[property_code]["has_multiple"] = match_count > 1

            print(f"Successfully built control-panel lookup map with {len(property_control_panel_map)} entries.")
        except sqlite3.Error as e:
            print(f"WARNING: Could not query 'UBC - Asset Data Master Info' for control panel lookup: {e}")
            property_control_panel_map = {}
        
        
        def control_panel_for_property(property_name):
            property_code = property_name_to_code_map.get(property_name)
            if property_code is None:
                return {}
            return property_control_panel_map.get(str(property_code).strip(), {})

        # --- 5. Get existing assets from 'new_device' table ---
        print("Executing query for 'new_device' table...")
        
        # Standard select
        cursor.execute('SELECT * FROM new_device ORDER BY "Creation Date" DESC')
        
        print("Query for 'new_device' executed. Fetching rows...")
        rows = cursor.fetchall()
        
        if not rows:
            print("FLS get_fls_asset_data: 'new_device' table is empty.")
        
        for row in rows:
            row_dict = dict(row)
            status_val = row_dict.get('Status') 
            
            if str(status_val) not in ('0', '1'):
                workflow_val = row_dict.get('Workflow')
                workflow_normalized = (workflow_val or "").strip().lower()
                status_val = '1' if workflow_normalized.startswith('complete') else '0'
            else:
                status_val = str(status_val)
            
            # Get the asset group from the database
            db_asset_group = row_dict.get('Asset Group')
            # Look up the full AssetGroupOption using the name-to-option mapping
            asset_group_option = asset_group_name_to_option_map.get(db_asset_group, db_asset_group)
            
            # --- [FIX] FORCE .00 FORMAT FOR WORK ORDER ---
            # If the database stored '339998.00' as the integer '339998', we manually reconstruct the format.
            raw_work_order = row_dict.get('Work Order')
            work_order_display = ""
            
            if raw_work_order is not None:
                # Try to format as float with 2 decimals
                try:
                    # If it looks like a number, force 2 decimals
                    val_float = float(raw_work_order)
                    work_order_display = "{:.2f}".format(val_float)
                except (ValueError, TypeError):
                    # If it's text like "TBD", leave it alone
                    work_order_display = str(raw_work_order)

            asset = {
                "index": row_dict.get('index'),
                "work_order": work_order_display,  # Use the formatted string
                "asset_tag": row_dict.get('Asset Tag'),
                "asset_group": asset_group_option,  # Use the full option for dropdown matching
                "description": row_dict.get('Description'),
                "property": row_dict.get('Property'),
                "space": row_dict.get('Space'),
                "space_details": row_dict.get('Space Details'),
                "attribute_set": row_dict.get('Attribute Set'),
                "device_address": row_dict.get('Device Address'),
                "device_type": row_dict.get('Device Type'),
                "un_account_number": row_dict.get('UN Account Number'),
                "planon_code": row_dict.get('Planon Code'),
                "creation_date": str(row_dict.get('Creation Date') or ''),
                "status": status_val,
                "workflow": row_dict.get('Workflow'),
                "request_open": row_dict.get('Request Open'),
                "request_date": row_dict.get('Request Date'),
                "elapsed_time": row_dict.get('Elapsed Time'),
                "complete": row_dict.get('Complete'),
                "ticket_number": row_dict.get('Ticket Number')
            }
            control_panel = control_panel_for_property(asset["property"])
            asset["control_panel_code"] = control_panel.get("code", "")
            asset["control_panel_description"] = control_panel.get("description", "")
            asset["control_panel_match_count"] = control_panel.get("match_count", 0)
            asset["control_panel_has_multiple"] = control_panel.get("has_multiple", False)
            existing_assets.append(asset)
        
        print(f"FLS get_fls_asset_data: Successfully loaded {len(existing_assets)} assets from QR_codes.db.")
        import sys
        sys.stdout.flush()

    except Exception as e:
        print(f"CRITICAL ERROR fetching FLS data from QR_codes.db: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to load FLS asset data from QR_codes.db: {e}"}), 500
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")
        print("--- [END] get_fls_asset_data ---")

    # 3. Return all data
    return jsonify({
        "existing_assets": existing_assets,
        "property_list": all_properties, 
        "filter_property_list": filter_properties, 
        "spaces_by_property": spaces_by_prop,
        "device_type_map": device_map,
        "asset_group_options": asset_group_options,
        "asset_group_lookup_map": asset_group_lookup_map,
        "property_asset_tag_map": property_asset_tag_map,
        "property_name_to_code_map": property_name_to_code_map,
        "property_control_panel_map": property_control_panel_map
    })


@main_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not current_user.check_password(old_password):
            flash('Your old password was entered incorrectly. Please try again.', 'danger')
            return redirect(url_for('main.change_password'))

        if new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
            return redirect(url_for('main.change_password'))

        if len(new_password) < 8:
            flash('New password must be at least 8 characters long.', 'danger')
            return redirect(url_for('main.change_password'))

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', new_password):
            flash('New password must contain at least one special character (e.g., !@#$%).', 'danger')
            return redirect(url_for('main.change_password'))

        current_user.set_password(new_password)
        db.session.commit()

        flash('Your password has been updated successfully!', 'success')
        return redirect(url_for('main.index'))
        
    return render_template('change_password.html')

# ------------------ Chart Routes ------------------
@main_bp.get("/chart/approval.png")
@login_required
def approval_chart():
    if not CHARTS_AVAILABLE:
        return Response("Chart module unavailable", status=503, mimetype="text/plain")
    building = request.args.get("building", "All")
    chart_type = request.args.get("chart_type", "all")
    status = request.args.get("status", "all")
    user = (request.args.get("user") or "").strip() or None
    fmt = (request.args.get("fmt") or "png").strip().lower()
    if fmt != "svg":
        fmt = "png"
    try:
        chart_bytes = approval_mod.render_chart_png(
            building=building, chart_type=chart_type, status=status, user=user, fmt=fmt
        )
        mimetype = "image/svg+xml" if fmt == "svg" else "image/png"
        resp = Response(chart_bytes, mimetype=mimetype)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        print(f"Chart error for type '{chart_type}': {e}")
        return Response(f"Chart error: {e}", status=500, mimetype="text/plain")


@main_bp.get("/api/overview/kpis")
@login_required
def overview_kpis():
    """Data for the Overview Key Performance Indicators charts (Chart.js)."""
    if not CHARTS_AVAILABLE:
        return jsonify({"error": "Chart module unavailable"}), 503
    building = request.args.get("building", "All")
    user = (request.args.get("user") or "").strip() or None
    try:
        payload = approval_mod.overview_kpi_payload(building=building, user=user)
        resp = jsonify(payload)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        print(f"Overview KPI data error: {e}")
        return jsonify({"error": "data unavailable"}), 500


@main_bp.get("/api/analytics/buildings")
@login_required
def analytics_buildings():
    user = (request.args.get("user") or "").strip() or None
    return jsonify({"buildings": _get_building_options(user)})

@main_bp.get("/chart/completeness.png")
@login_required
def completeness_chart():
    if not COMPLETENESS_CHART_AVAILABLE:
        return Response("Completeness chart module unavailable", status=503, mimetype="text/plain")
    
    building = request.args.get("building", "All")
    process_scope = request.args.get("process_scope", "all")
    try:
        png_bytes = completeness_mod.render_chart_png(building=building, process_scope=process_scope)
        if not png_bytes:
             return Response("No data for this chart", status=200, mimetype="text/plain")
        
        resp = Response(png_bytes, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        print(f"Completeness chart error for building '{building}': {e}")
        return Response(f"Chart error: {e}", status=500, mimetype="text/plain")

@main_bp.get("/chart/ai_confidence.png")
@login_required
def ai_confidence_chart():
    if not AI_CONFIDENCE_CHART_AVAILABLE:
        return Response("AI confidence chart module unavailable", status=503, mimetype="text/plain")

    building = request.args.get("building", "All")
    process_scope = request.args.get("process_scope", "all")
    try:
        png_bytes = ai_confidence_mod.render_chart_png(building=building, process_scope=process_scope)
        if not png_bytes:
            return Response("No data for this chart", status=200, mimetype="text/plain")

        resp = Response(png_bytes, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        print(f"AI confidence chart error for building '{building}': {e}")
        return Response(f"Chart error: {e}", status=500, mimetype="text/plain")

@main_bp.get("/chart/data_quality.png")
@login_required
def data_quality_chart():
    if not DATA_QUALITY_CHART_AVAILABLE:
        return Response("Data quality chart module unavailable", status=503, mimetype="text/plain")

    building = request.args.get("building", "All")
    process_scope = request.args.get("process_scope", "all")
    user = (request.args.get("user") or "").strip() or None
    try:
        png_bytes = data_quality_mod.render_chart_png(building=building, process_scope=process_scope, user=user)
        resp = Response(png_bytes, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        print(f"Data quality chart error for building '{building}': {e}")
        return Response(f"Chart error: {e}", status=500, mimetype="text/plain")

@main_bp.get("/chart/operational_cost.png")
@login_required
def operational_cost_chart():
    if not OPERATIONAL_COST_CHART_AVAILABLE:
        abort(404, "Operational cost chart module not available.")
    try:
        chart_type = request.args.get("type", "combo")
        building = request.args.get("building", "All")
        metric = request.args.get("metric", "duration")  # [NEW] Default to duration
        user = (request.args.get("user") or "").strip() or None
        png_bytes = operational_cost_mod.render_chart_png(chart_type=chart_type, building=building, metric=metric, user=user)
        
        resp = Response(png_bytes, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        print(f"Operational cost chart error for type '{chart_type}': {e}")
        return Response(f"Chart error: {e}", status=500, mimetype="text/plain")

@main_bp.get("/chart/sdi_flow")
@login_required
def sdi_flow_chart():
    """
    Renders the SDI Label Flow Chart (standalone HTML).
    """
    chart_data = []
    if FLOW_CHART_AVAILABLE:
        try:
             # Pass the global DB_PATH
            chart_data = flow_quantity_chart.build_asset_workflow(db_path=DB_PATH)
        except Exception as e:
            print(f"Error generating SDI flow chart data: {e}")
            
    return render_template("sdi_label.html", chart_data=chart_data)

# --- [FIXED FLS CHART ROUTE FOR ORIGINAL SCRIPT] ---
@main_bp.get("/chart/fls_charts.html")
@login_required
def fls_charts():
    if not FLS_CHARTS_AVAILABLE:
        error_msg = f"FLS charts module unavailable. Details: {CHARTS_IMPORT_ERROR}"
        return Response(error_msg, 503, mimetype="text/plain")

    # The original script fetches its own data via its fls_df() function.
    # It takes no arguments.
    try:
        df = fls_charts_mod.fls_df()
        if df.empty:
            ts = int(time.time())
            return render_template("fls_charts_container.html", ts=ts, has_data=False)

        fls_charts_mod.generate_charts()
    except Exception as e:
        print(f"Error generating FLS charts: {e}")
        traceback.print_exc()
        return Response(f"Error generating charts: {e}", 500, mimetype="text/plain")

    # Render the container. has_data=True triggers the iframes.
    ts = int(time.time())
    return render_template("fls_charts_container.html", ts=ts, has_data=True)

# ------------------ FLS Asset CRUD Routes ------------------
@main_bp.post("/api/fls-assets/add")
@main_bp.post("/fls/add_assets")
@login_required
@require_permission("operations", "fls_devices", "editor")
def add_fls_assets():
    """
    Adds a new asset or updates an existing one in the QR_codes.db
    using an INSERT ... ON CONFLICT (upsert) command.
    This now saves ALL fields from the modal.
    """
    data = request.get_json()
    if not data or not isinstance(data, list):
        return jsonify({"success": False, "message": "Invalid data format."}), 400

    conn = None
    try:
        asset_data = data[0]
        attribute_set = (asset_data.get('attribute_set') or '').strip() or FLS_DEFAULT_ATTRIBUTE_CODE
        asset_data['attribute_set'] = attribute_set
        
        # --- Prepare all data from modal ---
        asset_index = asset_data.get('index')
        
        # Generate creation date string, used *only* if inserting
        creation_date = datetime.utcnow().strftime('%m/%d/%Y')

        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        cursor = conn.cursor()

        # --- Use INSERT ... ON CONFLICT (Upsert) ---
        query = """
            INSERT INTO new_device (
                "index", "Asset Tag", "Asset Group", "Description", "Property", "Space", "Space Details",
                "Attribute Set", "Device Address", "Device Type", "UN Account Number",
                "Status", "Work Order", "Creation Date", "Planon Code", "Workflow", "Ticket Number"
            ) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT("index") DO UPDATE SET
                "Asset Tag" = excluded."Asset Tag",
                "Asset Group" = excluded."Asset Group",
                "Description" = excluded."Description",
                "Property" = excluded."Property",
                "Space" = excluded."Space",
                "Space Details" = excluded."Space Details",
                "Attribute Set" = excluded."Attribute Set",
                "Device Address" = excluded."Device Address",
                "Device Type" = excluded."Device Type",
                "UN Account Number" = excluded."UN Account Number",
                "Status" = excluded."Status",
                "Work Order" = excluded."Work Order",
                "Planon Code" = excluded."Planon Code",
                "Workflow" = excluded."Workflow",
                "Ticket Number" = excluded."Ticket Number"
        """
        
        # Ensure Work Order is treated as a string, but the DB might still store as number
        work_order_value = asset_data.get('work_order')
        if work_order_value is not None:
            work_order_value = str(work_order_value)
        else:
            work_order_value = ''
        
        params = (
            asset_index,
            asset_data.get('asset_tag'),
            asset_data.get('asset_group'),
            asset_data.get('description'),
            asset_data.get('property'),
            asset_data.get('space'),
            asset_data.get('space_details'), 
            attribute_set,
            asset_data.get('device_address'),
            asset_data.get('device_type'),
            asset_data.get('un_account_number'),
            asset_data.get('status'),
            work_order_value, 
            creation_date,
            asset_data.get('planon_code'),
            asset_data.get('workflow'),
            asset_data.get('ticket_number')
        )
        
        cursor.execute(query, params)
        conn.commit()

        if cursor.rowcount > 0:
            # Fetch the *actual* creation date from the DB
            cursor.execute('SELECT "Creation Date" FROM new_device WHERE "index" = ?', (asset_index,))
            result = cursor.fetchone()
            if result:
                asset_data['creation_date'] = result[0]
            else:
                asset_data['creation_date'] = creation_date
            
            message = "Asset successfully saved."
        else:
            message = "No changes detected."

        return jsonify({"success": True, "message": message, "assets": [asset_data]})

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"ERROR in add_fls_assets: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Database error: {e}"}), 500
    finally:
        if conn:
            conn.close()


@main_bp.post("/api/fls-assets/delete")
@main_bp.post("/fls/delete_assets")
@login_required
@require_permission("operations", "fls_devices", "editor")
def delete_fls_assets():
    data = request.get_json()
    indices = data.get('indices', [])
    if not indices:
        return jsonify({"success": False, "message": "No asset indices provided."}), 400
    
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        cursor = conn.cursor()
        
        # Create placeholders for the IN clause
        placeholders = ', '.join('?' for _ in indices)
        # --- FIX: Use "index" instead of QR_code_ID ---
        query = f'DELETE FROM new_device WHERE "index" IN ({placeholders})'
        
        cursor.execute(query, indices)
        conn.commit()
        
        print(f"FLS delete_fls_assets: Deleted {len(indices)} assets.")
        return jsonify({"success": True, "message": f"Successfully deleted {len(indices)} asset(s)."})
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"ERROR in delete_fls_assets: {e}")
        return jsonify({"success": False, "message": f"Database error: {e}"}), 500
    finally:
        if conn:
            conn.close()

@main_bp.post("/api/fls-assets/bulk-update")
@main_bp.post("/fls/bulk_update_assets")
@login_required
@require_permission("operations", "fls_devices", "editor")
def bulk_update_assets():
    """
    Updates multiple assets at once based on user selection.
    Only updates columns that are explicitly allowed.
    """
    data = request.get_json()
    indices = data.get('indices', [])
    updates = data.get('updates', {})

    if not indices:
        return jsonify({"success": False, "message": "No asset indices provided."}), 400
    if not updates:
        return jsonify({"success": False, "message": "No updates specified."}), 400

    # Whitelist of columns allowed for bulk update.
    # This matches the fields available in the bulk edit modal.
    ALLOWED_BULK_UPDATE_COLS = {"Property", "Space", "Status", "Workflow"}
    
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        conn.row_factory = sqlite3.Row  # To fetch updated rows by column name
        cursor = conn.cursor()

        set_clauses = []
        params = []
        
        for column_name, value in updates.items():
            if column_name in ALLOWED_BULK_UPDATE_COLS:
                # Use quoted column names
                set_clauses.append(f'"{column_name}" = ?')
                params.append(value)
            else:
                return jsonify({"success": False, "message": f"Bulk update for column '{column_name}' is not allowed."}), 400

        if not set_clauses:
            return jsonify({"success": False, "message": "No valid update fields provided."}), 400

        set_query_part = ", ".join(set_clauses)
        where_placeholders = ', '.join('?' for _ in indices)
        
        # --- FIX: Use "index" instead of QR_code_ID ---
        query = f'UPDATE new_device SET {set_query_part} WHERE "index" IN ({where_placeholders})'
        
        params.extend(indices)
        
        cursor.execute(query, params)
        conn.commit()
        
        print(f"FLS bulk_update_assets: Updated {len(indices)} assets with fields: {list(updates.keys())}.")

        # --- Fetch the updated rows to send back to the client ---
        select_placeholders = ', '.join('?' for _ in indices)
        
        select_query = f'SELECT * FROM new_device WHERE "index" IN ({select_placeholders})'
        
        cursor.execute(select_query, indices)
        updated_rows = cursor.fetchall()

        assets_list = []
        for row in updated_rows:
            row_dict = dict(row)
            status_val = row_dict.get('Status')
            if str(status_val) not in ('0', '1'):
                status_val = '0' # Default to 'Ongoing' if invalid
            else:
                status_val = str(status_val)
            
            # --- [FIX] FORCE .00 FORMAT FOR WORK ORDER IN BULK UPDATE ---
            raw_work_order = row_dict.get('Work Order')
            work_order_display = ""
            if raw_work_order is not None:
                try:
                    val_float = float(raw_work_order)
                    work_order_display = "{:.2f}".format(val_float)
                except (ValueError, TypeError):
                    work_order_display = str(raw_work_order)

            # --- FIX: Read all columns from the DB row ---
            asset = {
                "index": row_dict.get('index'),
                "work_order": work_order_display,
                "asset_tag": row_dict.get('Asset Tag'),
                "asset_group": row_dict.get('Asset Group'),
                "description": row_dict.get('Description'),
                "property": row_dict.get('Property'),
                "space": row_dict.get('Space'),
                "space_details": row_dict.get('Space Details'), 
                "attribute_set": row_dict.get('Attribute Set'),
                "device_address": row_dict.get('Device Address'),
                "device_type": row_dict.get('Device Type'),
                "un_account_number": row_dict.get('UN Account Number'),
                "planon_code": row_dict.get('Planon Code'),
                "creation_date": row_dict.get('Creation Date'),
                "status": status_val,
                "workflow": row_dict.get('Workflow')
            }
            assets_list.append(asset)

        return jsonify({"success": True, "message": "Assets updated successfully.", "assets": assets_list})

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"ERROR in bulk_update_assets: {e}")
        return jsonify({"success": False, "message": f"Database error: {e}"}), 500
    finally:
        if conn:
            conn.close()


@main_bp.post("/api/fls-assets/update-field")
@main_bp.post("/fls/update_field")
@login_required
@require_permission("operations", "fls_devices", "editor")
def update_fls_asset_field():
    """
    Updates a single field for a specific asset.
    Used for inline edits like checkboxes in the FLS tab.
    """
    data = request.get_json()
    asset_index = data.get('index')
    field = data.get('field')
    value = data.get('value')
    
    if not asset_index or not field:
        return jsonify({"success": False, "message": "Missing index or field name."}), 400
        
    # Whitelist of allowed fields for security
    ALLOWED_FIELDS = {
        "Request Open", "Request Date", "Elapsed Time", "Complete", "Ticket Number"
    }
    
    if field not in ALLOWED_FIELDS:
        return jsonify({"success": False, "message": f"Field '{field}' is not allowed for inline update."}), 400

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=DB_PATH)
        cursor = conn.cursor()
        
        # Update the specific field
        # Note: We use string formatting for the column name because it's validated against the whitelist
        query = f'UPDATE new_device SET "{field}" = ? WHERE "index" = ?'
        
        cursor.execute(query, (value, asset_index))
        conn.commit()
        
        if cursor.rowcount > 0:
            return jsonify({"success": True, "message": "Field updated successfully."})
        else:
            return jsonify({"success": False, "message": "Asset not found or no change made."}), 404
            
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"ERROR in update_fls_asset_field: {e}")
        return jsonify({"success": False, "message": f"Database error: {e}"}), 500
    finally:
        if conn:
            conn.close()

# ------------------ Task Routes ------------------
@main_bp.post("/run/<task_key>")
@login_required
def run_task(task_key: str):
    try:
        task = _validate_task_key(task_key)
        log_path = _launch_cmd_detached(task["cmd"], task.get("cwd"))
        return jsonify({"success": True, "log_name": log_path.name})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.get("/log_status/<name>")
@login_required
def log_status(name: str):
    try:
        path = _safe_log_path(name)
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        lines = [line for line in text.splitlines() if line.strip()]
        last_line = lines[-1] if lines else ""

        if "Traceback (most recent call last):" in text or "Error:" in text or "ModuleNotFoundError" in text:
            return jsonify({"status": "error"})
        
        success_keywords = ["Saved", "Total assets found", "Finished", "Completed", "Done", "SUMMARY", "Successfully updated database", "Success! Updated"]
        if any(keyword.lower() in text.lower() for keyword in success_keywords):
             if any(keyword.lower() in last_line.lower() for keyword in success_keywords):
                return jsonify({"status": "success"})

        return jsonify({"status": "running"})
    except Exception:
        return jsonify({"status": "error"}), 404

# ------------------ Log UI Routes ------------------
@main_bp.get("/logs")
@login_required
def list_logs():
    from_view = request.args.get("from", None)
    rows = []
    try:
        log_files = _system_log_paths()[:200]
        for p in log_files:
            try:
                ts_raw = _extract_ts_from_logname(p.name)
                rows.append({
                    "name": p.name,
                    "when": _when_from_ts(ts_raw, path=p),
                    "title": _title_from_logname(p.name),
                    "size_kb": f"{max(p.stat().st_size // 1024, 1)} KB",
                    "audit": _audit_status_view(p),
                })
            except Exception as e:
                print(f"WARNING: Skipping log file '{p.name}' due to error: {e}")

    except Exception as e:
        print(f"CRITICAL: Could not read logs directory: {e}")
        flash("Error: Could not read the log directory.", "danger")

    return render_template("logs.html", rows=rows, from_view=from_view)

@main_bp.get("/logs/read")
@login_required
def read_log():
    name = request.args.get("name", "")
    path = _safe_log_path(name)
    is_ai_check_log = (path == AI_CHECK_LOG_PATH) or (path.name == AI_CHECK_LOG_PATH.name)
    mode = request.args.get("mode") or ("runs" if is_ai_check_log else "summary")
    if mode == "runs" and not is_ai_check_log:
        mode = "summary"
    
    # Raw mode guards:
    # - hard cap: refuse very large files
    # - reorder cap: only apply expensive timestamp reordering on smaller logs
    # - tail bytes: for larger logs, show recent tail quickly to avoid 502 timeouts
    RAW_VIEW_LIMIT_BYTES = int(os.getenv("RAW_VIEW_LIMIT_BYTES", str(5 * 1024 * 1024)))
    RAW_REORDER_MAX_BYTES = int(os.getenv("RAW_REORDER_MAX_BYTES", str(512 * 1024)))
    RAW_TAIL_BYTES = int(os.getenv("RAW_TAIL_BYTES", str(1024 * 1024)))
    AI_CHECK_RAW_WINDOW_HOURS = int(os.getenv("AI_CHECK_RAW_WINDOW_HOURS", "72"))
    content = ""
    structured_report = None
    is_summary = (mode == "summary")

    try:
        if mode == "runs":
            try:
                page = max(1, int(request.args.get("page", "1")))
            except (TypeError, ValueError):
                page = 1
            structured_report = parse_ai_check_log(
                path,
                hours=AI_CHECK_RAW_WINDOW_HOURS,
                page=page,
                status_filter=request.args.get("status", ""),
                query=request.args.get("q", ""),
            )
        elif is_summary:
            # Efficiently summarize from the file path
            content = _summarize_log(path=path)
        else:  # Raw mode
            if is_ai_check_log:
                content = _read_recent_log_window_text(path, AI_CHECK_RAW_WINDOW_HOURS)
            else:
                file_size = path.stat().st_size
                if file_size > RAW_VIEW_LIMIT_BYTES:
                    size_mb = file_size / 1024 / 1024
                    content = (f"Error: Raw log file is too large to display ({size_mb:.2f} MB).\n\n"
                               f"Please use the 'Download' button to view the full log.")
                else:
                    if file_size <= RAW_REORDER_MAX_BYTES:
                        content = path.read_text(encoding="utf-8", errors="replace")
                        content = _order_log_text_desc(content)
                    else:
                        # Fast path for active/large logs: tail only, then reorder this tail chunk newest-first.
                        tail_text, total_size, read_size = _read_tail_text(path, RAW_TAIL_BYTES)
                        tail_text = _order_log_text_desc(tail_text)
                        content = (
                            f"[Raw Tail Mode] Showing last {read_size / 1024:.1f} KB of "
                            f"{total_size / 1024:.1f} KB. Display order: newest first (tail-only).\n\n"
                            f"{tail_text}"
                        )

    except Exception as e:
        print(f"CRITICAL ERROR reading log {name}: {e}")
        flash(f"Could not read log file: {e}", "danger")
        content = f"Error: A critical error occurred while trying to read the log file."

    return render_template(
        "log_read.html", name=name, title=_title_from_logname(name),
        when=_when_from_ts(_extract_ts_from_logname(name), path=path),
        is_summary=is_summary, content=content, view_mode=mode,
        structured_report=structured_report, is_ai_check_log=is_ai_check_log,
        ai_check_raw_window_hours=AI_CHECK_RAW_WINDOW_HOURS if is_ai_check_log else None
    )
    
@main_bp.get("/logs/download")
@login_required
def download_log():
    name = request.args.get("name", "")
    path = _safe_log_path(name)
    return send_from_directory(path.parent, path.name, as_attachment=True, mimetype="text/plain")


# ------------------ SLD Extraction Logs (AI Process Queue) ------------------
@main_bp.get("/sld-logs/runs")
@login_required
def sld_runs_list():
    """JSON list of recent SLD extraction runs for AJAX refresh."""
    try:
        limit = max(1, min(200, int(request.args.get("limit", "20"))))
    except ValueError:
        limit = 20
    return jsonify({"runs": _sld_runs_index(limit=limit)})


@main_bp.get("/sld-logs/runs/<run_id>")
@login_required
def sld_run_detail(run_id):
    """Drilldown page: run summary + extracted asset rows + model calls."""
    detail = _sld_run_detail(run_id)
    if not detail:
        flash(f"SLD run not found: {run_id}", "warning")
        return redirect(url_for("main.index") + "#qr-pending-view")
    return render_template(
        "sld_log_detail.html",
        run=detail,
        is_dashboard_admin=is_dashboard_admin(),
        sld_review_base_url=SLD_REVIEW_BASE_URL,
        username=current_user.username,
    )


@main_bp.post("/sld-logs/runs/<run_id>/rerun")
@login_required
@dashboard_admin_required
def sld_run_rerun(run_id):
    """Server-side reverse proxy to EL Reviewer's /sld/api/rerun/<run_id>.

    Forwards the user's session cookies over loopback so the EL service's
    @login_required + admin check both pass. Avoids CORS entirely (browser
    talks only to the Dashboard origin).
    """
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", run_id or "")
    if not safe or safe != run_id:
        return jsonify({"error": "Invalid run_id"}), 400
    import urllib.request
    import urllib.error
    target = f"{SLD_REVIEW_INTERNAL_BASE.rstrip('/')}/sld/api/rerun/{safe}"
    cookie_header = request.headers.get("Cookie", "")
    req = urllib.request.Request(target, data=b"", method="POST")
    if cookie_header:
        req.add_header("Cookie", cookie_header)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=current_app.config.get("SLD_RERUN_TIMEOUT", 1800)) as resp:
            body = resp.read()
            try:
                return Response(body, status=resp.status, mimetype="application/json")
            except Exception:
                return jsonify({"error": "Invalid upstream response"}), 502
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return Response(body or json.dumps({"error": f"Upstream HTTP {e.code}"}).encode(), status=e.code, mimetype="application/json")
    except urllib.error.URLError as e:
        return jsonify({"error": f"Could not reach EL Reviewer at {SLD_REVIEW_INTERNAL_BASE}: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"error": f"Re-run proxy failed: {e}"}), 500

app.register_blueprint(main_bp)

# Life Cycle Assessment feature (in-process blueprint mounted at /life-cycle).
# Wrapped in try/except like the optional chart modules: a missing pipeline
# dependency (pandas/sqlalchemy/openpyxl) degrades to "feature absent" instead
# of crashing portal boot.
try:
    from life_cycle import life_cycle_bp
    app.register_blueprint(life_cycle_bp, url_prefix="/life-cycle")
except Exception as e:  # noqa: BLE001 - keep the portal up if the feature can't load
    app.logger.warning("Life Cycle Assessment blueprint not loaded: %s", e)


# ------------------ Main ------------------
if __name__ == "__main__":
    print("[Asset Portal] Running at http://127.0.0.1:8002")
    for key, t in TASKS.items():
        sp = _cmd_script_path(t["cmd"])
        print(f"Task {key}: {t.get('label','')} -> {sp}")
    app.run(host="127.0.0.1", port=8002, debug=False)
