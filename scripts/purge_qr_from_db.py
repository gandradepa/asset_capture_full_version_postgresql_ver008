#!/usr/bin/env python3
"""Purge every DB row referencing a single QR.

Tables and conditions are spelled out explicitly (no schema-walk). The script
takes a SQLite backup of QR_codes.db before mutating anything, runs all
DELETEs in a single transaction, and reports row counts before and after.

Usage:
  python3 purge_qr_from_db.py 0000084088 --dry-run   # report only
  python3 purge_qr_from_db.py 0000084088             # apply
"""
from __future__ import annotations
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

DB = "/home/developer/asset_capture_app_dev/data/QR_codes.db"

# (table, where_sql, param_factory). param_factory takes the QR and returns
# the sqlite parameter tuple element (used for both COUNT and DELETE).
TABLE_QUERIES = [
    ("QR_code_assets", 'code_assets LIKE ?', lambda q: f"{q} %"),
    ("QR_codes",       '"QR_code_ID" = ?',   lambda q: q),
    ("process_type",   '"QR Code" = ?',      lambda q: q),
    ("sdi_dataset",    '"QR Code" = ?',      lambda q: q),
]
# NOTE (PostgreSQL): the C3 FK model gives QR_codes children ON DELETE CASCADE
# (QR_code_assets, process_type, sdi_dataset, sdi_dataset_EL, json_files), so the
# explicit DELETE of "QR_codes" auto-removes them -- the later per-table DELETEs
# then report 0 rows (already cascaded), and sdi_dataset_EL/json_files (not listed
# here) are removed too, so a PG purge is MORE thorough than the SQLite one.
# Packaged QRs (sdi_print_out[_arch], FK RESTRICT) make the QR_codes DELETE raise
# a ForeignKeyViolation, aborting the whole purge. On SQLite (FK enforcement off)
# each listed table is deleted explicitly and EL/json_files orphans are left.


def count_rows(con: sqlite3.Connection, qr: str) -> dict[str, int]:
    counts = {}
    for tbl, where, fn in TABLE_QUERIES:
        try:
            n = con.execute(
                f'SELECT COUNT(*) FROM "{tbl}" WHERE {where}', (fn(qr),)
            ).fetchone()[0]
        except qrdb.DatabaseError:
            n = -1  # missing table or column
            if qrdb.is_postgres():
                con.rollback()  # clear the aborted tx so remaining counts run
        counts[tbl] = n
    return counts


def backup_db(qr: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if qrdb.is_postgres():
        dsn = os.environ.get(
            "QR_PG_DSN", "host=127.0.0.1 port=5433 dbname=qr_code_db user=developer"
        )
        dest = os.path.join(
            os.path.dirname(DB), f"qr_code_db.bak_{stamp}_purge_{qr}.dump"
        )
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "-f", dest, dsn],
            check=True,
        )
        return dest
    dest = DB.replace(".db", f".bak_{stamp}_purge_{qr}.db")
    shutil.copy2(DB, dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("qr", help="QR code to purge (exact match)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    qr = args.qr.strip()
    if not qr:
        print("Refusing to run on empty QR.")
        return 2

    con = qrdb.get_connection(sqlite_path=DB)
    before = count_rows(con, qr)
    total_before = sum(v for v in before.values() if v > 0)
    print(f"QR={qr!r}: rows referencing it before:")
    for t, n in before.items():
        print(f"   {t:<18} {n}")
    print(f"   TOTAL              {total_before}")

    if args.dry_run:
        print("\n[DRY RUN] No changes applied.")
        return 0
    if total_before == 0:
        print("Nothing to delete.")
        return 0

    backup = backup_db(qr)
    print(f"\nDB backed up to: {backup}")

    deleted = {}
    try:
        if not qrdb.is_postgres():
            con.execute("BEGIN")
        for tbl, where, fn in TABLE_QUERIES:
            try:
                cur = con.execute(
                    f'DELETE FROM "{tbl}" WHERE {where}', (fn(qr),)
                )
                deleted[tbl] = cur.rowcount
            except sqlite3.OperationalError as e:
                # SQLite: missing table/column -> skip. On PG the target tables
                # exist and any error (e.g. FK RESTRICT on a packaged QR) is not an
                # sqlite3.OperationalError, so it propagates to the rollback below
                # and aborts the whole purge (no partial delete).
                print(f"   [{tbl}] skipped: {e}")
                deleted[tbl] = 0
        con.commit()
    except Exception:
        con.rollback()
        raise

    print("\nRows deleted:")
    for t, n in deleted.items():
        print(f"   {t:<18} {n}")
    print(f"   TOTAL              {sum(deleted.values())}")

    after = count_rows(con, qr)
    leftover = sum(v for v in after.values() if v > 0)
    print(f"\nResidual references after purge: {leftover}")
    return 0 if leftover == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
