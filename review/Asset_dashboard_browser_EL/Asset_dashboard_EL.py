#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import sqlite3
import time
import copy
from typing import Optional
import sys
import importlib.util  # For importing the dictionary file
from functools import lru_cache
from threading import Lock
from datetime import datetime
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, send_file, jsonify, flash, Blueprint, g

import excel_export
import db as qrdb  # backend-agnostic QR_codes DB layer (Phase C / C4)
from review_buttons import REVIEW_BUTTONS  # canonical review-page action buttons (three-copy rule)

# Per-app Flask endpoint names for the canonical review buttons
# (the registry itself is app-agnostic; see review_buttons.py). The dashboard
# target is dynamic per request (base_route) and is added at render time.
REVIEW_ENDPOINTS_STATIC = {"print": "main.review_print", "export": "main.review_export"}

## Import authentication and environment variable libraries
from flask_login import login_required, current_user, login_user, logout_user
from dotenv import load_dotenv

## Add the shared auth_service directory to Python's path
sys.path.append('/home/developer/auth_service')
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.append(REPO_ROOT)
API_DIR = os.path.join(REPO_ROOT, "API")
if API_DIR not in sys.path:
    sys.path.append(API_DIR)

try:
    from audit.logger import log_change as _audit_log_change
except Exception as _audit_exc:
    print(f"[audit] import failed in reviewer_el: {_audit_exc}")
    _audit_log_change = None

from review_installation_date import (
    InstallationDateError, get_installation_date,
    parse_installation_date, update_installation_date,
)

try:
    from electrical_equipment_rules import derive_electrical_equipment_type, derive_electrical_power_type
except ModuleNotFoundError:
    _ELECTRICAL_EQUIPMENT_TYPE_MAP = {
        "MDP": "Main Distribution Panel",
        "CDP": "Central Distribution Panel",
        "SPL": "Splitter",
        "MCC": "Motor Control Center",
        "PNL": "Panel",
        "SWBD": "Switchboard",
        "ATS": "Automatic Transfer Switch",
        "TX": "Transformer",
    }

    def derive_electrical_equipment_type(tag_value: object) -> str:
        tag_upper = str(tag_value or "").strip().upper()
        if not tag_upper:
            return ""
        for code, name in sorted(_ELECTRICAL_EQUIPMENT_TYPE_MAP.items(), key=lambda item: len(item[0]), reverse=True):
            if tag_upper.startswith(code):
                return name
        return ""

    _ELECTRICAL_POWER_TYPE_CODES = {"NES", "NE", "NS", "ES", "N", "E", "S"}
    _ELECTRICAL_POWER_TYPE_RE = re.compile(r"NES|NE|NS|ES|N|E|S")

    def derive_electrical_power_type(tag_value: object) -> str:
        tag_upper = str(tag_value or "").strip().upper()
        if not tag_upper:
            return ""
        parts = tag_upper.split("-")
        if len(parts) <= 1:
            return ""
        match = _ELECTRICAL_POWER_TYPE_RE.search(parts[1])
        if not match:
            return ""
        system_code = match.group(0)
        return system_code if system_code in _ELECTRICAL_POWER_TYPE_CODES else ""

try:
    from validators_shared import normalize_el_supply_from_tag, normalize_power_rating_pair
except ModuleNotFoundError:
    _POWER_RATING_NUM_FIRST_RE = re.compile(r"(?<!\d)(\d{1,5})\s*(KVA|KW|VA)\b", re.IGNORECASE)
    _POWER_RATING_UNIT_FIRST_RE = re.compile(r"\b(KVA|KW|VA)\s*(\d{1,5})(?!\d)", re.IGNORECASE)

    def normalize_power_rating_pair(value: str, uom: str = "") -> tuple[str, str]:
        candidates = [
            str(value or "").strip(),
            str(uom or "").strip(),
            f"{str(value or '').strip()} {str(uom or '').strip()}".strip(),
            f"{str(uom or '').strip()} {str(value or '').strip()}".strip(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            text = candidate.upper()
            match = _POWER_RATING_NUM_FIRST_RE.search(text)
            if match:
                return match.group(1), match.group(2).upper()
            match = _POWER_RATING_UNIT_FIRST_RE.search(text)
            if match:
                return match.group(2), match.group(1).upper()
        if re.fullmatch(r"\d{1,5}", str(value or "").strip()) and str(uom or "").strip().upper() in {"KVA", "KW", "VA"}:
            return str(value or "").strip(), str(uom or "").strip().upper()
        return "", ""

    def normalize_el_supply_from_tag(value: object) -> str:
        clean = str(value or "").upper().replace("EQUIPMENT NAME:", '').replace("MAIN", '').strip()
        if not clean:
            return ""
        if clean.startswith("TX") or (clean.startswith("T") and len(clean) > 1 and clean[1].isdigit()):
            return clean
        if clean[0].isdigit():
            return f"PNL-{clean}"
        for abbr in ("MDP", "CDP", "SPL", "MCC", "PNL", "SWBD", "ATS"):
            if clean.startswith(abbr):
                remainder = clean[len(abbr):].lstrip(" -_")
                return f"{abbr}-{remainder}" if remainder else abbr
        return clean

from auth_model import db, bcrypt, User, ensure_user_access_table, has_permission, is_admin, require_permission, access_denied_response
from auth_controller import login_manager

# --- GPS Service ---
from gps_service import gps_bp
from json_persistence import (
    RevisionConflictError,
    atomic_write_json,
    compute_review_revision,
    ensure_revision_matches,
    has_meaningful_structured_data,
    mark_json_processed,
    merge_form_into_structured,
    normalize_dashboard_query,
)

# ---------------------------------------------------------------------
# Dictionary Import Logic
# ---------------------------------------------------------------------
label_schema = {}

try:
    elec_candidates = []
    env_elec_path = os.environ.get("ELEC_DICT_PATH", '').strip()
    if env_elec_path:
        elec_candidates.append(env_elec_path)
    env_elec_path_alt = os.environ.get("ELECTRICAL_DICT_PATH", '').strip()
    if env_elec_path_alt and env_elec_path_alt not in elec_candidates:
        elec_candidates.append(env_elec_path_alt)
    elec_candidates.append("/home/developer/dictionary/electrical.dictionary.py")
    elec_candidates.append(
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dictionary", "electrical.dictionary.py")
        )
    )

    for path in elec_candidates:
        if not path:
            continue
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("electrical_dictionary", path)
            elec_dict_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(elec_dict_module)
            label_schema = getattr(elec_dict_module, "label_schema", {})
            break
    if not label_schema:
        print("[WARN] Electrical dictionary not found or empty.")
except Exception as e:
    print(f"[WARN] Failed to load electrical dictionary: {e}")

# Legacy electrical flow rules — imported ONLY to gate `_apply_dictionary_priority`
# call sites on Buildings.Process; the standard flow above is untouched. See
# review/Asset_dashboard_browser_EL/legacy_flow.py and
# Markdowns_documentation/rules/review_apps.rules.md ("EL Legacy Flow Rules").
import legacy_flow as el_legacy_flow

MECH_PREFIX_KEYS = []
mechanical_asset_dict = {}
MECH_DICT_PATH = None
MECH_DICT_MTIME = None
MECH_DICT_LOCK = Lock()


def _mechanical_dict_candidates() -> list[str]:
    candidates = []
    env_mech_path = os.environ.get("MECH_DICT_PATH", '').strip()
    if env_mech_path:
        candidates.append(os.path.normpath(env_mech_path))
    candidates.append(os.path.normpath("/home/developer/dictionary/mechanical_dictionary.py"))
    candidates.append(
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dictionary", "mechanical_dictionary.py")
        )
    )
    seen = set()
    ordered = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def _load_mechanical_dictionary_file(path: str) -> dict:
    spec = importlib.util.spec_from_file_location("mechanical_dictionary_runtime", path)
    mech_dict_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mech_dict_module)
    loaded = getattr(mech_dict_module, "ASSET_DICTIONARY", {}) or {}
    return loaded if isinstance(loaded, dict) else {}


def _get_live_mechanical_dictionary(force_reload: bool = False) -> dict:
    global mechanical_asset_dict, MECH_PREFIX_KEYS, MECH_DICT_PATH, MECH_DICT_MTIME

    with MECH_DICT_LOCK:
        selected_path = None
        selected_mtime = None
        for path in _mechanical_dict_candidates():
            if not os.path.exists(path):
                continue
            selected_path = path
            try:
                selected_mtime = os.path.getmtime(path)
            except OSError:
                selected_mtime = None
            break

        if not selected_path:
            if force_reload or mechanical_asset_dict:
                print("[WARN] Mechanical dictionary not found.")
            mechanical_asset_dict = {}
            MECH_PREFIX_KEYS = []
            MECH_DICT_PATH = None
            MECH_DICT_MTIME = None
            return mechanical_asset_dict

        needs_reload = (
            force_reload
            or not mechanical_asset_dict
            or selected_path != MECH_DICT_PATH
            or selected_mtime != MECH_DICT_MTIME
        )
        if not needs_reload:
            return mechanical_asset_dict

        try:
            loaded = _load_mechanical_dictionary_file(selected_path)
        except Exception as e:
            print(f"[WARN] Failed to load mechanical dictionary: {e}")
            return mechanical_asset_dict

        mechanical_asset_dict = loaded
        MECH_PREFIX_KEYS = sorted(mechanical_asset_dict.keys(), key=len, reverse=True)
        MECH_DICT_PATH = selected_path
        MECH_DICT_MTIME = selected_mtime
        return mechanical_asset_dict


_get_live_mechanical_dictionary(force_reload=True)


# ---------------------------------------------------------------------
# Flask app setup
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

## Load environment variables from the central .env file
load_dotenv('/home/developer/auth_service.env', override=True)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "review_asset_templates"),
    static_folder=os.path.join(BASE_DIR, "review_asset_templates", "static")
)

## Configure the app using variables from the .env file
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI')
app.config['SESSION_COOKIE_DOMAIN']    = os.getenv('SESSION_COOKIE_DOMAIN')
app.config['SESSION_COOKIE_SAMESITE']  = 'None'
app.config['SESSION_COOKIE_SECURE']    = True
app.config['SESSION_COOKIE_HTTPONLY']  = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'None'
app.config['REMEMBER_COOKIE_SECURE']   = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True

# Staging/local QA over plain HTTP (env-gated; NO-OP in prod).
if os.getenv('STAGING_INSECURE_COOKIES') == '1':
    app.config['SESSION_COOKIE_DOMAIN']  = None
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE']   = False
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_SECURE']   = False

## Connect the extensions
db.init_app(app)
bcrypt.init_app(app)
login_manager.init_app(app)

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
JSON_DIR = os.environ.get("JSON_DIR", "/home/developer/Output_jason_api")
IMG_DIR = os.environ.get("IMG_DIR", "/home/developer/Capture_photos_upload")
DB_PATH = os.environ.get("DB_PATH", "/home/developer/asset_capture_app_dev/data/QR_codes.db")

# --- GPS Service configuration ---
app.config['DATABASE_FILE_PATH'] = DB_PATH
app.register_blueprint(gps_bp)

# --- Single Line Diagram configuration ---
app.config["DB_PATH"] = DB_PATH
app.config["IMG_DIR"] = IMG_DIR
app.config["SLD_PDF_STORAGE"] = os.environ.get(
    "SLD_PDF_STORAGE",
    os.path.join(os.path.dirname(DB_PATH), "sld_pdfs"),
)
app.config["SLD_EXTRACT_SCRIPT"] = os.environ.get(
    "SLD_EXTRACT_SCRIPT",
    os.path.join(BASE_DIR, "sld", "extract_electrical_schema.py"),
)
app.config["SLD_EXTRACT_TIMEOUT"] = int(os.environ.get("SLD_EXTRACT_TIMEOUT", "1800"))
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("SLD_MAX_CONTENT_LENGTH", str(100 * 1024 * 1024)))
os.makedirs(app.config["SLD_PDF_STORAGE"], exist_ok=True)

# Run schema migrations (idempotent) before any request is served.
try:
    from migrations.run import run as _run_migrations
    _run_migrations(DB_PATH, os.path.join(BASE_DIR, "migrations"))
except Exception as _mig_err:
    app.logger.warning("[sld] migration runner failed: %s", _mig_err)

from sld_blueprint import sld_bp, ensure_sld_schema, get_building_display
with app.app_context():
    try:
        ensure_sld_schema()
    except Exception as _schema_err:
        app.logger.warning("[sld] ensure_sld_schema failed: %s", _schema_err)
    ensure_user_access_table()
app.register_blueprint(sld_bp)

SDI_TABLE = "sdi_dataset_EL"
QR_CODES_TABLE = "QR_codes"
QR_CODE_ID_COL = "QR_code_ID"
QR_APPROVED_COL = "Approved"
QR_LOCATION_COL = "Location"
QR_CODE_ID_COL_CANDIDATES = [
    QR_CODE_ID_COL,
    "QR Code ID",
    "QR Code",
    "QR_code",
    "QRCode",
]
QR_LOCATION_COL_CANDIDATES = [
    QR_LOCATION_COL,
    "Space",
    "QR Location",
]
QR_CODE_ASSETS_TABLE = "QR_code_assets"
QR_CODE_ASSETS_PROCESS_COL = "Col_process"
QR_CODE_ASSETS_QR_COL_CANDIDATES = [
    "code_assets",
    "code_asset",
    "QR_code",
    "QR Code",
    "QR_code_ID",
    "QR Code ID",
]

SDI_PRINT_OUT_TABLE = "sdi_print_out"
SDI_ARCHIVE_TABLE = "sdi_print_out_arch"

ATTRIBUTE_TABLE = "Attribute"
ATTRIBUTE_CODE_COL = "Code"       
ATTRIBUTE_VAL_COL = "Attribute"   

ASSET_GROUP_TABLE = "Asset_Group"
ASSET_GROUP_NAME_COL = "Name"     
ASSET_GROUP_CLASS_COL = "Full Classification" 
ASSET_GROUP_LEVEL_COL = "Level"   
ASSET_GROUP_DEFAULT = "Panels"    

VALID_IMAGE_EXTS = ['.jpg', '.JPG', '.jpeg', '.JPEG', '.png', '.PNG']

EL_REQUIRED_COMMON_FIELDS = ("UBC Asset Tag",)
EL_REQUIRED_GROUP_FIELDS = {
    "Interior Distribution Transformers": (
        "Power Rating",
        "Power Rating (UoM)",
        "Voltage Rating",
        "Voltage Rating (UoM)",
        "Equipment ID",
        "Equipment Type",
        "Fed From Amperage Rating",
        "Fed From Amperage Rating (UoM)",
        "Fed From Equipment ID",
        "Power Type",
    ),
    "Panels": (
        "Amperage Rating",
        "Amperage Rating (UoM)",
        "Equipment ID",
        "Voltage Rating",
        "Voltage Rating (UoM)",
        "Supply From",
        "Equipment Type",
        "Fed From Amperage Rating",
        "Fed From Amperage Rating (UoM)",
        "Fed From Equipment ID",
        "Power Type",
    ),
    "Other Service and Distribution": (
        "Amperage Rating",
        "Amperage Rating (UoM)",
        "Equipment ID",
        "Equipment Type",
        "Fed From Equipment ID",
        "Power Type",
        "Voltage Rating",
        "Voltage Rating (UoM)",
    ),
    "Motor Control Centers": (
        "Amperage Rating",
        "Amperage Rating (UoM)",
        "Equipment ID",
        "Equipment Type",
        "Fed From Amperage Rating",
        "Fed From Amperage Rating (UoM)",
        "Fed From Equipment ID",
        "Power Type",
        "Voltage Rating",
        "Voltage Rating (UoM)",
    ),
    "Automatic Transfer Switches": (
        "Amperage Rating",
        "Amperage Rating (UoM)",
        "Equipment ID",
        "Equipment Type",
        "Fed From Amperage Rating",
        "Fed From Equipment ID",
        "Power Type",
        "Voltage Rating",
        "Voltage Rating (UoM)",
    ),
}
# 'Main Transformers' (Asset_Group EL.21.306.4057, elec_dist_setup='Y') is a
# distinct classification from 'Interior Distribution Transformers'
# (EL.21.306.4050) but carries exactly the same Planon-facing field set.
# Without this alias _build_el_required_fields_payload falls through to the
# common set and reports a green 'Complete' traffic light for a transformer
# whose ratings are all blank. Aliased rather than duplicated so the two can
# never drift apart.
EL_REQUIRED_GROUP_FIELDS["Main Transformers"] = EL_REQUIRED_GROUP_FIELDS[
    "Interior Distribution Transformers"
]
# Asset groups scored against the transformer field set (Power Rating instead
# of Amperage Rating).
EL_TRANSFORMER_ASSET_GROUPS = ("Interior Distribution Transformers", "Main Transformers")
EL_SLD_DEPENDENT_FIELDS = ("Fed From Amperage Rating", "Fed From Amperage Rating (UoM)")
# ME-style nameplate fields captured by the General (non-distribution) review
# form variant (2026-08-07). Column names on sdi_dataset_EL match these JSON
# keys; Distribution rows keep them blank.
EL_NAMEPLATE_FIELDS = ("Manufacturer", "Model", "Serial Number", "Year")
# Optional General-form fields (2026-08-07 Capacity follow-up): stored and
# packaged like the nameplate fields, but deliberately excluded from
# EL_NAMEPLATE_FIELDS so completeness scoring, the hover checklist, and the
# traffic light are unaffected.
EL_CAPACITY_FIELDS = ("Capacity", "Capacity (UoM)")
EL_REQUIRED_ALL_COLUMNS = tuple(
    dict.fromkeys(
        ("QR Code", "Asset Group", "Building", *EL_REQUIRED_COMMON_FIELDS)
        + tuple(
            field
            for fields in EL_REQUIRED_GROUP_FIELDS.values()
            for field in fields
        )
        + EL_NAMEPLATE_FIELDS
    )
)

# ---------------------------------------------------------------------
# Photo rules
# ---------------------------------------------------------------------
ALL_SHOW = ['-0', '-1', '-2', '-3']
REQUIRED = ['-1', '-2']
SEQ_SHOW = ALL_SHOW[:]

JSON_NAME_RE = re.compile(r"^([A-Za-z0-9]+)_EL_(\d+(?:-\d+)?)\.json$")


# --- START: Directory Sync Logic ---

DATA_DIR = os.path.dirname(DB_PATH)
PROCESSED_LOG_EL = os.path.join(DATA_DIR, "processed_images_el.log")
IMG_NAME_RE_EL = re.compile(r"^(\d+)\s+(.+?)\s+EL\s+-\s+[0-3]\.(?:jpe?g|png)$", re.IGNORECASE)
image_sync_lock = Lock()
PROCESSED_JSON_LOG_EL = os.path.join(DATA_DIR, "processed_json_el.log")
json_sync_lock = Lock()


def _is_el_filename(filename: str) -> bool:
    return bool(JSON_NAME_RE.match(filename))


def sync_image_directory_to_db_el():
    if not image_sync_lock.acquire(blocking=False):
        return
    try:
        if not os.path.isdir(IMG_DIR):
            return

        processed_files = set()
        if os.path.exists(PROCESSED_LOG_EL):
            with open(PROCESSED_LOG_EL, 'r', encoding='utf-8') as f:
                processed_files = {line.strip() for line in f if line.strip()}

        current_files = {f for f in os.listdir(IMG_DIR) if f.lower().endswith(tuple(VALID_IMAGE_EXTS))}
        new_files = sorted(list(current_files - processed_files))

        if not new_files:
            return

        print(f"SYNC-IMG (EL): Found {len(new_files)} new image(s).")
        successfully_processed = []
        for filename in new_files:
            match = IMG_NAME_RE_EL.match(filename)
            if not match:
                if " EL " in filename:
                    successfully_processed.append(filename)
                continue

            qr, building = match.groups()
            qr_s, building_s = qr.strip(), building.strip()
            try:
                # The placeholder sync calls _sync_db_from_structured with sd={},
                # which derives every column as "" and, via _db_upsert_el_row,
                # issues an UPDATE that overwrites every field to blank. If an
                # sdi_dataset_EL row already holds real AI-captured data for
                # this QR+Building (which is the normal case once a JSON has
                # been synced), re-placeholdering would wipe that row. Skip it.
                if _sdi_row_has_data(qr_s, building_s):
                    successfully_processed.append(filename)
                    continue
                print(f"   -> Syncing placeholder for QR: {qr_s}, Building: {building_s} from {filename}")
                # No Legacy/Standard gate needed here (task-6 review Finding H
                # analysis): with sd={}, every input to the five gated helpers
                # is blank -- ubc_final/branch/supply_from all resolve to "" --
                # and each helper is a proven no-op on blank input:
                # _get_el_equipment_id_value is `str(tag or "").strip()`;
                # derive_electrical_equipment_type/derive_electrical_power_type
                # both guard `if not tag_upper: return ""`; and
                # normalize_el_supply_from_tag/normalize_supply_from both guard
                # `if not clean/s: return ""`. So the Standard branch of
                # _sync_db_from_structured produces the exact same "" for
                # Equipment ID/Equipment Type/Power Type/Supply From/Fed From
                # Equipment ID that the Legacy branch would (it reads the same
                # blank sd fields as-is) -- passing process= here cannot change
                # this call's outcome, so the default is left as-is.
                _sync_db_from_structured(qr=qr_s, building=building_s, sd={})
                successfully_processed.append(filename)
            except Exception as e:
                print(f"SYNC-IMG-ERROR (EL): DB upsert failed for {filename}: {e}")

        if successfully_processed:
            with open(PROCESSED_LOG_EL, 'a', encoding='utf-8') as f:
                for filename in successfully_processed:
                    f.write(f"{filename}\n")
    finally:
        image_sync_lock.release()


def sync_json_directory_to_db_el():
    if not json_sync_lock.acquire(blocking=False):
        return
    try:
        if not os.path.isdir(JSON_DIR):
            return

        processed_files = {}
        if os.path.exists(PROCESSED_JSON_LOG_EL):
            with open(PROCESSED_JSON_LOG_EL, 'r', encoding='utf-8') as f:
                try:
                    processed_files = json.load(f)
                except json.JSONDecodeError:
                    print("SYNC-JSON-WARN (EL): Could not read log, starting fresh.")

        force_resync = not _db_table_has_column(SDI_TABLE, "Avg_ai_conf")
        files_to_process = {}
        for filename in os.listdir(JSON_DIR):
            if not _is_el_filename(filename):
                continue
            
            filepath = os.path.join(JSON_DIR, filename)
            current_mtime = os.path.getmtime(filepath)
            
            if force_resync or filename not in processed_files or current_mtime > processed_files.get(filename, 0):
                files_to_process[filename] = current_mtime

        if not files_to_process:
            return

        processed_updates = {}
        print(f"SYNC-JSON (EL): Found {len(files_to_process)} new/updated JSON file(s).")
        # Legacy gate (task-6 review Finding H): this is the load-bearing batch
        # path feeding sdi_dataset_EL -- force_resync above can replay EVERY EL
        # JSON through here (e.g. after an Avg_ai_conf column migration), so a
        # missing per-record gate would clobber Legacy DB rows on every bulk
        # resync. One connection is opened for the whole loop (reused per
        # record for the lightweight Process lookup only -- _sync_db_from_structured
        # still manages its own connection for the actual upsert, unchanged).
        # If the DB is not reachable at all, skip this sync pass entirely
        # rather than guess a process for any record (no silent default).
        if not _connectable():
            print("SYNC-JSON-WARN (EL): DB not connectable; skipping this sync pass.")
            return
        try:
            with qrdb.get_connection(sqlite_path=DB_PATH) as _bp_conn:
                for filename, mtime in files_to_process.items():
                    match = JSON_NAME_RE.match(filename)
                    if not match:
                        continue

                    qr, building = match.groups()
                    filepath = os.path.join(JSON_DIR, filename)
                    try:
                        try:
                            _process = el_legacy_flow.get_building_process(_bp_conn, building)
                        except el_legacy_flow.BuildingProcessError as exc:
                            print(f"SYNC-JSON-WARN (EL): Skipping {filename}: {exc}")
                            continue

                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = json.load(f)

                        structured_data = content.get("structured_data", {})
                        if has_meaningful_structured_data(structured_data):
                            if _coerce_packaged_approval(qr, structured_data):
                                content["structured_data"] = structured_data
                                with open(filepath, 'w', encoding='utf-8') as f:
                                    json.dump(content, f, ensure_ascii=False, indent=4)
                                mtime = os.path.getmtime(filepath)
                            avg_ai_val = _extract_avg_ai_conf(content)
                            comp_score = float(content.get("completeness_score") or 0.0)
                            # Guard: if this JSON carries zero completeness and zero AI
                            # confidence (i.e. it is effectively a placeholder even though
                            # Description holds a template like "Panel - "), do not let it
                            # overwrite a DB row that already holds real captured content.
                            # Legitimate user saves go through save_review which writes the
                            # DB directly AND marks the file processed, so they are never
                            # re-processed by this path.
                            if comp_score == 0.0 and not avg_ai_val and _sdi_row_has_data(qr, building):
                                print(f"SYNC-JSON-WARN (EL): Skipping {filename} — zero completeness/confidence but DB row already has real content.")
                                processed_files[filename] = mtime
                                processed_updates[filename] = mtime
                            else:
                                print(f"   -> Syncing full data from {filename}")
                                _sync_db_from_structured(
                                    qr=qr,
                                    building=building,
                                    sd=structured_data,
                                    asset_type=content.get("asset_type"),
                                    avg_ai_conf=avg_ai_val,
                                    process=_process,
                                )
                                processed_files[filename] = mtime
                                processed_updates[filename] = mtime
                        else:
                            print(f"SYNC-JSON-WARN (EL): Skipping sparse structured_data in {filename}; marked processed to prevent replay.")
                            processed_files[filename] = mtime
                            processed_updates[filename] = mtime

                    except Exception as e:
                        print(f"SYNC-JSON-ERROR (EL): Failed to process {filename}: {e}")
        except Exception as e:
            # Guard (task-6 round-4 review): sync_json_directory_to_db_el runs
            # from before_request_handler on nearly every EL route. _connectable()
            # above only checks configuration, not reachability, so during a DB
            # outage the connection attempt (or a mid-loop connectivity drop)
            # would otherwise propagate out of this background sync and 500 every
            # page. Degrade gracefully instead: warn once and skip this pass.
            print(f"SYNC-JSON-WARN (EL): batch sync skipped: {e}")
            return

        if processed_updates:
            mark_json_processed(PROCESSED_JSON_LOG_EL, filename_mtimes=processed_updates)
    finally:
        json_sync_lock.release()


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
    if not packaged or str(structured.get("Approved", '') or "").strip() == "True":
        return False
    structured["Approved"] = "True"
    return True


# ---------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------

# --- UPDATED: Dictionary Parsing Helper ---
def _derive_volts_loc(tag: str):
    """
    Parses UBC Asset Tag using 'label_schema' to find Volts and Location.
    Handles both hyphenated (CDP-6-N-1-...) and compressed (PNL-2N3L1) formats.
    Returns (volts, location) tuple. Values are None if not found.
    """
    if not tag or not label_schema:
        return None, None

    tag = tag.strip().upper()
    # Normalize spacing so tags like "CDP 6-N-1-L-1" parse like "CDP-6-N-1-L-1".
    tag = re.sub(r"\s+", "-", tag)
    tag = re.sub(r"-{2,}", "-", tag).strip("-")
    parts = tag.split('-')
    
    # 1. Try Transformer (Prefix "TX")
    if parts[0] == 'TX' and 'transformer' in label_schema:
        # Schema: TX-<SYSTEM>-<LOCATION>-<SEQUENCE> (hyphenated)
        if len(parts) >= 3:
            loc_code = parts[2]
            loc_map = label_schema['transformer'].get('codes', {}).get('location', {})
            location = loc_map.get(loc_code)
            
            if not location and loc_code.isdigit():
                location = f"Level {loc_code}"
                
            return None, location 

        # Schema: TX-<System><Location><Seq> (compressed, e.g. TX-N01)
        elif len(parts) == 2:
            suffix = parts[1]
            if len(suffix) >= 2:
                 # Assuming System(1 char) + Location(1 char)
                 loc_code = suffix[1]
                 loc_map = label_schema['transformer'].get('codes', {}).get('location', {})
                 location = loc_map.get(loc_code)
                 if not location and loc_code.isdigit():
                    location = f"Level {loc_code}"
                 return None, location

    # 2. Try Panel / Distribution
    elif 'panel' in label_schema:
        panel_codes = label_schema['panel'].get('codes', {})
        volts = None
        location = None
        
        # Scenario A: Fully Hyphenated
        # Example: CDP-6-N-1-L-1
        # Indices: 0(Prefix)-1(Volt)-2(Sys)-3(Loc)-4(Type)-5(Seq)
        if len(parts) >= 4:
            # Voltage
            volt_code = parts[1]
            volt_map = panel_codes.get('voltage', {})
            volts = volt_map.get(volt_code)
            
            # Location
            loc_code = parts[3]
            loc_map = panel_codes.get('location', {})
            location = loc_map.get(loc_code)
            
            if not location and re.fullmatch(r'\d+(?:\.\d+)?', loc_code):
                location = f"Level {loc_code}"
        
        # Scenario B: Compressed / Short Format
        # Example: PNL-2N3L1 or CDP-6N3M1
        # Structure: Prefix-Voltage(1)System(1)Location(1)...
        elif len(parts) == 2:
            suffix = parts[1]
            # Support locations with decimals (e.g., 1.5) by regex parsing
            m = re.match(r'^([A-Z0-9])([A-Z])?([0-9]+(?:\.[0-9]+)?)(.*)$', suffix)
            if m:
                volt_code, _, loc_code, _ = m.groups()
                volt_map = panel_codes.get('voltage', {})
                volts = volt_map.get(volt_code)

                loc_map = panel_codes.get('location', {})
                location = loc_map.get(loc_code)

                if not location and re.fullmatch(r'\d+(?:\.\d+)?', loc_code):
                    location = f"Level {loc_code}"

        return volts, location

    return None, None


def _apply_dictionary_priority(data: dict, tag: str) -> bool:
    if not tag:
        return False
    d_volts, _ = _derive_volts_loc(tag)
    changed = False
    volts_manual = str(data.get("volts_manual_override") or "").strip() == "1"
    current_volts = str(data.get("Volts") or "").strip()
    if volts_manual and current_volts:
        return False
    if d_volts and current_volts != d_volts:
        data["Volts"] = d_volts
        changed = True
    return changed

def _clear_legacy_tag_derived_location(data: dict, tag: str) -> bool:
    if not isinstance(data, dict) or not tag:
        return False
    current_location = str(data.get("Location") or "").strip()
    if not current_location:
        return False
    _, derived_location = _derive_volts_loc(tag)
    if not derived_location:
        return False
    if current_location.casefold() != str(derived_location).strip().casefold():
        return False
    data["Location"] = ""
    return True


def _apply_tag_dictionary_first(data: dict, asset_type: str = None):
    """
    Apply mechanical dictionary (asset group/attribute/description) and UBC tag dictionary
    (volts only) before any other derivations. Returns (tag_used, changed_flag).
    """
    tag = (data.get("UBC Asset Tag") or data.get("Branch Panel") or "").strip()
    before = {
        "Attribute": data.get("Attribute"),
        "Asset Group": data.get("Asset Group"),
        "Description": data.get("Description"),
        "Volts": data.get("Volts"),
        "Location": data.get("Location"),
    }
    changed = False
    if tag:
        if _clear_legacy_tag_derived_location(data, tag):
            changed = True
        if _apply_mechanical_fallback(data, tag, asset_type):
            changed = True
        if _apply_dictionary_priority(data, tag):
            changed = True
    after = {
        "Attribute": data.get("Attribute"),
        "Asset Group": data.get("Asset Group"),
        "Description": data.get("Description"),
        "Volts": data.get("Volts"),
        "Location": data.get("Location"),
    }
    if before != after:
        changed = True
    return tag, changed


def _normalize_asset_type(asset_type: str) -> str:
    """
    Normalize asset_type values so comparisons ignore punctuation/casing.
    Examples: "- EL" -> "EL", "me" -> "ME".
    """
    if asset_type is None:
        return None
    cleaned = re.sub(r'[^A-Za-z0-9]+', '', str(asset_type))
    cleaned = cleaned.upper()
    return cleaned or None

def _normalize_asset_type_values(value) -> set[str]:
    """
    Normalize asset_type values and support lists/tuples.
    Returns a set of normalized strings; empty set means "not specified".
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    out = set()
    for v in values:
        cleaned = re.sub(r'[^A-Za-z0-9]+', '', str(v or ""))
        cleaned = cleaned.upper()
        if cleaned:
            out.add(cleaned)
    return out

def _get_entry_asset_type(entry: dict) -> set[str]:
    """Fetch and normalize asset_type from dictionary entry using loose key matching."""
    if not isinstance(entry, dict):
        return set()
    normalized = set()
    for key, val in entry.items():
        key_clean = re.sub(r'[^A-Za-z0-9]+', '', str(key)).lower()
        if key_clean in ("assettype", "assettypes", "type"):
            normalized |= _normalize_asset_type_values(val)
    return normalized


def _get_mechanical_entry(tag: str, asset_type: str = None):
    """Look up mechanical dictionary entry with composite key support (Tag|Type).
    
    Priority order:
    1. Exact composite key match (e.g., "PNL-100" matches "PNL-100|EL")
    2. Composite prefix match (e.g., "PNL-100" matches "PNL|EL")
    3. Legacy simple key match (e.g., "PNL-100" matches "PNL")
    """
    current_dict = _get_live_mechanical_dictionary()
    if not tag or not current_dict:
        return None
    
    # Default to EL context
    clean_types = _normalize_asset_type_values(asset_type or "EL")
    current_type = next(iter(clean_types)) if clean_types else "EL"
    clean_tag = tag.strip().upper()
    
    print(f"[EL-DICT-LOOKUP] UBC: '{clean_tag}', Type: '{current_type}'")
    
    # STEP 1: Try exact composite key match
    composite_key = f"{clean_tag}|{current_type}"
    if composite_key in current_dict:
        print(f"[EL-DICT-MATCH] Exact composite key: {composite_key}")
        return current_dict[composite_key]
    
    # STEP 2: Try composite prefix matching
    for key in sorted(current_dict.keys(), key=len, reverse=True):
        if '|' not in key:
            continue  # Skip simple keys in this pass
        
        try:
            tag_prefix, key_type = key.split('|', 1)
            if clean_tag.startswith(tag_prefix.upper()) and key_type.upper() == current_type:
                print(f"[EL-DICT-MATCH] Composite prefix: {key} (matched {clean_tag})")
                return current_dict[key]
        except ValueError:
            continue  # Skip malformed keys
    
    # STEP 3: Fall back to simple key matching (legacy support)
    for prefix in MECH_PREFIX_KEYS:
        if '|' in prefix:
            continue  # Skip composite keys in legacy matching
        
        if clean_tag.startswith(prefix.upper()):
            entry = current_dict.get(prefix)
            d_types = _get_entry_asset_type(entry)
            
            # Type compatibility check
            if clean_types and d_types and clean_types.isdisjoint(d_types):
                continue
            
            print(f"[EL-DICT-MATCH] Legacy simple key: {prefix} (matched {clean_tag})")
            return entry
    
    print(f"[EL-DICT-NO-MATCH] No dictionary match found for UBC: {clean_tag}, Type: {current_type}")
    return None


def _compose_desc_with_tag(description: str, tag: str) -> str:
    base = (description or "").strip()
    clean_tag = (tag or "").strip()
    if not base:
        return clean_tag
    if not clean_tag or clean_tag in base:
        return base
    return f"{base} - {clean_tag}"


def _is_ai_default_description(description, tag) -> bool:
    """Detect the legacy AI extraction default Description ("Panel - <tag>").
    The EL extractor used to hardcode every Description as ``Panel - <UBC tag>``
    regardless of tag prefix, which broke CDP/TX/ATS records. When the JSON's
    Description is exactly that placeholder, snapshot/restore must NOT preserve
    it -- the dictionary value should win instead. Anything else (including a
    user-typed override) is treated as authored content and preserved."""
    desc_clean = str(description or "").strip()
    if not desc_clean:
        return False
    tag_clean = str(tag or "").strip()
    if not tag_clean:
        return desc_clean.casefold() == "panel -"
    return desc_clean.casefold() == f"panel - {tag_clean}".casefold()


def _apply_mechanical_fallback(data: dict, ubc_tag: str, asset_type: str = None) -> bool:
    entry = _get_mechanical_entry(ubc_tag, asset_type)
    if not entry:
        return False
    attr = str(entry.get("attribute_set") or "").strip()
    asset_group = str(entry.get("asset_group") or "").strip()
    description = str(entry.get("description") or "").strip()
    main_asset = str(entry.get("main_asset") or "").strip()

    if attr:
        data["Attribute"] = attr
    if asset_group:
        data["Asset Group"] = asset_group
    if main_asset:
        data["Main Asset"] = main_asset
    if description or ubc_tag:
        data["Description"] = _compose_desc_with_tag(description, ubc_tag)
    return True


def find_image(qr: str, building: str, seq_tag: str):
    seq = seq_tag.replace('-', '').strip()
    base = f"{qr} {building} EL - {seq}"
    for ext in VALID_IMAGE_EXTS:
        candidate = os.path.join(IMG_DIR, base + ext)
        if os.path.exists(candidate):
            return os.path.basename(candidate)
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
    if value in (None, ''):
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


def _normalize_conf_max(value):
    if value in (None, ''):
        return None
    try:
        conf_max = int(float(value))
    except (TypeError, ValueError):
        return None
    return conf_max if conf_max in {25, 50, 75, 100} else None


def _matches_conf_max(item, conf_max):
    if conf_max is None:
        return True
    try:
        score = float(item.get("Avg_ai_conf"))
    except (TypeError, ValueError):
        return False
    return score <= conf_max

def _extract_avg_ai_conf(payload: dict):
    if not isinstance(payload, dict):
        return None

    direct = payload.get("Avg_ai_conf")
    if direct not in (None, ''):
        return direct

    scores = payload.get("confidence_scores")
    if not isinstance(scores, dict):
        return None

    values = []
    for field, score in scores.items():
        if field in {"Branch Panel", "Volts", "Location"}:
            continue
        try:
            values.append(float(score))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)

def _sanitize_qr_value(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (raw or "").strip()).upper()

def _qr_conflicts(new_qr: str, building: str) -> bool:
    filename = f"{new_qr}_EL_{building}.json"
    if os.path.exists(os.path.join(JSON_DIR, filename)):
        return True
    if not _connectable():
        return False
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            qr_col, _ = _resolve_qr_codes_columns()
            if qr_col:
                cur.execute(f'SELECT 1 FROM "{QR_CODES_TABLE}" WHERE "{qr_col}" = ? LIMIT 1', (new_qr,))
                if cur.fetchone(): return True
            cols = list(qrdb.table_columns(conn, QR_CODE_ASSETS_TABLE))
            qr_assets_col = next((c for c in QR_CODE_ASSETS_QR_COL_CANDIDATES if c in cols), None)
            if qr_assets_col:
                cur.execute(f'SELECT 1 FROM "{QR_CODE_ASSETS_TABLE}" WHERE "{qr_assets_col}" LIKE ? LIMIT 1', (f"{new_qr}%",))
                if cur.fetchone(): return True
    except Exception:
        return False
    return False

def _rename_asset_images(old_qr: str, new_qr: str, building: str):
    if not os.path.isdir(IMG_DIR): return
    for tag in ALL_SHOW:
        seq = tag.replace('-', '').strip()
        for ext in VALID_IMAGE_EXTS:
            old_name = f"{old_qr} {building} EL - {seq}{ext}"
            new_name = f"{new_qr} {building} EL - {seq}{ext}"
            old_path = os.path.join(IMG_DIR, old_name)
            new_path = os.path.join(IMG_DIR, new_name)
            if not os.path.exists(old_path): continue
            if os.path.exists(new_path): continue
            try:
                os.rename(old_path, new_path)
            except Exception as e:
                print(f"[WARN] Image rename failed ({old_name} -> {new_name}): {e}")

def _update_processed_json_log_filename(old_name: str, new_name: str):
    if not os.path.exists(PROCESSED_JSON_LOG_EL): return
    try:
        with open(PROCESSED_JSON_LOG_EL, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and old_name in data:
            data[new_name] = data.pop(old_name)
            atomic_write_json(PROCESSED_JSON_LOG_EL, data, indent=2)
    except Exception:
        pass

def _replace_qr_in_db(old_qr: str, new_qr: str):
    if not _connectable(): return
    targets = [
        (QR_CODES_TABLE, None, False),
        (QR_CODE_ASSETS_TABLE, None, True),
        ("sdi_dataset", "QR Code", False),
        ("sdi_dataset_EL", "QR Code", False),
        ("sdi_print_out", "QR Code", False),
        ("sdi_print_out_arch", "QR Code", False),
        ("process_type", "QR Code", False),
        ("json_files", "code", False),
    ]
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            for table, col, prefix in targets:
                cols = list(qrdb.table_columns(conn, table))
                if table == QR_CODES_TABLE:
                    qr_cols = _resolve_qr_codes_columns()
                    col = qr_cols[0] if qr_cols else None
                elif table == QR_CODE_ASSETS_TABLE:
                    col = next((c for c in QR_CODE_ASSETS_QR_COL_CANDIDATES if c in cols), None)
                if not col or col not in cols:
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
        print(f"[WARN] DB QR replace failed ({old_qr}->{new_qr}): {e}")


@lru_cache(maxsize=1)
def _connectable():
    return qrdb.is_postgres() or os.path.exists(DB_PATH)


def _qr_prefix_expr() -> str:
    # SQL expression returning the first space-separated token of code_assets (= QR prefix).
    # SQLite has INSTR; PostgreSQL doesn't but provides split_part.
    return ("split_part(code_assets, ' ', 1)" if qrdb.is_postgres()
            else "SUBSTR(code_assets, 1, INSTR(code_assets || ' ', ' ') - 1)")


@lru_cache(maxsize=1)
def _get_qr_codes_columns() -> list:
    if not _connectable():
        return []
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            return [c for c in qrdb.table_columns(conn, QR_CODES_TABLE) if c]
    except Exception as e:
        print(f"[WARN] Could not read QR_codes schema: {e}")
        return []


def _resolve_qr_codes_columns():
    columns = _get_qr_codes_columns()
    if not columns:
        return None, None
    lower_map = {c.lower(): c for c in columns}

    def pick(candidates, fallback_match):
        for cand in candidates:
            key = str(cand).lower()
            if key in lower_map:
                return lower_map[key]
        for col in columns:
            if fallback_match(col.lower()):
                return col
        return None

    qr_col = pick(QR_CODE_ID_COL_CANDIDATES, lambda lc: "qr" in lc and "code" in lc)
    loc_col = pick(QR_LOCATION_COL_CANDIDATES, lambda lc: "location" in lc or "space" in lc)
    return qr_col, loc_col


def _resolve_qr_codes_ai_column(columns: list) -> Optional[str]:
    if not columns:
        return None
    def norm(name: str) -> str:
        return name.lower().replace(" ", '').replace("_", '')
    normalized = {norm(col): col for col in columns}
    return normalized.get("aistatus")


@lru_cache(maxsize=512)
def _fetch_qr_code_location(qr_code_id: str) -> str:
    if not _connectable():
        return ""
    qr_code_id = (qr_code_id or "").strip()
    if not qr_code_id:
        return ""
    try:
        qr_col, loc_col = _resolve_qr_codes_columns()
        if not qr_col or not loc_col:
            print(f"[WARN] QR_codes lookup missing columns: qr={qr_col}, location={loc_col}")
            return ""
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            q = f'SELECT "{loc_col}" FROM "{QR_CODES_TABLE}" WHERE "{qr_col}" = ? LIMIT 1'
            cur.execute(q, (qr_code_id,))
            row = cur.fetchone()
            return (row[loc_col] or "").strip() if row else ""
    except Exception as e:
        print(f"[WARN] DB QR code location fetch failed for {qr_code_id}: {e}")
        return ""


def _fetch_capture_info(qr_code_id: str, building: str = "", discipline: str = "EL") -> dict:
    """Look up the latest capture user / date / hour from QR_code_assets for
    the given QR (optionally constrained by building + discipline). Returns
    {"user": str, "date": "YYYY-MM-DD", "hour": "HH:MM", "gps_coordinates": str}
    -- empty strings when nothing is on file. Used to surface the User Activity
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
                # PG: "user" is reserved keyword; "QR_code_assets"/"ID" mixed case need quoting.
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


def _apply_qr_location_fallback(data: dict, qr_code_id: str) -> bool:
    if not isinstance(data, dict):
        return False
    current_location = str(data.get("Location") or "").strip()
    if current_location:
        if data.get("Location") != current_location:
            data["Location"] = current_location
            return True
        return False
    qr_location = _fetch_qr_code_location(qr_code_id)
    if not qr_location:
        return False
    data["Location"] = qr_location
    return True


def _fetch_attribute_default_for_code(code_value: str) -> str:
    if not _connectable():
        return ""
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            q = (
                f'SELECT "{ATTRIBUTE_VAL_COL}" AS attr '
                f'FROM "{ATTRIBUTE_TABLE}" '
                f'WHERE "{ATTRIBUTE_CODE_COL}" = ? LIMIT 1'
            )
            cur.execute(q, (code_value,))
            row = cur.fetchone()
            return (row["attr"] or "").strip() if row else ""
    except Exception as e:
        print(f"[WARN] DB default attribute fetch failed: {e}")
        return ""


def get_qr_process_map():
    """
    Return a mapping of QR code to its highest Col_process value.
    Returns None if the table or columns are unavailable so filtering is skipped.
    """
    if not _connectable():
        return None
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            # qrdb.table_columns returns a list of column NAMES (was PRAGMA tuples).
            cols = set(qrdb.table_columns(conn, QR_CODE_ASSETS_TABLE))
            if not cols:
                return None
            cols_lc = {c.lower(): c for c in cols}
            qr_col = next(
                (cols_lc.get(c.lower()) for c in QR_CODE_ASSETS_QR_COL_CANDIDATES if c.lower() in cols_lc),
                None,
            )
            if not qr_col:
                qr_col = next((c for c in cols if "qr" in c.lower() and "code" in c.lower()), None)
            if not qr_col:
                return None

            process_col = cols_lc.get(QR_CODE_ASSETS_PROCESS_COL.lower())
            if not process_col:
                return None

            query = f'SELECT "{qr_col}", "{process_col}" FROM "{QR_CODE_ASSETS_TABLE}"'
            cur.execute(query)

            mapping = {}
            for row in cur.fetchall():
                q_raw = str(row[0]).strip() if row[0] is not None else ""
                if not q_raw:
                    continue
                q_clean = q_raw.split(None, 1)[0]
                try:
                    p_val = int(str(row[1]).strip())
                except Exception:
                    continue

                prev = mapping.get(q_clean)
                if prev is None or p_val > prev:
                    mapping[q_clean] = p_val
            return {k: str(v) for k, v in mapping.items()}
    except Exception as e:
        print(f"[WARN] Failed to fetch process map: {e}")
        return None


def get_qrs_with_process_value(target_value: str = "0"):
    process_map = get_qr_process_map()
    if process_map is None:
        return None
    target_text = str(target_value)
    return {qr for qr, proc in process_map.items() if proc == target_text}


def _fetch_el_asset_group_options() -> list:
    opts = []
    if not _connectable():
        return []
    
    prefixes = [
        "EL.24", "EL.21.306.4050", "EL.21.306.4057", 
        "EL.21.306.4145", "EL.21.308.4150", "EL.21.306.4063",
        "EL.20.307.4018", "EL.20.307.4032", "EL.20.307.4068",
        "EL.20.307.4140", "EL.20.307.4141", "EL.20.307.4142",
        "EL.21.308.4001"
    ]
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            like_clauses = [f'"{ASSET_GROUP_CLASS_COL}" LIKE ?' for _ in prefixes]
            or_clause = " OR ".join(like_clauses)
            q = (
                f'SELECT "{ASSET_GROUP_NAME_COL}" AS name '
                f'FROM "{ASSET_GROUP_TABLE}" '
                f'WHERE ({or_clause}) '
                f'AND "{ASSET_GROUP_LEVEL_COL}" != ? '
                f'ORDER BY "{ASSET_GROUP_NAME_COL}"'
            )
            params = [f"{p}%" for p in prefixes] + ['Level 3']
            cur.execute(q, params)
            opts = [(r["name"] or "").strip() for r in cur.fetchall() if (r["name"] or "").strip()]
    except Exception as e:
        print(f"[WARN] DB asset group fetch failed: {e}")
    return opts

def _desc_from_ubc_or_branch(ubc_tag: str, branch: str) -> str:
    tag = (ubc_tag or "").strip() or (branch or "").strip()
    if not tag:
        return "Panel"
    tag_upper = tag.upper()
    if tag_upper.startswith("TX"):
        return f"Transformer - {tag}"
    if tag_upper.startswith("CDP"):
        return f"Distribution - {tag}"
    if tag_upper.startswith("ATS"):
        return f"Transfer Switch - {tag}"
    return f"Panel - {tag}"

def _get_asset_group_from_tag(tag: str, asset_type: str = None) -> str:
    tag = (tag or "").strip().upper()
    mech_entry = _get_mechanical_entry(tag, asset_type)
    if mech_entry:
        mech_group = str(mech_entry.get("asset_group") or "").strip()
        if mech_group:
            return mech_group
    if tag.startswith("ATS"):
        return "Automatic Transfer Switches"
    if tag.startswith("CDP"):
        return "Other Service and Distribution"
    if tag.startswith("TX"):
        return "Interior Distribution Transformers"
    # 'T1' / 'T-1' unit naming: the dictionary's 'T-|EL' prefix entry already
    # classifies these as transformers, but its prefix match needs the hyphen
    # ('T1' misses 'T-'). Same digit-suffix anchor as
    # legacy_flow.is_legacy_transformer so TSBC/'T1A'/panel idents never match.
    if re.match(r"^T[-.\s]?\d+$", tag):
        return "Interior Distribution Transformers"
    return ASSET_GROUP_DEFAULT

def _get_desc_prefix_from_asset_group(asset_group: str) -> str:
    clean_group = (asset_group or "").strip()
    group_lower = clean_group.lower()
    if group_lower == "automatic transfer switches":
        return "Transfer Switch"
    if group_lower == "other service and distribution":
        return "Distribution"
    if group_lower == "interior distribution transformers":
        return "Transformer"
    if group_lower == "panels":
        return "Panel"
    return clean_group or "Panel"

def _compute_description_from_group(asset_group: str, tag: str) -> str:
    prefix = _get_desc_prefix_from_asset_group(asset_group)
    clean_tag = (tag or "").strip()
    if clean_tag:
        return f"{prefix} - {clean_tag}"
    return prefix

def _resolve_description(asset_group: str, tag: str, existing_desc) -> str:
    existing_text = str(existing_desc or "")
    if existing_text.strip():
        return existing_text
    return _compute_description_from_group(asset_group, tag)

def _db_existing_cols(conn) -> list:
    cur = conn.cursor()
    return list(qrdb.table_columns(conn, SDI_TABLE))


def _sdi_row_has_data(qr: str, building: str) -> bool:
    """True when an sdi_dataset_EL row for this QR+Building already holds any
    non-empty AI-captured content that must not be wiped by a placeholder
    re-sync. Returns False only when the row is missing or every content
    column is blank (placeholder-eligible).

    A prior version of this guard only checked UBC Asset Tag / Equipment ID;
    editing a tag to a value that zeroed both identity columns would then let
    the next placeholder sync overwrite the whole row with blanks. The guard
    now treats any of the major content columns as evidence of real data."""
    if not _connectable():
        return False
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.execute(
                f'SELECT 1 FROM "{SDI_TABLE}" '
                f'WHERE "QR Code"=? AND "Building"=? AND ('
                f'TRIM(COALESCE("UBC Asset Tag",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Equipment ID",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Branch Panel",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Supply From",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Volts",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Ampere",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Location",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Description",\'\')) <> \'\' '
                f'OR TRIM(COALESCE("Asset Group",\'\')) <> \'\' '
                # CAST keeps this valid on PG, where Avg_ai_conf is TEXT and
                # COALESCE(text, 0) is a type error (SQLite tolerated it).
                f'OR TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), \'\')) NOT IN (\'\', \'0\', \'0.0\')'
                f') LIMIT 1',
                (qr, building),
            )
            return cur.fetchone() is not None
    except qrdb.DatabaseError:
        return False

def _db_ensure_cols(conn, table: str, column_defs: dict[str, str]):
    existing = set(_db_existing_cols(conn) if table == SDI_TABLE else [])
    if not existing:
        existing = set(qrdb.table_columns(conn, table))
    if not existing:
        return

    cur = conn.cursor()
    for col_name, col_type in column_defs.items():
        if col_name not in existing:
            cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {col_type}')

def _db_table_has_column(table: str, column: str) -> bool:
    if not _connectable():
        return False
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            return column in qrdb.table_columns(conn, table)
    except Exception:
        return False

def _normalize_el_amperage_value(value: object) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.search(r"\d{1,4}", value)
    return match.group(0) if match else ""

def _get_el_amperage_value(sd: dict) -> str:
    if not isinstance(sd, dict):
        return ""
    # The review form edits ``Ampere``; ``Amperage Rating`` is the Planon-facing
    # alias that is synchronized from it. Prefer the editable field so a stale
    # alias cannot overwrite a reviewer correction during save.
    for key in ("Ampere", "Amperage Rating"):
        normalized = _normalize_el_amperage_value(sd.get(key))
        if normalized:
            return normalized
    return ""

def _get_el_equipment_id_value(tag_value) -> str:
    return str(tag_value or "").strip()

def _get_el_equipment_type_value(equipment_id_value, tag_value=None) -> str:
    source_value = str(equipment_id_value or "").strip() or str(tag_value or "").strip()
    return derive_electrical_equipment_type(source_value)

def _get_el_power_type_value(equipment_id_value, tag_value=None) -> str:
    source_value = str(equipment_id_value or "").strip() or str(tag_value or "").strip()
    return derive_electrical_power_type(source_value)

def _get_el_power_rating_pair(sd: dict) -> tuple[str, str]:
    if not isinstance(sd, dict):
        return "", ""
    return normalize_power_rating_pair(
        str(sd.get("Power Rating", '') or "").strip(),
        str(sd.get("Power Rating (UoM)", '') or "").strip(),
    )

EL_REVIEW_BASE_SCORING_FIELDS = ("UBC Asset Tag", "Volts")
EL_REVIEW_NON_TRANSFORMER_SCORING_FIELDS = EL_REVIEW_BASE_SCORING_FIELDS + ("Ampere", "Supply From")
EL_REVIEW_TRANSFORMER_SCORING_FIELDS = EL_REVIEW_BASE_SCORING_FIELDS + ("Power Rating", "Power Rating (UoM)")
EL_REVIEW_GENERAL_SCORING_FIELDS = ("UBC Asset Tag",) + EL_NAMEPLATE_FIELDS

def _el_review_scoring_fields(sd: dict) -> tuple[str, ...]:
    tag = str((sd or {}).get("UBC Asset Tag") or (sd or {}).get("Branch Panel") or "").strip()
    asset_group = _get_asset_group_from_tag(tag, (sd or {}).get("asset_type"))
    # A row may carry its dictionary-assigned group ('Main Transformers') even
    # when the tag heuristic above lands on the interior-distribution default.
    stored_group = str((sd or {}).get("Asset Group") or "").strip()
    # The stored group decides General vs Distribution: a reviewer-assigned
    # General group on a PNL-style tag must score against the nameplate set,
    # not the tag-derived Distribution default.
    if _el_form_variant(stored_group or asset_group) == "general":
        return EL_REVIEW_GENERAL_SCORING_FIELDS
    if asset_group in EL_TRANSFORMER_ASSET_GROUPS or stored_group in EL_TRANSFORMER_ASSET_GROUPS:
        return EL_REVIEW_TRANSFORMER_SCORING_FIELDS
    return EL_REVIEW_NON_TRANSFORMER_SCORING_FIELDS

def _normalize_review_confidence_score(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0

def _project_el_review_confidence_scores(sd: dict, existing_scores: dict | None) -> dict:
    scores = existing_scores if isinstance(existing_scores, dict) else {}
    projected = {}
    for field in _el_review_scoring_fields(sd):
        if str((sd or {}).get(field) or "").strip():
            projected[field] = _normalize_review_confidence_score(scores.get(field, 0))
        else:
            projected[field] = 0
    return projected

def _avg_review_confidence(scores: dict) -> float:
    values = []
    for raw in (scores or {}).values():
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return sum(values) / len(values) if values else 0.0

def _sync_el_review_quality_metadata(json_data: dict, structured: dict) -> None:
    fields = _el_review_scoring_fields(structured)
    if fields:
        filled = sum(1 for field in fields if str((structured or {}).get(field) or "").strip())
        json_data["completeness_score"] = (filled / len(fields)) * 100.0
    confidence_scores = _project_el_review_confidence_scores(
        structured,
        json_data.get("confidence_scores"),
    )
    json_data["confidence_scores"] = confidence_scores
    json_data["Avg_ai_conf"] = _avg_review_confidence(confidence_scores)

def _normalize_el_supply_from_lookup_value(value: object) -> str:
    normalized = normalize_el_supply_from_tag(value)
    return normalized or str(value or "").strip()

def _is_el_supply_from_manual(sd: dict) -> bool:
    return str((sd or {}).get("supply_from_manual_override") or "").strip() == "1"

def _get_el_supply_from_stored_value(sd: dict) -> str:
    raw_value = str((sd or {}).get("Supply From") or "").strip()
    if _is_el_supply_from_manual(sd):
        return raw_value
    return normalize_el_supply_from_tag(raw_value)

def _get_el_fed_from_amperage_value(conn, building: str, supply_from: str) -> str:
    # Sourced from the SLD (diagram side), not captured assets: blank until an
    # active SLD row exists for the feeder tag in this building.
    building_value = str(building or "").strip()
    supply_from_value = _normalize_el_supply_from_lookup_value(supply_from)
    if not building_value or not supply_from_value:
        return ""
    row = conn.execute(
        '''
        SELECT TRIM(COALESCE("Amperage Rating", ''))
        FROM "electrical_building_schema"
        WHERE UPPER(TRIM(COALESCE("Building", ''))) = UPPER(?)
          AND UPPER(TRIM(COALESCE("Equipment ID", ''))) = UPPER(?)
          AND TRIM(COALESCE("new_draw", '')) = 'TRUE'
        LIMIT 1
        ''',
        (building_value, supply_from_value),
    ).fetchone()
    if not row:
        return ""
    return str(row[0] or "").strip()

def _resolve_el_fed_from_amperage_value(building: str, supply_from: str) -> str:
    if not _connectable():
        return ""
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            return _get_el_fed_from_amperage_value(conn, building, supply_from)
    except Exception:
        return ""

def _get_el_fed_from_amperage_uom_value(fed_from_amperage_value) -> str:
    return "A" if str(fed_from_amperage_value or "").strip() else ""

def _apply_el_supply_from_formatting(value: object) -> str:
    return normalize_el_supply_from_tag(value)

def _get_el_fed_from_equipment_id_value(supply_from_value: object) -> str:
    return _apply_el_supply_from_formatting(supply_from_value)

def _compute_el_upstream_fields(data: dict, process: str):
    """Compute Equipment ID / Equipment Type / Power Type / Power Rating pair /
    Supply From / Fed From Equipment ID for a structured EL record, branching on
    the already-resolved Buildings.Process (`process`) so the Legacy branch never
    runs the standard derivations, which would pre-fill/force-overwrite the exact
    fields legacy_flow.apply_legacy_rules owns (task-6 review Finding C):
    Equipment ID is always non-blank once `_get_el_equipment_id_value` runs (it's
    just the trimmed tag), so the legacy ident-normalization rule could never fire;
    the standard system-code parser misreads legacy tags like `PNL-UPS/CM` as
    Power Type "S"; and `normalize_el_supply_from_tag` strips the word "MAIN",
    destroying the MDC/DCC discriminator `normalize_legacy_supply_from` needs.

    Returns (equipment_id, equipment_type, power_type, power_rating,
    power_rating_uom, supply_from, fed_from_equipment_id). Does not mutate
    `data`; callers assign the returned `supply_from` into `data["Supply From"]`
    themselves (matching the pre-existing call-site pattern).
    """
    power_rating_value, power_rating_uom = _get_el_power_rating_pair(data)
    if process == "Legacy":
        raw_tag = str(data.get("UBC Asset Tag") or "").strip()
        parsed = el_legacy_flow.parse_legacy_ident(raw_tag)
        # Final review Finding C1: prefer the Equipment ID already stored in
        # `data` (extraction/apply_legacy_rules populates it correctly, e.g.
        # "DCC #1") and only fall back to tag-based re-derivation when blank.
        # parse_legacy_ident only understands "PNL <ident>"/"MCC2"-style
        # legacy labels, not the composed X-tag format ("DCC-2XXD1"), so
        # falling back unconditionally on a populated X-tag silently clobbered
        # Equipment ID with the raw tag itself, breaking the SLD cross-
        # reference (peers store Fed From Equipment ID = "DCC #1").
        stored_equipment_id = str(data.get("Equipment ID") or "").strip()
        equipment_id_value = stored_equipment_id or (parsed["equipment_id"] if parsed else raw_tag)
        stored_equipment_type = str(data.get("Equipment Type") or "").strip()
        if stored_equipment_type:
            equipment_type_value = stored_equipment_type
        else:
            _, equipment_type_value = el_legacy_flow.derive_legacy_equipment_metadata(equipment_id_value)
        # Legacy: Supply From is kept exactly as captured/stored — the standard
        # normalizer below strips "MAIN" and cannot distinguish MDC from DCC.
        supply_from_value = str(data.get("Supply From") or "").strip()
        fed_from = el_legacy_flow.normalize_legacy_supply_from(supply_from_value)
        fed_from_equipment_id_value = fed_from["fed_from_id"]
        # Same blank-first pattern for Power Type: parsed["system_hint"] is
        # unavailable/wrong for X-tags, and extraction may already have
        # corroborated a real Power Type.
        stored_power_type = str(data.get("Power Type") or "").strip()
        if stored_power_type:
            power_type_value = stored_power_type
        else:
            power_type_value = (
                el_legacy_flow.corroborated_power_type(parsed["system_hint"], fed_from) if parsed else ""
            )
        return (equipment_id_value, equipment_type_value, power_type_value,
                power_rating_value, power_rating_uom, supply_from_value, fed_from_equipment_id_value)
    equipment_id_value = _get_el_equipment_id_value(data.get("UBC Asset Tag"))
    equipment_type_value = _get_el_equipment_type_value(equipment_id_value, data.get("UBC Asset Tag"))
    power_type_value = _get_el_power_type_value(equipment_id_value, data.get("UBC Asset Tag"))
    supply_from_value = _get_el_supply_from_stored_value(data)
    fed_from_equipment_id_value = _get_el_fed_from_equipment_id_value(supply_from_value)
    return (equipment_id_value, equipment_type_value, power_type_value,
            power_rating_value, power_rating_uom, supply_from_value, fed_from_equipment_id_value)

def _ensure_el_amperage_columns(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Amperage Rating": "TEXT", "Amperage Rating (UoM)": "TEXT"})
    cur = conn.cursor()
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Amperage Rating" = TRIM(COALESCE("Ampere", ''))
         WHERE TRIM(COALESCE("Amperage Rating", '')) = ''
           AND TRIM(COALESCE("Ampere", '')) != ''
        '''
    )
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Amperage Rating (UoM)" = CASE
               WHEN TRIM(COALESCE("Amperage Rating", '')) != '' THEN 'AMP'
               ELSE ''
           END
         WHERE COALESCE("Amperage Rating (UoM)", '') != CASE
               WHEN TRIM(COALESCE("Amperage Rating", '')) != '' THEN 'AMP'
               ELSE ''
           END
        '''
    )

def _get_el_voltage_uom_value(voltage_value: object) -> str:
    return "VLT" if str(voltage_value or "").strip() else ""

# Canonical storage keeps the bare voltage value ("208/120"); the unit is
# carried in "Voltage Rating (UoM)" as VLT. Legacy JSON Volts and dictionary
# entries may still carry unit letters ("208/120V", "600V-208Y/120V").
_EL_VOLTAGE_UNIT_SUFFIX_RE = re.compile(r"(?<=\d)\s*V(?:AC|DC|OLTS?)?\b", re.IGNORECASE)

def _strip_el_voltage_unit_letters(value: object) -> str:
    return _EL_VOLTAGE_UNIT_SUFFIX_RE.sub("", str(value or "").strip()).strip()

def _ensure_el_voltage_columns(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Voltage Rating": "TEXT", "Voltage Rating (UoM)": "TEXT"})
    cur = conn.cursor()
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Voltage Rating" = TRIM(COALESCE("Volts", ''))
         WHERE TRIM(COALESCE("Voltage Rating", '')) = ''
           AND TRIM(COALESCE("Volts", '')) != ''
        '''
    )
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Volts" = TRIM(COALESCE("Voltage Rating", ''))
         WHERE TRIM(COALESCE("Volts", '')) = ''
           AND TRIM(COALESCE("Voltage Rating", '')) != ''
        '''
    )
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Voltage Rating (UoM)" = CASE
               WHEN TRIM(COALESCE("Voltage Rating", '')) != '' THEN 'VLT'
               ELSE ''
           END
         WHERE COALESCE("Voltage Rating (UoM)", '') != CASE
               WHEN TRIM(COALESCE("Voltage Rating", '')) != '' THEN 'VLT'
               ELSE ''
           END
        '''
    )

def _ensure_el_equipment_columns(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Equipment ID": "TEXT", "Equipment Type": "TEXT"})
    cur = conn.cursor()
    # Backend-conditional: PG has no rowid; use ctid (UPDATE WHERE needs ::tid cast).
    rowid_col = "ctid" if qrdb.is_postgres() else "rowid"
    rowid_where = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Equipment ID" = TRIM(COALESCE("UBC Asset Tag", ''))
         WHERE TRIM(COALESCE("Equipment ID", '')) = ''
           AND TRIM(COALESCE("UBC Asset Tag", '')) != ''
        '''
    )
    rows = cur.execute(
        f'''
        SELECT
            {rowid_col} AS _rowid,
            "Equipment Type",
            "Equipment ID",
            "UBC Asset Tag"
        FROM "{SDI_TABLE}"
        '''
    ).fetchall()
    for rowid, current_type, equipment_id, ubc_tag in rows:
        desired_type = _get_el_equipment_type_value(equipment_id, ubc_tag)
        if str(current_type or "").strip() or not desired_type:
            continue
        cur.execute(
            f'UPDATE "{SDI_TABLE}" SET "Equipment Type" = ? WHERE {rowid_where}',
            (desired_type, rowid),
        )

def _ensure_el_power_type_column(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Power Type": "TEXT"})
    cur = conn.cursor()
    rowid_col = "ctid" if qrdb.is_postgres() else "rowid"
    rowid_where = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
    rows = cur.execute(
        f'''
        SELECT
            {rowid_col} AS _rowid,
            "Power Type",
            "Equipment ID",
            "UBC Asset Tag"
        FROM "{SDI_TABLE}"
        '''
    ).fetchall()
    for rowid, current_power_type, equipment_id, ubc_tag in rows:
        desired_power_type = _get_el_power_type_value(equipment_id, ubc_tag)
        if str(current_power_type or "").strip() or not desired_power_type:
            continue
        cur.execute(
            f'UPDATE "{SDI_TABLE}" SET "Power Type" = ? WHERE {rowid_where}',
            (desired_power_type, rowid),
        )

def _ensure_el_power_rating_columns(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Power Rating": "TEXT", "Power Rating (UoM)": "TEXT"})
    cur = conn.cursor()
    rowid_col = "ctid" if qrdb.is_postgres() else "rowid"
    rowid_where = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
    rows = cur.execute(
        f'''
        SELECT
            {rowid_col} AS _rowid,
            "Power Rating",
            "Power Rating (UoM)"
        FROM "{SDI_TABLE}"
        '''
    ).fetchall()
    for rowid, current_rating, current_uom in rows:
        desired_rating, desired_uom = normalize_power_rating_pair(current_rating, current_uom)
        if str(current_rating or "").strip() == desired_rating and str(current_uom or "").strip() == desired_uom:
            continue
        cur.execute(
            f'UPDATE "{SDI_TABLE}" SET "Power Rating" = ?, "Power Rating (UoM)" = ? WHERE {rowid_where}',
            (desired_rating, desired_uom, rowid),
        )

def _ensure_el_fed_from_equipment_id_column(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Fed From Equipment ID": "TEXT"})
    cur = conn.cursor()
    rowid_col = "ctid" if qrdb.is_postgres() else "rowid"
    rowid_where = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
    # Legacy guard (2026-07-29, post-deploy hotfix): unlike the sibling ensure-
    # backfills (blank-fill only), this one force-rewrites every row to keep
    # "Fed From Equipment ID" in lockstep with the STANDARD derivation from
    # "Supply From". Legacy-building rows store legacy-composed identifiers
    # ("MDC", "DCC #1") whose "Supply From" ("MDC via TX T1") the standard
    # normalizer mangles ("MDC-VIA", "DCC"), so rows in Legacy buildings are
    # skipped — their Fed From Equipment ID is owned by the legacy flow
    # (extraction + apply_legacy_rules) and upserted as-is by
    # _sync_db_from_structured. If Buildings.Process is unreadable (e.g. a
    # pre-cutover SQLite file without the column), the set stays empty and the
    # pre-existing behavior applies unchanged — environments without the
    # Process column cannot contain legacy-composed rows.
    # Probe via metadata first: a failed SELECT would abort the whole
    # PostgreSQL transaction (poisoning the caller's connection), while
    # qrdb.table_columns never raises mid-transaction for a missing column.
    legacy_buildings = set()
    try:
        if "Process" in set(qrdb.table_columns(conn, "Buildings")):
            legacy_buildings = {
                str(code or "").strip()
                for (code,) in cur.execute(
                    'SELECT "Code" FROM "Buildings" WHERE "Process" = ?', ("Legacy",)
                ).fetchall()
            }
    except Exception:
        legacy_buildings = set()
    rows = cur.execute(
        f'''
        SELECT {rowid_col}, "Building", "Supply From", "Fed From Equipment ID"
        FROM "{SDI_TABLE}"
        '''
    ).fetchall()
    for rowid, building, current_supply, current_fed_from in rows:
        if str(building or "").strip() in legacy_buildings:
            continue
        desired_fed_from = _get_el_fed_from_equipment_id_value(current_supply)
        if str(current_fed_from or "").strip() == desired_fed_from:
            continue
        cur.execute(
            f'''
            UPDATE "{SDI_TABLE}"
               SET "Fed From Equipment ID" = ?
             WHERE {rowid_where}
            ''',
            (desired_fed_from, rowid),
        )

def _ensure_el_fed_from_amperage_column(conn) -> None:
    _db_ensure_cols(conn, SDI_TABLE, {"Fed From Amperage Rating": "TEXT", "Fed From Amperage Rating (UoM)": "TEXT"})
    cur = conn.cursor()
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Fed From Amperage Rating" = '',
               "Fed From Amperage Rating (UoM)" = ''
         WHERE TRIM(COALESCE("Supply From", '')) = ''
           AND TRIM(COALESCE("Fed From Equipment ID", '')) = ''
           AND TRIM(COALESCE("Fed From Amperage Rating", '')) != ''
        '''
    )
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}" AS child
           SET "Fed From Amperage Rating" = COALESCE((
               SELECT TRIM(COALESCE(parent."Amperage Rating", ''))
               FROM "electrical_building_schema" AS parent
               WHERE UPPER(TRIM(COALESCE(parent."Building", ''))) = UPPER(TRIM(COALESCE(child."Building", '')))
                 AND UPPER(TRIM(COALESCE(parent."Equipment ID", ''))) = UPPER(TRIM(COALESCE(NULLIF(child."Fed From Equipment ID", ''), child."Supply From", '')))
                 AND TRIM(COALESCE(parent."new_draw", '')) = 'TRUE'
               LIMIT 1
           ), '')
         WHERE TRIM(COALESCE(child."Supply From", '')) != ''
            OR TRIM(COALESCE(child."Fed From Equipment ID", '')) != ''
        '''
    )
    cur.execute(
        f'''
        UPDATE "{SDI_TABLE}"
           SET "Fed From Amperage Rating (UoM)" = CASE
               WHEN TRIM(COALESCE("Fed From Amperage Rating", '')) != '' THEN 'A'
               ELSE ''
           END
         WHERE COALESCE("Fed From Amperage Rating (UoM)", '') != CASE
               WHEN TRIM(COALESCE("Fed From Amperage Rating", '')) != '' THEN 'A'
               ELSE ''
           END
        '''
    )

def _db_upsert_el_row(conn, row: dict):
    all_cols = [
        "QR Code", "Building", "Description", "UBC Asset Tag", "Equipment ID", "Equipment Type", "Branch Panel", "Amperage Rating", "Amperage Rating (UoM)", "Ampere",
        "Power Type", "Power Rating", "Power Rating (UoM)", "Supply From", "Fed From Equipment ID", "Fed From Amperage Rating", "Fed From Amperage Rating (UoM)", "Volts", "Voltage Rating", "Voltage Rating (UoM)", "Location", "Asset Group", "Attribute", "Approved", "Flagged",
        "Avg_ai_conf", "Main Asset",
        "Manufacturer", "Model", "Serial Number", "Year",
        "Capacity", "Capacity (UoM)"
    ]
    existing = _db_existing_cols(conn)
    if not existing:
        return "error"
    cur = conn.cursor()
    set_cols = [c for c in all_cols if c in existing and c not in ("QR Code", "Building")]
    if set_cols:
        set_part = ", ".join([f'"{c}"=?' for c in set_cols])
        sql_upd = f'''
            UPDATE "{SDI_TABLE}"
               SET {set_part}
             WHERE "QR Code"=? AND "Building"=?
        '''
        params_upd = [row.get(c, '') for c in set_cols] + [row.get("QR Code", ''), row.get("Building", '')]
        cur.execute(sql_upd, params_upd)
        if cur.rowcount and cur.rowcount > 0:
            return "updated"
    ins_cols = [c for c in all_cols if c in existing]
    placeholders = ",".join(["?"] * len(ins_cols))
    sql_ins = f'''
        INSERT INTO "{SDI_TABLE}" ({",".join(f'"{c}"' for c in ins_cols)})
        VALUES ({placeholders})
    '''
    cur.execute(sql_ins, [row.get(c, '') for c in ins_cols])
    return "inserted"

def _sync_db_from_structured(qr: str, building: str, sd: dict, asset_type: str = None, avg_ai_conf=None, process: str = None):
    _coerce_packaged_approval(qr, sd)
    ubc_final = (sd.get("UBC Asset Tag") or "").strip()
    branch = (sd.get("Branch Panel") or "").strip()
    if not ubc_final:
        ubc_final = branch
    # Asset Group preservation is gated on the persisted asset_group_manual
    # flag: only restore the user's saved value when the flag is "1". When it
    # is "0" / missing the saved value is treated as auto-derived and the
    # dictionary's current value flows into the DB upsert. The placeholder
    # bootstrap path (sd={}) carries blanks here, so it is unaffected.
    # Description still uses the legacy "Panel - <tag>" placeholder detector.
    asset_group_manual_persisted = str(sd.get("asset_group_manual") or "").strip() == "1"
    pre_dict_asset_group = (sd.get("Asset Group") or "").strip()
    pre_dict_description = str(sd.get("Description") or "")
    pre_dict_desc_is_placeholder = _is_ai_default_description(pre_dict_description, sd.get("UBC Asset Tag") or sd.get("Branch Panel"))
    # Legacy (invariant 6): _apply_tag_dictionary_first runs
    # _clear_legacy_tag_derived_location, which blanks a Location that happens to
    # equal the STANDARD tag-derived value -- "Level 3" for a digit-ending legacy
    # ident like PNL-EM3. On the Legacy path that match is a coincidence, so a
    # reviewer-entered Location would be erased straight into sdi_dataset_EL /
    # Planon here. Snapshot before, restore after; both lines are gated on
    # process == "Legacy" so the shared helper and the Standard path are untouched.
    _legacy_location_snapshot = el_legacy_flow.snapshot_reviewer_location(sd) if process == "Legacy" else ""
    tag_for_group, _ = _apply_tag_dictionary_first(sd, asset_type)
    if process == "Legacy":
        el_legacy_flow.restore_reviewer_location(sd, _legacy_location_snapshot)
    if pre_dict_asset_group and asset_group_manual_persisted:
        sd["Asset Group"] = pre_dict_asset_group
    if pre_dict_description.strip() and not pre_dict_desc_is_placeholder:
        sd["Description"] = pre_dict_description
    if not tag_for_group:
        tag_for_group = ubc_final
    
    volts = (sd.get("Volts") or "").strip()
    location = (sd.get("Location") or "").strip()
    if not location and _apply_qr_location_fallback(sd, qr):
        location = (sd.get("Location") or "").strip()

    # Prefer dictionary voltage unless a reviewer explicitly overrode Volts.
    # Location is sourced from QR_codes when blank; it is no longer derived from the tag.
    volts_manual = str(sd.get("volts_manual_override") or "").strip() == "1"
    if tag_for_group and not (volts_manual and volts):
        d_volts, _ = _derive_volts_loc(tag_for_group)
        if d_volts:
            volts = d_volts
    voltage_rating_value = _strip_el_voltage_unit_letters(volts)
    voltage_rating_uom = _get_el_voltage_uom_value(voltage_rating_value)

    attr = (sd.get("Attribute") or "").strip() or "Electrical"
    approved_db = "1" if (sd.get("Approved") or "").strip() == "True" else "0"   # PG CHECK requires '0'/'1'
    flagged_db = "1" if (sd.get("Flagged") or "").strip() == "true" else "0"
    asset_group_val = sd.get("Asset Group") or _get_asset_group_from_tag(tag_for_group, asset_type)
    desc_input = sd.get("Description")
    description_val = _resolve_description(asset_group_val, tag_for_group, desc_input)
    amperage_value = _get_el_amperage_value(sd)
    power_rating_value, power_rating_uom = _get_el_power_rating_pair(sd)
    if process == "Legacy":
        # Legacy: apply_legacy_rules (called from save_review before this sync)
        # already owns Equipment ID / Equipment Type / Power Type / Fed From
        # Equipment ID -- upsert the structured values as-is instead of
        # re-deriving them with the standard tag-based helpers immediately
        # before the write, which would negate field-gap rules 1/8 at this
        # exact write path and silently persist standard-derived values (e.g.
        # "PANEL EPH" instead of "PNL-EPH") to sdi_dataset_EL / Planon export
        # (task-6 review Finding F). Supply From is likewise kept exactly as
        # stored/submitted (never run through the standard MAIN-stripping
        # normalizer). Blank stays blank -- no new derivations here (rule 9).
        equipment_id_value = (sd.get("Equipment ID") or "").strip()
        equipment_type_value = (sd.get("Equipment Type") or "").strip()
        power_type_value = (sd.get("Power Type") or "").strip()
        supply_from_value = (sd.get("Supply From") or "").strip()
        fed_from_equipment_id_value = (sd.get("Fed From Equipment ID") or "").strip()
    else:
        # Standard path, byte-identical (also the default when `process` is
        # None -- e.g. callers without process context, see review_apps.rules.md
        # "EL Legacy Flow Rules").
        equipment_id_value = _get_el_equipment_id_value(ubc_final)
        equipment_type_value = _get_el_equipment_type_value(equipment_id_value, ubc_final)
        power_type_value = _get_el_power_type_value(equipment_id_value, ubc_final)
        supply_from_value = _get_el_supply_from_stored_value(sd)
        fed_from_equipment_id_value = _get_el_fed_from_equipment_id_value(supply_from_value)

    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        _db_ensure_cols(conn, SDI_TABLE, {"Avg_ai_conf": "REAL", "Main Asset": "TEXT"})
        _ensure_el_equipment_columns(conn)
        _ensure_el_power_type_column(conn)
        _ensure_el_amperage_columns(conn)
        _ensure_el_voltage_columns(conn)
        _ensure_el_power_rating_columns(conn)
        _ensure_el_fed_from_equipment_id_column(conn)
        _ensure_el_fed_from_amperage_column(conn)
        fed_from_amperage_value = _get_el_fed_from_amperage_value(conn, building, fed_from_equipment_id_value or supply_from_value)
        fed_from_amperage_uom = _get_el_fed_from_amperage_uom_value(fed_from_amperage_value)
        # Nameplate columns are populated only for General (non-distribution)
        # rows; Distribution rows are kept blank by contract. The JSON keeps
        # the values either way, so reclassifying back to General re-syncs
        # them without loss.
        is_general = _el_form_variant(asset_group_val) == "general"
        row = {
            "QR Code": qr,
            "Building": building,
            "Description": description_val,
            "UBC Asset Tag": ubc_final,
            "Equipment ID": equipment_id_value,
            "Equipment Type": equipment_type_value,
            "Branch Panel": branch,
            "Amperage Rating": amperage_value,
            "Amperage Rating (UoM)": "AMP" if amperage_value else "",
            "Ampere": amperage_value,
            "Power Type": power_type_value,
            "Power Rating": power_rating_value,
            "Power Rating (UoM)": power_rating_uom,
            "Supply From": supply_from_value,
            "Fed From Equipment ID": fed_from_equipment_id_value,
            "Fed From Amperage Rating": fed_from_amperage_value,
            "Fed From Amperage Rating (UoM)": fed_from_amperage_uom,
            "Volts": voltage_rating_value,
            "Voltage Rating": voltage_rating_value,
            "Voltage Rating (UoM)": voltage_rating_uom,
            "Location": location,
            "Asset Group": asset_group_val,
            "Attribute": attr,
            "Approved": approved_db,
            "Flagged": flagged_db,
            "Avg_ai_conf": _normalize_avg_ai_conf(avg_ai_conf)[0],
            "Main Asset": (sd.get("Main Asset") or "").strip(),
            "Manufacturer": (sd.get("Manufacturer") or "").strip() if is_general else "",
            "Model": (sd.get("Model") or "").strip() if is_general else "",
            "Serial Number": (sd.get("Serial Number") or "").strip() if is_general else "",
            "Year": (sd.get("Year") or "").strip() if is_general else "",
            "Capacity": (sd.get("Capacity") or "").strip() if is_general else "",
            "Capacity (UoM)": (sd.get("Capacity (UoM)") or "").strip() if is_general else "",
        }
        _db_upsert_el_row(conn, row)
        _ensure_el_power_type_column(conn)
        _ensure_el_fed_from_amperage_column(conn)
        conn.commit()

def _db_upsert_qr_approved(qr_code_id: str, approved_text: str):
    if not _connectable():
        return
    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO "{QR_CODES_TABLE}" ("{QR_CODE_ID_COL}", "{QR_APPROVED_COL}")
            VALUES (?, ?)
            ON CONFLICT("{QR_CODE_ID_COL}") DO UPDATE SET
                "{QR_APPROVED_COL}" = excluded."{QR_APPROVED_COL}";
        """, (qr_code_id, approved_text))
        conn.commit()

def _auto_register_qr_code(qr: str, process_value: str = "0"):
    """Auto-register QR code in QR_code_assets if not already present.
    
    Checks both exact match and prefix match (e.g., '0000184404 198 EL - 0')
    to avoid creating duplicate entries when full-format entries already exist.
    """
    if not _connectable():
        return
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            # Check for BOTH exact match AND prefix match (full format entries)
            cur.execute(
                f'SELECT 1 FROM "{QR_CODE_ASSETS_TABLE}" WHERE "code_assets" = ? OR "code_assets" LIKE ? LIMIT 1',
                (qr, qr + " %"),
            )
            if not cur.fetchone():
                cur.execute(
                    f'INSERT INTO "{QR_CODE_ASSETS_TABLE}" ("code_assets", "Col_process") VALUES (?, ?)',
                    (qr, process_value),
                )
                conn.commit()
    except Exception as e:
        print(f"[WARN] Failed to auto-register QR code '{qr}': {e}")

def _db_toggle_qr_sdi(qr_code_id: str):
    if not _connectable():
        raise Exception("Database not accessible")
    new_val = 0
    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT "sdi" FROM "{QR_CODES_TABLE}" WHERE "{QR_CODE_ID_COL}" = ?', (qr_code_id,))
        row = cur.fetchone()
        current_val = 0
        if row:
            current_val = 1 if (row[0] == 1 or row[0] == "1") else 0
        new_val = 0 if current_val == 1 else 1
        cur.execute(f"""
            INSERT INTO "{QR_CODES_TABLE}" ("{QR_CODE_ID_COL}", "sdi")
            VALUES (?, ?)
            ON CONFLICT("{QR_CODE_ID_COL}") DO UPDATE SET
                "sdi" = excluded."sdi";
        """, (qr_code_id, new_val))
        new_process = "2" if new_val == 1 else "0"
        like_pattern = qr_code_id + "%"
        cur.execute(
            f'UPDATE "{QR_CODE_ASSETS_TABLE}" SET "{QR_CODE_ASSETS_PROCESS_COL}" = ? WHERE "code_assets" LIKE ?',
            (new_process, like_pattern),
        )
        if cur.rowcount == 0:
            cur.execute(
                f'INSERT INTO "{QR_CODE_ASSETS_TABLE}" ("code_assets", "Col_process") VALUES (?, ?) '
                f'ON CONFLICT("code_assets") DO UPDATE SET "Col_process"=excluded."Col_process"',
                (qr_code_id, new_process),
            )
        conn.commit()
    return new_val

def get_qr_sdi_states():
    states = {}
    if not _connectable():
        return states
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            columns = list(qrdb.table_columns(conn, QR_CODES_TABLE))
            if "sdi" not in columns:
                return states
            cur.execute(f'SELECT "{QR_CODE_ID_COL}", "sdi" FROM "{QR_CODES_TABLE}"')
            for row in cur.fetchall():
                qr_id = str(row[QR_CODE_ID_COL]).strip()
                val_raw = row["sdi"]
                val = 1 if (val_raw == 1 or val_raw == "1") else 0
                if qr_id:
                    states[qr_id] = val
    except Exception as e:
        print(f"[WARN] Access to DB failed for SDI states: {e}")
    return states

def get_qr_ai_status_map():
    states = {}
    if not _connectable():
        return states
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            columns = _get_qr_codes_columns()
            if not columns:
                return states
            qr_col, _ = _resolve_qr_codes_columns()
            ai_col = _resolve_qr_codes_ai_column(columns)
            if not qr_col or not ai_col:
                return states
            cur.execute(f'SELECT "{qr_col}", "{ai_col}" FROM "{QR_CODES_TABLE}"')
            for row in cur.fetchall():
                qr_id = str(row[qr_col]).strip()
                val_raw = row[ai_col]
                val = "1" if (str(val_raw) == "1") else "0"
                if qr_id:
                    states[qr_id] = val
    except Exception as e:
        print(f"[WARN] Access to DB failed for AI Status: {e}")
    return states

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
                    if r[0]: dates[str(r[0]).strip()] = str(r[1] or "").strip()
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
            # Backend-conditional position fn (instr is SQLite-only; PG uses strpos).
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
    qr_dates = get_qr_dates()
    capture_notes_map = get_qr_capture_notes()
    process_map = get_qr_process_map()
    ai_status_map = get_qr_ai_status_map()
    captured_by_map = get_qr_captured_by()
    if process_map is None:
        process_map = {}

    for filename in os.listdir(JSON_DIR):
        if not filename.endswith(".json") or filename.endswith("_raw_ocr.json"):
            continue
        m = JSON_NAME_RE.match(filename)
        if not m:
            continue
        qr, building = m.groups()
        doc_id = filename[:-5]
        try:
            current_status = process_map.get(qr)
            if current_status in (None, ''):
                _auto_register_qr_code(qr, "0")
                current_status = "0"
                process_map[qr] = current_status

            if str(current_status) != str(process_target):
                continue
            with open(os.path.join(JSON_DIR, filename), 'r', encoding='utf-8') as f:
                raw = json.load(f)
            data = raw.get("structured_data") or {}
            
            # Extract and format Capture Date
            capture_timestamp = qr_dates.get(str(raw.get("qr_code") or data.get("qr_code")).strip(), '')
            if capture_timestamp:
                # Format to YYYY-MM-DD HH:MM
                data["Capture Date"] = capture_timestamp[:16]
            else:
                data["Capture Date"] = ""

            if not isinstance(data, dict):
                continue

            # Legacy gate: looked up once per record and reused for every
            # downstream derivation choice below (both the upstream field
            # computation here and the dictionary-priority call further down).
            # Non-request/batch path (no Flask request context to flash into):
            # a blank/missing Buildings.Process stops just this record and logs
            # a warning (invariant: no silent default), then moves on.
            with qrdb.get_connection(sqlite_path=DB_PATH) as _bp_conn:
                try:
                    _process = el_legacy_flow.get_building_process(_bp_conn, building)
                except el_legacy_flow.BuildingProcessError as exc:
                    print(f"[WARN] Skipping {filename}: {exc}")
                    continue

            amperage_value = _get_el_amperage_value(data)
            (equipment_id_value, equipment_type_value, power_type_value,
             power_rating_value, power_rating_uom, supply_from_value,
             fed_from_equipment_id_value) = _compute_el_upstream_fields(data, _process)
            data["Supply From"] = supply_from_value
            fed_from_amperage_value = _resolve_el_fed_from_amperage_value(building, data.get("Supply From"))
            fed_from_amperage_uom = _get_el_fed_from_amperage_uom_value(fed_from_amperage_value)
            data["Ampere"] = amperage_value
            data["Equipment ID"] = equipment_id_value
            data["Equipment Type"] = equipment_type_value
            data["Power Type"] = power_type_value
            data["Power Rating"] = power_rating_value
            data["Power Rating (UoM)"] = power_rating_uom
            data["Fed From Equipment ID"] = fed_from_equipment_id_value
            data["Fed From Amperage Rating"] = fed_from_amperage_value
            data["Fed From Amperage Rating (UoM)"] = fed_from_amperage_uom
            data["Amperage Rating (UoM)"] = "AMP" if amperage_value else ""
            keep_blank = [
                "UBC Asset Tag", "Equipment ID", "Equipment Type", "Branch Panel", "Ampere", "Supply From", "Volts", "Location",
                "Power Type", "Power Rating", "Power Rating (UoM)", "Fed From Equipment ID", "Fed From Amperage Rating", "Fed From Amperage Rating (UoM)", "Attribute", "Approved", "Asset Group", "Description", "Amperage Rating (UoM)"
            ]
            for k in keep_blank:
                data.setdefault(k, '')
            data.setdefault("Flagged", "false")
            asset_type_val = raw.get("asset_type")
            # Asset Group preservation is gated on the persisted asset_group_manual
            # flag (see GET handler comment). load_json_items feeds dashboard list
            # rows; without the gate, retroactively-added dictionary entries (e.g.
            # SWBD|EL) would never reach the column for old AI-default rows.
            asset_group_manual_persisted = str(data.get("asset_group_manual") or "").strip() == "1"
            pre_dict_asset_group = (data.get("Asset Group") or "").strip()
            pre_dict_description = str(data.get("Description") or "")
            pre_dict_desc_is_placeholder = _is_ai_default_description(pre_dict_description, data.get("UBC Asset Tag") or data.get("Branch Panel"))
            tag_for_group, dict_applied = _apply_tag_dictionary_first(data, asset_type_val)
            if pre_dict_asset_group and asset_group_manual_persisted:
                data["Asset Group"] = pre_dict_asset_group
            if pre_dict_description.strip() and not pre_dict_desc_is_placeholder:
                data["Description"] = pre_dict_description
            if not tag_for_group:
                tag_for_group = (data.get("UBC Asset Tag") or "").strip() or (data.get("Branch Panel") or "").strip()
            if not data.get("Attribute"):
                data["Attribute"] = "Electrical"

            if not data.get("Asset Group"):
                data["Asset Group"] = _get_asset_group_from_tag(tag_for_group, asset_type_val)

            if tag_for_group:
                # Gate: reuses the `_process` looked up once above.
                if _process == "Legacy":
                    el_legacy_flow.apply_legacy_rules(data)
                else:
                    _apply_dictionary_priority(data, tag_for_group)   # standard path, byte-identical
            _apply_qr_location_fallback(data, qr)

            asset_group = data.get("Asset Group")
            tag = tag_for_group
            data["Description"] = _resolve_description(
                asset_group,
                tag,
                data.get("Description")
            )

            present_map = {tag: bool(find_image(qr, building, tag)) for tag in ALL_SHOW}
            pass_ok = all(present_map.get(tag, False) for tag in REQUIRED)
            required_show = ['-0', '-1', '-2']
            present_all = sum(1 for tag in required_show if present_map.get(tag, False))
            fraction = f"{present_all}/3"
            has_extra_photo = present_map.get('-3', False)
            friendly_map = {'-0': 'Asset Plate', '-1': 'UBC Asset Tag', '-2': 'Full Interior Panel'}
            missing_list = ", ".join(
                friendly_map[t] for t in required_show if not present_map.get(t, False)
            )
            space_from_db = _fetch_qr_code_location(qr)
            sdi_val = 1 if str(current_status) == "2" else 0
            avg_ai_conf, avg_ai_conf_display = _normalize_avg_ai_conf(_extract_avg_ai_conf(raw))
            comp_score, comp_score_display = _normalize_avg_ai_conf(raw.get("completeness_score"))
            items.append({
                **data,
                "doc_id": doc_id,
                "qr_code": qr,
                "Capture Notes": capture_notes_map.get(qr, ""),
                "captured_by": captured_by_map.get(qr, ''),
                "building": building,
                "asset_type": raw.get("asset_type", ''),
                "Flagged": data.get("Flagged", "false"),
                "Approved": data.get("Approved", ''),
                "ExcludeSDI": sdi_val,
                "Modified": bool(raw.get("modified", False)),
                "Missed Photo": "NO" if pass_ok else "YES",
                "Photos Summary": fraction,
                "Missing List": missing_list,
                "Extra Photo": has_extra_photo,
                "Space": space_from_db,
                "ai_status": ai_status_map.get(qr, "0"),
                "Avg_ai_conf": avg_ai_conf,
                "Avg_ai_conf_display": avg_ai_conf_display,
                "Comp_score": comp_score,
                "Comp_score_display": comp_score_display
            })
        except Exception as e:
            print(f"[WARN] Error loading {filename}: {e}")
    _attach_package_locks_to_items(items)
    return items

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


def _package_lock_message(lock: Optional[dict] = None) -> str:
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
        item["package_lock_source"] = lock.get("source", '')
        item["package_lock_source_label"] = lock.get("source_label", '')
        item["package_id"] = lock.get("package_id", '')
        item["package_lock_message"] = _package_lock_message(lock) if lock.get("locked") else ""


def _package_lock_response(lock: dict):
    return jsonify({
        "success": False,
        "error": _package_lock_message(lock),
        "package_locked": True,
        "source": lock.get("source", ''),
        "package_id": lock.get("package_id", ''),
    }), 409


def _package_lock_check_failed_response(exc: Exception):
    return jsonify({
        "success": False,
        "error": f"Could not verify SDI package status: {exc}",
    }), 500


def _is_filled_sdi_value(value) -> bool:
    if value is None:
        return False
    return str(value).strip() != ''


def _fetch_el_required_field_rows(qr_codes: list[str]) -> dict[str, dict]:
    cleaned_qrs = []
    seen = set()
    for qr in qr_codes or []:
        qr_text = str(qr or "").strip()
        if not qr_text or qr_text in seen:
            continue
        seen.add(qr_text)
        cleaned_qrs.append(qr_text)

    if not cleaned_qrs or not _connectable():
        return {}

    rows_by_qr = {}
    chunk_size = 500

    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Deploy-order tolerance (same contract as _db_upsert_el_row): on a
            # DB that predates the 2026-08-07 nameplate migration the missing
            # columns are dropped from the SELECT instead of failing the whole
            # batch -- only their checklist entries read as unfilled.
            existing = set(_db_existing_cols(conn))
            select_fields = (
                [col for col in EL_REQUIRED_ALL_COLUMNS if col in existing]
                if existing else list(EL_REQUIRED_ALL_COLUMNS)
            )
            select_cols = ", ".join(_quote(col) for col in select_fields)
            for idx in range(0, len(cleaned_qrs), chunk_size):
                chunk = cleaned_qrs[idx:idx + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                query = (
                    f'SELECT {select_cols} FROM "{SDI_TABLE}" '
                    f'WHERE "QR Code" IN ({placeholders})'
                )
                cur.execute(query, chunk)
                for row in cur.fetchall():
                    qr_value = str(row["QR Code"] or "").strip()
                    if qr_value and qr_value not in rows_by_qr:
                        rows_by_qr[qr_value] = dict(row)
    except Exception as e:
        print(f"[WARN] Failed to fetch EL required-field rows: {e}")
        return {}

    return rows_by_qr


def _get_sld_buildings() -> set[str]:
    # Buildings with an active SLD drawing; Fed From Amperage checks only
    # apply where one exists.
    if not _connectable():
        return set()
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            rows = conn.execute(
                '''
                SELECT DISTINCT UPPER(TRIM(COALESCE("Building", '')))
                FROM "electrical_building_schema"
                WHERE TRIM(COALESCE("new_draw", '')) = 'TRUE'
                '''
            ).fetchall()
        return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}
    except Exception as e:
        print(f"[WARN] Failed to fetch SLD buildings: {e}")
        return set()


def _build_el_required_fields_payload(sdi_row: Optional[dict], sld_available: bool = True) -> dict:
    asset_group = str((sdi_row or {}).get("Asset Group") or "").strip()
    fields = list(EL_REQUIRED_COMMON_FIELDS)
    if asset_group in EL_REQUIRED_GROUP_FIELDS:
        fields.extend(EL_REQUIRED_GROUP_FIELDS[asset_group])
    elif _el_form_variant(asset_group) == "general":
        # General (non-distribution) assets are checked against the nameplate
        # field set captured by the General review-form variant (2026-08-07).
        fields.extend(EL_NAMEPLATE_FIELDS)
    if not sld_available:
        fields = [field for field in fields if field not in EL_SLD_DEPENDENT_FIELDS]

    checklist = []
    ok_count = 0
    for field in fields:
        filled = _is_filled_sdi_value((sdi_row or {}).get(field))
        checklist.append({"field": field, "filled": filled})
        if filled:
            ok_count += 1

    total_count = len(checklist)
    missing_count = max(0, total_count - ok_count)
    if missing_count == 0:
        traffic_light = "green"
        traffic_light_label = "Complete"
    elif missing_count <= 2:
        traffic_light = "yellow"
        traffic_light_label = "Needs Attention"
    else:
        traffic_light = "red"
        traffic_light_label = "Critical Missing Fields"

    return {
        "asset_group": asset_group,
        "checklist": checklist,
        "ok_count": ok_count,
        "total_count": total_count,
        "missing_count": missing_count,
        "traffic_light": traffic_light,
        "traffic_light_label": traffic_light_label,
        "has_sdi_row": bool(sdi_row),
        "sld_available": sld_available,
    }


def _attach_el_required_fields(*tables: list[dict]) -> None:
    qr_codes = []
    for table in tables:
        for item in table or []:
            qr_codes.append(item.get("qr_code"))

    rows_by_qr = _fetch_el_required_field_rows(qr_codes)
    sld_buildings = _get_sld_buildings()

    for table in tables:
        for item in table or []:
            qr_value = str(item.get("qr_code") or "").strip()
            sdi_row = rows_by_qr.get(qr_value)
            building_key = str((sdi_row or {}).get("Building") or "").strip().upper()
            sld_available = building_key in sld_buildings
            item["el_required_fields"] = _build_el_required_fields_payload(sdi_row, sld_available)


def get_archived_qrs():
    archived = set()
    if not _connectable():
        return archived
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            if not qrdb.has_table(conn, SDI_ARCHIVE_TABLE):
                return archived
            cur.execute(f'SELECT "QR Code" FROM "{SDI_ARCHIVE_TABLE}"')
            for row in cur.fetchall():
                if row[0]:
                    archived.add(str(row[0]).strip())
    except Exception as e:
        print(f"[WARN] Failed to fetch archived QRs: {e}")
    return archived


##-------------------------------------------------------------##
## Authentication Routes Blueprint                             ##
##-------------------------------------------------------------##
auth_bp = Blueprint('auth', __name__, template_folder=os.path.join(BASE_DIR, "review_asset_templates"))

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
            return render_template('login.html', title="Login - Asset Reviewer EL")
    return render_template('login.html', title="Login - Asset Reviewer EL")

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

app.register_blueprint(auth_bp)

##-------------------------------------------------------------##
## Main Application Routes Blueprint                           ##
##-------------------------------------------------------------##
main_bp = Blueprint('main', __name__, template_folder=os.path.join(BASE_DIR, "review_asset_templates"))

@main_bp.before_request
@login_required
def before_request_handler():
    g.embedded = request.args.get('embedded', '').lower() == 'true'
    if request.endpoint in ('main.static', 'main.serve_image', 'main.check_sdi', 'main.toggle_sdi', 'main.asset_dictionary_api', 'auth.login', 'auth.logout'):
        return
    sync_image_directory_to_db_el()
    sync_json_directory_to_db_el()


@main_bp.route("/api/asset-dictionary")
@require_permission("application", "reviewer_electrical", "viewer")
def asset_dictionary_api():
    response = jsonify(_get_live_mechanical_dictionary())
    response.headers["Cache-Control"] = "no-store"
    return response


# Distribution-view Asset Groups come from Asset_Group.elec_dist_setup = 'Y'
# (2026-08-04 migration). The static excel_export.EL_DISTRIBUTION_ASSET_GROUPS
# frozenset remains only as the fallback when the DB is unreachable or the
# column is absent (e.g. the frozen local SQLite dev copy).
_DIST_GROUPS_CACHE_TTL_SECONDS = 60.0
_dist_groups_cache = {"groups": None, "expires": 0.0}
_dist_groups_lock = Lock()


def get_distribution_asset_groups() -> frozenset:
    now = time.monotonic()
    with _dist_groups_lock:
        if _dist_groups_cache["groups"] is not None and now < _dist_groups_cache["expires"]:
            return _dist_groups_cache["groups"]
    groups = None
    if _connectable():
        try:
            with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    'SELECT DISTINCT "Name" FROM "Asset_Group" '
                    "WHERE UPPER(TRIM(COALESCE(elec_dist_setup, ''))) = 'Y'"
                )
                names = {(r["Name"] or "").strip() for r in cur.fetchall()}
                names.discard("")
                if names:
                    groups = frozenset(names)
        except Exception as e:
            print(f"[WARN] Distribution asset-group fetch failed; using static fallback: {e}")
    if groups is None:
        groups = excel_export.EL_DISTRIBUTION_ASSET_GROUPS
    with _dist_groups_lock:
        _dist_groups_cache["groups"] = groups
        _dist_groups_cache["expires"] = now + _DIST_GROUPS_CACHE_TTL_SECONDS
    return groups


def _el_form_variant(asset_group) -> str:
    """Review-form variant for an asset's resolved Asset Group (2026-08-07).

    'general' renders the ME-style nameplate form; 'distribution' renders the
    electrical tech-card form. Blank/unknown groups default to 'distribution'
    (the pre-split behavior) so a mis-defaulted asset never loses fields --
    the tag heuristics always land on a Distribution group anyway
    (ASSET_GROUP_DEFAULT = 'Panels').
    """
    group = str(asset_group or "").strip()
    if group and group not in get_distribution_asset_groups():
        return "general"
    return "distribution"


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


def get_filtered_data_and_counts(query_args, process_target: str = "0", apply_client_filters: bool = True, distribution_mode: str = None):

    flagged_filter = query_args.get("flagged")
    modified_filter = query_args.get("modified")
    missed_filter = query_args.get("missed")
    # Support both 'building' and 'filter_building' parameter names
    # (comma-joined list of codes for the multi-select)
    building_codes = _parse_filter_values(query_args.get("building") or query_args.get("filter_building"))
    conf_min = _normalize_conf_bound(query_args.get("conf_min"), 0)
    conf_max = _normalize_conf_bound(query_args.get("conf_max"), 100)
    
    # Context-aware default for Approved filter
    approved_arg = query_args.get("approved")
    if approved_arg is None:
        approved_arg = query_args.get("filter_approved")
    
    if approved_arg is not None:
        approved_filter = approved_arg
    else:
        # Default: Pending for all process targets
        approved_filter = "False"

    archive_val = query_args.get("archive")
    hide_archived = (archive_val != 'false')
    
    # Client-side DataTables filter parameters
    filter_qr = (query_args.get("filter_qr") or "").strip().upper()
    filter_tag = (query_args.get("filter_tag") or "").strip().upper()
    filter_notes = (query_args.get("filter_notes") or "").strip().upper()
    filter_groups = _parse_filter_values(query_args.get("filter_group"))
    filter_date = (query_args.get("filter_date") or "").strip()

    all_data = load_json_items(process_target)
    if hide_archived:
        archived_qrs = get_archived_qrs()
        base_data = [item for item in all_data if item.get("qr_code") not in archived_qrs]
    else:
        base_data = all_data

    if conf_min != 0 or conf_max != 100:
        base_data = [item for item in base_data if _matches_conf_range(item, conf_min, conf_max)]

    if distribution_mode in ("only", "exclude"):
        dist_groups = get_distribution_asset_groups()
        if distribution_mode == "only":
            base_data = [item for item in base_data if item.get("Asset Group") in dist_groups]
        else:
            base_data = [item for item in base_data if item.get("Asset Group") not in dist_groups]
        
    data_to_filter = base_data
    if flagged_filter == "true" and modified_filter == "true":
        data_to_filter = [item for item in data_to_filter if item.get("Flagged") == "true" and item.get("Modified")]
    elif flagged_filter == "true":
        data_to_filter = [item for item in data_to_filter if item.get("Flagged") == "true"]
    elif modified_filter == "true":
        data_to_filter = [item for item in data_to_filter if item.get("Modified")]
    if missed_filter == "true":
        data_to_filter = [item for item in data_to_filter if item.get("Missed Photo") == "YES"]
    if building_codes:
        building_set = set(building_codes)
        data_to_filter = [item for item in data_to_filter if item.get("building") in building_set]
    approved_str = str(approved_filter or "").strip().lower()

    # Unified Filter Loop (Robust)
    new_data = []
    for item in data_to_filter:
        val = item.get("Approved")
        sval = str(val or "").strip()
        
        should_keep = True
        
        if approved_str == "true":
             if sval != "True": should_keep = False
        elif approved_str == "false":
             if sval == "True": should_keep = False
        
        if should_keep:
             new_data.append(item)
    
    data_to_filter = new_data
    
    
    if apply_client_filters:
        # Keep review pagination aligned with the dashboard's client-side filters.
        if filter_qr:
            data_to_filter = [item for item in data_to_filter if filter_qr in (item.get("qr_code") or "").upper()]
        if filter_tag:
            data_to_filter = [item for item in data_to_filter if filter_tag in (item.get("UBC Asset Tag") or item.get("UBC Tag") or "").upper()]
        if filter_notes in ("YES", "NO"):
            data_to_filter = [
                item for item in data_to_filter
                if ("YES" if (item.get("Capture Notes") or "").strip() else "NO") == filter_notes
            ]
        if filter_groups:
            group_set = set(filter_groups)
            data_to_filter = [item for item in data_to_filter if (item.get("Asset Group") or "") in group_set]

        if filter_date:
            # Filter by date part (YYYY-MM-DD)
            # Capture Date format is YYYY-MM-DD HH:MM
            data_to_filter = [item for item in data_to_filter if item.get("Capture Date", '').startswith(filter_date)]
    
    # Fallback ordering when the client does not supply an explicit sequence:
    # mirror the dashboard's default sort (Capture Date, newest first) so the
    # review prev/next order is sensible even without the localStorage order.
    # Two stable passes: doc_id asc as a deterministic tiebreaker, then Capture
    # Date desc as the primary key.
    data_to_filter.sort(key=lambda x: str(x.get('doc_id') or ''))
    data_to_filter.sort(key=lambda x: str(x.get('Capture Date') or ''), reverse=True)
    return data_to_filter, base_data

def _get_card_scope_data(process_target: str, building_filter: str = "", distribution_mode: str = None):
    data = load_json_items(process_target)
    if distribution_mode in ("only", "exclude"):
        dist_groups = get_distribution_asset_groups()
        if distribution_mode == "only":
            data = [item for item in data if item.get("Asset Group") in dist_groups]
        else:
            data = [item for item in data if item.get("Asset Group") not in dist_groups]
    codes = _parse_filter_values(building_filter)
    if codes:
        code_set = set(codes)
        data = [item for item in data if item.get("building") in code_set]
    return data

def _landing_pending_counts():
    """Pending-review counts per scope for the landing page cards (2026-08-08).

    Mirrors the dashboards' default view exactly: New-process items ("0"),
    archived QRs hidden, Pending = Approved != "True", scope split by
    Asset_Group.elec_dist_setup membership. The landing page must never 500
    over a counting problem, so any failure degrades to None counts (the
    template renders an em dash).
    """
    try:
        items = load_json_items("0")
        archived = get_archived_qrs()
        dist_groups = get_distribution_asset_groups()
        counts = {"general": 0, "distribution": 0}
        for item in items:
            if item.get("qr_code") in archived:
                continue
            if str(item.get("Approved") or "").strip() == "True":
                continue
            scope = "distribution" if item.get("Asset Group") in dist_groups else "general"
            counts[scope] += 1
        return counts
    except Exception as exc:
        app.logger.warning("[landing] pending-count computation failed: %r", exc)
        return {"general": None, "distribution": None}


def _get_buildings_for_selector(*item_lists):
    codes = sorted({str(it.get("building") or "").strip()
                    for lst in item_lists for it in lst
                    if it.get("building")})
    return [{"code": c, "display": get_building_display(c) or c} for c in codes]

def _get_buildings_name_map():
    """Return {Code: Name} for the Buildings table. Used by the dashboard
    Building column tooltip ("<Name> (<Code>)"); mirrors the ME/BF helper."""
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


@main_bp.route("/")
def landing():
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return access_denied_response("Asset Reviewer - Electrical")
    pending = _landing_pending_counts()
    return render_template(
        "landing.html",
        username=current_user.username,
        pending_general=pending.get("general"),
        pending_distribution=pending.get("distribution"),
    )

@main_bp.route("/review-all")
def review_all():
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return access_denied_response("Asset Reviewer - Electrical")
    return _render_dashboard_view(distribution_mode="exclude", base_route="main.review_all", page_title="Review Electrical Assets")

@main_bp.route("/review-distribution")
def review_distribution():
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return access_denied_response("Asset Reviewer - Electrical")
    return _render_dashboard_view(distribution_mode="only", base_route="main.review_distribution", page_title="Review Electrical Assets - Distribution")

def _render_dashboard_view(distribution_mode=None, base_route="main.index", page_title=None):

    tab_param = request.args.get("tab")
    process_param = request.args.get("process", "0")
    
    if tab_param in ("new", "update", "manual", "sld"):
        active_tab = tab_param
    else:
        if process_param == "1":
            active_tab = "update"
        elif process_param == "2":
            active_tab = "manual"
        else:
            active_tab = "new"

    flagged_filter = request.args.get("flagged")
    modified_filter = request.args.get("modified")
    missed_filter = request.args.get("missed")

    approved_filter_raw = request.args.get("approved")
    if approved_filter_raw is None:
        approved_filter_raw = request.args.get("filter_approved")
    if approved_filter_raw is None:
        approved_filter_raw = "False"
    approved_filter = approved_filter_raw
    
    archive_val = request.args.get("archive")
    archive_filter_active = (archive_val != 'false')

    # EL is single-building by design: the dashboard selector offers one
    # building at a time (single-select mode of the shared multi-select
    # component). Parse defensively (legacy comma-joined URLs) and keep the
    # first code only.
    selected_building_codes = _parse_filter_values(request.args.get("filter_building") or request.args.get("building"))[:1]
    selected_building_code = selected_building_codes[0] if selected_building_codes else ""

    data_new_filtered, data_new_base = get_filtered_data_and_counts(request.args, "0", apply_client_filters=False, distribution_mode=distribution_mode)
    data_update_filtered, data_update_base = get_filtered_data_and_counts(request.args, "1", apply_client_filters=False, distribution_mode=distribution_mode)
    data_manual_filtered, data_manual_base = get_filtered_data_and_counts(request.args, "2", apply_client_filters=False, distribution_mode=distribution_mode)

    card_new = _get_card_scope_data("0", selected_building_code, distribution_mode=distribution_mode)
    card_update = _get_card_scope_data("1", selected_building_code, distribution_mode=distribution_mode)
    card_manual = _get_card_scope_data("2", selected_building_code, distribution_mode=distribution_mode)
    card_items = card_new + card_update + card_manual
    card_total_assets = len(card_items)
    card_pending_review = sum(1 for item in card_items if item.get("Approved") != "True")
    card_missed = sum(1 for item in card_items if item.get("Missed Photo") == "YES")
    card_approved = card_total_assets - card_pending_review

    if selected_building_code:
        table_new = data_new_filtered
        table_update = data_update_filtered
        table_manual = data_manual_filtered
        _attach_el_required_fields(table_new, table_update, table_manual)
    else:
        table_new, table_update, table_manual = [], [], []

    buildings_list = _get_buildings_for_selector(data_new_base, data_update_base, data_manual_base)
    selected_building_display = (get_building_display(selected_building_code) or selected_building_code) if selected_building_code else ""

    def get_counts(ds):
        return {
            "flagged": sum(1 for item in ds if item.get("Flagged") == "true"),
            "modified": sum(1 for item in ds if item.get("Modified")),
            "missed": sum(1 for item in ds if item.get("Missed Photo") == "YES"),
        }

    def scope_counts_to_selected_building(ds):
        if not selected_building_codes:
            return ds
        code_set = set(selected_building_codes)
        return [item for item in ds if item.get("building") in code_set]

    count_scope_new = scope_counts_to_selected_building(data_new_base)
    count_scope_manual = scope_counts_to_selected_building(data_manual_base)
    count_scope_update = scope_counts_to_selected_building(data_update_base)

    counts_new = get_counts(count_scope_new)
    counts_manual = get_counts(count_scope_manual)
    counts_update = get_counts(count_scope_update)
    count_unapproved_new = sum(1 for item in count_scope_new if item.get("Approved") != "True")
    count_unapproved_manual = sum(1 for item in count_scope_manual if item.get("Approved") != "True")
    count_unapproved_update = sum(1 for item in count_scope_update if item.get("Approved") != "True")

    return render_template(
        "dashboard.html",
        page_title=page_title,
        back_url=url_for("main.landing"),
        base_route=base_route,
        title="Asset Review Dashboard - Electrical",
        data_new=table_new,
        data_update=table_update,
        data_manual=table_manual,
        warn_missing=True,
        flagged_filter=flagged_filter,
        modified_filter=modified_filter,
        missed_filter=missed_filter,
        conf_min=_normalize_conf_bound(request.args.get("conf_min"), 0),
        conf_max=_normalize_conf_bound(request.args.get("conf_max"), 100),
        approved_filter=approved_filter,
        approved_filter_raw=approved_filter_raw,
        archive_filter_active=archive_filter_active,
        count_flagged_new=counts_new['flagged'],
        count_modified_new=counts_new['modified'],
        count_missed_new=counts_new['missed'],
        count_flagged_update=counts_update['flagged'],
        count_modified_update=counts_update['modified'],
        count_missed_update=counts_update['missed'],
        count_flagged_manual=counts_manual['flagged'],
        count_modified_manual=counts_manual['modified'],
        count_missed_manual=counts_manual['missed'],
        count_unapproved_new=count_unapproved_new,
        count_unapproved_manual=count_unapproved_manual,
        count_unapproved_update=count_unapproved_update,
        card_total_assets=card_total_assets,
        card_approved=card_approved,
        card_pending_review=card_pending_review,
        card_missed=card_missed,
        active_tab=active_tab,
        selected_building_code=selected_building_code,
        selected_building_codes=selected_building_codes,
        selected_building_display=selected_building_display,
        buildings_list=buildings_list,
        building_name_map=_get_buildings_name_map(),
        username=current_user.username,
        is_admin=is_admin(current_user),
        can_edit=has_permission(current_user, "application", "reviewer_electrical", "editor"),
    )

# ---------------------------------------------------------------------
# Asset Review Sheet — printable PDF + self-contained HTML export
# (mirrors the ME reviewer; adds a per-asset Single Line Diagram strip)
# ---------------------------------------------------------------------
def _file_data_uri(abs_path, downscale=True, max_dim=1400, quality=82):
    """Return a base64 'data:' URI for an image on disk, so it can be inlined
    into a fully self-contained HTML export (no external/asset-route refs).
    Downscales via Pillow (longest side <= max_dim, EXIF-aware) and re-encodes
    as JPEG to keep export size small; falls back to the raw file bytes if
    Pillow is unavailable or fails. Returns None when the path is missing."""
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

def _el_building_name(code):
    """Buildings."Name" for a building code (the human description); falls back
    to the bare code if not found or the DB is unavailable."""
    code_str = str(code or "").strip()
    if not code_str or not _connectable():
        return code_str
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            row = conn.execute(
                'SELECT "Name" FROM "Buildings" WHERE "Code" = ? LIMIT 1',
                (code_str,),
            ).fetchone()
        if row and str(row[0] or "").strip():
            return str(row[0]).strip()
    except Exception as e:
        print(f"[export] building name lookup failed for {code_str}: {e}")
    return code_str

# ---------------------------------------------------------------------
# SLD branch reconstruction — built from the electrical_building_schema chart
# data (the same data the EL "Single Line Diagram" view uses), NOT the PDF.
# Produces a self-contained inline SVG of the asset's branch:
#   Supply From (blue)  ->  asset (red flag)  ->  direct children.
# ---------------------------------------------------------------------
SLD_TYPE_COLORS = {
    "CDP": "#0d1b3e", "NDC": "#0d1b3e", "EDC": "#0d1b3e", "MDC": "#0d1b3e",
    "MDP": "#0d1b3e", "MCC": "#0d1b3e", "SWBD": "#556270", "ATS": "#1a8a9b",
    "TX": "#2e6ea6", "PNL": "#4a90c4", "SPL": "#7b68ae",
}
SLD_TYPE_DEFAULT = "#6b7c93"
SLD_MAX_ANCESTORS = 6
SLD_MAX_DESC_DEPTH = 4
SLD_MAX_CHILDREN_PER = 10
SLD_MAX_NODES = 40

def _xesc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

def _sld_node_color(equipment_id):
    prefix = str(equipment_id or "").split("-")[0].strip().upper()
    return SLD_TYPE_COLORS.get(prefix, SLD_TYPE_DEFAULT)

# Rows copied from the SDI side may carry Planon UoM codes ("VLT"/"AMP");
# the diagram always shows the display units ("V"/"A"), like the interactive SLD.
_SLD_RATING_DISPLAY_UOM = {"VLT": "V", "AMP": "A"}

def _sld_rating_text(row):
    row = row or {}
    parts = []
    for val_key, uom_key, default_uom in (("Voltage Rating", "Voltage Rating (UoM)", "V"),
                                          ("Amperage Rating", "Amperage Rating (UoM)", "A"),
                                          ("Power Rating", "Power Rating (UoM)", "")):
        val = str(row.get(val_key) or "").strip()
        if val:
            uom = str(row.get(uom_key) or "").strip()
            uom = _SLD_RATING_DISPLAY_UOM.get(uom.upper(), uom) or default_uom
            parts.append(val + uom)
    return " | ".join(parts)

def _sld_qr_for(conn, building, equipment_id, qr_available):
    """The QR Code for an SLD node (sdi_dataset_EL by Building + UBC Asset Tag ==
    Equipment ID), or '' when not captured."""
    eq = str(equipment_id or "").strip()
    if not qr_available or not eq:
        return ""
    try:
        r = conn.execute(
            '''
            SELECT "QR Code" AS qr FROM "sdi_dataset_EL"
            WHERE TRIM(COALESCE("UBC Asset Tag", '')) <> ''
              AND UPPER(TRIM(COALESCE("Building", ''))) = UPPER(TRIM(COALESCE(?, '')))
              AND UPPER(TRIM(COALESCE("UBC Asset Tag", ''))) = UPPER(TRIM(COALESCE(?, '')))
            LIMIT 1
            ''',
            (building, eq),
        ).fetchone()
        return str((r["qr"] if r else "") or "").strip()
    except Exception:
        return ""

def _get_sld_branch_tree(building, *tags):
    """From the active SLD data (electrical_building_schema), build the asset's
    END-TO-END branch as a tree: the upstream lineage (ancestors via Supply From,
    up toward the root) -> the reviewed asset -> its full downstream subtree
    (every asset it ultimately supplies). The asset's *siblings* are intentionally
    excluded. Returns {'root', 'truncated'} where root is a nested
    {'row','role','children'} node, or None when the asset is not on the diagram."""
    building_value = str(building or "").strip()
    candidates = [str(t or "").strip() for t in tags if str(t or "").strip()]
    if not building_value or not candidates or not _connectable():
        return None
    q_by_eqid = '''
        SELECT * FROM "electrical_building_schema"
        WHERE UPPER(TRIM(COALESCE("Building", ''))) = UPPER(?)
          AND UPPER(TRIM(COALESCE("Equipment ID", ''))) = UPPER(?)
          AND TRIM(COALESCE("new_draw", '')) = 'TRUE'
        LIMIT 1
    '''
    q_supplied = '''
        SELECT * FROM "electrical_building_schema"
        WHERE UPPER(TRIM(COALESCE("Building", ''))) = UPPER(?)
          AND UPPER(TRIM(COALESCE("Supply From", ''))) = UPPER(?)
          AND TRIM(COALESCE("new_draw", '')) = 'TRUE'
        ORDER BY CAST("Hierarchy" AS INTEGER), "Equipment ID"
    '''
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            asset = None
            for tag in candidates:
                r = conn.execute(q_by_eqid, (building_value, tag)).fetchone()
                if r:
                    asset = dict(r)
                    break
            if not asset:
                return None
            try:
                qr_available = "QR Code" in set(qrdb.table_columns(conn, "sdi_dataset_EL"))
            except Exception:
                qr_available = False

            def with_qr(row):
                row["QR Code"] = _sld_qr_for(conn, building_value, row.get("Equipment ID"), qr_available)
                return row

            asset_eqid = str(asset.get("Equipment ID") or "").strip()
            seen = {asset_eqid.upper()}
            budget = {"n": 1, "cut": False}

            # Downstream: the asset's full descendant subtree (all it supplies).
            def descendants(eqid, depth):
                if depth >= SLD_MAX_DESC_DEPTH or budget["n"] >= SLD_MAX_NODES:
                    return []
                kids = [dict(k) for k in conn.execute(q_supplied, (building_value, eqid)).fetchall()]
                if len(kids) > SLD_MAX_CHILDREN_PER:
                    budget["cut"] = True
                nodes = []
                for k in kids[:SLD_MAX_CHILDREN_PER]:
                    ke = str(k.get("Equipment ID") or "").strip()
                    if not ke or ke.upper() in seen:
                        continue
                    if budget["n"] >= SLD_MAX_NODES:
                        budget["cut"] = True
                        break
                    seen.add(ke.upper())
                    budget["n"] += 1
                    nodes.append({"row": with_qr(k), "role": "child", "children": descendants(ke, depth + 1)})
                return nodes

            current = {"row": with_qr(asset), "role": "asset", "children": descendants(asset_eqid, 0)}

            # Upstream: climb Supply From toward the root (lineage only, no siblings).
            cur_row = asset
            for depth in range(SLD_MAX_ANCESTORS):
                sf = str(cur_row.get("Supply From") or "").strip()
                if not sf or sf.upper() in seen:
                    break
                seen.add(sf.upper())
                pr = conn.execute(q_by_eqid, (building_value, sf)).fetchone()
                anc = dict(pr) if pr else {"Equipment ID": sf}
                budget["n"] += 1
                role = "parent" if depth == 0 else "ancestor"
                current = {"row": with_qr(anc), "role": role, "children": [current]}
                if not pr:
                    break
                cur_row = anc

            return {"root": current, "truncated": budget["cut"]}
    except Exception as e:
        print(f"[export] SLD branch lookup failed: {e}")
        return None

# Equipment-type glyphs copied from the SLD chart legend (sld_panel.html) so the
# report nodes carry the same icons. Each is (viewBox_w, viewBox_h, inner_svg);
# rendered on a small white chip at the left of the node so type-coloured details
# (e.g. transformer coils) stay visible on the coloured box.
SLD_SHAPE_BY_PREFIX = {
    "CDP": "panel", "NDC": "panel", "EDC": "panel", "MDC": "panel", "MDP": "panel", "MCC": "panel",
    "SWBD": "swbd", "ATS": "ats", "TX": "tx", "PNL": "pnl", "SPL": "spl",
}
SLD_GLYPHS = {
    "panel": (30, 14,
        '<rect x="0.5" y="0.5" width="29" height="13" rx="2" fill="#0d1b3e" stroke="#c8d6e5" stroke-width="0.6"/>'
        '<rect x="1.5" y="1.5" width="27" height="11" rx="1" fill="none" stroke="#8899aa" stroke-width="0.3"/>'
        '<line x1="9" y1="1.5" x2="9" y2="12.5" stroke="#8899aa" stroke-width="0.3"/>'
        '<line x1="5" y1="3" x2="5" y2="11" stroke="#fff" stroke-width="0.7"/>'
        '<rect x="3.5" y="3.5" width="3" height="3" rx="1.5" fill="none" stroke="#fff" stroke-width="0.5"/>'
        '<rect x="3.5" y="7.5" width="3" height="3" rx="1.5" fill="none" stroke="#fff" stroke-width="0.5"/>'
        '<path d="M7,9 L6,11 L7,11 L6.2,12.5 L8.5,10.5 L7.2,10.5 Z" fill="#fff"/>'),
    "ats": (24, 18,
        '<rect x="0.5" y="0.5" width="23" height="17" rx="2" fill="#1a8a9b" stroke="#c8d6e5" stroke-width="0.6"/>'
        '<circle cx="7" cy="3" r="1.3" fill="none" stroke="#fff" stroke-width="0.7"/>'
        '<circle cx="17" cy="3" r="1.3" fill="none" stroke="#fff" stroke-width="0.7"/>'
        '<line x1="7" y1="4.3" x2="7" y2="8" stroke="#fff" stroke-width="0.7"/>'
        '<line x1="17" y1="4.3" x2="17" y2="8" stroke="#fff" stroke-width="0.7"/>'
        '<circle cx="7" cy="8.5" r="1" fill="none" stroke="#fff" stroke-width="0.6"/>'
        '<circle cx="17" cy="8.5" r="1" fill="none" stroke="#fff" stroke-width="0.6"/>'
        '<line x1="7" y1="8.5" x2="12" y2="12" stroke="#fff" stroke-width="0.8"/>'
        '<line x1="12" y1="12.5" x2="12" y2="15" stroke="#fff" stroke-width="0.7"/>'
        '<circle cx="12" cy="15.5" r="1.3" fill="none" stroke="#fff" stroke-width="0.7"/>'),
    "tx": (20, 18,
        '<rect x="2" y="6" width="16" height="10" rx="2" fill="#2e6ea6" stroke="#c8d6e5" stroke-width="0.6"/>'
        '<line x1="5" y1="1" x2="5" y2="6" stroke="#2e6ea6" stroke-width="1.2"/>'
        '<line x1="10" y1="1" x2="10" y2="6" stroke="#2e6ea6" stroke-width="1.2"/>'
        '<line x1="15" y1="1" x2="15" y2="6" stroke="#2e6ea6" stroke-width="1.2"/>'
        '<ellipse cx="5" cy="3" rx="2" ry="1.2" fill="none" stroke="#2e6ea6" stroke-width="0.8"/>'
        '<ellipse cx="10" cy="3" rx="2" ry="1.2" fill="none" stroke="#2e6ea6" stroke-width="0.8"/>'
        '<ellipse cx="15" cy="3" rx="2" ry="1.2" fill="none" stroke="#2e6ea6" stroke-width="0.8"/>'
        '<path d="M11,8 L8.5,11.5 L10.5,11.5 L9,14.5 L12,10.5 L10,10.5 Z" fill="none" stroke="#fff" stroke-width="0.8"/>'),
    "pnl": (20, 20,
        '<rect x="0.5" y="0.5" width="19" height="16" rx="2" fill="#4a90c4" stroke="#c8d6e5" stroke-width="0.6"/>'
        '<rect x="2" y="2" width="16" height="12" rx="1" fill="none" stroke="rgba(255,255,255,0.5)" stroke-width="0.4"/>'
        '<circle cx="5.5" cy="4" r="1" fill="none" stroke="#fff" stroke-width="0.5"/>'
        '<circle cx="8" cy="4" r="1" fill="none" stroke="#fff" stroke-width="0.5"/>'
        '<circle cx="10.5" cy="4" r="1" fill="none" stroke="#fff" stroke-width="0.5"/>'
        '<circle cx="13" cy="4" r="1" fill="none" stroke="#fff" stroke-width="0.5"/>'
        '<path d="M9,6 L6.5,11 L9.5,11 Z" fill="none" stroke="#fff" stroke-width="0.6"/>'
        '<line x1="5" y1="12" x2="13" y2="12" stroke="#fff" stroke-width="0.5"/>'
        '<line x1="5" y1="13.5" x2="13" y2="13.5" stroke="#fff" stroke-width="0.5"/>'),
    "spl": (22, 20,
        '<rect x="7" y="0" width="8" height="5" rx="2" fill="none" stroke="#7b68ae" stroke-width="1"/>'
        '<line x1="11" y1="5" x2="11" y2="8" stroke="#7b68ae" stroke-width="1.2"/>'
        '<path d="M11,8 L4,8 L4,12" fill="none" stroke="#7b68ae" stroke-width="1.2" stroke-linejoin="round"/>'
        '<path d="M11,8 L18,8 L18,12" fill="none" stroke="#7b68ae" stroke-width="1.2" stroke-linejoin="round"/>'
        '<rect x="1" y="12" width="6" height="4.5" rx="1.5" fill="none" stroke="#7b68ae" stroke-width="1"/>'
        '<rect x="15" y="12" width="6" height="4.5" rx="1.5" fill="none" stroke="#7b68ae" stroke-width="1"/>'),
    "swbd": (30, 14,
        '<rect x="0.5" y="0.5" width="29" height="13" rx="2" fill="#556270" stroke="#3a4550" stroke-width="0.6"/>'
        '<rect x="1" y="1" width="9" height="12" rx="1" fill="#3a4550"/>'
        '<line x1="3" y1="3" x2="8" y2="3" stroke="#6b7c8a" stroke-width="0.5"/>'
        '<line x1="3" y1="4.5" x2="8" y2="4.5" stroke="#6b7c8a" stroke-width="0.5"/>'
        '<rect x="22" y="1.5" width="6" height="2.5" rx="0.5" fill="#64737e"/>'
        '<rect x="22" y="4.5" width="6" height="2.5" rx="0.5" fill="#64737e"/>'
        '<rect x="22" y="7.5" width="6" height="2.5" rx="0.5" fill="#64737e"/>'
        '<rect x="22" y="10.5" width="6" height="2.5" rx="0.5" fill="#64737e"/>'),
}

def _sld_node_icon(prefix, bx, top_y, box_h, box_w):
    """Return (svg, label_center_x): the type glyph on a small white chip at the
    left of the node box, plus where the Equipment ID text should be centred."""
    shape = SLD_SHAPE_BY_PREFIX.get(prefix)
    if not shape or shape not in SLD_GLYPHS:
        return "", bx + box_w / 2.0
    vb_w, vb_h, inner = SLD_GLYPHS[shape]
    scale = min(16.0 / vb_h, 28.0 / vb_w)
    gw, gh, pad = vb_w * scale, vb_h * scale, 2.0
    chip_w, chip_h = gw + 2 * pad, gh + 2 * pad
    cx0 = bx + 6
    cy0 = top_y + (box_h - chip_h) / 2.0
    svg = (f'<rect x="{cx0:.1f}" y="{cy0:.1f}" width="{chip_w:.1f}" height="{chip_h:.1f}" rx="3" fill="#ffffff"/>'
           f'<g transform="translate({cx0 + pad:.1f},{cy0 + pad:.1f}) scale({scale:.3f})">{inner}</g>')
    label_cx = (cx0 + chip_w + 6 + bx + box_w - 6) / 2.0
    return svg, label_cx

def _build_sld_branch_svg(building, equipment_id, ubc_tag):
    """Reconstruct the asset's end-to-end SLD branch from the chart data as a
    self-contained inline SVG, laid out LEFT-TO-RIGHT (a compact 'ladder': upstream
    ancestors on the left -> the reviewed asset (red flag) -> downstream subtree
    fanning out to the right), so it stays short enough to fit on one page. The
    asset's Supply From is accented blue; nodes carry the chart's type icons.
    Returns the SVG markup, or None when the asset is not on the active diagram."""
    tree = _get_sld_branch_tree(building, equipment_id, ubc_tag)
    if not tree or not tree.get("root"):
        return None
    root = tree["root"]
    truncated = tree.get("truncated")

    W, BOX_H, QR_H, RATE_H = 152, 44, 15, 15
    BLOCK_H = QR_H + BOX_H + RATE_H
    H_GAP, V_GAP, PAD = 38, 8, 14

    counter = [0]
    max_depth = [0]

    def layout(n, depth):
        n["depth"] = depth
        if depth > max_depth[0]:
            max_depth[0] = depth
        ch = n.get("children") or []
        if not ch:
            n["rowpos"] = counter[0]
            counter[0] += 1
        else:
            for c in ch:
                layout(c, depth + 1)
            n["rowpos"] = (ch[0]["rowpos"] + ch[-1]["rowpos"]) / 2.0

    layout(root, 0)
    lines = max(1, counter[0])
    svg_w = 2 * PAD + (max_depth[0] + 1) * W + max_depth[0] * H_GAP
    svg_h = 2 * PAD + lines * BLOCK_H + (lines - 1) * V_GAP + (14 if truncated else 0)

    def node_cx(n):
        return PAD + W / 2.0 + n["depth"] * (W + H_GAP)

    def node_top(n):
        return PAD + QR_H + n["rowpos"] * (BLOCK_H + V_GAP)

    def link(sx, sy, tx, ty):
        mx = (sx + tx) / 2.0
        return (f'<path d="M{sx:.1f},{sy:.1f} L{mx:.1f},{sy:.1f} L{mx:.1f},{ty:.1f} '
                f'L{tx:.1f},{ty:.1f}" fill="none" stroke="#a0b4c8" stroke-width="2"/>')

    def draw_node(n):
        ncx = node_cx(n)
        top_y = node_top(n)
        row = n["row"]
        role = n["role"]
        eq = row.get("Equipment ID")
        color = _sld_node_color(eq)
        bx = ncx - W / 2.0
        if role == "asset":
            stroke, sw = "#d62828", 3
        elif role == "parent":
            stroke, sw = "#0055B7", 2.5
        else:
            stroke, sw = "#c8d6e5", 1.5
        icon_svg, label_cx = _sld_node_icon(str(eq or "").split("-")[0].strip().upper(), bx, top_y, BOX_H, W)
        eqid = _xesc(eq or "—")
        rating = _xesc(_sld_rating_text(row))
        qr = _xesc(row.get("QR Code") or "")
        s = [f'<rect x="{bx:.1f}" y="{top_y:.1f}" width="{W}" height="{BOX_H}" rx="4" ry="4" '
             f'fill="{color}" stroke="{stroke}" stroke-width="{sw}"/>', icon_svg,
             f'<text x="{label_cx:.1f}" y="{top_y + BOX_H / 2 + 4:.1f}" text-anchor="middle" '
             f'font-size="12" font-weight="700" fill="#ffffff">{eqid}</text>']
        if qr:
            s.append(f'<text x="{ncx:.1f}" y="{top_y - 4:.1f}" text-anchor="middle" '
                     f'font-size="9" font-weight="700" fill="#0d1b3e">{qr}</text>')
        if rating:
            s.append(f'<text x="{ncx:.1f}" y="{top_y + BOX_H + 11:.1f}" text-anchor="middle" '
                     f'font-size="9" fill="#6b7c93">{rating}</text>')
        if role in ("asset", "parent"):
            fx = bx + W - 5
            ft = top_y - 1
            s.append(f'<line x1="{fx:.1f}" y1="{ft - 16:.1f}" x2="{fx:.1f}" y2="{ft + 2:.1f}" '
                     f'stroke="#212529" stroke-width="2"/>')
            s.append(f'<polygon points="{fx:.1f},{ft - 16:.1f} {fx - 14:.1f},{ft - 11:.1f} '
                     f'{fx:.1f},{ft - 6:.1f}" fill="{stroke}"/>')
        return "".join(s)

    links = []
    boxes = []

    def walk(n):
        cy = node_top(n) + BOX_H / 2.0
        rx = node_cx(n) + W / 2.0
        for c in (n.get("children") or []):
            links.append(link(rx, cy, node_cx(c) - W / 2.0, node_top(c) + BOX_H / 2.0))
        boxes.append(draw_node(n))
        for c in (n.get("children") or []):
            walk(c)

    walk(root)
    parts = links + boxes
    if truncated:
        parts.append(f'<text x="{svg_w / 2.0:.1f}" y="{svg_h - 4:.1f}" text-anchor="middle" '
                     f'font-size="9" fill="#6b7c93">Diagram truncated to fit</text>')

    aria = _xesc(root["row"].get("Equipment ID") or "")
    return (f'<svg viewBox="0 0 {svg_w:.0f} {svg_h:.0f}" width="{svg_w:.0f}" height="{svg_h:.0f}" '
            f'role="img" aria-label="SLD branch from {aria}" '
            f'style="max-width:100%;max-height:62mm;font-family:system-ui,-apple-system,sans-serif;">'
            f'{"".join(parts)}</svg>')

def _sld_legend_html():
    """Legend for the SLD box: the red/blue flags plus the equipment-type icons
    (the same glyphs as the SLD chart). Returns a self-contained HTML snippet."""
    def flag(color):
        return ('<svg width="13" height="15" viewBox="0 0 13 15" style="vertical-align:middle">'
                '<line x1="2" y1="1" x2="2" y2="14" stroke="#212529" stroke-width="1.5"/>'
                '<polygon points="2,1 12,4.5 2,8" fill="' + color + '"/></svg>')
    items = [(flag("#d62828"), "Current Asset"), (flag("#0055B7"), "Supply From")]
    for shape, label in (("panel", "Panel"), ("tx", "Transformer"), ("ats", "ATS"),
                         ("pnl", "PNL"), ("spl", "Splitter"), ("swbd", "Switchboard")):
        vb_w, vb_h, inner = SLD_GLYPHS[shape]
        w = vb_w * (16.0 / vb_h)
        icon = ('<svg width="%.0f" height="16" viewBox="0 0 %d %d" style="vertical-align:middle">%s</svg>'
                % (w, vb_w, vb_h, inner))
        items.append((icon, label))
    spans = "".join(
        '<span style="display:inline-flex;align-items:center;gap:4px;white-space:nowrap;margin-right:14px;">'
        + icon + '<span>' + _xesc(label) + '</span></span>'
        for icon, label in items
    )
    return ('<div style="margin-top:8px;padding-top:7px;border-top:1px solid #d7dce2;'
            'display:flex;flex-wrap:wrap;row-gap:4px;font-size:9.5px;color:#5a6472;align-items:center;">'
            '<span style="font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-right:10px;">Legend</span>'
            + spans + '</div>')

def _build_el_sheet_context(doc_id):
    """Shared loader for the EL printable/exportable Asset Review Sheet. Mirrors
    the GET data-prep of review() (minus pagination/dropdowns). Returns
    (context, None) on success or (None, (body, status)) on a load error."""
    json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(json_path):
        return None, ("Not found", 404)
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return None, ("Bad ID", 400)
    qr, building = m.groups()
    with open(json_path, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    data = copy.deepcopy(loaded.get("structured_data", {}) or {})

    # Legacy gate: looked up once per record and reused for every downstream
    # derivation choice below (both the upstream field computation here and the
    # dictionary-priority call further down). Not itself a route function, so
    # BuildingProcessError uses the existing (ctx, err) error-tuple contract
    # (see the "Not found"/"Bad ID" returns above) rather than flash+redirect —
    # a redirect back to the review page doesn't fit a print-preview/download
    # response.
    with qrdb.get_connection(sqlite_path=DB_PATH) as _bp_conn:
        try:
            _process = el_legacy_flow.get_building_process(_bp_conn, building)
        except el_legacy_flow.BuildingProcessError as exc:
            return None, (str(exc), 409)

    amperage_value = _get_el_amperage_value(data)
    (equipment_id_value, equipment_type_value, power_type_value,
     power_rating_value, power_rating_uom, supply_from_value,
     fed_from_equipment_id_value) = _compute_el_upstream_fields(data, _process)
    data["Supply From"] = supply_from_value
    fed_from_amperage_value = _resolve_el_fed_from_amperage_value(building, data.get("Supply From"))
    fed_from_amperage_uom = _get_el_fed_from_amperage_uom_value(fed_from_amperage_value)
    data["Ampere"] = amperage_value
    data["Equipment ID"] = equipment_id_value
    data["Equipment Type"] = equipment_type_value
    data["Power Type"] = power_type_value
    data["Power Rating"] = power_rating_value
    data["Power Rating (UoM)"] = power_rating_uom
    data["Fed From Equipment ID"] = fed_from_equipment_id_value
    data["Fed From Amperage Rating"] = fed_from_amperage_value
    data["Fed From Amperage Rating (UoM)"] = fed_from_amperage_uom
    data["Amperage Rating (UoM)"] = "AMP" if amperage_value else ""
    keep_blank = [
        "UBC Asset Tag", "Equipment ID", "Equipment Type", "Branch Panel", "Ampere", "Supply From", "Volts", "Location",
        "Power Type", "Power Rating", "Power Rating (UoM)", "Fed From Equipment ID", "Fed From Amperage Rating",
        "Fed From Amperage Rating (UoM)", "Attribute", "Approved", "Asset Group", "Description", "Amperage Rating (UoM)",
        "Manufacturer", "Model", "Serial Number", "Year", "Capacity", "Capacity (UoM)"
    ]
    for k in keep_blank:
        data.setdefault(k, '')
    data.setdefault("Flagged", "false")
    asset_type_val = loaded.get("asset_type")
    asset_group_manual_persisted = str(data.get("asset_group_manual") or "").strip() == "1"
    pre_dict_asset_group = (data.get("Asset Group") or "").strip()
    pre_dict_description = str(data.get("Description") or "")
    pre_dict_desc_is_placeholder = _is_ai_default_description(pre_dict_description, data.get("UBC Asset Tag") or data.get("Branch Panel"))
    tag_for_group, _ = _apply_tag_dictionary_first(data, asset_type_val)
    if pre_dict_asset_group and asset_group_manual_persisted:
        data["Asset Group"] = pre_dict_asset_group
    if pre_dict_description.strip() and not pre_dict_desc_is_placeholder:
        data["Description"] = pre_dict_description
    if not tag_for_group:
        tag_for_group = (data.get("UBC Asset Tag") or "").strip() or (data.get("Branch Panel") or "").strip()
    if not data.get("Attribute"):
        data["Attribute"] = "Electrical"
    if not data.get("Asset Group"):
        data["Asset Group"] = _get_asset_group_from_tag(tag_for_group, asset_type_val)
    if tag_for_group:
        # Gate: reuses the `_process` looked up once above.
        if _process == "Legacy":
            el_legacy_flow.apply_legacy_rules(data)
        else:
            _apply_dictionary_priority(data, tag_for_group)   # standard path, byte-identical
    _apply_qr_location_fallback(data, qr)
    data["Description"] = _resolve_description(data.get("Asset Group"), tag_for_group, data.get("Description"))
    _, avg_ai_conf_display = _normalize_avg_ai_conf(_extract_avg_ai_conf(loaded))
    space = _fetch_qr_code_location(qr)
    capture_info = _fetch_capture_info(qr, building, "EL")
    images = {}
    for tag in SEQ_SHOW:
        fn = find_image(qr, building, tag)
        uri = _file_data_uri(os.path.join(IMG_DIR, fn)) if fn else None
        images[tag] = {"exists": bool(uri), "url": uri}
    logo_uri = _file_data_uri(os.path.join(BASE_DIR, "review_asset_templates", "static", "ubc-facilities_logo.jpg"), downscale=False)
    sld_branch_svg = _build_sld_branch_svg(building, data.get("Equipment ID"), data.get("UBC Asset Tag"))
    sld_legend_html = _sld_legend_html() if sld_branch_svg else None
    # Degrade like the builder's other DB lookups: a DB outage must not 500 the sheet.
    try:
        installation_date = get_installation_date(qrdb, DB_PATH, qr)
    except Exception as e:
        print(f"Error fetching installation date: {e}")
        installation_date = ""
    ctx = dict(
        title="Asset Review Sheet - Electrical",
        doc_id=doc_id,
        qr_code=qr,
        building=building,
        building_name=_el_building_name(building),
        space=space,
        avg_ai_conf_display=avg_ai_conf_display,
        form_variant=_el_form_variant(data.get("Asset Group")),
        data=data,
        capture_info=capture_info,
        capture_notes=get_qr_capture_notes().get(qr, ""),
        installation_date=installation_date,
        images=images,
        logo_uri=logo_uri,
        sld_branch_svg=sld_branch_svg,
        sld_legend_html=sld_legend_html,
        approved=(str(data.get("Approved", "") or "").strip() == "True"),
        username=current_user.username,
        generated_on=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return ctx, None

@main_bp.route("/review/<doc_id>/print")
def review_print(doc_id):
    """Print-optimized EL Asset Review Sheet, rendered inline with auto-print
    (Save-as-PDF via the browser). Opened top-level in a new tab."""
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return access_denied_response("Asset Reviewer - Electrical")
    ctx, err = _build_el_sheet_context(doc_id)
    if err:
        return err
    return render_template("review_print.html", auto_print=True, **ctx)

@main_bp.route("/review/<doc_id>/export")
def review_export(doc_id):
    """Export the EL Asset Review Sheet as a fully self-contained HTML download
    (photos, logo, and any SLD strip inlined as base64 data URIs)."""
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return access_denied_response("Asset Reviewer - Electrical")
    ctx, err = _build_el_sheet_context(doc_id)
    if err:
        return err
    html = render_template("review_print.html", auto_print=False, **ctx)
    safe_qr = re.sub(r"[^A-Za-z0-9_.-]", "", str(ctx["qr_code"])) or "asset"
    safe_bld = re.sub(r"[^A-Za-z0-9_.-]", "", str(ctx["building"]))
    fname = f"Asset_Review_EL_{safe_qr}_{safe_bld}.html"
    return send_file(BytesIO(html.encode("utf-8")), as_attachment=True, download_name=fname, mimetype="text/html")

@main_bp.route("/api/asset-preview/<doc_id>")
def asset_preview(doc_id):
    """Lightweight JSON for the review page's 'Next Asset' preview rail: QR, UBC
    tag, location, and thumbnail URLs for an arbitrary doc_id. Lets the client
    drive the preview from the dashboard's filtered+sorted order (localStorage),
    so the previewed 'next' asset matches the order the reviewer actually sees."""
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return jsonify({"error": "forbidden"}), 403
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return jsonify({"error": "bad id"}), 400
    json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(json_path):
        return jsonify({"error": "not found"}), 404
    qr, building = m.groups()
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return jsonify({"error": "unreadable"}), 500
    sd = loaded.get("structured_data", {}) or {}
    label_map = {'-0': 'Asset Plate/Label', '-1': 'UBC Asset Tag', '-2': 'Full Interior Panel', '-3': 'Extra Photo'}
    images = []
    for tag in SEQ_SHOW:
        fn = find_image(qr, building, tag)
        if fn:
            images.append({"url": url_for('main.serve_image', filename=fn), "label": label_map.get(tag, tag)})
    return jsonify({
        "doc_id": doc_id,
        "qr_code": qr,
        "ubc_tag": sd.get("UBC Asset Tag") or sd.get("UBC Tag") or "",
        "location": _fetch_qr_code_location(qr) or sd.get("Location") or "",
        "images": images,
    })

@main_bp.route("/review/<doc_id>")
def review(doc_id):
    if not has_permission(current_user, "application", "reviewer_electrical", "viewer"):
        return access_denied_response("Asset Reviewer - Electrical")
    json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(json_path):
        return "Not found", 404
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return "Bad ID", 400
    qr, building = m.groups()
    package_lock = _get_qr_package_lock(qr)
    package_locked = bool(package_lock.get("locked"))
    with open(json_path, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    review_revision = compute_review_revision(loaded)
    data = copy.deepcopy(loaded.get("structured_data", {}) or {})

    # Legacy gate: looked up once per record and reused for every downstream
    # derivation choice below (both the upstream field computation here and the
    # dictionary-priority call further down). Genuine request handler
    # (GET /review/<doc_id>) with `building` in scope, so the brief's
    # flash+redirect pattern applies as-is.
    with qrdb.get_connection(sqlite_path=DB_PATH) as _bp_conn:
        try:
            _process = el_legacy_flow.get_building_process(_bp_conn, building)
        except el_legacy_flow.BuildingProcessError as exc:
            flash(str(exc), "warning")
            return redirect(request.referrer or url_for("main.review_all"))

    amperage_value = _get_el_amperage_value(data)
    (equipment_id_value, equipment_type_value, power_type_value,
     power_rating_value, power_rating_uom, supply_from_value,
     fed_from_equipment_id_value) = _compute_el_upstream_fields(data, _process)
    data["Supply From"] = supply_from_value
    fed_from_amperage_value = _resolve_el_fed_from_amperage_value(building, data.get("Supply From"))
    fed_from_amperage_uom = _get_el_fed_from_amperage_uom_value(fed_from_amperage_value)
    data["Ampere"] = amperage_value
    data["Equipment ID"] = equipment_id_value
    data["Equipment Type"] = equipment_type_value
    data["Power Type"] = power_type_value
    data["Power Rating"] = power_rating_value
    data["Power Rating (UoM)"] = power_rating_uom
    data["Fed From Equipment ID"] = fed_from_equipment_id_value
    data["Fed From Amperage Rating"] = fed_from_amperage_value
    data["Fed From Amperage Rating (UoM)"] = fed_from_amperage_uom
    data["Amperage Rating (UoM)"] = "AMP" if amperage_value else ""
    keep_blank = [
        "UBC Asset Tag", "Equipment ID", "Equipment Type", "Branch Panel", "Ampere", "Supply From", "Volts", "Location",
        "Power Type", "Power Rating", "Power Rating (UoM)", "Fed From Equipment ID", "Fed From Amperage Rating", "Fed From Amperage Rating (UoM)", "Attribute", "Approved", "Asset Group", "Description", "Amperage Rating (UoM)",
        "Manufacturer", "Model", "Serial Number", "Year", "Capacity", "Capacity (UoM)"
    ]
    for k in keep_blank:
        data.setdefault(k, '')
    data.setdefault("Flagged", "false")
    asset_type_val = loaded.get("asset_type")
    # Asset Group preservation is gated on the persisted ``asset_group_manual``
    # flag: only restore the saved value when the user explicitly edited it
    # (flag == "1"). When the flag is "0" / missing the saved value is treated
    # as auto-derived, so retroactive dictionary updates (e.g. adding SWBD|EL
    # after old extractions saved "Panels") flow through on next render.
    # Description still uses the legacy "Panel - <tag>" placeholder detector to
    # decide AI default vs. user edit.
    asset_group_manual_persisted = str(data.get("asset_group_manual") or "").strip() == "1"
    pre_dict_asset_group = (data.get("Asset Group") or "").strip()
    pre_dict_description = str(data.get("Description") or "")
    pre_dict_desc_is_placeholder = _is_ai_default_description(pre_dict_description, data.get("UBC Asset Tag") or data.get("Branch Panel"))
    tag_for_group, _ = _apply_tag_dictionary_first(data, asset_type_val)
    if pre_dict_asset_group and asset_group_manual_persisted:
        data["Asset Group"] = pre_dict_asset_group
    if pre_dict_description.strip() and not pre_dict_desc_is_placeholder:
        data["Description"] = pre_dict_description
    if not tag_for_group:
        tag_for_group = (data.get("UBC Asset Tag") or "").strip() or (data.get("Branch Panel") or "").strip()
    if not data.get("Attribute"):
        data["Attribute"] = "Electrical"
    if not data.get("Asset Group"):
        data["Asset Group"] = _get_asset_group_from_tag(tag_for_group, asset_type_val)

    if tag_for_group:
        # Gate: reuses the `_process` looked up once above.
        if _process == "Legacy":
            el_legacy_flow.apply_legacy_rules(data)
        else:
            _apply_dictionary_priority(data, tag_for_group)   # standard path, byte-identical
    _apply_qr_location_fallback(data, qr)

    asset_group = data.get("Asset Group")
    form_variant = _el_form_variant(asset_group)
    tag = tag_for_group
    data["Description"] = _resolve_description(
        asset_group,
        tag,
        data.get("Description")
    )
    amp_rating_warning = excel_export.has_el_amperage_warning(
        asset_group,
        data.get("Ampere"),
        data.get("Fed From Amperage Rating"),
        distribution_groups=get_distribution_asset_groups(),
    )
    avg_ai_conf, avg_ai_conf_display = _normalize_avg_ai_conf(_extract_avg_ai_conf(loaded))
    
    space = _fetch_qr_code_location(qr)
    capture_info = _fetch_capture_info(qr, building, "EL")
    images = {}
    for tag in SEQ_SHOW:
        filename = find_image(qr, building, tag)
        images[tag] = {
            "exists": bool(filename),
            "url": url_for('main.serve_image', filename=filename) if filename else None 
        }
    asset_group_options = _fetch_el_asset_group_options()

    # --- NEW: Calculate Pagination Index & Next Asset ---
    process_param = request.args.get("process", "0")
    base_route = request.args.get("base_route") or "main.review_all"
    dist_mode = "exclude" if base_route == "main.review_all" else "only"
    filtered_data, _ = get_filtered_data_and_counts(request.args, process_param, distribution_mode=dist_mode)

    total_count = len(filtered_data)
    current_index = 0
    ids = [i['doc_id'] for i in filtered_data]
    
    next_asset = None
    
    if doc_id in ids:
        idx = ids.index(doc_id)
        current_index = idx + 1
        
        if idx + 1 < len(filtered_data):
            next_item = filtered_data[idx + 1]
            
            # Find ALL valid thumbnails for the next asset
            next_images = []
            next_qr = next_item.get('qr_code')
            next_bld = next_item.get('building')
            
            # Map suffix tags to human readable labels (EL Specific)
            label_map = {
                '-0': 'Asset Plate/Label',
                '-1': 'UBC Asset Tag',
                '-2': 'Full Interior Panel',
                '-3': 'Extra Photo'
            }
            
            # EL uses SEQ_SHOW or similar. 
            # In load_json_items it lists ALL_SHOW. 
            # Let's use SEQ_SHOW if available globally or define checks.
            # Usually SEQ_SHOW is imported or defined at top. 
            # Based on line 1442: 'for tag in SEQ_SHOW:' exists in review route.
            
            for tag in SEQ_SHOW:
                fn = find_image(next_qr, next_bld, tag)
                if fn:
                    next_images.append({
                        "url": url_for('main.serve_image', filename=fn),
                        "label": label_map.get(tag, tag)
                    })
            
            next_asset = {
                "qr_code": next_item.get('qr_code'),
                "ubc_tag": next_item.get('UBC Asset Tag') or next_item.get('UBC Tag'),
                "location": next_item.get('Location'),
                "images": next_images
            }
    # ---------------------------------------

    return render_template(
        "review.html",
        doc_id=doc_id,
        qr_code=qr,
        building=building,
        asset_type=loaded.get("asset_type", ''),
        data=data,
        avg_ai_conf=avg_ai_conf,
        avg_ai_conf_display=avg_ai_conf_display,
        space=space,
        capture_info=capture_info,
        capture_notes=get_qr_capture_notes().get(qr, ""),
        installation_date=get_installation_date(qrdb, DB_PATH, qr),
        images=images,
        attribute_options=[],
        asset_group_options=asset_group_options,
        username=current_user.username,
        asset_dictionary=_get_live_mechanical_dictionary(),
        current_index=current_index,
        total_count=total_count,
        next_asset=next_asset,
        review_locked=(str(data.get("Approved", '') or "").strip() == "True" or package_locked),
        package_locked=package_locked,
        package_lock=package_lock,
        package_lock_message=_package_lock_message(package_lock) if package_locked else "",
        base_route=base_route,
        review_revision=review_revision,
        amp_rating_warning=amp_rating_warning,
        distribution_asset_groups=sorted(get_distribution_asset_groups()),
        form_variant=form_variant,
        review_buttons=REVIEW_BUTTONS,
        review_endpoints=dict(REVIEW_ENDPOINTS_STATIC, dashboard=base_route),
    )

@main_bp.route("/review/<doc_id>", methods=["POST"])
@require_permission("application", "reviewer_electrical", "editor")
def save_review(doc_id):
    json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(json_path):
        return "Not found", 404

    # --- Capture dashboard context & filtered order BEFORE mutating current record ---
    dashboard_query_raw = request.form.get("dashboard_query", '')
    orig_base_route = request.form.get("base_route") or request.args.get("base_route") or "main.review_all"
    dashboard_query_string, saved_params = normalize_dashboard_query(dashboard_query_raw)
    next_url = url_for(orig_base_route)
    if dashboard_query_string:
        next_url += f"?{dashboard_query_string}"

    reload_url = url_for("main.review", doc_id=doc_id)
    if dashboard_query_string:
        reload_url += f"?{dashboard_query_string}"
    process_param = saved_params.get("process") or request.args.get("process", "0")

    filter_args = {
        "flagged": saved_params.get("flagged") or request.args.get("flagged"),
        "modified": saved_params.get("modified") or request.args.get("modified"),
        "missed": saved_params.get("missed") or request.args.get("missed"),
        "archive": saved_params.get("archive") or request.args.get("archive"),
        "conf_max": saved_params.get("conf_max") or request.args.get("conf_max"),
        "building": saved_params.get("building") or request.args.get("building"),
        "filter_building": saved_params.get("filter_building") or request.args.get("filter_building"),
        "approved": saved_params.get("approved") or saved_params.get("filter_approved") or request.args.get("approved", ''),
        "filter_approved": saved_params.get("filter_approved") or request.args.get("filter_approved"),
        "filter_qr": saved_params.get("filter_qr") or request.args.get("filter_qr"),
        "filter_tag": saved_params.get("filter_tag") or request.args.get("filter_tag"),
        "filter_notes": saved_params.get("filter_notes") or request.args.get("filter_notes"),
        "filter_group": saved_params.get("filter_group") or request.args.get("filter_group"),
    }

    dist_mode = "exclude" if orig_base_route == "main.review_all" else "only"
    filtered_before, _ = get_filtered_data_and_counts(filter_args, process_param, distribution_mode=dist_mode)
    filtered_doc_ids = [item["doc_id"] for item in filtered_before]
    current_index = filtered_doc_ids.index(doc_id) if doc_id in filtered_doc_ids else None

    def _resolve_neighbor_doc(action_name):
        """Return the next or previous doc_id within the active filter, even when
        the current doc has just been filtered out (e.g., toggling Approved on a
        Pending-only view via /toggle_approved). Recomputes against a broader
        scope without the approved filter so the doc's position is known, then
        picks the closest neighbor that still matches the live filter. An empty
        string ('') disables the approved filter inside get_filtered_data_and_counts;
        passing None would let it fall back to the Pending-only default."""
        if action_name not in ("save_next", "save_prev"):
            return None
        # Honor the client-supplied neighbor first: the dashboard passes the
        # visible filtered+sorted order (column sort included) via the hidden
        # nav_next / nav_prev fields, so Save & Next/Prev follow exactly what the
        # reviewer saw on the Distribution dashboard. Fall back to the server
        # order only when the client value is missing or stale.
        client_nav = (request.form.get("nav_next") if action_name == "save_next"
                      else request.form.get("nav_prev")) or ""
        client_nav = client_nav.strip()
        if client_nav and JSON_NAME_RE.match(f"{client_nav}.json") \
                and os.path.exists(os.path.join(JSON_DIR, f"{client_nav}.json")):
            return client_nav
        if current_index is not None:
            if action_name == "save_next" and current_index + 1 < len(filtered_doc_ids):
                return filtered_doc_ids[current_index + 1]
            if action_name == "save_prev" and current_index > 0:
                return filtered_doc_ids[current_index - 1]
            return None
        broader_args = dict(filter_args)
        broader_args["approved"] = ""
        broader_args["filter_approved"] = ""
        broader_data, _ = get_filtered_data_and_counts(broader_args, process_param, distribution_mode=dist_mode)
        broader_ids = [item["doc_id"] for item in broader_data]
        if doc_id not in broader_ids:
            return None
        bi = broader_ids.index(doc_id)
        nav_set = set(filtered_doc_ids)
        if action_name == "save_next":
            for j in range(bi + 1, len(broader_ids)):
                if broader_ids[j] in nav_set:
                    return broader_ids[j]
        else:
            for j in range(bi - 1, -1, -1):
                if broader_ids[j] in nav_set:
                    return broader_ids[j]
        return None
    # ------------------------------------------------------------------------------
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return "Bad ID", 400
    qr, building = m.groups()
    action = request.form.get("action")
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        flash(f"Could not verify SDI package status: {exc}", "danger")
        return redirect(reload_url)
    if package_lock.get("locked"):
        if action in ("save_next", "save_prev"):
            neighbor = _resolve_neighbor_doc(action)
            if neighbor:
                suffix = f"?{dashboard_query_string}" if dashboard_query_string else ""
                return redirect(url_for('main.review', doc_id=neighbor) + suffix)
            return redirect(next_url)
        flash(_package_lock_message(package_lock), "warning")
        return redirect(reload_url)

    submitted_revision = request.form.get("review_revision", '')
    json_sync_lock.acquire()
    try:
        if not os.path.exists(json_path):
            return "Not found", 404

        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        try:
            ensure_revision_matches(json_data, submitted_revision)
        except RevisionConflictError as exc:
            flash(str(exc), "danger")
            return redirect(reload_url)

        asset_type = json_data.get("asset_type")
        structured = json_data.get("structured_data", {})
        if not isinstance(structured, dict):
            structured = {}
            json_data["structured_data"] = structured

        # The "save_toggle" action comes from the Pending/Approved pill: it submits
        # the full form alongside the flipped Approved value so the user's pending
        # edits land in the JSON. Bypass the approved-record early return so the
        # merge below actually runs.
        if str(structured.get("Approved", '') or "").strip() == "True" and action != "save_toggle":
            if action == "save_stay":
                # Approved records are read-only; nothing to save, stay on the page.
                return redirect(reload_url)
            neighbor = _resolve_neighbor_doc(action)
            if neighbor:
                suffix = f"?{dashboard_query_string}" if dashboard_query_string else ""
                return redirect(url_for('main.review', doc_id=neighbor) + suffix)
            return redirect(next_url)

        if "installation_date" in request.form:
            try:
                installation_date_iso = parse_installation_date(request.form.get("installation_date", ""))
                update_installation_date(
                    qrdb, DB_PATH, qr, installation_date_iso,
                    modified_by=current_user.username,
                    app_name="reviewer_electrical",
                    audit_log_change=_audit_log_change,
                )
            except (InstallationDateError, LookupError, RuntimeError) as exc:
                flash(str(exc), "danger")
                return redirect(reload_url)

        old_ubc_tag = (structured.get("UBC Asset Tag") or "").strip()
        old_branch_tag = (structured.get("Branch Panel") or "").strip()
        old_asset_group = (structured.get("Asset Group") or "").strip()
        old_description = str(structured.get("Description") or "")
        asset_group_manual = request.form.get("asset_group_manual") == "1"
        # Capture explicit form values for Asset Group / Description. The dictionary
        # auto-derive in _apply_tag_dictionary_first / _resolve_description rewrites
        # both fields whenever a tag-prefix entry exists, so any non-empty submission
        # must be re-applied after the derive runs to prevent silent clobbering --
        # including the common case where the form value already matches the JSON
        # state (e.g., a re-save after a successful previous save). Empty submissions
        # still defer to the dictionary so new/blank rows continue to auto-fill.
        # Exception: the legacy AI default "Panel - <tag>" is treated as a placeholder
        # (not a user edit) so the dictionary's correct prefix wins for CDP/TX/etc.
        form_asset_group_raw = request.form.get("Asset Group")
        form_description_raw = request.form.get("Description")
        form_ampere_raw = request.form.get("Ampere")
        form_volts_raw = request.form.get("Volts")
        form_supply_from_raw = request.form.get("Supply From")
        form_asset_group = (form_asset_group_raw or "").strip()
        form_description = str(form_description_raw or "")
        form_ampere_value = (
            _normalize_el_amperage_value(form_ampere_raw)
            if form_ampere_raw is not None
            else None
        )
        form_volts = str(form_volts_raw or "").strip()
        form_supply_from = str(form_supply_from_raw or "").strip()
        ubc_tag_for_placeholder_check = (
            str(request.form.get("UBC Asset Tag") or "").strip()
            or old_ubc_tag
            or old_branch_tag
        )
        honor_form_asset_group = form_asset_group_raw is not None and form_asset_group != ''
        honor_form_description = (
            form_description_raw is not None
            and form_description.strip() != ''
            and not _is_ai_default_description(form_description, ubc_tag_for_placeholder_check)
        )
        if honor_form_asset_group and form_asset_group != old_asset_group:
            asset_group_manual = True
        if form_volts_raw is not None:
            tag_for_volts = (
                str(request.form.get("UBC Asset Tag") or "").strip()
                or old_ubc_tag
                or old_branch_tag
            )
            derived_form_volts, _ = _derive_volts_loc(tag_for_volts)
            new_volts_manual = "1" if form_volts and form_volts != str(derived_form_volts or "").strip() else "0"
            if structured.get("volts_manual_override") != new_volts_manual:
                json_data["modified"] = True
            structured["volts_manual_override"] = new_volts_manual
        if form_supply_from_raw is not None:
            normalized_form_supply_from = _apply_el_supply_from_formatting(form_supply_from)
            new_supply_from_manual = (
                "1" if form_supply_from and form_supply_from != normalized_form_supply_from else "0"
            )
            if structured.get("supply_from_manual_override") != new_supply_from_manual:
                json_data["modified"] = True
            structured["supply_from_manual_override"] = new_supply_from_manual
        amperage_value = form_ampere_value if form_ampere_raw is not None else _get_el_amperage_value(structured)
        power_rating_value, power_rating_uom = _get_el_power_rating_pair(structured)
        structured["Ampere"] = amperage_value
        structured["Power Rating"] = power_rating_value
        structured["Power Rating (UoM)"] = power_rating_uom
        structured["Amperage Rating (UoM)"] = "AMP" if amperage_value else ""
        keep_blank = [
            "UBC Asset Tag", "Equipment ID", "Equipment Type", "Branch Panel", "Ampere", "Supply From", "Volts", "Location",
            "Power Type", "Power Rating", "Power Rating (UoM)", "Fed From Equipment ID", "Fed From Amperage Rating", "Fed From Amperage Rating (UoM)", "Attribute", "Approved", "Asset Group", "Description", "Amperage Rating (UoM)", "Main Asset",
            "Manufacturer", "Model", "Serial Number", "Year", "Capacity", "Capacity (UoM)"
        ]
        for k in keep_blank:
            structured.setdefault(k, '')
        structured.setdefault("Flagged", "false")
        # Persist asset_group_manual into structured so future renders /
        # list-loads know whether the saved Asset Group is a user override
        # ("1" = preserve over dict) or auto-derived ("0" = let dict refresh).
        # JS sets the hidden form field to "1" the moment the user clicks the
        # dropdown; once set, the saved value sticks until the user explicitly
        # picks a value matching the current dictionary.
        new_asset_group_manual = "1" if asset_group_manual else (
            str(structured.get("asset_group_manual") or "0").strip() or "0"
        )
        if structured.get("asset_group_manual") != new_asset_group_manual:
            json_data["modified"] = True
        structured["asset_group_manual"] = new_asset_group_manual
        if "Flagged" in request.form:
            new_flagged = "true" if request.form.get("Flagged") == "on" else "false"
            if structured.get("Flagged", "false") != new_flagged:
                json_data["modified"] = True
            structured["Flagged"] = new_flagged

        skip_fields = {"Flagged", "Approved", "Description"}
        hidden_passthrough_fields = {"Amperage Rating", "Equipment ID", "Equipment Type", "Power Type", "Power Rating", "Power Rating (UoM)", "Fed From Amperage Rating", "Fed From Amperage Rating (UoM)"}
        # form_variant is the render-time variant echo (general|distribution);
        # nav_prev/nav_next are pagination hints. None of them are asset data,
        # so they must never merge into structured_data.
        if merge_form_into_structured(
            structured,
            request.form,
            skip_fields=skip_fields,
            hidden_passthrough_fields=hidden_passthrough_fields,
            ignored_form_fields={"Flagged", "action", "dashboard_query", "new_qr_code", "review_revision", "base_route", "asset_group_manual", "installation_date", "form_variant", "nav_prev", "nav_next"},
        ):
            json_data["modified"] = True

        # The Pending/Approved pill (save_toggle) carries the flipped Approved value in the
        # form, but the generic merge intentionally skips "Approved". Apply it explicitly for
        # save_toggle so the Pending<->Approved flip actually persists to the JSON.
        if action == "save_toggle":
            new_approved = "True" if str(request.form.get("Approved", "")).strip() == "True" else ""
            if structured.get("Approved", "") != new_approved:
                structured["Approved"] = new_approved
                json_data["modified"] = True

        # Legacy gate: looked up once per record and reused for every
        # downstream derivation choice below (both the force-overwrite/
        # blank-fill block here and the apply_legacy_rules/dictionary-priority
        # call further down). Genuine POST request handler with `building` in
        # scope and an established flash+redirect(reload_url) pattern already
        # used elsewhere in this same try block (e.g. RevisionConflictError,
        # package-lock checks above) — the early `return` here still runs the
        # `finally: json_sync_lock.release()` below, same as those.
        with qrdb.get_connection(sqlite_path=DB_PATH) as _bp_conn:
            try:
                _process = el_legacy_flow.get_building_process(_bp_conn, building)
            except el_legacy_flow.BuildingProcessError as exc:
                flash(str(exc), "danger")
                return redirect(reload_url)

        normalized_amperage = form_ampere_value if form_ampere_raw is not None else _get_el_amperage_value(structured)
        desired_uom = "AMP" if normalized_amperage else ""
        if structured.get("Ampere", '') != normalized_amperage:
            structured["Ampere"] = normalized_amperage
            json_data["modified"] = True
        if structured.get("Amperage Rating", '') != normalized_amperage:
            structured["Amperage Rating"] = normalized_amperage
            json_data["modified"] = True
        if structured.get("Amperage Rating (UoM)", '') != desired_uom:
            structured["Amperage Rating (UoM)"] = desired_uom
            json_data["modified"] = True
        if _process != "Legacy":
            # Standard path, byte-identical: force-overwrite Equipment ID /
            # Equipment Type / Power Type from the standard tag-derivation
            # helpers on every save.
            desired_equipment_id = _get_el_equipment_id_value(structured.get("UBC Asset Tag"))
            if structured.get("Equipment ID", '') != desired_equipment_id:
                structured["Equipment ID"] = desired_equipment_id
                json_data["modified"] = True
            desired_equipment_type = _get_el_equipment_type_value(desired_equipment_id, structured.get("UBC Asset Tag"))
            if structured.get("Equipment Type", '') != desired_equipment_type:
                structured["Equipment Type"] = desired_equipment_type
                json_data["modified"] = True
            desired_power_type = _get_el_power_type_value(desired_equipment_id, structured.get("UBC Asset Tag"))
            if structured.get("Power Type", '') != desired_power_type:
                structured["Power Type"] = desired_power_type
                json_data["modified"] = True
        # Legacy: Equipment ID / Equipment Type / Power Type are never
        # force-overwritten here — apply_legacy_rules (below) owns them with
        # blank-fill-only semantics (never overwrites a non-blank reviewer
        # value; corroborated Power Type fills blank only).
        normalized_power_rating, normalized_power_rating_uom = _get_el_power_rating_pair(structured)
        if structured.get("Power Rating", '') != normalized_power_rating:
            structured["Power Rating"] = normalized_power_rating
            json_data["modified"] = True
        if structured.get("Power Rating (UoM)", '') != normalized_power_rating_uom:
            structured["Power Rating (UoM)"] = normalized_power_rating_uom
            json_data["modified"] = True
        supply_from_manual = _is_el_supply_from_manual(structured)
        raw_supply_from = str(structured.get("Supply From") or "").strip()
        if _process == "Legacy":
            # Legacy: keep Supply From exactly as submitted/stored — the
            # standard normalizer strips "MAIN" and destroys the MDC/DCC
            # discriminator that legacy_flow.normalize_legacy_supply_from
            # needs. Fed From Equipment ID is likewise owned by
            # apply_legacy_rules (blank-fill only) below.
            desired_supply_from = raw_supply_from
            desired_fed_from_equipment_id = str(structured.get("Fed From Equipment ID") or "").strip()
        else:
            normalized_supply_from = _apply_el_supply_from_formatting(raw_supply_from)
            desired_supply_from = raw_supply_from if supply_from_manual else normalized_supply_from
            if structured.get("Supply From", '') != desired_supply_from:
                structured["Supply From"] = desired_supply_from
                json_data["modified"] = True
            desired_fed_from_equipment_id = _get_el_fed_from_equipment_id_value(desired_supply_from)
            if structured.get("Fed From Equipment ID", '') != desired_fed_from_equipment_id:
                structured["Fed From Equipment ID"] = desired_fed_from_equipment_id
                json_data["modified"] = True
        desired_fed_from_amperage = _resolve_el_fed_from_amperage_value(
            building,
            desired_fed_from_equipment_id or desired_supply_from,
        )
        if structured.get("Fed From Amperage Rating", '') != desired_fed_from_amperage:
            structured["Fed From Amperage Rating"] = desired_fed_from_amperage
            json_data["modified"] = True
        desired_fed_from_amperage_uom = _get_el_fed_from_amperage_uom_value(desired_fed_from_amperage)
        if structured.get("Fed From Amperage Rating (UoM)", '') != desired_fed_from_amperage_uom:
            structured["Fed From Amperage Rating (UoM)"] = desired_fed_from_amperage_uom
            json_data["modified"] = True

        desc_form_value = str(request.form.get("Description", '') or "")
        if structured.get("Description", '') != desc_form_value:
            json_data["modified"] = True
        structured["Description"] = desc_form_value

        # Legacy (invariant 6): see the matching guard in
        # _sync_db_from_structured. _apply_tag_dictionary_first ->
        # _clear_legacy_tag_derived_location blanks a Location that coincidentally
        # equals the STANDARD tag-derived value ("Level 3" for PNL-EM3), which on
        # the Legacy path erases what the reviewer just typed in this very POST --
        # into the JSON and, via the sync calls below, into sdi_dataset_EL.
        # Snapshot/restore gated on `_process` (already hoisted above); the
        # _apply_tag_dictionary_first call itself and the Standard path are untouched.
        _legacy_location_snapshot = el_legacy_flow.snapshot_reviewer_location(structured) if _process == "Legacy" else ""
        tag_for_group, dict_changed = _apply_tag_dictionary_first(structured, asset_type)
        if _process == "Legacy":
            el_legacy_flow.restore_reviewer_location(structured, _legacy_location_snapshot)
        if not tag_for_group:
            tag_for_group = (structured.get("UBC Asset Tag") or "").strip() or (structured.get("Branch Panel") or "").strip()
        if dict_changed:
            json_data["modified"] = True
        if not structured.get("Attribute"):
            structured["Attribute"] = "Electrical"

        new_ubc_tag = (structured.get("UBC Asset Tag") or "").strip()
        new_branch_tag = (structured.get("Branch Panel") or "").strip()
        old_tag = old_ubc_tag or old_branch_tag
        new_tag = new_ubc_tag or new_branch_tag
        new_asset_group = (structured.get("Asset Group") or "").strip()
        derived_old_group = _get_asset_group_from_tag(old_tag, asset_type) if old_tag else ASSET_GROUP_DEFAULT
        tag_changed = bool(new_tag) and old_tag != new_tag
        old_group_auto = (not old_asset_group) or (old_asset_group == derived_old_group)
        if tag_changed and not asset_group_manual and old_group_auto:
            derived_new_group = _get_asset_group_from_tag(new_tag, asset_type)
            if derived_new_group and derived_new_group != new_asset_group:
                structured["Asset Group"] = derived_new_group
                json_data["modified"] = True

        if tag_for_group:
            # Gate: reuses the `_process` looked up once above.
            if _process == "Legacy":
                if el_legacy_flow.apply_legacy_rules(structured):
                    json_data["modified"] = True
            elif _apply_dictionary_priority(structured, tag_for_group):   # standard path, byte-identical
                json_data["modified"] = True
        if str(structured.get("volts_manual_override") or "").strip() == "1" and form_volts_raw is not None:
            if structured.get("Volts", '') != form_volts:
                structured["Volts"] = form_volts
                json_data["modified"] = True

        final_asset_group = structured.get("Asset Group")
        resolved_desc = _resolve_description(
            final_asset_group,
            tag_for_group,
            structured.get("Description")
        )
        if structured.get("Description") != resolved_desc:
            structured["Description"] = resolved_desc
            json_data["modified"] = True

        # Honor any explicit non-empty form value for Asset Group / Description over the
        # dictionary-derived value. _apply_mechanical_fallback rewrites both fields any
        # time a tag-prefix entry matches, so this restoration must run unconditionally
        # whenever the form carried a value -- including idempotent re-saves where the
        # form value already matches the JSON state.
        if honor_form_asset_group and structured.get("Asset Group", '') != form_asset_group:
            structured["Asset Group"] = form_asset_group
            json_data["modified"] = True
        if honor_form_description and structured.get("Description", '') != form_description:
            structured["Description"] = form_description
            json_data["modified"] = True

        new_qr_raw = request.form.get("new_qr_code", '')
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
            elif candidate != qr and _qr_conflicts(candidate, building):
                new_qr_error = f"QR code {candidate} already exists."
            elif candidate != qr:
                new_qr_clean = candidate
                json_data["modified"] = True

        target_qr = new_qr_clean or qr
        if _apply_qr_location_fallback(structured, target_qr):
            json_data["modified"] = True
        json_data["qr_code"] = target_qr
        _sync_el_review_quality_metadata(json_data, structured)

        atomic_write_json(json_path, json_data)

        if new_qr_error:
            try:
                _sync_db_from_structured(qr, building, structured, asset_type=asset_type, avg_ai_conf=_extract_avg_ai_conf(json_data), process=_process)
            except Exception as e:
                print(f"[WARN] DB sync failed (save_review): {e}")
            try:
                mark_json_processed(PROCESSED_JSON_LOG_EL, file_paths=[json_path])
            except Exception as e:
                print(f"[WARN] processed log update failed (save_review): {e}")
            flash(new_qr_error, "danger")
            return redirect(reload_url)

        if new_qr_clean:
            new_doc_id = f"{new_qr_clean}_EL_{building}"
            new_filename = f"{new_doc_id}.json"
            new_path = os.path.join(JSON_DIR, new_filename)
            if os.path.exists(new_path):
                flash(f"QR code {new_qr_clean} is already linked to another record.", "danger")
                try:
                    _sync_db_from_structured(qr, building, structured, asset_type=asset_type, avg_ai_conf=_extract_avg_ai_conf(json_data), process=_process)
                except Exception as e:
                    print(f"[WARN] DB sync failed (save_review): {e}")
                try:
                    mark_json_processed(PROCESSED_JSON_LOG_EL, file_paths=[json_path])
                except Exception as e:
                    print(f"[WARN] processed log update failed (save_review): {e}")
                return redirect(reload_url)
            try:
                old_qr = qr
                old_filename = os.path.basename(json_path)
                os.rename(json_path, new_path)
                _update_processed_json_log_filename(old_filename, new_filename)
                json_path = new_path
                doc_id = new_doc_id
                qr = new_qr_clean
                reload_url = url_for("main.review", doc_id=doc_id)
                if dashboard_query_string:
                    reload_url += f"?{dashboard_query_string}"
                _rename_asset_images(old_qr, new_qr_clean, building)
                _replace_qr_in_db(old_qr, new_qr_clean)
            except Exception as e:
                flash(f"Could not move asset to the new QR code: {e}", "danger")
                try:
                    _sync_db_from_structured(qr, building, structured, asset_type=asset_type, avg_ai_conf=_extract_avg_ai_conf(json_data), process=_process)
                except Exception as sync_error:
                    print(f"[WARN] DB sync failed (save_review): {sync_error}")
                try:
                    mark_json_processed(PROCESSED_JSON_LOG_EL, file_paths=[json_path])
                except Exception as mark_error:
                    print(f"[WARN] processed log update failed (save_review): {mark_error}")
                return redirect(reload_url)

        try:
            _sync_db_from_structured(qr, building, structured, asset_type=asset_type, avg_ai_conf=_extract_avg_ai_conf(json_data), process=_process)
        except Exception as e:
            print(f"[WARN] DB sync failed (save_review): {e}")
        try:
            mark_json_processed(PROCESSED_JSON_LOG_EL, file_paths=[json_path])
        except Exception as e:
            print(f"[WARN] processed log update failed (save_review): {e}")
    finally:
        json_sync_lock.release()

    # When the Pending pill submitted the form, return to the same review page
    # so the lock/unlock state of the now-saved Approved value takes effect.
    # The Save button (save_stay) also returns here so the reviewer keeps editing.
    if action in ("save_toggle", "save_stay"):
        if action == "save_stay":
            flash("Changes saved.", "success")
        suffix = f"?{dashboard_query_string}" if dashboard_query_string else ""
        return redirect(url_for('main.review', doc_id=doc_id) + suffix)

    neighbor = _resolve_neighbor_doc(action)
    if neighbor:
        suffix = f"?{dashboard_query_string}" if dashboard_query_string else ""
        return redirect(url_for('main.review', doc_id=neighbor) + suffix)

    return redirect(next_url)

@main_bp.route("/toggle_approved/<doc_id>", methods=["POST"])
@require_permission("application", "reviewer_electrical", "editor")
def toggle_approved(doc_id):
    json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
    if not os.path.exists(json_path):
        return jsonify({"success": False, "error": "Not found"}), 404
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return jsonify({"success": False, "error": "Bad ID"}), 400
    qr, building = m.groups()
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        return _package_lock_check_failed_response(exc)
    if package_lock.get("locked"):
        return _package_lock_response(package_lock)
    try:
        json_sync_lock.acquire()
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            structured = json_data.get("structured_data", {})
            if not isinstance(structured, dict):
                structured = {}
                json_data["structured_data"] = structured
            cur_val = structured.get("Approved", '')
            new_val = "True" if cur_val == "" else ""
            structured["Approved"] = new_val

            # Legacy gate (task-6 review Finding G): mirrors save_review's gate
            # -- the standard Supply From / Fed From Equipment ID force-set
            # below would otherwise strip "MAIN" and destroy the MDC/DCC
            # discriminator legacy_flow needs, persisting a standard-derived
            # value to sdi_dataset_EL on every Approve click for a Legacy
            # building. toggle_approved is a JSON API endpoint (every branch
            # in this function returns jsonify(...); no template/redirect
            # anywhere), so the equivalent of flash+redirect here is a JSON
            # error response matching this function's own existing
            # error-response style (e.g. the "Not found"/"Bad ID" returns
            # above).
            with qrdb.get_connection(sqlite_path=DB_PATH) as _bp_conn:
                try:
                    _process = el_legacy_flow.get_building_process(_bp_conn, building)
                except el_legacy_flow.BuildingProcessError as exc:
                    return jsonify({"success": False, "error": str(exc)}), 409

            if _process == "Legacy":
                # Legacy: keep Supply From / Fed From Equipment ID exactly as
                # already stored -- apply_legacy_rules owns them.
                #
                # Run the legacy rules here as well: approving does NOT require a
                # prior save (a reviewer can approve straight from the dashboard,
                # and the BF/EL bulk-approve queue fires this endpoint per row), so
                # without this call the upsert below would persist blank/raw values
                # to sdi_dataset_EL -- exactly the fields the standard branch
                # force-sets and the Legacy branch deliberately skips. Safe to run
                # unconditionally: apply_legacy_rules is blank-fill only and never
                # overwrites a non-blank reviewer value (invariant 6). Placed before
                # the Fed From Amperage lookup below so it sees the legacy-derived
                # Fed From Equipment ID.
                el_legacy_flow.apply_legacy_rules(structured)
                stored_supply_from = str(structured.get("Supply From") or "").strip()
                structured.setdefault("Fed From Equipment ID", "")
            else:
                # Standard path, byte-identical.
                stored_supply_from = _get_el_supply_from_stored_value(structured)
                structured["Supply From"] = stored_supply_from
            _apply_qr_location_fallback(structured, qr)
            if _process != "Legacy":
                structured["Fed From Equipment ID"] = _get_el_fed_from_equipment_id_value(stored_supply_from)
            fed_from_amperage_value = _resolve_el_fed_from_amperage_value(
                building,
                structured["Fed From Equipment ID"] or stored_supply_from,
            )
            structured["Fed From Amperage Rating"] = fed_from_amperage_value
            structured["Fed From Amperage Rating (UoM)"] = _get_el_fed_from_amperage_uom_value(fed_from_amperage_value)
            json_data["structured_data"] = structured
            _sync_el_review_quality_metadata(json_data, structured)
            atomic_write_json(json_path, json_data)
            # PG CHECK constraint requires Approved IN ('0','1'); SQLite also normalized to '0'/'1' in Phase A.
            db_val = "1" if new_val == "True" else "0"
            _db_upsert_qr_approved(qr_code_id=qr, approved_text=db_val)
            try:
                _sync_db_from_structured(qr, building, structured, asset_type=json_data.get("asset_type"), avg_ai_conf=_extract_avg_ai_conf(json_data), process=_process)
            except Exception as e:
                print(f"[WARN] DB sync failed (toggle_approved): {e}")
            try:
                mark_json_processed(PROCESSED_JSON_LOG_EL, file_paths=[json_path])
            except Exception as e:
                print(f"[WARN] processed log update failed (toggle_approved): {e}")
            return jsonify({
                "success": True,
                "new_value": structured["Approved"],
                "review_revision": compute_review_revision(json_data),
            })
        finally:
            json_sync_lock.release()
    except Exception as e:
        import traceback
        print(f"[ERROR] toggle_approved failed for {doc_id}: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500



def _reprocess_json_protected(json_path: str) -> str:
    """Return a non-empty reason if the EL JSON holds reviewer-owned state that an AI
    re-extraction must not overwrite (do not erase human overrides). Checks
    structured_data.Approved == "True" (reviewed) and the top-level 'modified' flag
    (set whenever a reviewer edits any field)."""
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


@main_bp.route("/toggle_ai_status/<doc_id>", methods=["POST"])
@require_permission("application", "reviewer_electrical", "editor")
def toggle_ai_status(doc_id):
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return jsonify({"success": False, "error": "Invalid ID"}), 400
    qr, _ = m.groups()
    # Explicit, confirmed override from the UI to reprocess despite draft edits.
    force = str(request.values.get("force", "")).strip().lower() in ("1", "true", "yes")
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        return _package_lock_check_failed_response(exc)
    if package_lock.get("locked"):
        return _package_lock_response(package_lock)
    try:
        new_val = "0"
        reprocess_moved = ""
        json_path = os.path.join(JSON_DIR, f"{doc_id}.json")
        # Path-traversal guard: keep os.replace within JSON_DIR even for a crafted doc_id.
        try:
            _jdir_real = os.path.realpath(JSON_DIR)
            if os.path.commonpath([os.path.realpath(json_path), _jdir_real]) != _jdir_real:
                json_path = ""
        except Exception:
            json_path = ""
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            columns = _get_qr_codes_columns()
            qr_col, _ = _resolve_qr_codes_columns()
            if not qr_col:
                return jsonify({"success": False, "error": "QR code column not found"}), 500
            ai_col = _resolve_qr_codes_ai_column(columns)
            if not ai_col:
                cur.execute(f'ALTER TABLE "{QR_CODES_TABLE}" ADD COLUMN "ai_status" TEXT DEFAULT "0"')
                _get_qr_codes_columns.cache_clear()
                columns = _get_qr_codes_columns()
                ai_col = _resolve_qr_codes_ai_column(columns)
            if not ai_col:
                return jsonify({"success": False, "error": "AI Status column not found"}), 500

            cur.execute(f'SELECT "{ai_col}" FROM "{QR_CODES_TABLE}" WHERE "{qr_col}" = ? LIMIT 1', (qr,))
            row = cur.fetchone()
            current_val = "0"
            if row:
                current_val = "1" if str(row[0]) == "1" else "0"
            new_val = "0" if current_val == "1" else "1"

            # Toggling to '0' = re-extract request: move the JSON aside so the EL extractor's
            # skip-if-exists guard re-runs it. Block (protect human work) when the JSON is
            # Approved or human-edited, or the asset is a Manual Entry (Col_process=2).
            # 'modified' (draft edits) may be overridden by an explicit, confirmed force.
            will_reprocess = False
            forced_reprocess = False
            bak_path = ""
            if new_val == "0" and json_path and os.path.isfile(json_path):
                protected = _reprocess_json_protected(json_path)
                if protected and not (protected == "modified" and force):
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

            cur.execute(f'UPDATE "{QR_CODES_TABLE}" SET "{ai_col}" = ? WHERE "{qr_col}" = ?', (new_val, qr))
            if cur.rowcount == 0:
                cur.execute(
                    f'INSERT INTO "{QR_CODES_TABLE}" ("{qr_col}", "{ai_col}") VALUES (?, ?)',
                    (qr, new_val),
                )
            conn.commit()

        # Move the JSON aside AFTER the DB commit so a crash/commit failure can never leave
        # the file gone while ai_status is unchanged (which would make the asset unreachable).
        if will_reprocess and os.path.isfile(json_path):
            try:
                os.replace(json_path, bak_path)
                reprocess_moved = os.path.basename(bak_path)
                if forced_reprocess:
                    print(f"[reprocess] EL FORCED reprocess {qr}: discarded manual edits; backup {os.path.basename(bak_path)}")
            except Exception as move_exc:
                print(f"[reprocess] EL toggle_ai_status JSON move failed for {qr}: {move_exc}")

        return jsonify({"success": True, "new_value": new_val, "reprocess_requested": bool(reprocess_moved)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/toggle_sdi/<doc_id>", methods=["POST"])
@require_permission("application", "reviewer_electrical", "editor")
def toggle_sdi(doc_id):
    m = JSON_NAME_RE.match(f"{doc_id}.json")
    if not m:
        return jsonify({"success": False, "error": "Invalid ID"}), 400
    qr, _ = m.groups()
    try:
        package_lock = _get_qr_package_lock(qr, raise_on_error=True)
    except Exception as exc:
        return _package_lock_check_failed_response(exc)
    if package_lock.get("locked"):
        return _package_lock_response(package_lock)
    try:
        new_val = _db_toggle_qr_sdi(qr)
        return jsonify({"success": True, "new_value": new_val})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@main_bp.route("/check_sdi/<qr_code>")
@require_permission("application", "reviewer_electrical", "viewer")
def check_sdi(qr_code):
    if not _connectable():
        return jsonify({"error": "Database not accessible"}), 500
    try:
        package_lock = _get_qr_package_lock(qr_code, raise_on_error=True)
        return jsonify({
            "exists": bool(package_lock.get("locked")),
            "source": package_lock.get("source", ''),
            "package_id": package_lock.get("package_id", ''),
        })
    except qrdb.DatabaseError as e:
        if "no such table" in str(e).lower():
            return jsonify({"exists": False})
        return jsonify({"error": f"Database query failed: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@main_bp.route("/api/ai_status_map")
@require_permission("application", "reviewer_electrical", "viewer")
def ai_status_map():
    """Read-only {QR: ai_status} map for the dashboard's AI-status auto-refresh
    poller. Must stay read-only: it never writes and never triggers extraction."""
    if not _connectable():
        return jsonify({"success": False, "error": "Database not accessible"}), 500
    try:
        columns = _get_qr_codes_columns()
        qr_col, _ = _resolve_qr_codes_columns()
        ai_col = _resolve_qr_codes_ai_column(columns)
        if not qr_col or not ai_col:
            return jsonify({"success": True, "statuses": {}})
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(f'SELECT "{qr_col}", "{ai_col}" FROM "{QR_CODES_TABLE}"')
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
            cols = list(qrdb.table_columns(conn, 'QR_code_assets'))
            if "user" not in cols or "date_hour" not in cols:
                return out
            chunk = 500
            for i in range(0, len(cleaned), chunk):
                part = cleaned[i:i + chunk]
                placeholders = ",".join("?" for _ in part)
                qr_prefix = _qr_prefix_expr()
                cur.execute(
                    # PG: MAX("user")/MAX("date_hour") so strict GROUP BY accepts them.
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


@main_bp.route("/export/review-xlsx", methods=["POST"])
@require_permission("application", "reviewer_electrical", "viewer")
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
    logo_path = os.path.join(BASE_DIR, "review_asset_templates", "static", "ubc-facilities_logo.jpg")
    blob = excel_export.build_workbook(
        process="EL",
        tab=tab,
        building=building,
        rows=rows,
        meta=meta,
        process_title="Electrical",
        logo_path=logo_path,
        distribution_groups=get_distribution_asset_groups(),
    )
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    if not bld_codes:
        bld = "all"
    elif len(bld_codes) <= 3:
        bld = "_".join(bld_codes)
    else:
        bld = f"{bld_codes[0]}_plus{len(bld_codes) - 1}"
    fname = f"Review_EL_{bld}_{ts}.xlsx"
    return send_file(
        BytesIO(blob),
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@main_bp.route("/api/user-activity", methods=["GET", "POST"])
@require_permission("application", "reviewer_electrical", "viewer")
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
            
            # Check if required columns exist
            columns = list(qrdb.table_columns(conn, 'QR_code_assets'))
            
            if "user" not in columns or "date_hour" not in columns:
                return jsonify({"error": "User tracking columns not available."}), 404
            
            # PG quirks: "user" is reserved; "QR_code_assets"/"ID" mixed case; INSTR SQLite-only.
            # MAX("user")/MAX("date_hour") aggregate-wraps so PG's strict GROUP BY accepts them.
            qr_prefix = _qr_prefix_expr()

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
                      AND (code_assets LIKE '% EL -%' OR code_assets LIKE '% EL %')
                    GROUP BY {qr_prefix}
                    ORDER BY MAX("date_hour") DESC
                """)
                rows = cursor.fetchall()

                cursor.execute("""
                    SELECT DISTINCT "user" FROM "QR_code_assets"
                    WHERE "user" IS NOT NULL AND "user" != ''
                      AND (code_assets LIKE '% EL -%' OR code_assets LIKE '% EL %')
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
            
    except qrdb.DatabaseError as e:
        return jsonify({"error": str(e)}), 500


@main_bp.route("/images/<path:filename>")
@require_permission("application", "reviewer_electrical", "viewer")
def serve_image(filename):
    return send_from_directory(IMG_DIR, filename)

@main_bp.route("/api/fed_from_lookup/<building>/<supply_from>")
@login_required
@require_permission("application", "reviewer_electrical", "viewer")
def api_fed_from_lookup(building, supply_from):
    amperage = _resolve_el_fed_from_amperage_value(building, supply_from)
    uom = _get_el_fed_from_amperage_uom_value(amperage)
    equipment_id = _get_el_fed_from_equipment_id_value(supply_from)
    return jsonify({"fed_from_amperage": amperage, "fed_from_uom": uom, "fed_from_equipment_id": equipment_id})

app.register_blueprint(main_bp)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8005, debug=True, use_reloader=False, threaded=True)
