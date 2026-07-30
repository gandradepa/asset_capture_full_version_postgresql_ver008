"""Add the "Process" flow-decision column to the "Buildings" table.

Gate for the electrical extraction flow (adopted 2026-07-28):
  'Standard' -> core flow (dictionary/electrical.dictionary.py)
  'Legacy'   -> legacy flow (dictionary/electrical.dictionary_old.py)
  blank/NULL -> processing must STOP and warn the user

Idempotent — safe to re-run. The initial backfill sets every blank row to
'Legacy' (current fleet default). Values are constrained to Standard/Legacy
(NULL/'' allowed to represent the stop-and-warn state).

Run against the intended database via the db.py backend switches, e.g. locally:
  DB_BACKEND=postgres QR_PG_DSN="host=127.0.0.1 port=5432 dbname=qr_code_db user=postgres" \
      python scripts/migrate_buildings_process_column.py
Back up first (pg_dump) per Markdowns_documentation/01_GLOBAL_RULES.md.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asset_capture_app_dev"))
import db

CONSTRAINT = "ck_buildings_process"


def main() -> None:
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        columns = set(db.table_columns(conn, "Buildings"))
        if "Process" not in columns:
            cursor.execute('ALTER TABLE "Buildings" ADD COLUMN "Process" TEXT')
            print('Added "Process" column to "Buildings"')
        else:
            print('"Process" column already present')

        if db.backend() == "postgres":
            cursor.execute("SELECT 1 FROM pg_constraint WHERE conname = ?", (CONSTRAINT,))
            if cursor.fetchone() is None:
                cursor.execute(
                    'ALTER TABLE "Buildings" ADD CONSTRAINT ck_buildings_process '
                    "CHECK (\"Process\" IS NULL OR \"Process\" IN ('Standard', 'Legacy', ''))"
                )
                print(f"Added {CONSTRAINT} CHECK constraint")
            else:
                print(f"{CONSTRAINT} already present")

        cursor.execute(
            'UPDATE "Buildings" SET "Process" = ? WHERE "Process" IS NULL OR "Process" = \'\'',
            ("Legacy",),
        )
        print(f"Backfilled {cursor.rowcount} blank rows to 'Legacy'")
        conn.commit()

        cursor.execute('SELECT "Process", COUNT(*) FROM "Buildings" GROUP BY "Process"')
        for value, count in cursor.fetchall():
            print(f"  Process={value!r}: {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
