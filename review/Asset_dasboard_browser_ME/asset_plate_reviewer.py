import os
import json
import re
import sqlite3
import sys
import ast
from functools import lru_cache
from pathlib import Path
from threading import Lock
from datetime import datetime
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, send_file, jsonify, Blueprint, flash, g

import excel_export
import db as qrdb  # backend-agnostic QR_codes DB layer (Phase C / C4)
from review_buttons import REVIEW_BUTTONS  # canonical review-page action buttons (three-copy rule)

# Per-app Flask endpoint names for the canonical review buttons
# (the registry itself is app-agnostic; see review_buttons.py).
REVIEW_ENDPOINTS = {"print": "review_print", "export": "review_export", "dashboard": "index"}

# Make the shared `audit/` package importable (sibling of the app folders)
_PROJECT_ROOT_GUESS = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT_GUESS not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_GUESS)
try:
    from audit.logger import log_change as _audit_log_change
    from audit.diff import diff_dicts as _audit_diff_dicts
except Exception as _audit_exc:  # never block the app on audit-import failure
    print(f"[audit] import failed in reviewer_me: {_audit_exc}")
    _audit_log_change = None
    _audit_diff_dicts = None

from review_installation_date import (
    InstallationDateError, get_installation_date,
    parse_installation_date, update_installation_date,
)

# --- START: SSO & AUTHENTICATION IMPORTS ---
from flask_login import login_required, current_user, login_user, logout_user
from dotenv import load_dotenv

# Add shared auth directory to path
sys.path.append('/home/developer/auth_service')
try:
    from auth_model import db, bcrypt, User, ensure_user_access_table, has_permission, is_admin, require_permission, access_denied_response
    from auth_controller import login_manager
except ImportError:
    print("Warning: Auth service components not found.")
# --- END: SSO & AUTHENTICATION IMPORTS ---

# --- NEW: IMPORT GPS SERVICE ---
try:
    from gps_service import gps_bp
except ImportError:
    gps_bp = None

# --- START: SMART DICTIONARY LOADER ---
DICTIONARY_FILE_PATH = r"/home/developer/dictionary/mechanical_dictionary.py"

# Global cache variables
_CACHED_DICTIONARY = {}
_LAST_LOAD_TIME = 0

def get_asset_dictionary():
    global _CACHED_DICTIONARY, _LAST_LOAD_TIME
    
    if not os.path.exists(DICTIONARY_FILE_PATH):
        return {}

    try:
        current_mtime = os.path.getmtime(DICTIONARY_FILE_PATH)
        if current_mtime > _LAST_LOAD_TIME:
            print(f"DETECTED: Dictionary change. Reloading from {DICTIONARY_FILE_PATH}...")
            with open(DICTIONARY_FILE_PATH, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            tree = ast.parse(file_content)
            found = False
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'ASSET_DICTIONARY':
                            _CACHED_DICTIONARY = ast.literal_eval(node.value)
                            _LAST_LOAD_TIME = current_mtime
                            found = True
                            print("SUCCESS: Mechanical Dictionary reloaded.")
                            break
                if found: break
    except Exception as e:
        print(f"ERROR: Could not reload dictionary file: {e}")
        return _CACHED_DICTIONARY

    return _CACHED_DICTIONARY
# --- END: SMART DICTIONARY LOADER ---


# --- PATHS ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "review_asset_templates"
CANDIDATE_STATIC = [TEMPLATE_DIR / "static", BASE_DIR / "static"]
STATIC_DIR = next((p for p in CANDIDATE_STATIC if p.exists()), None)
EL_HELPER_DIR = (BASE_DIR.parent / "Asset_dashboard_browser_EL").resolve()
if str(EL_HELPER_DIR) not in sys.path:
    sys.path.append(str(EL_HELPER_DIR))

from json_persistence import has_meaningful_structured_data, mark_json_processed, normalize_dashboard_query

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR) if STATIC_DIR else None,
)

# --- CONFIG ---
load_dotenv('/home/developer/auth_service.env', override=True)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or 'dev-key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI') or 'sqlite:///'
app.config['SESSION_COOKIE_DOMAIN']    = os.getenv('SESSION_COOKIE_DOMAIN')
app.config['SESSION_COOKIE_SAMESITE']  = 'None'
app.config['SESSION_COOKIE_SECURE']    = True
app.config['SESSION_COOKIE_HTTPONLY']  = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'None'
app.config['REMEMBER_COOKIE_SECURE']   = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True

# Staging / local QA over plain HTTP (env-gated; NO-OP in prod).
if os.getenv('STAGING_INSECURE_COOKIES') == '1':
    app.config['SESSION_COOKIE_DOMAIN']  = None
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE']   = False
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_SECURE']   = False

if 'db' in globals():
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)


# --- DATA SETTINGS ---
JSON_DIR = r"/home/developer/Output_jason_api"
IMG_DIR  = r"/home/developer/Capture_photos_upload"
DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"

# Configure DB Path for GPS Service
app.config['DATABASE_FILE_PATH'] = DB_PATH
if gps_bp:
    app.register_blueprint(gps_bp)


# Database Constants
QR_CODES_TABLE   = "QR_codes"
QR_CODE_ID_COL   = "QR_code_ID"
QR_APPROVED_COL  = "Approved"
QR_LOCATION_COL = "Location"

QR_CODE_ASSETS_TABLE = "QR_code_assets"
QR_CODE_ASSETS_PROCESS_COL = "Col_process"
QR_CODE_ASSETS_QR_COL_CANDIDATES = [
    "code_assets", "code_asset", "QR_code", "QR Code", "QR_code_ID", "QR Code ID"
]

SDI_TABLE = "sdi_dataset"
SDI_PRINT_OUT_TABLE = "sdi_print_out"
SDI_ARCHIVE_TABLE = "sdi_print_out_arch"
ASSET_GROUP_TABLE = "Asset_Group"
ASSET_GROUP_COL   = "Name"
ATTRIBUTE_TABLE   = "Attribute"
ATTRIBUTE_COL     = "Code"

VALID_IMAGE_EXTS = ['.jpg', '.JPG', '.jpeg', '.JPEG', '.png', '.PNG']
SEQ_CHECK = ['-0', '-1', '-2']
SEQ_SHOW  = ['-0', '-1', '-2', '-3', '-4']

# Regex to parse filenames
JSON_NAME_RE = re.compile(r"^([A-Za-z0-9]+)_([A-Za-z]+)_(.+)\.json$")


# --- AUTH BLUEPRINT ---
auth_bp = Blueprint('auth', __name__, template_folder='review_asset_templates')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user, remember=request.form.get('remember'))
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Nome de utilizador ou palavra-passe inválidos.', 'danger')
            return render_template('login.html')
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

app.register_blueprint(auth_bp)


# --- SYNC LOGIC ---
DATA_DIR = Path(DB_PATH).parent
PROCESSED_LOG = DATA_DIR / "processed_images.log"
IMG_NAME_RE = re.compile(r"^([A-Za-z0-9]+)\s+(.+?)\s+ME\s+-\s+[0-4]\.(?:jpe?g|png)$", re.IGNORECASE)
image_sync_lock = Lock()
PROCESSED_JSON_LOG = DATA_DIR / "processed_json.log"
json_sync_lock = Lock()

def _connectable():
    return qrdb.is_postgres() or os.path.exists(DB_PATH)

def _quote(name: str) -> str:
    return f'"{name}"'.replace('""', '"')

def _empty_qr_package_lock(qr: object = "") -> dict:
    return {
        "qr_code": str(qr or "").strip(),
        "locked": False,
        "source": "",
        "source_label": "",
        "package_id": "",
        "package_date": "",
        "package_time": "",
    }

def _package_lock_message(lock: dict | None = None) -> str:
    lock = lock or {}
    source_label = str(lock.get("source_label") or "SDI package").strip()
    package_id = str(lock.get("package_id") or "").strip()
    if package_id:
        return f"This asset is already in {source_label} {package_id} and cannot be changed."
    return "This asset is already in an SDI package and cannot be changed."

def _get_qr_package_lock_map(qr_codes: list[str], *, raise_on_error: bool = False) -> dict[str, dict]:
    cleaned_qrs = []
    seen = set()
    for qr in qr_codes or []:
        qr_text = str(qr or "").strip()
        if not qr_text or qr_text in seen:
            continue
        seen.add(qr_text)
        cleaned_qrs.append(qr_text)

    locks = {qr: _empty_qr_package_lock(qr) for qr in cleaned_qrs}
    if not cleaned_qrs or not _connectable():
        return locks

    package_tables = (
        (SDI_PRINT_OUT_TABLE, "active", "active SDI package"),
        (SDI_ARCHIVE_TABLE, "archive", "archived SDI package"),
    )
    chunk_size = 500

    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for table_name, source, source_label in package_tables:
                if not qrdb.has_table(conn, table_name):
                    continue

                columns = set(qrdb.table_columns(conn, table_name))
                if "QR Code" not in columns:
                    continue

                package_id_expr = '"id_print_out"' if "id_print_out" in columns else "''"
                package_date_expr = '"date"' if "date" in columns else "''"
                package_time_expr = '"time"' if "time" in columns else "''"

                for idx in range(0, len(cleaned_qrs), chunk_size):
                    chunk = cleaned_qrs[idx:idx + chunk_size]
                    placeholders = ",".join("?" for _ in chunk)
                    query = f"""
                        SELECT
                            TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) AS qr_code,
                            {package_id_expr} AS package_id,
                            {package_date_expr} AS package_date,
                            {package_time_expr} AS package_time
                        FROM {_quote(table_name)}
                        WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) IN ({placeholders})
                    """
                    cur.execute(query, chunk)
                    for row in cur.fetchall():
                        qr_value = str(row["qr_code"] or "").strip()
                        if not qr_value or qr_value not in locks or locks[qr_value].get("locked"):
                            continue
                        locks[qr_value] = {
                            "qr_code": qr_value,
                            "locked": True,
                            "source": source,
                            "source_label": source_label,
                            "package_id": str(row["package_id"] or "").strip(),
                            "package_date": str(row["package_date"] or "").strip(),
                            "package_time": str(row["package_time"] or "").strip(),
                        }
    except Exception as e:
        if raise_on_error:
            raise
        print(f"[WARN] Failed to fetch SDI package locks: {e}")

    return locks

def _get_qr_package_lock(qr_code: object, *, raise_on_error: bool = False) -> dict:
    qr_text = str(qr_code or "").strip()
    if not qr_text:
        return _empty_qr_package_lock(qr_text)
    return _get_qr_package_lock_map([qr_text], raise_on_error=raise_on_error).get(qr_text, _empty_qr_package_lock(qr_text))

def _attach_package_locks_to_items(items: list[dict]) -> None:
    if not items:
        return
    locks = _get_qr_package_lock_map([item.get("qr_code") for item in items])
    for item in items:
        qr_value = str(item.get("qr_code") or "").strip()
        lock = locks.get(qr_value, _empty_qr_package_lock(qr_value))
        item["package_lock"] = lock
        item["package_locked"] = bool(lock.get("locked"))
        item["package_lock_source"] = lock.get("source", "")
        item["package_lock_source_label"] = lock.get("source_label", "")
        item["package_id"] = lock.get("package_id", "")
        item["package_lock_message"] = _package_lock_message(lock) if lock.get("locked") else ""

def _coerce_packaged_approval(qr_code: object, structured: dict) -> bool:
    """Keep SDI-packaged assets approved even when stale JSON says otherwise."""
    if not isinstance(structured, dict):
        return False
    qr_text = str(qr_code or "").strip()
    if not qr_text:
        return False
    try:
        packaged = bool(_get_qr_package_lock(qr_text).get("locked"))
    except Exception as exc:
        print(f"[WARN] Could not verify package approval guard for {qr_text}: {exc}")
        packaged = False
    if not packaged or str(structured.get("Approved", "") or "").strip() == "True":
        return False
    structured["Approved"] = "True"
    return True

def _package_lock_response(lock: dict):
    return jsonify({
        "success": False,
        "error": _package_lock_message(lock),
        "package_locked": True,
        "source": lock.get("source", ""),
        "package_id": lock.get("package_id", ""),
    }), 409

def _package_lock_check_failed_response(exc: Exception):
    return jsonify({
        "success": False,
        "error": f"Could not verify SDI package status: {exc}",
    }), 500

def _db_get_columns(conn, table: str):
    # Backend-agnostic (was PRAGMA table_info; PG uses information_schema via db.py)
    return set(qrdb.table_columns(conn, table))

def _qr_prefix_expr() -> str:
    # SQL expression returning the first space-separated token of code_assets (= QR prefix).
    # SQLite has INSTR; PostgreSQL doesn't but provides split_part.
    return ("split_part(code_assets, ' ', 1)" if qrdb.is_postgres()
            else "SUBSTR(code_assets, 1, INSTR(code_assets || ' ', ' ') - 1)")

def _db_ensure_columns(conn, table: str, column_defs: dict[str, str]):
    existing_cols = _db_get_columns(conn, table)
    if not existing_cols:
        return

    cur = conn.cursor()
    for col_name, col_type in column_defs.items():
        if col_name not in existing_cols:
            cur.execute(f'ALTER TABLE {_quote(table)} ADD COLUMN {_quote(col_name)} {col_type}')

def _db_upsert_row(conn, table: str, key_cols: list[str], row: dict):
    existing_cols = _db_get_columns(conn, table)
    if not existing_cols: return
    
    filtered = {k: ("" if row.get(k) is None else row.get(k)) for k in row.keys() if k in existing_cols}
    
    # Ensure keys exist
    for key in key_cols:
        if key not in filtered: filtered[key] = ""

    set_cols = [c for c in filtered.keys() if c not in key_cols]
    cur = conn.cursor()
    
    if set_cols:
        set_clause = ", ".join(f'{_quote(c)} = ?' for c in set_cols)
        where_clause = " AND ".join(f'{_quote(k)} = ?' for k in key_cols)
        sql_upd = f'UPDATE {_quote(table)} SET {set_clause} WHERE {where_clause}'
        params_upd = [filtered[c] for c in set_cols] + [filtered[k] for k in key_cols]
        cur.execute(sql_upd, params_upd)
        if cur.rowcount == 0:
            _db_insert_row_helper(cur, table, filtered)
    else:
        _db_insert_row_helper(cur, table, filtered)

def _db_insert_row_helper(cur, table, filtered):
    cols = list(filtered.keys())
    placeholders = ", ".join("?" for _ in cols)
    sql_ins = f'INSERT INTO {_quote(table)} ({", ".join(_quote(c) for c in cols)}) VALUES ({placeholders})'
    params_ins = [filtered[c] for c in cols]
    try:
        cur.execute(sql_ins, params_ins)
    except qrdb.IntegrityError:
        # Cross-backend (SQLite IntegrityError + psycopg2 IntegrityError). The PG schema
        # now enforces a PK on sdi_dataset("QR Code"); a cross-building INSERT for an
        # already-curated QR raises UniqueViolation here. Preserve the existing
        # silent-fallthrough semantics so the review UI doesn't 500 mid-edit.
        pass

def _db_upsert_sdi_dataset(
    qr: str,
    building: str,
    structured: dict,
    avg_ai_conf=None,
    *,
    audit_source: str = "human",
    audit_app: str = "reviewer_me",
    audit_description: str = "",
    audit_source_map: dict | None = None,
    audit_modified_by: str | None = None,
):
    if not _connectable(): return

    _coerce_packaged_approval(qr, structured)
    approved_flag = "1" if (structured.get("Approved", "") == "True") else "0"
    flagged_val = "1" if (structured.get("Flagged", "false") == "true") else "0"
    avg_ai_conf_val, _ = _normalize_avg_ai_conf(avg_ai_conf)

    # Handle dual tag keys
    tag_val = structured.get("UBC Asset Tag") or structured.get("UBC Tag", "")

    desc = _resolve_description(
        structured.get("Asset Group", ""),
        tag_val,
        structured.get("Description", ""),
        structured.get("asset_type")
    )

    row = {
        "QR Code": qr or "",
        "Building": building or "",
        "Manufacturer": str(structured.get("Manufacturer", "") or ""),
        "Model": str(structured.get("Model", "") or ""),
        "Serial": str(structured.get("Serial Number", "") or ""),
        "UBC Tag": str(tag_val or ""),
        "Asset Group": str(structured.get("Asset Group", "") or ""),
        "Attribute": str(structured.get("Attribute", "") or ""),
        "Description": str(desc or ""),
        "Diameter": str(structured.get("Diameter", "") or ""),
        "Year": str(structured.get("Year", "") or ""),
        "Technical Safety BC": str(structured.get("Technical Safety BC", "") or ""),
        "Approved": approved_flag,
        "Flagged": flagged_val,
        "Avg_ai_conf": avg_ai_conf_val,
        "Main Asset": str(structured.get("Main Asset", "") or ""),
    }

    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            _db_ensure_columns(conn, SDI_TABLE, {"Avg_ai_conf": "REAL", "Main Asset": "TEXT"})

            # Snapshot before for audit
            before_row: dict = {}
            if _audit_log_change and _audit_diff_dicts:
                try:
                    cur = conn.execute(
                        f'SELECT * FROM {_quote(SDI_TABLE)} WHERE "QR Code"=? AND "Building"=?',
                        (qr or "", building or ""),
                    )
                    r = cur.fetchone()
                    if r:
                        cols = [d[0] for d in cur.description]
                        before_row = dict(zip(cols, r))
                except Exception as snap_exc:
                    print(f"[audit] ME snapshot-before failed: {snap_exc}")

            _db_upsert_row(conn, SDI_TABLE, key_cols=["QR Code", "Building"], row=row)

            # Audit: per-field diff against the freshly written state
            if _audit_log_change and _audit_diff_dicts:
                try:
                    cur = conn.execute(
                        f'SELECT * FROM {_quote(SDI_TABLE)} WHERE "QR Code"=? AND "Building"=?',
                        (qr or "", building or ""),
                    )
                    r = cur.fetchone()
                    after_row: dict = {}
                    if r:
                        cols = [d[0] for d in cur.description]
                        after_row = dict(zip(cols, r))

                    changes = _audit_diff_dicts(before_row, after_row)
                    if changes:
                        op = "INSERT" if not before_row else "UPDATE"
                        # Group fields by source to keep one log_change call per source
                        source_map = audit_source_map or {}
                        per_source: dict[str, dict] = {}
                        for fld, pair in changes.items():
                            src = source_map.get(fld, audit_source)
                            per_source.setdefault(src, {})[fld] = pair
                        for src, fld_changes in per_source.items():
                            _audit_log_change(
                                conn,
                                qr_code=qr,
                                app_name=audit_app,
                                table_name=SDI_TABLE,
                                record_pk=f"{qr}|{building}",
                                op_type=op,
                                field_changes=fld_changes,
                                source=src,
                                modified_by=audit_modified_by,
                                description=audit_description or "_db_upsert_sdi_dataset",
                            )
                except Exception as audit_exc:
                    print(f"[audit] ME upsert audit failed: {audit_exc}")

            conn.commit()
    except Exception as e:
        print(f"DB Upsert Error: {e}")

def _db_table_has_column(table: str, column: str) -> bool:
    if not _connectable():
        return False
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            return column in _db_get_columns(conn, table)
    except Exception:
        return False

def _auto_register_qr_code(qr: str, process_value: str = "0"):
    """Auto-register QR code in QR_code_assets if not already present.
    
    Checks both exact match and prefix match (e.g., '0000184404 198 ME - 0')
    to avoid creating duplicate entries when full-format entries already exist.
    """
    if not _connectable(): return
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            # Check for BOTH exact match AND prefix match (full format entries)
            cur.execute(
                'SELECT code_assets FROM "QR_code_assets" WHERE code_assets = ? OR code_assets LIKE ?',
                (qr, qr + " %")
            )
            if not cur.fetchone():
                cur.execute('INSERT INTO "QR_code_assets" (code_assets, "Col_process") VALUES (?, ?)', (qr, process_value))
                conn.commit()
    except Exception as e:
        print(f"Auto-register warning: {e}")

def _force_reset_qr_to_new(qr: str):
    if not _connectable(): return
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            sql = f'UPDATE "{QR_CODE_ASSETS_TABLE}" SET "{QR_CODE_ASSETS_PROCESS_COL}" = ? WHERE "code_assets" = ?'
            cur.execute(sql, ("0", qr))
            conn.commit()
            print(f"   -> FIXED: Moved {qr} back to New Assets (Process 0)")
    except Exception as e:
        print(f"   -> Failed to fix {qr}: {e}")

def _sdi_row_has_data_me(qr: str, building: str) -> bool:
    """True when a sdi_dataset row for this QR+Building already holds any
    non-empty AI-captured content that must not be wiped by a placeholder
    re-sync. Mirrors the EL guard (widened to every content column the ME
    upsert writes) so editing a tag to a blank value cannot open a window
    where the next placeholder upsert zeroes the whole row."""
    if not _connectable():
        return False
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.execute(
                f'SELECT 1 FROM {_quote(SDI_TABLE)} '
                f'WHERE "QR Code"=? AND "Building"=? AND ('
                f'TRIM(COALESCE("UBC Tag",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Manufacturer",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Model",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Serial",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Asset Group",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Description",\'\')) <> \'\' '
                # CAST keeps this valid on PG, where Avg_ai_conf is TEXT and
                # COALESCE(text, 0) is a type error (SQLite tolerated it).
                f'OR TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), \'\')) NOT IN (\'\', \'0\', \'0.0\')'
                f') LIMIT 1',
                (qr, building),
            )
            return cur.fetchone() is not None
    except qrdb.DatabaseError:
        return False


def sync_image_directory_to_db():
    if not image_sync_lock.acquire(blocking=False): return
    try:
        if not os.path.isdir(IMG_DIR): return
        processed = set()
        if PROCESSED_LOG.exists():
            with open(PROCESSED_LOG, 'r', encoding='utf-8') as f:
                processed = {line.strip() for line in f if line.strip()}

        curr = {f for f in os.listdir(IMG_DIR) if f.lower().endswith(tuple(VALID_IMAGE_EXTS))}
        new_files = sorted(list(curr - processed))

        success = []
        for fn in new_files:
            m = IMG_NAME_RE.match(fn)
            if not m:
                success.append(fn)
                continue
            qr, bld = m.groups()
            qr_s, bld_s = qr.strip(), bld.strip()
            try:
                # Blank-payload upsert overwrites every editable column with "".
                # Skip it when the row already holds real AI-captured content.
                if _sdi_row_has_data_me(qr_s, bld_s):
                    success.append(fn)
                    continue
                _db_upsert_sdi_dataset(
                    qr_s, bld_s, {},
                    audit_source="system",
                    audit_description="image-sync placeholder",
                    audit_modified_by="system:image-sync",
                )
                _auto_register_qr_code(qr_s, "0")
                success.append(fn)
            except: pass

        if success:
            with open(PROCESSED_LOG, 'a', encoding='utf-8') as f:
                for fn in success: f.write(f"{fn}\n")
    finally: image_sync_lock.release()

def sync_json_directory_to_db():
    if not json_sync_lock.acquire(blocking=False): return
    try:
        if not os.path.isdir(JSON_DIR): return
        processed = {}
        if PROCESSED_JSON_LOG.exists():
            with open(PROCESSED_JSON_LOG, 'r', encoding='utf-8') as f:
                try: processed = json.load(f)
                except: pass
        
        force_resync = not _db_table_has_column(SDI_TABLE, "Avg_ai_conf")
        to_proc = {}
        for fn in os.listdir(JSON_DIR):
            if not _is_me_filename(fn): continue
            fp = os.path.join(JSON_DIR, fn)
            mtime = os.path.getmtime(fp)
            if force_resync or fn not in processed or mtime > processed[fn]:
                to_proc[fn] = mtime
        
        processed_updates = {}
        for fn, mtime in to_proc.items():
            m = JSON_NAME_RE.match(fn)
            if not m: continue
            qr, discipline, bld = m.groups()
            fp = os.path.join(JSON_DIR, fn)
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                structured = content.get("structured_data", {})
                if has_meaningful_structured_data(structured):
                    if _coerce_packaged_approval(qr, structured):
                        content["structured_data"] = structured
                        with open(fp, 'w', encoding='utf-8') as f:
                            json.dump(content, f, ensure_ascii=False, indent=4)
                        mtime = os.path.getmtime(fp)
                    structured = apply_dictionary_rules(structured, asset_type=discipline)
                    _db_upsert_sdi_dataset(
                        qr, bld, structured,
                        avg_ai_conf=_extract_avg_ai_conf(content),
                        audit_source="ai:gpt-5.5",
                        audit_description=f"JSON sync ({fn})",
                        audit_modified_by="ai-pipeline",
                    )
                    _auto_register_qr_code(qr, "0")
                    processed[fn] = mtime
                    processed_updates[fn] = mtime
                else:
                    print(f"JSON Sync Warning {fn}: sparse structured_data skipped; marked processed.")
                    processed[fn] = mtime
                    processed_updates[fn] = mtime
            except Exception as e:
                print(f"JSON Sync Error {fn}: {e}")
        
        if processed_updates:
            mark_json_processed(str(PROCESSED_JSON_LOG), filename_mtimes=processed_updates)
    finally: json_sync_lock.release()

def sync_manual_entry_flags():
    """Keep Manual Entry process and SDI exclusion flags aligned for ME assets."""
    if not _connectable():
        return
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()

            qr_codes_cols = set(qrdb.table_columns(conn, QR_CODES_TABLE))
            if QR_CODE_ID_COL not in qr_codes_cols:
                return
            if "sdi" not in qr_codes_cols:
                cur.execute(f'ALTER TABLE "{QR_CODES_TABLE}" ADD COLUMN "sdi" TEXT DEFAULT \'0\'')   # '' literal, not "" identifier (PG)

            qr_asset_cols = set(qrdb.table_columns(conn, QR_CODE_ASSETS_TABLE))
            if "code_assets" not in qr_asset_cols or QR_CODE_ASSETS_PROCESS_COL not in qr_asset_cols:
                return

            process_manual_qrs = set()
            cur.execute(f'SELECT "code_assets", "{QR_CODE_ASSETS_PROCESS_COL}" FROM "{QR_CODE_ASSETS_TABLE}"')
            for code_assets, process_val in cur.fetchall():
                if code_assets is None:
                    continue
                try:
                    if int(str(process_val).strip()) != 2:
                        continue
                except Exception:
                    continue
                qr = str(code_assets).strip().split(None, 1)[0]
                if qr:
                    process_manual_qrs.add(qr)

            sdi_manual_qrs = set()
            cur.execute(f'SELECT "{QR_CODE_ID_COL}" FROM "{QR_CODES_TABLE}" WHERE "sdi" = ?', ('1',))
            for row in cur.fetchall():
                qr = str(row[0]).strip() if row and row[0] is not None else ""
                if qr:
                    sdi_manual_qrs.add(qr)

            manual_qrs = process_manual_qrs.union(sdi_manual_qrs)
            if not manual_qrs:
                return

            for qr in manual_qrs:
                cur.execute(
                    f'''
                    INSERT INTO "{QR_CODES_TABLE}" ("{QR_CODE_ID_COL}", "sdi")
                    VALUES (?, ?)
                    ON CONFLICT("{QR_CODE_ID_COL}") DO UPDATE SET "sdi"=excluded."sdi"
                    ''',
                    (qr, "1"),
                )
                like_pattern = qr + "%"
                cur.execute(
                    f'UPDATE "{QR_CODE_ASSETS_TABLE}" SET "{QR_CODE_ASSETS_PROCESS_COL}" = ? WHERE "code_assets" LIKE ?',
                    ("2", like_pattern),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        f'''
                        INSERT INTO "{QR_CODE_ASSETS_TABLE}" ("code_assets", "{QR_CODE_ASSETS_PROCESS_COL}")
                        VALUES (?, ?)
                        ON CONFLICT("code_assets") DO UPDATE SET "{QR_CODE_ASSETS_PROCESS_COL}"=excluded."{QR_CODE_ASSETS_PROCESS_COL}"
                        ''',
                        (qr, "2"),
                    )

            conn.commit()
    except Exception as e:
        print(f"Manual Entry sync warning: {e}")

@app.before_request
def before_request_handler():
    g.embedded = request.args.get('embedded', '').lower() == 'true'
    if request.endpoint in ('static', 'serve_image', 'health', 'auth.login', 'auth.logout'): return
    sync_image_directory_to_db()
    sync_json_directory_to_db()
    sync_manual_entry_flags()


# --- HELPERS ---
def _is_me_filename(filename: str) -> bool:
    if not filename.endswith(".json"): return False
    m = JSON_NAME_RE.match(filename)
    if not m: return False
    return m.groups()[1].upper() == "ME"

def _normalize_asset_type_values(value):
    """Normalize '- ME' or '- EL' to 'ME' or 'EL'"""
    if value is None: return set()
    vals = value if isinstance(value, (list, tuple, set)) else [value]
    out = set()
    for v in vals:
        cleaned = re.sub(r'[^A-Za-z0-9]+', '', str(v or "")).upper()
        if cleaned: out.add(cleaned)
    return out

def _get_entry_asset_types(entry: dict):
    if not isinstance(entry, dict): return set()
    normalized = set()
    for key, val in entry.items():
        key_clean = re.sub(r'[^a-z0-9]+', '', str(key).lower())
        if key_clean in ("assettype", "assettypes", "type"):
            normalized |= _normalize_asset_type_values(val)
    return normalized

def _find_dictionary_entry(ubc, current_type, json_types=None):
    """Resolve the ASSET_DICTIONARY entry for a UBC tag.

    Priority order:
    1. Exact composite key match (e.g., "AHU-100" matches "AHU-100|ME")
    2. Composite prefix match (e.g., "AHU-100" matches "AHU|ME")
    3. Legacy simple key match (e.g., "AHU-100" matches "AHU")

    Returns (entry, matched_types) or (None, None).
    """
    cd = get_asset_dictionary()
    if not ubc or not cd:
        return None, None

    # STEP 1: Try exact composite key match (highest priority)
    composite_key = f"{ubc}|{current_type}"
    if composite_key in cd:
        print(f"[DICT-MATCH] Exact composite key: {composite_key}")
        return cd[composite_key], {current_type}

    # STEP 2: Try composite prefix matching (e.g., "T-001" matches "T-|ME")
    for key in sorted(cd.keys(), key=len, reverse=True):
        if '|' not in key:
            continue  # Skip simple keys in this pass

        try:
            tag_prefix, key_type = key.split('|', 1)
        except ValueError:
            continue  # Skip malformed keys
        # Check if UBC tag starts with prefix AND types match
        if ubc.startswith(tag_prefix.upper()) and key_type.upper() == current_type:
            print(f"[DICT-MATCH] Composite prefix: {key} (matched {ubc})")
            return cd[key], {current_type}

    # STEP 3: Fall back to simple key matching (legacy support)
    for prefix in sorted(cd.keys(), key=len, reverse=True):
        if '|' in prefix:
            continue  # Skip composite keys in legacy matching

        if not ubc.startswith(prefix.upper()):
            continue
        m = cd[prefix]
        dict_types = _get_entry_asset_types(m)

        # Discipline match check (for simple keys with asset_type field)
        if json_types and dict_types and dict_types.isdisjoint(json_types):
            continue

        print(f"[DICT-MATCH] Legacy simple key: {prefix} (matched {ubc})")
        return m, dict_types

    print(f"[DICT-NO-MATCH] No dictionary match found for UBC: {ubc}, Type: {current_type}")
    return None, None


def apply_dictionary_rules(data, asset_type=None):
    """Apply dictionary lookup with composite key support (Tag|Type).

    Fields flagged with asset_group_manual / attribute_manual == "1" hold a
    reviewer override and are never overwritten by the dictionary (see
    _update_manual_field_flags).
    """
    preserve_existing = data.get("Approved") == "True"

    def apply_match(entry):
        asset_group = entry.get("asset_group", "")
        attribute_set = entry.get("attribute_set", "")
        main_asset = entry.get("main_asset", "")

        if preserve_existing:
            if not str(data.get("Asset Group", "") or "").strip() and asset_group:
                data["Asset Group"] = asset_group
            if not str(data.get("Attribute", "") or "").strip() and attribute_set:
                data["Attribute"] = attribute_set
            if not str(data.get("Main Asset", "") or "").strip() and main_asset:
                data["Main Asset"] = main_asset
            return

        ag_manual = str(data.get("asset_group_manual") or "").strip() == "1"
        attr_manual = str(data.get("attribute_manual") or "").strip() == "1"
        if not (ag_manual and str(data.get("Asset Group") or "").strip()):
            data["Asset Group"] = asset_group
        if not (attr_manual and str(data.get("Attribute") or "").strip()):
            data["Attribute"] = attribute_set
        data["Main Asset"] = main_asset

    # Priority check for UBC Asset Tag (from JSON) or UBC Tag (from Form)
    ubc = str(data.get("UBC Asset Tag") or data.get("UBC Tag") or "").strip().upper()
    if not ubc:
        return data

    cd = get_asset_dictionary()
    if not cd:
        print(f"[DICT-WARN] Dictionary is empty or not loaded")
        return data

    # Normalize discipline from JSON or context (default to ME)
    json_types = _normalize_asset_type_values(data.get("asset_type") or asset_type)
    current_type = next(iter(json_types)) if json_types else "ME"

    print(f"[DICT-LOOKUP] UBC: '{ubc}', Type: '{current_type}'")

    entry, matched_types = _find_dictionary_entry(ubc, current_type, json_types)
    if not entry:
        return data
    apply_match(entry)
    if not data.get("asset_type") and matched_types:
        data["asset_type"] = next(iter(matched_types))
    return data


def _update_manual_field_flags(data, submitted_keys, asset_type=None):
    """Set/clear asset_group_manual / attribute_manual after a human save.

    A field becomes manual ("1") when the reviewer submitted a non-blank value
    that differs from the current dictionary derivation for the tag; it reverts
    to dictionary control ("0") when the submission is blank or matches the
    dictionary. Fields not in the submitted form keep their persisted flag.
    Returns True when either flag changed.
    """
    ubc = str(data.get("UBC Asset Tag") or data.get("UBC Tag") or "").strip().upper()
    json_types = _normalize_asset_type_values(data.get("asset_type") or asset_type)
    current_type = next(iter(json_types)) if json_types else "ME"
    entry, _ = _find_dictionary_entry(ubc, current_type, json_types)
    changed = False
    for field, flag_key, entry_key in (
        ("Asset Group", "asset_group_manual", "asset_group"),
        ("Attribute", "attribute_manual", "attribute_set"),
    ):
        if field not in submitted_keys:
            continue
        value = str(data.get(field) or "").strip()
        dict_value = str((entry or {}).get(entry_key) or "").strip()
        new_flag = "1" if value and value != dict_value else "0"
        if str(data.get(flag_key) or "0").strip() != new_flag:
            changed = True
        data[flag_key] = new_flag
    return changed

def _resolve_description(asset_group, ubc_tag, existing_desc, asset_type=None):
    """Resolve Description with dictionary description taking priority over Asset Group.

    Priority order:
    1) Keep an existing non-empty description (user-edited or sourced data).
    2) Use the dictionary entry's `description` when the UBC tag matches:
       - exact composite key (e.g., "AHU-100|ME")
       - composite prefix (e.g., "AHU|ME")
       - legacy simple key (e.g., "AHU")
    3) Fall back to Asset Group.
    4) Fall back to UBC tag.
    """
    if str(existing_desc or "").strip():
        return existing_desc

    tag_raw = str(ubc_tag or "").strip()
    tag = tag_raw.upper()
    cd = get_asset_dictionary() or {}

    # Normalize discipline for composite keys (default to ME).
    json_types = _normalize_asset_type_values(asset_type)
    current_type = next(iter(json_types)) if json_types else "ME"

    prefix = ""

    if tag and cd:
        # STEP 1: exact composite key match
        composite_key = f"{tag}|{current_type}"
        entry = cd.get(composite_key)
        if isinstance(entry, dict) and entry.get("description"):
            prefix = str(entry.get("description") or "").strip()

        # STEP 2: composite prefix match (e.g., "AHU|ME")
        if not prefix:
            for key in sorted(cd.keys(), key=len, reverse=True):
                if "|" not in key:
                    continue
                try:
                    tag_prefix, key_type = key.split("|", 1)
                except ValueError:
                    continue
                if key_type.upper() != current_type:
                    continue
                if tag.startswith(str(tag_prefix or "").upper()):
                    entry = cd.get(key) or {}
                    desc = entry.get("description") if isinstance(entry, dict) else None
                    if desc:
                        prefix = str(desc).strip()
                        break

        # STEP 3: legacy simple key match (e.g., "AHU")
        if not prefix:
            for key in sorted(cd.keys(), key=len, reverse=True):
                if "|" in key:
                    continue
                if tag.startswith(str(key or "").upper()):
                    entry = cd.get(key) or {}
                    if not isinstance(entry, dict):
                        continue
                    dict_types = _get_entry_asset_types(entry)
                    if json_types and dict_types and dict_types.isdisjoint(json_types):
                        continue
                    desc = entry.get("description")
                    if desc:
                        prefix = str(desc).strip()
                        break

    # Fall back to Asset Group when no dictionary description is available.
    if not prefix:
        prefix = str(asset_group or "").strip()

    if prefix and tag_raw:
        return f"{prefix} - {tag_raw}"
    return prefix or tag_raw

def _sanitize_qr_value(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (raw or "").strip()).upper()

def _qr_conflicts(new_qr: str, asset_type: str, building: str) -> bool:
    filename = f"{new_qr}_{asset_type}_{building}.json"
    if os.path.exists(os.path.join(JSON_DIR, filename)):
        return True
    if not _connectable():
        return False
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT 1 FROM "{QR_CODES_TABLE}" WHERE "{QR_CODE_ID_COL}" = ? LIMIT 1', (new_qr,))
            if cur.fetchone(): return True
            cols = list(qrdb.table_columns(conn, QR_CODE_ASSETS_TABLE))
            qr_col = next((c for c in QR_CODE_ASSETS_QR_COL_CANDIDATES if c in cols), None)
            if qr_col:
                cur.execute(f'SELECT 1 FROM "{QR_CODE_ASSETS_TABLE}" WHERE "{qr_col}" LIKE ? LIMIT 1', (f"{new_qr}%",))
                if cur.fetchone(): return True
    except Exception:
        return False
    return False

def _rename_asset_images(old_qr: str, new_qr: str, building: str, asset_type: str):
    if not os.path.isdir(IMG_DIR): return
    for tag in SEQ_SHOW:
        seq = tag.replace('-', '').strip()
        for ext in VALID_IMAGE_EXTS:
            old_name = f"{old_qr} {building} {asset_type} - {seq}{ext}"
            new_name = f"{new_qr} {building} {asset_type} - {seq}{ext}"
            old_path = os.path.join(IMG_DIR, old_name)
            new_path = os.path.join(IMG_DIR, new_name)
            if not os.path.exists(old_path): continue
            if os.path.exists(new_path): continue
            try:
                os.rename(old_path, new_path)
            except Exception as e:
                print(f"Image rename warning ({old_name} -> {new_name}): {e}")

def _update_processed_json_log_filename(old_name: str, new_name: str):
    if not PROCESSED_JSON_LOG.exists(): return
    try:
        with open(PROCESSED_JSON_LOG, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict): return
        if old_name in data:
            data[new_name] = data.pop(old_name)
            with open(PROCESSED_JSON_LOG, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass

def _replace_qr_in_db(old_qr: str, new_qr: str):
    if not _connectable(): return
    updates = [
        (QR_CODES_TABLE, QR_CODE_ID_COL, False),
        (QR_CODE_ASSETS_TABLE, None, True),
        (SDI_TABLE, "QR Code", False),
        ("sdi_dataset_EL", "QR Code", False),
        ("sdi_print_out", "QR Code", False),
        ("sdi_print_out_arch", "QR Code", False),
        ("process_type", "QR Code", False),
        ("json_files", "code", False),
    ]
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            for table, col, prefix in updates:
                cols = list(qrdb.table_columns(conn, table))
                if table == QR_CODE_ASSETS_TABLE:
                    col = next((c for c in QR_CODE_ASSETS_QR_COL_CANDIDATES if c in cols), None)
                    if not col: continue
                elif col not in cols:
                    continue
                if prefix:
                    cur.execute(
                        f'UPDATE "{table}" SET "{col}" = ? || substr("{col}", length(?) + 1) WHERE "{col}" LIKE ?',
                        (new_qr, old_qr, f"{old_qr}%")
                    )
                else:
                    cur.execute(
                        f'UPDATE "{table}" SET "{col}" = ? WHERE "{col}" = ?',
                        (new_qr, old_qr)
                    )
            conn.commit()
    except Exception as e:
        print(f"DB QR replace warning ({old_qr} -> {new_qr}): {e}")

def find_image(qr, building, seq_tag):
    seq = seq_tag.replace('-', '').strip()
    base = f"{qr} {building} ME - {seq}"
    for ext in VALID_IMAGE_EXTS:
        p = os.path.join(IMG_DIR, base + ext)
        if os.path.exists(p): return os.path.basename(p)
    return None


def _normalize_avg_ai_conf(value):
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return None, "N/A"

    conf = max(0.0, min(100.0, conf))
    conf_text = f"{conf:.2f}".replace(".", ",")
    return conf, f"{conf_text}%"


def _normalize_conf_bound(value, default):
    if value in (None, ""):
        return default
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, v))


def _matches_conf_range(item, conf_min, conf_max):
    try:
        score = float(item.get("Avg_ai_conf"))
    except (TypeError, ValueError):
        return False
    return conf_min <= score <= conf_max

def _extract_avg_ai_conf(payload: dict):
    if not isinstance(payload, dict):
        return None

    direct = payload.get("Avg_ai_conf")
    if direct not in (None, ""):
        return direct

    scores = payload.get("confidence_scores")
    if not isinstance(scores, dict):
        return None

    exclude_fields = set()
    asset_type = str(payload.get("asset_type", "") or "").upper()
    if "ME" in asset_type:
        qr = str(payload.get("qr_code", "") or "").strip()
        building = str(payload.get("building_number", "") or "").strip()
        if not find_image(qr, building, "-3"):
            exclude_fields.add("Technical Safety BC")

    values = []
    for field, score in scores.items():
        if field in exclude_fields:
            continue
        try:
            values.append(float(score))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)

def get_qr_locations():
    locs = {}
    if not _connectable(): return locs
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cols = list(qrdb.table_columns(conn, QR_CODES_TABLE))
            if QR_LOCATION_COL in cols:
                cur.execute(f'SELECT "{QR_CODE_ID_COL}", "{QR_LOCATION_COL}" FROM "{QR_CODES_TABLE}"')
                for r in cur.fetchall():
                    if r[0]: locs[str(r[0]).strip()] = str(r[1] or "").strip()
    except: pass
    return locs

def get_qr_sdi_states():
    states = {}
    if not _connectable(): return states
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(f'SELECT "{QR_CODE_ID_COL}", "sdi" FROM "{QR_CODES_TABLE}"')
            for row in cur.fetchall():
                try:
                    val = 1 if str(row["sdi"]) == "1" else 0
                    if row[QR_CODE_ID_COL]: states[str(row[QR_CODE_ID_COL]).strip()] = val
                except: pass
    except: pass
    return states

def get_qr_process_map():
    if not _connectable(): return {}
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cols = set(qrdb.table_columns(conn, QR_CODE_ASSETS_TABLE))
            
            qr_col = next((c for c in QR_CODE_ASSETS_QR_COL_CANDIDATES if c in cols), None)
            if not qr_col: return {}
            if QR_CODE_ASSETS_PROCESS_COL not in cols: return {}

            query = f'SELECT "{qr_col}", "{QR_CODE_ASSETS_PROCESS_COL}" FROM "{QR_CODE_ASSETS_TABLE}"'
            cur.execute(query)
            
            mapping = {}
            for row in cur.fetchall():
                q = str(row[0]).strip() if row[0] is not None else ""
                if q:
                    q_clean = q.split(None, 1)[0]
                    try:
                        p_val = int(str(row[1]).strip())
                    except Exception:
                        p_val = None
                    if p_val is None:
                        continue
                    prev = mapping.get(q_clean)
                    if prev is None or p_val > prev:
                        mapping[q_clean] = p_val
            return {k: str(v) for k, v in mapping.items()}
    except Exception as e:
        print(f"Error fetching process map: {e}")
        return {}

def get_qr_ai_status_map():
    if not _connectable(): return {}
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            columns = list(qrdb.table_columns(conn, QR_CODES_TABLE))
            if 'ai_status' not in columns:
                return {}
            cur.execute(f'SELECT "{QR_CODE_ID_COL}", "ai_status" FROM "{QR_CODES_TABLE}"')
            mapping = {}
            for row in cur.fetchall():
                q_raw = str(row[0]).strip() if row[0] is not None else ""
                if not q_raw: continue
                mapping[q_raw] = str(row[1])
            return mapping
    except Exception as e:
        print(f"⚠️ Failed to fetch ai_status map: {e}")
        return {}

def get_qr_dates():
    dates = {}
    if not _connectable(): return dates
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cols = list(qrdb.table_columns(conn, QR_CODES_TABLE))
            if "date_set" in cols:
                cur.execute(f'SELECT "{QR_CODE_ID_COL}", "date_set" FROM "{QR_CODES_TABLE}"')
                for r in cur.fetchall():
                    if r[0]: dates[str(r[0]).strip()] = str(r[1] or "").strip()[:16]
    except Exception as e:
        print(f"Error fetching QR dates: {e}")
    return dates

def get_qr_capture_notes():
    notes = {}
    if not _connectable(): return notes
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cols = list(qrdb.table_columns(conn, QR_CODES_TABLE))
            if "capture_notes" in cols:
                cur.execute(f'SELECT "{QR_CODE_ID_COL}", "capture_notes" FROM "{QR_CODES_TABLE}"')
                for r in cur.fetchall():
                    if r[0]: notes[str(r[0]).strip()] = str(r[1] or "").strip()
    except Exception as e:
        print(f"Error fetching QR capture notes: {e}")
    return notes

def get_qr_captured_by():
    qr_to_name = {}
    if not _connectable(): return qr_to_name
    try:
        qr_to_user = {}
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            # Backend-conditional position function (instr is SQLite-only; PG uses strpos).
            # Also: "" is a zero-length identifier on PG; the empty string is ''.
            pos_fn = "strpos" if qrdb.is_postgres() else "instr"
            cur.execute(
                f'SELECT substr("code_assets", 1, {pos_fn}("code_assets", \' \') - 1) AS qr, "user" '
                'FROM "QR_code_assets" '
                "WHERE \"user\" IS NOT NULL AND \"user\" != '' "
                f"AND {pos_fn}(\"code_assets\", ' ') > 0"
            )
            for qr, username in cur.fetchall():
                if qr and username:
                    qr_to_user.setdefault(qr, username)
        if not qr_to_user:
            return qr_to_name
        try:
            usernames = set(qr_to_user.values())
            name_map = {
                u.username: (u.name or u.username)
                for u in User.query.filter(User.username.in_(usernames)).all()
            }
        except Exception:
            name_map = {}
        for qr, uname in qr_to_user.items():
            qr_to_name[qr] = name_map.get(uname, uname)
    except Exception as e:
        print(f"Error fetching captured-by map: {e}")
    return qr_to_name

def load_json_items(process_target: str = "0"):
    items = []
    locations_map = get_qr_locations()
    sdi_map = get_qr_sdi_states()
    process_map = get_qr_process_map()
    ai_status_map = get_qr_ai_status_map()
    dates_map = get_qr_dates()
    capture_notes_map = get_qr_capture_notes()
    captured_by_map = get_qr_captured_by()
    if process_map is None: process_map = {}

    if not os.path.exists(JSON_DIR): return []

    for filename in os.listdir(JSON_DIR):
        if not _is_me_filename(filename): continue
        m = JSON_NAME_RE.match(filename)
        if not m: continue
        
        qr, discipline, building = m.groups()
        doc_id = filename[:-5]
        
        try:
            current_status = process_map.get(qr)
            if current_status in (None, ""):
                _auto_register_qr_code(qr, "0")
                current_status = "0"

            if str(current_status) != str(process_target):
                continue
            
            path = os.path.join(JSON_DIR, filename)
            if not os.path.exists(path): continue
            
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            
            data = raw.get("structured_data") or {}
            if not isinstance(data, dict): continue

            for k in ["Manufacturer", "Model", "Serial Number", "Year", "UBC Tag", "Technical Safety BC", 
                      "Asset Group", "Attribute", "Main Asset", "Diameter"]:
                data.setdefault(k, "")
            data.setdefault("Flagged", "false")
            data.setdefault("Approved", "")

            # Apply dictionary rules
            data = apply_dictionary_rules(data, asset_type=discipline)
            
            # Resolve description using best tag
            tag_val = data.get("UBC Asset Tag") or data.get("UBC Tag", "")
            data["Description"] = _resolve_description(data.get("Asset Group"), tag_val, data.get("Description"), discipline)

            missing_tags = [tag for tag in SEQ_CHECK if not find_image(qr, building, tag)]
            has_extra_photo = bool(find_image(qr, building, '-4'))

            if str(process_target) == "2": sdi_visual = 1
            else: sdi_visual = sdi_map.get(qr, 0)

            avg_ai_conf, avg_ai_conf_display = _normalize_avg_ai_conf(_extract_avg_ai_conf(raw))
            comp_score, comp_score_display = _normalize_avg_ai_conf(raw.get("completeness_score"))

            items.append({
                **data,
                "doc_id": doc_id,
                "qr_code": qr,
                "Capture Date": dates_map.get(qr, ""),
                "Capture Notes": capture_notes_map.get(qr, ""),
                "captured_by": captured_by_map.get(qr, ""),
                "building": building,
                "Location": locations_map.get(qr, ""),
                "asset_type": discipline,
                "Flagged": data.get("Flagged", "false"),
                "Approved": data.get("Approved", ""),
                "ExcludeSDI": sdi_visual,
                "Modified": raw.get("modified", False),
                "Missed Photo": "YES" if missing_tags else "NO",
                "Missing List": ", ".join(missing_tags),
                "Photos Summary": f"{3 - len(missing_tags)}/3",
                "Extra Photo": has_extra_photo,
                "ai_status": ai_status_map.get(qr, "0"),
                "Avg_ai_conf": avg_ai_conf,
                "Avg_ai_conf_display": avg_ai_conf_display,
                "Comp_score": comp_score,
                "Comp_score_display": comp_score_display,
            })
        except Exception as e:
            print(f"Error loading item {filename}: {e}")
            continue

    _attach_package_locks_to_items(items)
    return items


def _parse_filter_values(raw):
    """Parse a comma-joined filter value ('A' or 'A,B') -> ordered de-duplicated
    list. Shared by the building and asset-group filters. '' / None -> []."""
    out, seen = [], set()
    for part in str(raw or "").split(","):
        code = part.strip()
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def get_filtered_data_and_counts(query_args, process_target: str = "0", apply_client_filters: bool = True):
    flagged = query_args.get("flagged")
    modified = query_args.get("modified")
    missed = query_args.get("missed")
    bld_codes = _parse_filter_values(query_args.get("building") or query_args.get("filter_building"))
    conf_min = _normalize_conf_bound(query_args.get("conf_min"), 0)
    conf_max = _normalize_conf_bound(query_args.get("conf_max"), 100)
    
    # Context-aware default for Approved filter
    approved_arg = query_args.get("approved")
    if approved_arg is None:
        approved_arg = query_args.get("filter_approved")
    
    if approved_arg is not None:
        approved = approved_arg
    else:
        # Default: Pending across every tab
        approved = "False"

    archive = query_args.get("archive")
    hide_arch = (archive != 'false')

    # Client-side equivalent filters (so navigation/save-next respects UI filters)
    filter_qr = (query_args.get("filter_qr") or "").strip().upper()
    filter_date = (query_args.get("filter_date") or "").strip()
    filter_tag = (query_args.get("filter_tag") or "").strip().upper()
    filter_notes = (query_args.get("filter_notes") or "").strip().upper()
    filter_groups = _parse_filter_values(query_args.get("filter_group"))

    all_data = load_json_items(process_target)
    
    if hide_arch:
        archived = set()
        if _connectable():
            try:
                with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
                    cur = conn.cursor()
                    if qrdb.has_table(conn, SDI_ARCHIVE_TABLE):
                        cur.execute(f'SELECT "QR Code" FROM "{SDI_ARCHIVE_TABLE}"')
                        archived = {str(r[0]).strip() for r in cur.fetchall() if r[0]}
            except: pass
        base_data = [i for i in all_data if i.get("qr_code") not in archived]
    else:
        base_data = all_data

    if conf_min != 0 or conf_max != 100:
        base_data = [i for i in base_data if _matches_conf_range(i, conf_min, conf_max)]

    filtered = base_data
    if flagged == "true": filtered = [i for i in filtered if i.get("Flagged") == "true"]
    if modified == "true": filtered = [i for i in filtered if i.get("Modified")]
    if missed == "true": filtered = [i for i in filtered if i.get("Missed Photo") == "YES"]
    if bld_codes:
        bld_set = set(bld_codes)
        filtered = [i for i in filtered if i.get("building") in bld_set]
    
    if approved == "True": filtered = [i for i in filtered if i.get("Approved") == "True"]
    elif approved == "False": filtered = [i for i in filtered if i.get("Approved") != "True"]

    if apply_client_filters:
        if filter_qr:
            filtered = [i for i in filtered if filter_qr in (i.get("qr_code") or "").upper()]
        if filter_date:
            filtered = [i for i in filtered if (i.get("Capture Date") or "").startswith(filter_date)]
        if filter_tag:
            filtered = [i for i in filtered if filter_tag in (i.get("UBC Asset Tag") or i.get("UBC Tag") or "").upper()]
        if filter_notes in ("YES", "NO"):
            filtered = [
                i for i in filtered
                if ("YES" if (i.get("Capture Notes") or "").strip() else "NO") == filter_notes
            ]
        if filter_groups:
            group_set = set(filter_groups)
            filtered = [i for i in filtered if (i.get("Asset Group") or "") in group_set]
    
    # Fallback ordering when the client does not supply an explicit sequence:
    # mirror the dashboard's default sort (Capture Date, newest first) so the
    # review prev/next order is sensible even without the localStorage order.
    filtered.sort(key=lambda x: str(x.get('doc_id') or ''))
    filtered.sort(key=lambda x: str(x.get('Capture Date') or ''), reverse=True)
    return filtered, base_data

def _get_counts_dict(ds):
    return {
        "flagged": sum(1 for i in ds if i.get("Flagged") == "true"),
        "modified": sum(1 for i in ds if i.get("Modified")),
        "missed": sum(1 for i in ds if i.get("Missed Photo") == "YES")
    }

def _get_card_scope_data(process_target: str, building_filter: str = ""):
    data = load_json_items(process_target)
    codes = _parse_filter_values(building_filter)
    if codes:
        code_set = set(codes)
        data = [item for item in data if item.get("building") in code_set]
    return data


# --- ROUTES ---
@app.route("/")
@login_required
def index():
    if not has_permission(current_user, "application", "reviewer_mechanical", "viewer"):
        return access_denied_response("Asset Reviewer - Mechanical")
    try:
        tab_param = request.args.get("tab")
        process_param = request.args.get("process", "0")
        
        active_tab = "new"
        if tab_param: active_tab = tab_param
        elif process_param == "1": active_tab = "update"
        elif process_param == "2": active_tab = "manual"
        
        table_new, base_new = get_filtered_data_and_counts(request.args, "0", apply_client_filters=False)
        table_update, base_update = get_filtered_data_and_counts(request.args, "1", apply_client_filters=False)
        table_manual, base_manual = get_filtered_data_and_counts(request.args, "2", apply_client_filters=False)

        card_building_filter = request.args.get("building") or request.args.get("filter_building") or ""
        card_new = _get_card_scope_data("0", card_building_filter)
        card_update = _get_card_scope_data("1", card_building_filter)
        card_manual = _get_card_scope_data("2", card_building_filter)
        card_total_assets = len(card_new) + len(card_update) + len(card_manual)
        card_pending_review = sum(1 for i in card_new + card_update + card_manual if i.get("Approved") != "True")
        card_missed = sum(1 for i in card_new + card_update + card_manual if i.get("Missed Photo") == "YES")
        card_approved = card_total_assets - card_pending_review
        
        counts_new = _get_counts_dict(base_new)
        counts_update = _get_counts_dict(base_update)
        counts_manual = _get_counts_dict(base_manual)

        approved_filter_raw = request.args.get("approved")
        if approved_filter_raw is None:
            approved_filter_raw = request.args.get("filter_approved")
        if approved_filter_raw is None:
            approved_filter_raw = "False"

        approved_filter = approved_filter_raw

        return render_template(
            "dashboard.html",
            title="Asset Review Dashboard - Mechanical",
            data_new=table_new,
            data_update=table_update,
            data_manual=table_manual,
            flagged_filter=request.args.get("flagged"),
            modified_filter=request.args.get("modified"),
            missed_filter=request.args.get("missed"),
            conf_min=_normalize_conf_bound(request.args.get("conf_min"), 0),
            conf_max=_normalize_conf_bound(request.args.get("conf_max"), 100),
            approved_filter=approved_filter,
            approved_filter_raw=approved_filter_raw,
            archive_filter_active=(request.args.get("archive") != 'false'),
            count_flagged_new=counts_new['flagged'],
            count_modified_new=counts_new['modified'],
            count_missed_new=counts_new['missed'],
            count_flagged_update=counts_update['flagged'],
            count_modified_update=counts_update['modified'],
            count_missed_update=counts_update['missed'],
            count_flagged_manual=counts_manual['flagged'],
            count_modified_manual=counts_manual['modified'],
            count_missed_manual=counts_manual['missed'],
            count_unapproved_new=sum(1 for i in base_new if i.get("Approved") != "True"),
            count_unapproved_update=sum(1 for i in base_update if i.get("Approved") != "True"),
            count_unapproved_manual=sum(1 for i in base_manual if i.get("Approved") != "True"),
            card_total_assets=card_total_assets,
            card_approved=card_approved,
            card_pending_review=card_pending_review,
            card_missed=card_missed,
            active_tab=active_tab,
            username=current_user.username,
            building_name_map=_get_buildings_name_map(),
            is_admin=is_admin(current_user),
            can_edit=has_permission(current_user, "application", "reviewer_mechanical", "editor"),
        )
    except Exception as e:
        import traceback
        return f"CRASH: {str(e)}\n{traceback.format_exc()}", 500

@app.route("/api/asset-preview/<doc_id>")
@login_required
def asset_preview(doc_id):
    """Lightweight JSON for the review page's 'Next Asset' preview rail: QR, UBC
    tag, location, and thumbnail URLs for an arbitrary doc_id. Lets the client
    drive the preview from the dashboard's filtered+sorted order (localStorage),
    so the previewed 'next' asset matches the order the reviewer actually sees."""
    if not has_permission(current_user, "application", "reviewer_mechanical", "viewer"):
        return jsonify({"error": "forbidden"}), 403
    if not _is_me_filename(f"{doc_id}.json"):
        return jsonify({"error": "bad id"}), 400
    path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    qr, discipline, building = m.groups()
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return jsonify({"error": "unreadable"}), 500
    sd = loaded.get("structured_data", {}) or {}
    label_map = {'-0': 'Asset Plate', '-1': 'UBC Tag', '-2': 'Main Picture', '-3': 'Misc', '-4': 'Extra Photo'}
    images = []
    for tag in SEQ_SHOW:
        fn = find_image(qr, building, tag)
        if fn:
            images.append({"url": url_for('serve_image', filename=fn), "label": label_map.get(tag, tag)})
    return jsonify({
        "doc_id": doc_id,
        "qr_code": qr,
        "ubc_tag": sd.get("UBC Asset Tag") or sd.get("UBC Tag") or "",
        "location": get_qr_locations().get(qr, "") or sd.get("Location") or "",
        "images": images,
    })

@app.route("/review/<doc_id>")
@login_required
def review(doc_id):
    if not has_permission(current_user, "application", "reviewer_mechanical", "viewer"):
        return access_denied_response("Asset Reviewer - Mechanical")
    if not _is_me_filename(f"{doc_id}.json"): return "ID Inválido", 400
    
    path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(path): return "Não encontrado", 404

    with open(path, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    qr, discipline, building = m.groups()
    package_lock = _get_qr_package_lock(qr)
    package_locked = bool(package_lock.get("locked"))
    
    data = loaded.get("structured_data", {}) or {}
    data = apply_dictionary_rules(data, asset_type=discipline)
    
    tag_val = data.get("UBC Asset Tag") or data.get("UBC Tag", "")
    data["Description"] = _resolve_description(data.get("Asset Group"), tag_val, data.get("Description"), discipline)
    
    locs = get_qr_locations()
    capture_notes = get_qr_capture_notes().get(qr, "")
    avg_ai_conf, avg_ai_conf_display = _normalize_avg_ai_conf(_extract_avg_ai_conf(loaded))
    
    images = {}
    for tag in SEQ_SHOW:
        fn = find_image(qr, building, tag)
        images[tag] = {"exists": bool(fn), "url": url_for('serve_image', filename=fn) if fn else None}

    # --- NEW: Calculate Pagination Index & Next Asset ---
    process_param = request.args.get("process", "0")
    filtered_data, _ = get_filtered_data_and_counts(request.args, process_param)
    
    total_count = len(filtered_data)
    current_index = 0
    ids = [i['doc_id'] for i in filtered_data]
    
    next_asset = None

    if doc_id in ids:
        idx = ids.index(doc_id)
        current_index = idx + 1
        
        # Calculate Next Asset
        if idx + 1 < len(filtered_data):
            next_item = filtered_data[idx + 1]
            
            # Find ALL valid thumbnails for the next asset
            next_images = []
            next_qr = next_item.get('qr_code')
            next_bld = next_item.get('building')
            
            # Map suffix tags to human readable labels
            label_map = {
                '-0': 'Asset Plate',
                '-1': 'UBC Tag',
                '-2': 'Main Picture',
                '-3': 'Misc',
                '-4': 'Extra Photo'
            }
            
            for tag in SEQ_SHOW:
                fn = find_image(next_qr, next_bld, tag)
                if fn:
                    next_images.append({
                        "url": url_for('serve_image', filename=fn),
                        "label": label_map.get(tag, tag)
                    })
            
            next_asset = {
                "qr_code": next_item.get('qr_code'),
                "ubc_tag": next_item.get('UBC Asset Tag') or next_item.get('UBC Tag'),
                "location": next_item.get('Location'),
                "images": next_images
            }

    return render_template(
        "review.html",
        title="Asset Review - Mechanical",
        doc_id=doc_id,
        qr_code=qr,
        building=building,
        location=locs.get(qr, ""),
        capture_notes=capture_notes,
        installation_date=get_installation_date(qrdb, DB_PATH, qr),
        asset_type=discipline,
        data=data,
        avg_ai_conf=avg_ai_conf,
        avg_ai_conf_display=avg_ai_conf_display,
        capture_info=_fetch_capture_info(qr, building, "ME"),
        images=images,
        asset_group_options=_fetch_column_values(ASSET_GROUP_TABLE, ASSET_GROUP_COL),
        attribute_options=_fetch_column_values(ATTRIBUTE_TABLE, ATTRIBUTE_COL),
        username=current_user.username,
        asset_dictionary=get_asset_dictionary(),
        current_index=current_index,
        total_count=total_count,
        next_asset=next_asset,
        review_locked=(str(data.get("Approved", "") or "").strip() == "True" or package_locked),
        package_locked=package_locked,
        package_lock=package_lock,
        package_lock_message=_package_lock_message(package_lock) if package_locked else "",
        review_buttons=REVIEW_BUTTONS,
        review_endpoints=REVIEW_ENDPOINTS,
    )

def _file_data_uri(abs_path, downscale=True, max_dim=1400, quality=82):
    """Return a base64 'data:' URI for an image on disk, so it can be inlined
    into a fully self-contained HTML export (no external/asset-route refs).
    When downscale is on, the image is shrunk (longest side <= max_dim) and
    re-encoded as JPEG via Pillow to keep export size small; EXIF orientation
    is honoured. Falls back to the raw file bytes if Pillow is unavailable or
    fails. Returns None when the path is missing."""
    if not abs_path or not os.path.exists(abs_path):
        return None
    import base64
    data = None
    mime = "image/jpeg"
    if downscale:
        try:
            from PIL import Image, ImageOps
            with Image.open(abs_path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((max_dim, max_dim))
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
        except Exception as e:
            print(f"[export] Pillow downscale failed for {abs_path}: {e}")
            data = None
    if data is None:
        with open(abs_path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(abs_path)[1].lower()
        mime = {".png": "image/png", ".jpeg": "image/jpeg", ".jpg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")

def _build_review_sheet_context(doc_id):
    """Load and assemble the template context for the single-asset Review Sheet,
    shared by the print view (review_print) and the HTML export (review_export).
    Photos and the logo are inlined as base64 data URIs so the very same render
    works both on-screen (Save-as-PDF) and as a portable offline file. Returns
    (context, None) on success or (None, (body, status)) on a load error."""
    if not _is_me_filename(f"{doc_id}.json"):
        return None, ("ID Inválido", 400)

    path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(path):
        return None, ("Não encontrado", 404)

    with open(path, 'r', encoding='utf-8') as f:
        loaded = json.load(f)

    qr, discipline, building = JSON_NAME_RE.match(f"{doc_id}.json").groups()

    data = loaded.get("structured_data", {}) or {}
    data = apply_dictionary_rules(data, asset_type=discipline)
    tag_val = data.get("UBC Asset Tag") or data.get("UBC Tag", "")
    data["Description"] = _resolve_description(data.get("Asset Group"), tag_val, data.get("Description"), discipline)

    locs = get_qr_locations()
    # Show the human-readable building description (Buildings."Name") rather than
    # the bare building code; fall back to the code if it isn't in the table.
    building_name = _get_buildings_name_map().get(str(building).strip()) or building
    _, avg_ai_conf_display = _normalize_avg_ai_conf(_extract_avg_ai_conf(loaded))

    images = {}
    for tag in SEQ_SHOW:
        fn = find_image(qr, building, tag)
        uri = _file_data_uri(os.path.join(IMG_DIR, fn)) if fn else None
        images[tag] = {"exists": bool(uri), "url": uri}

    logo_uri = _file_data_uri(os.path.join(str(STATIC_DIR), "ubc-facilities_logo.jpg"),
                              downscale=False) if STATIC_DIR else None

    # Degrade like the builder's other DB lookups: a DB outage must not 500 the sheet.
    try:
        installation_date = get_installation_date(qrdb, DB_PATH, qr)
    except Exception as e:
        print(f"Error fetching installation date: {e}")
        installation_date = ""

    ctx = dict(
        title="Asset Review Sheet - Mechanical",
        doc_id=doc_id,
        qr_code=qr,
        building=building,
        building_name=building_name,
        location=locs.get(qr, ""),
        asset_type=discipline,
        data=data,
        avg_ai_conf_display=avg_ai_conf_display,
        capture_info=_fetch_capture_info(qr, building, "ME"),
        capture_notes=get_qr_capture_notes().get(qr, ""),
        installation_date=installation_date,
        images=images,
        approved=(str(data.get("Approved", "") or "").strip() == "True"),
        username=current_user.username,
        generated_on=datetime.now().strftime("%Y-%m-%d %H:%M"),
        logo_uri=logo_uri,
    )
    return ctx, None

@app.route("/review/<doc_id>/print")
@login_required
def review_print(doc_id):
    """Print-optimized Asset Review Sheet (Save-as-PDF via the browser). Renders
    the self-contained sheet inline in a new tab and auto-opens the print dialog."""
    if not has_permission(current_user, "application", "reviewer_mechanical", "viewer"):
        return access_denied_response("Asset Reviewer - Mechanical")
    ctx, err = _build_review_sheet_context(doc_id)
    if err:
        return err
    return render_template("review_print.html", auto_print=True, **ctx)

@app.route("/review/<doc_id>/export")
@login_required
def review_export(doc_id):
    """Export the Asset Review Sheet as a fully self-contained HTML file (photos
    and logo inlined as base64 data URIs) and send it to the browser as a
    download. Portable/offline; no server-side PDF engine required."""
    if not has_permission(current_user, "application", "reviewer_mechanical", "viewer"):
        return access_denied_response("Asset Reviewer - Mechanical")
    ctx, err = _build_review_sheet_context(doc_id)
    if err:
        return err
    html = render_template("review_print.html", auto_print=False, **ctx)
    safe_qr = re.sub(r"[^A-Za-z0-9_.-]", "", str(ctx["qr_code"])) or "asset"
    safe_bld = re.sub(r"[^A-Za-z0-9_.-]", "", str(ctx["building"]))
    fname = f"Asset_Review_ME_{safe_qr}_{safe_bld}.html"
    return send_file(
        BytesIO(html.encode("utf-8")),
        as_attachment=True,
        download_name=fname,
        mimetype="text/html",
    )

def _fetch_column_values(table: str, col: str):
    if not _connectable(): return []
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL')
            vals = {str(r[0]).strip() for r in cur.fetchall() if r[0]}
            return sorted(list(vals), key=lambda s: (s.lower(), s))
    except: return []


def _fetch_capture_info(qr_code_id: str, building: str = "", discipline: str = "ME") -> dict:
    """Look up the latest capture user / date / hour from QR_code_assets for
    the given QR (optionally constrained by building + discipline). Returns
    {"user": str, "date": "YYYY-MM-DD", "hour": "HH:MM", "gps_coordinates": str}
    -- empty strings when nothing is on file. Used to surface User Activity
    Log fields beneath the Description on the review page."""
    blank = {"user": "", "date": "", "hour": "", "gps_coordinates": ""}
    if not _connectable():
        return blank
    qr = (qr_code_id or "").strip()
    if not qr:
        return blank
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            result = blank.copy()
            cols = set(qrdb.table_columns(conn, QR_CODES_TABLE))
            if "GPS Coordinates (lat,long)" in cols and QR_CODE_ID_COL in cols:
                cur.execute(
                    f'SELECT "GPS Coordinates (lat,long)" AS gps_coordinates '
                    f'FROM "{QR_CODES_TABLE}" WHERE "{QR_CODE_ID_COL}" = ? LIMIT 1',
                    (qr,),
                )
                gps_row = cur.fetchone()
                if gps_row:
                    result["gps_coordinates"] = (gps_row["gps_coordinates"] or "").strip()
            bld = (building or "").strip()
            disc = (discipline or "").strip()
            if bld and disc:
                pattern = f"{qr} {bld} {disc}%"
            elif bld:
                pattern = f"{qr} {bld}%"
            else:
                pattern = f"{qr} %"
            cur.execute(
                # PG: "user" is reserved (returns CURRENT_USER); quote everywhere.
                # "QR_code_assets" and "ID" are mixed-case, must be quoted.
                """SELECT "user", "date_hour" FROM "QR_code_assets"
                   WHERE code_assets LIKE ?
                     AND "user" IS NOT NULL AND "user" <> ''
                   ORDER BY "date_hour" DESC, "ID" DESC
                   LIMIT 1""",
                (pattern,),
            )
            row = cur.fetchone()
            if not row:
                return result
            user = (row["user"] or "").strip()
            stamp = (row["date_hour"] or "").strip()
            date_part, hour_part = "", ""
            if "T" in stamp:
                head, _, tail = stamp.partition("T")
                date_part = head
                hour_part = tail[:5]
            result.update({"user": user, "date": date_part, "hour": hour_part})
            return result
    except Exception as e:
        print(f"[WARN] capture info fetch failed for {qr}: {e}")
        return blank


def _get_buildings_name_map():
    """Return {Code: Name} for the Buildings table. Used by the dashboard
    Building filter to render dropdown options as "<Code> - <Name>" while
    keeping <option value> as the bare code so existing column filters work."""
    if not _connectable():
        return {}
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute('SELECT "Code", "Name" FROM "Buildings" WHERE "Code" IS NOT NULL')  # PG: mixed-case identifiers must be quoted
            return {
                str(code).strip(): str(name or "").strip()
                for code, name in cur.fetchall()
                if str(code or "").strip()
            }
    except Exception:
        return {}


@app.route("/review/<doc_id>", methods=["POST"])
@login_required
@require_permission("application", "reviewer_mechanical", "editor")
def save_review(doc_id):
    path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(path): return "N?o encontrado", 404

    with open(path, 'r', encoding='utf-8') as f:
        content = json.load(f)

    structured = content.get("structured_data", {})
    if not isinstance(structured, dict): structured = {}

    # --- Preserve dashboard context & navigation BEFORE mutating current record ---
    dq = request.form.get("dashboard_query", "")
    dashboard_query_string, saved_params = normalize_dashboard_query(dq)
    next_url = url_for("index")
    if dashboard_query_string:
        next_url += f"?{dashboard_query_string}"

    proc_param = saved_params.get("process") or request.args.get("process", "0")

    # Build filter args (saved params take precedence over current URL args)
    filter_args = {
        "flagged": saved_params.get("flagged") or request.args.get("flagged"),
        "modified": saved_params.get("modified") or request.args.get("modified"),
        "missed": saved_params.get("missed") or request.args.get("missed"),
        "archive": saved_params.get("archive") or request.args.get("archive"),
        "conf_min": saved_params.get("conf_min") or request.args.get("conf_min"),
        "conf_max": saved_params.get("conf_max") or request.args.get("conf_max"),
        "building": saved_params.get("building") or request.args.get("building"),
        "filter_building": saved_params.get("filter_building") or request.args.get("filter_building"),
        "approved": saved_params.get("approved") or saved_params.get("filter_approved") or request.args.get("approved", ""),
        "filter_approved": saved_params.get("filter_approved") or request.args.get("filter_approved"),
        "filter_qr": saved_params.get("filter_qr") or request.args.get("filter_qr"),
        "filter_tag": saved_params.get("filter_tag") or request.args.get("filter_tag"),
        "filter_notes": saved_params.get("filter_notes") or request.args.get("filter_notes"),
        "filter_group": saved_params.get("filter_group") or request.args.get("filter_group"),
    }

    class FilterArgs:
        def __init__(self, d):
            self._d = d
        def get(self, key, default=None):
            return self._d.get(key, default)

    # Snapshot the filtered list before any changes so navigation honors the original view
    filtered_before, _ = get_filtered_data_and_counts(FilterArgs(filter_args), proc_param)
    nav_ids = [i['doc_id'] for i in filtered_before]
    nav_idx = nav_ids.index(doc_id) if doc_id in nav_ids else None

    def _resolve_neighbor(action_name):
        """Return the next or previous doc_id within the active filter, even when
        the current doc has just been filtered out (e.g., toggling Approved on a
        Pending-only view). Recomputes against a broader scope so the doc's
        position is known, then picks the closest neighbor that still matches
        the live filter."""
        if action_name not in ("save_next", "save_prev"):
            return None
        # Honor the client-supplied neighbor first: the dashboard passes the
        # visible filtered+sorted order (column sort included) via the hidden
        # nav_next / nav_prev fields, so Save & Next/Prev follow exactly what the
        # reviewer saw on the dashboard. Fall back to the server order only when
        # the client value is missing or stale.
        client_nav = (request.form.get("nav_next") if action_name == "save_next"
                      else request.form.get("nav_prev")) or ""
        client_nav = client_nav.strip()
        if client_nav and _is_me_filename(f"{client_nav}.json") \
                and os.path.exists(os.path.join(JSON_DIR, f"{client_nav}.json")):
            return client_nav
        if nav_idx is not None:
            if action_name == "save_next" and nav_idx + 1 < len(nav_ids):
                return nav_ids[nav_idx + 1]
            if action_name == "save_prev" and nav_idx > 0:
                return nav_ids[nav_idx - 1]
            return None
        # Empty string disables the approved filter inside get_filtered_data_and_counts;
        # passing None would let it fall back to the "Pending-only" default for the
        # New/Update tabs and the doc would still be excluded.
        broader_args = dict(filter_args)
        broader_args["approved"] = ""
        broader_args["filter_approved"] = ""
        broader_data, _ = get_filtered_data_and_counts(FilterArgs(broader_args), proc_param)
        broader_ids = [i['doc_id'] for i in broader_data]
        if doc_id not in broader_ids:
            return None
        bi = broader_ids.index(doc_id)
        nav_set = set(nav_ids)
        if action_name == "save_next":
            for j in range(bi + 1, len(broader_ids)):
                if broader_ids[j] in nav_set:
                    return broader_ids[j]
        else:
            for j in range(bi - 1, -1, -1):
                if broader_ids[j] in nav_set:
                    return broader_ids[j]
        return None
    # -----------------------------------------------------------------------------

    action = request.form.get("action")
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return "Bad ID", 400
    qr, discipline, building = m.groups()
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        flash(f"Could not verify SDI package status: {exc}", "danger")
        return redirect(url_for("review", doc_id=doc_id, **saved_params))
    if package_lock.get("locked"):
        if action in ("save_next", "save_prev"):
            neighbor = _resolve_neighbor(action)
            if neighbor:
                return redirect(url_for("review", doc_id=neighbor, **saved_params))
            return redirect(next_url)
        flash(_package_lock_message(package_lock), "warning")
        return redirect(url_for("review", doc_id=doc_id, **saved_params))

    # The "save_toggle" action comes from the Pending/Approved pill: it submits the
    # full form alongside the flipped Approved value so the user's pending edits
    # land in the JSON. Bypass the approved-record early return so the merge below
    # actually runs.
    if str(structured.get("Approved", "") or "").strip() == "True" and action != "save_toggle":
        if action == "save_stay":
            # Approved records are read-only; nothing to save, stay on the page.
            return redirect(url_for("review", doc_id=doc_id, **saved_params))
        neighbor = _resolve_neighbor(action)
        if neighbor:
            return redirect(url_for("review", doc_id=neighbor, **saved_params))
        return redirect(next_url)

    if "installation_date" in request.form:
        try:
            installation_date_iso = parse_installation_date(request.form.get("installation_date", ""))
            update_installation_date(
                qrdb, DB_PATH, qr, installation_date_iso,
                modified_by=current_user.username,
                app_name="reviewer_mechanical",
                audit_log_change=_audit_log_change,
            )
        except (InstallationDateError, LookupError, RuntimeError) as exc:
            flash(str(exc), "danger")
            return redirect(url_for("review", doc_id=doc_id, **saved_params))

    if "Flagged" in request.form:
        new_flagged = "true" if request.form.get("Flagged") == "on" else "false"
        if structured.get("Flagged") != new_flagged:
            content["modified"] = True
        structured["Flagged"] = new_flagged
    else:
        structured.setdefault("Flagged", "false")

    # Track which fields were submitted by the human vs. filled by dictionary rules
    human_submitted_keys: set[str] = set()
    if "Flagged" in request.form:
        human_submitted_keys.add("Flagged")
    for k, v in request.form.items():
        if k in ("Flagged", "action", "dashboard_query", "new_qr_code", "installation_date",
                 "asset_group_manual", "attribute_manual"): continue
        if structured.get(k) != v: content["modified"] = True
        structured[k] = v
        human_submitted_keys.add(k)

    # Record reviewer overrides of the dictionary-owned fields before the
    # rules re-apply, so the re-apply (and every later sync/render) keeps them.
    if _update_manual_field_flags(structured, human_submitted_keys, discipline):
        content["modified"] = True

    # Re-apply rules with potentially changed UBC Tag
    structured = apply_dictionary_rules(structured, asset_type=discipline)
    tag_val = structured.get("UBC Asset Tag") or structured.get("UBC Tag", "")
    structured["Description"] = _resolve_description(structured.get("Asset Group"), tag_val, structured.get("Description"), discipline)
    content["structured_data"] = structured

    # Build per-field source map for audit: SDI columns the human submitted are
    # tagged 'human'; columns whose value comes from dictionary auto-fill (not in
    # the form) are tagged 'ai:dictionary'. Maps SDI column names, not form keys.
    _SDI_TO_FORM = {
        "Manufacturer": "Manufacturer", "Model": "Model", "Serial": "Serial Number",
        "UBC Tag": "UBC Asset Tag", "Asset Group": "Asset Group",
        "Attribute": "Attribute", "Description": "Description",
        "Diameter": "Diameter", "Year": "Year",
        "Technical Safety BC": "Technical Safety BC",
        "Approved": "Approved", "Flagged": "Flagged", "Main Asset": "Main Asset",
    }
    audit_source_map_me = {}
    for sdi_col, form_key in _SDI_TO_FORM.items():
        audit_source_map_me[sdi_col] = "human" if form_key in human_submitted_keys else "ai:dictionary"
    # Avg_ai_conf is metadata derived from the underlying JSON
    audit_source_map_me["Avg_ai_conf"] = "ai:gpt-5.5"

    new_qr_raw = request.form.get("new_qr_code", "")
    new_qr_clean = None
    new_qr_error = None
    if new_qr_raw:
        candidate = _sanitize_qr_value(new_qr_raw)
        if not qr.upper().startswith("T"):
            new_qr_error = "QR replacement is only available for temporary codes."
        elif not candidate:
            new_qr_error = "Please enter a valid QR code using letters and numbers."
        elif not re.match(r"^[A-Za-z0-9]+$", new_qr_raw.strip()):
            new_qr_error = "Use only letters and numbers for the new QR code."
        elif candidate.startswith("T"):
            new_qr_error = "The final QR code cannot start with T."
        elif candidate != qr and _qr_conflicts(candidate, discipline, building):
            new_qr_error = f"QR code {candidate} already exists."
        elif candidate != qr:
            new_qr_clean = candidate
            content["modified"] = True

    target_qr = new_qr_clean or qr
    content["qr_code"] = target_qr

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(content, f, ensure_ascii=False, indent=4)

    if new_qr_error:
        _db_upsert_sdi_dataset(
            qr, building, structured,
            avg_ai_conf=_extract_avg_ai_conf(content),
            audit_source_map=audit_source_map_me,
            audit_description="POST /review (qr-replace error)",
        )
        flash(new_qr_error, "danger")
        return redirect(url_for("review", doc_id=doc_id))

    if new_qr_clean:
        new_doc_id = f"{new_qr_clean}_{discipline}_{building}"
        new_filename = f"{new_doc_id}.json"
        new_path = os.path.join(JSON_DIR, new_filename)
        if os.path.exists(new_path):
            flash(f"QR code {new_qr_clean} is already linked to another record.", "danger")
            _db_upsert_sdi_dataset(
                qr, building, structured,
                avg_ai_conf=_extract_avg_ai_conf(content),
                audit_source_map=audit_source_map_me,
                audit_description="POST /review (qr-replace conflict)",
            )
            return redirect(url_for("review", doc_id=doc_id))
        try:
            os.rename(path, new_path)
            _update_processed_json_log_filename(f"{doc_id}.json", new_filename)
            _rename_asset_images(qr, new_qr_clean, building, discipline)
            _replace_qr_in_db(qr, new_qr_clean)
            path = new_path
            doc_id = new_doc_id
            qr = new_qr_clean
        except Exception as e:
            flash(f"Could not move asset to the new QR code: {e}", "danger")
            _db_upsert_sdi_dataset(
                qr, building, structured,
                avg_ai_conf=_extract_avg_ai_conf(content),
                audit_source_map=audit_source_map_me,
                audit_description="POST /review (qr-replace failed)",
            )
            return redirect(url_for("review", doc_id=doc_id))

    _db_upsert_sdi_dataset(
        qr, building, structured,
        avg_ai_conf=_extract_avg_ai_conf(content),
        audit_source_map=audit_source_map_me,
        audit_description="POST /review",
    )

    # When the Pending pill submitted the form, return the user to the same review
    # page so the lock/unlock state of the now-saved Approved value takes effect.
    # The Save button (save_stay) also returns here so the reviewer keeps editing.
    if action in ("save_toggle", "save_stay"):
        if action == "save_stay":
            flash("Changes saved.", "success")
        return redirect(url_for("review", doc_id=doc_id, **saved_params))

    neighbor = _resolve_neighbor(action)
    if neighbor:
        return redirect(url_for("review", doc_id=neighbor, **saved_params))

    return redirect(next_url)

@app.route("/toggle_approved/<doc_id>", methods=["POST"])
@login_required
@require_permission("application", "reviewer_mechanical", "editor")
def toggle_approved(doc_id):
    path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(path): return jsonify({"success": False}), 404
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return jsonify({"success": False, "error": "Invalid ID"}), 400
    qr, discipline, building = m.groups()
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        return _package_lock_check_failed_response(exc)
    if package_lock.get("locked"):
        return _package_lock_response(package_lock)
    
    with open(path, 'r', encoding='utf-8') as f:
        content = json.load(f)
    structured = content.get("structured_data", {})
    if not isinstance(structured, dict):
        structured = {}
    
    curr = structured.get("Approved", "")
    new_val = "True" if curr != "True" else ""
    structured["Approved"] = new_val
    if new_val == "True":
        structured = apply_dictionary_rules(structured, asset_type=discipline)
        tag_val = structured.get("UBC Asset Tag") or structured.get("UBC Tag", "")
        structured["Description"] = _resolve_description(
            structured.get("Asset Group"),
            tag_val,
            structured.get("Description"),
            discipline
        )
    content["structured_data"] = structured
    
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(content, f, ensure_ascii=False, indent=4)
    
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            db_val = "1" if new_val == "True" else "0"   # PG CHECK requires Approved IN ('0','1')
            old_app_row = cur.execute(
                f'SELECT "{QR_APPROVED_COL}" FROM "{QR_CODES_TABLE}" WHERE "{QR_CODE_ID_COL}" = ?',
                (qr,),
            ).fetchone()
            old_app_val = old_app_row[0] if old_app_row else None
            sql = f'INSERT INTO "{QR_CODES_TABLE}" ("{QR_CODE_ID_COL}", "{QR_APPROVED_COL}") VALUES (?, ?) ON CONFLICT("{QR_CODE_ID_COL}") DO UPDATE SET "{QR_APPROVED_COL}"=excluded."{QR_APPROVED_COL}"'
            cur.execute(sql, (qr, db_val))
            if _audit_log_change and str(old_app_val or "") != db_val:
                try:
                    _audit_log_change(
                        conn,
                        qr_code=qr,
                        app_name="reviewer_me",
                        table_name=QR_CODES_TABLE,
                        record_pk=qr,
                        op_type="UPDATE",
                        field_changes={QR_APPROVED_COL: (old_app_val, db_val)},
                        source="human",
                        description="POST /toggle_approved (ME)",
                    )
                except Exception as audit_exc:
                    print(f"[audit] ME toggle_approved audit failed: {audit_exc}")
            conn.commit()
        _db_upsert_sdi_dataset(
            qr, building, structured,
            avg_ai_conf=_extract_avg_ai_conf(content),
            audit_source="human",
            audit_description="POST /toggle_approved (ME)",
        )
    except: pass

    return jsonify({"success": True, "new_value": new_val})

def _reprocess_json_protected(json_path: str) -> str:
    """Return a non-empty reason if the ME JSON holds reviewer-owned state that an AI
    re-extraction must not overwrite (High-Risk Invariant: do not erase human
    overrides). Checks structured_data.Approved == "True" (reviewed) and the
    top-level 'modified' flag (set whenever a reviewer edits any field)."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            content = json.load(f)
    except Exception:
        return ""
    if not isinstance(content, dict):
        return ""
    sd = content.get("structured_data", {})
    sd = sd if isinstance(sd, dict) else {}
    if str(sd.get("Approved", "") or "").strip() == "True":
        return "approved"
    mod = content.get("modified", False)
    if mod is True or str(mod).strip().lower() in ("true", "1", "yes"):
        return "modified"
    return ""


@app.route("/toggle_ai_status/<doc_id>", methods=["POST"])
@login_required
@require_permission("application", "reviewer_mechanical", "editor")
def toggle_ai_status(doc_id):
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m: return jsonify({"success": False, "error": "Invalid ID"}), 400
    qr = m.groups()[0]
    # Explicit, confirmed override from the UI to reprocess despite draft edits.
    force = str(request.values.get("force", "")).strip().lower() in ("1", "true", "yes")
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        return _package_lock_check_failed_response(exc)
    if package_lock.get("locked"):
        return _package_lock_response(package_lock)

    if not _connectable(): return jsonify({"success": False, "error": "Database not accessible"}), 500

    try:
        new_val = '0'
        reprocess_moved = ""
        json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
        # Path-traversal guard: JSON_NAME_RE's building group allows '/' and '..', so a
        # crafted doc_id could otherwise make os.replace touch a file outside JSON_DIR.
        try:
            _jdir_real = os.path.realpath(JSON_DIR)
            if os.path.commonpath([os.path.realpath(json_path), _jdir_real]) != _jdir_real:
                json_path = ""
        except Exception:
            json_path = ""
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cols = list(qrdb.table_columns(conn, QR_CODES_TABLE))
            if "ai_status" not in cols:
                cur.execute(f'ALTER TABLE "{QR_CODES_TABLE}" ADD COLUMN "ai_status" TEXT DEFAULT "0"')

            cur.execute(f'SELECT "ai_status" FROM "{QR_CODES_TABLE}" WHERE "{QR_CODE_ID_COL}" = ?', (qr,))
            row = cur.fetchone()
            current = '0'
            if row and row[0]: current = str(row[0])
            
            new_val = '1' if current == '0' else '0'

            # Toggling to '0' is an explicit "re-extract" request: move the JSON aside so the
            # extractor's skip-if-exists guard re-runs the asset on the next cron cycle.
            # Guards (do NOT erase human work): block reprocess when the JSON is Approved or
            # human-edited, and when the asset is a Manual Entry row (Col_process=2) that
            # deliberately bypasses AI. Flagged=true assets are intentionally NOT blocked —
            # low-confidence AI results are the main reason to reprocess.
            will_reprocess = False
            forced_reprocess = False
            bak_path = ""
            if new_val == '0' and json_path and os.path.isfile(json_path):
                protected = _reprocess_json_protected(json_path)
                # 'modified' (draft edits) may be overridden by an explicit, confirmed force
                # request; 'approved' may not (un-approve first — it has SDI/Planon impact).
                if protected and not (protected == "modified" and force):
                    # Return 200 with success=False so the dashboard surfaces this message
                    # (the toggle's .fail() handler only shows a generic "Server Error").
                    msg = ("This asset is approved; reprocessing is blocked to protect the reviewed values. "
                           "Un-approve it first if you need to re-run AI."
                           if protected == "approved"
                           else "This asset has manual edits; reprocessing is blocked to protect your corrections.")
                    return jsonify({
                        "success": False, "error": msg, "code": "reprocess_blocked",
                        "forceable": (protected == "modified"),
                    })
                try:
                    cur.execute(
                        f'SELECT 1 FROM "{QR_CODE_ASSETS_TABLE}" WHERE "code_assets" LIKE ? '
                        f'AND "{QR_CODE_ASSETS_PROCESS_COL}" = ? LIMIT 1',
                        (qr + "%", "2"),
                    )
                    manual_entry = cur.fetchone() is not None
                except Exception:
                    manual_entry = False
                if manual_entry:
                    return jsonify({
                        "success": False,
                        "error": "This is a Manual Entry asset; reprocessing is blocked to protect the manually entered data.",
                        "code": "manual_entry_locked",
                    })
                forced_reprocess = bool(protected == "modified" and force)
                will_reprocess = True
                bak_path = f"{json_path}.bak_{datetime.utcnow().strftime('%Y%m%d_%H%M%SZ')}"

            cur.execute(f"""
                INSERT INTO "{QR_CODES_TABLE}" ("{QR_CODE_ID_COL}", "ai_status")
                VALUES (?, ?)
                ON CONFLICT("{QR_CODE_ID_COL}") DO UPDATE SET
                    "ai_status" = excluded."ai_status";
            """, (qr, new_val))

            if _audit_log_change and current != new_val:
                try:
                    if forced_reprocess:
                        _src = "human"
                        _desc = "POST /toggle_ai_status (ME) + FORCED reprocess (discarded manual edits; JSON backed up)"
                    elif will_reprocess:
                        _src = "system"
                        _desc = "POST /toggle_ai_status (ME) + reprocess requested (move JSON aside)"
                    else:
                        _src = "system"
                        _desc = "POST /toggle_ai_status (ME)"
                    _audit_log_change(
                        conn,
                        qr_code=qr,
                        app_name="reviewer_me",
                        table_name=QR_CODES_TABLE,
                        record_pk=qr,
                        op_type="UPDATE",
                        field_changes={"ai_status": (current, new_val)},
                        source=_src,
                        description=_desc,
                    )
                except Exception as audit_exc:
                    print(f"[audit] ME toggle_ai_status audit failed: {audit_exc}")
            conn.commit()

        # Move the JSON aside AFTER the DB commit so a crash or commit failure can never
        # leave the file gone while ai_status is unchanged (which would make the asset
        # unreachable). Worst case here is benign: ai_status=0 with the JSON still present
        # simply does not reprocess until the operator retries.
        if will_reprocess and os.path.isfile(json_path):
            try:
                os.replace(json_path, bak_path)
                reprocess_moved = os.path.basename(bak_path)
            except Exception as move_exc:
                print(f"[reprocess] ME toggle_ai_status JSON move failed for {qr}: {move_exc}")

        return jsonify({"success": True, "new_value": new_val, "reprocess_requested": bool(reprocess_moved)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/toggle_sdi/<doc_id>", methods=["POST"])
@login_required
@require_permission("application", "reviewer_mechanical", "editor")
def toggle_sdi(doc_id):
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m: return jsonify({"success": False}), 400
    qr = m.groups()[0]
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        return _package_lock_check_failed_response(exc)
    if package_lock.get("locked"):
        return _package_lock_response(package_lock)
    
    new_val = 0
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT "sdi" FROM "{QR_CODES_TABLE}" WHERE "{QR_CODE_ID_COL}" = ?', (qr,))
            row = cur.fetchone()
            curr_sdi = 1 if (row and str(row[0]) == "1") else 0
            new_val = 1 if curr_sdi == 0 else 0

            sql_sdi = f'INSERT INTO "{QR_CODES_TABLE}" ("{QR_CODE_ID_COL}", "sdi") VALUES (?, ?) ON CONFLICT("{QR_CODE_ID_COL}") DO UPDATE SET "sdi"=excluded."sdi"'
            cur.execute(sql_sdi, (qr, new_val))

            new_process = "2" if new_val == 1 else "0"
            like_pattern = qr + "%"
            sql_proc_upd = f'UPDATE "{QR_CODE_ASSETS_TABLE}" SET "{QR_CODE_ASSETS_PROCESS_COL}" = ? WHERE "code_assets" LIKE ?'
            cur.execute(sql_proc_upd, (new_process, like_pattern))

            if cur.rowcount == 0:
                sql_proc_ins = f'INSERT INTO "{QR_CODE_ASSETS_TABLE}" ("code_assets", "Col_process") VALUES (?, ?) ON CONFLICT("code_assets") DO UPDATE SET "Col_process"=excluded."Col_process"'
                cur.execute(sql_proc_ins, (qr, new_process))

            if _audit_log_change:
                try:
                    _audit_log_change(
                        conn,
                        qr_code=qr,
                        app_name="reviewer_me",
                        table_name=QR_CODES_TABLE,
                        record_pk=qr,
                        op_type="UPDATE",
                        field_changes={"sdi": (curr_sdi, new_val)},
                        source="human",
                        description="POST /toggle_sdi (ME)",
                    )
                except Exception as audit_exc:
                    print(f"[audit] ME toggle_sdi audit failed: {audit_exc}")

            conn.commit()
        json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            structured = data.get("structured_data")
            if not isinstance(structured, dict):
                structured = {}
                data["structured_data"] = structured
            structured["ExcludeSDI"] = 1 if new_val == 1 else 0
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500
    
    return jsonify({"success": True, "new_value": new_val})

@app.route("/check_sdi/<qr_code>")
@login_required
@require_permission("application", "reviewer_mechanical", "viewer")
def check_sdi(qr_code):
    if not _connectable():
        return jsonify({"error": "Database not accessible"}), 500
    try:
        package_lock = _get_qr_package_lock(qr_code, raise_on_error=True)
        return jsonify({
            "exists": bool(package_lock.get("locked")),
            "source": package_lock.get("source", ""),
            "package_id": package_lock.get("package_id", ""),
        })
    except qrdb.DatabaseError as e:   # cross-backend (sqlite3 + psycopg2)
        if "no such table" in str(e).lower() or "does not exist" in str(e).lower():
            return jsonify({"exists": False})
        return jsonify({"error": f"Database query failed: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ai_status_map")
@login_required
@require_permission("application", "reviewer_mechanical", "viewer")
def ai_status_map():
    """Read-only {QR: ai_status} map for the dashboard's AI-status auto-refresh
    poller. Must stay read-only: it never writes and never triggers extraction."""
    if not _connectable():
        return jsonify({"success": False, "error": "Database not accessible"}), 500
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            if "ai_status" not in set(qrdb.table_columns(conn, QR_CODES_TABLE)):
                return jsonify({"success": True, "statuses": {}})
            cur.execute(f'SELECT "{QR_CODE_ID_COL}", "ai_status" FROM "{QR_CODES_TABLE}"')
            statuses = {}
            for qr, status in cur.fetchall():
                qr = str(qr or "").strip()
                if qr:
                    statuses[qr] = "1" if str(status or "").strip() == "1" else "0"
        return jsonify({"success": True, "statuses": statuses})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def _fetch_capture_meta_map(qr_codes: list[str]) -> dict:
    """Returns {qr: {"user": str, "date": "YYYY-MM-DD", "hour": "HH:MM"}} for
    the latest QR_code_assets entry per QR. Powers the Captured by / Date /
    Hour columns in the Excel export."""
    out: dict = {}
    if not qr_codes or not _connectable():
        return out
    cleaned = [str(q).strip() for q in qr_codes if str(q or "").strip()]
    if not cleaned:
        return out
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cols = set(qrdb.table_columns(conn, "QR_code_assets"))
            if "user" not in cols or "date_hour" not in cols:
                return out
            qr_prefix = _qr_prefix_expr()  # backend-conditional (PG has no INSTR)
            chunk = 500
            for i in range(0, len(cleaned), chunk):
                part = cleaned[i:i + chunk]
                placeholders = ",".join("?" for _ in part)
                cur.execute(
                    # MAX("user")/MAX("date_hour") aggregates so PG's strict GROUP BY accepts them
                    # (SQLite tolerated bare non-aggregate columns; PG doesn't). Same row count, deterministic.
                    f"""SELECT {qr_prefix} AS qr,
                               MAX("user") AS user_v, MAX("date_hour") AS date_hour_v, MAX("ID") AS latest_id
                          FROM "QR_code_assets"
                         WHERE "user" IS NOT NULL AND "user" != ''
                           AND {qr_prefix} IN ({placeholders})
                         GROUP BY {qr_prefix}""",
                    part,
                )
                for qr, user, date_hour, _ in cur.fetchall():
                    if not qr:
                        continue
                    stamp = (date_hour or "").strip()
                    date_part, hour_part = "", ""
                    if "T" in stamp:
                        head, _, tail = stamp.partition("T")
                        date_part = head
                        hour_part = tail[:5]
                    out[str(qr).strip()] = {
                        "user": user or "",
                        "date": date_part,
                        "hour": hour_part,
                    }
    except Exception as e:
        print(f"[WARN] _fetch_capture_meta_map failed: {e}")
    return out


@app.route("/export/review-xlsx", methods=["POST"])
@login_required
@require_permission("application", "reviewer_mechanical", "viewer")
def export_review_xlsx():
    """Export the currently visible rows of the active tab as a styled .xlsx."""
    payload = request.get_json(silent=True) or {}
    tab = (payload.get("tab") or "new").lower()
    bld_codes = _parse_filter_values(payload.get("building"))
    building = ", ".join(bld_codes)
    qr_codes = [str(q).strip() for q in (payload.get("qr_codes") or []) if str(q or "").strip()]

    process_target = {"new": "0", "update": "1", "manual": "2"}.get(tab, "0")
    all_rows = load_json_items(process_target)
    qr_set = set(qr_codes)
    rows = [r for r in all_rows if str(r.get("qr_code") or "").strip() in qr_set] if qr_set else []
    if bld_codes:
        bld_set = set(bld_codes)
        rows = [r for r in rows if r.get("building") in bld_set]

    meta = _fetch_capture_meta_map(qr_codes)
    logo_path = str(BASE_DIR / "review_asset_templates" / "static" / "ubc-facilities_logo.jpg")
    blob = excel_export.build_workbook(
        process="ME",
        tab=tab,
        building=building,
        rows=rows,
        meta=meta,
        process_title="Mechanical",
        logo_path=logo_path,
    )
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    if not bld_codes:
        bld = "all"
    elif len(bld_codes) <= 3:
        bld = "_".join(bld_codes)
    else:
        bld = f"{bld_codes[0]}_plus{len(bld_codes) - 1}"
    fname = f"Review_ME_{bld}_{ts}.xlsx"
    return send_file(
        BytesIO(blob),
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/user-activity", methods=["GET", "POST"])
@login_required
@require_permission("application", "reviewer_mechanical", "viewer")
def get_user_activity():
    """Fetch user activity data filtered by QR codes from dashboard."""
    if not _connectable():
        return jsonify({"error": "Database not found"}), 404
    
    # Get QR codes filter from POST body
    qr_filter = []
    if request.method == "POST" and request.is_json:
        data = request.get_json() or {}
        qr_filter = data.get("qr_codes", [])
    
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cursor = conn.cursor()

            # Check if required columns exist (backend-agnostic)
            columns = set(qrdb.table_columns(conn, "QR_code_assets"))
            if "user" not in columns or "date_hour" not in columns:
                return jsonify({"error": "User tracking columns not available."}), 404

            # PG quirks: "user" is a reserved keyword; "QR_code_assets"/"ID" are mixed case;
            # INSTR() is SQLite-only. Use the helper for the QR-prefix expression.
            qr_prefix = _qr_prefix_expr()

            # MAX("user")/MAX("date_hour"): aggregate-wrap so PG's strict GROUP BY accepts them
            # (SQLite tolerated bare non-aggregate columns; PG doesn't). Same row count.
            if qr_filter:
                placeholders = ",".join(["?" for _ in qr_filter])
                query = f"""
                    SELECT
                        {qr_prefix} as qr_code,
                        MAX("user") as user_v,
                        MAX("date_hour") as date_hour_v,
                        MAX("ID") as latest_id
                    FROM "QR_code_assets"
                    WHERE "user" IS NOT NULL AND "user" != ''
                      AND {qr_prefix} IN ({placeholders})
                    GROUP BY {qr_prefix}
                    ORDER BY MAX("date_hour") DESC
                """
                cursor.execute(query, qr_filter)
                rows = cursor.fetchall()

                user_query = f"""
                    SELECT DISTINCT "user" FROM "QR_code_assets"
                    WHERE "user" IS NOT NULL AND "user" != ''
                      AND {qr_prefix} IN ({placeholders})
                    ORDER BY "user"
                """
                cursor.execute(user_query, qr_filter)
            else:
                cursor.execute(f"""
                    SELECT
                        {qr_prefix} as qr_code,
                        MAX("user") as user_v,
                        MAX("date_hour") as date_hour_v,
                        MAX("ID") as latest_id
                    FROM "QR_code_assets"
                    WHERE "user" IS NOT NULL AND "user" != ''
                      AND (code_assets LIKE '% ME -%' OR code_assets LIKE '% ME %')
                    GROUP BY {qr_prefix}
                    ORDER BY MAX("date_hour") DESC
                """)
                rows = cursor.fetchall()

                cursor.execute("""
                    SELECT DISTINCT "user" FROM "QR_code_assets"
                    WHERE "user" IS NOT NULL AND "user" != ''
                      AND (code_assets LIKE '% ME -%' OR code_assets LIKE '% ME %')
                    ORDER BY "user"
                """)

            users = [row[0] for row in cursor.fetchall()]
            
            # Format response
            activity_data = []
            for row in rows:
                date_part, time_part = "", ""
                if row[2] and "T" in row[2]:
                    parts = row[2].split("T")
                    date_part = parts[0]
                    time_part = parts[1][:5] if len(parts) > 1 else ""
                
                activity_data.append({
                    "qr_code": row[0] or "",
                    "user": row[1] or "",
                    "date": date_part,
                    "hour": time_part
                })
            
            return jsonify({"data": activity_data, "users": users})

    except qrdb.DatabaseError as e:   # cross-backend (sqlite3 + psycopg2)
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return "OK", 200

@app.route("/images/<path:filename>")
@login_required
@require_permission("application", "reviewer_mechanical", "viewer")
def serve_image(filename):
    return send_from_directory(IMG_DIR, filename)

with app.app_context():
    db.create_all()
    ensure_user_access_table()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5002, debug=True)
