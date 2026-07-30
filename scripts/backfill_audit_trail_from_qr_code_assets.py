#!/usr/bin/env python3
"""One-shot backfill: synthesize INSERT rows in `audit_trail` from existing
QR_code_assets entries that have user/date_hour metadata.

Goal: keep the rewritten Dashboard "User Activity Log" panel non-empty on
day one. Without this, the new endpoint returns nothing for assets that
were captured before audit logging existed.

Idempotency: skipped rows are detected via a (table_name, record_pk,
op_type, field_name) match; safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

DEFAULT_DB = "/home/developer/asset_capture_app_dev/data/QR_codes.db"


def _split_iso(ts: str) -> tuple[str, str]:
    if not ts:
        return "", ""
    if "T" in ts:
        d, _, t = ts.partition("T")
    elif " " in ts:
        d, _, t = ts.partition(" ")
    else:
        return ts[:10], ""
    return d.strip(), (t.strip()[:8] or "")


def backfill(db_path: str) -> None:
    if not qrdb.is_postgres() and not os.path.exists(db_path):
        print(f"[backfill] DB not found: {db_path}", file=sys.stderr)
        sys.exit(2)

    conn = qrdb.get_connection(sqlite_path=db_path, timeout=10.0)
    try:
        # Sanity: audit_trail must exist
        if not qrdb.has_table(conn, "audit_trail"):
            print("[backfill] audit_trail not found — run migrate_create_audit_trail.py first")
            sys.exit(2)

        # Sanity: source columns must exist
        cols = qrdb.table_columns(conn, "QR_code_assets")
        if "user" not in cols or "date_hour" not in cols:
            print("[backfill] QR_code_assets lacks user/date_hour columns — nothing to backfill")
            return

        # Pull eligible rows ("user" is a PG reserved keyword -> must be quoted).
        rows = conn.execute("""
            SELECT "ID", code_assets, "user", date_hour
            FROM "QR_code_assets"
            WHERE "user" IS NOT NULL AND TRIM("user") <> ''
        """).fetchall()
        print(f"[backfill] {len(rows)} eligible QR_code_assets rows")

        inserted = 0
        skipped = 0
        for row_id, code_assets, user, date_hour in rows:
            if not code_assets:
                skipped += 1
                continue

            # De-dupe: skip if a backfill row already exists for this record_pk
            existing = conn.execute("""
                SELECT 1 FROM audit_trail
                WHERE table_name='QR_code_assets'
                  AND record_pk=? AND op_type='INSERT' AND field_name='code_assets'
                LIMIT 1
            """, (str(row_id),)).fetchone()
            if existing:
                skipped += 1
                continue

            qr_code = (code_assets.split(" ", 1)[0] or "").strip() or None
            mod_date, mod_time = _split_iso(date_hour or "")
            if not mod_date:
                # Unknown date → use the epoch-ish placeholder so the row sorts last
                mod_date = "1970-01-01"
            if not mod_time:
                mod_time = "00:00:00"

            conn.execute("""
                INSERT INTO audit_trail (
                    qr_code, description, modification_date, modification_time,
                    modified_by, source, app_name, table_name, record_pk,
                    op_type, field_name, old_value, new_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                qr_code,
                "backfill from QR_code_assets",
                mod_date,
                mod_time,
                str(user),
                "human",
                "mobile",
                "QR_code_assets",
                str(row_id),
                "INSERT",
                "code_assets",
                None,
                str(code_assets),
            ))
            inserted += 1

        conn.commit()
        print(f"[backfill] inserted={inserted}, skipped={skipped}")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", DEFAULT_DB),
        help=f"Path to SQLite DB (default: $DB_PATH or {DEFAULT_DB})",
    )
    args = p.parse_args()
    backfill(args.db)


if __name__ == "__main__":
    main()
