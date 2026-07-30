#!/usr/bin/env python3
"""One-shot backfill of the new ``Main Asset`` column in ``sdi_dataset`` and
``sdi_dataset_EL``.

Per-row sourcing:
- **EL rows** (``sdi_dataset_EL``): prefer ``structured_data["Main Asset"]`` from
  the matching ``Output_jason_api/<QR>_EL_<bld>.json``. If absent, look up the
  UBC tag prefix in the mechanical dictionary at the composite key
  ``<prefix>|EL`` (then a plain-prefix legacy fallback).
- **ME rows** in ``sdi_dataset`` (rows whose JSON exists at
  ``Output_jason_api/<QR>_ME_<bld>.json``): prefer ``structured["Main Asset"]``,
  else dictionary lookup with composite key ``<prefix>|ME``.
- **BF rows** in ``sdi_dataset`` (JSON at ``<QR>_BF_<bld>.json``): hardcoded
  constant ``"Domestic Water Distribution Equipment"`` (matches the new
  write-on-save behavior in ``asset_plate_reviewer_bf.py``).
- Rows where neither path resolves: empty string (per plan: surfaces dictionary
  gaps rather than inventing values).

Safety:
- Timestamped DB backup before any write.
- ``--dry-run`` prints intended updates without touching the DB.
- Only fills rows whose current ``Main Asset`` is NULL or empty (re-runs are
  idempotent).
- ``ALTER TABLE … ADD COLUMN`` is guarded by ``PRAGMA table_info`` so re-runs do
  not raise on existing columns.

Out of scope (intentional):
- ``sdi_print_out`` / ``sdi_print_out_arch`` (Planon export) — separate
  follow-up once Planon's expected schema is confirmed.

Usage:
    python3 scripts/backfill_main_asset.py --dry-run
    python3 scripts/backfill_main_asset.py
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from typing import Dict, Optional, Tuple

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_DB = "/home/developer/asset_capture_app_dev/data/QR_codes.db"
DEFAULT_JSON_DIR = "/home/developer/Output_jason_api"
DEFAULT_DICT = "/home/developer/dictionary/mechanical_dictionary.py"

LOCAL_DB_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "asset_capture_app_dev",
    "data",
    "QR_codes.db",
)
LOCAL_JSON_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "Output_jason_api"
)
LOCAL_DICT_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "dictionary",
    "mechanical_dictionary.py",
)

BF_CONSTANT = "Domestic Water Distribution Equipment"

# ---------------------------------------------------------------------------
# Mechanical dictionary loader (ast-based, no Flask dependency)
# ---------------------------------------------------------------------------


def _load_mechanical_dictionary(path: str) -> Dict[str, dict]:
    if not path or not os.path.exists(path):
        print(f"[WARN] mechanical dictionary not found at {path}; tag-prefix fallback disabled.")
        return {}
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "ASSET_DICTIONARY":
                    return ast.literal_eval(node.value)
    return {}


def _resolve_main_asset_from_dict(
    asset_dict: Dict[str, dict], tag: str, asset_type: str
) -> str:
    """Return the dictionary's ``main_asset`` for a tag, or ``""``.

    Mirrors the lookup pattern in ``API_interface_EL_ver00.py``: exact composite
    key first, then composite-prefix match, then plain-prefix legacy fallback.
    """
    clean = (tag or "").strip().upper()
    disc = (asset_type or "").strip().upper()
    if not clean or not asset_dict:
        return ""
    entry = asset_dict.get(f"{clean}|{disc}")
    if entry is None:
        composite_keys = sorted(
            (k for k in asset_dict if "|" in k), key=len, reverse=True
        )
        for k in composite_keys:
            tp, _, kt = k.partition("|")
            if kt.upper() == disc and clean.startswith(tp.upper()):
                entry = asset_dict[k]
                break
    if entry is None:
        plain_keys = sorted(
            (k for k in asset_dict if "|" not in k), key=len, reverse=True
        )
        for k in plain_keys:
            if clean.startswith(k.upper()):
                entry = asset_dict[k]
                break
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("main_asset") or "").strip()


# ---------------------------------------------------------------------------
# JSON locator
# ---------------------------------------------------------------------------

_JSON_NAME = re.compile(r"^([A-Z0-9]+)_([A-Z]+)_(.+)\.json$")


def _find_json_for(qr: str, building: str, json_dir: str, discipline_hint: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return (path, discipline) for the first matching JSON, or (None, None).

    discipline_hint constrains the search when known (e.g. EL rows live in
    ``sdi_dataset_EL`` and only need EL JSONs).
    """
    if discipline_hint:
        candidate = os.path.join(json_dir, f"{qr}_{discipline_hint}_{building}.json")
        if os.path.exists(candidate):
            return candidate, discipline_hint
        return None, None
    for path in sorted(glob.glob(os.path.join(json_dir, f"{qr}_*_{building}.json"))):
        m = _JSON_NAME.match(os.path.basename(path))
        if not m:
            continue
        _, disc, _ = m.groups()
        return path, disc.upper()
    return None, None


def _read_structured(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        print(f"[WARN] could not read {path}: {exc}")
        return {}
    sd = payload.get("structured_data")
    return sd if isinstance(sd, dict) else {}


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _table_columns(conn, table: str):
    return qrdb.table_columns(conn, table)


def _ensure_main_asset_column(conn: sqlite3.Connection, table: str, dry_run: bool) -> bool:
    cols = _table_columns(conn, table)
    if not cols:
        print(f"[WARN] table {table} not found; skipping.")
        return False
    if "Main Asset" in cols:
        return True
    if dry_run:
        print(f"[DRY RUN] would: ALTER TABLE {table} ADD COLUMN \"Main Asset\" TEXT")
        return True
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "Main Asset" TEXT')
    conn.commit()
    print(f"  added column Main Asset to {table}")
    return True


# ---------------------------------------------------------------------------
# Per-table backfill
# ---------------------------------------------------------------------------


def _resolve_main_asset(
    qr: str,
    building: str,
    discipline: str,
    asset_dict: Dict[str, dict],
    json_dir: str,
) -> str:
    """Source-of-truth resolver mirroring the new write-on-save behavior."""
    if discipline == "BF":
        return BF_CONSTANT

    json_path, _ = _find_json_for(qr, building, json_dir, discipline_hint=discipline)
    if json_path:
        sd = _read_structured(json_path)
        ma = str(sd.get("Main Asset") or "").strip()
        if ma:
            return ma
        # Fall back to the same dict the review apps use server-side.
        tag = (sd.get("UBC Asset Tag") or sd.get("UBC Tag") or sd.get("Branch Panel") or "").strip()
        if tag:
            return _resolve_main_asset_from_dict(asset_dict, tag, discipline)

    # No JSON found — try to discover discipline from any JSON for this QR/Bld.
    fallback_path, fallback_disc = _find_json_for(qr, building, json_dir)
    if fallback_path and fallback_disc:
        if fallback_disc == "BF":
            return BF_CONSTANT
        sd = _read_structured(fallback_path)
        ma = str(sd.get("Main Asset") or "").strip()
        if ma:
            return ma
        tag = (sd.get("UBC Asset Tag") or sd.get("UBC Tag") or sd.get("Branch Panel") or "").strip()
        if tag:
            return _resolve_main_asset_from_dict(asset_dict, tag, fallback_disc)

    return ""


def _backfill_table(
    conn: sqlite3.Connection,
    table: str,
    discipline_hint_for_table: Optional[str],
    asset_dict: Dict[str, dict],
    json_dir: str,
    dry_run: bool,
) -> Tuple[int, int]:
    """Returns (eligible_row_count, updated_row_count). For sdi_dataset
    (mixed ME + BF), discipline is determined by JSON suffix.
    """
    cols = _table_columns(conn, table)
    if not cols:
        return (0, 0)
    column_present = "Main Asset" in cols
    if not column_present and not dry_run:
        # Live mode without the column means the ALTER step didn't run; abort
        # to avoid silently skipping the whole table.
        print(f"[ERROR] {table} is missing the Main Asset column even though we tried to add it.")
        return (0, 0)
    has_building = "Building" in cols
    if column_present:
        where = ' WHERE TRIM(COALESCE("Main Asset", \'\')) = \'\''
    else:
        # In dry-run before the ALTER actually runs, scan every row so the user
        # gets a meaningful preview of what the live run would touch.
        where = ""
    rowid_expr = "ctid" if qrdb.is_postgres() else "rowid"
    rowid_pred = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
    select_sql = (
        f'SELECT {rowid_expr}, "QR Code"' + (', "Building"' if has_building else "")
        + f' FROM "{table}"{where}'
    )
    rows = list(conn.execute(select_sql))
    eligible = len(rows)
    print(f"  {table}: {eligible} row(s) eligible for backfill")
    if not rows:
        return (0, 0)

    updated = 0
    for r in rows:
        rowid = r[0]
        qr = (r[1] or "").strip()
        bld = (r[2] or "").strip() if has_building else ""
        if not qr:
            continue
        # Force discipline for EL table; for sdi_dataset we infer from JSON.
        discipline = discipline_hint_for_table or ""
        if not discipline:
            _, found_disc = _find_json_for(qr, bld, json_dir)
            discipline = (found_disc or "").upper()
        value = _resolve_main_asset(qr, bld, discipline, asset_dict, json_dir)
        if not value:
            continue  # leave NULL/empty so future runs (or future captures) can fill
        if dry_run:
            print(f"    [DRY] {table} rowid={rowid} qr={qr} bld={bld} -> {value!r}")
            updated += 1
            continue
        conn.execute(
            f'UPDATE "{table}" SET "Main Asset" = ? WHERE {rowid_pred}',
            (value, rowid),
        )
        updated += 1
    if not dry_run:
        conn.commit()
    print(f"  {table}: {updated} row(s) {'would be ' if dry_run else ''}updated")
    return (eligible, updated)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_db(db_path: str) -> str:
    if qrdb.is_postgres():
        return ""  # SQLite-file backup is meaningless on PostgreSQL; use pg_dump.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = db_path.replace(".db", f".bak_{stamp}_main_asset_backfill.db")
    shutil.copy2(db_path, dest)
    return dest


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without modifying DB.")
    ap.add_argument(
        "--db",
        default=DEFAULT_DB if os.path.exists(DEFAULT_DB) else LOCAL_DB_FALLBACK,
        help="Path to QR_codes.db",
    )
    ap.add_argument(
        "--json-dir",
        default=DEFAULT_JSON_DIR if os.path.isdir(DEFAULT_JSON_DIR) else LOCAL_JSON_FALLBACK,
        help="Path to Output_jason_api directory",
    )
    ap.add_argument(
        "--dict",
        default=DEFAULT_DICT if os.path.exists(DEFAULT_DICT) else LOCAL_DICT_FALLBACK,
        help="Path to mechanical_dictionary.py",
    )
    args = ap.parse_args()

    print(f"DB:        {args.db}")
    print(f"JSON dir:  {args.json_dir}")
    print(f"Dict:      {args.dict}")
    print(f"Mode:      {'DRY-RUN' if args.dry_run else 'APPLY'}")
    if not os.path.exists(args.db):
        print(f"[ERROR] DB not found at {args.db}")
        return 2

    asset_dict = _load_mechanical_dictionary(args.dict)
    print(f"Loaded {len(asset_dict)} dictionary entries.\n")

    if not args.dry_run:
        backup = backup_db(args.db)
        if backup:
            print(f"DB backed up to: {backup}\n")
        else:
            print("[WARN] PostgreSQL backend: SQLite-file backup skipped. "
                  "Take a pg_dump first if you need a restore point.\n")

    with qrdb.get_connection(sqlite_path=args.db) as conn:
        # Schema first.
        if not _ensure_main_asset_column(conn, "sdi_dataset", args.dry_run):
            return 3
        if not _ensure_main_asset_column(conn, "sdi_dataset_EL", args.dry_run):
            return 3

        # Backfills.
        sdi_eligible, sdi_updated = _backfill_table(
            conn, "sdi_dataset", None, asset_dict, args.json_dir, args.dry_run
        )
        el_eligible, el_updated = _backfill_table(
            conn, "sdi_dataset_EL", "EL", asset_dict, args.json_dir, args.dry_run
        )

    print("\n--- Summary ---")
    print(f"sdi_dataset:    {sdi_updated}/{sdi_eligible} rows {'would be ' if args.dry_run else ''}updated")
    print(f"sdi_dataset_EL: {el_updated}/{el_eligible} rows {'would be ' if args.dry_run else ''}updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
