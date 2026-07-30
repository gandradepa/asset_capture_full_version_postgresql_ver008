#!/usr/bin/env python3
"""One-shot cleanup: purge `api_pipeline` audit rows for `date_set` churn.

Pre-fix the api_pipeline cron flapped `QR_codes.date_set` between ~3 photo
mtimes every 2 minutes, producing thousands of bogus audit rows. The dedupe
fix in update_qr_codes_table stops the churn going forward; this script
cleans up the historical noise.

Default mode is DRY-RUN. Use --apply to actually delete.

Targets: rows where
    app_name   = 'api_pipeline'
    field_name = 'date_set'
    table_name = 'QR_codes'
    description LIKE 'updating_process_database%'

Optional --keep-after <ID> preserves rows with id > <ID> (useful if you want
to keep the post-fix normalization batch and only delete the older churn).
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


def cleanup(db_path: str, apply: bool, keep_after: int | None) -> int:
    if not qrdb.is_postgres() and not os.path.exists(db_path):
        print(f"[cleanup] DB not found: {db_path}", file=sys.stderr)
        return 2

    conn = qrdb.get_connection(sqlite_path=db_path, timeout=10.0)
    try:
        where = (
            "app_name = 'api_pipeline' "
            "AND field_name = 'date_set' "
            "AND table_name = 'QR_codes' "
            "AND description LIKE 'updating_process_database%'"
        )
        params: list = []
        if keep_after is not None:
            where += " AND id <= ?"
            params.append(keep_after)

        # Counts
        total = conn.execute(f"SELECT COUNT(*) FROM audit_trail WHERE {where}", params).fetchone()[0]
        if total == 0:
            print("[cleanup] Nothing to delete — 0 matching rows.")
            return 0

        bounds = conn.execute(
            f"SELECT MIN(id), MAX(id), MIN(modification_date), MAX(modification_date) "
            f"FROM audit_trail WHERE {where}", params,
        ).fetchone()

        print(f"[cleanup] Matching rows : {total:,}")
        print(f"[cleanup] id range      : {bounds[0]} .. {bounds[1]}")
        print(f"[cleanup] date range    : {bounds[2]} .. {bounds[3]}")

        # Sample
        sample = conn.execute(
            f"SELECT id, modification_date, modification_time, qr_code, "
            f"substr(old_value,1,30), substr(new_value,1,30) "
            f"FROM audit_trail WHERE {where} ORDER BY id LIMIT 3", params,
        ).fetchall()
        print("[cleanup] sample (oldest 3):")
        for r in sample:
            print(f"           {r}")

        if not apply:
            print()
            print("[cleanup] DRY RUN — no rows deleted. Re-run with --apply to actually delete.")
            return 0

        # Apply
        cur = conn.execute(f"DELETE FROM audit_trail WHERE {where}", params)
        conn.commit()
        print(f"[cleanup] DELETED {cur.rowcount:,} rows.")
        remaining = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        print(f"[cleanup] audit_trail row count: {remaining:,}")
        return 0
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", DEFAULT_DB),
        help=f"Path to SQLite DB (default: $DB_PATH or {DEFAULT_DB})",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Actually delete (default is dry-run).",
    )
    p.add_argument(
        "--keep-after", type=int, default=None,
        help="Optional: only delete rows with id <= N (keeps newer rows).",
    )
    args = p.parse_args()
    sys.exit(cleanup(args.db, apply=args.apply, keep_after=args.keep_after))


if __name__ == "__main__":
    main()
