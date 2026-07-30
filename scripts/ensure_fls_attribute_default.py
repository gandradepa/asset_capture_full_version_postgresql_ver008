#!/usr/bin/env python3
"""Ensure the FLS new-device Attribute Set default exists and is applied.

Runs against SQLite by default. Set DB_BACKEND=postgres and QR_PG_DSN to use
the shared PostgreSQL DB layer on the VM.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "asset_capture_app_dev"))

import db as qrdb  # noqa: E402
from audit.logger import log_change  # noqa: E402


DEFAULT_CODE = "FireAlarmDevice"
DEFAULT_LABEL = "Electrical/FLS - Fire Alarm Device"


def _norm(value) -> str:
    return "" if value is None else str(value).strip()


def _fetch_attribute_rows(conn):
    return conn.execute(
        """
        SELECT "Code", "Attribute"
        FROM "Attribute"
        WHERE UPPER(TRIM(COALESCE(CAST("Code" AS TEXT), ''))) = UPPER(TRIM(?))
        """,
        (DEFAULT_CODE,),
    ).fetchall()


def _ensure_attribute(conn) -> tuple[str, int]:
    rows = _fetch_attribute_rows(conn)
    if len(rows) > 1:
        raise RuntimeError(f"Found {len(rows)} Attribute rows matching {DEFAULT_CODE!r}; expected at most one.")

    if not rows:
        conn.execute(
            'INSERT INTO "Attribute" ("Code", "Attribute") VALUES (?, ?)',
            (DEFAULT_CODE, DEFAULT_LABEL),
        )
        audit_rows = log_change(
            conn,
            qr_code=None,
            app_name="Dashboard",
            table_name="Attribute",
            record_pk=DEFAULT_CODE,
            op_type="INSERT",
            field_changes={
                "Code": (None, DEFAULT_CODE),
                "Attribute": (None, DEFAULT_LABEL),
            },
            modified_by="system",
            source="system",
            description="Ensure FLS Fire Alarm Device attribute code",
        )
        return "inserted", audit_rows

    old_code, old_label = rows[0][0], rows[0][1]
    changes = {}
    if _norm(old_code) != DEFAULT_CODE:
        changes["Code"] = (old_code, DEFAULT_CODE)
    if _norm(old_label) != DEFAULT_LABEL:
        changes["Attribute"] = (old_label, DEFAULT_LABEL)

    if changes:
        conn.execute(
            """
            UPDATE "Attribute"
            SET "Code" = ?, "Attribute" = ?
            WHERE UPPER(TRIM(COALESCE(CAST("Code" AS TEXT), ''))) = UPPER(TRIM(?))
            """,
            (DEFAULT_CODE, DEFAULT_LABEL, DEFAULT_CODE),
        )
        audit_rows = log_change(
            conn,
            qr_code=None,
            app_name="Dashboard",
            table_name="Attribute",
            record_pk=DEFAULT_CODE,
            op_type="UPDATE",
            field_changes=changes,
            modified_by="system",
            source="system",
            description="Normalize FLS Fire Alarm Device attribute code",
        )
        return "updated", audit_rows

    return "unchanged", 0


def _update_new_device(conn) -> tuple[int, int, int]:
    rows = conn.execute('SELECT "index", "Attribute Set" FROM new_device').fetchall()
    changed = [(row[0], row[1]) for row in rows if _norm(row[1]) != DEFAULT_CODE]

    conn.execute('UPDATE new_device SET "Attribute Set" = ?', (DEFAULT_CODE,))

    audit_rows = 0
    for row_index, old_value in changed:
        audit_rows += log_change(
            conn,
            qr_code=None,
            app_name="Dashboard",
            table_name="new_device",
            record_pk=row_index,
            op_type="UPDATE",
            field_changes={"Attribute Set": (old_value, DEFAULT_CODE)},
            modified_by="system",
            source="system",
            description="Normalize FLS new-device Attribute Set default",
        )
    return len(rows), len(changed), audit_rows


def _set_postgres_default(conn) -> bool:
    if not qrdb.is_postgres():
        return False
    conn.execute('ALTER TABLE new_device ALTER COLUMN "Attribute Set" SET DEFAULT ?', (DEFAULT_CODE,))
    return True


def _verify(conn) -> tuple[tuple, int]:
    attr_row = conn.execute(
        'SELECT "Code", "Attribute" FROM "Attribute" WHERE "Code" = ?',
        (DEFAULT_CODE,),
    ).fetchone()
    non_default = conn.execute(
        'SELECT COUNT(*) FROM new_device WHERE COALESCE("Attribute Set", \'\') <> ?',
        (DEFAULT_CODE,),
    ).fetchone()[0]
    return attr_row, non_default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=str(ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db"),
        help="SQLite DB path when DB_BACKEND is not postgres.",
    )
    args = parser.parse_args()

    conn = qrdb.get_connection(sqlite_path=args.sqlite_path)
    try:
        if not qrdb.is_postgres():
            conn.row_factory = sqlite3.Row
        if not qrdb.has_table(conn, "Attribute"):
            raise RuntimeError('Missing required table "Attribute".')
        if not qrdb.has_table(conn, "new_device"):
            raise RuntimeError('Missing required table "new_device".')
        if not qrdb.has_table(conn, "audit_trail"):
            raise RuntimeError('Missing required table "audit_trail"; refusing unaudited maintenance.')

        attr_status, attr_audit_rows = _ensure_attribute(conn)
        total_devices, changed_devices, device_audit_rows = _update_new_device(conn)
        default_set = _set_postgres_default(conn)
        attr_row, non_default = _verify(conn)

        if not attr_row or attr_row[1] != DEFAULT_LABEL:
            raise RuntimeError(f"Attribute verification failed: {attr_row!r}")
        if non_default != 0:
            raise RuntimeError(f"new_device verification failed: {non_default} rows are not {DEFAULT_CODE}.")

        conn.commit()
        print(f"backend={qrdb.backend()}")
        print(f"attribute_status={attr_status}")
        print(f"attribute_audit_rows={attr_audit_rows}")
        print(f"new_device_total={total_devices}")
        print(f"new_device_changed={changed_devices}")
        print(f"new_device_audit_rows={device_audit_rows}")
        print(f"postgres_default_set={int(default_set)}")
        print(f"verified_attribute={attr_row[0]}|{attr_row[1]}")
        print(f"verified_non_default_new_device={non_default}")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
