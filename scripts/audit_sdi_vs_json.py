"""Detect placeholder-sync wipes in sdi_dataset* by cross-checking JSONs.

Runs stateless — for every ``Output_jason_api/<qr>_<TYPE>_<bldg>.json`` file
whose ``structured_data`` holds a real identity tag, confirm the matching SDI
row carries the same identity. If the JSON has real content but the DB row is
missing or its identity/signal columns are blank, the row is flagged as a
likely wipe.

Catches the class of incident documented in
``INCIDENT_2026-04-23_placeholder_sync_wipe.md`` quickly — no baseline
snapshot required, pure JSON-vs-DB comparison.

Exit code:
    0 — no anomalies
    1 — one or more anomalies
    2 — setup error (missing DB / JSON dir)

Usage:
    python3 scripts/audit_sdi_vs_json.py               # human table to stdout
    python3 scripts/audit_sdi_vs_json.py --quiet       # only print if anomalies
    python3 scripts/audit_sdi_vs_json.py --json-dir /path --db-path /path

Cron (runs hourly, records only anomalies):
    0 * * * *  /usr/bin/python3 /home/developer/scripts/audit_sdi_vs_json.py --quiet \
                   >> /home/developer/logs/sdi_audit.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from typing import Optional

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
DEFAULT_JSON_DIR = os.path.join(REPO_ROOT, "Output_jason_api")
DEFAULT_DB_PATH = os.path.join(
    REPO_ROOT, "asset_capture_app_dev", "data", "QR_codes.db"
)

JSON_NAME_RE = re.compile(r"^(\d+)_(EL|ME|BF)_(\d+)\.json$")


# Per-type SDI table and identity column. Signal list mirrors the production
# _sdi_row_has_data* guards so an anomaly flagged here is exactly one the
# widened guard would have prevented.
TYPE_CONFIG = {
    "EL": {
        "table": "sdi_dataset_EL",
        "identity_col": "UBC Asset Tag",
        "signal_cols": (
            "UBC Asset Tag", "Equipment ID", "Branch Panel", "Supply From",
            "Volts", "Ampere", "Location", "Description", "Asset Group",
        ),
    },
    "ME": {
        "table": "sdi_dataset",
        "identity_col": "UBC Tag",
        "signal_cols": (
            "UBC Tag", "Manufacturer", "Model", "Serial", "Asset Group",
            "Description",
        ),
    },
    "BF": {
        "table": "sdi_dataset",
        "identity_col": "UBC Tag",
        "signal_cols": (
            "UBC Tag", "Manufacturer", "Model", "Serial", "Asset Group",
            "Description", "Diameter", "Year", "Technical Safety BC",
        ),
    },
}


def _json_identity(sd: dict, asset_type: str) -> str:
    if asset_type == "EL":
        tag = (sd.get("UBC Asset Tag") or sd.get("Branch Panel") or "").strip()
    else:
        tag = (sd.get("UBC Asset Tag") or sd.get("UBC Tag") or "").strip()
    return tag


def _row_snapshot(conn: sqlite3.Connection, cfg: dict, qr: str, building: str) -> Optional[dict]:
    cols_quoted = ", ".join(f'"{c}"' for c in cfg["signal_cols"])
    sql = (
        f'SELECT {cols_quoted} FROM "{cfg["table"]}" '
        f'WHERE "QR Code"=? AND "Building"=? LIMIT 1'
    )
    row = conn.execute(sql, (qr, building)).fetchone()
    if row is None:
        return None
    return {c: row[i] for i, c in enumerate(cfg["signal_cols"])}


def _is_blank(value) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    return s == ""


def _row_signal_count(snapshot: dict, avg_ai_conf: Optional[float]) -> int:
    n = sum(0 if _is_blank(v) else 1 for v in snapshot.values())
    try:
        if avg_ai_conf is not None and float(avg_ai_conf) > 0:
            n += 1
    except (TypeError, ValueError):
        pass
    return n


def _avg_ai_conf(conn: sqlite3.Connection, table: str, qr: str, building: str) -> Optional[float]:
    try:
        row = conn.execute(
            f'SELECT "Avg_ai_conf" FROM "{table}" WHERE "QR Code"=? AND "Building"=? LIMIT 1',
            (qr, building),
        ).fetchone()
    except qrdb.DatabaseError:
        # Missing column/table: on PG the failed statement aborts the
        # transaction — roll back so the (read-only) scan loop can continue.
        if qrdb.is_postgres():
            conn.rollback()
        return None
    if row is None:
        return None
    return row[0]


def audit(json_dir: str, db_path: str) -> tuple[int, list[dict]]:
    """Returns (total_scanned, anomalies)."""
    anomalies: list[dict] = []
    scanned = 0
    if not os.path.isdir(json_dir):
        raise FileNotFoundError(f"JSON dir not found: {json_dir}")
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")

    with qrdb.get_connection(sqlite_path=db_path) as conn:
        conn.row_factory = None
        for name in os.listdir(json_dir):
            m = JSON_NAME_RE.match(name)
            if not m:
                continue
            qr, asset_type, building = m.groups()
            cfg = TYPE_CONFIG[asset_type]
            path = os.path.join(json_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                anomalies.append({
                    "kind": "json_read_error",
                    "qr": qr, "building": building, "type": asset_type,
                    "detail": str(exc), "path": path,
                })
                continue

            sd = payload.get("structured_data") or {}
            if not isinstance(sd, dict):
                continue
            json_identity = _json_identity(sd, asset_type)
            if not json_identity:
                # JSON never captured a tag — placeholder blank state. Skip.
                continue

            scanned += 1
            snap = _row_snapshot(conn, cfg, qr, building)
            if snap is None:
                anomalies.append({
                    "kind": "row_missing",
                    "qr": qr, "building": building, "type": asset_type,
                    "detail": f'JSON identity = "{json_identity}" but no row in {cfg["table"]}',
                    "path": path,
                })
                continue

            identity_in_row = str(snap.get(cfg["identity_col"]) or "").strip()
            avg_ai = _avg_ai_conf(conn, cfg["table"], qr, building)
            row_signals = _row_signal_count(snap, avg_ai)

            if not identity_in_row:
                anomalies.append({
                    "kind": "identity_wiped",
                    "qr": qr, "building": building, "type": asset_type,
                    "detail": (
                        f'JSON identity = "{json_identity}" but DB '
                        f'"{cfg["identity_col"]}" is blank '
                        f"(row signals: {row_signals})"
                    ),
                    "path": path,
                })
                continue

            # Row has an identity but it drastically disagrees with the JSON.
            # Flag only when the row carries <=1 signal columns (i.e. almost
            # everything else is blank) to avoid noise from legitimate edits.
            if identity_in_row.upper() != json_identity.upper() and row_signals <= 1:
                anomalies.append({
                    "kind": "identity_mismatch_and_mostly_blank",
                    "qr": qr, "building": building, "type": asset_type,
                    "detail": (
                        f'JSON="{json_identity}" DB="{identity_in_row}" '
                        f"row signals={row_signals}"
                    ),
                    "path": path,
                })

    return scanned, anomalies


def _print_report(scanned: int, anomalies: list[dict], quiet: bool) -> None:
    if anomalies:
        print(f"[AUDIT] ANOMALIES: {len(anomalies)} found across {scanned} scanned JSON(s)")
        for a in anomalies:
            print(
                f'  - {a["kind"]}  type={a["type"]}  qr={a["qr"]}  building={a["building"]}'
            )
            print(f"      detail: {a['detail']}")
            print(f"      json:   {a['path']}")
    else:
        if not quiet:
            print(f"[AUDIT] OK — scanned {scanned} JSON(s), no anomalies")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-dir", default=DEFAULT_JSON_DIR)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--quiet", action="store_true",
                        help="suppress OK line; print only when anomalies found")
    args = parser.parse_args()

    try:
        scanned, anomalies = audit(args.json_dir, args.db_path)
    except FileNotFoundError as exc:
        print(f"[AUDIT] ERROR: {exc}", file=sys.stderr)
        return 2

    _print_report(scanned, anomalies, args.quiet)

    if anomalies:
        import datetime
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(f"[AUDIT] RUN_AT={stamp} ANOMALIES={len(anomalies)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
