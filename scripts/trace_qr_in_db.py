#!/usr/bin/env python3
"""Inspect every table in QR_codes.db for any row referencing the given QR.

Usage:
  python3 trace_qr_in_db.py 0000084088
"""
from __future__ import annotations
import os
import sqlite3
import sys

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

DB = "/home/developer/asset_capture_app_dev/data/QR_codes.db"


def main(qr: str) -> int:
    con = qrdb.get_connection(sqlite_path=DB)
    con.row_factory = sqlite3.Row
    tables = sorted(t for t in qrdb.list_tables(con) if not t.startswith("sqlite_"))
    total_hits = 0
    print(f"Tracing QR={qr!r} across {len(tables)} tables\n")
    for tbl in tables:
        cols = qrdb.table_columns(con, tbl)
        # Find columns that look textual and might hold the QR / a string containing it.
        text_cols = [c for c in cols]
        where_parts = []
        params = []
        for c in text_cols:
            # CAST to text so '=' / LIKE work uniformly across backends: PG is
            # strict and rejects `bigint ~~ text` (LIKE) on numeric/identity columns.
            where_parts.append(f'CAST("{c}" AS TEXT) = ?')
            params.append(qr)
            where_parts.append(f'CAST("{c}" AS TEXT) LIKE ?')
            params.append(f"%{qr}%")
        if not where_parts:
            continue
        sql = f'SELECT * FROM "{tbl}" WHERE ' + " OR ".join(where_parts)
        try:
            rows = con.execute(sql, params).fetchall()
        except qrdb.DatabaseError as e:
            print(f"[{tbl}] skipped: {e}")
            if qrdb.is_postgres():
                con.rollback()  # clear aborted tx so remaining tables can be scanned
            continue
        if rows:
            total_hits += len(rows)
            print(f"[{tbl}] {len(rows)} row(s):")
            for r in rows:
                d = dict(r)
                # Show only fields that contain the QR for readability.
                rel = {k: v for k, v in d.items() if v is not None and qr in str(v)}
                print(f"   pk?={d.get('ID') or d.get('rowid')} :: {rel}")
            print()
    print(f"Total hits across all tables: {total_hits}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: trace_qr_in_db.py <QR>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
