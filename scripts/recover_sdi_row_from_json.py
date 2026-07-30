"""Re-populate sdi_dataset_EL for any EL asset from its JSON file.

Standalone recovery for incidents where a sdi_dataset_EL row is wiped.
Values come from the JSON file plus derived columns the EL dashboard
normally computes (Equipment ID/Type, Power Type, Amperage UoM, Voltage
UoM, Asset Group, Description, Fed From Equipment ID, ID_check).

This script intentionally does NOT import Asset_dashboard_EL (which pulls a
full Flask app + auth stack). It writes the known-good column values directly.

IMPORTANT: After updating the DB this script also updates the EL processed-
JSON log so that sync_json_directory_to_db_el does not immediately re-read
the (possibly sparse) on-disk JSON and overwrite the recovered row.

Usage on the VM:

    python scripts/recover_sdi_row_from_json.py \\
        --json-path /home/developer/Output_jason_api/0000184486_EL_217.json \\
        --db-path /home/developer/asset_capture_app_dev/data/QR_codes.db \\
        --dry-run                         # preview, no write
    python scripts/recover_sdi_row_from_json.py \\
        --json-path /home/developer/Output_jason_api/0000184486_EL_217.json \\
        --db-path /home/developer/asset_capture_app_dev/data/QR_codes.db
                                          # commit the recovery

Safe to re-run: UPDATE first, INSERT only if no row exists.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, Optional

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
DEFAULT_JSON_PATH = os.path.join(
    REPO_ROOT, "Output_jason_api", "0000184474_EL_217.json"
)
DEFAULT_DB_PATH = os.path.join(
    REPO_ROOT, "asset_capture_app_dev", "data", "QR_codes.db"
)

# Relative to the DB file — mirrors PROCESSED_JSON_LOG_EL in Asset_dashboard_EL.py
PROCESSED_JSON_LOG_NAME = "processed_json_el.log"

SDI_TABLE = "sdi_dataset_EL"

# Derived mappings (mirrors electrical_equipment_rules.py; kept inline so the
# script stays runnable without that module on disk).
EQUIPMENT_TYPE_MAP = {
    "PNL": "Panel",
    "CDP": "Central Distribution Panel",
    "MDP": "Main Distribution Panel",
    "SPL": "Splitter",
    "MCC": "Motor Control Center",
    "SWBD": "Switchboard",
    "ATS": "Automatic Transfer Switch",
    "TX": "Transformer",
}
# "S" = Single-phase-ish service; this mirrors the current dashboard mapping
# for panel tags.
POWER_TYPE_MAP = {
    "PNL": "S",
    "CDP": "S",
    "MDP": "S",
    "SPL": "S",
    "MCC": "S",
    "SWBD": "S",
    "ATS": "S",
    "TX": "S",
}
ASSET_GROUP_MAP = {
    "PNL": "Panels",
    "CDP": "Other Service and Distribution",
    "MDP": "Other Service and Distribution",
    "SPL": "Other Service and Distribution",
    "MCC": "Motor Control Centers",
    "SWBD": "Other Service and Distribution",
    "ATS": "Automatic Transfer Switches",
    "TX": "Interior Distribution Transformers",
}


def _code_prefix(tag: str) -> str:
    t = (tag or "").strip().upper()
    for code in sorted(EQUIPMENT_TYPE_MAP, key=len, reverse=True):
        if t.startswith(code):
            return code
    return ""


def build_row_from_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    sd = payload.get("structured_data") or {}
    qr = str(payload.get("qr_code") or "").strip()
    building = str(payload.get("building_number") or "").strip()

    ubc_tag = str(sd.get("UBC Asset Tag") or "").strip()
    branch = str(sd.get("Branch Panel") or "").strip()
    supply_from = str(sd.get("Supply From") or "").strip()
    # Canonical storage keeps the bare voltage value ("208/120"); the unit is
    # carried in "Voltage Rating (UoM)" as VLT. Legacy JSONs may embed the letters.
    volts = re.sub(r"(?<=\d)\s*V(?:AC|DC|OLTS?)?\b", "", str(sd.get("Volts") or "").strip(), flags=re.IGNORECASE).strip()
    ampere = str(sd.get("Ampere") or "").strip()
    location = str(sd.get("Location") or "").strip()

    code = _code_prefix(ubc_tag)
    equipment_type = EQUIPMENT_TYPE_MAP.get(code, "")
    power_type = POWER_TYPE_MAP.get(code, "")
    asset_group = ASSET_GROUP_MAP.get(code, "Panels")
    description = (
        sd.get("Description")
        or (f"{equipment_type} - {ubc_tag}" if equipment_type and ubc_tag else "")
    )

    avg_ai = payload.get("Avg_ai_conf")
    try:
        avg_ai_f = float(avg_ai) if avg_ai is not None else None
    except (TypeError, ValueError):
        avg_ai_f = None

    row: Dict[str, Any] = {
        "QR Code": qr,
        "Building": building,
        "Description": description,
        "UBC Asset Tag": ubc_tag,
        "Branch Panel": branch,
        "Ampere": ampere,
        "Supply From": supply_from,
        "Volts": volts,
        "Location": location,
        "Asset Group": asset_group,
        "Attribute": "Electrical",
        "Approved": "",
        "Flagged": "0",
        "Avg_ai_conf": avg_ai_f,
        "Amperage Rating": ampere,
        "Amperage Rating (UoM)": "AMP" if ampere else "",
        "Equipment ID": ubc_tag,
        "Equipment Type": equipment_type,
        "Fed From Amperage Rating": "",
        "Fed From Amperage Rating (UoM)": "",
        "Fed From Equipment ID": supply_from,
        "Power Rating": str(sd.get("Power Rating") or "").strip(),
        "Power Rating (UoM)": str(sd.get("Power Rating (UoM)") or "").strip(),
        "Power Type": power_type,
        "Voltage Rating": volts,
        "Voltage Rating (UoM)": "VLT" if volts else "",
        "ID_check": f"{building} | {ubc_tag} | {supply_from}",
    }
    return row


def _snapshot_row(conn: sqlite3.Connection, qr: str, building: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        f'SELECT * FROM "{SDI_TABLE}" WHERE "QR Code" = ? AND "Building" = ?',
        (qr, building),
    ).fetchone()
    return dict(row) if row else None


def _existing_columns(conn) -> list:
    return qrdb.table_columns(conn, SDI_TABLE)


def _generated_columns(conn, table: str) -> set:
    """Names of GENERATED (read-only) columns to exclude from INSERT/UPDATE.
    PG: information_schema is_generated='ALWAYS'; SQLite: table_xinfo hidden 2/3.
    (e.g. sdi_dataset_EL.ID_check — writing it errors on PostgreSQL.)"""
    if qrdb.is_postgres():
        cur = qrdb.raw_conn(conn).cursor()
        try:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s AND is_generated='ALWAYS'", (table,))
            return {r[0] for r in cur.fetchall()}
        finally:
            cur.close()
    try:
        rows = conn.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
    except qrdb.DatabaseError:
        return set()
    return {r["name"] for r in rows if (r["hidden"] if "hidden" in r.keys() else 0) in (2, 3)}


def _print_diff(before: Optional[Dict[str, Any]], after: Dict[str, Any]) -> None:
    keys = sorted(set((before or {}).keys()) | set(after.keys()))
    print(f"  {'column':<35} {'BEFORE':<45} {'AFTER':<45}")
    for k in keys:
        b = "" if before is None else before.get(k)
        a = after.get(k)
        b_s = "<missing-row>" if before is None else ("" if b is None else str(b))
        a_s = "" if a is None else str(a)
        marker = "  " if b_s == a_s else "* "
        print(f"{marker}{k!r:<35} {b_s[:43]:<45} {a_s[:43]:<45}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-path", default=DEFAULT_JSON_PATH)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--processed-log",
        default=None,
        help=(
            "Path to the EL processed-JSON log file "
            "(default: <db-dir>/processed_json_el.log). "
            "Pass --no-mark-processed to skip this step."
        ),
    )
    parser.add_argument(
        "--no-mark-processed",
        action="store_true",
        help="Skip updating the processed-JSON log after recovery.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.json_path):
        print(f"[ERROR] JSON not found: {args.json_path}")
        return 2
    if not qrdb.is_postgres() and not os.path.exists(args.db_path):
        print(f"[ERROR] DB not found: {args.db_path}")
        return 3

    with open(args.json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    desired = build_row_from_json(payload)
    qr = desired["QR Code"]
    building = desired["Building"]
    if not qr or not building:
        print("[ERROR] JSON is missing qr_code or building_number.")
        return 4

    with qrdb.get_connection(sqlite_path=args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cols = _existing_columns(conn)
        if not cols:
            print(f"[ERROR] Table {SDI_TABLE} is missing or has no columns.")
            return 5

        generated = _generated_columns(conn, SDI_TABLE)
        writable = [c for c in desired if c in cols and c not in generated]
        skipped = [c for c in desired if c not in cols or c in generated]
        if skipped:
            print(f"[NOTE] Ignoring columns not writable (missing or generated): {skipped}")

        before = _snapshot_row(conn, qr, building)
        print(f"[BEFORE] QR={qr} Building={building}  "
              f"row {'exists' if before else 'MISSING'}")
        print("[DIFF]")
        _print_diff(before, desired)
        print()

        if args.dry_run:
            print("[DRY-RUN] No changes written.")
            return 0

        non_key = [c for c in writable if c not in ("QR Code", "Building")]
        set_clause = ", ".join(f'"{c}" = ?' for c in non_key)
        cur = conn.execute(
            f'UPDATE "{SDI_TABLE}" SET {set_clause} WHERE "QR Code" = ? AND "Building" = ?',
            [desired[c] for c in non_key] + [qr, building],
        )
        updated = cur.rowcount or 0
        if updated == 0:
            col_sql = ", ".join(f'"{c}"' for c in writable)
            placeholders = ", ".join(["?"] * len(writable))
            conn.execute(
                f'INSERT INTO "{SDI_TABLE}" ({col_sql}) VALUES ({placeholders})',
                [desired[c] for c in writable],
            )
            action = "inserted"
        else:
            action = f"updated ({updated} row)"
        conn.commit()
        after = _snapshot_row(conn, qr, building)

    print(f"[OK] sdi_dataset_EL row {action}.")
    print("[AFTER]")
    _print_diff(before, after or {})

    # Update the processed-JSON log so sync_json_directory_to_db_el does not
    # immediately re-read the on-disk JSON (which may still be sparse) and
    # overwrite the row we just recovered.
    if not args.no_mark_processed:
        log_path = args.processed_log or os.path.join(
            os.path.dirname(os.path.abspath(args.db_path)),
            PROCESSED_JSON_LOG_NAME,
        )
        try:
            log_data: dict = {}
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as lf:
                    import json as _json_mod
                    log_data = _json_mod.load(lf)
            json_filename = os.path.basename(args.json_path)
            json_mtime = os.path.getmtime(args.json_path)
            log_data[json_filename] = json_mtime
            tmp = log_path + ".part"
            with open(tmp, "w", encoding="utf-8") as lf:
                import json as _json_mod2
                _json_mod2.dump(log_data, lf, indent=2)
            os.replace(tmp, log_path)
            print(f"[OK] Marked {json_filename} as processed in {log_path}")
        except Exception as exc:
            print(f"[WARN] Could not update processed log at {log_path}: {exc}")
            print("       Run: touch <json_path> then let the dashboard sync skip it via the new guard.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
