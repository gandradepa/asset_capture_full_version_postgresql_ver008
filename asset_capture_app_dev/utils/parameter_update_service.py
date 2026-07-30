# Parameter Update Service
# Handles atomic updates when users change Building, Location, or Asset Type
# during repeat photo capture

import os
import re
import shutil
import sqlite3
import tempfile
import json
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any, Optional

import db  # backend-agnostic DB layer; production runs PostgreSQL, tests may use SQLite.


# Paths (can be overridden via environment)
JSON_OUTPUT_DIR = os.environ.get("JSON_OUTPUT_DIR", "/home/developer/Output_jason_api")
DISCIPLINE_TYPES = {"ME", "EL", "BF"}
JSON_NAME_RE = re.compile(r"^([A-Za-z0-9]+)_([A-Za-z]+)_(.+)\.json$")
PROCESSED_JSON_LOGS = {
    "ME": "processed_json.log",
    "EL": "processed_json_el.log",
    "BF": "processed_json_bf.log",
}
PROCESSED_IMAGE_LOGS = {
    "ME": "processed_images.log",
    "EL": "processed_images_el.log",
    "BF": "processed_images_bf.log",
}


# Asset type abbreviation mapping (same as app.py)
def map_asset_type_to_abbrev(t: str) -> str:
    """Convert asset type to abbreviation."""
    t = (t or "").strip().lower()
    if t.startswith("mech"): return "ME"
    if t.startswith("elec"): return "EL"
    if t.startswith("back"): return "BF"
    return t[:2].upper() or "AS"


def sanitize_component(s: str, *, prefer_digits: bool = True, maxlen: int = 80) -> str:
    """Sanitize a filename component (same as app.py)."""
    import hashlib
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


def normalize_asset_type_label(value: str) -> str:
    """Return a normalized human-readable asset type."""
    v = (value or "").strip().lower()
    if v in ("me", "mech", "mechanical"): return "Mechanical"
    if v in ("el", "elec", "electrical"): return "Electrical"
    if v in ("bf", "backflow", "back flow", "back-flow"): return "Backflow"
    return value or ""


def parse_filename_base(base: str) -> Dict[str, Optional[str]]:
    """
    Parse a filename base of the form:
      "<qr> <building> <asset> - <seq>"
    Returns dict with qr, building, asset_type, seq (all optional).
    """
    result: Dict[str, Optional[str]] = {"qr": None, "building": None, "asset_type": None, "seq": None}
    if not base:
        return result

    head = base
    if " - " in base:
        head, seq = base.rsplit(" - ", 1)
        seq = seq.strip()
        result["seq"] = seq if seq else None

    parts = head.split()
    if not parts:
        return result

    result["qr"] = parts[0]
    if len(parts) >= 3:
        result["asset_type"] = parts[-1]
        result["building"] = " ".join(parts[1:-1])
    elif len(parts) == 2:
        result["building"] = parts[1]

    return result


def infer_params_from_files(upload_dir: str, qr_code: str) -> Dict[str, str]:
    """
    Infer building and asset type from the first matching file.
    """
    files = get_affected_files(upload_dir, qr_code)
    if not files:
        return {}

    base = os.path.splitext(os.path.basename(files[0]))[0]
    parsed = parse_filename_base(base)
    return {
        "building_code": parsed.get("building") or "",
        "asset_type": parsed.get("asset_type") or "",
    }


ALLOWED_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def detect_parameter_changes(old_params: Dict[str, str], new_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Compare old and new parameters, return a dict with:
      - changed: bool (any parameter changed)
      - building_changed: bool
      - location_changed: bool  
      - asset_type_changed: bool
      - requires_file_rename: bool (building or asset_type changed)
    """
    old_bldg = sanitize_component(old_params.get("building_code", ""), prefer_digits=False)
    new_bldg = sanitize_component(new_params.get("building_code", ""), prefer_digits=False)
    
    old_loc = old_params.get("location", "").strip()
    new_loc = new_params.get("location", "").strip()
    
    old_type = map_asset_type_to_abbrev(old_params.get("asset_type", ""))
    new_type = map_asset_type_to_abbrev(new_params.get("asset_type", ""))
    
    building_changed = old_bldg != new_bldg
    location_changed = old_loc != new_loc
    asset_type_changed = old_type != new_type
    
    return {
        "changed": building_changed or location_changed or asset_type_changed,
        "building_changed": building_changed,
        "location_changed": location_changed,
        "asset_type_changed": asset_type_changed,
        "requires_file_rename": building_changed or asset_type_changed,
        "old_building": old_bldg,
        "new_building": new_bldg,
        "old_asset_type": old_type,
        "new_asset_type": new_type,
    }


def get_affected_files(upload_dir: str, qr_code: str) -> List[str]:
    """
    Find all files for a given QR code in the upload directory.
    Returns list of absolute file paths.
    """
    safe_qr = sanitize_component(qr_code, prefer_digits=True)
    prefix = f"{safe_qr} "
    
    affected = []
    if not os.path.isdir(upload_dir):
        return affected
    
    for fname in os.listdir(upload_dir):
        if fname.startswith(prefix) and fname.lower().endswith(ALLOWED_EXTS):
            affected.append(os.path.join(upload_dir, fname))
    
    return affected


def backup_files(file_paths: List[str]) -> Optional[str]:
    """
    Create a temporary backup directory with copies of the files.
    Returns the backup directory path, or None if no files to backup.
    """
    if not file_paths:
        return None
    
    backup_dir = tempfile.mkdtemp(prefix="param_update_backup_")
    
    for fpath in file_paths:
        if os.path.exists(fpath):
            fname = os.path.basename(fpath)
            shutil.copy2(fpath, os.path.join(backup_dir, fname))
    
    return backup_dir


def compute_new_filename(old_filename: str, qr_code: str, new_building: str, new_asset_type: str) -> str:
    """
    Given an old filename, compute the new filename with updated parameters.
    
    Old: "0000177276 314-1 BF - 0.jpg"
    New: "0000177276 217 ME - 0.jpg"
    """
    safe_qr = sanitize_component(qr_code, prefer_digits=True)
    safe_bldg = sanitize_component(new_building, prefer_digits=False)
    safe_type = sanitize_component(new_asset_type, prefer_digits=False)
    
    base, ext = os.path.splitext(old_filename)
    
    # Extract the sequence number from the old filename
    # Pattern: "QRCODE BUILDING TYPE - SEQ"
    match = re.search(r'\s-\s(\d+)$', base)
    if match:
        seq = match.group(1)
    else:
        seq = "0"
    
    new_base = f"{safe_qr} {safe_bldg} {safe_type} - {seq}"
    return new_base + ext


def _atomic_write_json(path: str, payload: Any, *, indent: int = 2) -> None:
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{os.path.basename(path)}.",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_text(path: str, text: str) -> None:
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{os.path.basename(path)}.",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _review_json_parts(filename: str) -> Optional[Tuple[str, str, str]]:
    match = JSON_NAME_RE.match(filename)
    if not match:
        return None
    qr, asset_type, building = match.groups()
    asset_type = map_asset_type_to_abbrev(asset_type)
    if asset_type not in DISCIPLINE_TYPES:
        return None
    return qr, asset_type, building


def _same_qr_review_jsons(json_dir: str, safe_qr: str) -> List[str]:
    files: List[str] = []
    if not os.path.isdir(json_dir):
        return files
    for fname in os.listdir(json_dir):
        parts = _review_json_parts(fname)
        if not parts:
            continue
        qr, _asset_type, _building = parts
        if qr == safe_qr:
            files.append(os.path.join(json_dir, fname))
    return sorted(files)


def _unique_backup_path(path: str, stamp: Optional[str] = None) -> str:
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = f"{path}.bak_{stamp}_param_update"
    if not os.path.exists(candidate):
        return candidate
    i = 1
    while True:
        alt = f"{candidate}_{i}"
        if not os.path.exists(alt):
            return alt
        i += 1


def _backup_original(path: str, backup_dir: str) -> None:
    if not os.path.exists(path):
        return
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))


def _archive_active_json(path: str, backup_dir: str, stamp: str) -> str:
    """Move an active review JSON out of the root output set.

    The backup copy supports rollback. The on-disk .bak copy is intentionally
    kept after success so stale cross-discipline review JSON cannot keep
    appearing in a review app, but the original payload remains recoverable.
    """
    _backup_original(path, backup_dir)
    archived_path = _unique_backup_path(path, stamp)
    os.replace(path, archived_path)
    return archived_path


def rename_files_atomic(
    upload_dir: str,
    file_paths: List[str],
    qr_code: str,
    new_building: str,
    new_asset_type: str
) -> List[Tuple[str, str, str, str]]:
    """
    Rename files to use new parameters.
    Returns list of tuples: (old_path, new_path, old_base, new_base)
    """
    # Returns list of tuples: (old_path, new_path, old_base, new_base)
    renames: List[Tuple[str, str, str, str]] = []
    planned: List[Tuple[str, str, str, str]] = []

    for old_path in file_paths:
        old_fname = os.path.basename(old_path)
        new_fname = compute_new_filename(old_fname, qr_code, new_building, new_asset_type)
        new_path = os.path.join(upload_dir, new_fname)

        old_base = os.path.splitext(old_fname)[0]
        new_base = os.path.splitext(new_fname)[0]
        planned.append((old_path, new_path, old_base, new_base))

    # Collision detection: do not clobber existing unrelated files
    planned_sources = {p[0] for p in planned}
    for _, new_path, _, _ in planned:
        if new_path in planned_sources:
            continue
        if os.path.exists(new_path):
            raise FileExistsError(f"Target filename already exists: {new_path}")

    # Apply renames
    for old_path, new_path, old_base, new_base in planned:
        if old_path != new_path and os.path.exists(old_path):
            os.rename(old_path, new_path)
            renames.append((old_path, new_path, old_base, new_base))

    return renames


def rollback_file_changes(
    backup_dir: str,
    upload_dir: str,
    original_files: List[str],
    renames: Optional[List[Tuple[str, str, str, str]]] = None
) -> None:
    """
    Restore files from backup directory and undo any renames.
    """
    if not backup_dir or not os.path.isdir(backup_dir):
        return

    # First try to revert renames (new_path -> old_path)
    if renames:
        for old_path, new_path, _, _ in reversed(renames):
            try:
                if os.path.exists(new_path):
                    os.rename(new_path, old_path)
            except Exception:
                pass
    
    for fpath in original_files:
        fname = os.path.basename(fpath)
        backup_path = os.path.join(backup_dir, fname)
        
        if os.path.exists(backup_path):
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
                shutil.copy2(backup_path, fpath)
            except Exception:
                pass
    
    shutil.rmtree(backup_dir, ignore_errors=True)


def _quote_ident(s: str) -> str:
    """Quote a SQL identifier."""
    return '"' + s.replace('"', '""') + '"'


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Get column names for a table (backend-agnostic: PRAGMA on SQLite, information_schema on PG)."""
    return db.table_columns(conn, table)


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists (backend-agnostic)."""
    return db.has_table(conn, table)


def _find_assets_table(conn: sqlite3.Connection) -> Optional[str]:
    """Find the QR_code_assets table name."""
    names = db.list_tables(conn)
    
    for candidate in ["QR_code_assets", "QR_codes_assets"]:
        for n in names:
            if n.lower() == candidate.lower():
                return n
    
    for n in names:
        l = n.lower()
        if "qr" in l and "code" in l and "asset" in l:
            return n
    
    return None


def _lookup_dataset_params(conn: sqlite3.Connection, qr_code: str) -> Dict[str, str]:
    """
    Look up building / location / asset type from sdi_dataset or sdi_dataset_EL.
    """
    out: Dict[str, str] = {}
    for table in ["sdi_dataset", "sdi_dataset_EL"]:
        if not _has_table(conn, table):
            continue
        cols = _table_columns(conn, table)
        colset = set(cols)
        qr_col = None
        for candidate in ["QR Code", "QR_Code", "qr_code", "QRCode"]:
            if candidate in colset:
                qr_col = candidate
                break
        if not qr_col:
            continue

        cur = conn.execute(
            f'SELECT * FROM "{table}" WHERE {_quote_ident(qr_col)}=? LIMIT 1',
            (qr_code,)
        )
        row = cur.fetchone()
        if not row:
            continue

        row_dict = dict(zip(cols, row))
        building = row_dict.get("Building") or row_dict.get("building") or row_dict.get("Building_Code") or ""
        if building:
            out["building"] = str(building)
        if "Location" in colset and row_dict.get("Location"):
            out["location"] = str(row_dict.get("Location"))
        if table == "sdi_dataset_EL":
            out["asset_type"] = "EL"
        break

    return out


def update_qr_codes_table(
    conn: sqlite3.Connection,
    qr_code: str,
    new_building: str,
    new_location: str,
    new_asset_type: str = ""
) -> bool:
    """
    Update the QR_codes table with new building and location values.
    Returns True if successful.
    """
    table = "QR_codes"
    if not _has_table(conn, table):
        return False
    
    cols = set(_table_columns(conn, table))
    if "QR_code_ID" not in cols:
        return False

    # No DDL in the request path: ALTER TABLE needs table ownership on PG
    # (app role 'assetcap_app' has DML grants only) and a failed statement
    # aborts the whole PG transaction even when the exception is swallowed.
    # Missing columns are skipped; schema changes are owner-run migrations.
    bcode_candidates = ["Building Code", "Building_Code", "Code", "Property", "Bldg Code", "Bldg", "Building"]
    asset_type_candidates = ["asset_type", "Asset_Type", "Asset type"]

    bcode_col = next((c for c in bcode_candidates if c in cols), None)
    asset_type_col = next((c for c in asset_type_candidates if c in cols), None)
    loc_col = "Location" if "Location" in cols else None
    
    updates = []
    params = []
    
    if bcode_col and new_building:
        updates.append(f'{_quote_ident(bcode_col)}=?')
        params.append(new_building)
    
    if loc_col and new_location:
        updates.append(f'{_quote_ident(loc_col)}=?')
        params.append(new_location)

    if asset_type_col and new_asset_type:
        updates.append(f'{_quote_ident(asset_type_col)}=?')
        params.append(map_asset_type_to_abbrev(new_asset_type))
    
    if not updates:
        return True
    
    sql = f'UPDATE "{table}" SET {", ".join(updates)} WHERE "QR_code_ID"=?'
    params.append(qr_code)
    conn.execute(sql, tuple(params))
    
    return True


def update_assets_table(
    conn: sqlite3.Connection,
    file_renames: List[Tuple[str, str, str, str]]
) -> bool:
    """
    Update the QR_code_assets table with new filename bases.
    """
    table = _find_assets_table(conn)
    if not table:
        return True
    
    cols = set(_table_columns(conn, table))
    if "code_assets" not in cols:
        return True
    
    for old_path, new_path, old_base, new_base in file_renames:
        if old_base != new_base:
            conn.execute(
                f'UPDATE "{table}" SET "code_assets"=? WHERE "code_assets"=?',
                (new_base, old_base)
            )

    return True


def purge_orphan_asset_rows(conn: sqlite3.Connection, qr_code: str) -> None:
    """
    Remove any row whose code_assets is exactly the QR (no suffix) or
    starts with the QR but does not contain a sequence suffix (` - `).
    """
    table = _find_assets_table(conn)
    if not table:
        return
    cols = set(_table_columns(conn, table))
    if "code_assets" not in cols:
        return
    # Delete the exact QR row
    conn.execute(f'DELETE FROM "{table}" WHERE "code_assets"=?', (qr_code,))
    # Delete any row that starts with the QR but lacks the " - " separator
    like_prefix = qr_code + "%"
    conn.execute(
        f'DELETE FROM "{table}" WHERE "code_assets" LIKE ? AND "code_assets" NOT LIKE ?',
        (like_prefix, qr_code + " % - %")
    )


def delete_exact_asset_row(conn: sqlite3.Connection, qr_code: str) -> None:
    """
    Remove any row whose code_assets is exactly the QR (no filename suffix).
    This cleans up orphan records like '0000184403' after a rename.
    """
    table = _find_assets_table(conn)
    if not table:
        return
    cols = set(_table_columns(conn, table))
    if "code_assets" not in cols:
        return
    conn.execute(f'DELETE FROM "{table}" WHERE "code_assets"=?', (qr_code,))


def update_sdi_dataset_table(
    conn: sqlite3.Connection,
    qr_code: str,
    new_building: str,
    new_location: str,
    new_asset_type: str
) -> bool:
    """
    Update sdi_dataset and sdi_dataset_EL rows (if present) with new parameters.
    Uses UPDATE, not INSERT, to avoid creating duplicate rows.
    """
    tables = ["sdi_dataset", "sdi_dataset_EL"]
    updated_any = False

    for table in tables:
        if not _has_table(conn, table):
            continue

        cols = set(_table_columns(conn, table))

        qr_col = None
        for candidate in ["QR Code", "QR_Code", "qr_code", "QRCode"]:
            if candidate in cols:
                qr_col = candidate
                break
        if not qr_col:
            continue

        bldg_col = None
        for candidate in ["Building", "building", "Building_Code", "Bldg"]:
            if candidate in cols:
                bldg_col = candidate
                break

        loc_col = "Location" if "Location" in cols else None
        attr_col = "Attribute" if "Attribute" in cols else None

        updates = []
        params = []

        if bldg_col and new_building:
            updates.append(f'{_quote_ident(bldg_col)}=?')
            params.append(new_building)
        if loc_col and new_location:
            updates.append(f'{_quote_ident(loc_col)}=?')
            params.append(new_location)
        if attr_col and new_asset_type:
            updates.append(f'{_quote_ident(attr_col)}=?')
            params.append(normalize_asset_type_label(new_asset_type))

        if not updates:
            continue

        sql = f'UPDATE "{table}" SET {", ".join(updates)} WHERE {_quote_ident(qr_col)}=?'
        params.append(qr_code)
        cur = conn.execute(sql, tuple(params))
        updated_any = updated_any or cur.rowcount > 0
        print(f"[update_sdi_dataset] Updated {cur.rowcount} rows in '{table}' for QR {qr_code}")

    return updated_any


def _qr_has_package_rows(conn: sqlite3.Connection, qr_code: str) -> bool:
    """Return True if this QR is already in active or archived SDI packages."""
    for table in ("sdi_print_out", "sdi_print_out_arch"):
        if not _has_table(conn, table):
            continue
        cols = _table_columns(conn, table)
        if not cols:
            continue
        predicates = []
        params = []
        for col in cols:
            # Package schemas have shifted over time. Keep this conservative and
            # parameterized so a QR found in any text-like column blocks migration.
            predicates.append(f'CAST({_quote_ident(col)} AS TEXT)=?')
            params.append(qr_code)
        sql = f'SELECT 1 FROM "{table}" WHERE {" OR ".join(predicates)} LIMIT 1'
        if conn.execute(sql, tuple(params)).fetchone() is not None:
            return True
    return False


def reset_qr_ai_status(conn: sqlite3.Connection, qr_code: str) -> None:
    """Mark a QR for extraction after its capture identity changes."""
    if not _has_table(conn, "QR_codes"):
        return
    cols = set(_table_columns(conn, "QR_codes"))
    if "QR_code_ID" not in cols or "ai_status" not in cols:
        return
    conn.execute(
        'UPDATE "QR_codes" SET "ai_status"=? WHERE "QR_code_ID"=?',
        (0, qr_code),
    )


def retire_sdi_rows_for_asset_type_change(conn: sqlite3.Connection, qr_code: str) -> int:
    """Remove curated discipline rows that no longer match a changed asset type.

    A type change invalidates the discipline-specific extraction schema. The
    next chained AI run should repopulate the correct table from the renamed
    photos rather than carrying stale ME/BF/EL fields across disciplines.
    """
    if _qr_has_package_rows(conn, qr_code):
        raise ValueError(
            f"QR {qr_code} is already in an SDI package; asset type cannot be changed safely."
        )

    deleted = 0
    for table in ("sdi_dataset", "sdi_dataset_EL"):
        if not _has_table(conn, table):
            continue
        cols = set(_table_columns(conn, table))
        qr_col = next((c for c in ["QR Code", "QR_Code", "qr_code", "QRCode"] if c in cols), None)
        if not qr_col:
            continue
        cur = conn.execute(
            f'DELETE FROM "{table}" WHERE {_quote_ident(qr_col)}=?',
            (qr_code,),
        )
        deleted += max(0, cur.rowcount)
    return deleted


def _processed_log_paths(log_dir: str) -> List[str]:
    names = set(PROCESSED_JSON_LOGS.values()) | set(PROCESSED_IMAGE_LOGS.values())
    return [os.path.join(log_dir, name) for name in sorted(names)]


def backup_processed_logs(log_dir: str) -> Optional[str]:
    paths = [p for p in _processed_log_paths(log_dir) if os.path.exists(p)]
    if not paths:
        return None
    backup_dir = tempfile.mkdtemp(prefix="param_update_logs_")
    for path in paths:
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
    return backup_dir


def rollback_processed_logs(log_backup_dir: Optional[str], log_dir: str) -> None:
    if not log_backup_dir or not os.path.isdir(log_backup_dir):
        return
    for fname in os.listdir(log_backup_dir):
        src = os.path.join(log_backup_dir, fname)
        dst = os.path.join(log_dir, fname)
        try:
            os.replace(src, dst)
        except OSError:
            pass
    shutil.rmtree(log_backup_dir, ignore_errors=True)


def _remove_json_log_entries(path: str, filenames: set[str], qr_code: str = "") -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    before = len(data)
    for name in list(data.keys()):
        if name in filenames or (qr_code and str(name).startswith(f"{qr_code}_")):
            data.pop(name, None)
    if len(data) != before:
        _atomic_write_json(path, data, indent=2)
    return before - len(data)


def _remove_image_log_entries(path: str, filenames: set[str], qr_code: str = "") -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    kept = []
    removed = 0
    for line in lines:
        value = line.strip()
        if value in filenames or (qr_code and value.startswith(f"{qr_code} ")):
            removed += 1
            continue
        kept.append(line)
    if removed:
        _atomic_write_text(path, "".join(kept))
    return removed


def update_processed_logs(
    log_dir: str,
    qr_code: str,
    file_renames: List[Tuple[str, str, str, str]],
    json_updates: List[Tuple[str, str]],
    *,
    clear_all_for_qr: bool = False,
) -> Dict[str, int]:
    """Clear stale watcher entries after parameter-driven renames/archives."""
    image_names: set[str] = set()
    for old_path, new_path, _old_base, _new_base in file_renames:
        image_names.add(os.path.basename(old_path))
        image_names.add(os.path.basename(new_path))

    json_names: set[str] = set()
    for old_path, new_path in json_updates:
        for path in (old_path, new_path):
            name = os.path.basename(path)
            if name.lower().endswith(".json"):
                json_names.add(name)

    removed = {"json": 0, "images": 0}
    qr_prefix = qr_code if clear_all_for_qr else ""
    for log_name in PROCESSED_JSON_LOGS.values():
        removed["json"] += _remove_json_log_entries(
            os.path.join(log_dir, log_name),
            json_names,
            qr_prefix,
        )
    for log_name in PROCESSED_IMAGE_LOGS.values():
        removed["images"] += _remove_image_log_entries(
            os.path.join(log_dir, log_name),
            image_names,
            qr_prefix,
        )
    return removed


def update_json_files(
    qr_code: str,
    old_building: str,
    new_building: str,
    old_asset_type: str,
    new_asset_type: str,
    json_dir: str = None,
    new_location: str = "",
    backup_dir: Optional[str] = None,
    asset_type_changed: bool = False
) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """
    Update JSON files: rename and update building_number / asset_type fields.
    Returns (renames, backup_dir).
    """
    if json_dir is None:
        json_dir = os.environ.get("JSON_OUTPUT_DIR", JSON_OUTPUT_DIR)
    
    if not os.path.isdir(json_dir):
        return [], backup_dir
    
    safe_qr = sanitize_component(qr_code, prefer_digits=True)
    old_bldg = sanitize_component(old_building, prefer_digits=False)
    new_bldg = sanitize_component(new_building, prefer_digits=False)
    old_type = sanitize_component(old_asset_type, prefer_digits=False)

    candidates: List[str] = []
    if asset_type_changed:
        candidates = _same_qr_review_jsons(json_dir, safe_qr)
    else:
        expected_old = os.path.join(json_dir, f"{safe_qr}_{old_type}_{old_bldg}.json")
        if os.path.exists(expected_old):
            candidates.append(expected_old)
        else:
            type_matches = []
            for fname in os.listdir(json_dir):
                parts = _review_json_parts(fname)
                if not parts:
                    continue
                qr, asset_type, _building = parts
                if qr == safe_qr and asset_type == old_type:
                    type_matches.append(os.path.join(json_dir, fname))
            if len(type_matches) == 1:
                candidates.append(type_matches[0])
            elif len(type_matches) > 1:
                raise ValueError(
                    f"Ambiguous JSON files for QR {safe_qr} and type {old_type}; "
                    "refusing broad parameter update."
                )

    if not candidates:
        return [], backup_dir

    if backup_dir is None:
        backup_dir = tempfile.mkdtemp(prefix="param_update_json_")

    renames: List[Tuple[str, str]] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if asset_type_changed:
        for src in candidates:
            archived_path = _archive_active_json(src, backup_dir, stamp)
            renames.append((src, archived_path))
            print(f"[update_json] Retired active JSON {os.path.basename(src)} -> {os.path.basename(archived_path)}")
        return renames, backup_dir

    for src in candidates:
        # Backup original
        _backup_original(src, backup_dir)

        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)

        src_name = os.path.basename(src)
        parts = _review_json_parts(src_name)
        if parts:
            _qr, source_type, _source_building = parts
        else:
            source_type = old_type
        dest_name = f"{safe_qr}_{source_type}_{new_bldg}.json"
        dest_path = os.path.join(json_dir, dest_name)

        if dest_path != src and os.path.exists(dest_path):
            archived_dest = _archive_active_json(dest_path, backup_dir, stamp)
            renames.append((dest_path, archived_dest))
            print(
                f"[update_json] Archived conflicting destination "
                f"{os.path.basename(dest_path)} -> {os.path.basename(archived_dest)}"
            )

        if "qr_code" in data:
            data["qr_code"] = qr_code
        if "building_number" in data:
            data["building_number"] = new_building
        if "asset_type" in data:
            data["asset_type"] = f"- {source_type}"
        if new_location and "location" in data:
            data["location"] = new_location
        structured = data.get("structured_data")
        if new_location and isinstance(structured, dict) and "Location" in structured:
            structured["Location"] = new_location

        fd, tmp_path = tempfile.mkstemp(prefix="param_update_", suffix=".json", dir=json_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=4)
        os.replace(tmp_path, dest_path)

        if dest_path != src and os.path.exists(src):
            try:
                os.remove(src)
            except Exception:
                pass

        renames.append((src, dest_path))
        print(f"[update_json] Updated {os.path.basename(src)} -> {dest_name}")

    return renames, backup_dir


def rollback_json_updates(json_updates: List[Tuple[str, str]], backup_dir: Optional[str]) -> None:
    """Restore JSON files from backups after a failed update."""
    if not backup_dir or not os.path.isdir(backup_dir):
        return

    target_dir = os.path.dirname(json_updates[0][0]) if json_updates else None

    for old_path, new_path in json_updates:
        try:
            if new_path != old_path and os.path.exists(new_path):
                os.remove(new_path)
        except Exception:
            pass

    for fname in os.listdir(backup_dir):
        backup_src = os.path.join(backup_dir, fname)
        restore_dest = None
        if json_updates:
            # Find matching original path
            for old_path, _ in json_updates:
                if os.path.basename(old_path) == fname:
                    restore_dest = old_path
                    break
        if not restore_dest and target_dir:
            restore_dest = os.path.join(target_dir, fname)

        if restore_dest:
            try:
                os.replace(backup_src, restore_dest)
            except Exception:
                pass

    shutil.rmtree(backup_dir, ignore_errors=True)


def get_current_params(
    conn: sqlite3.Connection,
    qr_code: str,
    upload_dir: Optional[str] = None
) -> Optional[Dict[str, str]]:
    """
    Get the current parameters for a QR code from the database (with fallbacks).
    """
    result: Dict[str, str] = {"qr_code": qr_code}
    found = False

    table = "QR_codes"
    if _has_table(conn, table):
        cols = set(_table_columns(conn, table))
        if "QR_code_ID" in cols:
            bcode_candidates = ["Building Code", "Building_Code", "Code", "Property", "Bldg Code", "Bldg", "Building"]
            asset_type_candidates = ["asset_type", "Asset_Type", "Asset type"]
            bcode_col = next((c for c in bcode_candidates if c in cols), None)
            loc_col = "Location" if "Location" in cols else None
            asset_type_col = next((c for c in asset_type_candidates if c in cols), None)

            # Must be quoted: PostgreSQL folds unquoted identifiers to lowercase
            # (qr_code_id does not exist); SQLite tolerated the unquoted form.
            select_cols = [_quote_ident("QR_code_ID")]
            if bcode_col:
                select_cols.append(_quote_ident(bcode_col))
            if loc_col:
                select_cols.append(_quote_ident(loc_col))
            if asset_type_col:
                select_cols.append(_quote_ident(asset_type_col))

            sql = f'SELECT {", ".join(select_cols)} FROM "{table}" WHERE "QR_code_ID"=?'
            cur = conn.execute(sql, (qr_code,))
            row = cur.fetchone()

            if row:
                found = True
                idx = 1
                if bcode_col:
                    result["building_code"] = row[idx] or ""
                    idx += 1
                if loc_col:
                    result["location"] = row[idx] or ""
                    idx += 1
                if asset_type_col:
                    raw_asset_type = str(row[idx] or "").strip()
                    if raw_asset_type:
                        result["asset_type"] = map_asset_type_to_abbrev(raw_asset_type)

    # Fallback to dataset tables for building / location / asset_type
    dataset_info = _lookup_dataset_params(conn, qr_code)
    if dataset_info:
        found = True
        if dataset_info.get("building") and not result.get("building_code"):
            result["building_code"] = dataset_info["building"]
        if dataset_info.get("location") and not result.get("location"):
            result["location"] = dataset_info["location"]
        if dataset_info.get("asset_type") and not result.get("asset_type"):
            result["asset_type"] = dataset_info["asset_type"]

    # Fallback to file names
    if upload_dir:
        inferred = infer_params_from_files(upload_dir, qr_code)
        if inferred:
            found = True
            if inferred.get("building_code") and not result.get("building_code"):
                result["building_code"] = inferred["building_code"]
            if inferred.get("asset_type") and not result.get("asset_type"):
                result["asset_type"] = map_asset_type_to_abbrev(inferred["asset_type"])

    return result if found else None


def get_current_asset_type(upload_dir: str, qr_code: str) -> Optional[str]:
    """
    Infer the current asset type from existing files.
    """
    files = get_affected_files(upload_dir, qr_code)
    if not files:
        return None
    
    base = os.path.splitext(os.path.basename(files[0]))[0]
    parsed = parse_filename_base(base)
    return parsed.get("asset_type")


def execute_parameter_update(
    db_path: str,
    upload_dir: str,
    qr_code: str,
    old_params: Dict[str, str],
    new_params: Dict[str, str]
) -> Tuple[bool, str]:
    """
    Main orchestrator function for parameter updates.
    
    Performs atomic update of:
    - Photo files (rename)
    - QR_codes table
    - QR_code_assets table  
    - sdi_dataset or sdi_dataset_EL table (UPDATE only)
    - JSON files in Output_jason_api directory
    """
    safe_qr = sanitize_component(qr_code, prefer_digits=True)

    resolved_old = dict(old_params or {})
    lookup_conn = None
    try:
        lookup_conn = db.get_connection(sqlite_path=db_path)
        current = get_current_params(lookup_conn, safe_qr, upload_dir)
    except Exception:
        current = None
    finally:
        try:
            if lookup_conn:
                lookup_conn.close()
        except Exception:
            pass

    for key in ("building_code", "location", "asset_type"):
        if not resolved_old.get(key) and current and current.get(key):
            resolved_old[key] = current[key]

    changes = detect_parameter_changes(resolved_old, new_params)
    if not changes["changed"]:
        return (True, "No parameter changes detected")

    new_building = new_params.get("building_code", "")
    new_location = new_params.get("location", "")
    new_asset_type = new_params.get("asset_type", "")
    old_building = resolved_old.get("building_code", "")
    old_asset_type = resolved_old.get("asset_type", "")

    file_backup_dir = None
    json_backup_dir = None
    log_backup_dir = None
    conn = None
    affected_files: List[str] = []
    file_renames: List[Tuple[str, str, str, str]] = []
    json_updates: List[Tuple[str, str]] = []
    retired_sdi_rows = 0

    try:
        # Phase 1: collect files and create backups
        affected_files = get_affected_files(upload_dir, safe_qr)
        if changes["requires_file_rename"] and affected_files:
            file_backup_dir = backup_files(affected_files)
        log_dir = os.path.dirname(os.path.abspath(db_path))
        log_backup_dir = backup_processed_logs(log_dir)

        # Phase 2: Database transaction
        conn = db.get_connection(sqlite_path=db_path)
        # NOTE (Phase C/C4): 'BEGIN IMMEDIATE' is SQLite-specific. On SQLite this is the
        # legacy behavior; for PostgreSQL this must become a plain transaction (psycopg2
        # opens one implicitly). Guarded so the SQLite path is unchanged.
        if not db.is_postgres():
            conn.execute("BEGIN IMMEDIATE")

        # Phase 3: rename photo files (if needed)
        if changes["requires_file_rename"] and affected_files:
            file_renames = rename_files_atomic(
                upload_dir,
                affected_files,
                safe_qr,
                new_building,
                map_asset_type_to_abbrev(new_asset_type)
            )

        # Phase 4: update database tables
        update_qr_codes_table(
            conn,
            safe_qr,
            new_building,
            new_location,
            new_asset_type
        )

        # Clean any orphan asset rows that are just the QR code
        purge_orphan_asset_rows(conn, safe_qr)

        if file_renames:
            update_assets_table(conn, file_renames)
        # Clean up any orphan asset rows that only contain the QR value
        delete_exact_asset_row(conn, safe_qr)
        # Final sweep: remove any rows with bad pattern for this QR
        purge_orphan_asset_rows(conn, safe_qr)

        update_sdi_dataset_table(
            conn,
            safe_qr,
            new_building,
            new_location,
            new_asset_type
        )

        if changes["asset_type_changed"]:
            retired_sdi_rows = retire_sdi_rows_for_asset_type_change(conn, safe_qr)
            reset_qr_ai_status(conn, safe_qr)

        # Phase 5: update JSON outputs if filenames or location changed
        if changes["requires_file_rename"] or changes["location_changed"]:
            json_updates, json_backup_dir = update_json_files(
                safe_qr,
                old_building,
                new_building,
                map_asset_type_to_abbrev(old_asset_type),
                map_asset_type_to_abbrev(new_asset_type),
                new_location=new_location,
                backup_dir=json_backup_dir,
                asset_type_changed=changes["asset_type_changed"],
            )

        update_processed_logs(
            log_dir,
            safe_qr,
            file_renames,
            json_updates,
            clear_all_for_qr=changes["asset_type_changed"],
        )

        conn.execute("COMMIT")
        conn.close()
        conn = None

        if file_backup_dir:
            shutil.rmtree(file_backup_dir, ignore_errors=True)
        if json_backup_dir:
            shutil.rmtree(json_backup_dir, ignore_errors=True)
        if log_backup_dir:
            shutil.rmtree(log_backup_dir, ignore_errors=True)

        changes_list = []
        if changes["building_changed"]:
            changes_list.append(f"Building: {old_building} -> {new_building}")
        if changes["location_changed"]:
            changes_list.append("Location updated")
        if changes["asset_type_changed"]:
            old_type_display = map_asset_type_to_abbrev(old_asset_type) if old_asset_type else ""
            new_type_display = map_asset_type_to_abbrev(new_asset_type) if new_asset_type else ""
            changes_list.append(f"Asset Type: {old_type_display or 'N/A'} -> {new_type_display or 'N/A'}")

        files_renamed = len(file_renames)
        json_updated = len(json_updates)
        msg = "Parameters updated successfully."
        if changes_list:
            msg += " " + "; ".join(changes_list) + "."
        if files_renamed > 0:
            msg += f" {files_renamed} photo(s) renamed."
        if json_updated > 0:
            if changes["asset_type_changed"]:
                msg += f" {json_updated} stale JSON file(s) retired for reprocessing."
            else:
                msg += f" {json_updated} JSON file(s) updated."
        if retired_sdi_rows > 0:
            msg += f" {retired_sdi_rows} stale curated SDI row(s) retired."

        return (True, msg)

    except Exception as e:
        if conn:
            try:
                conn.execute("ROLLBACK")
                conn.close()
            except Exception:
                pass

        rollback_json_updates(json_updates, json_backup_dir)
        rollback_processed_logs(log_backup_dir, os.path.dirname(os.path.abspath(db_path)))

        if file_backup_dir and affected_files:
            rollback_file_changes(file_backup_dir, upload_dir, affected_files, file_renames)

        return (False, f"Update failed: {str(e)}")
