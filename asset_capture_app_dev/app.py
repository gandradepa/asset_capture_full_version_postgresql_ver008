# /home/developer/asset_capture_app_dev/app.py

import os
import re
import json
import hashlib
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone
from typing import List, Any, Dict

# Ensure project root and utils directory are on sys.path for module resolution (systemd/gunicorn)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UTILS_DIR = os.path.join(BASE_DIR, "utils")
PROJECT_ROOT = os.path.dirname(BASE_DIR)
for p in (BASE_DIR, UTILS_DIR, PROJECT_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

# Shared audit-trail logger (sibling package at project root)
from audit.logger import log_change
from audit.diff import diff_dicts

# Import parameter update service with a robust fallback
try:
    from utils.parameter_update_service import (
        execute_parameter_update,
        get_current_params,
        get_current_asset_type,
        detect_parameter_changes
    )
except ModuleNotFoundError:
    import importlib.util
    pus_path = os.path.join(UTILS_DIR, "parameter_update_service.py")
    spec = importlib.util.spec_from_file_location("parameter_update_service", pus_path)
    if spec and spec.loader:
        pus = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pus)
        execute_parameter_update = pus.execute_parameter_update
        get_current_params = pus.get_current_params
        get_current_asset_type = pus.get_current_asset_type
        detect_parameter_changes = pus.detect_parameter_changes
    else:
        raise

# Shared coordinate validators. Canonical home: API/validators_shared.py
# (the discipline-agnostic normalize_* family). Loaded defensively so a missing
# file never breaks /submit — GPS coordinates are best-effort capture metadata.
try:
    from API.validators_shared import normalize_lat_lng, resolve_capture_coordinates, format_gps_coordinates
except Exception:
    normalize_lat_lng = None
    resolve_capture_coordinates = None
    format_gps_coordinates = None
    try:
        import importlib.util as _ilu
        _vs_path = os.path.join(PROJECT_ROOT, "API", "validators_shared.py")
        _spec = _ilu.spec_from_file_location("validators_shared", _vs_path)
        if _spec and _spec.loader and os.path.exists(_vs_path):
            _vs = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_vs)  # raises if the file vanished mid-load
            normalize_lat_lng = _vs.normalize_lat_lng
            resolve_capture_coordinates = _vs.resolve_capture_coordinates
            format_gps_coordinates = _vs.format_gps_coordinates
    except Exception:
        normalize_lat_lng = None
        resolve_capture_coordinates = None
        format_gps_coordinates = None
    if normalize_lat_lng is None:
        def normalize_lat_lng(lat, lng):  # safe no-op fallback
            return "", ""
    if format_gps_coordinates is None:
        def format_gps_coordinates(lat, lng):  # safe no-op fallback
            nlat, nlng = normalize_lat_lng(lat, lng)
            return f"{nlat},{nlng}" if nlat and nlng else ""
    if resolve_capture_coordinates is None:
        def resolve_capture_coordinates(device_lat, device_lng, building_lat="", building_lng="",
                                        existing_source="", existing_lat=""):
            nlat, nlng = normalize_lat_lng(device_lat, device_lng)
            return (nlat, nlng, "device") if nlat and nlng else ("", "", "")

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_from_directory, jsonify, session
)
from werkzeug.utils import secure_filename
from flask_login import login_required, current_user
from dotenv import load_dotenv

# Add the shared service directory to the Python path
sys.path.append('/home/developer/auth_service')
from auth_model import db, bcrypt, ensure_user_access_table, has_permission, is_admin, require_permission
from auth_controller import login_manager
from auth_routes import auth_bp

# -----------------------------------------------------------------------------
# Flask app setup
# -----------------------------------------------------------------------------
# MODIFIED: Load the shared .env file
load_dotenv('/home/developer/auth_service.env', override=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URI")
app.config["SESSION_COOKIE_DOMAIN"] = os.getenv("SESSION_COOKIE_DOMAIN")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30 MB uploads

if os.getenv("STAGING_INSECURE_COOKIES") == "1":
    app.config["SESSION_COOKIE_DOMAIN"] = None
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = False

# Initialize extensions
db.init_app(app)
bcrypt.init_app(app)
login_manager.init_app(app)

# Register the authentication blueprint
app.register_blueprint(auth_bp, url_prefix='/auth')


APP_ROOT = app.root_path
# Upload path changed as requested
UPLOAD_DIR = "/home/developer/Capture_photos_upload"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# DB path (override with env QR_CODES_DB_PATH if needed)
DB_PATH = os.environ.get(
    "QR_CODES_DB_PATH",
    os.path.join(APP_ROOT, "data", "QR_codes.db")
)

ELAPSE_JSON_DIR = os.environ.get("JSON_OUTPUT_DIR", "/home/developer/Output_jason_api")

# -----------------------------------------------------------------------------
# Behavior after submit: success (default) or capture
# -----------------------------------------------------------------------------
def get_after_submit_mode(request_after: str | None = None) -> str:
    if request_after:
        v = request_after.strip().lower()
        if v in ("success", "capture"):
            return v
    v = os.environ.get("AFTER_SUBMIT", "success").strip().lower()
    return v if v in ("success", "capture") else "success"

# -----------------------------------------------------------------------------
# Helpers (general)
# -----------------------------------------------------------------------------
def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        return str(x).strip()
    except Exception:
        return ""


MISSING_INPUT_TOKENS = {"", "none", "null", "nan", "undefined"}
VALID_QR_CODE_PATTERN = re.compile(r"^(?:T\d+|\d+)$", re.IGNORECASE)
MOBILE_CAPTURE_PROCESS = "0"
PRIMARY_CAPTURE_FILE_KEYS = ("image_0", "image_1")


def _is_missing_input(x: Any) -> bool:
    return _safe_str(x).lower() in MISSING_INPUT_TOKENS


def _has_primary_capture_photo(files: Any) -> bool:
    if files is None:
        return False
    for file_key in PRIMARY_CAPTURE_FILE_KEYS:
        uploaded_file = files.get(file_key)
        filename = getattr(uploaded_file, "filename", "") if uploaded_file else ""
        if _safe_str(filename):
            return True
    return False


def _capture_validation_errors(qr_code: str, building_code: str, location: str, asset_type: str) -> List[str]:
    errors = []
    if _is_missing_input(qr_code):
        errors.append("QR code is required.")
    if _is_missing_input(building_code):
        errors.append("Building is required.")
    if _is_missing_input(location):
        errors.append("Location is required.")
    if _is_missing_input(asset_type):
        errors.append("Asset type is required.")
    return errors


def _is_valid_asset_base(base: str) -> bool:
    text = _safe_str(base)
    match = re.fullmatch(r"(\S+)\s+(\S+)\s+([A-Za-z]+)\s+-\s+(\d+)", text)
    if not match:
        return False
    qr_code, building_code, asset_type, _seq = match.groups()
    if _is_missing_input(qr_code) or _is_missing_input(building_code) or _is_missing_input(asset_type):
        return False
    return bool(VALID_QR_CODE_PATTERN.fullmatch(qr_code))

def sanitize_component(s: str, *, prefer_digits: bool = True, maxlen: int = 80) -> str:
    s = (s or "").strip()
    if prefer_digits:
        m = re.search(r'(\d{6,})', s)
        if m:
            s = m.group(1)
    s = re.sub(r'^[a-zA-Z]+:', '', s).strip('/')
    s = re.sub(r'[<>:"/\\|?*]+', '_', s)
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^A-Za-z0-9._-]+', '', s).strip('._-')
    if not s:
        s = hashlib.sha1(b'fallback').hexdigest()[:8]
    return s[:maxlen]

def parse_iso8601_utc(iso_value: str) -> datetime | None:
    text = _safe_str(iso_value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def normalize_iso_date(value: str) -> str:
    """Return 'YYYY-MM-DD' if value is a real calendar date, else ''."""
    text = _safe_str(value).strip()
    if not text or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return ""
    try:
        date.fromisoformat(text)
    except ValueError:
        return ""
    return text

def elapsed_mmss(started_at_iso: str) -> str:
    started_at = parse_iso8601_utc(started_at_iso)
    if not started_at:
        return "00:00"
    elapsed_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())
    if elapsed_seconds < 0:
        elapsed_seconds = 0
    minutes, seconds = divmod(elapsed_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"

def write_elapsed_time_json(
    qr_code: str,
    building_number: str,
    asset_type_abbrev: str,
    elapsed_time: str,
    capture_notes: str = "",
    installation_date: str = ""
):
    safe_qr = sanitize_component(qr_code, prefer_digits=True)
    payload = {
        "qr_code": safe_qr,
        "building_number": _safe_str(building_number),
        "asset_type": f"- {sanitize_component(asset_type_abbrev, prefer_digits=False)}",
        "elapsetime": _safe_str(elapsed_time) or "00:00",
        "capture_notes": _safe_str(capture_notes),
        "installation_date": _safe_str(installation_date),
    }

    os.makedirs(ELAPSE_JSON_DIR, exist_ok=True)
    target_path = os.path.join(ELAPSE_JSON_DIR, f"{safe_qr}_et.json")

    fd, tmp_path = tempfile.mkstemp(prefix=f"{safe_qr}_", suffix=".tmp", dir=ELAPSE_JSON_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, target_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

def map_asset_type_to_abbrev(t: str) -> str:
    t = (t or "").strip().lower()
    if t.startswith("mech"): return "ME"
    if t.startswith("elec"): return "EL"
    if t.startswith("back"): return "BF"
    return t[:2].upper() or "AS"

def seq_to_label(asset_type: str, seq: str) -> str:
    t = (asset_type or "").strip().lower()
    try:
        i = int(seq)
    except Exception:
        return "Photo"

    if t == "mechanical":
        mech_map = {0: "Asset Plate", 1: "UBC Tag", 2: "Main Asset Photo", 3: "Technical Safety BC", 4: "Extra Photo"}
        return mech_map.get(i, f"Photo {i}")

    if t in ("back flow", "backflow", "back-flow", "bf", "back"):
        bf_map = {0: "Asset Plate", 1: "Asset Plate (additional)", 2: "Main Photo", 3: "Extra Photo"}
        return bf_map.get(i, f"Photo {i}")

    if t == "electrical":
        el_map = {0: "Asset Plate (Optional)", 1: "UBC Asset Tag", 2: "Panel Schedule", 3: "Extra Photo"}
        return el_map.get(i, f"Photo {i}")

    other_map = {0: "Asset Plate", 1: "UBC Tag", 2: "Main Asset Photo", 3: "Extra Photo"}
    return other_map.get(i, f"Photo {i}")

# -----------------------------------------------------------------------------
# Image saver  (optional Pillow with fallback)
# -----------------------------------------------------------------------------
def save_image_file(storage, dest_path: str):
    try:
        from PIL import Image, ImageOps
        import io as _io
        storage.stream.seek(0)
        data = storage.read()
        storage.stream.seek(0)
        ext = os.path.splitext(dest_path)[1].lower()
        try:
            img = Image.open(_io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            if ext in (".jpg", ".jpeg"):
                if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                img.save(dest_path, format="JPEG", quality=90, optimize=True)
            else:
                img.save(dest_path)
        except Exception:
            with open(dest_path, "wb") as f: f.write(data)
    except Exception:
        storage.save(dest_path)

# -----------------------------------------------------------------------------
# SQLite helpers
# -----------------------------------------------------------------------------
def _open_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Backend-agnostic connection (Phase C / C4): SQLite by default — identical
    # behavior to the legacy sqlite3.connect(DB_PATH) — or PostgreSQL when the
    # env DB_BACKEND=postgres is set. See db.py (shared DB layer pattern-setter).
    import db
    return db.get_connection(sqlite_path=DB_PATH)

def _table_columns(conn, table: str) -> List[str]:
    # Backend-agnostic (PRAGMA on SQLite / information_schema on PostgreSQL). See db.py.
    import db
    return db.table_columns(conn, table)

def _has_table(conn, table: str) -> bool:
    import db
    return db.has_table(conn, table)

def _quote_ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'

def _list_tables(conn) -> List[str]:
    import db
    return db.list_tables(conn)

def _find_assets_table(conn: sqlite3.Connection) -> str | None:
    names = _list_tables(conn)
    for candidate in ["QR_code_assets", "QR_codes_assets"]:
        for n in names:
            if n.lower() == candidate.lower():
                return n
    for n in names:
        l = n.lower()
        if "qr" in l and "code" in l and "asset" in l:
            return n
    return None

def _load_locations_from_sqlite(building_code: str | None = None) -> List[str]:
    if not os.path.exists(DB_PATH):
        app.logger.error(f"DB file does not exist at {DB_PATH}")
        return []
    conn = None
    try:
        conn = _open_db()
        if building_code:
            sql = 'SELECT DISTINCT "Location" FROM "Buildings_with_SpaceUID" WHERE "Code" = ? ORDER BY "Location"'
            cur = conn.execute(sql, (building_code,))
        else:
            sql = 'SELECT DISTINCT "Location" FROM "Buildings_with_SpaceUID" ORDER BY "Location"'
            cur = conn.execute(sql)

        locations = [row[0] for row in cur.fetchall() if row[0]]
        return locations
    except Exception as e:
        app.logger.error(f"Error loading locations: {e}", exc_info=True)
        return []
    finally:
        try:
            if conn: conn.close()
        except Exception:
            pass

def _load_buildings_from_sqlite() -> List[Dict[str, str]]:
    import db
    if not db.is_postgres() and not os.path.exists(DB_PATH):
        return []
    conn = None
    try:
        conn = _open_db()
        tables = db.list_tables(conn)
        candidates = [t for t in tables if t.lower() == "buildings"] or \
                     [t for t in tables if "building" in t.lower()]
        if not candidates:
            return []
        table = candidates[0]
        cols = _table_columns(conn, table)

        code_keys = ["Code", "Building Code", "Bldg Code", "Bldg_Num", "Bldg Number", "Property", "Bldg"]
        name_keys = ["Name", "Building Name", "Building_Name", "Description", "Building"]

        code_col = next((c for c in cols for ck in code_keys
                         if c.lower().replace("_"," ") == ck.lower().replace("_"," ")), None)
        name_col = next((c for c in cols for nk in name_keys
                         if c.lower().replace("_"," ") == nk.lower().replace("_"," ")), None)

        items: List[Dict[str,str]] = []
        if code_col:
            if not name_col: name_col = code_col
            cur = conn.execute(f'SELECT {_quote_ident(code_col)}, {_quote_ident(name_col)} FROM "{table}"')
            for code, name in cur.fetchall():
                c = _safe_str(code); n = _safe_str(name)
                if c: items.append({"code": c, "name": n or c})
        else:
            cur = conn.execute(f'SELECT * FROM "{table}"')
            rows = cur.fetchall(); cols = _table_columns(conn, table)
            for r in rows:
                row = dict(zip(cols, r))
                code_guess = ""; name_guess = ""
                for v in row.values():
                    vs = _safe_str(v)
                    if vs.isdigit(): code_guess = vs; break
                for v in row.values():
                    vs = _safe_str(v)
                    if vs and not vs.isdigit(): name_guess = vs; break
                if code_guess:
                    items.append({"code": code_guess, "name": name_guess or code_guess})

        seen = set(); out = []
        for it in sorted(items, key=lambda d: d["name"].upper()):
            if it["code"] not in seen:
                seen.add(it["code"]); out.append(it)
        return out
    except Exception:
        return []
    finally:
        try:
            if conn: conn.close()
        except Exception:
            pass

def get_building_options() -> List[Dict[str, str]]:
    opts = _load_buildings_from_sqlite()
    if opts:
        return opts
    return [
        {"code": "314-1", "name": "Building 314-1"},
        {"code": "482",   "name": "Abdul Ladha Science Student Centre 482"},
        {"code": "775",   "name": "Recreation Centre North"},
    ]

def _snapshot_qr_row(conn: sqlite3.Connection, qr_code: str) -> dict:
    """Return the QR_codes row for `qr_code` as a dict (empty dict if absent)."""
    if not _has_table(conn, "QR_codes"):
        return {}
    cur = conn.execute('SELECT * FROM "QR_codes" WHERE "QR_code_ID"=?', (qr_code,))
    row = cur.fetchone()
    if not row:
        return {}
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _snapshot_asset_rows(conn: sqlite3.Connection, qr_code: str) -> list[dict]:
    """Return all QR_code_assets rows whose code_assets matches the QR prefix."""
    table = _find_assets_table(conn)
    if not table or "code_assets" not in _table_columns(conn, table):
        return []
    cur = conn.execute(
        f'SELECT * FROM "{table}" WHERE "code_assets" LIKE ?',
        (qr_code + " %",),
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def _lookup_building_centroid(conn, building_code: str) -> tuple[str, str]:
    """Return the (lat, lng) centroid for a building code from the property GPS
    reference table, or ("", "") if unavailable.

    Used as a no-prompt fallback when the device did not provide a precise GPS
    fix (permission denied / unavailable). Read-only, best-effort — any error or
    missing table/row yields ("", "") so it can never break /submit.
    """
    bc = _safe_str(building_code)
    if not bc:
        return "", ""
    table = "UBC - All Properties List with GPS Coordinates"
    try:
        if not _has_table(conn, table):
            return "", ""
        cur = conn.execute(
            f'SELECT "Latitude", "Longitude" FROM "{table}" WHERE "Code"=? LIMIT 1',
            (bc,),
        )
        row = cur.fetchone()
    except Exception:
        return "", ""
    if not row:
        return "", ""
    return normalize_lat_lng(row[0], row[1])


def upsert_qr_codes(
    conn: sqlite3.Connection,
    qr_code: str,
    building_code: str,
    location: str,
    asset_type: str | None = None,
    elapsed_time: str | None = None,
    latitude: str | None = None,
    longitude: str | None = None,
    coord_source: str | None = None,
    capture_notes: str | None = None,
    installation_date: str | None = None
):
    table = "QR_codes"
    if not _has_table(conn, table):
        return
    cols = set(_table_columns(conn, table))
    if "QR_code_ID" not in cols:
        return

    asset_type = asset_type or ""
    elapsed_time = _safe_str(elapsed_time)
    latitude = _safe_str(latitude)
    longitude = _safe_str(longitude)
    coord_source = _safe_str(coord_source)
    capture_notes = _safe_str(capture_notes).strip()[:200]
    installation_date = _safe_str(installation_date).strip()

    bcode_candidates = ["Building Code", "Building_Code", "Code", "Property", "Bldg Code", "Bldg", "Building"]
    asset_type_candidates = ["asset_type", "Asset_Type", "Asset type"]
    elapsed_time_candidates = ["elapsetime", "elapsed_time", "Elapsed Time"]

    # No DDL in the request path: ALTER TABLE needs table ownership on PG
    # (app role 'assetcap_app' has DML grants only) and a failed statement
    # aborts the whole PG transaction even when the exception is swallowed.
    # Missing columns are skipped; schema changes are owner-run migrations.
    bcode_col = next((c for c in bcode_candidates if c in cols), None)
    
    loc_col = "Location" if "Location" in cols else None
    ai_status_col = "ai_status" if "ai_status" in cols else None
    asset_type_col = next((c for c in asset_type_candidates if c in cols), None)
    elapsed_time_col = next((c for c in elapsed_time_candidates if c in cols), None)
    lat_col = "capture_latitude" if "capture_latitude" in cols else None
    lng_col = "capture_longitude" if "capture_longitude" in cols else None
    src_col = "capture_coord_source" if "capture_coord_source" in cols else None
    gps_col = "GPS Coordinates (lat,long)" if "GPS Coordinates (lat,long)" in cols else None
    notes_col = "capture_notes" if "capture_notes" in cols else None
    install_col = "installation_date" if "installation_date" in cols else None
    gps_coordinates = format_gps_coordinates(latitude, longitude)
    # Coordinates are written only as a complete pair (and only when both columns
    # exist) so a half-applied migration can never persist a lone lat/lng.
    coords_ok = bool(lat_col and lng_col and gps_coordinates)

    cur = conn.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE "QR_code_ID"=?',
        (qr_code,)
    )
    exists = cur.fetchone()[0] > 0
    
    if exists:
        updates = []
        params = []
        if ai_status_col:
            updates.append(f'{_quote_ident(ai_status_col)}=?')
            params.append('0')  # ai_status is TEXT on PG; pass '0' not int 0
        if bcode_col:
            updates.append(f'{_quote_ident(bcode_col)}=?')
            params.append(building_code)
        if loc_col:
            updates.append(f'{_quote_ident(loc_col)}=?')
            params.append(location)
        if asset_type_col and asset_type:
            updates.append(f'{_quote_ident(asset_type_col)}=?')
            params.append(map_asset_type_to_abbrev(asset_type))
        if elapsed_time_col and elapsed_time:
            updates.append(f'{_quote_ident(elapsed_time_col)}=?')
            params.append(elapsed_time)
        # Notes / installation date: latest non-empty submission wins; an empty
        # resubmission never erases a previously entered value (Invariant #6).
        # Clearing a value is a review-layer job, not a capture-app job.
        if notes_col and capture_notes:
            updates.append(f'{_quote_ident(notes_col)}=?')
            params.append(capture_notes)
        if install_col and installation_date:
            updates.append(f'{_quote_ident(install_col)}=?')
            params.append(installation_date)
        # Write coords only when present so a later GPS-denied re-submit never
        # erases a good earlier fix (High-Risk Invariant #6).
        if coords_ok:
            updates.append(f'{_quote_ident(lat_col)}=?')
            params.append(latitude)
            updates.append(f'{_quote_ident(lng_col)}=?')
            params.append(longitude)
            if src_col and coord_source:
                updates.append(f'{_quote_ident(src_col)}=?')
                params.append(coord_source)
            if gps_col:
                updates.append(f'{_quote_ident(gps_col)}=?')
                params.append(gps_coordinates)

        if updates:
            sql = f'UPDATE "{table}" SET {", ".join(updates)} WHERE "QR_code_ID"=?'
            params.append(qr_code)
            conn.execute(sql, tuple(params))
    else:
        columns = ['"QR_code_ID"']  # quote: PG folds unquoted QR_code_ID -> qr_code_id (UndefinedColumn)
        placeholders = ["?"]
        params = [qr_code]
        if bcode_col:
            columns.append(_quote_ident(bcode_col))
            placeholders.append("?")
            params.append(building_code)
        if loc_col:
            columns.append(_quote_ident(loc_col))
            placeholders.append("?")
            params.append(location)
        if asset_type_col and asset_type:
            columns.append(_quote_ident(asset_type_col))
            placeholders.append("?")
            params.append(map_asset_type_to_abbrev(asset_type))
        if elapsed_time_col and elapsed_time:
            columns.append(_quote_ident(elapsed_time_col))
            placeholders.append("?")
            params.append(elapsed_time)
        if notes_col and capture_notes:
            columns.append(_quote_ident(notes_col))
            placeholders.append("?")
            params.append(capture_notes)
        if install_col and installation_date:
            columns.append(_quote_ident(install_col))
            placeholders.append("?")
            params.append(installation_date)
        if coords_ok:
            columns.append(_quote_ident(lat_col))
            placeholders.append("?")
            params.append(latitude)
            columns.append(_quote_ident(lng_col))
            placeholders.append("?")
            params.append(longitude)
            if src_col and coord_source:
                columns.append(_quote_ident(src_col))
                placeholders.append("?")
                params.append(coord_source)
            if gps_col:
                columns.append(_quote_ident(gps_col))
                placeholders.append("?")
                params.append(gps_coordinates)

        sql = f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({", ".join(placeholders)})'
        conn.execute(sql, tuple(params))


def insert_into_assets(conn: sqlite3.Connection, file_bases: List[str], username: str = ""):
    import db
    table = _find_assets_table(conn)
    if not table:
        print("[assets] No QR_*code*_assets table found; skipping inserts.")
        return
    cols = set(_table_columns(conn, table))
    if "code_assets" not in cols:
        print(f"[assets] Table '{table}' lacks 'code_assets'; skipping inserts.")
        return
    
    # No DDL in the request path: CREATE INDEX / ALTER TABLE require table
    # ownership on PG ('assetcap_app' has DML grants only) and the failed
    # statement aborted the whole /submit transaction — every mobile capture
    # since the 2026-06-08 cutover lost its DB rows (incident 2026-06-11).
    # The dedupe semantics of ON CONFLICT DO NOTHING / INSERT OR IGNORE rely
    # on the unique index ux_QR_code_assets_code_assets, created once as the
    # table owner via scripts/migrations/2026-06-11_ux_qr_code_assets.sql.

    # Get current timestamp in ISO 8601 format
    current_timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    
    # Build column list and values based on available columns
    insert_cols = ['"code_assets"']
    value_placeholders = ['?']
    
    if "api_int" in cols:
        insert_cols.append('"api_int"')
        value_placeholders.append('0')
    if "user" in cols and username:
        insert_cols.append('"user"')
        value_placeholders.append('?')
    if "date_hour" in cols:
        insert_cols.append('"date_hour"')
        value_placeholders.append('?')
    
    if db.is_postgres():
        # PostgreSQL has no "INSERT OR IGNORE"; ON CONFLICT DO NOTHING (no target)
        # ignores any unique/PK violation — matches the SQLite ignore-dup semantics.
        sql = f'INSERT INTO "{table}" ({', '.join(insert_cols)}) VALUES ({', '.join(value_placeholders)}) ON CONFLICT DO NOTHING'
    else:
        sql = f'INSERT OR IGNORE INTO "{table}" ({', '.join(insert_cols)}) VALUES ({', '.join(value_placeholders)})'
    
    # Build parameter tuples for each file base
    params_list = []
    for b in file_bases:
        if not _is_valid_asset_base(b):
            print(f"[assets] Skipping invalid code_assets value: {b!r}")
            continue
        params = [b]
        if "user" in cols and username:
            params.append(username)
        if "date_hour" in cols:
            params.append(current_timestamp)
        params_list.append(tuple(params))
    
    if params_list:
        conn.executemany(sql, params_list)

def delete_from_assets_by_qr(conn: sqlite3.Connection, qr_code: str):
    table = _find_assets_table(conn)
    if not table: return
    cols = set(_table_columns(conn, table))
    if "code_assets" not in cols: return
    conn.execute(f'DELETE FROM "{table}" WHERE "code_assets" LIKE ?', (qr_code + " %",))

def delete_files_by_qr(qr_code: str):
    prefix = f"{qr_code} "
    if not os.path.isdir(UPLOAD_DIR): return
    for fname in os.listdir(UPLOAD_DIR):
        if fname.startswith(prefix):
            try: os.remove(os.path.join(UPLOAD_DIR, fname))
            except Exception: pass

def qr_exists(conn: sqlite3.Connection, qr_code: str) -> bool:
    if not _has_table(conn, "QR_codes"): return False
    cols = set(_table_columns(conn, "QR_codes"))
    if "QR_code_ID" not in cols: return False
    cur = conn.execute('SELECT 1 FROM "QR_codes" WHERE "QR_code_ID"=? LIMIT 1', (qr_code,))
    return cur.fetchone() is not None

ALLOWED_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")

def list_existing_uploads(qr_code: str, building_code: str, asset_type: str) -> List[Dict[str, str]]:
    safe_qr   = sanitize_component(qr_code, prefer_digits=True)
    safe_bldg = sanitize_component(building_code, prefer_digits=False)
    safe_type = sanitize_component(map_asset_type_to_abbrev(asset_type), prefer_digits=False)
    prefix = f"{safe_qr} {safe_bldg} {safe_type} - "

    out = []
    if not os.path.isdir(UPLOAD_DIR):
        return out
    for fname in os.listdir(UPLOAD_DIR):
        if not fname.lower().endswith(ALLOWED_EXTS): continue
        if not fname.startswith(prefix): continue
        base, _ = os.path.splitext(fname)
        m = re.search(r'\s-\s(\d+)$', base)
        seq = m.group(1) if m else ""
        out.append({
            "filename": fname,
            "base": base,
            "url": url_for("uploaded_file", filename=fname),
            "seq": seq,
            "label": seq_to_label(asset_type, seq),
        })
    out.sort(key=lambda x: int(x["seq"]) if x["seq"].isdigit() else 9999)
    return out

def get_next_temp_code(conn: sqlite3.Connection) -> str | None:
    if not _has_table(conn, "temp_code"):
        return None
    
    # Find the first available code
    cur = conn.execute('SELECT "temp_code" FROM "temp_code" WHERE "status" = \'0\' ORDER BY "ID" ASC LIMIT 1')
    row = cur.fetchone()
    if not row:
        return None
    
    code = row[0]
    
    # Note: Status update deferred to submission
    
    return code

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET"])
@login_required
def start():
    if not has_permission(current_user, "application", "asset_capture_mobile", "viewer"):
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Access Denied</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body { font-family: sans-serif; text-align: center; padding: 2rem; background: #f8f9fa; color: #333; }
                h2 { color: #dc3545; }
                a.btn { display: inline-block; margin-top: 1rem; padding: 0.5rem 1rem; background-color: #0b2265; color: white; text-decoration: none; border-radius: 4px; }
            </style>
        </head>
        <body>
            <h2>Access Denied</h2>
            <p>You do not have permission to access the Asset Capture Mobile App.</p>
            <p>Please contact your administrator if you need access.</p>
            <a href="/auth/logout" class="btn">Log Out</a>
        </body>
        </html>
        """, 403
        
    building_code = request.args.get("building_code", "")
    location = request.args.get("location", "")
    asset_type = request.args.get("asset_type", "Mechanical")
    capture_process = MOBILE_CAPTURE_PROCESS

    building_options = get_building_options()
    location_options = _load_locations_from_sqlite(building_code=building_code or None)

    return render_template(
        "start.html",
        building_options=building_options,
        location_options=location_options,
        building_code=building_code,
        location=location,
        asset_type=asset_type,
        capture_process=capture_process,
        username=current_user.username
    )

@app.route("/api/locations")
@login_required
@require_permission("application", "asset_capture_mobile", "viewer")
def api_locations():
    building_code = request.args.get("building_code")
    if not building_code:
        return jsonify([])
    try:
        locations = _load_locations_from_sqlite(building_code=building_code)
        return jsonify(locations)
    except Exception as e:
        app.logger.error(f"API error fetching locations for building '{building_code}': {e}", exc_info=True)
        return jsonify({"error": "Could not retrieve locations"}), 500


@app.route("/api/check-qr", methods=["GET"])
@login_required
@require_permission("application", "asset_capture_mobile", "viewer")
def api_check_qr():
    raw = _safe_str(request.args.get("qr"))
    qr = sanitize_component(raw, prefer_digits=True)
    try:
        conn = _open_db()
        exists = qr_exists(conn, qr)
        
        # If exists, also return current parameters for comparison
        current_params = None
        current_asset_type = None
        if exists:
            current_params = get_current_params(conn, qr, UPLOAD_DIR)
            current_asset_type = get_current_asset_type(UPLOAD_DIR, qr) or (current_params or {}).get("asset_type")
        
        response = {
            "exists": bool(exists),
            "qr": qr
        }
        
        if current_params:
            response["current_building"] = current_params.get("building_code", "")
            response["current_location"] = current_params.get("location", "")
            if not current_asset_type:
                current_asset_type = current_params.get("asset_type")
        if current_asset_type:
            response["current_asset_type"] = current_asset_type
        
        return jsonify(response)
    except Exception as e:
        app.logger.error(f"QR existence check failed for '{qr}': {e}", exc_info=True)
        return jsonify({"exists": False, "error": str(e)}), 500
    finally:
        try: conn.close()
        except Exception: pass


@app.route("/api/update-parameters", methods=["POST"])
@login_required
@require_permission("application", "asset_capture_mobile", "editor")
def api_update_parameters():
    """
    Update parameters (Building, Location, Asset Type) for an existing QR code.
    Handles both file renaming and database updates atomically.
    """
    data = request.get_json(silent=True) or {}
    
    qr_code = _safe_str(data.get("qr_code"))
    if not qr_code:
        return jsonify({"success": False, "error": "Missing qr_code"}), 400
    
    old_params = data.get("old_params", {})
    new_params = data.get("new_params", {})
    
    if not old_params or not new_params:
        return jsonify({"success": False, "error": "Missing old_params or new_params"}), 400
    
    audit_conn = None
    try:
        audit_conn = _open_db()
        before_qr = _snapshot_qr_row(audit_conn, qr_code)
        audit_conn.close()
        audit_conn = None

        success, message = execute_parameter_update(
            db_path=DB_PATH,
            upload_dir=UPLOAD_DIR,
            qr_code=qr_code,
            old_params=old_params,
            new_params=new_params
        )

        if success:
            try:
                audit_conn = _open_db()
                after_qr = _snapshot_qr_row(audit_conn, qr_code)
                changes = diff_dicts(before_qr, after_qr)
                if changes:
                    log_change(
                        audit_conn,
                        qr_code=qr_code,
                        app_name="mobile",
                        table_name="QR_codes",
                        record_pk=qr_code,
                        op_type="UPDATE",
                        field_changes=changes,
                        source="human",
                        description="POST /api/update-parameters",
                    )
                    audit_conn.commit()
            except Exception as audit_exc:
                app.logger.warning(
                    "audit logging failed for /api/update-parameters: %s", audit_exc
                )
            finally:
                try:
                    if audit_conn:
                        audit_conn.close()
                except Exception:
                    pass
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "error": message}), 500

    except Exception as e:
        app.logger.error(f"Error updating parameters: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try:
            if audit_conn:
                audit_conn.close()
        except Exception:
            pass

@app.route("/api/get-temp-code", methods=["POST"])
@login_required
@require_permission("application", "asset_capture_mobile", "editor")
def api_get_temp_code():
    try:
        conn = _open_db()
        code = get_next_temp_code(conn)
        if code:
            return jsonify({"code": code})
        else:
            return jsonify({"error": "No temp codes available"}), 404
    except Exception as e:
        app.logger.error(f"Error fetching temp code: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        try: conn.close()
        except Exception: pass

@app.route("/capture", methods=["GET", "POST"])
@login_required
@require_permission("application", "asset_capture_mobile", "editor")
def capture():
    if request.method == "POST":
        qr_code = _safe_str(request.form.get("qr_code"))
        building_code = _safe_str(request.form.get("building_code"))
        location = _safe_str(request.form.get("location"))
        asset_type = _safe_str(request.form.get("asset_type") or "Mechanical")
        overwrite = _safe_str(request.form.get("overwrite") or "0")
        stopwatch_start_utc = _safe_str(request.form.get("stopwatch_start_utc"))
    else:
        qr_code = _safe_str(request.args.get("qr_code"))
        building_code = _safe_str(request.args.get("building_code"))
        location = _safe_str(request.args.get("location"))
        asset_type = _safe_str(request.args.get("asset_type") or "Mechanical")
        overwrite = _safe_str(request.args.get("overwrite") or "0")
        stopwatch_start_utc = _safe_str(request.args.get("stopwatch_start_utc"))

    capture_process = MOBILE_CAPTURE_PROCESS

    errors = _capture_validation_errors(qr_code, building_code, location, asset_type)

    if errors:
        for e in errors: flash(e, "warning")
        building_options = get_building_options()
        location_options = _load_locations_from_sqlite(building_code=building_code or None)
        return render_template(
            "start.html",
            building_options=building_options,
            location_options=location_options,
            building_code=building_code,
            location=location,
            asset_type=asset_type,
            capture_process=capture_process,
            username=current_user.username
        ), 400

    if request.method == "POST":
        return redirect(url_for(
            "capture",
            qr_code=qr_code,
            building_code=building_code,
            location=location,
            asset_type=asset_type,
            overwrite=overwrite,
            stopwatch_start_utc=stopwatch_start_utc
        ))

    existing_files = list_existing_uploads(qr_code, building_code, asset_type)

    return render_template(
        "capture.html",
        qr_code=qr_code,
        building_code=building_code,
        location=location,
        asset_type=asset_type,
        existing_files=existing_files,
        overwrite=overwrite,
        capture_process=capture_process,
        stopwatch_start_utc=stopwatch_start_utc,
        username=current_user.username
    )

@app.route("/submit", methods=["POST"])
@login_required
@require_permission("application", "asset_capture_mobile", "editor")
def submit():
    qr_code = _safe_str(request.form.get("qr_code"))
    building_code = _safe_str(request.form.get("building_code"))
    location = _safe_str(request.form.get("location"))
    asset_type = _safe_str(request.form.get("asset_type") or "Mechanical")
    overwrite = _safe_str(request.form.get("overwrite") or "0")
    capture_process = MOBILE_CAPTURE_PROCESS
    stopwatch_start_utc = _safe_str(request.form.get("stopwatch_start_utc"))
    type_abbrev = map_asset_type_to_abbrev(asset_type)
    elapsed_time = elapsed_mmss(stopwatch_start_utc)
    # Best-effort device GPS from the capture form. Validation + the building-
    # centroid fallback are resolved later, inside the DB transaction (where the
    # connection and the existing row snapshot are available).
    device_latitude = request.form.get("latitude")
    device_longitude = request.form.get("longitude")
    # Optional capture details. Server-side clamp/validation is authoritative:
    # both fields are best-effort metadata and never block the save.
    capture_notes = _safe_str(request.form.get("capture_notes")).strip()[:200]
    installation_date = normalize_iso_date(request.form.get("installation_date"))
    if request.form.get("installation_date") and not installation_date:
        app.logger.warning(
            "Ignoring invalid installation_date %r for QR %s",
            request.form.get("installation_date"), qr_code
        )

    errors = _capture_validation_errors(qr_code, building_code, location, asset_type)
    if errors:
        for error in errors:
            flash(error, "warning")
        return redirect(url_for("start"))

    if not _has_primary_capture_photo(request.files):
        flash("Take at least one of the first two photos before saving.", "warning")
        return redirect(url_for(
            "capture",
            qr_code=qr_code,
            building_code=building_code,
            location=location,
            asset_type=asset_type,
            overwrite=overwrite,
            stopwatch_start_utc=stopwatch_start_utc,
        ))

    safe_qr   = sanitize_component(qr_code, prefer_digits=True)
    safe_bldg = sanitize_component(building_code, prefer_digits=False)
    safe_type = sanitize_component(type_abbrev, prefer_digits=False)

    if overwrite == "1":
        try:
            conn = _open_db()
            removed_assets = _snapshot_asset_rows(conn, safe_qr)
            delete_from_assets_by_qr(conn, safe_qr)
            for row in removed_assets:
                log_change(
                    conn,
                    qr_code=safe_qr,
                    app_name="mobile",
                    table_name=_find_assets_table(conn) or "QR_code_assets",
                    record_pk=row.get("ID") or row.get("code_assets"),
                    op_type="DELETE",
                    field_changes={k: (v, None) for k, v in row.items() if v not in (None, "")},
                    source="human",
                    description="POST /submit (overwrite=1)",
                )
            conn.commit()
        except Exception as e:
            app.logger.error(f"/submit overwrite cleanup failed for QR '{safe_qr}': {e}", exc_info=True)
            flash(f"Warning: could not clear prior DB rows for this QR ({e}).", "warning")
        finally:
            try: conn.close()
            except Exception: pass
        delete_files_by_qr(safe_qr)

    files_saved: List[str] = []
    bases_saved: List[str] = []
    db_write_ok = False

    for seq in ("0", "1", "2", "3", "4"):
        file_key = f"image_{seq}"
        file = request.files.get(file_key)
        if not file or file.filename == "":
            continue
        orig = secure_filename(file.filename)
        _, ext = os.path.splitext(orig)
        ext = (ext or ".jpg").lower()
        if ext not in ALLOWED_EXTS:
            ext = ".jpg"

        base = f"{safe_qr} {safe_bldg} {safe_type} - {seq}"
        fname = base + ext

        dest = os.path.join(UPLOAD_DIR, fname)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        save_image_file(file, dest)

        bases_saved.append(base)
        files_saved.append(fname)

    try:
        conn = _open_db()
        before_qr = _snapshot_qr_row(conn, safe_qr)
        # Resolve coordinates: precise device GPS wins; otherwise fall back to the
        # building centroid (no prompt). Never downgrade an existing device fix.
        b_lat, b_lng = _lookup_building_centroid(conn, building_code)
        latitude, longitude, coord_source = resolve_capture_coordinates(
            device_latitude, device_longitude, b_lat, b_lng,
            existing_source=_safe_str(before_qr.get("capture_coord_source")),
            existing_lat=_safe_str(before_qr.get("capture_latitude")),
        )
        upsert_qr_codes(
            conn,
            qr_code=safe_qr,
            building_code=building_code,
            location=location,
            asset_type=asset_type,
            elapsed_time=elapsed_time,
            latitude=latitude,
            longitude=longitude,
            coord_source=coord_source,
            capture_notes=capture_notes,
            installation_date=installation_date,
        )
        after_qr = _snapshot_qr_row(conn, safe_qr)
        qr_changes = diff_dicts(before_qr, after_qr)
        if qr_changes:
            log_change(
                conn,
                qr_code=safe_qr,
                app_name="mobile",
                table_name="QR_codes",
                record_pk=safe_qr,
                op_type="INSERT" if not before_qr else "UPDATE",
                field_changes=qr_changes,
                source="human",
                description="POST /submit",
            )

        if bases_saved:
            insert_into_assets(conn, bases_saved, current_user.username)
            for base in bases_saved:
                log_change(
                    conn,
                    qr_code=safe_qr,
                    app_name="mobile",
                    table_name=_find_assets_table(conn) or "QR_code_assets",
                    record_pk=base,
                    op_type="INSERT",
                    field_changes={"code_assets": (None, base)},
                    source="human",
                    description="POST /submit (asset capture)",
                )

        update_asset_process(conn, qr_code=safe_qr, process_status=capture_process)

        conn.commit()
        db_write_ok = True
    except Exception as e:
        app.logger.error(
            f"/submit DB registration failed for QR '{safe_qr}' "
            f"(photos already saved: {files_saved}): {e}",
            exc_info=True,
        )
        flash(
            f"Database registration FAILED for QR {safe_qr} — this capture was "
            f"NOT saved ({e}). Photos were stored on the server; please submit "
            f"again or report this QR.",
            "danger",
        )
    finally:
        try: conn.close()
        except Exception: pass

    try:
        write_elapsed_time_json(
            qr_code=safe_qr,
            building_number=building_code,
            asset_type_abbrev=type_abbrev,
            elapsed_time=elapsed_time,
            capture_notes=capture_notes,
            installation_date=installation_date,
        )
    except Exception as e:
        flash(f"Warning: could not write elapsed-time JSON ({e}).", "warning")

    if not db_write_ok:
        # Photos and elapsed-time JSON are on disk for recovery, but the QR has
        # no DB rows — showing the success page here is what hid this failure
        # class for 3 days (incident 2026-06-11).
        return redirect(url_for("start"))

    mode = get_after_submit_mode(request.form.get("after_submit"))
    session["last_submit_success"] = {
        "qr_code": safe_qr,
        "building_code": building_code,
        "location": location,
        "asset_type": asset_type,
        "elapsetime": elapsed_time,
        "files_saved": files_saved,
    }
    if mode == "capture":
        return redirect(url_for("capture",
                                qr_code=qr_code,
                                building_code=building_code,
                                location=location,
                                asset_type=asset_type,
                                overwrite=overwrite))
    return redirect(url_for("submit_success"))


@app.route("/submit/success", methods=["GET"])
@login_required
@require_permission("application", "asset_capture_mobile", "viewer")
def submit_success():
    payload = session.get("last_submit_success")
    if not payload:
        flash("No recent submission found. Please start a new upload.", "info")
        return redirect(url_for("start"))
    return render_template(
        "success.html",
        qr_code=payload.get("qr_code", ""),
        building_code=payload.get("building_code", ""),
        location=payload.get("location", ""),
        asset_type=payload.get("asset_type", ""),
        elapsetime=payload.get("elapsetime", "00:00"),
        files_saved=payload.get("files_saved", []),
        username=current_user.username
    )

def update_asset_process(conn: sqlite3.Connection, qr_code: str, process_status: str):
    table = _find_assets_table(conn)
    if not table:
        print("[assets] No QR_*code*_assets table found; skipping process update.")
        return
    cols = set(_table_columns(conn, table))
    if "code_assets" not in cols or "Col_process" not in cols:
        print(f"[assets] Table '{table}' lacks 'code_assets' or 'Col_process'; skipping process update.")
        return

    # Assuming `code_assets` is the column that stores the QR code identifier
    # And that the QR code might be part of a longer string in `code_assets`
    # We will update all rows that start with the given qr_code
    sql = f'UPDATE "{table}" SET "Col_process" = ? WHERE "code_assets" LIKE ?'
    
    # The LIKE pattern ensures we update all assets related to this QR code
    like_pattern = qr_code + '%' 
    
    try:
        conn.execute(sql, (process_status, like_pattern))
        print(f"[assets] Updated 'Col_process' to {process_status} for QR code starting with {qr_code}")
    except Exception as e:
        print(f"Error updating 'Col_process': {e}")

@app.route("/delete-upload", methods=["POST"])
@login_required
@require_permission("application", "asset_capture_mobile", "editor")
def delete_upload():
    data = request.get_json(silent=True) or {}
    qr_code = _safe_str(data.get("qr_code"))
    building_code = _safe_str(data.get("building_code"))
    asset_type = _safe_str(data.get("asset_type"))
    filename = os.path.basename(_safe_str(data.get("filename")))

    if not (qr_code and building_code and asset_type and filename):
        return jsonify(ok=False, error="missing parameters"), 400

    safe_qr   = sanitize_component(qr_code, prefer_digits=True)
    safe_bldg = sanitize_component(building_code, prefer_digits=False)
    safe_type = sanitize_component(map_asset_type_to_abbrev(asset_type), prefer_digits=False)
    expected_prefix = f"{safe_qr} {safe_bldg} {safe_type} - "

    if not filename.startswith(expected_prefix):
        return jsonify(ok=False, error="filename not in this context"), 400

    dest = os.path.join(UPLOAD_DIR, filename)
    base, _ = os.path.splitext(filename)

    if os.path.exists(dest):
        try:
            os.remove(dest)
        except Exception as e:
            return jsonify(ok=False, error=f"cannot delete file: {e}"), 500

    try:
        conn = _open_db()
        table = _find_assets_table(conn)
        if table and "code_assets" in _table_columns(conn, table):
            cur = conn.execute(f'SELECT * FROM "{table}" WHERE "code_assets"=?', (base,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description] if row else []
            before_row = dict(zip(cols, row)) if row else {}
            conn.execute(f'DELETE FROM "{table}" WHERE "code_assets"=?', (base,))
            if before_row:
                log_change(
                    conn,
                    qr_code=safe_qr,
                    app_name="mobile",
                    table_name=table,
                    record_pk=before_row.get("ID") or base,
                    op_type="DELETE",
                    field_changes={k: (v, None) for k, v in before_row.items() if v not in (None, "")},
                    source="human",
                    description="POST /delete-upload",
                )
            conn.commit()
    except Exception as e:
        return jsonify(ok=False, error=f"db error: {e}"), 500
    finally:
        try: conn.close()
        except Exception: pass

    return jsonify(ok=True)

@app.route("/uploads/<path:filename>")
@login_required
@require_permission("application", "asset_capture_mobile", "viewer")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

with app.app_context():
    db.create_all()
    ensure_user_access_table()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    print("🚀 Flask app running...")
    print(f"🔗 Open your browser and go to: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
