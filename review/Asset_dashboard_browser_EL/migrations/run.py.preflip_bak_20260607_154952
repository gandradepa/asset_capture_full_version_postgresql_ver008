#!/usr/bin/env python3
"""Idempotent SQLite migration runner.

Applies every .sql file in --sql-dir (alphabetical order) that is not yet
recorded in the schema_migrations table. Safe to run at app startup and from
ops. Each .sql file is executed as a single script inside its own transaction
(the .sql file itself manages BEGIN/COMMIT).
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime


SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def applied_migrations(conn):
    conn.execute(SCHEMA_MIGRATIONS_DDL)
    conn.commit()
    return {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}


def apply_sql_file(conn, sql_path):
    with open(sql_path, "r", encoding="utf-8") as f:
        script = f.read()
    conn.executescript(script)
    conn.execute(
        "INSERT INTO schema_migrations(filename, applied_at) VALUES (?, ?)",
        (os.path.basename(sql_path), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


def run(db_path, sql_dir):
    if not os.path.isfile(db_path):
        raise SystemExit(f"DB not found: {db_path}")
    if not os.path.isdir(sql_dir):
        raise SystemExit(f"SQL dir not found: {sql_dir}")

    conn = sqlite3.connect(db_path)
    try:
        already = applied_migrations(conn)
        files = sorted(f for f in os.listdir(sql_dir) if f.endswith(".sql"))
        new_files = [f for f in files if f not in already]

        for f in new_files:
            path = os.path.join(sql_dir, f)
            print(f"[migrate] applying {f}")
            apply_sql_file(conn, path)

        if not new_files:
            print("[migrate] nothing to apply")
        else:
            print(f"[migrate] applied {len(new_files)} migration(s)")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Run SQLite migrations")
    parser.add_argument("--db", required=True, help="Path to SQLite DB")
    parser.add_argument(
        "--sql-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Directory containing .sql files (default: this dir)",
    )
    args = parser.parse_args()
    run(args.db, args.sql_dir)


if __name__ == "__main__":
    main()
