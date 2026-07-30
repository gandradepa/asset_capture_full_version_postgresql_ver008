#!/usr/bin/env python3
"""Audit-trail health check — print summary stats for the unified audit log.

Use this post-deploy or whenever you want a quick sanity check without
writing SQL. Prints:
  - Schema check (table exists, expected columns + indexes)
  - Row totals + most recent timestamp
  - Breakdown by source / app_name / op_type / table_name
  - Top users
  - Index-usage plan for the QR-code lookup path
  - Recent activity sample
  - Optional --qr <code> drill-down

Exits non-zero when the table is missing or schema is wrong, so it can
double as a deploy smoke-test in CI.
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

EXPECTED_COLUMNS = {
    "id", "qr_code", "description", "modification_date", "modification_time",
    "modified_by", "source", "app_name", "table_name", "record_pk",
    "op_type", "field_name", "old_value", "new_value", "created_at",
}
EXPECTED_INDEXES = {
    "ix_audit_trail_qr", "ix_audit_trail_date", "ix_audit_trail_user",
    "ix_audit_trail_app_table", "ix_audit_trail_record",
}


def _hr(title: str) -> None:
    print()
    print(f"--- {title} " + "-" * max(0, 60 - len(title)))


def _print_breakdown(conn: sqlite3.Connection, column: str, label: str, limit: int = 20) -> None:
    _hr(f"Breakdown by {label}")
    rows = conn.execute(
        f"SELECT {column}, COUNT(*) AS n FROM audit_trail "
        f"GROUP BY {column} ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        print("  (no rows)")
        return
    width = max(len(str(r[0] or "")) for r in rows)
    for value, count in rows:
        print(f"  {str(value or ''):<{width}}  {count:>10,}")


def _audit_trail_indexes(conn) -> set:
    """Index names on audit_trail — PRAGMA on SQLite, pg_indexes on PostgreSQL."""
    if qrdb.is_postgres():
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='audit_trail'"
        ).fetchall()
        return {r[0] for r in rows}
    return {r[1] for r in conn.execute("PRAGMA index_list(audit_trail)").fetchall()}


def health(db_path: str, qr: str | None = None, sample: int = 5) -> int:
    if not qrdb.is_postgres() and not os.path.exists(db_path):
        print(f"[health] DB not found: {db_path}", file=sys.stderr)
        return 2

    conn = qrdb.get_connection(sqlite_path=db_path, timeout=10.0)
    try:
        # Table exists?
        if not qrdb.has_table(conn, "audit_trail"):
            print("[health] FAIL: audit_trail table missing — run migrate_create_audit_trail.py")
            return 1

        # Schema match
        actual_cols = set(qrdb.table_columns(conn, "audit_trail"))
        actual_idx = _audit_trail_indexes(conn)
        missing_cols = EXPECTED_COLUMNS - actual_cols
        missing_idx = EXPECTED_INDEXES - actual_idx
        ok = not missing_cols and not missing_idx

        _hr("Schema")
        print(f"  DB           : {db_path}")
        print(f"  columns      : {len(actual_cols)} (expected {len(EXPECTED_COLUMNS)})")
        if missing_cols:
            print(f"  MISSING cols : {sorted(missing_cols)}")
        print(f"  indexes      : {len(actual_idx)} (expected {len(EXPECTED_INDEXES)})")
        if missing_idx:
            print(f"  MISSING idx  : {sorted(missing_idx)}")
        print(f"  status       : {'OK' if ok else 'DEGRADED'}")

        # Totals
        total = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        latest = conn.execute(
            "SELECT modification_date, modification_time, modified_by, app_name "
            "FROM audit_trail ORDER BY id DESC LIMIT 1"
        ).fetchone()
        oldest = conn.execute(
            "SELECT modification_date, modification_time "
            "FROM audit_trail ORDER BY id ASC LIMIT 1"
        ).fetchone()
        _hr("Totals")
        print(f"  rows         : {total:,}")
        if oldest:
            print(f"  oldest entry : {oldest[0]} {oldest[1]}")
        if latest:
            print(f"  latest entry : {latest[0]} {latest[1]}  by {latest[2]} ({latest[3]})")

        if total == 0:
            print("\n[health] WARN: audit_trail is empty. Either no save events have run yet,")
            print("              or the apps are not importing audit/logger. Submit one capture")
            print("              to confirm fresh rows land.")
            return 0 if ok else 1

        _print_breakdown(conn, "source", "source")
        _print_breakdown(conn, "app_name", "app_name")
        _print_breakdown(conn, "op_type", "op_type")
        _print_breakdown(conn, "table_name", "table_name")
        _print_breakdown(conn, "modified_by", "modified_by (top 10)", limit=10)

        # Index plan check (SQLite: EXPLAIN QUERY PLAN -> detail at r[3];
        # PostgreSQL: EXPLAIN -> plan line at r[0]).
        _hr("Index plan: SELECT * FROM audit_trail WHERE qr_code = ?")
        if qrdb.is_postgres():
            for r in conn.execute(
                "EXPLAIN SELECT * FROM audit_trail WHERE qr_code=?", ("__probe__",)
            ).fetchall():
                print(f"  {r[0]}")
        else:
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM audit_trail WHERE qr_code=?",
                ("__probe__",),
            ).fetchall():
                print(f"  {r[3]}")

        # Recent activity
        _hr(f"Recent activity (last {sample})")
        rows = conn.execute(
            "SELECT modification_date, modification_time, modified_by, source, "
            "app_name, op_type, qr_code, field_name, old_value, new_value "
            "FROM audit_trail ORDER BY id DESC LIMIT ?",
            (sample,),
        ).fetchall()
        for r in rows:
            (date, time, user, source, app, op, qr_code,
             field, old_v, new_v) = r
            old_disp = "" if old_v is None else (old_v[:30] + "..." if len(old_v) > 30 else old_v)
            new_disp = "" if new_v is None else (new_v[:30] + "..." if len(new_v) > 30 else new_v)
            print(
                f"  {date} {time}  {user:<14}  {source:<14}  {app:<12}  "
                f"{op:<7}  qr={qr_code or '-':<14}  {field}: {old_disp!r} --> {new_disp!r}"
            )

        # Optional QR drill-down
        if qr:
            _hr(f"Drill-down for qr_code = {qr!r}")
            qr_rows = conn.execute(
                "SELECT modification_date, modification_time, modified_by, source, "
                "app_name, op_type, table_name, field_name, old_value, new_value "
                "FROM audit_trail WHERE qr_code = ? ORDER BY id DESC",
                (qr,),
            ).fetchall()
            if not qr_rows:
                print(f"  (no rows for qr_code = {qr!r})")
            else:
                print(f"  {len(qr_rows)} rows")
                for r in qr_rows[:50]:
                    (date, time, user, source, app, op, tbl,
                     field, old_v, new_v) = r
                    old_disp = "" if old_v is None else (old_v[:40] + "..." if len(old_v) > 40 else old_v)
                    new_disp = "" if new_v is None else (new_v[:40] + "..." if len(new_v) > 40 else new_v)
                    print(
                        f"  {date} {time}  {user:<14}  {source:<14}  {app:<12}  "
                        f"{op:<7}  {tbl:<16}  {field}: {old_disp!r} --> {new_disp!r}"
                    )
                if len(qr_rows) > 50:
                    print(f"  ... ({len(qr_rows) - 50} more rows truncated)")

        return 0 if ok else 1
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
        "--qr",
        default=None,
        help="Optional: drill down on a specific QR code (prints up to 50 rows).",
    )
    p.add_argument(
        "--sample", type=int, default=5,
        help="Number of recent rows to display (default 5).",
    )
    args = p.parse_args()
    sys.exit(health(args.db, qr=args.qr, sample=args.sample))


if __name__ == "__main__":
    main()
